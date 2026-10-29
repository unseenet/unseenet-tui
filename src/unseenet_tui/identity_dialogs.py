"""Recovery words and copying to another device (UN-RFC 0050, unseenet-tui#15): the dialogs.

The protocol and the vault work are in the core (``Session.recovery_words``,
``restore_identity``, ``start_copy``, ``receive_copy``, ...); these are only the screens.
TUI specifics from the issue: the QR code is drawn with Unicode half blocks, receiving is
by typed code (a terminal has no camera), there is no PIN, and on the bootable client the
words are only shown - never saved to a file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static, TextArea

from unseenet.core import recovery as rc

NOT_RESTORED = ("Closed groups do not come back with recovery words: their owners must "
                "invite you again.")
CLOUD_HINTS = ("dropbox", "onedrive", "icloud", "google drive", "googledrive", "mega",
               "nextcloud", "owncloud", "pcloud", "sync.com", "box sync")


def on_bootable() -> bool:
    """The bootable client: no saving of the words, only showing them (UN-RFC 0050 4.3)."""
    return (os.environ.get("UNSEENET_BOOTABLE") == "1"
            or Path("/opt/unseenet/bin/unseenet-tui").exists())


def qr_text(data: str) -> Text:
    """A QR code in Unicode half blocks, dark on light with the quiet zone, so a phone
    can scan it from the terminal whatever its theme."""
    import segno
    qr = segno.make(data, error="m", boost_error=False)
    rows = [list(r) for r in qr.matrix]
    size, quiet = len(rows), 4             # the quiet zone the standard asks for (gui#247)
    full = [[0] * (size + 2 * quiet) for _ in range(quiet)]
    full += [[0] * quiet + r + [0] * quiet for r in rows]
    full += [[0] * (size + 2 * quiet) for _ in range(quiet + 1)]
    out = Text()
    for y in range(0, len(full) - 1, 2):
        line = ""
        for top, bottom in zip(full[y], full[y + 1]):
            line += {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}[(bool(top), bool(bottom))]
        out.append(line, style="black on white")
        out.append("\n")
    return out


def words_grid(words: list[str]) -> str:
    """The 24 words numbered, in two columns of twelve - as they are written down."""
    return "\n".join(f"{i:2}. {words[i - 1]:<10}   {i + 12:2}. {words[i + 11]}" for i in range(1, 13))


def same_medium(a: Path, b: Path) -> bool:
    """True when two paths are on the same disk or stick (the words never go next to the
    vault, UN-RFC 0050 section 4.3)."""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def looks_like_cloud(path: Path) -> bool:
    """Whether a folder looks synchronised to a cloud service, where the recovery words must
    not be saved."""
    low = str(path).lower()
    return any(h in low for h in CLOUD_HINTS)




def _dialog_base():
    from .app import Dialog            # the app's base: Esc and #cancel close it
    return Dialog


class ShowWordsDialog(_dialog_base()):
    """The 24 words, what they do and do not bring back, and then a check of three of them.
    Returns "written" (checked), "later" (skipped) or None (closed)."""

    def __init__(self, name: str, fingerprint: str, words: list[str], first_time: bool = False,
                 vault_path: Path | None = None):
        super().__init__()
        self.ident, self.fingerprint, self.words = name, fingerprint, words
        self.first_time, self.vault_path = first_time, vault_path
        self.positions = rc.check_positions()

    def compose(self) -> ComposeResult:
        """The 24 recovery words in a grid, what they are and are not, and save, print or
        Done (UN-RFC 0050 4.2)."""
        with VerticalScroll(classes="dialog"):
            yield Label("Your recovery words" + (" - write them down now" if self.first_time else ""),
                        classes="title")
            yield Label(f"{self.ident}  ·  {self.fingerprint}", classes="hint", markup=False)
            yield Static(words_grid(self.words), id="words", markup=False)
            yield Label("These 24 words bring this identity back on any device, with your "
                        "groups, contacts and settings from the network. Whoever has them IS "
                        "you: write them on paper and keep it safe, away from the stick.",
                        classes="hint")
            yield Label(NOT_RESTORED, classes="error")
            yield Label(rc.NOT_A_WALLET, classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Later" if self.first_time else "Close", id="later")
                if not on_bootable():
                    yield Button("Save as file…", id="save")
                yield Button("I wrote them down", variant="primary", id="ok")

    @on(Button.Pressed, "#later")
    def _later(self) -> None:
        self.dismiss("later")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        def checked(ok) -> None:
            if ok:
                self.dismiss("written")
        self.app.push_screen(CheckWordsDialog(self.words, self.positions), checked)

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        self.app.push_screen(SaveWordsDialog(self.ident, self.fingerprint, self.words,
                                             self.vault_path))


class CheckWordsDialog(_dialog_base()):
    """Three of the words back, to be sure they were written down right (4.2)."""

    def __init__(self, words: list[str], positions: list[int]):
        super().__init__()
        self.words, self.positions = words, positions

    def compose(self) -> ComposeResult:
        """Three of the words to type again, so you know you wrote them down right."""
        with Vertical(classes="dialog"):
            yield Label("Check your recovery words", classes="title")
            yield Label("Type these words from what you wrote down (four letters are enough).",
                        classes="hint")
            for p in self.positions:
                yield Input(placeholder=f"Word {p}", id=f"w{p}")
            with Horizontal(classes="buttons"):
                yield Button("Back", id="cancel")
                yield Button("Check", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        wrong = []
        for p in self.positions:
            typed = self.query_one(f"#w{p}", Input).value.strip().lower()
            want = self.words[p - 1]
            if not typed or not (typed == want or (len(typed) >= 4 and want.startswith(typed[:4]))):
                wrong.append(str(p))
        if wrong:
            self.app.notify(f"Word {', '.join(wrong)} does not match. Look at what you wrote.",
                            severity="error")
            return
        self.dismiss(True)


class SaveWordsDialog(_dialog_base()):
    """Save as file, as a deliberate choice behind the warnings of UN-RFC 0050 4.3."""

    def __init__(self, name: str, fingerprint: str, words: list[str], vault_path: Path | None):
        super().__init__()
        self.ident, self.fingerprint, self.words, self.vault_path = name, fingerprint, words, vault_path

    def compose(self) -> ComposeResult:
        """Where to save the words as a file, after the warning, and Save."""
        with Vertical(classes="dialog"):
            yield Label("Save the recovery words as a file", classes="title")
            yield Label("Whoever has this file IS you: they can read your private posts and post "
                        "in your name. Paper is safer. Never put it on the same stick or disk as "
                        "the vault, and not in a folder synchronised to the cloud.", classes="error")
            yield Input(placeholder="Where, e.g. /media/other-stick/unseenet-words.txt", id="path")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="error", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        raw = self.query_one("#path", Input).value.strip()
        if not raw:
            return
        path = Path(raw).expanduser()
        folder = path.parent if path.parent.exists() else None
        if folder is None:
            self.app.notify("That folder does not exist", severity="error")
            return
        if self.vault_path is not None and same_medium(folder, self.vault_path.resolve().parent):
            self.app.notify("That is the same disk or stick as the vault: whoever takes it would "
                            "have both. Choose another stick, or write them on paper.",
                            severity="error", timeout=10)
            return
        if looks_like_cloud(path):
            self.app.notify("That folder looks synchronised to a cloud service. Choose another.",
                            severity="error", timeout=10)
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rc.text_page(self.ident, self.fingerprint, self.words))
        except FileExistsError:
            self.app.notify("A file of that name exists already", severity="error")
            return
        except OSError as exc:
            self.app.notify(f"Could not save: {exc}", severity="error")
            return
        self.app.notify(f"Saved to {path}. Keep it safe, or delete it once written on paper.",
                        timeout=12)
        self.dismiss(True)


class PassphraseDialog(_dialog_base()):
    """The vault passphrase again, before the words are shown (4.2)."""

    def __init__(self, error: str = ""):
        super().__init__()
        self.error = error

    def compose(self) -> ComposeResult:
        """The vault's passphrase again, before the words are shown."""
        with Vertical(classes="dialog"):
            yield Label("Show recovery words", classes="title")
            yield Label("Type the vault passphrase again: the words are your key.", classes="hint")
            yield Input(placeholder="Passphrase", password=True, id="pw")
            if self.error:
                yield Label(self.error, classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Show", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#pw", Input).value)


class RestoreDialog(_dialog_base()):
    """Restore from recovery words: the words, checked as they are typed, and a display
    name. Returns {"words", "display"} or None."""

    def compose(self) -> ComposeResult:
        """Restoring from the words: the words, checked as typed, and a display name."""
        with Vertical(classes="dialog"):
            yield Label("Restore from recovery words", classes="title")
            yield Label("Type your 24 words in order, separated by spaces or lines. Four letters "
                        "of each are enough.", classes="hint")
            yield TextArea(id="words")
            yield Label("", id="words-state", classes="hint")
            yield Input(placeholder="Display name, e.g. Andreas (optional)", id="display")
            yield Label(NOT_RESTORED, classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Restore", variant="primary", id="ok")

    @on(TextArea.Changed, "#words")
    def _typed(self, event: TextArea.Changed) -> None:
        text = event.text_area.text
        n = len(rc.split_words(text))
        state = self.query_one("#words-state", Label)
        if n < rc.WORDS:
            state.update(f"{n} of {rc.WORDS} words")
            return
        try:
            rc.seed_from_words(text)
            state.update("The words fit together.")
        except rc.RecoveryError as exc:
            state.update(str(exc))

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        text = self.query_one("#words", TextArea).text
        try:
            rc.seed_from_words(text)
        except rc.RecoveryError as exc:
            self.app.notify(str(exc), severity="error", timeout=10)
            return
        self.dismiss({"words": text, "display": self.query_one("#display", Input).value.strip()})


class ClosedGroupsDialog(_dialog_base()):
    """After a restore: closed groups that did not come back, and asking their owners.
    Returns the list of group IDs to ask about, or None."""

    def __init__(self, groups: list[dict], state: str):
        super().__init__()
        self.groups, self.state = groups, state

    def compose(self) -> ComposeResult:
        """After restoring: what came back from the network, and the closed groups to ask to
        be invited to again."""
        lead = {"read": "Your groups, contacts and settings came back from the network.",
                "none": "Nothing was found in the network for this identity: you start with the "
                        "default groups.",
                "unreachable": "The network could not be reached; your groups come back once it "
                               "can be."}.get(self.state, "")
        with VerticalScroll(classes="dialog"):
            yield Label("Your identity is back", classes="title")
            yield Label(lead, classes="hint")
            yield Label(NOT_RESTORED, classes="error")
            for i, g in enumerate(self.groups):
                yield Checkbox(g["label"], value=not g.get("mine"), id=f"g{i}",
                               disabled=bool(g.get("mine")))
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                if self.groups:
                    yield Button("Ask the owners to invite me", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        chosen = [g["group"] for i, g in enumerate(self.groups)
                  if self.query_one(f"#g{i}", Checkbox).value and not g.get("mine")]
        self.dismiss(chosen)


class CopyChoiceDialog(_dialog_base()):
    """Copy to another device: which identities, and whether closed groups' secrets go
    too. Returns {"names", "group_secrets"} or None."""

    def __init__(self, identities: list[tuple[str, str]]):
        super().__init__()
        self.identities = identities

    def compose(self) -> ComposeResult:
        """First start: a new identity, restore from words, or copy from another device."""
        with Vertical(classes="dialog"):
            yield Label("Copy to another device", classes="title")
            yield Label("The identities go straight to the other device over Tor, after you "
                        "check two words on both. A phone is lost more often than a stick: "
                        "leave out an identity that needs to stay hidden.", classes="hint")
            for i, (name, display) in enumerate(self.identities):
                yield Checkbox(f"{display}  ({name})", value=True, id=f"i{i}")
            yield Checkbox("Also the keys of closed groups (without them the other device cannot "
                           "read them)", value=True, id="secrets")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Show the code", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        names = [n for i, (n, _) in enumerate(self.identities) if self.query_one(f"#i{i}", Checkbox).value]
        if not names:
            self.app.notify("Choose at least one identity", severity="error")
            return
        self.dismiss({"names": names, "group_secrets": self.query_one("#secrets", Checkbox).value})


class CopyOfferDialog(_dialog_base()):
    """The code and QR code with a countdown; then the check words to confirm. The app
    sets ``check`` when the new device has connected. Returns True (words match), False
    (they do not) or None (cancelled)."""

    def __init__(self, code: str, qr: str, expires: float):
        super().__init__()
        self.code, self.qr, self.expires = code, qr, expires
        self.check: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        """Copying to another device: the code and QR code, the time left, and the check
        words when it connects."""
        with VerticalScroll(classes="dialog"):
            yield Label("Copy to another device", classes="title")
            yield Label("On the other device choose 'Get identity from another device', then "
                        "scan this or type the code:", classes="hint")
            yield Static(qr_text(self.qr), id="qr")
            yield Static(Text(self.code, style="bold"), id="code")
            yield Label("", id="countdown", classes="hint")
            yield Static("", id="check", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("They do not match", id="nomatch", disabled=True)
                yield Button("The words match", variant="primary", id="ok", disabled=True)

    def on_mount(self) -> None:
        """Start the countdown of the code's ten minutes."""
        self._tick()
        self.set_interval(1, self._tick)

    def _tick(self) -> None:
        left = max(0, int(self.expires - time.time()))
        self.query_one("#countdown", Label).update(f"The code works once, for {left // 60}:{left % 60:02d} more.")

    def show_check(self, words: tuple[str, str]) -> None:
        """Show the check words from the other device, to compare before Confirm."""
        self.check = words
        self.query_one("#qr").display = False
        self.query_one("#check", Static).update(
            f"Check words:   {words[0]}   {words[1]}\n\n"
            "Does the other device show the same two words? Nothing is sent until you say so.")
        self.query_one("#ok", Button).disabled = False
        self.query_one("#nomatch", Button).disabled = False
        self.query_one("#ok", Button).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#nomatch")
    def _no(self) -> None:
        self.dismiss(False)


class CopyCodeDialog(_dialog_base()):
    """Get identity from another device: the code it shows (or the QR code's text)."""

    def compose(self) -> ComposeResult:
        """Getting a copy: the code the other device shows."""
        with Vertical(classes="dialog"):
            yield Label("Get identity from another device", classes="title")
            yield Label("On the other device choose 'Copy to another device' and type the code it "
                        "shows, such as 7-guitar-tunnel.", classes="hint")
            yield Input(placeholder="Code", id="code")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Connect", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        code = self.query_one("#code", Input).value.strip()
        if code:
            self.dismiss(code)


class CopyWaitDialog(_dialog_base()):
    """On the new device: the check words while waiting for the other device's user."""

    def __init__(self, words: tuple[str, str]):
        super().__init__()
        self.words = words

    def compose(self) -> ComposeResult:
        """Waiting for the other device: the check words to compare, and Cancel."""
        with Vertical(classes="dialog"):
            yield Label("Check words", classes="title")
            yield Static(Text(f"{self.words[0]}   {self.words[1]}", style="bold"))
            yield Label("Confirm on the other device that it shows the same two words. "
                        "Waiting for it ...", classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
