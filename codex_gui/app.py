from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import signal
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
APP_NAME = "Codex Studio"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "codex-gui"
CONFIG_PATH = CONFIG_DIR / "config.json"

SANDBOX_MODES = ["read-only", "workspace-write", "danger-full-access"]
APPROVAL_POLICIES = ["untrusted", "on-request", "on-failure", "never"]


def _default_workspace() -> str:
    return str(Path.home())


def _common_bin_paths() -> list[str]:
    home = Path.home()
    candidates = [
        home / ".local" / "bin",
        home / ".local" / "share" / "npm" / "bin",
        home / ".npm-global" / "bin",
        home / ".config" / "npm" / "bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    return [str(path) for path in candidates if path.exists()]


def _rgba(color: str) -> Gdk.RGBA:
    value = Gdk.RGBA()
    value.parse(color)
    return value


def _short_path(path: str, limit: int = 46) -> str:
    expanded = str(Path(path).expanduser())
    home = str(Path.home())
    if expanded == home:
        expanded = "~"
    elif expanded.startswith(home + os.path.sep):
        expanded = "~" + expanded[len(home) :]
    if len(expanded) <= limit:
        return expanded
    return "..." + expanded[-(limit - 3) :]


def _truncate(text: str, limit: int = 32) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass
class CodexConfig:
    command: str = "codex"
    default_args: list[str] = field(default_factory=list)
    workspace: str = field(default_factory=_default_workspace)
    env: dict[str, str] = field(default_factory=dict)
    prefer_dark: bool = True
    auto_start: bool = False
    model: str = ""
    profile: str = ""
    sandbox: str = "workspace-write"
    approval: str = "on-request"
    enable_search: bool = False
    no_alt_screen: bool = False
    skip_git_repo_check: bool = False
    ephemeral_exec: bool = False
    terminal_font: str = "Monospace 11"
    prompt_history: list[str] = field(default_factory=list)
    recent_workspaces: list[str] = field(default_factory=list)

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
        if not isinstance(config.prompt_history, list):
            config.prompt_history = []
        if not isinstance(config.recent_workspaces, list):
            config.recent_workspaces = []
        if config.sandbox not in SANDBOX_MODES:
            config.sandbox = "workspace-write"
        if config.approval not in APPROVAL_POLICIES:
            config.approval = "on-request"
        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def env_block(self) -> dict[str, str]:
        env = dict(os.environ)
        existing_path = env.get("PATH", "")
        parts = existing_path.split(os.pathsep) if existing_path else []
        extra_paths = [path for path in _common_bin_paths() if path not in parts]
        if extra_paths:
            env["PATH"] = os.pathsep.join(extra_paths + parts)
        env.update({str(key): str(value) for key, value in self.env.items()})
        return env

    def resolve_command(self) -> str | None:
        command = os.path.expanduser(self.command.strip() or "codex")
        if os.path.sep in command:
            path = Path(command)
            return str(path) if path.exists() and os.access(path, os.X_OK) else None

        env = self.env_block()
        return shutil.which(command, path=env.get("PATH"))

    def remember_prompt(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            return
        history = [item for item in self.prompt_history if item != prompt]
        self.prompt_history = [prompt] + history[:9]

    def remember_workspace(self, workspace: str) -> None:
        workspace = str(Path(workspace).expanduser())
        history = [item for item in self.recent_workspaces if item != workspace]
        self.recent_workspaces = [workspace] + history[:7]


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


def button(label: str, style: str | None = None) -> Gtk.Button:
    widget = Gtk.Button.new_with_label(label)
    widget.set_halign(Gtk.Align.FILL)
    widget.set_relief(Gtk.ReliefStyle.NONE)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def section_title(label: str) -> Gtk.Label:
    widget = Gtk.Label(label=label, xalign=0)
    widget.get_style_context().add_class("section-title")
    return widget


def traffic_dot(color: str) -> Gtk.DrawingArea:
    area = Gtk.DrawingArea()
    area.set_size_request(12, 12)

    def on_draw(_widget: Gtk.DrawingArea, cr: object) -> bool:
        rgba = _rgba(color)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.arc(6, 6, 5, 0, math.pi * 2)
        cr.fill()
        return False

    area.connect("draw", on_draw)
    return area


class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, config: CodexConfig):
        super().__init__(title="Preferences", transient_for=parent, modal=True)
        self.set_default_size(720, 560)
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

        self.font_entry = Gtk.Entry()
        self.font_entry.set_text(config.terminal_font)
        self.font_entry.set_hexpand(True)

        self.model_entry = Gtk.Entry()
        self.model_entry.set_text(config.model)
        self.model_entry.set_placeholder_text("Use Codex default")
        self.model_entry.set_hexpand(True)

        self.profile_entry = Gtk.Entry()
        self.profile_entry.set_text(config.profile)
        self.profile_entry.set_placeholder_text("Optional config profile")
        self.profile_entry.set_hexpand(True)

        self.sandbox_combo = Gtk.ComboBoxText()
        for item in SANDBOX_MODES:
            self.sandbox_combo.append_text(item)
        self._set_combo(self.sandbox_combo, SANDBOX_MODES, config.sandbox)

        self.approval_combo = Gtk.ComboBoxText()
        for item in APPROVAL_POLICIES:
            self.approval_combo.append_text(item)
        self._set_combo(self.approval_combo, APPROVAL_POLICIES, config.approval)

        self.dark_check = Gtk.CheckButton.new_with_label("Use dark app chrome")
        self.dark_check.set_active(config.prefer_dark)

        self.auto_start_check = Gtk.CheckButton.new_with_label("Start an interactive session on launch")
        self.auto_start_check.set_active(config.auto_start)

        self.search_check = Gtk.CheckButton.new_with_label("Enable web search for agent sessions")
        self.search_check.set_active(config.enable_search)

        self.no_alt_screen_check = Gtk.CheckButton.new_with_label("Disable alternate screen for interactive sessions")
        self.no_alt_screen_check.set_active(config.no_alt_screen)

        self.skip_git_check = Gtk.CheckButton.new_with_label("Allow exec outside a Git repository")
        self.skip_git_check.set_active(config.skip_git_repo_check)

        self.ephemeral_exec_check = Gtk.CheckButton.new_with_label("Run one-off tasks without persisted session files")
        self.ephemeral_exec_check.set_active(config.ephemeral_exec)

        self.env_buffer = Gtk.TextBuffer()
        self.env_buffer.set_text(env_to_text(config.env))
        env_view = Gtk.TextView.new_with_buffer(self.env_buffer)
        env_view.set_monospace(True)
        env_view.set_wrap_mode(Gtk.WrapMode.NONE)

        env_scroll = Gtk.ScrolledWindow()
        env_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        env_scroll.set_min_content_height(190)
        env_scroll.add(env_view)

        notebook = Gtk.Notebook()
        notebook.append_page(self._general_page(), Gtk.Label(label="General"))
        notebook.append_page(self._agent_page(), Gtk.Label(label="Agent"))
        notebook.append_page(self._environment_page(env_scroll), Gtk.Label(label="Environment"))

        content = self.get_content_area()
        content.add(notebook)
        self.show_all()

    def _general_page(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=14, row_spacing=14, margin=18)
        grid.attach(Gtk.Label(label="Codex command", xalign=0), 0, 0, 1, 1)
        grid.attach(self.command_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Default arguments", xalign=0), 0, 1, 1, 1)
        grid.attach(self.args_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Workspace", xalign=0), 0, 2, 1, 1)
        grid.attach(self.workspace_entry, 1, 2, 1, 1)
        grid.attach(Gtk.Label(label="Terminal font", xalign=0), 0, 3, 1, 1)
        grid.attach(self.font_entry, 1, 3, 1, 1)
        grid.attach(self.dark_check, 1, 4, 1, 1)
        grid.attach(self.auto_start_check, 1, 5, 1, 1)
        return grid

    def _agent_page(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=14, row_spacing=14, margin=18)
        grid.attach(Gtk.Label(label="Model", xalign=0), 0, 0, 1, 1)
        grid.attach(self.model_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Profile", xalign=0), 0, 1, 1, 1)
        grid.attach(self.profile_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Sandbox", xalign=0), 0, 2, 1, 1)
        grid.attach(self.sandbox_combo, 1, 2, 1, 1)
        grid.attach(Gtk.Label(label="Approval", xalign=0), 0, 3, 1, 1)
        grid.attach(self.approval_combo, 1, 3, 1, 1)
        grid.attach(self.search_check, 1, 4, 1, 1)
        grid.attach(self.no_alt_screen_check, 1, 5, 1, 1)
        grid.attach(self.skip_git_check, 1, 6, 1, 1)
        grid.attach(self.ephemeral_exec_check, 1, 7, 1, 1)
        return grid

    def _environment_page(self, env_scroll: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        hint = Gtk.Label(
            label="Use KEY=value lines. Values are passed only to Codex processes started by this app.",
            xalign=0,
            wrap=True,
        )
        hint.get_style_context().add_class("muted")
        box.pack_start(hint, False, False, 0)
        box.pack_start(env_scroll, True, True, 0)
        return box

    def _set_combo(self, combo: Gtk.ComboBoxText, values: list[str], value: str) -> None:
        try:
            combo.set_active(values.index(value))
        except ValueError:
            combo.set_active(0)

    def _combo_value(self, combo: Gtk.ComboBoxText, fallback: str) -> str:
        value = combo.get_active_text()
        return value if value else fallback

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
            model=self.model_entry.get_text().strip(),
            profile=self.profile_entry.get_text().strip(),
            sandbox=self._combo_value(self.sandbox_combo, "workspace-write"),
            approval=self._combo_value(self.approval_combo, "on-request"),
            enable_search=self.search_check.get_active(),
            no_alt_screen=self.no_alt_screen_check.get_active(),
            skip_git_repo_check=self.skip_git_check.get_active(),
            ephemeral_exec=self.ephemeral_exec_check.get_active(),
            terminal_font=self.font_entry.get_text().strip() or "Monospace 11",
            prompt_history=current.prompt_history,
            recent_workspaces=current.recent_workspaces,
        )
        config.remember_workspace(config.workspace)
        return config


class PromptDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, title: str, action_label: str, history: list[str]):
        super().__init__(title=title, transient_for=parent, modal=True)
        self.set_default_size(720, 420)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button(action_label, Gtk.ResponseType.OK)

        self.buffer = Gtk.TextBuffer()
        text_view = Gtk.TextView.new_with_buffer(self.buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_left_margin(12)
        text_view.set_right_margin(12)
        text_view.set_top_margin(12)
        text_view.set_bottom_margin(12)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(text_view)
        scroll.get_style_context().add_class("prompt-editor")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        label = Gtk.Label(label="Describe what you want Codex to do:", xalign=0)
        label.get_style_context().add_class("section-title")
        box.pack_start(label, False, False, 0)
        box.pack_start(scroll, True, True, 0)

        if history:
            history_label = Gtk.Label(label="Recent prompts", xalign=0)
            history_label.get_style_context().add_class("muted")
            box.pack_start(history_label, False, False, 0)
            chips = Gtk.FlowBox()
            chips.set_selection_mode(Gtk.SelectionMode.NONE)
            chips.set_max_children_per_line(3)
            for item in history[:6]:
                chip = button(_truncate(item, 42), "chip")
                chip.connect("clicked", lambda _btn, value=item: self.buffer.set_text(value))
                chips.add(chip)
            box.pack_start(chips, False, False, 0)

        self.get_content_area().add(box)
        self.show_all()
        text_view.grab_focus()

    def prompt(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True).strip()


class SetupPage(Gtk.Box):
    def __init__(self, on_retry: Callable[[], None], on_settings: Callable[[], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin=28)
        self.get_style_context().add_class("welcome-page")

        title = Gtk.Label(label="Codex CLI is not available", xalign=0)
        title.get_style_context().add_class("hero-title")
        self.pack_start(title, False, False, 0)

        body = Gtk.Label(
            label=(
                "Install the official CLI in your user npm prefix, then click Retry. "
                "The app searches ~/.local/bin and ~/.local/share/npm/bin automatically."
            ),
            xalign=0,
            wrap=True,
        )
        body.get_style_context().add_class("muted")
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
        scroll.set_min_content_height(150)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.add(commands)
        scroll.get_style_context().add_class("code-card")
        self.pack_start(scroll, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        retry = button("Retry", "primary-button")
        retry.connect("clicked", lambda _button: on_retry())
        settings = button("Preferences", "secondary-button")
        settings.connect("clicked", lambda _button: on_settings())
        actions.pack_start(retry, False, False, 0)
        actions.pack_start(settings, False, False, 0)
        self.pack_start(actions, False, False, 0)


class WelcomePage(Gtk.Box):
    def __init__(self, window: "MainWindow"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=28)
        self.window = window
        self.get_style_context().add_class("welcome-page")

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="Build with Codex", xalign=0)
        title.get_style_context().add_class("hero-title")
        subtitle = Gtk.Label(
            label="A Fedora desktop shell for the official Codex CLI, with native sessions, tasks, review, resume, and diagnostics.",
            xalign=0,
            wrap=True,
        )
        subtitle.get_style_context().add_class("muted")
        hero.pack_start(title, False, False, 0)
        hero.pack_start(subtitle, False, False, 0)
        self.pack_start(hero, False, False, 0)

        context = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        context.pack_start(self._info_card("Workspace", _short_path(window.config.workspace)), True, True, 0)
        context.pack_start(self._info_card("Model", window.config.model or "Codex default"), True, True, 0)
        context.pack_start(self._info_card("Sandbox", window.config.sandbox), True, True, 0)
        self.pack_start(context, False, False, 0)

        self.pack_start(section_title("Start"), False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        cards = [
            ("Interactive Session", "Open the full Codex TUI in this workspace.", window.start_interactive_session),
            ("Prompted Session", "Start Codex with an initial prompt.", window.on_start_prompted_session),
            ("One-off Task", "Run codex exec and stream the output.", window.on_run_task),
            ("Resume", "Open the Codex resume picker.", window.start_resume_session),
            ("Code Review", "Run Codex review against the workspace.", window.start_review_session),
            ("Doctor", "Diagnose auth, config, and runtime issues.", window.start_doctor_session),
        ]
        for index, (title_text, body_text, callback) in enumerate(cards):
            grid.attach(self._action_card(title_text, body_text, callback), index % 3, index // 3, 1, 1)
        self.pack_start(grid, False, False, 0)

        if window.config.prompt_history:
            self.pack_start(section_title("Recent Prompts"), False, False, 0)
            recent = Gtk.FlowBox()
            recent.set_selection_mode(Gtk.SelectionMode.NONE)
            recent.set_max_children_per_line(3)
            for prompt in window.config.prompt_history[:6]:
                chip = button(_truncate(prompt, 54), "chip")
                chip.connect("clicked", lambda _button, value=prompt: window.run_task_with_prompt(value))
                recent.add(chip)
            self.pack_start(recent, False, False, 0)

    def _info_card(self, title: str, value: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=14)
        box.get_style_context().add_class("glass-card")
        top = Gtk.Label(label=title, xalign=0)
        top.get_style_context().add_class("muted")
        body = Gtk.Label(label=value, xalign=0, wrap=True)
        body.get_style_context().add_class("card-value")
        box.pack_start(top, False, False, 0)
        box.pack_start(body, False, False, 0)
        return box

    def _action_card(self, title: str, body: str, callback: Callable[[], None]) -> Gtk.Widget:
        card = Gtk.Button()
        card.set_relief(Gtk.ReliefStyle.NONE)
        card.get_style_context().add_class("action-card")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=16)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("card-title")
        body_label = Gtk.Label(label=body, xalign=0, wrap=True)
        body_label.get_style_context().add_class("muted")
        content.pack_start(title_label, False, False, 0)
        content.pack_start(body_label, False, False, 0)
        card.add(content)
        card.set_size_request(230, 118)
        card.connect("clicked", lambda _button: callback())
        return card


class CodexTerminalPage(Gtk.Box):
    def __init__(self, title: str, argv: list[str], cwd: str, env: dict[str, str], font: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.title = title
        self.argv = argv
        self.cwd = cwd if Path(cwd).exists() else str(Path.home())
        self.env = env
        self.pid: int | None = None
        self.exited = False

        chrome = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin=10)
        chrome.get_style_context().add_class("sessionbar")
        chrome.pack_start(traffic_dot("#ff5f57"), False, False, 0)
        chrome.pack_start(traffic_dot("#febc2e"), False, False, 0)
        chrome.pack_start(traffic_dot("#28c840"), False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("session-title")
        command_label = Gtk.Label(label=f"{_short_path(self.cwd)}  |  {shlex.join(argv)}", xalign=0)
        command_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        command_label.get_style_context().add_class("muted")
        labels.pack_start(title_label, False, False, 0)
        labels.pack_start(command_label, False, False, 0)
        chrome.pack_start(labels, True, True, 0)

        self.status_label = Gtk.Label(label="Running")
        self.status_label.get_style_context().add_class("status-pill")
        chrome.pack_end(self.status_label, False, False, 0)
        self.pack_start(chrome, False, False, 0)

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(40000)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_allow_hyperlink(True)
        self.terminal.set_font(Pango.FontDescription(font or "Monospace 11"))
        self.terminal.set_colors(
            _rgba("#e6edf3"),
            _rgba("#0b0f17"),
            [
                _rgba("#0b0f17"),
                _rgba("#ff6b6b"),
                _rgba("#6ee7b7"),
                _rgba("#fbbf24"),
                _rgba("#60a5fa"),
                _rgba("#c084fc"),
                _rgba("#22d3ee"),
                _rgba("#e6edf3"),
            ],
        )
        self.terminal.connect("child-exited", self._on_child_exited)

        frame = Gtk.Frame()
        frame.get_style_context().add_class("terminal-frame")
        frame.add(self.terminal)
        self.pack_start(frame, True, True, 0)
        GLib.idle_add(self.start)

    def start(self) -> bool:
        envv = [f"{key}={value}" for key, value in self.env.items()]
        try:
            ok, child_pid = self.terminal.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                self.cwd,
                self.argv,
                envv,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                None,
            )
            if ok:
                self.pid = child_pid
            else:
                self._mark_exited("Failed")
        except GLib.Error as exc:
            self.feed(f"codex-gui: failed to start {shlex.join(self.argv)}\r\n{exc.message}\r\n")
            self._mark_exited("Failed")
        return False

    def feed(self, text: str) -> None:
        self.terminal.feed(text.encode("utf-8"))

    def copy_clipboard(self) -> None:
        self.terminal.copy_clipboard()

    def paste_clipboard(self) -> None:
        self.terminal.paste_clipboard()

    def terminate(self) -> None:
        if self.pid and not self.exited:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except OSError:
                pass

    def _mark_exited(self, label: str) -> None:
        self.exited = True
        self.status_label.set_text(label)
        self.status_label.get_style_context().add_class("status-done")

    def _on_child_exited(self, _terminal: Vte.Terminal, status: int) -> None:
        self.feed(f"\r\ncodex-gui: process exited with status {status}\r\n")
        self._mark_exited("Exited")


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1280, 820)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.config = CodexConfig.load()
        self.setup_page: SetupPage | None = None
        self.welcome_page: WelcomePage | None = None

        self._apply_theme()
        self._build_header()
        self._build_layout()

        if self.config.auto_start:
            self.start_interactive_session()
        else:
            self._ensure_landing_page()

        self.show_all()

    def _build_header(self) -> None:
        header = Gtk.HeaderBar(title=APP_NAME, subtitle="Fedora desktop client for Codex")
        header.set_show_close_button(True)
        self.set_titlebar(header)

        workspace_button = button("Workspace", "toolbar-button")
        workspace_button.connect("clicked", self.on_choose_workspace)
        header.pack_start(workspace_button)

        new_button = button("New", "toolbar-button")
        new_button.connect("clicked", lambda _button: self.start_interactive_session())
        header.pack_start(new_button)

        task_button = button("Task", "toolbar-button")
        task_button.connect("clicked", self.on_run_task)
        header.pack_start(task_button)

        resume_button = button("Resume", "toolbar-button")
        resume_button.connect("clicked", lambda _button: self.start_resume_session())
        header.pack_start(resume_button)

        settings_button = button("Preferences", "toolbar-button")
        settings_button.connect("clicked", lambda _button: self.open_settings())
        header.pack_end(settings_button)

        paste_button = button("Paste", "toolbar-button")
        paste_button.connect("clicked", lambda _button: self.current_terminal_action("paste"))
        header.pack_end(paste_button)

        copy_button = button("Copy", "toolbar-button")
        copy_button.connect("clicked", lambda _button: self.current_terminal_action("copy"))
        header.pack_end(copy_button)

    def _build_layout(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.get_style_context().add_class("app-root")
        self.add(root)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        sidebar.set_size_request(306, -1)
        sidebar.get_style_context().add_class("sidebar")
        root.pack_start(sidebar, False, False, 0)

        traffic = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        traffic.pack_start(traffic_dot("#ff5f57"), False, False, 0)
        traffic.pack_start(traffic_dot("#febc2e"), False, False, 0)
        traffic.pack_start(traffic_dot("#28c840"), False, False, 0)
        sidebar.pack_start(traffic, False, False, 0)

        title = Gtk.Label(label="Codex Studio", xalign=0)
        title.get_style_context().add_class("app-title")
        sidebar.pack_start(title, False, False, 0)

        self.status_title = Gtk.Label(label="Ready", xalign=0)
        self.status_title.get_style_context().add_class("status-line")
        sidebar.pack_start(self.status_title, False, False, 0)

        workspace_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        workspace_card.get_style_context().add_class("sidebar-card")
        workspace_label = Gtk.Label(label="Workspace", xalign=0)
        workspace_label.get_style_context().add_class("muted")
        self.workspace_label = Gtk.Label(xalign=0, wrap=True)
        self.workspace_label.get_style_context().add_class("card-value")
        choose = button("Choose Folder", "secondary-button")
        choose.connect("clicked", self.on_choose_workspace)
        workspace_card.pack_start(workspace_label, False, False, 0)
        workspace_card.pack_start(self.workspace_label, False, False, 0)
        workspace_card.pack_start(choose, False, False, 0)
        sidebar.pack_start(workspace_card, False, False, 0)

        self.command_label = Gtk.Label(xalign=0, wrap=True)
        self.command_label.get_style_context().add_class("muted")
        sidebar.pack_start(self.command_label, False, False, 0)

        sidebar.pack_start(section_title("Actions"), False, False, 0)
        actions = [
            ("Interactive", self.start_interactive_session),
            ("Prompted Session", self.on_start_prompted_session),
            ("One-off Task", self.on_run_task),
            ("Resume Picker", self.start_resume_session),
            ("Resume Last", self.start_resume_last_session),
            ("Code Review", self.start_review_session),
            ("Doctor", self.start_doctor_session),
            ("Login", self.start_login_session),
        ]
        for label, callback in actions:
            item = button(label, "sidebar-button")
            item.connect("clicked", lambda _button, cb=callback: cb())
            sidebar.pack_start(item, False, False, 0)

        sidebar.pack_start(section_title("Utilities"), False, False, 0)
        utilities = [
            ("Apply Latest Diff", self.start_apply_session),
            ("Update Codex", self.start_update_session),
            ("Feature Flags", self.start_features_session),
            ("Restart Tab", self.restart_current_tab),
            ("Close Tab", self.close_current_tab),
        ]
        for label, callback in utilities:
            item = button(label, "sidebar-button-muted")
            item.connect("clicked", lambda _button, cb=callback: cb())
            sidebar.pack_start(item, False, False, 0)

        sidebar.pack_start(Gtk.Box(), True, True, 0)
        self.agent_label = Gtk.Label(xalign=0, wrap=True)
        self.agent_label.get_style_context().add_class("muted")
        sidebar.pack_start(self.agent_label, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.get_style_context().add_class("content-tabs")
        root.pack_start(self.notebook, True, True, 0)
        self._refresh_status()

    def _apply_theme(self) -> None:
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", self.config.prefer_dark)

        css = b"""
        .app-root { background: #0f1117; }
        .sidebar { background: #151820; border-right: 1px solid rgba(255,255,255,0.08); }
        .app-title { font-size: 24px; font-weight: 800; color: #f4f7fb; }
        .hero-title { font-size: 34px; font-weight: 800; color: #f7f8fb; }
        .section-title { font-size: 11px; font-weight: 800; color: #8792a2; }
        .status-line { color: #7dd3fc; font-weight: 700; }
        .muted { color: #95a0b2; }
        .card-value { color: #f4f7fb; font-size: 15px; font-weight: 700; }
        .sidebar-card, .glass-card, .action-card { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); border-radius: 18px; }
        .action-card:hover, .sidebar-button:hover, .sidebar-button-muted:hover, .toolbar-button:hover { background: rgba(255,255,255,0.12); }
        .card-title { color: #f6f7fb; font-size: 16px; font-weight: 800; }
        .sidebar-button, .sidebar-button-muted, .toolbar-button, .secondary-button, .primary-button, .chip { border-radius: 12px; padding: 8px 12px; }
        .sidebar-button { color: #ecf2ff; background: rgba(255,255,255,0.07); }
        .sidebar-button-muted { color: #b7c1d1; background: transparent; }
        .toolbar-button { color: #edf2fb; background: rgba(255,255,255,0.07); }
        .primary-button { color: #ffffff; background: #2563eb; font-weight: 800; }
        .secondary-button { color: #ecf2ff; background: rgba(255,255,255,0.08); }
        .chip { color: #dbeafe; background: rgba(96,165,250,0.16); border: 1px solid rgba(96,165,250,0.25); }
        .welcome-page { background: #10131a; }
        .sessionbar { background: #111722; border-bottom: 1px solid rgba(255,255,255,0.08); }
        .session-title { color: #f6f7fb; font-weight: 800; }
        .status-pill { color: #c7f9cc; background: rgba(34,197,94,0.18); border-radius: 999px; padding: 4px 10px; }
        .status-done { color: #e2e8f0; background: rgba(148,163,184,0.18); }
        .terminal-frame { border: 0; background: #0b0f17; }
        .prompt-editor, .code-card { border-radius: 14px; border: 1px solid rgba(255,255,255,0.10); background: rgba(255,255,255,0.05); }
        notebook tab { padding: 7px 12px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_status(self) -> None:
        command = self.config.resolve_command()
        self.workspace_label.set_text(_short_path(self.config.workspace, 54))
        self.command_label.set_text(f"Command: {command or self.config.command}")
        self.status_title.set_text("Codex ready" if command else "Setup required")
        model = self.config.model or "default"
        self.agent_label.set_text(
            f"Model: {model}\nSandbox: {self.config.sandbox}\nApproval: {self.config.approval}\nConfig: {CONFIG_PATH}"
        )

    def _codex_command(self) -> str | None:
        return self.config.resolve_command()

    def _codex_env(self) -> dict[str, str]:
        return self.config.env_block()

    def _agent_args(self, interactive: bool) -> list[str]:
        args = list(self.config.default_args)
        if self.config.model:
            args += ["--model", self.config.model]
        if self.config.profile:
            args += ["--profile", self.config.profile]
        if self.config.sandbox:
            args += ["--sandbox", self.config.sandbox]
        if self.config.approval:
            args += ["--ask-for-approval", self.config.approval]
        if self.config.enable_search:
            args.append("--search")
        if interactive and self.config.no_alt_screen:
            args.append("--no-alt-screen")
        args += ["--cd", self.config.workspace]
        return args

    def _exec_args(self) -> list[str]:
        args: list[str] = []
        if self.config.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if self.config.ephemeral_exec:
            args.append("--ephemeral")
        return args

    def _tab_label(self, title: str, page: Gtk.Widget) -> Gtk.Widget:
        tab = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=_truncate(title, 24))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        close = Gtk.Button.new_with_label("x")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.get_style_context().add_class("chip")
        close.connect("clicked", lambda _button: self._remove_page(page))
        tab.pack_start(label, True, True, 0)
        tab.pack_start(close, False, False, 0)
        tab.show_all()
        return tab

    def _add_page(self, page: Gtk.Widget, title: str) -> None:
        index = self.notebook.append_page(page, self._tab_label(title, page))
        self.notebook.set_current_page(index)
        self.notebook.show_all()

    def _remove_page(self, page: Gtk.Widget) -> None:
        if isinstance(page, CodexTerminalPage):
            page.terminate()
        page_num = self.notebook.page_num(page)
        if page_num >= 0:
            self.notebook.remove_page(page_num)
        if self.notebook.get_n_pages() == 0:
            self._ensure_landing_page()

    def _remove_setup_page(self) -> None:
        if self.setup_page is not None:
            self._remove_special_page(self.setup_page)
            self.setup_page = None

    def _remove_welcome_page(self) -> None:
        if self.welcome_page is not None:
            self._remove_special_page(self.welcome_page)
            self.welcome_page = None

    def _remove_special_page(self, page: Gtk.Widget) -> None:
        page_num = self.notebook.page_num(page)
        if page_num >= 0:
            self.notebook.remove_page(page_num)

    def _ensure_landing_page(self) -> None:
        if not self._codex_command():
            self.show_setup_page()
            return
        self.show_welcome_page()

    def show_setup_page(self) -> None:
        self._refresh_status()
        self._remove_welcome_page()
        if self.setup_page is not None:
            page_num = self.notebook.page_num(self.setup_page)
            if page_num >= 0:
                self.notebook.set_current_page(page_num)
                return
        self.setup_page = SetupPage(on_retry=self.retry_setup, on_settings=self.open_settings)
        self._add_page(self.setup_page, "Setup")

    def show_welcome_page(self) -> None:
        self._refresh_status()
        self._remove_setup_page()
        if self.welcome_page is not None:
            page_num = self.notebook.page_num(self.welcome_page)
            if page_num >= 0:
                self.notebook.set_current_page(page_num)
                return
        self.welcome_page = WelcomePage(self)
        self._add_page(self.welcome_page, "Home")

    def retry_setup(self) -> None:
        self._refresh_status()
        if self._codex_command():
            self._remove_setup_page()
            self.show_welcome_page()

    def _prepare_session(self) -> str | None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return None
        self._remove_setup_page()
        self._remove_welcome_page()
        self.config.remember_workspace(self.config.workspace)
        self.config.save()
        return command

    def _start_terminal(self, title: str, argv: list[str]) -> None:
        page = CodexTerminalPage(
            title=title,
            argv=argv,
            cwd=self.config.workspace,
            env=self._codex_env(),
            font=self.config.terminal_font,
        )
        self._add_page(page, title)

    def start_interactive_session(self) -> None:
        command = self._prepare_session()
        if not command:
            return
        argv = [command] + self._agent_args(interactive=True)
        self._start_terminal("Interactive", argv)

    def on_start_prompted_session(self, *_args: object) -> None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return
        dialog = PromptDialog(self, "Start Interactive Session", "Start", self.config.prompt_history)
        response = dialog.run()
        prompt = dialog.prompt()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not prompt:
            return
        self.config.remember_prompt(prompt)
        self.config.save()
        command = self._prepare_session()
        if not command:
            return
        argv = [command] + self._agent_args(interactive=True) + [prompt]
        self._start_terminal(_truncate(prompt, 26), argv)

    def on_run_task(self, *_args: object) -> None:
        command = self._codex_command()
        if not command:
            self.show_setup_page()
            return
        dialog = PromptDialog(self, "Run One-off Task", "Run", self.config.prompt_history)
        response = dialog.run()
        prompt = dialog.prompt()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not prompt:
            return
        self.run_task_with_prompt(prompt)

    def run_task_with_prompt(self, prompt: str) -> None:
        command = self._prepare_session()
        if not command:
            return
        self.config.remember_prompt(prompt)
        self.config.save()
        argv = [command] + self._agent_args(interactive=False) + ["exec"] + self._exec_args() + [prompt]
        self._start_terminal(_truncate(prompt, 26), argv)

    def start_resume_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        argv = [command] + self._agent_args(interactive=True) + ["resume"]
        self._start_terminal("Resume", argv)

    def start_resume_last_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        argv = [command] + self._agent_args(interactive=True) + ["resume", "--last"]
        self._start_terminal("Resume Last", argv)

    def start_review_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        argv = [command] + self._agent_args(interactive=False) + ["review"]
        self._start_terminal("Code Review", argv)

    def start_login_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        self._start_terminal("Login", [command, "login"])

    def start_doctor_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        self._start_terminal("Doctor", [command, "doctor"])

    def start_update_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        self._start_terminal("Update", [command, "update"])

    def start_apply_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        self._start_terminal("Apply", [command, "apply"])

    def start_features_session(self, *_args: object) -> None:
        command = self._prepare_session()
        if not command:
            return
        self._start_terminal("Features", [command, "features"])

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
            self.config.remember_workspace(folder)
            self.config.save()
            self._refresh_status()
            if self.welcome_page is not None:
                self._remove_welcome_page()
                self.show_welcome_page()

    def open_settings(self, *_args: object) -> None:
        dialog = SettingsDialog(self, self.config)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            try:
                self.config = dialog.to_config(self.config)
                self.config.save()
            except (ValueError, OSError) as exc:
                dialog.destroy()
                show_error(self, "Could not save preferences", str(exc))
                return
        dialog.destroy()
        self._apply_theme()
        self._refresh_status()
        if self.welcome_page is not None:
            self._remove_welcome_page()
            self.show_welcome_page()

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
        self._remove_page(page)
        self._start_terminal(title, argv)

    def close_current_tab(self, *_args: object) -> None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        if page is not None:
            self._remove_page(page)


class CodexGuiApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        actions = {
            "new": lambda _action, _param: self._window_call("start_interactive_session"),
            "task": lambda _action, _param: self._window_call("on_run_task"),
            "prompt": lambda _action, _param: self._window_call("on_start_prompted_session"),
            "resume": lambda _action, _param: self._window_call("start_resume_session"),
            "settings": lambda _action, _param: self._window_call("open_settings"),
            "close-tab": lambda _action, _param: self._window_call("close_current_tab"),
            "quit": lambda _action, _param: self.quit(),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.new", ["<Primary>n"])
        self.set_accels_for_action("app.task", ["<Primary>Return"])
        self.set_accels_for_action("app.prompt", ["<Primary><Shift>n"])
        self.set_accels_for_action("app.resume", ["<Primary>r"])
        self.set_accels_for_action("app.settings", ["<Primary>comma"])
        self.set_accels_for_action("app.close-tab", ["<Primary>w"])
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def _window_call(self, method: str) -> None:
        window = self.props.active_window
        if isinstance(window, MainWindow):
            getattr(window, method)()

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
