"""Git and GitHub: `/git` (status, diff, commit, push, pull, branch, revert, ...) and `/github`
(sign in, create a repository, pull requests).

Everything runs the real `git` program in the project folder. GitHub access uses a token:
  1. $GH_TOKEN / $GITHUB_TOKEN
  2. the token saved by `/github login` in ~/.bcagent/github/token.json (only readable by you)
  3. the GitHub CLI (`gh auth token`) when it is installed and signed in
For HTTPS GitHub remotes bcagent hands the token to git through a credential helper set in the
environment (GIT_CONFIG_COUNT), so the token is never written into .git/config or shown in a command.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import ui
from .config import HOME
from .ui import console

API = "https://api.github.com"
TOKEN_FILE = HOME / "github" / "token.json"
CLASSIC_TOKEN_URL = ("https://github.com/settings/tokens/new?scopes=repo,workflow,write:public_key"
                     "&description=bcagent")
FINE_TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"
SECRET_RE = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|client_secret.*\.json|"
                       r"credentials\.json|token\.json|.*\.p12)$", re.I)
SECRET_OK = re.compile(r"\.env\.(example|sample|template)$", re.I)
PY_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
venv/
env/
.env
*.egg-info/
dist/
build/
.ipynb_checkpoints/
.pytest_cache/
.DS_Store
.bcagent/colab/
"""


class GitError(RuntimeError):
    pass


# -- GitHub token ------------------------------------------------------------------------

_gh_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def _gh_cli_token() -> Optional[str]:
    if not shutil.which("gh"):
        return None
    hit = _gh_cache.get("t")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        tok = r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        tok = None
    _gh_cache["t"] = (time.time(), tok)
    return tok


def _saved() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def token_source() -> Tuple[Optional[str], str]:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var], f"${var}"
    if _saved().get("token"):
        return _saved()["token"], "bcagent (/github login)"
    tok = _gh_cli_token()
    if tok:
        return tok, "GitHub CLI (gh)"
    return None, "not signed in"


def token() -> Optional[str]:
    return token_source()[0]


def auth_env(env: Optional[dict] = None) -> dict:
    """Environment that lets git use the GitHub token for https://github.com remotes."""
    env = dict(os.environ if env is None else env)
    tok = token()
    if not tok:
        return env
    n = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    helper = '!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=$BCAGENT_GH_TOKEN"; }; f'
    for key, value in (("credential.https://github.com.helper", ""),  # ignore other (stale) helpers
                       ("credential.https://github.com.helper", helper)):
        env[f"GIT_CONFIG_KEY_{n}"] = key
        env[f"GIT_CONFIG_VALUE_{n}"] = value
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    env["BCAGENT_GH_TOKEN"] = tok
    return env


def api(method: str, path: str, body: Optional[dict] = None) -> Tuple[dict, dict]:
    """Call the GitHub REST API. Returns (json, headers)."""
    tok = token()
    if not tok:
        raise GitError("not signed in to GitHub. Run [bc]/github login[/].")
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28",
                                          "User-Agent": "bcagent"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode(errors="replace"))
            msg = j.get("message", "")
            details = "; ".join(x.get("message", "") or x.get("code", "") for x in j.get("errors", [])
                                if isinstance(x, dict))
            msg = f"{msg} ({details})" if details else msg
        except ValueError:
            msg = e.reason
        if e.code == 401:
            raise GitError("GitHub rejected the token (expired or revoked). Run [bc]/github login[/].") from None
        if e.code in (403, 404) and "Resource not accessible" in msg:
            raise GitError(f"the token is missing a permission: {msg}. Create a token with the "
                           "'repo' scope (run [bc]/github login[/]).") from None
        raise GitError(f"GitHub error {e.code}: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise GitError(f"cannot reach GitHub ({e})") from None


def whoami() -> Optional[dict]:
    try:
        return api("GET", "/user")[0]
    except GitError:
        return None


# -- guided GitHub sign-in ---------------------------------------------------------------

TOKEN_STEPS = f"""[bold]Sign in with a personal access token[/] [muted](about 1 minute)[/]

  1. Open [bc]{CLASSIC_TOKEN_URL}[/]
     (sign in to GitHub if asked). The link already fills in the name [bold]bcagent[/] and the
     scopes [bold]repo[/], [bold]workflow[/] and [bold]write:public_key[/] (the last one lets bcagent add SSH keys).
  2. Pick an [bold]Expiration[/] (for example 90 days) and click [bold]Generate token[/] at the bottom.
  3. Copy the token (it starts with [bc]ghp_[/]) and paste it below. GitHub shows it only once.

[muted]Prefer a fine-grained token? Use {FINE_TOKEN_URL} with Repository access "All repositories" and
the permissions Contents, Pull requests and Administration set to "Read and write".
The token is saved only on this computer, in {TOKEN_FILE}.[/]"""


def _save_token(tok: str, login: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"token": tok, "login": login}), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, TOKEN_FILE)


def login_token() -> bool:
    import getpass
    console.print(Panel(TOKEN_STEPS, border_style=ui.BC_BLUE_LIGHT, expand=False))
    if ui.choose("Open the GitHub token page in your browser?", ["Yes", "No, I'll open it myself"]) == 0:
        webbrowser.open(CLASSIC_TOKEN_URL)
    for _ in range(3):
        try:
            tok = getpass.getpass("  GitHub token (hidden while you type, enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if not tok:
            return False
        old = os.environ.pop("GH_TOKEN", None), os.environ.pop("GITHUB_TOKEN", None)
        os.environ["GH_TOKEN"] = tok
        try:
            user, headers = api("GET", "/user")
        except GitError as e:
            console.print(f"  [err]✗ {e}[/]")
            continue
        finally:
            del os.environ["GH_TOKEN"]
            for k, v in zip(("GH_TOKEN", "GITHUB_TOKEN"), old):
                if v:
                    os.environ[k] = v
        scopes = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes")
        if scopes is not None and tok.startswith("ghp_") and "repo" not in [s.strip() for s in scopes.split(",")]:
            console.print("  [warn]⚠ this token has no 'repo' scope, so pushing and creating repositories "
                          "will fail. Create a new token with the link above.[/]")
        _save_token(tok, user.get("login", ""))
        console.print(f"  [ok]✓[/] signed in to GitHub as [bc]{escape(user.get('login', '?'))}[/]")
        return True
    return False


def login_gh_cli() -> bool:
    console.print("[muted]The GitHub CLI asks a few questions: choose GitHub.com, HTTPS, and 'Login with a web "
                  "browser'. It shows a one-time code; paste it on the page that opens.[/]")
    try:
        code = subprocess.call(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https",
                                "--web", "--scopes", "repo,workflow,admin:public_key"])
    except (OSError, KeyboardInterrupt):
        return False
    _gh_cache.clear()
    user = whoami() if code == 0 else None
    if user:
        console.print(f"  [ok]✓[/] signed in to GitHub as [bc]{escape(user.get('login', '?'))}[/]")
        return True
    console.print("  [err]✗ GitHub CLI sign-in did not finish[/]")
    return False


def login() -> bool:
    if not ui.is_interactive():
        console.print("[err]✗ GitHub sign-in needs an interactive terminal.[/] Run [bc]bcagent github login[/] "
                      "or set GH_TOKEN.")
        return False
    console.print("[bold]Sign in to GitHub so bcagent can push, create repositories and open pull requests.[/]")
    opts, kinds = [], []
    if shutil.which("gh"):
        opts.append("GitHub CLI – sign in with your browser (recommended)")
        kinds.append("gh")
    opts.append("Personal access token – create one on github.com and paste it" +
                ("" if kinds else " (recommended)"))
    kinds.append("token")
    opts.append("SSH key – for git@github.com addresses (push and pull only)")
    kinds.append("ssh")
    opts.append("Cancel")
    c = ui.choose("How do you want to sign in?", opts, cancel=len(opts) - 1)
    if c >= len(kinds):
        return False
    if kinds[c] == "ssh":
        return ssh_setup()
    ok = login_gh_cli() if kinds[c] == "gh" else login_token()
    if not shutil.which("gh") and ok:
        console.print("  [muted]Tip: the GitHub CLI (https://cli.github.com) is optional; bcagent works "
                      "without it.[/]")
    return ok


def logout() -> bool:
    found = TOKEN_FILE.exists()
    try:
        TOKEN_FILE.unlink()
    except OSError:
        pass
    if token_source()[1].startswith("GitHub CLI"):
        console.print("[muted]bcagent still uses the GitHub CLI sign-in; run[/] [bc]gh auth logout[/] "
                      "[muted]to remove it.[/]")
    return found


def ensure_login() -> bool:
    if token():
        return True
    console.print("[warn]bcagent isn't signed in to GitHub yet.[/]")
    return login()


# -- SSH keys ----------------------------------------------------------------------------

SSH_DIR = Path.home() / ".ssh"
SSH_NEW_KEY_URL = "https://github.com/settings/ssh/new"
SSH_KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")


def is_ssh(url: Optional[str]) -> bool:
    return bool(url) and (url.startswith("git@") or url.startswith("ssh://"))


def https_url(url: str) -> Optional[str]:
    gh = parse_github(url)
    return f"https://github.com/{gh[0]}/{gh[1]}.git" if gh else None


def ssh_keys() -> List[Path]:
    """Existing public keys in ~/.ssh (preferred types first)."""
    found = [SSH_DIR / f"{n}.pub" for n in SSH_KEY_NAMES if (SSH_DIR / f"{n}.pub").is_file()]
    others = sorted(p for p in SSH_DIR.glob("*.pub") if p not in found) if SSH_DIR.is_dir() else []
    return found + others


def ensure_known_hosts() -> None:
    """Add GitHub's published host keys to ~/.ssh/known_hosts, so the first connection doesn't stop
    at "Are you sure you want to continue connecting?" (keys come from api.github.com/meta over HTTPS)."""
    if not shutil.which("ssh-keygen"):
        return
    try:
        kh = SSH_DIR / "known_hosts"
        if kh.exists() and subprocess.run(["ssh-keygen", "-F", "github.com", "-f", str(kh)],
                                          capture_output=True, timeout=10).returncode == 0:
            return
        req = urllib.request.Request(API + "/meta", headers={"User-Agent": "bcagent"})
        with urllib.request.urlopen(req, timeout=15) as r:
            keys = json.load(r).get("ssh_keys", [])
    except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError):
        return
    if not keys:
        return
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    with open(SSH_DIR / "known_hosts", "a", encoding="utf-8") as f:
        for k in keys:
            f.write(f"github.com {k}\n")


def ssh_check() -> Tuple[bool, str]:
    """(works, GitHub user name or the error) for `ssh -T git@github.com`."""
    if not shutil.which("ssh"):
        return False, "ssh is not installed"
    ensure_known_hosts()
    try:
        r = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                            "-o", "StrictHostKeyChecking=accept-new", "git@github.com"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"Hi ([^!]+)! You've successfully authenticated", out)
    if m:
        return True, m.group(1)
    return False, out.splitlines()[-1] if out else f"ssh exit code {r.returncode}"


def _copy(text: str) -> bool:
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-16le" if cmd[0] == "clip" else "utf-8"),
                               timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def _has_passphrase(private: Path) -> bool:
    try:
        r = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(private)], capture_output=True, timeout=10)
        return r.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return False


def _load_into_agent(private: Path) -> None:
    """Keys with a passphrase: add them to the ssh-agent (and the macOS keychain) so git doesn't ask
    for the passphrase every time."""
    import platform
    if platform.system() == "Darwin":
        cfg = SSH_DIR / "config"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        if "UseKeychain" not in text:
            with open(cfg, "a", encoding="utf-8") as f:
                f.write(f"\nHost github.com\n  AddKeysToAgent yes\n  UseKeychain yes\n  IdentityFile ~/.ssh/{private.name}\n")
        subprocess.call(["ssh-add", "--apple-use-keychain", str(private)])
        return
    if os.name == "nt":
        console.print("  [muted]If git keeps asking for the passphrase, start the agent once in an administrator "
                      "PowerShell:[/] [bc]Get-Service ssh-agent | Set-Service -StartupType Automatic; "
                      "Start-Service ssh-agent[/] [muted]then run[/] [bc]ssh-add[/]")
    if subprocess.call(["ssh-add", str(private)]) != 0:
        console.print("  [muted]No ssh-agent is running; git will ask for the passphrase when it connects.[/]")


def _upload_key(pub: Path) -> bool:
    """Add the public key to the GitHub account with the API. False when the token can't do it."""
    import platform
    key = pub.read_text(encoding="utf-8").strip()
    body = " ".join(key.split()[:2])
    try:
        existing, _ = api("GET", "/user/keys?per_page=100")
        if any(k.get("key", "").strip() == body for k in existing):
            console.print("  [ok]⎿[/]  this key is already on your GitHub account")
            return True
        api("POST", "/user/keys", {"title": f"bcagent {platform.node() or 'computer'}", "key": key})
    except GitError as e:
        if "already in use" in str(e):
            console.print("  [ok]⎿[/]  this key is already on GitHub")
            return True
        return False
    console.print("  [ok]⎿[/]  added the key to your GitHub account")
    return True


SSH_INTRO = """[bold]Set up an SSH key for GitHub[/] [muted](about 2 minutes, only once per computer)[/]

An SSH key is a pair of files in ~/.ssh: a private key that never leaves this computer, and a \
public key (.pub) that you give to GitHub. Then git can push and pull with git@github.com \
addresses without a password."""


def _manual_upload(pub: Path) -> None:
    key = pub.read_text(encoding="utf-8").strip()
    copied = _copy(key)
    console.print(Panel(
        f"""[bold]Add the key to GitHub[/]

  1. Open [bc]{SSH_NEW_KEY_URL}[/] (sign in if asked).
  2. Title: anything, e.g. [bold]my laptop[/]. Key type: [bold]Authentication Key[/].
  3. {"Paste the key into the [bold]Key[/] box – bcagent already copied it to your clipboard." if copied else
          "Copy this whole line into the [bold]Key[/] box:"}
  4. Click [bold]Add SSH key[/] (GitHub may ask you to confirm with your password or 2FA).""",
        border_style=ui.BC_BLUE_LIGHT, expand=False))
    if not copied:
        console.print(key, soft_wrap=True, markup=False)
    if ui.choose("Open that GitHub page in your browser?", ["Yes", "No, I'll open it myself"]) == 0:
        webbrowser.open(SSH_NEW_KEY_URL)
    try:
        console.input("  [muted]Press enter after you click 'Add SSH key'…[/] ")
    except (EOFError, KeyboardInterrupt):
        pass


def ssh_setup() -> bool:
    """Guided SSH set-up for GitHub. Returns True when `ssh -T git@github.com` works."""
    import getpass
    import platform
    if not ui.is_interactive():
        console.print("[err]✗ SSH set-up needs an interactive terminal.[/] Run [bc]bcagent github ssh[/].")
        return False
    if not (shutil.which("ssh") and shutil.which("ssh-keygen")):
        how = {"Windows": "Settings ▸ System ▸ Optional features ▸ Add a feature ▸ [bold]OpenSSH Client[/]",
               "Darwin": "it comes with macOS – run [bc]xcode-select --install[/]",
               }.get(platform.system(), "install the [bold]openssh-client[/] package "
                                       "(e.g. [bc]sudo apt install openssh-client[/])")
        console.print(f"[err]✗ the ssh program is not installed.[/] To get it: {how}.\n"
                      "  [muted]Or skip SSH: use HTTPS with[/] [bc]/github login[/] [muted](then bcagent switches "
                      "the remote for you).[/]")
        return False
    with console.status("[bc]Checking your SSH connection to GitHub…[/]", spinner="dots",
                        spinner_style=ui.BC_BLUE_LIGHT):
        ok, who = ssh_check()
    if ok:
        console.print(f"[ok]✓[/] SSH already works – GitHub knows you as [bc]{escape(who)}[/]")
        return True
    console.print(Panel(SSH_INTRO, border_style=ui.BC_BLUE_LIGHT, expand=False))
    keys = ssh_keys()
    pub: Optional[Path] = None
    if keys:
        opts = [f"Use my existing key {ui.shorten(k)}" for k in keys[:3]] + ["Create a new key", "Cancel"]
        c = ui.choose("bcagent found SSH keys on this computer.", opts, cancel=len(opts) - 1)
        if c == len(opts) - 1:
            return False
        if c < len(keys[:3]):
            pub = keys[c]
    if pub is None:
        name = "id_ed25519"
        n = 2
        while (SSH_DIR / name).exists():
            name, n = f"id_ed25519_github{'' if n == 2 else n}", n + 1
        private = SSH_DIR / name
        email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True,
                               text=True).stdout.strip() if shutil.which("git") else ""
        comment = email or f"{getpass.getuser()}@{platform.node()}"
        console.print("  [muted]ssh-keygen will ask for a [bold]passphrase[/]: a password that protects the key. "
                      "It's optional – press enter twice for none (fine on your own computer; use one on "
                      "shared computers).[/]")
        SSH_DIR.mkdir(mode=0o700, exist_ok=True)
        if subprocess.call(["ssh-keygen", "-t", "ed25519", "-C", comment, "-f", str(private)]) != 0:
            console.print("[err]✗ the key was not created[/]")
            return False
        pub = private.with_name(private.name + ".pub")
        console.print(f"  [ok]⎿[/]  created [bc]{ui.shorten(private)}[/] (private – never share it) and "
                      f"[bc]{ui.shorten(pub)}[/]")
        if name != "id_ed25519":  # not a default name: tell ssh to use it for github.com
            with open(SSH_DIR / "config", "a", encoding="utf-8") as f:
                f.write(f"\nHost github.com\n  IdentityFile ~/.ssh/{name}\n")
    private = pub.with_suffix("")
    if private.exists() and _has_passphrase(private):
        _load_into_agent(private)
    uploaded = bool(token()) and _upload_key(pub)
    if not uploaded:
        _manual_upload(pub)
    for attempt in range(3):
        with console.status("[bc]Testing the SSH connection…[/]", spinner="dots", spinner_style=ui.BC_BLUE_LIGHT):
            ok, who = ssh_check()
        if ok:
            console.print(f"[ok]✓[/] SSH works – GitHub knows you as [bc]{escape(who)}[/]")
            return True
        if attempt == 2:
            break
        c = ui.choose(f"GitHub didn't accept the key yet ({escape(who)}).",
                      ["Try again (after adding the key on GitHub)", "Show the steps to add the key", "Give up"],
                      cancel=2)
        if c == 2:
            break
        if c == 1:
            _manual_upload(pub)
    console.print("[err]✗ SSH still doesn't work.[/] Check that the key on "
                  "[bc]https://github.com/settings/keys[/] matches " + ui.shorten(pub) +
                  ". Or use HTTPS instead: [bc]/github login[/].")
    return False


# -- running git -------------------------------------------------------------------------

class Repo:
    def __init__(self, root: Path):
        self.root = Path(root)

    def run(self, *args: str, check: bool = True, quiet: bool = True) -> Tuple[int, str]:
        """Run git. With quiet=False the output streams to the terminal (push/pull/clone)."""
        if not shutil.which("git"):
            raise GitError("git is not installed. Get it from https://git-scm.com/downloads "
                           "(macOS: run `xcode-select --install`).")
        env = auth_env()
        env.setdefault("GIT_TERMINAL_PROMPT", "0" if token() else "1")
        try:
            if quiet:
                r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                                   env=env, encoding="utf-8", errors="replace")
                out = (r.stdout + r.stderr).strip()
                code = r.returncode
            else:
                console.print(f"[bc]$[/] git {escape(' '.join(args))}", highlight=False)
                code = subprocess.call(["git", *args], cwd=self.root, env=env)
                out = ""
        except OSError as e:
            raise GitError(str(e)) from None
        if check and code != 0:
            raise GitError(friendly(out) or f"git {args[0]} failed (exit code {code})")
        return code, out

    def out(self, *args: str) -> str:
        return self.run(*args)[1]

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False)[0] == 0

    # facts --------------------------------------------------------------------
    def is_repo(self) -> bool:
        try:
            return self.ok("rev-parse", "--is-inside-work-tree")
        except GitError:
            return False

    def require(self) -> None:
        if not self.is_repo():
            raise GitError("this folder is not a git repository yet. Run [bc]/git init[/] "
                           "(or [bc]/github create[/] to also put it on GitHub).")

    def branch(self) -> str:
        return self.out("branch", "--show-current") or "HEAD"

    def has_commits(self) -> bool:
        return self.ok("rev-parse", "--verify", "-q", "HEAD")

    def upstream(self) -> Optional[str]:
        code, out = self.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
        return out if code == 0 else None

    def remote_url(self, name: str = "origin") -> Optional[str]:
        code, out = self.run("remote", "get-url", name, check=False)
        return out if code == 0 else None

    def changes(self) -> List[Tuple[str, str]]:
        """[(status XY, path)] from `git status --porcelain`."""
        out = self.run("status", "--porcelain", "-uall")[1]
        return [(l[:2], l[3:].strip('"')) for l in out.splitlines() if l.strip()]

    def dirty(self) -> bool:
        """Uncommitted changes to tracked files (untracked files don't block revert/merge)."""
        return bool(self.run("status", "--porcelain", "-uno")[1])

    def ahead_behind(self) -> Tuple[int, int]:
        if not self.upstream():
            return 0, 0
        code, out = self.run("rev-list", "--left-right", "--count", "HEAD...@{u}", check=False)
        try:
            a, b = out.split()
            return int(a), int(b)
        except ValueError:
            return 0, 0

    def identity_ok(self) -> bool:
        return bool(self.run("config", "user.name", check=False)[1] and
                    self.run("config", "user.email", check=False)[1])


def friendly(out: str) -> str:
    """Turn common git errors into advice."""
    low = out.lower()
    if "permission denied (publickey)" in low or "host key verification failed" in low:
        return ("GitHub refused the SSH connection – this computer has no SSH key GitHub knows. Run "
                "[bc]/github ssh[/] to set one up (bcagent guides you), or switch to HTTPS with "
                "[bc]/github login[/].")
    if "authentication failed" in low or "could not read username" in low or "invalid username or password" in low:
        return "GitHub refused the sign-in. Run [bc]/github login[/] and try again."
    if "permission to" in low and "denied" in low or "403" in low and "github" in low:
        return "your GitHub account can't push to this repository (no write access, or the token lacks the 'repo' scope)."
    if "non-fast-forward" in low or "fetch first" in low or "rejected" in low and "behind" in low:
        return "GitHub has commits you don't have yet. Run [bc]/git pull[/] first, then push again."
    if "please tell me who you are" in low:
        return "git doesn't know your name and email yet. Run [bc]/git commit[/] again – bcagent will ask."
    if "conflict" in low:
        files = re.findall(r"CONFLICT \([^)]*\): .*? in (\S+)", out)
        return ("merge conflict" + (f" in {', '.join(files)}" if files else "") +
                ". Fix the files (look for <<<<<<< markers, or ask bcagent: \"resolve the merge conflict in "
                "<file>\"), then run [bc]/git commit[/]. Or cancel with [bc]/git merge --abort[/].")
    if "does not appear to be a git repository" in low or "no such remote" in low:
        return "this repository has no GitHub remote yet. Run [bc]/github create[/] or [bc]/git remote <url>[/]."
    if "repository not found" in low:
        return "GitHub repository not found (check the name, and that you are signed in: [bc]/github login[/])."
    return out.strip()[-1500:]


def parse_github(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """(owner, repo) of a GitHub remote URL."""
    if not url:
        return None
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return (m.group(1), m.group(2)) if m else None


def to_url(spec: str) -> str:
    """`owner/repo` -> https://github.com/owner/repo.git; URLs and local paths are kept."""
    spec = spec.strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", spec) and not Path(spec).exists():
        return f"https://github.com/{re.sub(r'[.]git$', '', spec)}.git"
    return spec


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "my-project"


# -- commit messages written by the local model ------------------------------------------

def suggest_message(session, diff: str) -> Optional[str]:
    if not session or not getattr(session, "client", None):
        return None
    prompt = ("Write a git commit message for the changes below.\n"
              "Rules: first line is a short summary in the imperative mood (\"Add ...\", \"Fix ...\"), "
              "at most 65 characters, no period. Optionally a blank line and up to 3 lines starting with "
              "'- '. Output ONLY the commit message, no code block, no quotes.\n\n" + diff[:6000])
    msgs = [{"role": "system", "content": "You write clear, short git commit messages."},
            {"role": "user", "content": prompt}]
    try:
        with console.status("[bc]Writing a commit message…[/]", spinner="dots", spinner_style=ui.BC_BLUE_LIGHT):
            text = "".join(session.client.chat_stream(session.agent.model, msgs,
                                                      {"temperature": 0.2, "num_predict": 160,
                                                       "num_ctx": int(session.cfg.get("num_ctx", 8192))}))
    except Exception:  # Ollama down, interrupted... -> the user types it
        return None
    text = re.sub(r"^```\w*|```$", "", text.strip(), flags=re.M).strip().strip('"\'`')
    lines = [l.rstrip() for l in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    lines[0] = re.sub(r"^(commit message:|summary:)\s*", "", lines[0], flags=re.I)[:72].rstrip(".")
    return "\n".join(lines[:6]).strip() or None


# -- the /git and /github commands -------------------------------------------------------

GIT_HELP = """[bold]/git[/] – version control for this folder

  [bc]/git[/]                    status: branch, changed files, commits to push
  [bc]/git init[/]               start a repository here (with a Python .gitignore)
  [bc]/git clone <url|owner/repo>[/]  copy a repository into this folder
  [bc]/git diff [file][/]        show what changed
  [bc]/git commit [message][/]   commit all changes (bcagent writes the message if you leave it out)
  [bc]/git push[/] · [bc]/git pull[/]     send / get commits from GitHub
  [bc]/git log[/]                recent commits
  [bc]/git branch [name][/]      list branches, or create one and switch to it
  [bc]/git switch <branch>[/]    switch branch  ·  [bc]/git merge <branch>[/]
  [bc]/git revert [commit][/]    undo a commit with a new commit (safe after pushing; default: the last one)
  [bc]/git uncommit[/]           undo the last commit but keep its changes (only before pushing)
  [bc]/git discard <file|all>[/] throw away uncommitted changes
  [bc]/git stash[/] · [bc]/git stash pop[/]
  [bc]/git remote [url][/]       show or set the GitHub address (origin)
  [bc]/git <anything else>[/]    runs that git command, e.g. [bc]/git tag v1.0[/]

[bold]/github[/] – GitHub
  [bc]/github login[/] · [bc]/github logout[/] · [bc]/github status[/]
  [bc]/github ssh[/]                       set up an SSH key for git@github.com addresses (guided)
  [bc]/github create [name] [--public][/]  create a GitHub repository for this folder and push it
  [bc]/github pr [title][/]                open a pull request for the current branch
  [bc]/github prs[/]                       list open pull requests
  [bc]/github open[/]                      open the repository in your browser"""


class GitCommands:
    def __init__(self, root: Path, session=None):
        self.repo = Repo(root)
        self.session = session

    # helpers ------------------------------------------------------------------
    def confirm(self, question: str, yes: str = "Yes") -> bool:
        if not ui.is_interactive():
            return bool(self.session and self.session.auto)
        return ui.choose(question, [yes, "No"], cancel=1) == 0

    def note(self, text: str) -> None:
        if self.session is not None:
            self.session.notes.append(text)

    def show_diff(self, text: str) -> None:
        if not text.strip():
            console.print("[muted]No changes.[/]")
            return
        lines = text.splitlines()
        if len(lines) > 400:
            text = "\n".join(lines[:400]) + f"\n… {len(lines) - 400} more lines"
        console.print(Syntax(text, "diff", theme="ansi_dark", background_color="default", word_wrap=True))

    def ensure_identity(self) -> bool:
        if self.repo.identity_ok():
            return True
        user = whoami() if token() else None
        name = (user or {}).get("name") or (user or {}).get("login") or ""
        email = ""
        if user and user.get("id"):
            email = f"{user['id']}+{user['login']}@users.noreply.github.com"
        console.print("[bold]git needs your name and email for commits[/] [muted](they appear in the history).[/]")
        if not ui.is_interactive():
            return False
        name = console.input(f"  Name{f' [muted](enter = {name})[/]' if name else ''} [bc]❯[/] ").strip() or name
        email = console.input(f"  Email{f' [muted](enter = {email})[/]' if email else ''} [bc]❯[/] ").strip() or email
        if not name or not email:
            return False
        scope = ui.choose("Use this for", ["All your repositories (git config --global)", "Only this repository"])
        flag = ["--global"] if scope == 0 else []
        self.repo.run("config", *flag, "user.name", name)
        self.repo.run("config", *flag, "user.email", email)
        return True

    def net(self, *args: str, url: Optional[str] = None, quiet: bool = False) -> Tuple[int, str]:
        """Run a git command that talks to GitHub. If it fails because SSH isn't set up, guide the user
        (create / upload a key, or switch the remote to HTTPS) and try once more."""
        url = url or self.repo.remote_url()
        if is_ssh(url):
            ensure_known_hosts()
        code, out = self.repo.run(*args, check=False, quiet=quiet)
        if code == 0 or not is_ssh(url) or not ui.is_interactive():
            return code, out
        ok, why = ssh_check()
        if ok:
            return code, out  # SSH works; the problem is something else
        console.print(f"[warn]GitHub refused the SSH connection[/] [muted]({escape(why)})[/]\n"
                      "[muted]This address uses SSH (git@github.com), and this computer has no SSH key that "
                      "GitHub knows.[/]")
        opts = ["Set up an SSH key now (bcagent guides you)"]
        alt = https_url(url) if url and self.repo.is_repo() and url == self.repo.remote_url() else None
        if alt:
            opts.append(f"Switch this repository to HTTPS ({alt}) and sign in with /github login")
        opts.append("Cancel")
        c = ui.choose("How do you want to connect?", opts, cancel=len(opts) - 1)
        if c == 0:
            if not ssh_setup():
                return code, out
        elif alt and c == 1:
            self.repo.run("remote", "set-url", "origin", alt)
            console.print(f"  [ok]⎿[/]  origin → {escape(alt)}")
            if not ensure_login():
                return code, out
        else:
            return code, out
        console.print("[muted]Trying again…[/]")
        return self.repo.run(*args, check=False, quiet=quiet)

    # git ----------------------------------------------------------------------
    def status(self) -> None:
        r = self.repo
        if not r.is_repo():
            console.print("[muted]This folder is not a git repository.[/] Start one with [bc]/git init[/], or "
                          "[bc]/github create[/] to put it on GitHub too.")
            return
        branch = r.branch()
        up = r.upstream()
        a, b = r.ahead_behind()
        line = f"On branch [bc]{escape(branch)}[/]"
        if up:
            line += f" → {escape(up)}"
            if a:
                line += f" · [warn]{a} commit(s) to push[/] ([bc]/git push[/])"
            if b:
                line += f" · [warn]{b} new commit(s) on GitHub[/] ([bc]/git pull[/])"
            if not a and not b:
                line += " · [ok]up to date[/]"
        elif r.remote_url():
            line += " · [muted]not pushed yet ([/][bc]/git push[/][muted])[/]"
        else:
            line += " · [muted]no GitHub remote ([/][bc]/github create[/][muted])[/]"
        console.print(line)
        changes = r.changes()
        if not changes:
            console.print("[ok]✓[/] no uncommitted changes")
            return
        label = {"M": "[warn]modified[/]", "A": "[ok]added[/]", "D": "[del]deleted[/]", "R": "renamed",
                 "?": "[ok]new[/]", "U": "[err]conflict[/]"}
        t = Table.grid(padding=(0, 2))
        for xy, path in changes[:60]:
            k = "U" if "U" in xy else (xy.strip() or "M")[0]
            t.add_row("  " + label.get(k, xy), escape(path))
        console.print(t)
        if len(changes) > 60:
            console.print(f"  [muted]… and {len(changes) - 60} more[/]")
        console.print("[muted]Commit them with[/] [bc]/git commit[/]")

    def init(self) -> None:
        r = self.repo
        if r.is_repo():
            console.print("[muted]Already a git repository.[/]")
            return
        if not r.ok("init", "-b", "main"):
            r.run("init")
        gi = r.root / ".gitignore"
        if not gi.exists():
            gi.write_text(PY_GITIGNORE, encoding="utf-8")
            console.print("  [ok]⎿[/]  created [bc].gitignore[/] for Python")
        console.print(f"[ok]✓[/] git repository created on branch [bc]{escape(r.branch())}[/]. "
                      "Next: [bc]/git commit[/], then [bc]/github create[/] to put it on GitHub.")

    def clone(self, spec: str) -> None:
        if not spec:
            raise GitError("usage: /git clone <url | owner/repo>")
        url = to_url(spec)
        root = self.repo.root
        empty = not any(p for p in root.iterdir() if p.name not in (".DS_Store", ".bcagent"))
        name = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        target = "." if empty else name
        code, _ = self.net("clone", url, target, url=url)
        if code != 0:
            raise GitError("clone failed. Check the address" + ("" if token() else
                           ", and for a private repository sign in first with [bc]/github login[/]") + ".")
        if target == ".":
            console.print("[ok]✓[/] cloned into this folder")
        else:
            console.print(f"[ok]✓[/] cloned into [bc]{name}/[/]. To work on it, leave and run "
                          f"[bc]bcagent -C {name}[/] (or tag files with @{name}/…).")

    def diff(self, arg: str) -> None:
        self.repo.require()
        files = arg.split()
        text = self.repo.out("diff", "HEAD", "--", *files) if self.repo.has_commits() else \
            self.repo.out("diff", "--cached", "--", *files)
        new = [p for xy, p in self.repo.changes() if xy == "??" and (not files or p in files)]
        self.show_diff(text)
        if new:
            console.print("[muted]new files (not tracked yet):[/] " + ", ".join(escape(p) for p in new[:20]))

    def commit(self, message: str) -> None:
        r = self.repo
        r.require()
        if r.out("diff", "--name-only", "--diff-filter=U"):
            console.print("[warn]There are unresolved merge conflicts.[/] Fix the files first.")
        staged = r.out("diff", "--cached", "--name-only")
        we_staged = not staged

        def unstage() -> None:
            if we_staged:
                if r.has_commits():
                    r.run("reset", "-q", check=False)
                else:
                    r.run("rm", "-r", "-q", "--cached", ".", check=False)
            console.print("[muted]Cancelled – nothing committed.[/]")

        if not staged:
            changes = r.changes()
            if not changes:
                console.print("[muted]Nothing to commit – no changes.[/]")
                return
            secrets_ = [p for _, p in changes if SECRET_RE.search(p) and not SECRET_OK.search(p)]
            if secrets_:
                console.print("[warn]⚠ these files may contain passwords or keys:[/] " + ", ".join(secrets_) +
                              "\n  [muted]Add them to .gitignore so they never reach GitHub.[/]")
                keep_out = (not ui.is_interactive() or
                            ui.choose("Commit them anyway?", ["No, leave them out", "Yes, they're safe"]) == 0)
                if keep_out:
                    console.print("  [muted]⎿  left out: " + ", ".join(secrets_) + "[/]")
                    r.run("add", "-A")
                    if r.has_commits():
                        r.run("reset", "-q", "--", *secrets_)
                    else:  # no HEAD yet
                        r.run("rm", "-q", "--cached", "--", *secrets_)
                else:
                    r.run("add", "-A")
            else:
                r.run("add", "-A")
            staged = r.out("diff", "--cached", "--name-only")
            if not staged:
                console.print("[muted]Nothing left to commit.[/]")
                return
        files = staged.splitlines()
        console.print(f"[bold]Committing {len(files)} file(s):[/] " + ", ".join(escape(f) for f in files[:15]) +
                      (" …" if len(files) > 15 else ""))
        if not message:
            diff = r.out("diff", "--cached", "--stat") + "\n\n" + r.out("diff", "--cached")
            message = suggest_message(self.session, diff) or ""
        while True:
            if message:
                console.print(Panel(escape(message), title="commit message", border_style=ui.BC_BLUE_LIGHT,
                                    expand=False))
                if not ui.is_interactive():
                    break
                c = ui.choose("Commit with this message?", ["Yes", "Edit the message", "Cancel"], cancel=2)
                if c == 0:
                    break
                if c == 2:
                    return unstage()
            if not ui.is_interactive():
                raise GitError("a commit message is needed: /git commit <message>")
            message = console.input("  [muted]commit message[/] [bc]❯[/] ").strip()
            if not message:
                return unstage()
        if not self.ensure_identity():
            raise GitError("commit needs your name and email (git config user.name / user.email)")
        r.run("commit", "-q", "-m", message)
        sha = r.out("rev-parse", "--short", "HEAD")
        console.print(f"[ok]✓[/] committed [bc]{sha}[/] {escape(message.splitlines()[0])}")
        self.note(f"I committed the changes as {sha}: {message.splitlines()[0]}")
        if r.remote_url():
            console.print("  [muted]⎿  Send it to GitHub with[/] [bc]/git push[/]")
        else:
            console.print("  [muted]⎿  Put it on GitHub with[/] [bc]/github create[/]")

    def push(self, arg: str) -> None:
        r = self.repo
        r.require()
        if not r.has_commits():
            raise GitError("nothing to push yet – make a commit first with [bc]/git commit[/]")
        url = r.remote_url()
        if not url:
            raise GitError("this repository has no GitHub remote. Run [bc]/github create[/] "
                           "(or [bc]/git remote <url>[/]).")
        if r.changes():
            console.print("[muted]Note: you have uncommitted changes; only commits are pushed "
                          "([/][bc]/git commit[/][muted] first to include them).[/]")
        if url.startswith("https://github.com") and not token():
            if not ensure_login():
                return
        if is_ssh(url):
            ensure_known_hosts()
        branch = r.branch()
        force = "--force" in arg.split()
        up = r.upstream()
        if up:
            r.run("fetch", "-q", check=False)
            a, b = r.ahead_behind()
            if not a and not force:
                console.print("[ok]✓[/] already up to date – nothing to push")
                return
            if b and not force:
                raise GitError(f"GitHub has {b} commit(s) you don't have. Run [bc]/git pull[/] first.")
            log = r.out("log", "--oneline", "@{u}..HEAD")
        else:
            log = r.out("log", "--oneline", "-n", "10")
        console.print(f"[bold]Push to {escape(url)} ({escape(branch)}):[/]\n" +
                      "\n".join("  " + escape(l) for l in log.splitlines()[:10]))
        if force and not self.confirm("Force push? This replaces the history on GitHub.", "Yes, force push"):
            return
        if not force and not self.confirm("Push these commits?"):
            return
        args = ["push"] + (["--force-with-lease"] if force else []) + ([] if up else ["-u", "origin", branch])
        code, _ = self.net(*args)
        if code != 0:
            # retry quietly to explain the error
            _, out = r.run(*args, check=False)
            raise GitError(friendly(out))
        console.print("[ok]✓[/] pushed")
        gh = parse_github(url)
        if gh and branch not in ("main", "master"):
            console.print(f"  [muted]⎿  Open a pull request with[/] [bc]/github pr[/]")

    def pull(self) -> None:
        r = self.repo
        r.require()
        if not r.upstream():
            if not r.remote_url():
                raise GitError("this repository has no remote to pull from")
            raise GitError(f"branch {r.branch()} isn't on GitHub yet – push it first with [bc]/git push[/]")
        code, out = self.net("pull", "--no-rebase", "--no-edit", quiet=True)
        if code != 0:
            raise GitError(friendly(out))
        console.print("[ok]✓[/] " + escape(out.splitlines()[-1] if out else "up to date"))
        self.note(f"I ran git pull:\n{out[-1500:]}")

    def log(self, arg: str) -> None:
        self.repo.require()
        if not self.repo.has_commits():
            console.print("[muted]No commits yet.[/]")
            return
        n = arg if arg.isdigit() else "15"
        console.print(escape(self.repo.out("log", "--graph", "--decorate", "--date=relative",
                                           "--format=%h %ad %s%d", "-n", n)))

    def branch(self, arg: str) -> None:
        r = self.repo
        r.require()
        name = arg.strip()
        if not name:
            console.print(escape(r.out("branch", "-vv")) or "[muted]No branches yet (make a commit first).[/]")
            return
        if r.ok("rev-parse", "--verify", "-q", f"refs/heads/{name}"):
            r.run("switch", name)
            console.print(f"[ok]✓[/] switched to existing branch [bc]{escape(name)}[/]")
            return
        r.run("switch", "-c", name)
        console.print(f"[ok]✓[/] created and switched to branch [bc]{escape(name)}[/]")

    def switch(self, arg: str) -> None:
        self.repo.require()
        if not arg:
            raise GitError("usage: /git switch <branch>")
        code, out = self.repo.run("switch", arg, check=False)
        if code != 0:
            if "local changes" in out:
                raise GitError("you have uncommitted changes that would be lost. Commit them "
                               "([bc]/git commit[/]) or stash them ([bc]/git stash[/]) first.")
            raise GitError(friendly(out))
        console.print(f"[ok]✓[/] on branch [bc]{escape(arg)}[/]")

    def merge(self, arg: str) -> None:
        self.repo.require()
        if not arg:
            raise GitError("usage: /git merge <branch>")
        if arg == "--abort":
            self.repo.run("merge", "--abort")
            console.print("[ok]✓[/] merge cancelled")
            return
        code, out = self.repo.run("merge", "--no-edit", arg, check=False)
        if code != 0:
            raise GitError(friendly(out))
        console.print(f"[ok]✓[/] merged {escape(arg)}")

    def revert(self, arg: str) -> None:
        r = self.repo
        r.require()
        target = arg.strip() or "HEAD"
        info = r.out("log", "-1", "--format=%h %s (%ar)", target)
        console.print(f"[bold]Revert[/] {escape(info)}\n[muted]This adds a new commit that undoes it; history "
                      "is kept, so it is safe even after pushing.[/]")
        if r.dirty():
            raise GitError("commit or stash your current changes first ([bc]/git commit[/] or [bc]/git stash[/])")
        if not self.confirm("Revert this commit?"):
            return
        if not self.ensure_identity():
            return
        code, out = r.run("revert", "--no-edit", target, check=False)
        if code != 0:
            raise GitError(friendly(out) + "  (cancel with [bc]/git revert --abort[/])")
        console.print(f"[ok]✓[/] reverted – new commit [bc]{r.out('rev-parse', '--short', 'HEAD')}[/]. "
                      "Run [bc]/git push[/] to send it to GitHub.")

    def uncommit(self) -> None:
        r = self.repo
        r.require()
        if not r.has_commits():
            raise GitError("no commits yet")
        if r.upstream() and r.ahead_behind()[0] == 0:
            raise GitError("the last commit is already on GitHub. Use [bc]/git revert[/] instead.")
        if not r.ok("rev-parse", "--verify", "-q", "HEAD~1"):
            raise GitError("this is the first commit; use [bc]/git revert[/] or delete .git to start over")
        info = r.out("log", "-1", "--format=%h %s")
        if not self.confirm(f"Undo the commit {info}? Its changes stay in your files."):
            return
        r.run("reset", "--soft", "HEAD~1")
        console.print("[ok]✓[/] commit undone – the changes are still here, uncommitted")

    def discard(self, arg: str) -> None:
        r = self.repo
        r.require()
        if not arg:
            raise GitError("usage: /git discard <file> (or /git discard all)")
        everything = arg.strip() == "all"
        what = "ALL uncommitted changes (new files too)" if everything else arg
        if not self.confirm(f"Throw away {what}? This can't be undone.", "Yes, discard"):
            return
        if everything:
            if r.has_commits():
                r.run("reset", "-q", "--hard")
            r.run("clean", "-fdq")
        else:
            for f in arg.split():
                tracked = r.ok("ls-files", "--error-unmatch", "--", f)
                if tracked:
                    r.run("restore", "--staged", "--worktree", "--source=HEAD", "--", f)
                else:
                    p = r.root / f
                    if p.is_file():
                        p.unlink()
        console.print("[ok]✓[/] discarded")

    def remote(self, arg: str) -> None:
        r = self.repo
        r.require()
        if not arg:
            console.print(escape(r.out("remote", "-v")) or "[muted]No remotes. Use[/] [bc]/github create[/]")
            return
        url = to_url(arg)
        if r.remote_url():
            r.run("remote", "set-url", "origin", url)
        else:
            r.run("remote", "add", "origin", url)
        console.print(f"[ok]✓[/] origin → {escape(url)}")

    def passthrough(self, arg: str) -> None:
        import shlex
        args = shlex.split(arg, posix=os.name != "nt")
        if args and args[0] in ("push", "pull", "fetch", "clone", "ls-remote"):
            url = next((a for a in args[1:] if is_ssh(a) or "://" in a), None) if args[0] in ("clone", "ls-remote") \
                else None
            code, out = self.net(*args, url=url)
        else:
            code, out = self.repo.run(*args, check=False)
            if out:
                console.print(escape(out))
        if code != 0:
            console.print(f"  [err]⎿  exit code {code}[/]")
        self.note(f"I ran `git {arg}` (exit code {code}).\n{out[-1500:]}")

    # github -------------------------------------------------------------------
    def gh_repo(self) -> Tuple[str, str]:
        gh = parse_github(self.repo.remote_url())
        if not gh:
            raise GitError("this folder isn't linked to a GitHub repository. Run [bc]/github create[/].")
        return gh

    def gh_status(self) -> None:
        tok, src = token_source()
        user = whoami() if tok else None
        if user:
            console.print(f"GitHub: signed in as [bc]{escape(user['login'])}[/] [muted](via {src})[/]")
        elif tok:
            console.print(f"GitHub: [err]token from {src} doesn't work[/] – run [bc]/github login[/]")
        else:
            console.print("GitHub: [muted]not signed in[/] – run [bc]/github login[/]")
        if shutil.which("ssh"):
            ok, who = ssh_check()
            console.print("SSH: " + (f"[ok]works[/] as [bc]{escape(who)}[/]" if ok else
                                     "[muted]not set up ([/][bc]/github ssh[/][muted] – only needed for "
                                     "git@github.com addresses)[/]"))
        if self.repo.is_repo():
            gh = parse_github(self.repo.remote_url())
            console.print("repository: " + (f"[bc]https://github.com/{gh[0]}/{gh[1]}[/]" if gh else
                                            "[muted]not on GitHub yet ([/][bc]/github create[/][muted])[/]"))

    def gh_create(self, arg: str) -> None:
        words = arg.split()
        public = "--public" in words
        words = [w for w in words if w not in ("--public", "--private")]
        r = self.repo
        if r.is_repo() and parse_github(r.remote_url()):
            o, n = parse_github(r.remote_url())
            raise GitError(f"this folder is already on GitHub: https://github.com/{o}/{n}")
        if not ensure_login():
            return
        user = whoami()
        if not user:
            raise GitError("could not read your GitHub account – run [bc]/github login[/]")
        name = safe_name(words[0] if words else r.root.name)
        desc = " ".join(words[1:])
        vis = "public" if public else "private"
        console.print(f"[bold]Create the {vis} repository[/] [bc]{escape(user['login'])}/{escape(name)}[/] "
                      "[muted]and push this folder to it.[/]")
        if not public:
            console.print("[muted](add --public to make it visible to everyone)[/]")
        if not self.confirm("Create it?"):
            return
        if not r.is_repo():
            self.init()
        if not r.has_commits():
            if not r.changes():
                (r.root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
            self.commit("Initial commit")
            if not r.has_commits():
                return
        body = {"name": name, "private": not public, "auto_init": False}
        if desc:
            body["description"] = desc
        data, _ = api("POST", "/user/repos", body)
        url = data["clone_url"]
        r.run("remote", "add", "origin", url)
        console.print(f"[ok]✓[/] created [bc]{data['html_url']}[/]")
        code, _ = r.run("push", "-u", "origin", r.branch(), check=False, quiet=False)
        if code != 0:
            raise GitError("the repository was created but the push failed – try [bc]/git push[/]")
        console.print("[ok]✓[/] pushed. From now on: [bc]/git commit[/] then [bc]/git push[/].")

    def gh_pr(self, arg: str) -> None:
        r = self.repo
        r.require()
        owner, name = self.gh_repo()
        if not ensure_login():
            return
        info, _ = api("GET", f"/repos/{owner}/{name}")
        base = info.get("default_branch", "main")
        branch = r.branch()
        if branch == base:
            raise GitError(f"you are on {base}. Make a branch first ([bc]/git branch my-change[/]), commit, "
                           "then [bc]/github pr[/].")
        self.net("fetch", "-q", "origin", base, quiet=True)
        log = r.out("log", "--format=%s", f"origin/{base}..HEAD") if \
            r.ok("rev-parse", "--verify", "-q", f"origin/{base}") else r.out("log", "--format=%s", "-n", "10")
        if not log:
            raise GitError(f"branch {branch} has no commits that aren't on {base} yet")
        if not r.upstream() or r.ahead_behind()[0]:
            console.print("[muted]Pushing the branch first…[/]")
            code, _ = self.net("push", "-u", "origin", branch)
            if code != 0:
                raise GitError("push failed – see above")
        subjects = log.splitlines()
        title = arg.strip() or (subjects[0] if len(subjects) == 1 else "")
        if ui.is_interactive():
            hint = f" [muted](enter = {escape(title)})[/]" if title else ""
            title = console.input(f"  Pull request title{hint} [bc]❯[/] ").strip() or title
        if not title:
            title = subjects[0]
        body = "\n".join(f"- {s}" for s in subjects[:20])
        data, _ = api("POST", f"/repos/{owner}/{name}/pulls",
                      {"title": title, "head": branch, "base": base, "body": body})
        console.print(f"[ok]✓[/] pull request #{data['number']}: [bc]{data['html_url']}[/]")

    def gh_prs(self) -> None:
        owner, name = self.gh_repo()
        data, _ = api("GET", f"/repos/{owner}/{name}/pulls?state=open&per_page=20")
        if not data:
            console.print("[muted]No open pull requests.[/]")
            return
        t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
        for col in ("#", "title", "branch", "author"):
            t.add_column(col)
        for pr in data:
            t.add_row(str(pr["number"]), escape(pr["title"]), escape(pr["head"]["ref"]), pr["user"]["login"])
        console.print(t)

    def gh_open(self) -> None:
        owner, name = self.gh_repo()
        url = f"https://github.com/{owner}/{name}"
        webbrowser.open(url)
        console.print(f"[muted]Opened[/] {url}")

    # dispatch -----------------------------------------------------------------
    def git(self, arg: str) -> None:
        sub, _, rest = arg.strip().partition(" ")
        rest = rest.strip()
        table = {
            "": self.status, "status": self.status, "st": self.status, "init": self.init,
            "uncommit": self.uncommit, "pull": self.pull,
        }
        with_arg = {
            "clone": self.clone, "diff": self.diff, "commit": self.commit, "push": self.push,
            "log": self.log, "branch": self.branch, "switch": self.switch, "checkout": self.switch,
            "merge": self.merge, "revert": self.revert, "discard": self.discard, "remote": self.remote,
        }
        if sub in ("help", "-h", "--help"):
            console.print(GIT_HELP)
        elif sub in table and not (sub == "pull" and rest):
            table[sub]()
        elif sub == "commit" and rest.startswith("-m "):
            self.commit(rest[3:].strip().strip("\"'"))
        elif sub in with_arg and not (sub in ("branch", "checkout") and rest.startswith("-")) \
                and not (sub == "revert" and rest.startswith("--")) and not (sub == "remote" and " " in rest):
            with_arg[sub](rest)
        else:
            self.passthrough(arg)

    def github(self, arg: str) -> None:
        sub, _, rest = arg.strip().partition(" ")
        if sub in ("", "status"):
            self.gh_status()
        elif sub == "login":
            login()
        elif sub == "logout":
            console.print("[ok]✓[/] removed bcagent's GitHub token" if logout() else
                          "[muted]bcagent had no saved GitHub token.[/]")
        elif sub == "create":
            self.gh_create(rest)
        elif sub == "pr":
            self.gh_pr(rest)
        elif sub == "prs":
            self.gh_prs()
        elif sub == "open":
            self.gh_open()
        elif sub == "clone":
            self.clone(rest.strip())
        elif sub == "ssh":
            ssh_setup()
        else:
            console.print(GIT_HELP)
