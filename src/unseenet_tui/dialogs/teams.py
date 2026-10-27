"""The owner's dialog for a team inbox (UN-RFC 0047, #13): who reads it, add a reader by
their key, and the name replies go out as.

Part of the terminal client, split by area (#20)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label

from .base import Dialog


class TeamReadersDialog(Dialog):
    """Who reads an inbox you own: a box per reader (unticked: removed), a key to add,
    the team's name. Dismissed with ``(remove, add, name)``."""

    def __init__(self, readers: list[str], team_name: str):
        super().__init__()
        self.readers, self.team_name = readers, team_name

    def compose(self) -> ComposeResult:
        """The readers, a key to add, the team's name, Save."""
        with Vertical(classes="dialog issuers"):
            yield Label("Who reads this inbox", classes="title")
            yield Label("Readers have a team key of their own, made for this inbox only. They read "
                        "it and reply as the team; your key is needed only to change who reads. "
                        "Untick a reader to remove it. At most 16.", classes="hint")
            with VerticalScroll():
                if not self.readers:
                    yield Label("Nobody reads it with a team key yet.", classes="hint")
                for i, k in enumerate(self.readers):
                    yield Checkbox(k, value=True, id=f"r{i}")
                yield Input(self.team_name, placeholder="The name replies go out as, e.g. "
                            "Example (operator)", id="name", max_length=64)
                yield Input(placeholder="Add a reader: their own key, ed25519:...", id="add")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        remove = [k for i, k in enumerate(self.readers) if not self.query_one(f"#r{i}", Checkbox).value]
        self.dismiss((remove, self.query_one("#add", Input).value.strip(),
                      self.query_one("#name", Input).value.strip()))
