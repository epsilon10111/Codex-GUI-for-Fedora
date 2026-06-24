#!/usr/bin/env bash
set -euo pipefail

if ! command -v npm >/dev/null 2>&1; then
  printf 'npm is required. Install it with:\n' >&2
  printf '  sudo dnf install -y nodejs npm\n' >&2
  exit 1
fi

NPM_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.local/share/npm}"
USER_BIN_DIR="$HOME/.local/bin"

mkdir -p "$NPM_PREFIX" "$USER_BIN_DIR"
npm config set prefix "$NPM_PREFIX" --location=user
npm i -g @openai/codex

if [[ -x "$NPM_PREFIX/bin/codex" ]]; then
  ln -sf "$NPM_PREFIX/bin/codex" "$USER_BIN_DIR/codex"
fi

printf 'Installed Codex CLI for the current user.\n'
printf 'Binary: %s/bin/codex\n' "$NPM_PREFIX"

case ":$PATH:" in
  *":$USER_BIN_DIR:"*) ;;
  *)
    printf '\nAdd this to your shell profile if codex is not found in new terminals:\n'
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    ;;
esac
