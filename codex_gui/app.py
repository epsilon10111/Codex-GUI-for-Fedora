from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Vte", "2.91")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte
except (ImportError, ValueError) as exc:
    print("Codex GUI requires Fedora GTK/VTE Python bindings.", file=sys.stderr)
    print("Install them with:", file=sys.stderr)
    print("  sudo dnf install -y python3-gobject gtk3 vte291", file=sys.stderr)
    print("If you are in conda/base, start the app with ./codex-gui or /usr/bin/python3.", file=sys.stderr)
    raise SystemExit(1) from exc


APP_ID = "io.github.codexgui.Fedora"
APP_NAME = "Codex GUI"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "codex-gui"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _default_workspace() -> str:
    return str(Path.home())


def _common_bin_paths() -> list[str]:
    home = Path.home()
    candidates = [
        home / ".local" / "bin",
        home / ".npm-global" / "bin",
        home / ".config" / "npm" / "bin",
        home / ".local" / "share" / "npm" / "bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    return [str(path) for path in candidates if path.exists()]


@dataclass
class CodexConfig:
    command: str = "codex"
    default_args: list[str] = field(default_factory=list)
    workspace: str = field(default_factory=_default_workspace)
    env: dict[str, str] = field(default_factory=dict)
    prefer_dark: bool = True
    auto_start: bool = True

    @classmethod
    def load(cls) -> "CodexConfig":
        if not CONFIG_PATH.exists():
            return cls()

        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        config = cls()
        for key in asdict(config):
            if key in data:
                setattr(config, key, data[key])

        if not isinstance(config.default_args, list):
            config.default_args = []
        if not isinstance(config.env, dict):
            config.env = {}
        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def env_block(self) -> dict[str, str]:
        env = dict(os.environ)
        existing_path = env.get("PATH", "")
        extra_paths = [path for path in _common_bin_paths() if path not in existing_path.split(os.pathsep)]
        if extra_paths:
            env["PATH"] = os.pathsep.join(extra_paths + [existing_path]) if existing_path else os.pathsep.join(extra_paths)
        env.update({str(key): str(value) for key, value in self.env.items()})
        return env

    def resolve_command(self) -> str | None:
        command = os.path.expanduser(self.command.strip() or "codex")
        if os.path.sep in command:
            path = Path(command)
            return str(path) if path.exists() and os.access(path, os.X_OK) else None

        env = self.env_block()
        return shutil.which(command, path=env.get("PATH"))


def parse_env_lines(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid environment line: {line}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid environment line: {line}")
        env[key] = value.strip()
    return env


def env_to_text(env: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(env.items()))


def show_error(parent: Gtk.Window, title: str, message: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, config: CodexConfig):
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(620, 420)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)

        self.command_entry = Gtk.Entry()
        self.command_entry.set_text(config.command)
        self.command_entry.set_hexpand(True)

        self.args_entry = Gtk.Entry()
        self.args_entry.set_text(shlex.join(config.default_args))
        self.args_entry.set_hexpand(True)

        self.workspace_entry = Gtk.Entry()
        self.workspace_entry.set_text(config.workspace)
        self.workspace_entry.set_hexpand(True)

        self.dark_check = Gtk.CheckButton.new_with_label("Prefer dark GTK theme")
        self.dark_check.set_active(config.prefer_dark)

        self.auto_start_check = Gtk.CheckButton.new_with_label("Start Codex automatically when the app opens")
        self.auto_start_check.set_active(config.auto_start)

        self.env_buffer = Gtk.TextBuffer()
        self.env_buffer.set_text(env_to_text(config.env))
        env_view = Gtk.TextView.new_with_buffer(self.env_buffer)
        env_view.set_monospace(True)
        env_view.set_wrap_mode(Gtk.WrapMode.NONE)

        env_scroll = Gtk.ScrolledWindow()
        env_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        env_scroll.set_min_content_height(140)
        env_scroll.add(env_view)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin=16)
        grid.attach(Gtk.Label(label="Codex command", xalign=0), 0, 0, 1, 1)
        grid.attach(self.command_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Default arguments", xalign=0), 0, 1, 1, 1)
        grid.attach(self.args_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Workspace", xalign=0), 0, 2, 1, 1)
        grid.attach(self.workspace_entry, 1, 2, 1, 1)
        grid.attach(self.dark_check, 1, 3, 1, 1)
        grid.attach(self.auto_start_check, 1, 4, 1, 1)
        grid.attach(Gtk.Label(label="Environment", xalign=0), 0, 5, 1, 1)
        grid.attach(env_scroll, 1, 5, 1, 1)

        hint = Gtk.Label(
            label="Use KEY=value lines. Example: OPENAI_API_KEY=...",
            xalign=0,
        )
        hint.get_style_context().add_class("muted")
        grid.attach(hint, 1, 6, 1, 1)

        content = self.get_content_area()
        content.add(grid)
        self.show_all()

    def to_config(self, current: CodexConfig) -> CodexConfig:
        start, end = self.env_buffer.get_bounds()
        env_text = self.env_buffer.get_text(start, end, True)
        args_text = self.args_entry.get_text().strip()

        config = CodexConfig(
            command=self.command_entry.get_text().strip() or "codex",
            default_args=shlex.split(args_text) if args_text else [],
            workspace=self.workspace_entry.get_text().strip() or current.workspace,
            env=parse_env_lines(env_text),
            prefer_dark=self.dark_check.get_active(),
            auto_start=self.auto_start_check.get_active(),
        )
        return config


class SetupPage(Gtk.Box):
    def __init__(self, on_retry: Callable[[], None], on_settings: Callable[[], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=28)
        self.get_style_context().add_class("setup-page")

        title = Gtk.Label(label="Codex CLI is not available")
        title.set_xalign(0)
        title.get_style_context().add_class("title")
        self.pack_start(title, False, False, 0)

        body = Gtk.Label(
            label=(
                "Install the official CLI, then click Retry. If your npm global "
                "bin directory is outside the desktop PATH, set the full command "
                "path in Settings."
            ),
            xalign=0,
            wrap=True,
        )
        self.pack_start(body, False, False, 0)

        commands = Gtk.TextView()
        commands.set_editable(False)
        commands.set_cursor_visible(False)
        commands.set_monospace(True)
        commands.set_wrap_mode(Gtk.WrapMode.NONE)
        commands.get_buffer().set_text(
            "sudo dnf install -y python3-gobject gtk3 vte291 nodejs npm\n"
            "npm config set prefix \"$HOME/.local/share/npm\" --location=user\n"
            "npm i -g @openai/codex\n"
            "mkdir -p \"$HOME/.local/bin\"\n"
            "ln -sf \"$HOME/.local/share/npm/bin/codex\" \"$HOME/.local/bin/codex\"\n"
            "codex login\n"
        )

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(110)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.add(commands)
        self.pack_start(scroll, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        retry = Gtk.Button.new_with_label("Retry")
        retry.connect("clicked", lambda _button: on_retry())
        settings = Gtk.Button.new_with_label("Settings")
        settings.connect("clicked", lambda _button: on_settings())
        actions.pack_start(retry, False, False, 0)
        actions.pack_start(settings, False, False, 0)
        self.pack_start(actions, False, False, 0)


class CodexTerminalPage(Gtk.Box):
    def __init__(self, title: str, argv: list[str], cwd: str, env: dict[str, str]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.title = title
        self.argv = argv
        self.cwd = cwd if Path(cwd).exists() else str(Path.home())
        self.env = env
        self.pid: int | None = None

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(30000)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_allow_hyperlink(True)
        self.terminal.set_font(Pango.FontDescription("Monospace 11"))
        self.terminal.connect("child-exited", self._on_child_exited)

        self.pack_start(self.terminal, True, True, 0)
        GLib.idle_add(self.start)

    def start(self) -> bool:
        envv = [f"{key}={value}" for key, value in self.env.items()]
        try:
            result = self.terminal.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                self.cwd,
                self.argv,
                envv,
                GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                None,
                None,
            )
            if isinstance(result, tuple) and len(result) > 1:
                self.pid = result[1]
        except GLib.Error as exc:
            self.feed(f"codex-gui: failed to start {shlex.join(self.argv)}\r\n{exc.message}\r\n")
        return False

    def feed(self, text: str) -> None:
        self.terminal.feed(text.encode("utf-8"))

    def copy_clipboard(self) -> None:
        self.terminal.copy_clipboard()

    def paste_clipboard(self) -> None:
        self.terminal.paste_clipboard()

    def _on_child_exited(self, _terminal: Vte.Terminal, status: int) -> None:
        self.feed(f"\r\ncodex-gui: process exited with status {status}\r\n")


class PromptDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window):
        super().__init__(title="Run Codex Task", transient_for=parent, modal=True)
        self.set_default_size(640, 360)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Run", Gtk.ResponseType.OK)

        self.buffer = Gtk.TextBuffer()
        text_view = Gtk.TextView.new_with_buffer(self.buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_monospace(False)

        scroll = Gtk.ScrolledWindow(margin=12)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(text_view)

        label = Gtk.Label(label="Describe the task to run with codex exec:", xalign=0, margin_start=12, margin_top=12)
        content = self.get_content_area()
        content.pack_start(label, False, False, 0)
        content.pack_start(scroll, True, True, 0)
        self.show_all()

    def prompt(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True).strip()


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1180, 760)
        self.config = CodexConfig.load()
        self.setup_page: SetupPage | None = None

        self._apply_theme()
        self._build_header()
        self._build_layout()

        if self.config.auto_start:
            self.start_interactive_session()
        else:
            self._ensure_setup_or_empty_state()

        self.show_all()

    def _build_header(self) -> None:
        header = Gtk.HeaderBar(title=APP_NAME)
        header.set_show_close_button(True)
        self.set_titlebar(header)

        workspace_button = Gtk.Button.new_with_label("Workspace")
        workspace_button.connect("clicked", self.on_choose_workspace)
        header.pack_start(workspace_button)

        new_button = Gtk.Button.new_with_label("Interactive")
        new_button.connect("clicked", lambda _button: self.start_interactive_session())
        header.pack_start(new_button)

        task_button = Gtk.Button.new_with_label("Task")
        task_button.connect("clicked", self.on_run_task)
        header.pack_start(task_button)

        login_button = Gtk.Button.new_with_label("Login")
        login_button.connect("clicked", lambda _button: self.start_login_session())
        header.pack_start(login_button)

        settings_button = Gtk.Button.new_with_label("Settings")
        settings_button.connect("clicked", lambda _button: self.open_settings())
        header.pack_end(settings_button)

        paste_button = Gtk.Button.new_with_label("Paste")
        paste_button.connect("clicked", lambda _button: self.current_terminal_action("paste"))
        header.pack_end(paste_button)

        copy_button = Gtk.Button.new_with_label("Copy")
        copy_button.connect("clicked", lambda _button: self.current_terminal_action("copy"))
        header.pack_end(copy_button)

    def _build_layout(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(root)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        sidebar.set_size_request(260, -1)
        sidebar.get_style_context().add_class("sidebar")
        root.pack_start(sidebar, False, False, 0)

        self.status_title = Gtk.Label(label="Ready", xalign=0)
        self.status_title.get_style_context().add_class("sidebar-title")
        sidebar.pack_start(self.status_title, False, False, 0)

        self.workspace_label = Gtk.Label(xalign=0, wrap=True)
        self.workspace_label.get_style_context().add_class("muted")
        sidebar.pack_start(self.workspace_label, False, False, 0)

        self.command_label = Gtk.Label(xalign=0, wrap=True)
        self.command_label.get_style_context().add_class("muted")
        sidebar.pack_start(self.command_label, False, False, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar.pack_start(separator, False, False, 8)

        for label, callback in [
            ("New interactive session", self.start_interactive_session),
            ("Run one-off task", self.on_run_task),
            ("Run codex login", self.start_login_session),
            ("Restart current tab", self.restart_current_tab),
            ("Close current tab", self.close_current_tab),
        ]:
            button = Gtk.Button.new_with_label(label)
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, cb=callback: cb())
            sidebar.pack_start(button, False, False, 0)

        sidebar.pack_start(Gtk.Box(), True, True, 0)
        config_path = Gtk.Label(label=f"Config:\n{CONFIG_PATH}", xalign=0, wrap=True)
        config_path.get_style_context().add_class("muted")
        sidebar.pack_start(config_path, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        root.pack_start(self.notebook, True, True, 0)
        self._refresh_status()

    def _apply_theme(self) -> None:
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", self.config.prefer_dark)

        css = b"""
        .sidebar { background: #141820; }
        .sidebar-title { font-size: 18px; font-weight: 700; }
        .title { font-size: 24px; font-weight: 700; }
        .muted { color: #9aa4b2; }
        textview { border-radius: 8px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_status(self) -> None:
        command = self.config.resolve_command()
        self.workspace_label.set_text(f"Workspace:\n{self.config.workspace}")
        self.command_label.set_text(f"Command:\n{command or self.config.command}")
        self.status_title.set_text("Codex ready" if command else "Setup required")

    def _codex_command(self) -> str | None:
        return self.config.resolve_command()

    def _codex_env(self) -> dict[str, str]:
        return self.config.env_block()

    def _add_page(self, page: Gtk.Widget, title: str) -> None:
        label = Gtk.Label(label=title)
        index = self.notebook.append_page(page, label)
        self.notebook.set_current_page(index)
        self.notebook.show_all()

    def _remove_setup_page(self) -> None:
        if self.setup_page is None:
            return
        page_num = self.notebook.page_num(self.setup_page)
        if page_num >= 0:
            self.notebook.remove_page(page_num)
        self.setup_page = None

    def _ensure_setup_or_empty_state(self) -> None:
        if self._codex_command():
            return
        self.show_setup_page()

    def show_setup_page(self) -> None:
        self._refresh_status()
        if self.setup_page is not None:
            page_num = self.notebook.page_num(self.setup_page)
            if page_num >= 0:
                self.notebook.set_current_page(page_num)
                return
        self.setup_page = SetupPage(on_retry=self.retry_setup, on_settings=self.open_settings)
        self._add_page(self.setup_page, "Setup")

    def retry_setup(self) -> None:
        self._refresh_status()
        if self._codex_command():
            self._remove_setup_page()
            self.start_interactive_session()

    def start_interactive_session(self) -> None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return
        self._remove_setup_page()
        argv = [command] + self.config.default_args
        self._start_terminal("Interactive", argv)

    def start_login_session(self) -> None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return
        self._remove_setup_page()
        self._start_terminal("Login", [command, "login"])

    def _start_terminal(self, title: str, argv: list[str]) -> None:
        page = CodexTerminalPage(title=title, argv=argv, cwd=self.config.workspace, env=self._codex_env())
        self._add_page(page, title)

    def on_run_task(self, *_args: object) -> None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return

        dialog = PromptDialog(self)
        response = dialog.run()
        prompt = dialog.prompt()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not prompt:
            return

        self._remove_setup_page()
        title = "Task"
        if len(prompt) <= 24:
            title = prompt
        elif prompt:
            title = prompt[:24] + "..."
        argv = [command] + self.config.default_args + ["exec", prompt]
        self._start_terminal(title, argv)

    def on_choose_workspace(self, *_args: object) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Choose workspace",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        current = Path(self.config.workspace).expanduser()
        if current.exists():
            dialog.set_current_folder(str(current))
        response = dialog.run()
        folder = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if folder:
            self.config.workspace = folder
            self.config.save()
            self._refresh_status()

    def open_settings(self, *_args: object) -> None:
        dialog = SettingsDialog(self, self.config)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            try:
                self.config = dialog.to_config(self.config)
                self.config.save()
            except (ValueError, OSError) as exc:
                dialog.destroy()
                show_error(self, "Could not save settings", str(exc))
                return
        dialog.destroy()
        self._apply_theme()
        self._refresh_status()

    def current_terminal_action(self, action: str) -> None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if not isinstance(page, CodexTerminalPage):
            return
        if action == "copy":
            page.copy_clipboard()
        elif action == "paste":
            page.paste_clipboard()

    def restart_current_tab(self, *_args: object) -> None:
        page_num = self.notebook.get_current_page()
        page = self.notebook.get_nth_page(page_num)
        if not isinstance(page, CodexTerminalPage):
            return
        title = page.title
        argv = list(page.argv)
        self.notebook.remove_page(page_num)
        self._start_terminal(title, argv)

    def close_current_tab(self, *_args: object) -> None:
        page_num = self.notebook.get_current_page()
        if page_num >= 0:
            self.notebook.remove_page(page_num)
        if self.notebook.get_n_pages() == 0:
            self._ensure_setup_or_empty_state()


class CodexGuiApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = MainWindow(self)
        window.present()


def main() -> int:
    app = CodexGuiApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
