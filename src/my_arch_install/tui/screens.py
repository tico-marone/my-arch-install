"""Wizard, confirmation, and minimalist progress screens for my-arch-install."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    Log,
    OptionList,
    ProgressBar,
    RadioButton,
    RadioSet,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

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
    list_reflector_countries,
    read_active_mirrors,
    run_reflector,
    search_official_packages,
    sync_temp_pacman_db,
)
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
from .widgets import PackageOptionList

if TYPE_CHECKING:
    from .app import MyArchInstallApp


def _badges_text(app: MyArchInstallApp) -> str:
    mode_tag = "[DRY-RUN] " if app.dry_run else ""
    return f"{mode_tag}[UEFI] [ext4] [linux-zen]"


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
                yield Static("my-arch-install  |  Step 1/7: Pre-flight Check", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Static("Live Environment Checks", classes="pane-heading")
                    yield Static("Running environment diagnostics...", id="preflight-list")
                    yield Static("", id="preflight-note")
                    with Horizontal(classes="button-row"):
                        yield Button("Re-check", id="btn-recheck", variant="default")

                with VerticalScroll(id="right-pane"):
                    yield Static("Target Hardware & System Scope", classes="pane-heading")
                    yield Static(
                        "Hardware Profile:\n"
                        "  CPU         : Intel Core i7-3770 (Ivy Bridge)\n"
                        "  GPU         : Intel HD Graphics 4000 (crocus + libva-intel-driver)\n"
                        "  Storage     : Single-boot SATA SSD (GPT: ESP + ext4)\n"
                        "  Network     : Ethernet via NetworkManager\n\n"
                        "System Blueprint:\n"
                        "  Kernel      : linux-zen (microcode packed in initramfs)\n"
                        "  Swap        : zram-generator (zstd compressed RAM)\n"
                        "  Bootloader  : systemd-boot (standard UEFI)\n"
                        "  Desktop     : Minimal KDE Plasma (Wayland + plasmalogin)\n\n"
                        "Safety Note:\n"
                        "  All AUR build artifacts and caches are isolated in /tmp\n"
                        "  so the installed system stays clean on first boot.",
                        id="preflight-info",
                    )

            with Horizontal(id="window-footer"):
                yield Static("Tab: Focus   R: Re-check   Q: Quit", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("Quit", id="btn-quit", variant="default")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._refresh_checks()

    def action_recheck(self) -> None:
        self._refresh_checks()

    def _refresh_checks(self) -> None:
        status = run_preflight()
        self._status = status

        def mark(ok: bool, warn: bool = False) -> str:
            if ok:
                return "[OK]  "
            return "[WARN]" if warn else "[FAIL]"

        lines = [
            f"{mark(status.is_uefi)} UEFI Boot Mode      : {status.uefi_bitness}",
            f"{mark(status.has_internet)} Internet Reachable  : {'Connected' if status.has_internet else 'Offline'}",
            f"{mark(status.time_synced, warn=True)} NTP Clock Synced    : {'Yes' if status.time_synced else 'Pending (timedatectl)'}",
            f"{mark(not status.reflector_service_active, warn=True)} Boot reflector.svc  : "
            + ("Still running in background" if status.reflector_service_active else "Idle / finished"),
            f"{mark(not status.missing_tools)} Live ISO Tools      : "
            + ("All present" if not status.missing_tools else f"Missing: {', '.join(status.missing_tools)}"),
        ]
        self.query_one("#preflight-list", Static).update("\n".join(lines))

        note_widget = self.query_one("#preflight-note", Static)
        next_btn = self.query_one("#btn-next", Button)

        if status.can_install:
            note_widget.update("\nAll required checks passed. Ready to configure installation.")
            note_widget.set_classes("ok-text")
            next_btn.disabled = False
        elif self.installer_app.dry_run:
            note_widget.update(
                "\nDry-run mode active: continuing despite missing Live ISO / UEFI environment."
            )
            note_widget.set_classes("warn-text")
            next_btn.disabled = False
        else:
            note_widget.update(
                "\nCannot proceed: UEFI boot mode, active internet, and Arch Live ISO tools are required."
            )
            note_widget.set_classes("error-text")
            next_btn.disabled = True

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

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 2/7: Localization", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with Vertical(id="left-pane"):
                    with TabbedContent(id="loc-tabs"):
                        with TabPane("Timezone", id="tab-tz"):
                            yield Input(placeholder="Filter timezones (e.g. Asia/Makassar)...", id="filter-tz")
                            yield OptionList(id="list-tz")
                        with TabPane("Locale", id="tab-locale"):
                            yield Input(placeholder="Filter UTF-8 locales (e.g. en_US)...", id="filter-locale")
                            yield OptionList(id="list-locale")
                        with TabPane("Keymap", id="tab-keymap"):
                            yield Input(placeholder="Filter console keymaps (e.g. us)...", id="filter-keymap")
                            yield OptionList(id="list-keymap")

                with VerticalScroll(id="right-pane"):
                    yield Static("Localization Inspector", classes="pane-heading")
                    yield Static(self._render_inspector(), id="loc-inspector")

            with Horizontal(id="window-footer"):
                yield Static("Enter: Select item   Tab: Switch panel   Esc: Back", id="footer-keys")
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

    def _populate_list(self, selector: str, items: list[str], query: str, current: str) -> None:
        ol = self.query_one(selector, OptionList)
        ol.clear_options()
        q = query.strip().lower()
        filtered = [item for item in items if q in item.lower()] if q else items
        for item in filtered[:250]:
            prefix = "[*] " if item == current else "    "
            ol.add_option(Option(f"{prefix}{item}", id=item))
        if ol.option_count > 0:
            ol.highlighted = 0

    def _render_inspector(self) -> str:
        cfg = self.installer_app.config
        return (
            "Current Selections:\n"
            f"  Timezone : {cfg.timezone}\n"
            f"  Locale   : {cfg.locale}\n"
            f"  Keymap   : {cfg.keymap}\n\n"
            "Details:\n"
            "  - Timezone writes /etc/localtime and syncs hardware clock.\n"
            "  - Locale enables UTF-8 generation in /etc/locale.gen and\n"
            "    sets LANG in /etc/locale.conf.\n"
            "  - Keymap writes /etc/vconsole.conf before initramfs build\n"
            "    so the sd-vconsole hook loads cleanly at boot."
        )

    def _update_inspector(self) -> None:
        self.query_one("#loc-inspector", Static).update(self._render_inspector())

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
            self.installer_app.config.timezone = event.option_id
            q = self.query_one("#filter-tz", Input).value
            self._populate_list("#list-tz", self._timezones, q, event.option_id)
            self._update_inspector()

    @on(OptionList.OptionSelected, "#list-locale")
    def _select_locale(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.installer_app.config.locale = event.option_id
            q = self.query_one("#filter-locale", Input).value
            self._populate_list("#list-locale", self._locales, q, event.option_id)
            self._update_inspector()

    @on(OptionList.OptionSelected, "#list-keymap")
    def _select_keymap(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.installer_app.config.keymap = event.option_id
            q = self.query_one("#filter-keymap", Input).value
            self._populate_list("#list-keymap", self._keymaps, q, event.option_id)
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
# Screen 3: Identity (Hostname, Username & Passwords)
# ---------------------------------------------------------------------------


class IdentityScreen(Screen[None]):
    """Step 3/7: Collect hostname, user account, and passwords interactively."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        cfg = self.installer_app.config
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 3/7: System Identity", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Label("Hostname", classes="field-label")
                    yield Input(value=cfg.hostname, placeholder="e.g. arch-desktop", id="inp-hostname")

                    yield Label("Username (wheel sudo access)", classes="field-label")
                    yield Input(value=cfg.username, placeholder="e.g. alex", id="inp-username")

                    yield Label("User Password", classes="field-label")
                    yield Input(value=cfg.user_password, password=True, placeholder="Enter password", id="inp-user-pw")
                    yield Input(value=cfg.user_password, password=True, placeholder="Confirm user password", id="inp-user-pw2")

                    with Horizontal(classes="switch-row"):
                        yield Static("Use same password for root", classes="switch-label")
                        yield Switch(value=cfg.same_root_password, id="sw-same-root")

                    yield Label("Root Password (if separate)", classes="field-label", id="lbl-root-pw")
                    yield Input(
                        value=cfg.root_password,
                        password=True,
                        placeholder="Enter root password",
                        id="inp-root-pw",
                        disabled=cfg.same_root_password,
                    )
                    yield Input(
                        value=cfg.root_password,
                        password=True,
                        placeholder="Confirm root password",
                        id="inp-root-pw2",
                        disabled=cfg.same_root_password,
                    )

                with VerticalScroll(id="right-pane"):
                    yield Static("Validation & Account Details", classes="pane-heading")
                    yield Static("", id="identity-inspector")

            with Horizontal(id="window-footer"):
                yield Static("Tab: Next field   Esc: Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._sync_and_validate()

    @on(Input.Changed)
    def _on_input_changed(self) -> None:
        self._sync_and_validate()

    @on(Switch.Changed, "#sw-same-root")
    def _on_same_root_toggled(self, event: Switch.Changed) -> None:
        self.query_one("#inp-root-pw", Input).disabled = event.value
        self.query_one("#inp-root-pw2", Input).disabled = event.value
        self._sync_and_validate()

    def _sync_and_validate(self) -> bool:
        cfg = self.installer_app.config
        hostname = self.query_one("#inp-hostname", Input).value.strip()
        username = self.query_one("#inp-username", Input).value.strip()
        user_pw = self.query_one("#inp-user-pw", Input).value
        user_pw2 = self.query_one("#inp-user-pw2", Input).value
        same_root = self.query_one("#sw-same-root", Switch).value
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

        lines = [
            f"{'[OK]' if h_ok else '[!] '} Hostname : {h_msg}",
            f"{'[OK]' if u_ok else '[!] '} Username : {u_msg}",
            f"{'[OK]' if p_ok else '[!] '} Password : {p_msg}",
            "",
            "Account Configuration:",
            "  - Primary user is added to the 'wheel' group.",
            "  - /etc/sudoers.d/10-wheel enables wheel sudo access.",
            "  - Passwords are piped to chpasswd via stdin and never",
            "    written to logs or command lines.",
        ]
        self.query_one("#identity-inspector", Static).update("\n".join(lines))
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

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._disks: list[DiskInfo] = []

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 4/7: Target Disk", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with Vertical(id="left-pane"):
                    yield Static("Available Physical Disks", classes="pane-heading")
                    yield Static(
                        "Live boot USB and active root disks are automatically excluded.",
                        classes="hint-text",
                    )
                    yield OptionList(id="disk-list")
                    with Horizontal(classes="button-row"):
                        yield Button("Refresh Disks", id="btn-refresh-disks")

                with VerticalScroll(id="right-pane"):
                    yield Static("Partition Layout Preview", classes="pane-heading")
                    yield Static("Select a disk on the left to inspect its plan.", id="disk-inspector")

            with Horizontal(id="window-footer"):
                yield Static("↑/↓ Highlight   Enter Select   Esc Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._load_disks()

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
        ol = self.query_one("#disk-list", OptionList)
        ol.clear_options()
        current_path = self.installer_app.config.target_disk.path if self.installer_app.config.target_disk else ""
        for disk in disks:
            prefix = "[*] " if disk.path == current_path else "    "
            ol.add_option(Option(f"{prefix}{disk.label}", id=disk.path))
        if ol.option_count > 0:
            ol.highlighted = 0

        if not disks:
            self.query_one("#disk-inspector", Static).update(
                "No eligible target disks found.\nEnsure your SATA SSD is connected and not mounted as '/'."
            )
            self.query_one("#btn-next", Button).disabled = True
        else:
            if self.installer_app.config.target_disk is None:
                self.installer_app.config.target_disk = disks[0]
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
        selected_mark = " (SELECTED)" if cfg.target_disk and cfg.target_disk.path == disk.path else ""
        esp_gib = cfg.esp_size_mib / 1024.0
        root_gib = max(0.0, disk.size_gib - esp_gib)
        text = (
            f"Device       : {disk.path}{selected_mark}\n"
            f"Model        : {disk.model or 'Unknown'}\n"
            f"Transport    : {(disk.transport or 'disk').upper()}\n"
            f"Total Size   : {disk.size_gib:.1f} GiB\n\n"
            "Planned Single-Boot GPT Layout:\n"
            f"  Part 1 (EFI)  : {cfg.esp_size_mib} MiB  FAT32  -> /boot\n"
            f"  Part 2 (ROOT) : ~{root_gib:.1f} GiB  ext4   -> /\n"
            "  Swap          : zram0 (zstd, up to 50% RAM)\n\n"
            "WARNING:\n"
            f"  All existing partitions and data on {disk.path}\n"
            "  will be completely erased during installation."
        )
        self.query_one("#disk-inspector", Static).update(text)

    @on(OptionList.OptionHighlighted, "#disk-list")
    def _on_disk_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self._render_disk_inspector(self._find_disk(event.option_id))

    @on(OptionList.OptionSelected, "#disk-list")
    def _on_disk_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            disk = self._find_disk(event.option_id)
            if disk is not None:
                self.installer_app.config.target_disk = disk
                self._load_disks()
                self._render_disk_inspector(disk)

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
# Screen 5: System Options (pacman.conf, multilib, AUR, Reflector Mirrors)
# ---------------------------------------------------------------------------


OPTION_HELP: dict[str, str] = {
    "sw-color": "Color\nEnables colored output in pacman for easier scanning of repos, versions, and warnings.",
    "sw-verbose": "VerbosePkgLists\nDisplays old/new package versions and download sizes in a clean column table.",
    "sw-candy": "ILoveCandy\nOfficial pacman visual option replacing '#' progress bars with a Pac-Man animation.",
    "sw-multilib": "[multilib] Repository\nEnables official 32-bit compatibility libraries (/usr/lib32) needed by Steam or Wine.\nAlso enables [multilib] in the live official package search tab.",
    "sw-clear-cache": "Clear Cache After Install\nRemoves downloaded .pkg.tar.zst archives from /var/cache/pacman/pkg/ at the end of installation.",
    "sw-paccache": "Weekly paccache.timer\nInstalls pacman-contrib and enables paccache.timer to automatically prune old package caches weekly.",
    "radio-parallel": "ParallelDownloads\nNumber of simultaneous package download streams used by pacman (Arch default: 5).",
    "radio-aur": "AUR Helper Choice\n- paru-bin: Prebuilt binary, fast install without compiling Rust.\n- paru: Compiled in /tmp with CARGO_HOME in /tmp; makepkg -sirc removes Rust afterward.\n- none: Skip AUR helper, base-devel, and git.",
    "radio-mirror": "Mirror Optimization (Reflector)\n- Keep current: Use the Live ISO's existing /etc/pacman.d/mirrorlist.\n- Optimize by country: Rank fastest HTTPS mirrors in selected countries (e.g. Indonesia,Singapore).\n- Optimize worldwide: Rank the 20 latest HTTPS mirrors globally by download speed.\nNote: pacstrap copies the Live ISO mirrorlist directly to the installed system.",
    "inp-countries": "Reflector Countries\nComma-separated country names (e.g. Indonesia,Singapore,Japan).\nUsed when 'Optimize by country' is selected.",
    "sw-target-reflector": "Install Reflector on Target\nInstalls reflector and enables reflector.timer on the installed system for periodic mirror refreshes.",
}


class OptionsAndMirrorsScreen(Screen[None]):
    """Step 5/7: Toggle pacman.conf, multilib, AUR helper, and Reflector mirrors."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        cfg = self.installer_app.config
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 5/7: System, Pacman & Mirrors", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Static("Pacman & Cleanup Options", classes="pane-heading")
                    with Horizontal(classes="switch-row"):
                        yield Static("Enable Color in pacman.conf", classes="switch-label")
                        yield Switch(value=cfg.pacman.color, id="sw-color")
                    with Horizontal(classes="switch-row"):
                        yield Static("Enable VerbosePkgLists", classes="switch-label")
                        yield Switch(value=cfg.pacman.verbose_pkg_lists, id="sw-verbose")
                    with Horizontal(classes="switch-row"):
                        yield Static("Enable ILoveCandy progress bar", classes="switch-label")
                        yield Switch(value=cfg.pacman.i_love_candy, id="sw-candy")
                    with Horizontal(classes="switch-row"):
                        yield Static("Enable [multilib] (32-bit repo)", classes="switch-label")
                        yield Switch(value=cfg.pacman.enable_multilib, id="sw-multilib")
                    with Horizontal(classes="switch-row"):
                        yield Static("Clear package cache after install", classes="switch-label")
                        yield Switch(value=cfg.pacman.clear_cache_after_install, id="sw-clear-cache")
                    with Horizontal(classes="switch-row"):
                        yield Static("Install pacman-contrib (paccache.timer)", classes="switch-label")
                        yield Switch(value=cfg.pacman.enable_paccache_timer, id="sw-paccache")

                    yield Label("ParallelDownloads Stream Count", classes="field-label")
                    with RadioSet(id="radio-parallel"):
                        yield RadioButton("3 streams", value=cfg.pacman.parallel_downloads == 3)
                        yield RadioButton("5 streams (Arch default)", value=cfg.pacman.parallel_downloads == 5)
                        yield RadioButton("8 streams", value=cfg.pacman.parallel_downloads == 8)

                    yield Label("AUR Helper", classes="field-label")
                    with RadioSet(id="radio-aur"):
                        yield RadioButton("paru-bin (prebuilt, fast)", value=cfg.aur_helper == "paru-bin")
                        yield RadioButton("paru (compile in /tmp, auto-clean Rust)", value=cfg.aur_helper == "paru")
                        yield RadioButton("None (skip AUR helper)", value=cfg.aur_helper == "none")

                    yield Static("Mirror Optimization (Reflector)", classes="pane-heading")
                    with RadioSet(id="radio-mirror"):
                        yield RadioButton("Keep current ISO mirrorlist", value=cfg.mirrors.mode == "keep")
                        yield RadioButton("Optimize by country", value=cfg.mirrors.mode == "country")
                        yield RadioButton("Optimize worldwide", value=cfg.mirrors.mode == "worldwide")

                    yield Label("Countries (comma-separated)", classes="field-label")
                    yield Input(
                        value=",".join(cfg.mirrors.countries),
                        placeholder="Indonesia,Singapore",
                        id="inp-countries",
                        disabled=cfg.mirrors.mode != "country",
                    )
                    with Horizontal(classes="button-row"):
                        yield Button("Optimize Mirrors Now", id="btn-run-reflector", variant="default")
                    yield Static("", id="mirror-status")

                    with Horizontal(classes="switch-row"):
                        yield Static("Install reflector & timer on target", classes="switch-label")
                        yield Switch(value=cfg.mirrors.install_reflector_on_target, id="sw-target-reflector")

                with VerticalScroll(id="right-pane"):
                    yield Static("Option Inspector & Active Mirrors", classes="pane-heading")
                    yield Static(OPTION_HELP["sw-color"], id="opt-help")
                    yield Static("", id="mirror-preview")

            with Horizontal(id="window-footer"):
                yield Static("Tab: Focus option   Space: Toggle   Esc: Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._refresh_mirror_preview()
        available = list_reflector_countries()
        self.installer_app.log(f"Loaded {len(available)} reflector countries")

    def on_descendant_focus(self, event) -> None:  # type: ignore[no-untyped-def]
        widget_id = getattr(event.widget, "id", None)
        if widget_id and widget_id in OPTION_HELP:
            self.query_one("#opt-help", Static).update(OPTION_HELP[widget_id])

    def _refresh_mirror_preview(self) -> None:
        mirrors = self.installer_app.config.mirrors.active_mirrors_preview or read_active_mirrors(limit=6)
        self.installer_app.config.mirrors.active_mirrors_preview = mirrors
        lines = ["\nCurrent Top Mirrors (/etc/pacman.d/mirrorlist):"]
        if mirrors:
            lines.extend(f"  {i}. {m}" for i, m in enumerate(mirrors[:6], 1))
        else:
            lines.append("  (no active mirrors readable on host)")
        self.query_one("#mirror-preview", Static).update("\n".join(lines))

    @on(Switch.Changed)
    def _on_switch(self, event: Switch.Changed) -> None:
        cfg = self.installer_app.config
        sid = event.switch.id
        if sid == "sw-color":
            cfg.pacman.color = event.value
        elif sid == "sw-verbose":
            cfg.pacman.verbose_pkg_lists = event.value
        elif sid == "sw-candy":
            cfg.pacman.i_love_candy = event.value
        elif sid == "sw-multilib":
            cfg.pacman.enable_multilib = event.value
        elif sid == "sw-clear-cache":
            cfg.pacman.clear_cache_after_install = event.value
        elif sid == "sw-paccache":
            cfg.pacman.enable_paccache_timer = event.value
        elif sid == "sw-target-reflector":
            cfg.mirrors.install_reflector_on_target = event.value

    @on(RadioSet.Changed, "#radio-parallel")
    def _on_parallel(self, event: RadioSet.Changed) -> None:
        counts = (3, 5, 8)
        if 0 <= event.index < len(counts):
            self.installer_app.config.pacman.parallel_downloads = counts[event.index]

    @on(RadioSet.Changed, "#radio-aur")
    def _on_aur(self, event: RadioSet.Changed) -> None:
        choices = ("paru-bin", "paru", "none")
        if 0 <= event.index < len(choices):
            self.installer_app.config.aur_helper = choices[event.index]  # type: ignore[assignment]

    @on(RadioSet.Changed, "#radio-mirror")
    def _on_mirror_mode(self, event: RadioSet.Changed) -> None:
        modes = ("keep", "country", "worldwide")
        if 0 <= event.index < len(modes):
            mode = modes[event.index]
            self.installer_app.config.mirrors.mode = mode  # type: ignore[assignment]
            self.query_one("#inp-countries", Input).disabled = mode != "country"

    @on(Input.Changed, "#inp-countries")
    def _on_countries_changed(self, event: Input.Changed) -> None:
        parsed = [c.strip() for c in event.value.split(",") if c.strip()]
        self.installer_app.config.mirrors.countries = parsed

    @on(Button.Pressed, "#btn-run-reflector")
    def _on_optimize_mirrors(self) -> None:
        if self.installer_app.config.mirrors.mode == "keep":
            self.query_one("#mirror-status", Static).update(
                "Mode is 'Keep current'. Select country or worldwide to rank mirrors."
            )
            return
        btn = self.query_one("#btn-run-reflector", Button)
        btn.disabled = True
        self.query_one("#mirror-status", Static).update("Ranking mirrors with reflector...")
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
        btn = self.query_one("#btn-run-reflector", Button)
        btn.disabled = False
        status = self.query_one("#mirror-status", Static)
        if error:
            status.update(f"Reflector failed: {error}")
            status.set_classes("error-text")
        else:
            self.installer_app.config.mirrors.active_mirrors_preview = mirrors
            status.update(f"Mirrorlist updated ({len(mirrors)} top mirrors).")
            status.set_classes("ok-text")
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
# Screen 6: Curated Packages + Live Official Search (pacman -Ss / -Si)
# ---------------------------------------------------------------------------


class PackagesScreen(Screen[None]):
    """Step 6/7: Toggle curated packages, read rationales, and search official repos."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._search_results: dict[str, SearchedPackage] = {}
        self._details_cache: dict[str, PackageDetails] = {}

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 6/7: Packages & Search", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with Vertical(id="left-pane"):
                    with TabbedContent(id="pkg-tabs"):
                        with TabPane("Curated Catalog", id="tab-curated"):
                            yield Input(placeholder="Filter curated packages...", id="filter-curated")
                            yield PackageOptionList(id="curated-list")
                        with TabPane("Search Official Repos", id="tab-search"):
                            yield Input(
                                placeholder="Search official Arch repos (pacman -Ss)...",
                                id="inp-search-pkg",
                            )
                            with Horizontal(classes="button-row"):
                                yield Button("Search", id="btn-search-pkg", variant="primary")
                                yield Button("Sync Repo DB", id="btn-sync-db")
                            yield Static("Enter keywords and press Search.", id="search-status", classes="hint-text")
                            yield PackageOptionList(id="search-list")

                with VerticalScroll(id="right-pane"):
                    yield Static("Package Inspector", classes="pane-heading")
                    yield Static(
                        "Highlight any package (including [REQ] core dependencies)\n"
                        "to read its summary and why it is included.",
                        id="pkg-inspector",
                    )
                    yield Static("", id="pkg-count-footer")

            with Horizontal(id="window-footer"):
                yield Static("↑/↓ Inspect   Space/Enter Toggle   Tab Switch   Esc Back", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Next ->", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._populate_curated("")
        self._update_total_count()

    def _populate_curated(self, filter_text: str) -> None:
        ol = self.query_one("#curated-list", PackageOptionList)
        ol.clear_options()
        catalog = self.installer_app.catalog
        cfg = self.installer_app.config
        q = filter_text.strip().lower()

        for group_id, group in catalog.groups.items():
            items = [
                pkg
                for pkg in catalog.by_group(group_id)
                if not q or q in pkg.name.lower() or q in pkg.summary.lower()
            ]
            if not items:
                continue
            ol.add_option(Option(f"-- {group.title} --", disabled=True))
            for pkg in items:
                ol.add_option(
                    Option(
                        self._format_curated_label(pkg.name, pkg.required, cfg.selected_curated_packages),
                        id=pkg.name,
                    )
                )

    @staticmethod
    def _format_curated_label(name: str, required: bool, selected: set[str]) -> str:
        if required:
            return f"[REQ] {name}"
        mark = "[x]" if name in selected else "[ ]"
        return f"{mark} {name}"

    def _update_total_count(self) -> None:
        cfg = self.installer_app.config
        total = len(cfg.all_packages(self.installer_app.catalog.required_names))
        custom_n = len(cfg.custom_packages)
        self.query_one("#pkg-count-footer", Static).update(
            f"\nTotal Selected: {total} packages ({custom_n} custom from pacman -Ss)"
        )

    def _inspect_curated_pkg(self, pkg_name: str) -> None:
        pkg = self.installer_app.catalog.by_name(pkg_name)
        if pkg is None:
            return
        group = self.installer_app.catalog.groups[pkg.group]
        status = (
            "Required Core (Locked)"
            if pkg.required
            else (
                "Selected"
                if pkg.name in self.installer_app.config.selected_curated_packages
                else "Not Selected"
            )
        )
        self.query_one("#pkg-inspector", Static).update(
            f"{pkg.name}\n"
            f"Repo   : {pkg.repo}\n"
            f"Group  : {group.title}\n"
            f"Status : {status}\n\n"
            f"Description:\n  {pkg.summary}\n\n"
            f"Why Included / Note:\n  {pkg.reason}"
        )

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
            self.notify(f"'{pkg.name}' is a required core package.", severity="information")
            return
        selected = self.installer_app.config.selected_curated_packages
        if pkg.name in selected:
            selected.remove(pkg.name)
        else:
            selected.add(pkg.name)
        ol = self.query_one("#curated-list", PackageOptionList)
        ol.replace_option_prompt(
            pkg.name,
            self._format_curated_label(pkg.name, pkg.required, selected),
        )
        self._update_total_count()
        self._inspect_curated_pkg(pkg.name)

    # --- Live Official Search Tab ---

    @on(Input.Submitted, "#inp-search-pkg")
    def _on_search_submitted(self) -> None:
        self._trigger_search()

    @on(Button.Pressed, "#btn-search-pkg")
    def _on_search_clicked(self) -> None:
        self._trigger_search()

    @on(Button.Pressed, "#btn-sync-db")
    def _on_sync_clicked(self) -> None:
        if shutil.which("pacman") is None:
            self.query_one("#search-status", Static).update(
                "pacman is unavailable on this host (requires Arch Linux / Live ISO)."
            )
            return
        self.query_one("#search-status", Static).update("Synchronizing temporary pacman database...")
        self._sync_db_worker()

    @work(thread=True)
    def _sync_db_worker(self) -> None:
        try:
            sync_temp_pacman_db(enable_multilib=self.installer_app.config.pacman.enable_multilib)
            self.app.call_from_thread(
                self.query_one("#search-status", Static).update,
                "Repository database synced. Ready to search.",
            )
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#search-status", Static).update,
                f"Sync error: {exc}",
            )

    def _trigger_search(self) -> None:
        if shutil.which("pacman") is None:
            self.query_one("#search-status", Static).update(
                "pacman is unavailable on this host (requires Arch Linux / Live ISO)."
            )
            return
        query = self.query_one("#inp-search-pkg", Input).value.strip()
        if not query:
            self.query_one("#search-status", Static).update("Enter at least one search term.")
            return
        self.query_one("#search-status", Static).update(f"Searching official repos for '{query}'...")
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
            status.update(f"Search unavailable: {error}")
            return
        if not results:
            status.update(f"No official packages matched '{query}'.")
            return

        status.update(f"Found {len(results)} result(s). Space/Enter to toggle selection.")
        custom = self.installer_app.config.custom_packages
        for pkg in results[:120]:
            self._search_results[pkg.name] = pkg
            mark = "[x]" if pkg.name in custom else "[ ]"
            ol.add_option(Option(f"{mark} {pkg.repo}/{pkg.name} ({pkg.version})", id=pkg.name))
        if ol.option_count > 0:
            ol.highlighted = 0

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
        lines = [
            f"{pkg.repo}/{pkg.name} ({pkg.version})",
            f"Selection : {'Added to install list' if selected else 'Not added'}",
            "",
            f"Description:\n  {pkg.description or '(no description)'}",
        ]
        if details is not None:
            lines.extend(
                [
                    "",
                    f"Download Size  : {details.download_size}",
                    f"Installed Size : {details.installed_size}",
                    f"Depends On     : {details.depends_on}",
                    f"URL            : {details.url}",
                ]
            )
        self.query_one("#pkg-inspector", Static).update("\n".join(lines))

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
            mark = "[ ]"
        else:
            custom[pkg.name] = pkg
            mark = "[x]"
        ol = self.query_one("#search-list", PackageOptionList)
        ol.replace_option_prompt(pkg.name, f"{mark} {pkg.repo}/{pkg.name} ({pkg.version})")
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
# Screen 7: Summary & Modal Confirmation
# ---------------------------------------------------------------------------


class ConfirmInstallModal(ModalScreen[bool]):
    """Centered confirmation modal requiring explicit 'INSTALL' confirmation."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, disk_path: str, dry_run: bool) -> None:
        super().__init__()
        self.disk_path = disk_path
        self.dry_run = dry_run

    def compose(self) -> ComposeResult:
        title = "Confirm Dry-Run Simulation" if self.dry_run else "Confirm Disk Erase & Install"
        warning = (
            f"Dry-run mode will simulate installation for {self.disk_path} without modifying disks."
            if self.dry_run
            else f"WARNING: All data on {self.disk_path} will be permanently erased!"
        )
        with Vertical(id="modal-box"):
            yield Static(title, id="modal-title")
            yield Static(warning)
            yield Label("Type INSTALL to begin:", classes="field-label")
            yield Input(placeholder="INSTALL", id="inp-confirm")
            with Horizontal(id="modal-actions"):
                yield Button("Cancel", id="btn-modal-cancel")
                yield Button("Start Install", id="btn-modal-confirm", variant="error", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#inp-confirm", Input).focus()

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
    """Step 7/7: Review full installation plan before execution."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static("my-arch-install  |  Step 7/7: Review & Confirm", id="header-title")
                yield Static(_badges_text(self.installer_app), id="header-badges")

            with Horizontal(id="grid-body"):
                with VerticalScroll(id="left-pane"):
                    yield Static("Installation Summary", classes="pane-heading")
                    yield Static("", id="summary-text")

                with VerticalScroll(id="right-pane"):
                    yield Static("Execution Checklist", classes="pane-heading")
                    steps_preview = "\n".join(
                        f"  {i}. {title}" for i, title in enumerate(STEP_TITLES, 1)
                    )
                    yield Static(
                        "Pipeline Steps:\n"
                        f"{steps_preview}\n\n"
                        "Cleanliness Guarantees:\n"
                        "  - Live log stored only in /tmp/my-arch-install.log\n"
                        "  - AUR helper built in /tmp with CARGO_HOME in /tmp\n"
                        "  - makepkg -sirc removes build-time Rust toolchain\n"
                        "  - Temporary sudoers rule removed in finally block\n\n"
                        "Press 'Start Installation' to confirm.",
                        id="summary-checklist",
                    )

            with Horizontal(id="window-footer"):
                yield Static("Esc: Back   Enter: Activate button", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("<- Back", id="btn-back")
                    yield Button("Start Installation", id="btn-install", variant="success")

    def on_mount(self) -> None:
        text = format_install_summary(self.installer_app.config, self.installer_app.catalog)
        self.query_one("#summary-text", Static).update(text)

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
# Screen 8: Minimalist Installation Progress View
# ---------------------------------------------------------------------------


class InstallProgressScreen(Screen[None]):
    """Minimalist progress bar + step checklist view with toggleable log drawer."""

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

    @property
    def installer_app(self) -> MyArchInstallApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        disk = self.installer_app.config.target_disk
        disk_label = disk.path if disk else "/dev/unknown"
        with Vertical(id="window-frame"):
            with Horizontal(id="window-header"):
                yield Static(
                    f"Installing Arch Linux  |  {disk_label} (ext4 / linux-zen)",
                    id="header-title",
                )
                yield Static("00:00", id="header-badges")

            with VerticalScroll(id="progress-body"):
                yield Static("Overall Progress", classes="progress-section-title")
                yield ProgressBar(total=100, show_eta=False, id="pb-overall")

                yield Static("Preparing installation...", id="task-label", classes="progress-section-title")
                yield ProgressBar(total=100, show_eta=False, id="pb-task")

                yield Static(self._render_steps(), id="steps-box")
                yield Static("", id="error-banner")
                yield Log(id="log-drawer", max_lines=200)

            with Horizontal(id="window-footer"):
                yield Static("L: Toggle Details (Hidden)   Log: /tmp/my-arch-install.log", id="footer-keys")
                with Horizontal(id="footer-actions"):
                    yield Button("Details (L)", id="btn-toggle-log")
                    yield Button("Exit", id="btn-exit", disabled=True)
                    yield Button("Reboot", id="btn-reboot", variant="success", disabled=True)

    def on_mount(self) -> None:
        self._start_time = time.monotonic()
        self._timer = self.set_interval(1.0, self._tick_elapsed)
        self._run_installation_worker()

    def _tick_elapsed(self) -> None:
        if self._finished:
            return
        elapsed = int(time.monotonic() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        self.query_one("#header-badges", Static).update(f"{mins:02d}:{secs:02d}")

    @staticmethod
    def _render_step_line(state: StepState, title: str) -> str:
        glyph = {
            StepState.PENDING: "[ ]",
            StepState.RUNNING: "[>]",
            StepState.DONE: "[x]",
            StepState.FAILED: "[!]",
        }[state]
        return f"  {glyph} {title}"

    def _render_steps(self) -> str:
        lines = ["Steps:"]
        for state, title in zip(self._step_states, STEP_TITLES):
            lines.append(self._render_step_line(state, title))
        return "\n".join(lines)

    def action_toggle_details(self) -> None:
        drawer = self.query_one("#log-drawer", Log)
        drawer.toggle_class("visible")
        visible = drawer.has_class("visible")
        state_word = "Visible" if visible else "Hidden"
        self.query_one("#footer-keys", Static).update(
            f"L: Toggle Details ({state_word})   Log: {self.installer_app.runner.log_path}"
        )

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
        self.query_one("#pb-overall", ProgressBar).update(total=100, progress=event.overall_percent)
        self.query_one("#task-label", Static).update(
            f"Step {event.step_index + 1}/{event.total_steps}: {event.task_label}"
        )
        pb_task = self.query_one("#pb-task", ProgressBar)
        if event.task_percent is None:
            pb_task.update(total=None, progress=0)
        else:
            pb_task.update(total=100, progress=event.task_percent)
        if event.log_line:
            self.query_one("#log-drawer", Log).write_line(event.log_line)

    def _on_install_success(self) -> None:
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
        self.query_one("#pb-overall", ProgressBar).update(total=100, progress=100)
        self.query_one("#pb-task", ProgressBar).update(total=100, progress=100)
        lbl = self.query_one("#task-label", Static)
        lbl.update("Installation completed cleanly.")
        lbl.add_class("ok-text")
        self.query_one("#btn-exit", Button).disabled = False
        self.query_one("#btn-reboot", Button).disabled = False

    def _on_install_failure(self, error_text: str) -> None:
        self._finished = True
        self._failed = True
        if self._timer is not None:
            self._timer.stop()
        lbl = self.query_one("#task-label", Static)
        lbl.update("Installation stopped due to an error.")
        lbl.add_class("error-text")
        banner = self.query_one("#error-banner", Static)
        banner.update(
            f"Installation Error:\n{error_text}\n\n"
            f"Full log saved to: {self.installer_app.runner.log_path}"
        )
        banner.add_class("visible")
        drawer = self.query_one("#log-drawer", Log)
        if not drawer.has_class("visible"):
            drawer.add_class("visible")
        self.query_one("#btn-exit", Button).disabled = False

    @on(Button.Pressed, "#btn-exit")
    def _on_exit(self) -> None:
        self.app.exit()

    @on(Button.Pressed, "#btn-reboot")
    def _on_reboot(self) -> None:
        if not self.installer_app.dry_run:
            subprocess.run(["systemctl", "reboot"], check=False)
        self.app.exit()
