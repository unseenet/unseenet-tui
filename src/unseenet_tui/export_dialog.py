"""Export your data (#14, on the core's export, unseenet#577 / UN-RFC 0049 section 7):
what an export holds and does not, private posts and chats only when chosen - then a
passphrase, twice - and where it is saved (Downloads, else the home folder)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label

from .identity_dialogs import _dialog_base

HOLDS = ("A zip with your own posts, comments, chat lines and pages exactly as you signed "
         "them - anyone can check them against your key - with their files, your profile "
         "and the groups you follow.")
NEVER = ("Never your keys: an export is not a backup of your identity (your recovery words "
         "are). It holds what the nodes still carry and what this computer kept, not "
         "everything you ever wrote.")


def default_path(fingerprint: str, home: Path | None = None) -> Path:
    """Where an export is saved: Downloads, else the home folder, with the fingerprint and
    the day in its name."""
    home = home or Path.home()
    folder = home / "Downloads"
    folder = folder if folder.is_dir() else home
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return folder / f"unseenet-export-{fingerprint[:8]}-{day}.zip"


class ExportDialog(_dialog_base()):
    """Returns {"private": bool, "passphrase": str | None} or None."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        """Export your data: what it holds and never holds, private messages with a passphrase
        twice, Export."""
        with Vertical(classes="dialog"):
            yield Label("Export your data", classes="title")
            yield Label(HOLDS)
            yield Label(NEVER, classes="hint")
            yield Label(f"Saved as {self.path}", classes="hint")
            yield Checkbox("Include private posts and chats (both sides)", id="private")
            yield Label("Then the whole file is encrypted with a passphrase - without it, it "
                        "cannot be opened. Write it down.", classes="hint", id="why")
            yield Input(placeholder="Passphrase for the export", password=True, id="pw")
            yield Input(placeholder="Again", password=True, id="pw2")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Export", variant="primary", id="ok")

    def on_mount(self) -> None:
        """The passphrase fields hidden until private messages are chosen."""
        self._show_passphrase(False)

    def _show_passphrase(self, on: bool) -> None:
        for wid in ("#why", "#pw", "#pw2"):
            self.query_one(wid).display = on

    @on(Checkbox.Changed, "#private")
    def _private(self, event: Checkbox.Changed) -> None:
        self._show_passphrase(event.value)

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        private = self.query_one("#private", Checkbox).value
        if not private:
            self.dismiss({"private": False, "passphrase": None})
            return
        a, b = self.query_one("#pw", Input).value, self.query_one("#pw2", Input).value
        error = ("The passphrases differ." if a != b else
                 "Use a passphrase of at least 8 characters." if len(a) < 8 else "")
        if error:
            self.query_one("#error", Label).update(error)
            return
        self.dismiss({"private": True, "passphrase": a})
