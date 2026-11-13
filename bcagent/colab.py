"""Google Colab support: `/colab <url>` links a Colab notebook so bcagent can edit it.

Colab notebooks are .ipynb files stored in Google Drive. bcagent downloads the notebook with the
Google Drive API and writes it as a plain Python file in "percent" format (cells start with `# %%`)
inside the project, in colab/<name>.py. The agent edits that file like any other file (with the
usual diff + approval). After every approved edit bcagent turns the file back into a notebook and
uploads it to Drive, keeping the outputs of the cells that did not change.

Sign-in (Google OAuth 2.0, Python standard library only):
  * quick:     paste an access token from the Google OAuth 2.0 Playground (valid for 1 hour)
  * permanent: your own "Desktop app" OAuth client (client_secret.json); bcagent signs you in through
               the browser once and refreshes the token by itself.

Files:
  ~/.bcagent/google/client.json   OAuth client id + secret (permanent sign-in)
  ~/.bcagent/google/token.json    access / refresh token (only readable by you)
  <project>/.bcagent/colab.json   linked notebooks (mirror file -> Drive file id, version)
  <project>/.bcagent/colab/<id>.ipynb  last synced copy (keeps outputs and cell metadata)
"""
from __future__ import annotations

import base64
import difflib
import getpass
import hashlib
import http.server
import json
import os
import re
import secrets
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.markup import escape
from rich.panel import Panel

from . import ui
from .config import HOME
from .ui import console

SCOPE = "https://www.googleapis.com/auth/drive"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
PLAYGROUND = "https://developers.google.com/oauthplayground"
COLAB_MIME = "application/vnd.google.colaboratory"
FIELDS = "id,name,mimeType,version,modifiedTime,webViewLink,capabilities/canEdit"

GOOGLE_DIR = HOME / "google"
CLIENT_FILE = GOOGLE_DIR / "client.json"
TOKEN_FILE = GOOGLE_DIR / "token.json"
MIRROR_DIR = "colab"

CELL_RE = re.compile(r"^# %%(?:[ \t]+\[(markdown|md|raw)\])?.*$", re.I)
MAGIC_RE = re.compile(r"^(\s*)[!%].*$")


class ColabError(RuntimeError):
    pass


# -- notebook <-> percent-format text --------------------------------------------------------

def _source(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def _norm(text: str) -> str:
    """Cell source without leading/trailing blank lines (how it reads back from the mirror file)."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _lines(text: str) -> List[str]:
    return text.splitlines(keepends=True)


def notebook_to_text(nb: dict, name: str) -> str:
    """The notebook as a Python file: `# %%` starts a code cell, `# %% [markdown]` a text cell."""
    out = [f"# Google Colab notebook: {name}\n",
           "# Synced by bcagent. Every cell starts with a '# %%' line; text cells are '# %% [markdown]'\n",
           "# with each line commented. Shell and magic lines (!pip, %time) work like in Colab.\n"]
    for cell in nb.get("cells", []):
        src = _norm(_source(cell))
        kind = cell.get("cell_type", "code")
        if kind == "code":
            out.append("\n# %%\n" + (src + "\n" if src else ""))
        else:
            tag = "markdown" if kind == "markdown" else "raw"
            body = "".join(("# " + l if l.strip() else "#") + "\n" for l in src.split("\n")) if src else ""
            out.append(f"\n# %% [{tag}]\n" + body)
    return "".join(out)


def text_to_cells(text: str) -> List[Tuple[str, str]]:
    """Parse the percent-format text back into [(cell_type, source)]. Lines before the first
    `# %%` line (the header comment) are ignored."""
    cells: List[Tuple[str, List[str]]] = []
    for line in text.splitlines():
        m = CELL_RE.match(line)
        if m:
            kind = (m.group(1) or "code").lower()
            cells.append(("markdown" if kind == "md" else kind, []))
        elif cells:
            cells[-1][1].append(line)
    out = []
    for kind, lines in cells:
        if kind != "code":
            lines = [l[2:] if l.startswith("# ") else (l[1:] if l.startswith("#") else l) for l in lines]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        out.append((kind, "\n".join(lines)))
    return out


def _new_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")[:12]


def merge_cells(base: dict, cells: List[Tuple[str, str]]) -> dict:
    """Build the new notebook from `base`: unchanged cells keep their outputs, ids and metadata,
    changed cells keep id/metadata but lose their (now stale) outputs, new cells are added."""
    nb = json.loads(json.dumps(base)) if base else {}
    nb.setdefault("nbformat", 4)
    nb.setdefault("nbformat_minor", 0)
    nb.setdefault("metadata", {"colab": {"provenance": []}, "kernelspec": {"name": "python3",
                                                                          "display_name": "Python 3"}})
    old = nb.get("cells", [])
    old_keys = [(c.get("cell_type", "code"), _norm(_source(c))) for c in old]
    colab_ids = any("id" in c.get("metadata", {}) for c in old) or not old
    top_ids = nb.get("nbformat_minor", 0) >= 5

    def fresh(kind: str, src: str, like: Optional[dict] = None) -> dict:
        cell = {"cell_type": kind, "metadata": dict(like.get("metadata", {})) if like else {},
                "source": _lines(src)}
        if colab_ids and "id" not in cell["metadata"]:
            cell["metadata"]["id"] = _new_id()
        if top_ids:
            cell["id"] = (like or {}).get("id") or _new_id()
        if kind == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        return cell

    new_cells: List[dict] = []
    sm = difflib.SequenceMatcher(a=old_keys, b=cells, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            new_cells.extend(old[i1:i2])
            continue
        olds = old[i1:i2] if op == "replace" else []
        for k, (kind, src) in enumerate(cells[j1:j2]):
            like = olds[k] if k < len(olds) and olds[k].get("cell_type") == kind else None
            new_cells.append(fresh(kind, src, like))
    nb["cells"] = new_cells
    return nb


def check_source(text: str, path: str = "<colab>") -> Optional[str]:
    """Syntax check for a notebook mirror: shell/magic lines (!pip, %time) are valid in Colab."""
    src = "\n".join(MAGIC_RE.sub(lambda m: m.group(1) + "pass", l) for l in text.splitlines())
    try:
        compile(src, path, "exec")
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}: {e.msg}"
    except ValueError as e:
        return str(e)
    return None


# -- URLs ----------------------------------------------------------------------------------

def parse_url(url: str) -> str:
    """Drive file id of a Colab / Drive link (or a bare id)."""
    url = url.strip().strip("<>\"'")
    if "colab.research.google.com/github/" in url or "colab.research.google.com/gist/" in url:
        raise ColabError("this notebook is on GitHub, not in Google Drive. In Colab choose "
                         "File ▸ Save a copy in Drive, then use the link of the copy.")
    for pat in (r"colab\.research\.google\.com/drive/([\w-]{20,})", r"/file/d/([\w-]{20,})",
                r"[?&]id=([\w-]{20,})", r"/open\?id=([\w-]{20,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    if re.fullmatch(r"[\w-]{20,}", url):
        return url
    raise ColabError("that doesn't look like a Colab link. It should look like "
                     "https://colab.research.google.com/drive/1AbC...xyz")


def colab_url(file_id: str) -> str:
    return f"https://colab.research.google.com/drive/{file_id}"


# -- credentials ---------------------------------------------------------------------------

def _save_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            j = json.loads(body)
            msg = j.get("error_description") or j.get("error") or body
        except ValueError:
            msg = body
        raise ColabError(f"Google sign-in failed: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ColabError(f"cannot reach Google ({e})") from None


class Credentials:
    """Access token for the Drive API, refreshed automatically when we have a refresh token."""

    def __init__(self):
        self.env_token = os.environ.get("BCAGENT_GOOGLE_TOKEN")

    def token(self) -> dict:
        return _load(TOKEN_FILE)

    def signed_in(self) -> bool:
        t = self.token()
        return bool(self.env_token or t.get("refresh_token") or
                    (t.get("access_token") and t.get("expires_at", 0) > time.time() + 30))

    def kind(self) -> str:
        if self.env_token:
            return "BCAGENT_GOOGLE_TOKEN"
        t = self.token()
        if t.get("refresh_token"):
            return "permanent (refreshes automatically)"
        if t.get("access_token"):
            left = int(t.get("expires_at", 0) - time.time())
            return f"quick token, {max(0, left) // 60} min left" if left > 0 else "quick token, expired"
        return "not signed in"

    def access_token(self, force_refresh: bool = False) -> str:
        if self.env_token:
            return self.env_token
        t = self.token()
        if t.get("access_token") and not force_refresh and t.get("expires_at", 0) > time.time() + 60:
            return t["access_token"]
        if t.get("refresh_token"):
            client = _load(CLIENT_FILE)
            if not client:
                raise ColabError("the OAuth client is missing. Run [bc]/colab login[/] again.")
            r = _post_form(TOKEN_URL, {"client_id": client["client_id"],
                                       "client_secret": client.get("client_secret", ""),
                                       "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
            t["access_token"] = r["access_token"]
            t["expires_at"] = time.time() + int(r.get("expires_in", 3600))
            _save_private(TOKEN_FILE, t)
            return t["access_token"]
        if t.get("access_token"):
            raise ColabError("your Google token expired (quick tokens last 1 hour). "
                             "Run [bc]/colab login[/] to sign in again.")
        raise ColabError("not signed in to Google. Run [bc]/colab login[/].")


def logout() -> bool:
    found = TOKEN_FILE.exists()
    t = _load(TOKEN_FILE)
    tok = t.get("refresh_token") or t.get("access_token")
    if tok:  # best effort: revoke at Google too
        try:
            _post_form("https://oauth2.googleapis.com/revoke", {"token": tok})
        except ColabError:
            pass
    try:
        TOKEN_FILE.unlink()
    except OSError:
        pass
    return found


# -- guided sign-in ------------------------------------------------------------------------

QUICK_STEPS = f"""[bold]Quick sign-in[/] [muted](about 1 minute, the token works for 1 hour)[/]

  1. Open [bc]{PLAYGROUND}[/]
  2. On the left, in [bold]Step 1[/], type this scope in the box [italic]Input your own scopes[/]:
       [bc]{SCOPE}[/]
     and click [bold]Authorize APIs[/]. Sign in with the Google account that owns the notebook
     and click [bold]Allow[/].
  3. In [bold]Step 2[/] click [bold]Exchange authorization code for tokens[/].
  4. Copy the [bold]Access token[/] (it starts with [bc]ya29.[/]) and paste it below.

[muted]The token is saved only on this computer, in {TOKEN_FILE}.[/]"""

PERMANENT_STEPS = """[bold]Permanent sign-in[/] [muted](about 5 minutes, only once)[/]

You create your own small Google Cloud "app" so bcagent can sign in and renew access by itself.
It's free and doesn't need a credit card.

  1. Create a project: [bc]https://console.cloud.google.com/projectcreate[/]
     (name it [bold]bcagent[/] and click [bold]Create[/]; make sure it is selected at the top of the page)
  2. Turn on the Google Drive API:
     [bc]https://console.cloud.google.com/apis/library/drive.googleapis.com[/]  →  [bold]Enable[/]
  3. Set up the consent screen: [bc]https://console.cloud.google.com/auth/overview[/]  →  [bold]Get started[/]
     App name [bold]bcagent[/], your email, Audience [bold]External[/], then [bold]Create[/].
  4. Add yourself as a tester: [bc]https://console.cloud.google.com/auth/audience[/]
     →  [bold]Test users[/]  →  [bold]Add users[/]  →  your Gmail address  →  [bold]Save[/].
  5. Create the client: [bc]https://console.cloud.google.com/auth/clients[/]  →  [bold]Create client[/]
     Application type [bold]Desktop app[/], name [bold]bcagent[/]  →  [bold]Create[/]  →  [bold]Download JSON[/].
  6. Give bcagent the downloaded file (client_secret_….json) below.

Then a browser window opens. Google shows [italic]"Google hasn't verified this app"[/] because the app is
yours and only for you: click [bold]Continue[/], then allow access to Google Drive.

[muted]Tip: in testing mode Google asks you to sign in again every 7 days (bcagent will open the browser
for you). To avoid that, click [bold]Publish app[/] on the Audience page.[/]"""


def _check_token(token: str) -> dict:
    try:
        with urllib.request.urlopen(f"{TOKENINFO_URL}?access_token={urllib.parse.quote(token)}", timeout=15) as r:
            info = json.load(r)
    except urllib.error.HTTPError:
        raise ColabError("Google didn't accept that token. Copy the whole access token (it starts with "
                         "ya29.) and try again.") from None
    except (urllib.error.URLError, OSError) as e:
        raise ColabError(f"cannot reach Google ({e})") from None
    if SCOPE not in info.get("scope", "").split():
        raise ColabError(f"this token can't edit Drive files. In the Playground use the scope {SCOPE}")
    return info


def login_quick() -> bool:
    console.print(Panel(QUICK_STEPS, border_style=ui.BC_BLUE_LIGHT, expand=False))
    if ui.choose("Open the OAuth 2.0 Playground in your browser?", ["Yes", "No, I'll open it myself"]) == 0:
        webbrowser.open(PLAYGROUND)
    for _ in range(3):
        try:
            tok = getpass.getpass("  Access token (hidden while you type, enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if not tok:
            return False
        try:
            info = _check_token(tok)
        except ColabError as e:
            console.print(f"  [err]✗ {e}[/]")
            continue
        _save_private(TOKEN_FILE, {"access_token": tok,
                                   "expires_at": time.time() + int(info.get("expires_in", 3600)),
                                   "email": info.get("email", "")})
        console.print(f"  [ok]✓[/] signed in{(' as ' + info['email']) if info.get('email') else ''} "
                      f"for {int(info.get('expires_in', 3600)) // 60} minutes")
        return True
    return False


def _read_client(path: str) -> dict:
    p = Path(path.strip().strip("'\"")).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        raise ColabError(f"cannot read {p}") from None
    except ValueError:
        raise ColabError(f"{p} is not a JSON file") from None
    c = data.get("installed") or data.get("web") or data
    if not c.get("client_id"):
        raise ColabError("this file has no client_id. Download the JSON of a 'Desktop app' OAuth client.")
    if "web" in data:
        raise ColabError("this is a 'Web application' client. Create a 'Desktop app' client instead.")
    return {"client_id": c["client_id"], "client_secret": c.get("client_secret", "")}


def _find_client_json() -> Optional[Path]:
    """The most recent client_secret*.json in Downloads, to suggest it."""
    found = sorted(Path.home().joinpath("Downloads").glob("client_secret*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


class _CodeHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    done = threading.Event()

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in q.items()}
        ok = "code" in q
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Signed in. You can close this tab and go back to bcagent." if ok
               else "Sign-in was cancelled. Go back to bcagent.")
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:3em;color:#003D79'>"
                         f"<h2>bcagent</h2><p>{msg}</p></body></html>".encode())
        type(self).done.set()

    def log_message(self, *a):  # keep the terminal clean
        pass


def browser_login(client: dict) -> dict:
    """OAuth 2.0 for installed apps: loopback redirect + PKCE. Returns the token response."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _CodeHandler.result = {}
    _CodeHandler.done = threading.Event()
    server = socketserver.TCPServer(("127.0.0.1", 0), _CodeHandler)
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    console.print("  [bc]●[/] Opening your browser to sign in with Google…")
    console.print("  [muted]If it doesn't open, visit this link:[/]")
    console.print(url, soft_wrap=True, markup=False)  # no line breaks, so it can be copied
    console.print()
    webbrowser.open(url)
    try:
        with console.status("[bc]Waiting for you to sign in in the browser…[/] [muted](ctrl+c to paste the "
                            "address instead)[/]", spinner="dots", spinner_style=ui.BC_BLUE_LIGHT):
            while not _CodeHandler.done.wait(0.5):
                pass
        result = _CodeHandler.result
    except KeyboardInterrupt:
        # e.g. the browser runs on another computer: the user pastes the address it ended on
        console.print("  [muted]After you allow access, the browser goes to an address starting with "
                      f"{redirect} (it may say it can't connect). Copy that whole address.[/]")
        pasted = console.input("  [bc]address ❯[/] ").strip()
        result = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()}
    finally:
        server.shutdown()
        server.server_close()
    if result.get("error"):
        raise ColabError(f"Google sign-in was not completed ({result['error']}). If it said "
                         "'access_denied', add your Gmail as a test user (step 4) and try again.")
    if result.get("state") != state or not result.get("code"):
        raise ColabError("sign-in did not finish – try [bc]/colab login[/] again")
    return _post_form(TOKEN_URL, {"client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
                                  "code": result["code"], "code_verifier": verifier,
                                  "grant_type": "authorization_code", "redirect_uri": redirect})


def login_permanent() -> bool:
    client = _load(CLIENT_FILE)
    if client and ui.choose("Use the OAuth client you set up before?", ["Yes", "No, set up a new one"]) != 0:
        client = {}
    if not client:
        console.print(Panel(PERMANENT_STEPS, border_style=ui.BC_BLUE_LIGHT, expand=False))
        guess = _find_client_json()
        while not client:
            hint = f" [muted](enter = {guess})[/]" if guess else ""
            try:
                path = console.input(f"  Path to the downloaded JSON file{hint} [bc]❯[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                return False
            if not path and guess:
                path = str(guess)
            if not path:
                return False
            try:
                client = _read_client(path)
            except ColabError as e:
                console.print(f"  [err]✗ {e}[/]")
        _save_private(CLIENT_FILE, client)
    tok = browser_login(client)
    if not tok.get("refresh_token"):
        raise ColabError("Google did not return a refresh token – try [bc]/colab login[/] again")
    data = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600))}
    try:
        data["email"] = _check_token(tok["access_token"]).get("email", "")
    except ColabError:
        pass
    _save_private(TOKEN_FILE, data)
    console.print(f"  [ok]✓[/] signed in{(' as ' + data['email']) if data.get('email') else ''} "
                  "– bcagent will keep this sign-in")
    return True


def login() -> bool:
    """Interactive, guided Google sign-in. Returns True when signed in."""
    if not ui.is_interactive():
        console.print("[err]✗ Google sign-in needs an interactive terminal.[/] Run [bc]bcagent colab login[/] "
                      "first, or set BCAGENT_GOOGLE_TOKEN to an access token.")
        return False
    console.print("[bold]Sign in to Google so bcagent can edit your Colab notebooks.[/]\n"
                  "[muted]Colab notebooks are files in your Google Drive, so bcagent needs permission to read "
                  "and save Drive files. bcagent only opens the notebooks you link with /colab.[/]")
    options = ["Permanent – your own Google OAuth client, about 5 minutes once (recommended)",
               "Quick – paste a token from the OAuth Playground, lasts 1 hour",
               "Cancel"]
    if CLIENT_FILE.exists():
        options[0] = "Permanent – sign in again with your saved OAuth client (recommended)"
    c = ui.choose("How do you want to sign in?", options, cancel=2)
    try:
        if c == 0:
            return login_permanent()
        if c == 1:
            return login_quick()
    except ColabError as e:
        console.print(f"  [err]✗ {e}[/]")
    return False


# -- Drive API -----------------------------------------------------------------------------

class Drive:
    def __init__(self, creds: Optional[Credentials] = None):
        self.creds = creds or Credentials()

    def _request(self, method: str, url: str, data: Optional[bytes] = None,
                 ctype: Optional[str] = None, retry: bool = True):
        headers = {"Authorization": "Bearer " + self.creds.access_token(force_refresh=not retry)}
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 401 and retry and not self.creds.env_token and self.creds.token().get("refresh_token"):
                return self._request(method, url, data, ctype, retry=False)
            try:
                err = json.loads(body).get("error", {})
                msg = err.get("message", body) if isinstance(err, dict) else str(err)
            except ValueError:
                msg = body[:200]
            if e.code == 401:
                raise ColabError("Google rejected the sign-in (it may have expired). "
                                 "Run [bc]/colab login[/].") from None
            if e.code == 404:
                raise ColabError("notebook not found. Check the link, and make sure you signed in with a "
                                 "Google account that can open this notebook.") from None
            if e.code == 403 and "insufficient" in msg.lower():
                raise ColabError(f"the sign-in doesn't allow editing Drive files. Run [bc]/colab login[/] "
                                 f"and allow access to Google Drive ({SCOPE}).") from None
            if e.code == 403 and "has not been used" in msg:
                raise ColabError("the Google Drive API is not enabled in your Google Cloud project. Enable it "
                                 "at https://console.cloud.google.com/apis/library/drive.googleapis.com") from None
            raise ColabError(f"Google Drive error {e.code}: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ColabError(f"cannot reach Google Drive ({e})") from None

    def metadata(self, file_id: str) -> dict:
        q = urllib.parse.urlencode({"fields": FIELDS, "supportsAllDrives": "true"})
        return json.loads(self._request("GET", f"{DRIVE}/files/{file_id}?{q}"))

    def download(self, file_id: str) -> dict:
        raw = self._request("GET", f"{DRIVE}/files/{file_id}?alt=media&supportsAllDrives=true")
        try:
            nb = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ColabError("this Drive file is not a Jupyter/Colab notebook") from None
        if not isinstance(nb, dict) or "cells" not in nb:
            raise ColabError("this Drive file is not a Jupyter/Colab notebook")
        return nb

    def upload(self, file_id: str, nb: dict, mime: str = COLAB_MIME) -> dict:
        body = json.dumps(nb, indent=1, ensure_ascii=False).encode("utf-8")
        q = urllib.parse.urlencode({"uploadType": "media", "fields": FIELDS, "supportsAllDrives": "true"})
        return json.loads(self._request("PATCH", f"{UPLOAD}/files/{file_id}?{q}", body, mime))


# -- linked notebooks in a project ---------------------------------------------------------

def _slug(name: str) -> str:
    stem = re.sub(r"\.ipynb$", "", name, flags=re.I)
    s = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    if not s or s[0].isdigit():
        s = "notebook_" + s
    return s[:50].rstrip("_")


class ColabSync:
    """Keeps local mirror files and their Colab notebooks in sync."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_dir = self.root / ".bcagent"
        self.state_file = self.state_dir / "colab.json"
        self.links: Dict[str, dict] = _load(self.state_file).get("notebooks", {})
        self.drive = Drive()

    # state --------------------------------------------------------------------
    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"notebooks": self.links}, indent=2), encoding="utf-8")

    def _base_path(self, file_id: str) -> Path:
        return self.state_dir / "colab" / f"{file_id}.ipynb"

    def _base(self, file_id: str) -> dict:
        return _load(self._base_path(file_id))

    def _store_base(self, file_id: str, nb: dict) -> None:
        p = self._base_path(file_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(nb, ensure_ascii=False), encoding="utf-8")

    def is_mirror(self, rel: str) -> bool:
        return rel in self.links

    def mirror_for(self, file_id: str) -> Optional[str]:
        for rel, link in self.links.items():
            if link["id"] == file_id:
                return rel
        return None

    # sign-in ------------------------------------------------------------------
    def ensure_signed_in(self) -> bool:
        if self.drive.creds.signed_in():
            return True
        console.print("[warn]bcagent isn't signed in to Google yet.[/]")
        return login()

    # actions ------------------------------------------------------------------
    def link(self, url: str) -> str:
        """Download the notebook and write its mirror file. Returns the mirror path (relative)."""
        file_id = parse_url(url)
        meta = self.drive.metadata(file_id)
        nb = self.drive.download(file_id)
        rel = self.mirror_for(file_id)
        if not rel:
            base = f"{MIRROR_DIR}/{_slug(meta.get('name', file_id))}"
            rel, n = f"{base}.py", 2
            while (self.root / rel).exists() or rel in self.links:
                rel, n = f"{base}_{n}.py", n + 1
        self._write_mirror(rel, nb, meta)
        self.links[rel] = {"id": file_id, "name": meta.get("name", ""), "version": meta.get("version"),
                           "mime": meta.get("mimeType") or COLAB_MIME, "url": colab_url(file_id),
                           "can_edit": bool(meta.get("capabilities", {}).get("canEdit", True))}
        self._save()
        return rel

    def _write_mirror(self, rel: str, nb: dict, meta: dict) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(notebook_to_text(nb, meta.get("name", "")), encoding="utf-8")
        self._store_base(meta["id"], nb)

    def pull(self, rel: str) -> int:
        link = self.links[rel]
        meta = self.drive.metadata(link["id"])
        nb = self.drive.download(link["id"])
        self._write_mirror(rel, nb, meta)
        link.update(version=meta.get("version"), name=meta.get("name", link["name"]))
        self._save()
        return len(nb.get("cells", []))

    def push(self, rel: str, ask: bool = True, force: bool = False) -> bool:
        """Upload the mirror file to Colab. Returns True when uploaded."""
        link = self.links[rel]
        if not link.get("can_edit", True):
            raise ColabError("you can only view this notebook, not edit it. In Colab choose File ▸ Save a "
                             "copy in Drive and link the copy with /colab <url>.")
        text = (self.root / rel).read_text(encoding="utf-8")
        cells = text_to_cells(text)
        if not cells:
            raise ColabError(f"{rel} has no cells (each cell must start with a '# %%' line); not uploaded")
        meta = self.drive.metadata(link["id"])
        if not force and link.get("version") and meta.get("version") != link["version"]:
            if not ask or not ui.is_interactive():
                raise ColabError("the notebook was changed in Colab since bcagent downloaded it. Run "
                                 "[bc]/colab pull[/] to get those changes (or [bc]/colab push --force[/]).")
            c = ui.choose("The notebook was changed in Colab after bcagent downloaded it.",
                          ["Upload bcagent's version anyway (Colab's newer changes are replaced)",
                           "Don't upload now (use /colab pull to get Colab's version)"], cancel=1)
            if c != 0:
                return False
        nb = merge_cells(self._base(link["id"]), cells)
        out = self.drive.upload(link["id"], nb, link.get("mime") or COLAB_MIME)
        self._store_base(link["id"], nb)
        link["version"] = out.get("version")
        self._save()
        return True

    def unlink(self, rel: str) -> None:
        link = self.links.pop(rel)
        try:
            self._base_path(link["id"]).unlink()
        except OSError:
            pass
        self._save()
