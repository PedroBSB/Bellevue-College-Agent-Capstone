"""Minimal Ollama HTTP client (stdlib only) - local inference, no cloud."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator, Optional


class OllamaError(RuntimeError):
    pass


def _normalize_host(host: Optional[str]) -> str:
    host = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    host = host.replace("0.0.0.0", "127.0.0.1")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    if host.count(":") == 1:  # scheme only, no port
        host += ":11434"
    return host.rstrip("/")


class Ollama:
    def __init__(self, host: Optional[str] = None):
        self.host = _normalize_host(host)
        self.last_stats: dict = {}

    # -- plumbing -------------------------------------------------------------
    def _open(self, path: str, payload=None, timeout=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.host + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                msg = json.loads(body).get("error", body)
            except ValueError:
                msg = body
            raise OllamaError(msg) from None
        except (urllib.error.URLError, OSError) as e:
            raise OllamaError(f"cannot reach Ollama at {self.host} ({e})") from None

    # -- server ---------------------------------------------------------------
    def version(self) -> Optional[str]:
        try:
            with self._open("/api/version", timeout=2) as r:
                return json.load(r).get("version", "?")
        except (OllamaError, ValueError):
            return None

    @staticmethod
    def binary() -> Optional[str]:
        exe = shutil.which("ollama")
        if exe:
            return exe
        candidates = [
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
        ]
        return next((c for c in candidates if c and Path(c).is_file()), None)

    def ensure_running(self, wait: float = 25.0) -> bool:
        """Start `ollama serve` in the background if the server is not up."""
        if self.version():
            return True
        exe = self.binary()
        if not exe:
            return False
        kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([exe, "serve"], **kwargs)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.version():
                return True
            time.sleep(0.4)
        return False

    # -- models ---------------------------------------------------------------
    def models(self) -> list:
        with self._open("/api/tags", timeout=10) as r:
            return [m["name"] for m in json.load(r).get("models", [])]

    def has_model(self, name: str) -> bool:
        names = set(self.models())
        return name in names or (":" not in name and f"{name}:latest" in names)

    def pull(self, name: str, progress: Optional[Callable[[dict], None]] = None) -> None:
        with self._open("/api/pull", {"name": name, "model": name, "stream": True}) as r:
            for line in r:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise OllamaError(ev["error"])
                if progress:
                    progress(ev)

    def delete(self, name: str) -> None:
        with self._open("/api/delete", {"name": name, "model": name}, method="DELETE"):
            pass

    # -- chat -----------------------------------------------------------------
    def chat_stream(self, model: str, messages: list, options: Optional[dict] = None) -> Iterator[str]:
        """Yield response tokens. Final timing stats land in self.last_stats."""
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": options or {}, "keep_alive": "30m"}
        self.last_stats = {}
        resp = self._open("/api/chat", payload)
        try:
            for line in resp:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise OllamaError(ev["error"])
                tok = (ev.get("message") or {}).get("content", "")
                if tok:
                    yield tok
                if ev.get("done"):
                    self.last_stats = ev
                    break
        finally:
            resp.close()
