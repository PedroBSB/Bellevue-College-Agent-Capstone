#!/usr/bin/env bash
# Remove bcagent.   ./uninstall.sh          -> bcagent only (keeps settings, Ollama and models)
#                   ./uninstall.sh --all    -> everything: bcagent, settings, MCP servers, models and Ollama
if [ "${1:-}" = "--all" ]; then shift; exec "$(dirname "$0")/install.sh" --uninstall-all "$@"; fi
exec "$(dirname "$0")/install.sh" --uninstall "$@"
