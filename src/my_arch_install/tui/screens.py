"""Minimalist, colorful, arrow-navigable wizard, modal, and progress screens."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, ContentSwitcher, Input, Label, Log, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from .. import __version__
from ..installer import STEP_TITLES, ArchInstaller
from ..models import (
    DiskInfo,
    PackageDetails,
    PreflightStatus,
    ProgressEvent,
    SearchedPackage,
    StepState,
)
from ..pacman_ops import (
    inspect_official_package,
    read_active_mirrors,
    run_reflector,
    search_official_packages,
    sync_temp_pacman_db,
)
from ..runner import read_appended_log_lines
from ..system_probe import (
    discover_disks,
    list_keymaps,
    list_locales,
    list_timezones,
    run_preflight,
)
from ..validation import (
    format_install_summary,
    validate_hostname,
    validate_passwords,
    validate_username,
)
from .widgets import CleanProgressBar, CleanToggle, PackageOptionList

if TYPE_CHECKING:
    from .app import MyArchInstallApp


def _window_title(section: str) -> str:
    return f"my-arch-install v{__version__}  |  {section}"


def _badges_text(app: MyArchInstallApp) -> str:
    mode_tag = "[DRY-RUN] " if app.dry_run else ""
    return f"{mode_tag}[UEFI] [ext4] [linux-zen]"


def _cycle_focus_buttons(screen: Screen[None], selectors: list[str], step: int) -> bool:
    """Move focus among enabled buttons in a row via Left/Right arrows."""
    focused = screen.focused
    widgets = []
    for sel in selectors:
        try:
            w = screen.query_one(sel, Button)
            if not w.disabled:
                widgets.append(w)
        except Exception:
            continue
    if not widgets:
        return False
    if focused in widgets:
        idx = (widgets.index(focused) + step) % len(widgets)
        widgets[idx].focus()
        return True
    return False


def _badge_option_text(badge: str, badge_style: str, label: str) -> Text:
    """Build a Rich Text prompt so OptionList never strips '[...]' as markup."""
    t = Text("  ")
    t.append(badge, style=badge_style)
    t.append(f"  {label}")
    return t


# ---------------------------------------------------------------------------
# Screen 1: Pre-flight Check
# ---------------------------------------------------------------------------


class PreflightScreen(Screen[None]):
    """Step 1/7: Verify UEFI mode, network, NTP clock, and required tools."""

    BINDINGS = [
        Binding("r", "recheck", "Re-check"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._status: PreflightStatus | None = None

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 1/7: Pre-flight"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Static("Environment Status", classes="pane-heading")
                    yield Static("Checking...", id="preflight-list")
                    yield Static("", id="preflight-note")

                with VerticalScroll(id="right-pane"):
                    yield Static("Target Profile", classes="pane-heading")
                    yield Static(
                        "CPU        : Intel Core i7-3770 (Ivy Bridge)\n"
                        "GPU        : Intel HD Graphics 4000 (crocus + VA-API)\n"
                        "Storage    : Single-boot SATA SSD (GPT: ESP + ext4)\n"
                        "Swap       : zram (zstd)\n"
                        "Kernel     : linux-zen\n"
                        "Bootloader : systemd-boot (UEFI)\n"
                        "Desktop    : Minimal KDE Plasma (Wayland)",
                        id="preflight-info",
                    )

            with Horizontal(id="window-footer"):
                yield Static("Left/Right: Actions   Enter: Confirm   R: Re-check   Q: Quit", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("Quit", id="btn-quit")
                    yield Button("Re-check", id="btn-recheck")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._refresh_checks()

    def on_key(self, event: events.Key) -> None:
        if event.key == "left":
            if _cycle_focus_buttons(self, ["#btn-quit", "#btn-recheck", "#btn-next"], -1):
                event.stop()
        elif event.key == "right":
            if _cycle_focus_buttons(self, ["#btn-quit", "#btn-recheck", "#btn-next"], 1):
                event.stop()
        elif event.key == "up":
            self.query_one("#left-pane", VerticalScroll).scroll_up(animate=False)
            self.query_one("#right-pane", VerticalScroll).scroll_up(animate=False)
            event.stop()
        elif event.key == "down":
            self.query_one("#left-pane", VerticalScroll).scroll_down(animate=False)
            self.query_one("#right-pane", VerticalScroll).scroll_down(animate=False)
            event.stop()

    def action_recheck(self) -> None:
        self._refresh_checks()

    def _refresh_checks(self) -> None:
        status = run_preflight()
        self._status = status

        rows = [
            ("UEFI Mode", status.uefi_bitness, status.is_uefi, False),
            ("Network", "Online" if status.has_internet else "Offline", status.has_internet, False),
            ("NTP Clock", "Synced" if status.time_synced else "Pending", status.time_synced, True),
            (
                "Boot Reflector",
                "Running" if status.reflector_service_active else "Idle",
                not status.reflector_service_active,
                True,
            ),
            (
                "Required Tools",
                "Present" if not status.missing_tools else f"Missing: {', '.join(status.missing_tools)}",
                not status.missing_tools,
                False,
            ),
        ]

        text = Text()
        for idx, (label, val, ok, warn) in enumerate(rows):
            if ok:
                text.append("[ OK ]", style="bold bright_green")
            elif warn:
                text.append("[WARN]", style="bold bright_yellow")
            else:
                text.append("[FAIL]", style="bold bright_red")
            text.append(f"  {label:<16} {val}")
            if idx < len(rows) - 1:
                text.append("\n")

        self.query_one("#preflight-list", Static).update(text)

        note = self.query_one("#preflight-note", Static)
        next_btn = self.query_one("#btn-next", Button)

        if status.can_install:
            note.update("\nStatus: Ready to continue.")
            note.set_classes("ok-text")
            next_btn.disabled = False
            next_btn.focus()
        elif self.installer_app.dry_run:
            note.update("\nStatus: Dry-run mode active.")
            note.set_classes("warn-text")
            next_btn.disabled = False
            next_btn.focus()
        else:
            note.update("\nStatus: UEFI boot, network access, and Live ISO tools are required.")
            note.set_classes("error-text")
            next_btn.disabled = True
            self.query_one("#btn-recheck", Button).focus()

    @on(Button.Pressed, "#btn-recheck")
    def _on_recheck(self) -> None:
        self._refresh_checks()

    @on(Button.Pressed, "#btn-quit")
    def _on_quit(self) -> None:
        self.app.exit()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        self.app.push_screen(LocalizationScreen())


# ---------------------------------------------------------------------------
# Screen 2: Localization (Timezone, Locale, Keymap)
# ---------------------------------------------------------------------------


class LocalizationScreen(Screen[None]):
    """Step 2/7: Interactive selection for timezone, locale, and console keymap."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._timezones: list[str] = []
        self._locales: list[str] = []
        self._keymaps: list[str] = []
        self._active_tab = "tab-tz"

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 2/7: Localization"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with Vertical(id="left-pane"):
                    with Horizontal(id="tab-bar"):
                        yield Button("[1] Timezone", id="btn-tab-tz", classes="tab-btn active")
                        yield Button("[2] Locale", id="btn-tab-locale", classes="tab-btn")
                        yield Button("[3] Keymap", id="btn-tab-keymap", classes="tab-btn")

                    with ContentSwitcher(initial="tab-tz", id="loc-switcher"):
                        with Vertical(id="tab-tz", classes="switcher-pane"):
                            yield Input(placeholder="Filter timezones (/ to search)...", id="filter-tz")
                            yield PackageOptionList(id="list-tz")
                        with Vertical(id="tab-locale", classes="switcher-pane"):
                            yield Input(placeholder="Filter UTF-8 locales (/ to search)...", id="filter-locale")
                            yield PackageOptionList(id="list-locale")
                        with Vertical(id="tab-keymap", classes="switcher-pane"):
                            yield Input(placeholder="Filter keymaps (/ to search)...", id="filter-keymap")
                            yield PackageOptionList(id="list-keymap")

                with VerticalScroll(id="right-pane"):
                    yield Static("Current Localization", classes="pane-heading")
                    yield Static(self._render_inspector(), id="loc-inspector")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: List   Space/Enter: Select   1/2/3: Tabs   /: Filter   Left/Right: Actions", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._timezones = list_timezones()
        self._locales = list_locales()
        self._keymaps = list_keymaps()
        self._populate_list("#list-tz", self._timezones, "", self.installer_app.config.timezone)
        self._populate_list("#list-locale", self._locales, "", self.installer_app.config.locale)
        self._populate_list("#list-keymap", self._keymaps, "", self.installer_app.config.keymap)
        self.query_one("#list-tz", PackageOptionList).focus()

    def _active_list_id(self) -> str:
        return {
            "tab-tz": "#list-tz",
            "tab-locale": "#list-locale",
            "tab-keymap": "#list-keymap",
        }[self._active_tab]

    def _active_filter_id(self) -> str:
        return {
            "tab-tz": "#filter-tz",
            "tab-locale": "#filter-locale",
            "tab-keymap": "#filter-keymap",
        }[self._active_tab]

    def _switch_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id
        self.query_one("#loc-switcher", ContentSwitcher).current = tab_id
        mapping = {
            "tab-tz": "#btn-tab-tz",
            "tab-locale": "#btn-tab-locale",
            "tab-keymap": "#btn-tab-keymap",
        }
        for tid, btn_sel in mapping.items():
            btn = self.query_one(btn_sel, Button)
            if tid == tab_id:
                btn.add_class("active")
            else:
                btn.remove_class("active")
        self.query_one(self._active_list_id(), PackageOptionList).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            if event.key == "down":
                self.query_one(self._active_list_id(), PackageOptionList).focus()
                event.stop()
            return

        if event.key == "1":
            self._switch_tab("tab-tz")
            event.stop()
        elif event.key == "2":
            self._switch_tab("tab-locale")
            event.stop()
        elif event.key == "3":
            self._switch_tab("tab-keymap")
            event.stop()
        elif event.character == "/":
            self.query_one(self._active_filter_id(), Input).focus()
            event.stop()
        elif event.key == "right":
            if isinstance(focused, OptionList):
                self.query_one("#btn-next", Button).focus()
                event.stop()
            else:
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], 1)
                event.stop()
        elif event.key == "left":
            if isinstance(focused, OptionList):
                self.query_one("#btn-back", Button).focus()
                event.stop()
            else:
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], -1)
                event.stop()
        elif event.key == "up" and isinstance(focused, Button):
            self.query_one(self._active_list_id(), PackageOptionList).focus()
            event.stop()

    @staticmethod
    def _format_loc_prompt(item: str, is_selected: bool) -> Text:
        if is_selected:
            return _badge_option_text("[ * ]", "bold bright_green", item)
        return _badge_option_text("[   ]", "white", item)

    def _populate_list(self, selector: str, items: list[str], query: str, current: str) -> None:
        ol = self.query_one(selector, PackageOptionList)
        ol.clear_options()
        q = query.strip().lower()
        filtered = [item for item in items if q in item.lower()] if q else items
        selected_idx = 0
        for idx, item in enumerate(filtered):
            is_sel = item == current
            if is_sel:
                selected_idx = idx
            ol.add_option(Option(self._format_loc_prompt(item, is_sel), id=item))
        if ol.option_count > 0:
            ol.highlighted = selected_idx

    def _update_selection_in_place(self, selector: str, old_val: str, new_val: str) -> None:
        """Update only the old and new rows so scroll position and cursor never jump."""
        ol = self.query_one(selector, PackageOptionList)
        if old_val and old_val != new_val:
            try:
                ol.replace_option_prompt(old_val, self._format_loc_prompt(old_val, False))
            except OptionDoesNotExist:
                pass
        try:
            ol.replace_option_prompt(new_val, self._format_loc_prompt(new_val, True))
        except OptionDoesNotExist:
            pass

    def _render_inspector(self) -> Text:
        cfg = self.installer_app.config
        t = Text()
        t.append("Timezone : ")
        t.append(f"{cfg.timezone}\n", style="bold bright_green")
        t.append("Locale   : ")
        t.append(f"{cfg.locale}\n", style="bold bright_green")
        t.append("Keymap   : ")
        t.append(f"{cfg.keymap}\n\n", style="bold bright_green")
        t.append("Press Space or Enter on any item to select.\n", style="white")
        t.append("Press 1, 2, or 3 to switch categories.", style="white")
        return t

    def _update_inspector(self) -> None:
        self.query_one("#loc-inspector", Static).update(self._render_inspector())

    @on(Button.Pressed, "#btn-tab-tz")
    def _tab_tz(self) -> None:
        self._switch_tab("tab-tz")

    @on(Button.Pressed, "#btn-tab-locale")
    def _tab_locale(self) -> None:
        self._switch_tab("tab-locale")

    @on(Button.Pressed, "#btn-tab-keymap")
    def _tab_keymap(self) -> None:
        self._switch_tab("tab-keymap")

    @on(Input.Changed, "#filter-tz")
    def _filter_tz(self, event: Input.Changed) -> None:
        self._populate_list("#list-tz", self._timezones, event.value, self.installer_app.config.timezone)

    @on(Input.Changed, "#filter-locale")
    def _filter_locale(self, event: Input.Changed) -> None:
        self._populate_list("#list-locale", self._locales, event.value, self.installer_app.config.locale)

    @on(Input.Changed, "#filter-keymap")
    def _filter_keymap(self, event: Input.Changed) -> None:
        self._populate_list("#list-keymap", self._keymaps, event.value, self.installer_app.config.keymap)

    @on(OptionList.OptionSelected, "#list-tz")
    def _select_tz(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            old = self.installer_app.config.timezone
            self.installer_app.config.timezone = event.option_id
            self._update_selection_in_place("#list-tz", old, event.option_id)
            self._update_inspector()

    @on(OptionList.OptionSelected, "#list-locale")
    def _select_locale(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            old = self.installer_app.config.locale
            self.installer_app.config.locale = event.option_id
            self._update_selection_in_place("#list-locale", old, event.option_id)
            self._update_inspector()

    @on(OptionList.OptionSelected, "#list-keymap")
    def _select_keymap(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            old = self.installer_app.config.keymap
            self.installer_app.config.keymap = event.option_id
            self._update_selection_in_place("#list-keymap", old, event.option_id)
            self._update_inspector()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        self.app.push_screen(IdentityScreen())


# ---------------------------------------------------------------------------
# Screen 3: Identity (Hostname, Username & Contextual Root Password)
# ---------------------------------------------------------------------------


class IdentityScreen(Screen[None]):
    """Step 3/7: Collect hostname, user account, and passwords."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        cfg = self.installer_app.config
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 3/7: Identity"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Label("Hostname", classes="field-label")
                    yield Input(value=cfg.hostname, placeholder="arch-desktop", id="inp-hostname")

                    yield Label("Username (wheel sudo)", classes="field-label")
                    yield Input(value=cfg.username, placeholder="alex", id="inp-username")

                    yield Label("User Password", classes="field-label")
                    yield Input(value=cfg.user_password, password=True, placeholder="Enter password", id="inp-user-pw")
                    yield Input(value=cfg.user_password, password=True, placeholder="Confirm password", id="inp-user-pw2")

                    yield CleanToggle(
                        "Use same password for root",
                        value=cfg.same_root_password,
                        id="tog-same-root",
                    )

                    with Vertical(
                        id="root-pw-section",
                        classes="hidden" if cfg.same_root_password else "",
                    ):
                        yield Label("Root Password", classes="field-label")
                        yield Input(
                            value=cfg.root_password,
                            password=True,
                            placeholder="Enter root password",
                            id="inp-root-pw",
                        )
                        yield Input(
                            value=cfg.root_password,
                            password=True,
                            placeholder="Confirm root password",
                            id="inp-root-pw2",
                        )

                with VerticalScroll(id="right-pane"):
                    yield Static("Validation", classes="pane-heading")
                    yield Static("", id="identity-inspector")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: Move field   Space: Toggle   Left/Right: Actions   Esc: Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._update_root_visibility(self.installer_app.config.same_root_password)
        self._sync_and_validate()
        self.query_one("#inp-hostname", Input).focus()

    def _update_root_visibility(self, same_root: bool) -> None:
        section = self.query_one("#root-pw-section", Vertical)
        if same_root:
            section.add_class("hidden")
        else:
            section.remove_class("hidden")

    def _focus_chain(self) -> list:
        chain = [
            self.query_one("#inp-hostname", Input),
            self.query_one("#inp-username", Input),
            self.query_one("#inp-user-pw", Input),
            self.query_one("#inp-user-pw2", Input),
            self.query_one("#tog-same-root", CleanToggle),
        ]
        if not self.query_one("#tog-same-root", CleanToggle).value:
            chain.append(self.query_one("#inp-root-pw", Input))
            chain.append(self.query_one("#inp-root-pw2", Input))
        next_btn = self.query_one("#btn-next", Button)
        chain.append(next_btn if not next_btn.disabled else self.query_one("#btn-back", Button))
        return chain

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        chain = self._focus_chain()

        if event.key == "down":
            if focused in chain:
                idx = min(len(chain) - 1, chain.index(focused) + 1)
                chain[idx].focus()
                event.stop()
        elif event.key == "up":
            if focused in chain:
                idx = max(0, chain.index(focused) - 1)
                chain[idx].focus()
                event.stop()
            elif focused == self.query_one("#btn-back", Button):
                chain[-2].focus()
                event.stop()
        elif event.key in {"left", "right"} and isinstance(focused, Button):
            step = -1 if event.key == "left" else 1
            _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], step)
            event.stop()

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        chain = self._focus_chain()
        if event.input in chain:
            idx = chain.index(event.input)
            if idx + 1 < len(chain):
                chain[idx + 1].focus()

    @on(Input.Changed)
    def _on_input_changed(self) -> None:
        self._sync_and_validate()

    @on(CleanToggle.Changed, "#tog-same-root")
    def _on_same_root_toggled(self, event: CleanToggle.Changed) -> None:
        self._update_root_visibility(event.value)
        self._sync_and_validate()

    def _sync_and_validate(self) -> bool:
        cfg = self.installer_app.config
        hostname = self.query_one("#inp-hostname", Input).value.strip()
        username = self.query_one("#inp-username", Input).value.strip()
        user_pw = self.query_one("#inp-user-pw", Input).value
        user_pw2 = self.query_one("#inp-user-pw2", Input).value
        same_root = self.query_one("#tog-same-root", CleanToggle).value
        root_pw = self.query_one("#inp-root-pw", Input).value
        root_pw2 = self.query_one("#inp-root-pw2", Input).value

        cfg.hostname = hostname
        cfg.username = username
        cfg.user_password = user_pw
        cfg.same_root_password = same_root
        cfg.root_password = root_pw

        h_ok, h_msg = validate_hostname(hostname)
        u_ok, u_msg = validate_username(username)
        p_ok, p_msg = validate_passwords(
            user_pw,
            user_pw2,
            same_root=same_root,
            root_pw=root_pw,
            root_confirm=root_pw2,
        )

        text = Text()
        for idx, (ok, label, msg) in enumerate(
            (
                (h_ok, "Hostname", h_msg),
                (u_ok, "Username", u_msg),
                (p_ok, "Password", p_msg),
            )
        ):
            if ok:
                text.append("[ OK ]", style="bold bright_green")
            else:
                text.append("[ -- ]", style="bold bright_red")
            text.append(f"  {label:<9}: {msg}")
            if idx < 2:
                text.append("\n")

        self.query_one("#identity-inspector", Static).update(text)
        valid = h_ok and u_ok and p_ok
        self.query_one("#btn-next", Button).disabled = not valid
        return valid

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        if self._sync_and_validate():
            self.app.push_screen(DiskScreen())


# ---------------------------------------------------------------------------
# Screen 4: Target Disk Selection
# ---------------------------------------------------------------------------


class DiskScreen(Screen[None]):
    """Step 4/7: Select target physical disk and preview GPT ext4 layout."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("r", "refresh_disks", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._disks: list[DiskInfo] = []

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 4/7: Target Disk"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with Vertical(id="left-pane"):
                    yield Static("Available Disks", classes="pane-heading")
                    yield Static("Boot media is automatically excluded.", classes="hint-text")
                    yield PackageOptionList(id="disk-list")

                with VerticalScroll(id="right-pane"):
                    yield Static("Partition Layout", classes="pane-heading")
                    yield Static("", id="disk-inspector")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: Disk   Space/Enter: Select   R: Refresh   Left/Right: Actions", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Refresh", id="btn-refresh-disks")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._load_disks()
        self.query_one("#disk-list", PackageOptionList).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if event.key == "right":
            if isinstance(focused, OptionList):
                self.query_one("#btn-next", Button).focus()
                event.stop()
            elif isinstance(focused, Button):
                _cycle_focus_buttons(self, ["#btn-back", "#btn-refresh-disks", "#btn-next"], 1)
                event.stop()
        elif event.key == "left":
            if isinstance(focused, OptionList):
                self.query_one("#btn-back", Button).focus()
                event.stop()
            elif isinstance(focused, Button):
                _cycle_focus_buttons(self, ["#btn-back", "#btn-refresh-disks", "#btn-next"], -1)
                event.stop()
        elif event.key == "up" and isinstance(focused, Button):
            self.query_one("#disk-list", PackageOptionList).focus()
            event.stop()

    def action_refresh_disks(self) -> None:
        self._load_disks()

    @staticmethod
    def _format_disk_prompt(disk: DiskInfo, is_selected: bool) -> Text:
        if is_selected:
            return _badge_option_text("[ * ]", "bold bright_green", disk.label)
        return _badge_option_text("[   ]", "white", disk.label)

    def _load_disks(self) -> None:
        disks = discover_disks()
        if not disks and self.installer_app.dry_run:
            disks = [
                DiskInfo(
                    name="sda",
                    path="/dev/sda",
                    size_bytes=256 * (1024**3),
                    model="Simulated SATA SSD (Dry-Run)",
                    transport="sata",
                    removable=False,
                )
            ]
        self._disks = disks
        if disks and self.installer_app.config.target_disk is None:
            self.installer_app.config.target_disk = disks[0]

        ol = self.query_one("#disk-list", PackageOptionList)
        ol.clear_options()
        current_path = self.installer_app.config.target_disk.path if self.installer_app.config.target_disk else ""
        sel_idx = 0
        for idx, disk in enumerate(disks):
            is_sel = disk.path == current_path
            if is_sel:
                sel_idx = idx
            ol.add_option(Option(self._format_disk_prompt(disk, is_sel), id=disk.path))
        if ol.option_count > 0:
            ol.highlighted = sel_idx

        if not disks:
            self.query_one("#disk-inspector", Static).update("No eligible target disks found.")
            self.query_one("#btn-next", Button).disabled = True
        else:
            self._render_disk_inspector(self.installer_app.config.target_disk)
            self.query_one("#btn-next", Button).disabled = False

    def _find_disk(self, disk_path: str) -> DiskInfo | None:
        for d in self._disks:
            if d.path == disk_path:
                return d
        return None

    def _render_disk_inspector(self, disk: DiskInfo | None) -> None:
        if disk is None:
            return
        cfg = self.installer_app.config
        is_sel = bool(cfg.target_disk and cfg.target_disk.path == disk.path)
        esp_gib = cfg.esp_size_mib / 1024.0
        root_gib = max(0.0, disk.size_gib - esp_gib)

        t = Text()
        t.append(f"Device     : {disk.path}")
        if is_sel:
            t.append("  [SELECTED]", style="bold bright_green")
        t.append(
            f"\nModel      : {disk.model or 'Unknown'}\n"
            f"Transport  : {(disk.transport or 'disk').upper()}\n"
            f"Capacity   : {disk.size_gib:.1f} GiB\n\n"
            "GPT Partitions:\n"
            f"  1. EFI   : {cfg.esp_size_mib} MiB  FAT32  -> /boot\n"
            f"  2. ROOT  : {root_gib:.1f} GiB   ext4   -> /\n"
            "  Swap     : zram (zstd)\n\n"
        )
        t.append(f"WARNING: All data on {disk.path} will be erased.", style="bold bright_red")
        self.query_one("#disk-inspector", Static).update(t)

    @on(OptionList.OptionHighlighted, "#disk-list")
    def _on_disk_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self._render_disk_inspector(self._find_disk(event.option_id))

    @on(OptionList.OptionSelected, "#disk-list")
    def _on_disk_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            old_disk = self.installer_app.config.target_disk
            new_disk = self._find_disk(event.option_id)
            if new_disk is not None:
                self.installer_app.config.target_disk = new_disk
                ol = self.query_one("#disk-list", PackageOptionList)
                if old_disk is not None and old_disk.path != new_disk.path:
                    try:
                        ol.replace_option_prompt(old_disk.path, self._format_disk_prompt(old_disk, False))
                    except OptionDoesNotExist:
                        pass
                try:
                    ol.replace_option_prompt(new_disk.path, self._format_disk_prompt(new_disk, True))
                except OptionDoesNotExist:
                    pass
                self._render_disk_inspector(new_disk)

    @on(Button.Pressed, "#btn-refresh-disks")
    def _on_refresh(self) -> None:
        self._load_disks()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        if self.installer_app.config.target_disk is not None:
            self.app.push_screen(OptionsAndMirrorsScreen())


# ---------------------------------------------------------------------------
# Screen 5: Unified Arrow-Navigable System, Pacman, AUR & Mirror Options
# ---------------------------------------------------------------------------


OPTION_HELP: dict[str, str] = {
    "opt:color": "Color\nEnables colored terminal output in /etc/pacman.conf.",
    "opt:verbose": "VerbosePkgLists\nShows old/new package versions and sizes in a table.",
    "opt:candy": "ILoveCandy\nReplaces pacman's '#' progress bar with Pac-Man style.",
    "opt:multilib": "[multilib] Repository\nEnables 32-bit compatibility libraries (/usr/lib32) and\nincludes [multilib] in the live official package search.",
    "opt:clear_cache": "Clear Cache After Install\nRemoves downloaded .pkg.tar.zst archives from target cache.",
    "opt:paccache": "Weekly paccache.timer\nInstalls pacman-contrib and schedules weekly cache pruning.",
    "opt:parallel": "ParallelDownloads\nCycles concurrent pacman download streams (3 / 5 / 8 / 10).",
    "aur:paru-bin": "paru-bin\nPrebuilt AUR helper binary; installs without compiling Rust.",
    "aur:paru": "paru\nCompiled in /var/tmp; removes build-time Rust afterward.",
    "aur:none": "No AUR Helper\nSkips paru, base-devel, and git.",
    "mir:keep": "Keep Current Mirrorlist\nUses the Live ISO's existing /etc/pacman.d/mirrorlist.",
    "mir:country": "Rank by Country\nRanks fastest HTTPS mirrors in the selected countries.",
    "mir:worldwide": "Rank Worldwide\nRanks the 20 latest HTTPS mirrors globally by speed.",
    "mir:countries": "Reflector Countries\nComma-separated country names used when 'Rank by country' is active.",
    "mir:target": "Install Reflector on Target\nInstalls reflector and enables reflector.timer on the target system.",
    "mir:run": "Optimize Mirrors Now\nRuns reflector now and updates /etc/pacman.d/mirrorlist.",
}


class CountriesModal(ModalScreen[str | None]):
    """Compact centered modal to edit Reflector country list."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current_countries: list[str]) -> None:
        super().__init__()
        self._current = ", ".join(current_countries)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Static("Reflector Countries", id="modal-title")
            yield Label("Comma-separated country names:", classes="field-label")
            yield Input(value=self._current, placeholder="Indonesia, Singapore", id="inp-modal-countries")
            with Horizontal(id="modal-actions"):
                yield Button("Cancel", id="btn-c-cancel")
                yield Button("Apply", id="btn-c-apply", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#inp-modal-countries", Input).focus()

    @on(Input.Submitted, "#inp-modal-countries")
    def _on_submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    @on(Button.Pressed, "#btn-c-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#btn-c-apply")
    def _on_apply(self) -> None:
        self.dismiss(self.query_one("#inp-modal-countries", Input).value)


class OptionsAndMirrorsScreen(Screen[None]):
    """Step 5/7: Unified list of pacman.conf, AUR helper, and Reflector options."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._mirror_status_msg = ""
        self._reflector_busy = False

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 5/7: Options & Mirrors"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with Vertical(id="left-pane"):
                    yield Static("System & Mirror Options", classes="pane-heading")
                    yield PackageOptionList(id="options-list")

                with VerticalScroll(id="right-pane"):
                    yield Static("Option Details", classes="pane-heading")
                    yield Static(OPTION_HELP["opt:color"], id="opt-help")
                    yield Static("", id="mirror-preview")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: Move   Space/Enter: Toggle or Run   Left/Right: Actions   Esc: Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._build_options()
        self._refresh_mirror_preview()
        self.query_one("#options-list", PackageOptionList).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if event.key == "right":
            if isinstance(focused, OptionList):
                self.query_one("#btn-next", Button).focus()
                event.stop()
            elif isinstance(focused, Button):
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], 1)
                event.stop()
        elif event.key == "left":
            if isinstance(focused, OptionList):
                self.query_one("#btn-back", Button).focus()
                event.stop()
            elif isinstance(focused, Button):
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], -1)
                event.stop()
        elif event.key == "up" and isinstance(focused, Button):
            self.query_one("#options-list", PackageOptionList).focus()
            event.stop()

    @staticmethod
    def _tog_prompt(val: bool, label: str) -> Text:
        if val:
            return _badge_option_text("[ ON ]", "bold bright_green", label)
        return _badge_option_text("[ OFF]", "white", label)

    @staticmethod
    def _rad_prompt(val: bool, label: str) -> Text:
        if val:
            return _badge_option_text("( * )", "bold bright_green", label)
        return _badge_option_text("(   )", "white", label)

    def _option_prompts(self) -> dict[str, Text]:
        cfg = self.installer_app.config
        return {
            "opt:color": self._tog_prompt(cfg.pacman.color, "Color output"),
            "opt:verbose": self._tog_prompt(cfg.pacman.verbose_pkg_lists, "Verbose package lists"),
            "opt:candy": self._tog_prompt(cfg.pacman.i_love_candy, "ILoveCandy progress bar"),
            "opt:multilib": self._tog_prompt(cfg.pacman.enable_multilib, "[multilib] 32-bit repository"),
            "opt:clear_cache": self._tog_prompt(cfg.pacman.clear_cache_after_install, "Clear cache after install"),
            "opt:paccache": self._tog_prompt(cfg.pacman.enable_paccache_timer, "Weekly paccache.timer"),
            "opt:parallel": _badge_option_text(
                f"[ {cfg.pacman.parallel_downloads:2d} ]",
                "bold bright_cyan",
                "Parallel downloads",
            ),
            "aur:paru-bin": self._rad_prompt(cfg.aur_helper == "paru-bin", "paru-bin (prebuilt)"),
            "aur:paru": self._rad_prompt(cfg.aur_helper == "paru", "paru (compile in /var/tmp)"),
            "aur:none": self._rad_prompt(cfg.aur_helper == "none", "none (skip AUR helper)"),
            "mir:keep": self._rad_prompt(cfg.mirrors.mode == "keep", "Keep current ISO mirrorlist"),
            "mir:country": self._rad_prompt(cfg.mirrors.mode == "country", "Rank by country"),
            "mir:worldwide": self._rad_prompt(cfg.mirrors.mode == "worldwide", "Rank worldwide"),
            "mir:countries": _badge_option_text(
                "[EDIT]",
                "bold bright_cyan",
                f"Countries: {', '.join(cfg.mirrors.countries) or '(none)'}",
            ),
            "mir:target": self._tog_prompt(cfg.mirrors.install_reflector_on_target, "Install reflector on target"),
            "mir:run": _badge_option_text(
                "[ ...]" if self._reflector_busy else "[ RUN]",
                "bold bright_yellow" if self._reflector_busy else "bold bright_cyan",
                "Ranking mirrors..." if self._reflector_busy else "Optimize mirrors now",
            ),
        }

    def _build_options(self) -> None:
        ol = self.query_one("#options-list", PackageOptionList)
        ol.clear_options()
        prompts = self._option_prompts()
        entries = [
            Option("  PACMAN", disabled=True),
            Option(prompts["opt:color"], id="opt:color"),
            Option(prompts["opt:verbose"], id="opt:verbose"),
            Option(prompts["opt:candy"], id="opt:candy"),
            Option(prompts["opt:multilib"], id="opt:multilib"),
            Option(prompts["opt:clear_cache"], id="opt:clear_cache"),
            Option(prompts["opt:paccache"], id="opt:paccache"),
            Option(prompts["opt:parallel"], id="opt:parallel"),
            Option("", disabled=True),
            Option("  AUR HELPER", disabled=True),
            Option(prompts["aur:paru-bin"], id="aur:paru-bin"),
            Option(prompts["aur:paru"], id="aur:paru"),
            Option(prompts["aur:none"], id="aur:none"),
            Option("", disabled=True),
            Option("  MIRRORS (REFLECTOR)", disabled=True),
            Option(prompts["mir:keep"], id="mir:keep"),
            Option(prompts["mir:country"], id="mir:country"),
            Option(prompts["mir:worldwide"], id="mir:worldwide"),
            Option(prompts["mir:countries"], id="mir:countries"),
            Option(prompts["mir:target"], id="mir:target"),
            Option(prompts["mir:run"], id="mir:run", disabled=self._reflector_busy),
        ]
        for opt in entries:
            ol.add_option(opt)
        ol.highlighted = 1

    def _sync_option_prompts_in_place(self) -> None:
        """Update option labels without clearing OptionList so scroll stays stable."""
        ol = self.query_one("#options-list", PackageOptionList)
        prompts = self._option_prompts()
        for oid, prompt in prompts.items():
            try:
                ol.replace_option_prompt(oid, prompt)
            except OptionDoesNotExist:
                continue
        if self._reflector_busy:
            ol.disable_option("mir:run")
        else:
            ol.enable_option("mir:run")

    def _refresh_mirror_preview(self) -> None:
        mirrors = self.installer_app.config.mirrors.active_mirrors_preview or read_active_mirrors(limit=6)
        self.installer_app.config.mirrors.active_mirrors_preview = mirrors
        lines = ["\nActive Mirrors (/etc/pacman.d/mirrorlist):"]
        if mirrors:
            lines.extend(f"  {i}. {m}" for i, m in enumerate(mirrors[:6], 1))
        else:
            lines.append("  (none readable on host)")
        if self._mirror_status_msg:
            lines.append(f"\n{self._mirror_status_msg}")
        self.query_one("#mirror-preview", Static).update("\n".join(lines))

    @on(OptionList.OptionHighlighted, "#options-list")
    def _on_opt_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id and event.option_id in OPTION_HELP:
            self.query_one("#opt-help", Static).update(OPTION_HELP[event.option_id])

    @on(OptionList.OptionSelected, "#options-list")
    def _on_opt_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id
        if not oid:
            return
        cfg = self.installer_app.config

        if oid == "opt:color":
            cfg.pacman.color = not cfg.pacman.color
        elif oid == "opt:verbose":
            cfg.pacman.verbose_pkg_lists = not cfg.pacman.verbose_pkg_lists
        elif oid == "opt:candy":
            cfg.pacman.i_love_candy = not cfg.pacman.i_love_candy
        elif oid == "opt:multilib":
            cfg.pacman.enable_multilib = not cfg.pacman.enable_multilib
        elif oid == "opt:clear_cache":
            cfg.pacman.clear_cache_after_install = not cfg.pacman.clear_cache_after_install
        elif oid == "opt:paccache":
            cfg.pacman.enable_paccache_timer = not cfg.pacman.enable_paccache_timer
        elif oid == "opt:parallel":
            choices = (3, 5, 8, 10)
            cur = cfg.pacman.parallel_downloads
            nxt = choices[(choices.index(cur) + 1) % len(choices)] if cur in choices else 5
            cfg.pacman.parallel_downloads = nxt
        elif oid.startswith("aur:"):
            cfg.aur_helper = oid.split(":", 1)[1]  # type: ignore[assignment]
        elif oid in {"mir:keep", "mir:country", "mir:worldwide"}:
            cfg.mirrors.mode = oid.split(":", 1)[1]  # type: ignore[assignment]
        elif oid == "mir:countries":
            self.app.push_screen(
                CountriesModal(cfg.mirrors.countries),
                self._apply_countries,
            )
            return
        elif oid == "mir:target":
            cfg.mirrors.install_reflector_on_target = not cfg.mirrors.install_reflector_on_target
        elif oid == "mir:run":
            self._trigger_reflector()
            return

        self._sync_option_prompts_in_place()

    def _apply_countries(self, value: str | None) -> None:
        if value is not None:
            parsed = [c.strip() for c in value.split(",") if c.strip()]
            self.installer_app.config.mirrors.countries = parsed
            self._sync_option_prompts_in_place()
        self.query_one("#options-list", PackageOptionList).focus()

    def _trigger_reflector(self) -> None:
        if self.installer_app.config.mirrors.mode == "keep":
            self._mirror_status_msg = "Select 'Rank by country' or 'Rank worldwide' first."
            self._refresh_mirror_preview()
            return
        self._reflector_busy = True
        self._mirror_status_msg = "Ranking mirrors..."
        self._sync_option_prompts_in_place()
        self._refresh_mirror_preview()
        self._run_reflector_worker()

    @work(thread=True)
    def _run_reflector_worker(self) -> None:
        cfg = self.installer_app.config
        try:
            mirrors = run_reflector(
                mode=cfg.mirrors.mode,
                countries=cfg.mirrors.countries,
                latest=cfg.mirrors.latest,
                runner=self.installer_app.runner,
            )
            if not self.installer_app.dry_run:
                sync_temp_pacman_db(
                    enable_multilib=cfg.pacman.enable_multilib,
                    runner=self.installer_app.runner,
                )
            self.app.call_from_thread(self._reflector_done, mirrors, None)
        except Exception as exc:
            self.app.call_from_thread(self._reflector_done, [], str(exc))

    def _reflector_done(self, mirrors: list[str], error: str | None) -> None:
        self._reflector_busy = False
        if error:
            self._mirror_status_msg = f"Reflector error: {error}"
        else:
            self.installer_app.config.mirrors.active_mirrors_preview = mirrors
            self._mirror_status_msg = f"Updated ({len(mirrors)} mirrors)."
        self._sync_option_prompts_in_place()
        self._refresh_mirror_preview()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        self.app.push_screen(PackagesScreen())


# ---------------------------------------------------------------------------
# Screen 6: Curated Packages + Live Official Search
# ---------------------------------------------------------------------------


class PackagesScreen(Screen[None]):
    """Step 6/7: Toggle curated packages and search official Arch repositories."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._search_results: dict[str, SearchedPackage] = {}
        self._details_cache: dict[str, PackageDetails] = {}
        self._active_tab = "tab-curated"

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 6/7: Packages"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with Vertical(id="left-pane"):
                    with Horizontal(id="tab-bar"):
                        yield Button("[1] Curated Catalog", id="btn-tab-curated", classes="tab-btn active")
                        yield Button("[2] Search Official Repos", id="btn-tab-search", classes="tab-btn")

                    with ContentSwitcher(initial="tab-curated", id="pkg-switcher"):
                        with Vertical(id="tab-curated", classes="switcher-pane"):
                            yield Input(placeholder="Filter packages (/ to search)...", id="filter-curated")
                            yield PackageOptionList(id="curated-list")

                        with Vertical(id="tab-search", classes="switcher-pane"):
                            yield Input(placeholder="Search official repos (Enter to run)...", id="inp-search-pkg")
                            with Horizontal(classes="button-row"):
                                yield Button("Search", id="btn-search-pkg", variant="primary")
                                yield Button("Sync Repo DB", id="btn-sync-db")
                            yield Static("Enter keywords and press Enter.", id="search-status", classes="hint-text")
                            yield PackageOptionList(id="search-list")

                with VerticalScroll(id="right-pane"):
                    yield Static("Package Inspector", classes="pane-heading")
                    yield Static("Highlight a package to view its description.", id="pkg-inspector")
                    yield Static("", id="pkg-count-footer")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: Move   Space/Enter: Toggle   1/2: Tabs   /: Search   Left/Right: Actions", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._populate_curated("")
        self._update_total_count()
        self.query_one("#curated-list", PackageOptionList).focus()

    def _switch_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id
        self.query_one("#pkg-switcher", ContentSwitcher).current = tab_id
        for tid, sel in (("tab-curated", "#btn-tab-curated"), ("tab-search", "#btn-tab-search")):
            btn = self.query_one(sel, Button)
            if tid == tab_id:
                btn.add_class("active")
            else:
                btn.remove_class("active")
        self._focus_active_content()

    def _focus_active_content(self) -> None:
        if self._active_tab == "tab-curated":
            self.query_one("#curated-list", PackageOptionList).focus()
        else:
            sl = self.query_one("#search-list", PackageOptionList)
            if sl.option_count > 0:
                sl.focus()
            else:
                self.query_one("#inp-search-pkg", Input).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            if event.key == "down":
                if self._active_tab == "tab-curated":
                    self.query_one("#curated-list", PackageOptionList).focus()
                else:
                    sl = self.query_one("#search-list", PackageOptionList)
                    if sl.option_count > 0:
                        sl.focus()
                    else:
                        self.query_one("#btn-search-pkg", Button).focus()
                event.stop()
            return

        if event.key == "1":
            self._switch_tab("tab-curated")
            event.stop()
        elif event.key == "2":
            self._switch_tab("tab-search")
            event.stop()
        elif event.character == "/":
            target = "#filter-curated" if self._active_tab == "tab-curated" else "#inp-search-pkg"
            self.query_one(target, Input).focus()
            event.stop()
        elif event.key == "right":
            if isinstance(focused, OptionList):
                self.query_one("#btn-next", Button).focus()
                event.stop()
            elif isinstance(focused, Button) and focused.id in {"btn-back", "btn-next"}:
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], 1)
                event.stop()
            elif isinstance(focused, Button) and focused.id in {"btn-search-pkg", "btn-sync-db"}:
                _cycle_focus_buttons(self, ["#btn-search-pkg", "#btn-sync-db"], 1)
                event.stop()
        elif event.key == "left":
            if isinstance(focused, OptionList):
                self.query_one("#btn-back", Button).focus()
                event.stop()
            elif isinstance(focused, Button) and focused.id in {"btn-back", "btn-next"}:
                _cycle_focus_buttons(self, ["#btn-back", "#btn-next"], -1)
                event.stop()
            elif isinstance(focused, Button) and focused.id in {"btn-search-pkg", "btn-sync-db"}:
                _cycle_focus_buttons(self, ["#btn-search-pkg", "#btn-sync-db"], -1)
                event.stop()
        elif event.key == "up" and isinstance(focused, Button) and focused.id in {"btn-back", "btn-next"}:
            self._focus_active_content()
            event.stop()

    @on(Button.Pressed, "#btn-tab-curated")
    def _on_tab_curated(self) -> None:
        self._switch_tab("tab-curated")

    @on(Button.Pressed, "#btn-tab-search")
    def _on_tab_search(self) -> None:
        self._switch_tab("tab-search")

    @staticmethod
    def _format_curated_prompt(name: str, repo: str, required: bool, selected: set[str]) -> Text:
        label = f"{name:<26} {repo}"
        if required:
            return _badge_option_text("[LOCK]", "bold bright_yellow", label)
        if name in selected:
            return _badge_option_text("[ ON ]", "bold bright_green", label)
        return _badge_option_text("[    ]", "white", label)

    @staticmethod
    def _format_search_prompt(pkg: SearchedPackage, is_selected: bool) -> Text:
        label = f"{pkg.repo}/{pkg.name:<24} {pkg.version}"
        if is_selected:
            return _badge_option_text("[ ON ]", "bold bright_green", label)
        return _badge_option_text("[    ]", "white", label)

    def _populate_curated(self, filter_text: str) -> None:
        ol = self.query_one("#curated-list", PackageOptionList)
        ol.clear_options()
        catalog = self.installer_app.catalog
        cfg = self.installer_app.config
        q = filter_text.strip().lower()

        first_section = True
        for group_id, group in catalog.groups.items():
            items = [
                pkg
                for pkg in catalog.by_group(group_id)
                if not q or q in pkg.name.lower() or q in pkg.summary.lower()
            ]
            if not items:
                continue
            if not first_section:
                ol.add_option(Option("", disabled=True))
            first_section = False
            ol.add_option(Option(f"  {group.title.upper()}", disabled=True))
            for pkg in items:
                ol.add_option(
                    Option(
                        self._format_curated_prompt(pkg.name, pkg.repo, pkg.required, cfg.selected_curated_packages),
                        id=pkg.name,
                    )
                )
        if ol.option_count > 1:
            ol.highlighted = 1

    def _update_total_count(self) -> None:
        cfg = self.installer_app.config
        total = len(cfg.all_packages(self.installer_app.catalog.required_names))
        custom_n = len(cfg.custom_packages)
        t = Text("\nTotal Selected: ")
        t.append(f"{total} packages", style="bold bright_green")
        t.append(f" ({custom_n} custom)")
        self.query_one("#pkg-count-footer", Static).update(t)

    def _inspect_curated_pkg(self, pkg_name: str) -> None:
        pkg = self.installer_app.catalog.by_name(pkg_name)
        if pkg is None:
            return
        group = self.installer_app.catalog.groups[pkg.group]
        t = Text()
        t.append(f"{pkg.name}\n", style="bold bright_cyan")
        t.append(f"Repository : {pkg.repo}\n")
        t.append(f"Group      : {group.title}\n")
        t.append("Status     : ")
        if pkg.required:
            t.append("Required (Locked)\n\n", style="bold bright_yellow")
        elif pkg.name in self.installer_app.config.selected_curated_packages:
            t.append("Selected\n\n", style="bold bright_green")
        else:
            t.append("Not Selected\n\n", style="white")
        t.append(f"Description:\n  {pkg.summary}\n\n")
        t.append(f"Role:\n  {pkg.reason}")
        self.query_one("#pkg-inspector", Static).update(t)

    @on(Input.Changed, "#filter-curated")
    def _on_curated_filter(self, event: Input.Changed) -> None:
        self._populate_curated(event.value)

    @on(OptionList.OptionHighlighted, "#curated-list")
    def _on_curated_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self._inspect_curated_pkg(event.option_id)

    @on(OptionList.OptionSelected, "#curated-list")
    def _on_curated_toggled(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        pkg = self.installer_app.catalog.by_name(event.option_id)
        if pkg is None:
            return
        if pkg.required:
            self.notify(f"'{pkg.name}' is required and locked.", severity="information")
            return
        selected = self.installer_app.config.selected_curated_packages
        if pkg.name in selected:
            selected.remove(pkg.name)
        else:
            selected.add(pkg.name)
        ol = self.query_one("#curated-list", PackageOptionList)
        ol.replace_option_prompt(
            pkg.name,
            self._format_curated_prompt(pkg.name, pkg.repo, pkg.required, selected),
        )
        self._update_total_count()
        self._inspect_curated_pkg(pkg.name)

    @on(Input.Submitted, "#inp-search-pkg")
    def _on_search_submitted(self) -> None:
        self._trigger_search()

    @on(Button.Pressed, "#btn-search-pkg")
    def _on_search_clicked(self) -> None:
        self._trigger_search()

    @on(Button.Pressed, "#btn-sync-db")
    def _on_sync_clicked(self) -> None:
        if shutil.which("pacman") is None:
            self.query_one("#search-status", Static).update("pacman unavailable on this host.")
            return
        self.query_one("#search-status", Static).update("Syncing temporary database...")
        self._sync_db_worker()

    @work(thread=True)
    def _sync_db_worker(self) -> None:
        try:
            sync_temp_pacman_db(enable_multilib=self.installer_app.config.pacman.enable_multilib)
            self.app.call_from_thread(
                self.query_one("#search-status", Static).update,
                "Database synced.",
            )
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#search-status", Static).update,
                f"Sync error: {exc}",
            )

    def _trigger_search(self) -> None:
        if shutil.which("pacman") is None:
            self.query_one("#search-status", Static).update("pacman unavailable on this host.")
            return
        query = self.query_one("#inp-search-pkg", Input).value.strip()
        if not query:
            self.query_one("#search-status", Static).update("Enter at least one keyword.")
            return
        self.query_one("#search-status", Static).update(f"Searching '{query}'...")
        self._search_worker(query)

    @work(thread=True, exclusive=True)
    def _search_worker(self, query: str) -> None:
        try:
            results = search_official_packages(
                query,
                enable_multilib=self.installer_app.config.pacman.enable_multilib,
            )
            self.app.call_from_thread(self._apply_search_results, query, results, None)
        except Exception as exc:
            self.app.call_from_thread(self._apply_search_results, query, [], str(exc))

    def _apply_search_results(
        self, query: str, results: list[SearchedPackage], error: str | None
    ) -> None:
        status = self.query_one("#search-status", Static)
        ol = self.query_one("#search-list", PackageOptionList)
        ol.clear_options()
        self._search_results.clear()

        if error:
            status.update(f"Error: {error}")
            return
        if not results:
            status.update(f"No matches for '{query}'.")
            return

        status.update(f"{len(results)} match(es). Space/Enter to select.")
        custom = self.installer_app.config.custom_packages
        for pkg in results[:120]:
            self._search_results[pkg.name] = pkg
            ol.add_option(Option(self._format_search_prompt(pkg, pkg.name in custom), id=pkg.name))
        if ol.option_count > 0:
            ol.highlighted = 0
            ol.focus()

    @on(OptionList.OptionHighlighted, "#search-list")
    def _on_search_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if not event.option_id:
            return
        pkg = self._search_results.get(event.option_id)
        if pkg is None:
            return
        cached = self._details_cache.get(pkg.name)
        self._render_searched_inspector(pkg, cached)
        if cached is None:
            self._fetch_details_worker(pkg.name)

    @work(thread=True, exclusive=True)
    def _fetch_details_worker(self, pkg_name: str) -> None:
        try:
            details = inspect_official_package(
                pkg_name,
                enable_multilib=self.installer_app.config.pacman.enable_multilib,
            )
            if details is not None:
                self._details_cache[pkg_name] = details
                pkg = self._search_results.get(pkg_name)
                if pkg is not None:
                    self.app.call_from_thread(self._render_searched_inspector, pkg, details)
        except Exception:
            pass

    def _render_searched_inspector(
        self, pkg: SearchedPackage, details: PackageDetails | None
    ) -> None:
        selected = pkg.name in self.installer_app.config.custom_packages
        t = Text()
        t.append(f"{pkg.repo}/{pkg.name} ({pkg.version})\n", style="bold bright_cyan")
        t.append("Selection  : ")
        t.append("Added\n\n" if selected else "Not added\n\n", style="bold bright_green" if selected else "white")
        t.append(f"Description:\n  {pkg.description or '(none)'}")
        if details is not None:
            t.append(
                f"\n\nDownload   : {details.download_size}\n"
                f"Installed  : {details.installed_size}\n"
                f"Depends On : {details.depends_on}\n"
                f"URL        : {details.url}"
            )
        self.query_one("#pkg-inspector", Static).update(t)

    @on(OptionList.OptionSelected, "#search-list")
    def _on_search_toggled(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        pkg = self._search_results.get(event.option_id)
        if pkg is None:
            return
        custom = self.installer_app.config.custom_packages
        if pkg.name in custom:
            del custom[pkg.name]
            is_sel = False
        else:
            custom[pkg.name] = pkg
            is_sel = True
        ol = self.query_one("#search-list", PackageOptionList)
        ol.replace_option_prompt(pkg.name, self._format_search_prompt(pkg, is_sel))
        self._update_total_count()
        self._render_searched_inspector(pkg, self._details_cache.get(pkg.name))

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-next")
    def _on_next(self) -> None:
        self.app.push_screen(SummaryScreen())


# ---------------------------------------------------------------------------
# Screen 7: Review & Confirmation
# ---------------------------------------------------------------------------


class ConfirmInstallModal(ModalScreen[bool]):
    """Centered confirmation modal requiring explicit 'INSTALL' confirmation."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, disk_path: str, dry_run: bool) -> None:
        super().__init__()
        self.disk_path = disk_path
        self.dry_run = dry_run

    def compose(self) -> ComposeResult:
        title = "Confirm Dry-Run" if self.dry_run else "Confirm Disk Erase & Install"
        warning = (
            f"Dry-run simulation for {self.disk_path} (no disk changes)."
            if self.dry_run
            else f"WARNING: All data on {self.disk_path} will be permanently erased."
        )
        with Vertical(id="modal-box"):
            yield Static(title, id="modal-title")
            yield Static(warning)
            yield Label("Type INSTALL to proceed:", classes="field-label")
            yield Input(placeholder="INSTALL", id="inp-confirm")
            with Horizontal(id="modal-actions"):
                yield Button("Cancel", id="btn-modal-cancel")
                yield Button("Install", id="btn-modal-confirm", variant="error", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#inp-confirm", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Button) and event.key in {"left", "right"}:
            _cycle_focus_buttons(
                self,
                ["#btn-modal-cancel", "#btn-modal-confirm"],
                -1 if event.key == "left" else 1,
            )
            event.stop()

    @on(Input.Changed, "#inp-confirm")
    def _on_confirm_changed(self, event: Input.Changed) -> None:
        self.query_one("#btn-modal-confirm", Button).disabled = event.value.strip() != "INSTALL"

    @on(Input.Submitted, "#inp-confirm")
    def _on_confirm_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip() == "INSTALL":
            self.dismiss(True)

    @on(Button.Pressed, "#btn-modal-cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-modal-confirm")
    def _on_confirm(self) -> None:
        self.dismiss(True)


class SummaryScreen(Screen[None]):
    """Step 7/7: Review configuration before installation."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(_window_title("Step 7/7: Review"), id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Container(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Static("Configuration Summary", classes="pane-heading")
                    yield Static("", id="summary-text")

                with VerticalScroll(id="right-pane"):
                    yield Static("Installation Steps", classes="pane-heading")
                    steps_preview = "\n".join(
                        f"  {i}. {title}" for i, title in enumerate(STEP_TITLES, 1)
                    )
                    yield Static(steps_preview, id="summary-checklist")

            with Horizontal(id="window-footer"):
                yield Static("Up/Down: Scroll   Left/Right: Actions   Enter: Start   Esc: Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Start Install", id="btn-install", variant="success")

    def on_mount(self) -> None:
        text = format_install_summary(self.installer_app.config, self.installer_app.catalog)
        self.query_one("#summary-text", Static).update(text)
        self.query_one("#btn-install", Button).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"left", "right"}:
            _cycle_focus_buttons(self, ["#btn-back", "#btn-install"], -1 if event.key == "left" else 1)
            event.stop()
        elif event.key == "up":
            self.query_one("#left-pane", VerticalScroll).scroll_up(animate=False)
            event.stop()
        elif event.key == "down":
            self.query_one("#left-pane", VerticalScroll).scroll_down(animate=False)
            event.stop()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self) -> None:
        self.action_go_back()

    @on(Button.Pressed, "#btn-install")
    def _on_install_clicked(self) -> None:
        disk = self.installer_app.config.target_disk
        disk_path = disk.path if disk else "(none)"
        self.app.push_screen(
            ConfirmInstallModal(disk_path, self.installer_app.dry_run),
            self._handle_confirm,
        )

    def _handle_confirm(self, confirmed: bool | None) -> None:
        if confirmed:
            self.app.push_screen(InstallProgressScreen())


# ---------------------------------------------------------------------------
# Screen 8: Minimalist Installation Progress View + Real-File Synced Log Drawer
# ---------------------------------------------------------------------------


class InstallProgressScreen(Screen[None]):
    """Minimalist progress bar and step list synced with the live installation log."""

    BINDINGS = [
        Binding("l", "toggle_details", "Toggle Details"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step_states: list[StepState] = [StepState.PENDING] * len(STEP_TITLES)
        self._start_time: float = 0.0
        self._finished: bool = False
        self._failed: bool = False
        self._timer = None
        self._log_sync_timer = None
        self._log_offset: int = 0

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        disk = self.installer_app.config.target_disk
        disk_label = disk.path if disk else "/dev/unknown"
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(
                    _window_title(f"Installing to {disk_label}"),
                    id="header-title",
                )
                yield Static("00:00", id="header-badges")

            with Vertical(id="progress-body"):
                yield Static("Overall Progress", classes="progress-section-title")
                yield CleanProgressBar(total=100, id="pb-overall")

                yield Static("Preparing...", id="task-label", classes="progress-section-title")
                yield CleanProgressBar(total=100, id="pb-task")

                yield Static(self._render_steps(), id="steps-box")
                yield Static("", id="error-banner")
                yield Log(id="log-drawer", max_lines=1000, auto_scroll=False)

            with Horizontal(id="window-footer"):
                yield Static(f"L: Details (Hidden)   Up/Down: Scroll Log   {self.installer_app.runner.log_path}", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("Details (L)", id="btn-toggle-log")
                    yield Button("Exit", id="btn-exit", disabled=True)
                    yield Button("Reboot", id="btn-reboot", variant="success", disabled=True)

    def on_mount(self) -> None:
        self._start_time = time.monotonic()
        self._timer = self.set_interval(1.0, self._tick_elapsed)
        self._log_sync_timer = self.set_interval(0.15, self._sync_log_drawer)
        self.query_one("#btn-toggle-log", Button).focus()
        self._run_installation_worker()

    def on_key(self, event: events.Key) -> None:
        drawer = self.query_one("#log-drawer", Log)
        drawer_visible = drawer.has_class("visible")

        if event.key in {"left", "right"}:
            _cycle_focus_buttons(
                self,
                ["#btn-toggle-log", "#btn-exit", "#btn-reboot"],
                -1 if event.key == "left" else 1,
            )
            event.stop()
            return

        if drawer_visible and not isinstance(self.focused, Log):
            if event.key == "up":
                drawer.scroll_up(animate=False)
                event.stop()
            elif event.key == "down":
                drawer.scroll_down(animate=False)
                event.stop()
            elif event.key == "pageup":
                drawer.scroll_page_up(animate=False)
                event.stop()
            elif event.key == "pagedown":
                drawer.scroll_page_down(animate=False)
                event.stop()
            elif event.key == "home":
                drawer.scroll_home(animate=False)
                event.stop()
            elif event.key == "end":
                drawer.scroll_end(animate=False)
                event.stop()

    def _sync_log_drawer(self) -> None:
        """Tail new lines directly from the real log file and auto-scroll if at bottom."""
        new_lines, new_offset = read_appended_log_lines(
            self.installer_app.runner.log_path,
            self._log_offset,
        )
        if not new_lines:
            return
        self._log_offset = new_offset
        drawer = self.query_one("#log-drawer", Log)
        at_bottom = (
            drawer.max_scroll_y <= 0
            or drawer.is_vertical_scroll_end
            or (drawer.max_scroll_y - drawer.scroll_y) <= 2
        )
        drawer.write_lines(new_lines, scroll_end=at_bottom)

    def _tick_elapsed(self) -> None:
        if self._finished:
            return
        elapsed = int(time.monotonic() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        self.query_one("#header-badges", Static).update(f"{mins:02d}:{secs:02d}")

    def _render_steps(self) -> Text:
        t = Text()
        t.append("Steps:\n", style="bold bright_cyan")
        for idx, (state, title) in enumerate(zip(self._step_states, STEP_TITLES)):
            if state == StepState.DONE:
                t.append("  [ OK ]", style="bold bright_green")
            elif state == StepState.RUNNING:
                t.append("  [ .. ]", style="bold bright_cyan")
            elif state == StepState.FAILED:
                t.append("  [FAIL]", style="bold bright_red")
            else:
                t.append("  [    ]", style="white")
            t.append(f"  {title}")
            if idx < len(STEP_TITLES) - 1:
                t.append("\n")
        return t

    def action_toggle_details(self) -> None:
        drawer = self.query_one("#log-drawer", Log)
        drawer.toggle_class("visible")
        visible = drawer.has_class("visible")
        state_word = "Visible" if visible else "Hidden"
        self.query_one("#footer-keys", Static).update(
            f"L: Details ({state_word})   Up/Down: Scroll Log   {self.installer_app.runner.log_path}"
        )
        if visible:
            self._sync_log_drawer()
            self.call_after_refresh(lambda: drawer.scroll_end(animate=False))

    @on(Button.Pressed, "#btn-toggle-log")
    def _on_toggle_btn(self) -> None:
        self.action_toggle_details()

    @work(thread=True)
    def _run_installation_worker(self) -> None:
        installer = ArchInstaller(
            self.installer_app.config,
            self.installer_app.catalog,
            self.installer_app.runner,
            on_progress=lambda ev: self.app.call_from_thread(self._handle_progress_event, ev),
        )
        try:
            installer.run()
            self.app.call_from_thread(self._on_install_success)
        except Exception as exc:
            self.app.call_from_thread(self._on_install_failure, str(exc))

    def _handle_progress_event(self, event: ProgressEvent) -> None:
        self._step_states[event.step_index] = event.step_state
        self.query_one("#steps-box", Static).update(self._render_steps())
        self.query_one("#pb-overall", CleanProgressBar).update(total=100, progress=event.overall_percent)
        self.query_one("#task-label", Static).update(
            f"Step {event.step_index + 1}/{event.total_steps}: {event.task_label}"
        )
        pb_task = self.query_one("#pb-task", CleanProgressBar)
        if event.task_percent is None:
            pb_task.update(total=None, progress=0)
        else:
            pb_task.update(total=100, progress=event.task_percent)

    def _on_install_success(self) -> None:
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
        self._sync_log_drawer()
        if self._log_sync_timer is not None:
            self._log_sync_timer.stop()
        self.query_one("#pb-overall", CleanProgressBar).update(total=100, progress=100)
        self.query_one("#pb-task", CleanProgressBar).update(total=100, progress=100)
        lbl = self.query_one("#task-label", Static)
        lbl.update("Installation complete.")
        lbl.add_class("ok-text")
        self.query_one("#btn-exit", Button).disabled = False
        reboot_btn = self.query_one("#btn-reboot", Button)
        reboot_btn.disabled = False
        reboot_btn.focus()

    def _on_install_failure(self, error_text: str) -> None:
        self._finished = True
        self._failed = True
        if self._timer is not None:
            self._timer.stop()
        self._sync_log_drawer()
        if self._log_sync_timer is not None:
            self._log_sync_timer.stop()
        lbl = self.query_one("#task-label", Static)
        lbl.update("Installation failed.")
        lbl.add_class("error-text")
        banner = self.query_one("#error-banner", Static)
        banner.update(
            f"Error:\n{error_text}\n\n"
            f"Log: {self.installer_app.runner.log_path}"
        )
        banner.add_class("visible")
        drawer = self.query_one("#log-drawer", Log)
        if not drawer.has_class("visible"):
            drawer.add_class("visible")
        self.call_after_refresh(lambda: drawer.scroll_end(animate=False))
        exit_btn = self.query_one("#btn-exit", Button)
        exit_btn.disabled = False
        exit_btn.focus()

    @on(Button.Pressed, "#btn-exit")
    def _on_exit(self) -> None:
        self.app.exit()

    @on(Button.Pressed, "#btn-reboot")
    def _on_reboot(self) -> None:
        if not self.installer_app.dry_run:
            subprocess.run(["systemctl", "reboot"], check=False)
        self.app.exit()
