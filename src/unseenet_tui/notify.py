"""Notifications when something lands in For you (unseenet-tui#4), the terminal way.

What is new comes from the core (``Session.notifications``), so the terminal and the
graphical client tell alike, and the choice - on, off or ask once; content shown or
not - is the same setting in both. This only tells: an OSC 9 / OSC 777 desktop
notification in terminals that show them as system notifications, else the terminal
bell, and always a line inside the client. Content is hidden by default
("Unseenet: 2 new in For you"): notifications show on lock screens and stay in
histories. Never on the bootable client, and only while the client runs - no push.
"""

from __future__ import annotations

import os

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label

from .identity_dialogs import on_bootable

FOR_YOU = "for-you:"


def desktop_terminal(env=os.environ) -> bool:
    """True in terminals known to show OSC 9 / OSC 777 as system notifications."""
    prog = env.get("TERM_PROGRAM", "")
    return (prog in ("iTerm.app", "WezTerm", "ghostty", "vscode")
            or env.get("TERM", "").startswith(("xterm-kitty", "foot", "xterm-ghostty"))
            or bool(env.get("KITTY_WINDOW_ID") or env.get("WT_SESSION")
                    or env.get("KONSOLE_VERSION") or env.get("WEZTERM_PANE")))


def _clean(text: str) -> str:
    """No control characters in an escape sequence (a hostile post could end it)."""
    return "".join(c for c in text if c.isprintable() and c not in ";")[:200]


def sequences(text: str) -> str:
    """OSC 9 (iTerm2, Windows Terminal, kitty, WezTerm...) and OSC 777 (foot, Konsole,
    urxvt...) for one notification; a terminal ignores the one it does not know."""
    t = _clean(text)
    return f"\x1b]9;{t}\x07\x1b]777;notify;Unseenet;{t}\x07"


def text_of(n: dict) -> str:
    items = n["items"]
    if n["settings"].get("content") and len(items) == 1 and items[0].get("text"):
        return f"{items[0]['who']}: {items[0]['text']}"
    return f"{len(items)} new in For you"


def on_screen(item: dict, current: str | None, focused: bool = True) -> bool:
    return focused and current is not None and (
        current in (item["group"], FOR_YOU)
        or (current.startswith("contact:") and item["why"] == "private"))


def _dialog_base():
    from .app import Dialog
    return Dialog


class AskDialog(_dialog_base()):
    """Asked once: "yes", "later" (not this time) or "never"."""

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Tell you about new messages?", classes="title")
            yield Label("A notification when a private message, a reply or a mention lands "
                        "in For you - only while Unseenet runs.", classes="hint")
            yield Label("It says only \"new in For you\": notifications can show on the lock "
                        "screen and stay in the system's history, so who wrote and what is "
                        "left out unless you choose otherwise (Ctrl+P).", classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Never", id="never")
                yield Button("Not now", id="cancel")
                yield Button("Yes", variant="primary", id="ok")

    CANCEL_RESULT = "later"

    @on(Button.Pressed, "#ok")
    def _yes(self) -> None:
        self.dismiss("yes")

    @on(Button.Pressed, "#never")
    def _never(self) -> None:
        self.dismiss("never")
