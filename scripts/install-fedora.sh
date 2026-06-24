#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${CODEX_GUI_PYTHON:-/usr/bin/python3}"

if [[ ! -x "$PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  else
    printf 'python3 is required. Install it with: sudo dnf install -y python3\n' >&2
    exit 1
  fi
fi

if ! "$PYTHON" -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Vte", "2.91")' >/dev/null 2>&1; then
  printf 'Missing GTK/VTE bindings for %s. Install them with:\n' "$PYTHON" >&2
  printf '  sudo dnf install -y python3-gobject gtk3 vte291\n' >&2
  printf 'If you are in conda/base, keep using this installer; it prefers /usr/bin/python3.\n' >&2
  exit 1
fi

APP_DIR="$HOME/.local/share/codex-gui"
USER_BIN_DIR="$HOME/.local/bin"
LAUNCHER="$USER_BIN_DIR/codex-gui"

mkdir -p "$APP_DIR" "$USER_BIN_DIR"
cp -R "$ROOT_DIR/codex_gui" "$APP_DIR/"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$APP_DIR"
PYTHON="\${CODEX_GUI_PYTHON:-$PYTHON}"

if [[ ! -x "\$PYTHON" ]]; then
  PYTHON="\$(command -v python3)"
fi

export PYTHONPATH="\$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "\$PYTHON" -m codex_gui.app "\$@"
EOF
chmod 0755 "$LAUNCHER"

mkdir -p "$HOME/.local/share/applications"
DESKTOP_FILE="$HOME/.local/share/applications/codex-gui.desktop"
while IFS= read -r line; do
  if [[ "$line" == "Exec=codex-gui" ]]; then
    printf 'Exec=%s\n' "$LAUNCHER"
  else
    printf '%s\n' "$line"
  fi
done < "$ROOT_DIR/data/codex-gui.desktop" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

CODEX_FOUND=0
for candidate in "$(command -v codex || true)" "$HOME/.local/bin/codex" "$HOME/.local/share/npm/bin/codex"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    CODEX_FOUND=1
    break
  fi
done

if [[ "$CODEX_FOUND" -eq 0 ]]; then
  printf '\nCodex CLI is not installed or not on PATH. Install it with:\n' >&2
  printf '  %s/scripts/install-codex-cli-user.sh\n' "$ROOT_DIR" >&2
fi

printf 'Installed Codex Studio.\n'
printf 'Launcher: %s\n' "$LAUNCHER"

case ":$PATH:" in
  *":$USER_BIN_DIR:"*)
    printf 'Launch it with: codex-gui\n'
    ;;
  *)
    printf 'Launch it with: %s\n' "$LAUNCHER"
    printf 'Add this to your shell profile if codex-gui is not found in new terminals:\n'
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    ;;
esac
