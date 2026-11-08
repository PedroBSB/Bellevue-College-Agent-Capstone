#!/usr/bin/env bash
# bcagent installer for macOS and Linux
# Installs everything that is missing: Python 3, Ollama, the bcagent CLI and the default local model.
#
#   ./install.sh                 # full install
#   ./install.sh --no-model      # skip downloading the model (downloads on first run instead)
#   ./install.sh --uninstall     # remove bcagent (keeps Ollama and Python)
#
# Environment overrides: BCAGENT_HOME (default ~/.bcagent), BCAGENT_MODEL (default qwen2.5-coder:0.5b)
set -euo pipefail

REPO_ZIP="https://github.com/PedroBSB/Bellevue-College-Agent-Capstone/archive/refs/heads/main.zip"
BCAGENT_HOME="${BCAGENT_HOME:-$HOME/.bcagent}"
MODEL="${BCAGENT_MODEL:-qwen2.5-coder:0.5b}"
BIN_DIR="$HOME/.local/bin"
PY_MIN_MINOR=9
SKIP_MODEL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"

BLUE=$'\033[1;38;2;79;143;219m'; BCB=$'\033[1;38;2;0;61;121;48;2;255;255;255m'
GREEN=$'\033[1;32m'; RED=$'\033[1;31m'; YEL=$'\033[1;33m'; DIM=$'\033[2m'; R=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$BLUE" "$R" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$GREEN" "$R" "$*"; }
warn() { printf '%s  !%s %s\n' "$YEL" "$R" "$*"; }
die()  { printf '%s  ✗ %s%s\n' "$RED" "$*" "$R" >&2; exit 1; }

OS="$(uname -s)"
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

banner() {
  printf '%s' "$BCB"
  cat <<'EOF'

     ,-.             ,-.
    ( ( `.  _____  .' ) )   bcagent
     \ \  \/     \/  / /    installer
      `.\  _     _  /,'
        | (o)   (o) |
        |    ___    |    BELLEVUE
       /|   (___)   |\   COLLEGE
      | \ \_______/ / |
       \ '.\/\/\/\/.' /
        '.__/~~~\__.'

EOF
  printf '%s\n' "$R"
}

uninstall() {
  say "Removing bcagent"
  rm -f "$BIN_DIR/bcagent"
  rm -rf "$BCAGENT_HOME/venv"
  ok "bcagent removed (config and agents kept in $BCAGENT_HOME; delete that folder to remove everything)"
  ok "Ollama and models were not touched. Remove a model with: ollama rm $MODEL"
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --no-model) SKIP_MODEL=1 ;;
    --uninstall) uninstall ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "unknown option $arg" ;;
  esac
done

# ---------------------------------------------------------------- helpers
have() { command -v "$1" >/dev/null 2>&1; }

download() {  # download URL FILE
  if have curl; then curl -fL --progress-bar "$1" -o "$2"
  elif have wget; then wget -q --show-progress "$1" -O "$2"
  else die "curl or wget is required"; fi
}

linux_pkg_install() {  # install packages with whatever package manager exists
  if have apt-get; then $SUDO apt-get update -y && $SUDO apt-get install -y "$@"
  elif have dnf; then $SUDO dnf install -y "$@"
  elif have yum; then $SUDO yum install -y "$@"
  elif have pacman; then $SUDO pacman -Sy --noconfirm "$@"
  elif have zypper; then $SUDO zypper install -y "$@"
  elif have apk; then $SUDO apk add "$@"
  else return 1; fi
}

python_ok() {  # python_ok CMD -> true if CMD is Python >= 3.9 with venv
  "$1" -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, $PY_MIN_MINOR) else 1)" >/dev/null 2>&1
}

find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if have "$c" || [ -x "$c" ]; then
      # On macOS /usr/bin/python3 is a stub when the Command Line Tools are missing.
      if [ "$OS" = "Darwin" ] && [ "$(command -v "$c" 2>/dev/null)" = "/usr/bin/python3" ] \
         && ! xcode-select -p >/dev/null 2>&1; then continue; fi
      if python_ok "$c"; then command -v "$c" 2>/dev/null || echo "$c"; return 0; fi
    fi
  done
  return 1
}

# ---------------------------------------------------------------- 1. Python
install_python() {
  say "Python 3.$PY_MIN_MINOR+ not found – installing Python"
  if [ "$OS" = "Darwin" ]; then
    if have brew; then
      brew install python@3.12
    else
      local pkg="/tmp/python-3.12.8-macos11.pkg"
      download "https://www.python.org/ftp/python/3.12.8/python-3.12.8-macos11.pkg" "$pkg"
      say "Running the official Python installer (your password may be requested)"
      sudo installer -pkg "$pkg" -target /
      rm -f "$pkg"
    fi
  else
    linux_pkg_install python3 python3-venv python3-pip 2>/dev/null \
      || linux_pkg_install python3 python3-pip \
      || linux_pkg_install python3 py3-pip \
      || die "could not install Python automatically – please install Python 3.9+ and re-run"
  fi
}

banner
say "Installing bcagent for $OS ($(uname -m))"

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  install_python
  hash -r
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "Python was installed but could not be found – open a new terminal and re-run"
fi
ok "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# ---------------------------------------------------------------- 2. bcagent (virtualenv)
say "Creating virtual environment in $BCAGENT_HOME/venv"
mkdir -p "$BCAGENT_HOME"
rm -rf "$BCAGENT_HOME/venv"
if ! "$PY" -m venv "$BCAGENT_HOME/venv" 2>/dev/null; then
  warn "venv module missing – installing it"
  PYV="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  linux_pkg_install "python${PYV}-venv" 2>/dev/null || linux_pkg_install python3-venv || true
  rm -rf "$BCAGENT_HOME/venv"
  "$PY" -m venv "$BCAGENT_HOME/venv" || die "could not create a virtual environment"
fi
VPY="$BCAGENT_HOME/venv/bin/python"
"$VPY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VPY" -m pip install --quiet --upgrade pip
if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/bcagent" ]; then
  say "Installing bcagent and its requirements from $SCRIPT_DIR"
  "$VPY" -m pip install --quiet --upgrade "$SCRIPT_DIR"
else
  say "Installing bcagent and its requirements from GitHub"
  "$VPY" -m pip install --quiet --upgrade "$REPO_ZIP"
fi
mkdir -p "$BIN_DIR"
ln -sf "$BCAGENT_HOME/venv/bin/bcagent" "$BIN_DIR/bcagent"
ok "bcagent $("$BCAGENT_HOME/venv/bin/bcagent" --version | awk '{print $2}') installed → $BIN_DIR/bcagent"

# add ~/.local/bin to PATH if needed
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    LINE='export PATH="$HOME/.local/bin:$PATH"'
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
      if [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ]; then
        grep -qs '.local/bin' "$rc" || { printf '\n# added by bcagent installer\n%s\n' "$LINE" >> "$rc"; ok "added $BIN_DIR to PATH in $rc"; }
      fi
    done
    export PATH="$BIN_DIR:$PATH"
    NEED_NEW_SHELL=1
    ;;
esac

# ---------------------------------------------------------------- 3. Ollama
ollama_bin() {
  for c in ollama /usr/local/bin/ollama /opt/homebrew/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama; do
    if have "$c" || [ -x "$c" ]; then command -v "$c" 2>/dev/null || echo "$c"; return 0; fi
  done
  return 1
}

OLLAMA="$(ollama_bin || true)"
if [ -z "$OLLAMA" ]; then
  say "Installing Ollama (local LLM runtime)"
  if [ "$OS" = "Darwin" ]; then
    if have brew; then
      brew install ollama
    else
      download "https://ollama.com/download/Ollama-darwin.zip" /tmp/Ollama-darwin.zip
      rm -rf /Applications/Ollama.app
      unzip -q /tmp/Ollama-darwin.zip -d /Applications && rm -f /tmp/Ollama-darwin.zip
    fi
  else
    have curl || linux_pkg_install curl || die "curl is required to install Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
  hash -r
  OLLAMA="$(ollama_bin || true)"
  [ -n "$OLLAMA" ] || die "Ollama install failed – install it from https://ollama.com/download and re-run"
fi
ok "Ollama: $OLLAMA"

if ! curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  say "Starting the Ollama server"
  if [ "$OS" = "Darwin" ] && have brew && brew list ollama >/dev/null 2>&1; then
    brew services start ollama >/dev/null 2>&1 || nohup "$OLLAMA" serve >/dev/null 2>&1 &
  else
    nohup "$OLLAMA" serve >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 40); do
    curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    sleep 0.5
  done
fi
curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1 && ok "Ollama server running" \
  || warn "Ollama server not responding yet – bcagent will try to start it"

# ---------------------------------------------------------------- 4. model
if [ "$SKIP_MODEL" -eq 0 ]; then
  say "Downloading the default model $MODEL (~400 MB, smallest & fastest local code LLM)"
  "$OLLAMA" pull "$MODEL" && ok "model $MODEL ready" || warn "model download failed – it will download on first run"
fi

echo
printf '%s  bcagent is ready! %s\n\n' "$BCB" "$R"
echo "  cd into a project folder and run:   ${BLUE}bcagent${R}"
echo "  one-shot mode:                      ${BLUE}bcagent -p \"write a fizzbuzz script in fizzbuzz.py\" --yes${R}"
echo "  install more agents:                ${BLUE}bcagent agents catalog${R}"
if [ "${NEED_NEW_SHELL:-0}" = "1" ]; then
  echo
  warn "Open a new terminal (or run: export PATH=\"\$HOME/.local/bin:\$PATH\") to use bcagent."
fi
