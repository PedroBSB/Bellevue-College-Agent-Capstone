#!/usr/bin/env bash
# bcagent installer for macOS and Linux
# Installs everything that is missing: Python 3, Ollama, the bcagent CLI and the default local model.
#
#   ./install.sh                 # full install
#   ./install.sh --no-model      # skip downloading the model (downloads on first run instead)
#   ./install.sh --user          # never use sudo: Python and Ollama go into ~/.bcagent if missing
#                                #   (done automatically when you are not an administrator)
#   ./install.sh --uninstall     # remove bcagent (keeps settings, Ollama and Python)
#   ./install.sh --uninstall-all # remove EVERYTHING: bcagent, settings/agents/MCP servers, models and Ollama
#                                #   (add --yes to skip the confirmation question)
#
# Environment overrides: BCAGENT_HOME (default ~/.bcagent), BCAGENT_MODEL (default qwen2.5-coder:0.5b),
#   BCAGENT_PRIVATE_PYTHON=1 (always use a private Python in ~/.bcagent/python)
set -euo pipefail

REPO_ZIP="https://github.com/PedroBSB/Bellevue-College-Agent-Capstone/archive/refs/heads/main.zip"
BCAGENT_HOME="${BCAGENT_HOME:-$HOME/.bcagent}"
MODEL="${BCAGENT_MODEL:-qwen2.5-coder:0.5b}"
BIN_DIR="$HOME/.local/bin"
PY_MIN_MINOR=9
SKIP_MODEL=0
ASSUME_YES=0
USER_ONLY=0
PBS_TAG="20241206"           # python-build-standalone release used when Python must be installed privately
PBS_PY="3.12.8"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"

BLUE=$'\033[1;38;2;79;143;219m'; BCB=$'\033[1;38;2;0;61;121;48;2;255;255;255m'
GREEN=$'\033[1;32m'; RED=$'\033[1;31m'; YEL=$'\033[1;33m'; DIM=$'\033[2m'; R=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$BLUE" "$R" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$GREEN" "$R" "$*"; }
warn() { printf '%s  !%s %s\n' "$YEL" "$R" "$*"; }
die()  { printf '%s  ✗ %s%s\n' "$RED" "$*" "$R" >&2; exit 1; }

OS="$(uname -s)"
case "$(uname -m)" in
  x86_64|amd64) ARCH=x86_64 ;;
  arm64|aarch64) ARCH=aarch64 ;;
  *) ARCH="$(uname -m)" ;;
esac

# Can we use administrator rights? root -> yes. Otherwise only when sudo exists AND this user is allowed
# to use it (passwordless, cached, or member of an admin group). Lab/school computers usually say no;
# then everything is installed for the current user only, inside $BCAGENT_HOME.
can_sudo() {
  [ "${USER_ONLY:-0}" = "1" ] && return 1
  command -v sudo >/dev/null 2>&1 || return 1
  sudo -n true >/dev/null 2>&1 && return 0
  id -Gn 2>/dev/null | tr ' ' '\n' | grep -qxE 'admin|sudo|wheel' && return 0
  return 1
}
pick_sudo() {
  SUDO=""; HAS_ADMIN=0
  if [ "$(id -u)" -eq 0 ]; then HAS_ADMIN=1
  elif can_sudo; then SUDO="sudo"; HAS_ADMIN=1; fi
}
pick_sudo

banner() {
  printf "%s" "$BLUE"
  cat <<'EOF'

     .-"-.            .-"-.
    / .-. \__________/ .-. \    bcagent
    \ \  /            \  / /    installer
     \_\/  .--.  .--.  \/_/
       |   | o|  |o |   |
       |   '--'  '--'   |       BELLEVUE
       |     .----.     |       COLLEGE
      /|    ( (__) )    |\
     | |  .--'----'--.  | |
     | | (  \/\/\/\/  ) | |
      \ \ '-.______.-' / /
       '-.____________.-'

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

uninstall_all() {
  printf '%s' "$BLUE"
  cat <<'MSG'
This removes EVERYTHING bcagent installed:
  - the bcagent command and its Python environment
  - your bcagent settings, agents, MCP servers and history (~/.bcagent)
  - the bcagent models and Ollama itself, including ALL Ollama models (~/.ollama)
Python is left installed (other programs may need it).
MSG
  printf '%s' "$R"
  if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Continue? [y/N] '
    { read -r ans </dev/tty; } 2>/dev/null || ans=""
    case "$ans" in y|Y|yes|YES) ;; *) say "Cancelled – nothing was removed"; exit 0 ;; esac
  fi
  local sudo="$SUDO"

  say "Removing bcagent"
  rm -f "$BIN_DIR/bcagent"
  # an Ollama installed privately by bcagent (no-sudo install) lives in $BCAGENT_HOME/ollama
  if [ -L "$BIN_DIR/ollama" ] && readlink "$BIN_DIR/ollama" | grep -q "$BCAGENT_HOME"; then rm -f "$BIN_DIR/ollama"; fi
  rm -rf "$BCAGENT_HOME"
  ok "bcagent, settings, agents, MCP servers and any private Python/Ollama removed"

  local ob=""
  for c in ollama /usr/local/bin/ollama /opt/homebrew/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama \
           "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    if command -v "$c" >/dev/null 2>&1; then ob="$(command -v "$c")"; break; fi
  done
  if [ -n "$ob" ]; then
    say "Removing bcagent models"
    for m in "$MODEL" qwen2.5-coder:0.5b qwen2.5-coder:1.5b qwen2.5-coder:3b qwen2.5-coder:7b; do
      "$ob" rm "$m" >/dev/null 2>&1 && ok "removed model $m" || true
    done
  fi

  say "Removing Ollama"
  if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true
    if command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then
      brew services stop ollama >/dev/null 2>&1 || true
      brew uninstall ollama >/dev/null 2>&1 || true
    fi
    pkill -f "ollama serve" >/dev/null 2>&1 || true
    rm -rf /Applications/Ollama.app "$HOME/Applications/Ollama.app" "$HOME/Library/Application Support/Ollama" \
           "$HOME/Library/Saved Application State/com.electron.ollama.savedState" 2>/dev/null || true
    if [ -e /usr/local/bin/ollama ] || [ -L /usr/local/bin/ollama ]; then $sudo rm -f /usr/local/bin/ollama || true; fi
  else
    if command -v systemctl >/dev/null 2>&1; then
      $sudo systemctl stop ollama >/dev/null 2>&1 || true
      $sudo systemctl disable ollama >/dev/null 2>&1 || true
      $sudo rm -f /etc/systemd/system/ollama.service || true
    fi
    pkill -f "ollama serve" >/dev/null 2>&1 || true
    if [ -n "$ob" ]; then $sudo rm -f "$ob" || true; fi
    $sudo rm -rf /usr/share/ollama /usr/local/lib/ollama 2>/dev/null || true
    $sudo userdel ollama >/dev/null 2>&1 || true
    $sudo groupdel ollama >/dev/null 2>&1 || true
  fi
  rm -rf "$HOME/.ollama"
  ok "Ollama and all models removed"
  ok "bcagent is completely uninstalled. Go Bulldogs! 🐾"
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --user|--no-sudo) USER_ONLY=1; pick_sudo ;;
  esac
done
for arg in "$@"; do
  case "$arg" in
    --no-model) SKIP_MODEL=1 ;;
    --user|--no-sudo) USER_ONLY=1; pick_sudo ;;
    -y|--yes) ;;
    --uninstall) uninstall ;;
    --uninstall-all|--all) uninstall_all ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
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

TMPD="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/bcagent-install-$$")"
mkdir -p "$TMPD"
trap 'rm -rf "$TMPD"' EXIT

linux_pkg_install() {  # install packages with whatever package manager exists (needs admin rights)
  [ "$HAS_ADMIN" = "1" ] || return 1
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
  for c in "$BCAGENT_HOME/python/bin/python3" "$BCAGENT_HOME/python-standalone/python/bin/python3"; do
    if [ -x "$c" ] && python_ok "$c"; then echo "$c"; return 0; fi
  done
  [ "${BCAGENT_PRIVATE_PYTHON:-0}" = "1" ] && return 1
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
install_python_system() {  # needs Homebrew or administrator rights
  if [ "$OS" = "Darwin" ]; then
    if have brew && brew install python@3.12; then return 0; fi
    [ "$HAS_ADMIN" = "1" ] || return 1
    local pkg="$TMPD/python-$PBS_PY-macos11.pkg"
    download "https://www.python.org/ftp/python/$PBS_PY/python-$PBS_PY-macos11.pkg" "$pkg" || return 1
    say "Running the official Python installer (your password may be requested)"
    sudo installer -pkg "$pkg" -target /
  else
    linux_pkg_install python3 python3-venv python3-pip 2>/dev/null \
      || linux_pkg_install python3 python3-pip \
      || linux_pkg_install python3 py3-pip
  fi
}

install_python_user() {  # no administrator rights needed: a private Python inside $BCAGENT_HOME
  say "Installing a private Python just for bcagent in $BCAGENT_HOME (no administrator rights needed)"
  mkdir -p "$BCAGENT_HOME"
  # 1) uv (a small tool from astral.sh) knows the right Python build for this machine
  local uvdir="$BCAGENT_HOME/uv" uv=""
  if download "https://astral.sh/uv/install.sh" "$TMPD/uv-install.sh" 2>/dev/null \
     && env UV_INSTALL_DIR="$uvdir" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 \
            sh "$TMPD/uv-install.sh" >/dev/null 2>&1; then
    for c in "$uvdir/uv" "$uvdir/bin/uv"; do [ -x "$c" ] && uv="$c" && break; done
  fi
  if [ -n "$uv" ]; then
    local found
    found="$(env UV_PYTHON_INSTALL_DIR="$BCAGENT_HOME/uv-python" UV_PYTHON_PREFERENCE=only-managed \
             sh -c '"$1" python install 3.12 >/dev/null 2>&1 && "$1" python find 3.12' _ "$uv" 2>/dev/null || true)"
    if [ -n "$found" ] && python_ok "$found"; then
      mkdir -p "$BCAGENT_HOME/python/bin"
      ln -sf "$found" "$BCAGENT_HOME/python/bin/python3"
      ok "private Python installed with uv"
      return 0
    fi
    warn "uv could not install Python – downloading a standalone Python build instead"
  fi
  # 2) a prebuilt, relocatable CPython from python-build-standalone
  local triple=""
  case "$OS" in
    Darwin) triple="$ARCH-apple-darwin" ;;
    Linux)  triple="$ARCH-unknown-linux-gnu" ;;
  esac
  [ -n "$triple" ] || return 1
  local url="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PBS_PY+$PBS_TAG-$triple-install_only.tar.gz"
  download "$url" "$TMPD/python.tar.gz" || return 1
  rm -rf "$BCAGENT_HOME/python-standalone"
  mkdir -p "$BCAGENT_HOME/python-standalone"
  tar -xzf "$TMPD/python.tar.gz" -C "$BCAGENT_HOME/python-standalone" || return 1
  python_ok "$BCAGENT_HOME/python-standalone/python/bin/python3" && ok "private Python $PBS_PY installed"
}

install_python() {
  say "Python 3.$PY_MIN_MINOR+ not found – installing Python"
  if [ "${BCAGENT_PRIVATE_PYTHON:-0}" != "1" ] && install_python_system; then
    hash -r
    find_python >/dev/null && return 0
  fi
  [ "$HAS_ADMIN" = "1" ] && warn "system install of Python did not work – falling back to a private Python"
  install_python_user || die "could not install Python automatically – please install Python 3.9+ and re-run"
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
[ "$HAS_ADMIN" = "1" ] || warn "no administrator rights – anything missing is installed for your user only"

# ---------------------------------------------------------------- 2. bcagent (virtualenv)
say "Creating virtual environment in $BCAGENT_HOME/venv"
mkdir -p "$BCAGENT_HOME"
rm -rf "$BCAGENT_HOME/venv"
if ! "$PY" -m venv "$BCAGENT_HOME/venv" >/dev/null 2>&1; then
  # e.g. Debian/Ubuntu without python3-venv. Try the package, else use a private Python.
  warn "this Python cannot create virtual environments – fixing it"
  PYV="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  rm -rf "$BCAGENT_HOME/venv"
  if ! { { linux_pkg_install "python${PYV}-venv" 2>/dev/null || linux_pkg_install python3-venv; } >/dev/null 2>&1 \
         && "$PY" -m venv "$BCAGENT_HOME/venv" >/dev/null 2>&1; }; then
    rm -rf "$BCAGENT_HOME/venv"
    install_python_user || die "could not install Python automatically – please install Python 3.9+ and re-run"
    PY="$(BCAGENT_PRIVATE_PYTHON=1 find_python || true)"
    [ -n "$PY" ] || die "could not find the private Python"
    ok "Python: $PY"
    "$PY" -m venv "$BCAGENT_HOME/venv" || die "could not create a virtual environment"
  fi
fi
VPY="$BCAGENT_HOME/venv/bin/python"
"$VPY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VPY" -m pip install --quiet --upgrade pip || warn "could not upgrade pip – continuing with the bundled one"
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
  for c in ollama /usr/local/bin/ollama /opt/homebrew/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama \
           "$HOME/Applications/Ollama.app/Contents/Resources/ollama" "$BCAGENT_HOME/ollama/bin/ollama"; do
    if have "$c" || [ -x "$c" ]; then command -v "$c" 2>/dev/null || echo "$c"; return 0; fi
  done
  return 1
}

install_ollama_mac() {
  if have brew && brew install ollama; then return 0; fi
  download "https://ollama.com/download/Ollama-darwin.zip" "$TMPD/Ollama-darwin.zip" || return 1
  local dest=/Applications
  if [ "$USER_ONLY" = "1" ] || [ "$HAS_ADMIN" != "1" ] || [ ! -w /Applications ]; then
    dest="$HOME/Applications"   # standard (non-admin) accounts can't write to /Applications
    mkdir -p "$dest"
    say "Installing Ollama for your user only in $dest"
  fi
  rm -rf "$dest/Ollama.app"
  unzip -q "$TMPD/Ollama-darwin.zip" -d "$dest"
}

install_ollama_linux_user() {  # no administrator rights: unpack the official build into $BCAGENT_HOME/ollama
  local a
  case "$ARCH" in x86_64) a=amd64 ;; aarch64) a=arm64 ;; *) return 1 ;; esac
  say "Installing Ollama for your user only in $BCAGENT_HOME/ollama (no administrator rights needed)"
  local dest="$BCAGENT_HOME/ollama"
  rm -rf "$dest"; mkdir -p "$dest"
  if download "https://ollama.com/download/ollama-linux-$a.tar.zst" "$TMPD/ollama.tar.zst" 2>/dev/null; then
    if have zstd; then
      zstd -dc "$TMPD/ollama.tar.zst" | tar -xf - -C "$dest" || return 1
    else
      # no zstd tool: decompress with Python (the zstandard package goes into bcagent's own environment)
      "$VPY" -m pip install --quiet zstandard || return 1
      "$VPY" - "$TMPD/ollama.tar.zst" "$dest" <<'PYZ' || return 1
import sys, tarfile, zstandard
with open(sys.argv[1], "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as zr:
    with tarfile.open(fileobj=zr, mode="r|") as tf:
        tf.extractall(sys.argv[2])
PYZ
    fi
  else
    download "https://ollama.com/download/ollama-linux-$a.tgz" "$TMPD/ollama.tgz" || return 1
    tar -xzf "$TMPD/ollama.tgz" -C "$dest" || return 1
  fi
  [ -x "$dest/bin/ollama" ] || return 1
  ln -sf "$dest/bin/ollama" "$BIN_DIR/ollama"
}

OLLAMA="$(ollama_bin || true)"
if [ -z "$OLLAMA" ]; then
  say "Installing Ollama (local LLM runtime)"
  if [ "$OS" = "Darwin" ]; then
    install_ollama_mac || warn "Ollama install failed"
  else
    done_sys=0
    if [ "$HAS_ADMIN" = "1" ] && { have curl || linux_pkg_install curl >/dev/null 2>&1; }; then
      if curl -fsSL https://ollama.com/install.sh | sh; then done_sys=1
      else warn "the official Ollama installer failed – installing Ollama for your user only"; fi
    fi
    hash -r
    if [ "$done_sys" = "0" ] || ! ollama_bin >/dev/null; then
      install_ollama_linux_user || warn "Ollama install failed"
    fi
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
