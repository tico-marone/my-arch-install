"""Console-safe custom TUI widgets without missing-glyph Unicode artifacts."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.scrollbar import ScrollBarRender
from textual.widget import Widget
from textual.widgets import OptionList


class CleanScrollBarRender(ScrollBarRender):
    """Scrollbar renderer using pure colored spaces so Linux TTY never prints '#'."""

    BLANK_GLYPH = " "
    HORIZONTAL_BARS = [" "] * 8
    VERTICAL_BARS = [" "] * 8


class PackageOptionList(OptionList):
    """OptionList where Space selects/toggles items without affecting Input boxes."""

    BINDINGS = [
        Binding("space", "select", "Toggle", show=False),
    ]


class CleanToggle(Widget, can_focus=True):
    """High-contrast ASCII toggle row with an explicit bracketed container."""

    BINDINGS = [
        Binding("space", "toggle", "Toggle", show=False),
        Binding("enter", "toggle", "Toggle", show=False),
    ]

    value: reactive[bool] = reactive(True)

    class Changed(Message):
        """Posted when the toggle state changes."""

        def __init__(self, toggle: CleanToggle, value: bool) -> None:
            super().__init__()
            self.toggle = toggle
            self.value = value

    def __init__(
        self,
        label: str,
        *,
        value: bool = True,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._label = label
        self.value = value

    def render(self) -> Text:
        text = Text()
        if self.value:
            text.append("[ ON  ]", style="bold green")
        else:
            text.append("[ OFF ]", style="bold red")
        text.append(f"  {self._label}")
        return text

    def action_toggle(self) -> None:
        self.value = not self.value
        self.post_message(self.Changed(self, self.value))

    def on_click(self, event: events.Click) -> None:
        self.focus()
        self.action_toggle()


class CleanProgressBar(Widget):
    """Console-safe progress bar rendered with pure solid background cells."""

    progress: reactive[float] = reactive(0.0)
    total: reactive[float | None] = reactive(100.0)
    pulse_step: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        total: float | None = 100.0,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.total = total

    def on_mount(self) -> None:
        self.set_interval(0.15, self._advance_pulse)

    def _advance_pulse(self) -> None:
        if self.total is None:
            self.pulse_step = (self.pulse_step + 1) % 1000

    def update(self, *, total: float | None = 100.0, progress: float = 0.0) -> None:
        self.total = total
        self.progress = progress

    def render(self) -> Text:
        width = max(24, self.size.width or 48)
        inner = max(8, width - 8)
        out = Text()
        out.append("[", style="bold white")
        if self.total is None or self.total <= 0:
            seg = min(10, max(3, inner // 5))
            pos = self.pulse_step % max(1, inner - seg + 1)
            if pos > 0:
                out.append(" " * pos, style="on bright_black")
            out.append(" " * seg, style="on cyan")
            rem = inner - pos - seg
            if rem > 0:
                out.append(" " * rem, style="on bright_black")
            out.append("]  ... ", style="bold white")
            return out

        ratio = max(0.0, min(1.0, self.progress / self.total))
        filled = round(inner * ratio)
        empty = max(0, inner - filled)
        if filled > 0:
            out.append(" " * filled, style="on blue")
        if empty > 0:
            out.append(" " * empty, style="on bright_black")
        pct_int = int(round(ratio * 100))
        out.append(f"] {pct_int:3d}%", style="bold white")
        return out
