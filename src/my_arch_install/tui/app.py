"""Textual Application container for my-arch-install."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from textual.app import App

from ..catalog import PackageCatalog, load_catalog
from ..models import InstallConfig
from ..runner import CommandRunner
from ..system_probe import is_linux_tty
from .screens import PreflightScreen


ThemePreference = Literal["auto", "ansi-dark", "textual-dark"]


class MyArchInstallApp(App[None]):
    """Centered responsive grid TUI installer for Arch Linux."""

    TITLE = "my-arch-install"
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
        self.dry_run = dry_run
        self.theme_preference = theme_preference
        self.catalog = catalog or load_catalog()
        self.runner = CommandRunner(dry_run=dry_run, log_path=log_path)
        self.config = InstallConfig(
            selected_curated_packages=set(self.catalog.default_selected_names),
        )

    def on_mount(self) -> None:
        self._apply_theme()
        self.push_screen(PreflightScreen())

    def _apply_theme(self) -> None:
        desired = self.theme_preference
        if desired == "auto":
            desired = "ansi-dark" if is_linux_tty() else "textual-dark"

        available = getattr(self, "available_themes", {})
        if desired in available:
            self.theme = desired
        elif desired == "ansi-dark" and "textual-ansi" in available:
            self.theme = "textual-ansi"
