"""MCP (Model Context Protocol) support: install public MCP servers and let agents call their tools.

Servers are stored in the same format Claude Code / Claude Desktop use:
  ~/.bcagent/mcp.json          user servers (all projects)
  <project>/.mcp.json          project servers (shared with the repo)

    {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"], "env": {}}}}

Only stdio servers are supported (the server is started as a local process).
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import HOME, ensure_home

USER_FILE = HOME / "mcp.json"
PROJECT_FILE = ".mcp.json"
PROTOCOL_VERSION = "2025-06-18"

# Public, well-known MCP servers. "{cwd}" is replaced with the project folder at start-up.
CATALOG: Dict[str, dict] = {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{cwd}"],
                   "description": "Read, write and search files in the project folder (Node.js)"},
    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"],
              "description": "Fetch web pages and convert them to markdown (Python/uv)"},
    "git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "{cwd}"],
            "description": "Git status, diff, log, commit in the project repo (Python/uv)"},
    "time": {"command": "uvx", "args": ["mcp-server-time"],
             "description": "Current time and time-zone conversion (Python/uv)"},
    "memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
               "description": "Persistent knowledge-graph memory (Node.js)"},
    "sequential-thinking": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                            "description": "Step-by-step problem solving tool (Node.js)"},
    "everything": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"],
                   "description": "Reference/test server that exercises every MCP feature (Node.js)"},
}


class MCPError(Exception):
    pass


# -- configuration -----------------------------------------------------------------

def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("mcpServers", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, servers: dict) -> None:
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data["mcpServers"] = servers
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def configured(root: Optional[Path]) -> Dict[str, dict]:
    """All servers: user servers, overridden by project servers. Each entry gets a 'scope'."""
    out = {n: dict(s, scope="user") for n, s in _read(USER_FILE).items()}
    if root:
        out.update({n: dict(s, scope="project") for n, s in _read(Path(root) / PROJECT_FILE).items()})
    return out


def spec_to_server(spec: str, args: List[str]) -> Tuple[str, dict]:
    """Turn what the user typed into (name, server-config).

    filesystem                     -> catalog entry
    @scope/some-mcp-server         -> npx -y @scope/some-mcp-server
    npm:pkg / pypi:pkg (or uvx:)   -> npx -y pkg / uvx pkg
    <name> <command> [args...]     -> any command
    """
    if spec in CATALOG and not args:
        entry = dict(CATALOG[spec])
        entry.pop("description", None)
        return spec, entry
    if spec.startswith("@") or spec.startswith("npm:"):
        pkg = spec[4:] if spec.startswith("npm:") else spec
        return _name_from_pkg(pkg), {"command": "npx", "args": ["-y", pkg] + args}
    if spec.startswith(("pypi:", "uvx:")):
        pkg = spec.split(":", 1)[1]
        return _name_from_pkg(pkg), {"command": "uvx", "args": [pkg] + args}
    if args:
        return spec, {"command": args[0], "args": args[1:]}
    raise MCPError(f"'{spec}' is not in the catalog. Use a catalog name, an npm package (@scope/pkg), "
                   "pypi:<package>, or: <name> -- <command> [args...]")


def _name_from_pkg(pkg: str) -> str:
    base = pkg.split("/")[-1].split("@")[0]
    base = re.sub(r"^(mcp-server-|server-)|(-mcp-server|-mcp|-server)$", "", base)
    return base or "server"


def add(name: str, server: dict, root: Optional[Path] = None, project: bool = False) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise MCPError("server names may only use letters, numbers, '-', '_' and '.'")
    ensure_home()
    path = Path(root) / PROJECT_FILE if project else USER_FILE
    servers = _read(path)
    servers[name] = {k: v for k, v in server.items() if k in ("command", "args", "env")}
    _write(path, servers)
    return path


def remove(name: str, root: Optional[Path] = None) -> Path:
    for path in ([Path(root) / PROJECT_FILE] if root else []) + [USER_FILE]:
        servers = _read(path)
        if name in servers:
            del servers[name]
            _write(path, servers)
            return path
    raise MCPError(f"no MCP server named '{name}'")


# -- stdio client --------------------------------------------------------------------

class MCPClient:
    """Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0)."""

    def __init__(self, name: str, server: dict, cwd: Path):
        self.name = name
        self.server = server
        self.cwd = Path(cwd)
        self.proc: Optional[subprocess.Popen] = None
        self.tools: List[dict] = []
        self._id = 0
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._stderr: List[str] = []

    def _cmd(self) -> List[str]:
        sub = lambda s: str(s).replace("{cwd}", str(self.cwd))  # noqa: E731
        cmd = sub(self.server.get("command", ""))
        exe = shutil.which(cmd)
        if not exe:
            hint = {"npx": "Node.js (https://nodejs.org)",
                    "uvx": "uv (https://docs.astral.sh/uv/ – `pip install uv`)"}.get(cmd, cmd)
            raise MCPError(f"'{cmd}' not found – install {hint}")
        return [exe] + [sub(a) for a in self.server.get("args", [])]

    def start(self, timeout: float = 60) -> None:
        env = dict(os.environ, **{k: str(v) for k, v in (self.server.get("env") or {}).items()})
        try:
            self.proc = subprocess.Popen(self._cmd(), cwd=str(self.cwd), env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            raise MCPError(f"could not start {self.name}: {e}")
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._err_reader, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                    "clientInfo": {"name": "bcagent", "version": "1.0"}}, timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.request("tools/list", {}, timeout).get("tools", [])

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg and "id" in msg:  # request from the server
                if msg["method"] == "ping":
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                else:
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601, "message": "not supported by bcagent"}})
            elif "id" in msg:
                self._q.put(msg)

    def _err_reader(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr = (self._stderr + [line.rstrip()])[-20:]

    def _send(self, msg: dict) -> None:
        if not self.proc or self.proc.poll() is not None:
            raise MCPError(f"{self.name} is not running" + self._err_tail())
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise MCPError(f"{self.name}: {e}" + self._err_tail())

    def _err_tail(self) -> str:
        return (": " + " | ".join(self._stderr[-3:])) if self._stderr else ""

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                msg = self._q.get(timeout=timeout)
            except queue.Empty:
                if self.proc and self.proc.poll() is not None:
                    raise MCPError(f"{self.name} exited" + self._err_tail())
                raise MCPError(f"{self.name}: no answer to {method} after {timeout:.0f}s")
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result") or {}

    def call(self, tool: str, arguments: dict, timeout: float = 120) -> str:
        res = self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource":
                parts.append(c.get("resource", {}).get("text", "") or str(c.get("resource")))
            else:
                parts.append(f"[{c.get('type')} content]")
        text = "\n".join(parts).strip() or json.dumps(res.get("structuredContent", res))[:4000]
        return ("ERROR: " if res.get("isError") else "") + text

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()


class MCPManager:
    """Starts the configured servers on first use and routes tool calls."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.clients: Dict[str, MCPClient] = {}
        self.errors: Dict[str, str] = {}
        self.started = False

    def servers(self) -> Dict[str, dict]:
        return configured(self.root)

    def start(self, on_status=None) -> None:
        if self.started:
            return
        self.started = True
        for name, server in self.servers().items():
            self.start_one(name, server, on_status)

    def start_one(self, name: str, server: dict, on_status=None) -> Optional[MCPClient]:
        old = self.clients.pop(name, None)
        if old:
            old.close()
        if on_status:
            on_status(name)
        client = MCPClient(name, server, self.root)
        try:
            client.start()
        except MCPError as e:
            client.close()
            self.errors[name] = str(e)
            return None
        self.errors.pop(name, None)
        self.clients[name] = client
        return client

    def tools(self) -> List[Tuple[str, dict]]:
        return [(n, t) for n, c in self.clients.items() for t in c.tools]

    def call(self, full_name: str, arguments: dict) -> str:
        server, _, tool = full_name.partition(".")
        if server not in self.clients:
            for n, c in self.clients.items():  # model forgot the server prefix
                if any(t.get("name") == full_name for t in c.tools):
                    server, tool = n, full_name
                    break
        if server not in self.clients:
            raise MCPError(f"unknown tool '{full_name}'")
        return self.clients[server].call(tool, arguments)

    def prompt_section(self) -> str:
        tools = self.tools()
        if not tools:
            return ""
        lines = []
        for server, t in tools[:40]:
            props = (t.get("inputSchema") or {}).get("properties", {})
            req = set((t.get("inputSchema") or {}).get("required", []))
            args = ", ".join(f"{k}{'' if k in req else '?'}: {v.get('type', 'any')}" for k, v in props.items())
            desc = " ".join((t.get("description") or "").split())[:160]
            lines.append(f"- {server}.{t['name']}({args}) – {desc}")
        return ("Tools (MCP). To use a tool, answer with ONLY a ```tool block containing JSON, then stop and "
                "wait for the result:\n```tool\n{\"tool\": \"server.tool_name\", \"arguments\": {...}}\n```\n"
                "Only use a tool when you need information or an action you cannot do yourself.\n"
                "Available tools:\n" + "\n".join(lines))

    def close(self) -> None:
        for c in self.clients.values():
            c.close()
        self.clients.clear()
        self.started = False


TOOL_BLOCK = re.compile(r"```tool[^\n]*\n(.*?)```", re.S)


def parse_tool_calls(text: str) -> List[Tuple[str, dict]]:
    calls = []
    for body in TOOL_BLOCK.findall(text):
        try:
            data = json.loads(body.strip())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("tool"):
                args = item.get("arguments") or item.get("args") or {}
                calls.append((str(item["tool"]), args if isinstance(args, dict) else {}))
    return calls
