# Codex GUI for Fedora

Unofficial native Fedora desktop wrapper for the official `@openai/codex` CLI.

The app embeds a real VTE terminal inside a GTK window, so Codex keeps its full
interactive behavior while you get a desktop launcher, workspace picker, login
launcher, quick task dialog, settings, and persistent configuration.

## Why this approach

The npm package provides the official Codex command line app, not a Linux GUI.
Reimplementing Codex internals would be fragile. This project keeps the official
CLI as the execution engine and adds a native GUI shell around it.

## Fedora dependencies

Install the system packages:

```bash
sudo dnf install -y python3-gobject gtk3 vte291 nodejs npm
```

Install the Codex CLI without writing to `/usr/local`:

```bash
./scripts/install-codex-cli-user.sh
```

This avoids the common npm `EACCES: permission denied, mkdir
'/usr/local/lib/node_modules'` error. Do not use `sudo npm i -g` unless you
intentionally want root-owned global npm packages.

If your npm global bin directory is not on the desktop session `PATH`, the GUI
also searches `~/.local/bin` and `~/.local/share/npm/bin`. You can still open
the GUI settings and set the full path to the `codex` binary.

## Run from source

```bash
./codex-gui
```

or:

```bash
python3 -m codex_gui.app
```

## Install for the current user

```bash
./scripts/install-fedora.sh
```

After installation, launch `Codex GUI` from your app menu or run:

```bash
codex-gui
```

## Features

- Start a full interactive Codex session in a selected workspace.
- Run `codex login` from the GUI.
- Run one-off tasks through `codex exec`.
- Keep command path, default arguments, workspace, environment variables, and
  theme preference in `~/.config/codex-gui/config.json`.
- Use a real PTY through VTE, preserving terminal UI behavior, keyboard input,
  streaming output, colors, and prompts.
- Install a `.desktop` launcher for Fedora desktops.

## Notes

- The GUI does not bundle OpenAI credentials or API keys.
- The GUI intentionally calls the official `codex` binary instead of duplicating
  Codex logic.
- If `codex` is missing, the app shows setup instructions instead of failing.
- The launcher prefers `/usr/bin/python3` so conda/base Python environments do
  not hide Fedora's `python3-gobject` bindings.
