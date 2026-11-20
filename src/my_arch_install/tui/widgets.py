"""Custom reusable widgets for the my-arch-install TUI."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import OptionList


class PackageOptionList(OptionList):
    """OptionList where Space toggles/selects items without interfering with Inputs."""

    BINDINGS = [
        Binding("space", "select", "Toggle", show=False),
    ]
