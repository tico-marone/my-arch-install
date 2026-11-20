"""Textual Application container for my-arch-install."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from textual.app import App
from textual.scrollbar import ScrollBar
from textual.theme import Theme

from .. import __version__
from ..catalog import PackageCatalog, load_catalog
from ..models import InstallConfig
from ..runner import CommandRunner
from ..system_probe import is_linux_tty
from .screens import PreflightScreen
from .widgets import CleanScrollBarRender


ThemePreference = Literal["auto", "ansi-dark", "textual-dark"]


TTY_CLEAN_THEME = Theme(
    name="tty-clean",
    primary="#2655d8",
    secondary="#1a3fa8",
    accent="#18d4d4",
    foreground="#e5e5e5",
    background="#000000",
    surface="#000000",
    panel="#000000",
    success="#22b83a",
    warning="#d9b41a",
    error="#dc3838",
    dark=True,
)

MODERN_DARK_THEME = Theme(
    name="modern-dark",
    primary="#2655d8",
    secondary="#1a3fa8",
    accent="#18d4d4",
    foreground="#e5e5e5",
    background="#0b0f17",
    surface="#111827",
    panel="#161f30",
    success="#22b83a",
    warning="#d9b41a",
    error="#dc3838",
    dark=True,
)


class MyArchInstallApp(App[None]):
    """Centered responsive grid TUI installer for Arch Linux."""

    TITLE = f"my-arch-install v{__version__}"
    CSS_PATH = Path(__file__).with_name("styles.tcss")

    def __init__(
        self,
        *,
        dry_run: bool = False,
        log_path: Path = Path("/tmp/my-arch-install.log"),
        theme_preference: ThemePreference = "auto",
        catalog: PackageCatalog | None = None,
    ) -> None:
        super().__init__()
        ScrollBar.renderer = CleanScrollBarRender
        self.dry_run = dry_run
        self.theme_preference = theme_preference
        self.catalog = catalog or load_catalog()
        self.runner = CommandRunner(dry_run=dry_run, log_path=log_path)
        self.config = InstallConfig(
            selected_curated_packages=set(self.catalog.default_selected_names),
        )

    def on_mount(self) -> None:
        self.register_theme(TTY_CLEAN_THEME)
        self.register_theme(MODERN_DARK_THEME)
        self._apply_theme()
        self.push_screen(PreflightScreen())

    def _apply_theme(self) -> None:
        if self.theme_preference == "ansi-dark":
            self.theme = "tty-clean"
        elif self.theme_preference == "textual-dark":
            self.theme = "modern-dark"
        else:
            self.theme = "tty-clean" if is_linux_tty() else "modern-dark"
