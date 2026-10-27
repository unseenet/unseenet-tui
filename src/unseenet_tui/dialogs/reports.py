"""Dialogs for spam reports (UN-RFC 0045, unseenet#122, #11): the issuers you follow,
becoming an issuer, publishing a report - and reporting a post, with why, a note in
Markdown and screenshots (#74, unseenet-gui#70).

Part of the terminal client, split by area (#20)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Select, TextArea

from unseenet.core.reports import REASONS

from ..emoji import EmojiTextArea
from .base import Dialog

from unseenet.core.reports import REASON_LABELS as REASON_TEXT  # noqa: E402 - one list (unseenet#659)
MARKDOWN_HINT = "Markdown: *emphasis*, **strong**, - lists, > quotes, links."


class ReportDialog(Dialog):
    """Report a post to the people who can act on it (#74): why, who gets it, a note in
    Markdown and screenshots, sent encrypted to each. Dismissed with ``(chosen, note,
    anonymous, reason, files)`` - files as paths."""

    def __init__(self, post: str, targets: list[tuple[str, str, str]], reasons: dict[str, str]):
        super().__init__()
        self.post, self.targets, self.reasons = post, targets, reasons

    def compose(self) -> ComposeResult:
        """The post in short, why, who gets it, the note, screenshots, Report."""
        with Vertical(classes="dialog report"):
            yield Label("Report a post", classes="title")
            yield Label(self.post, classes="hint", markup=False)
            with VerticalScroll():
                yield Label("What is wrong with it?")
                yield Select([(label, key) for key, label in self.reasons.items()], id="reason",
                             prompt="Choose (optional)", allow_blank=True)
                yield Label("Send it to")
                for i, (who, _, _) in enumerate(self.targets):
                    yield Checkbox(who, value=True, id=f"t{i}")
                yield Label("Tell them more (optional)")
                yield EmojiTextArea("", id="note", soft_wrap=True)
                yield Label(MARKDOWN_HINT, classes="hint")
                yield Input(placeholder="Screenshots or PDFs: paths, separated by commas, "
                                        "e.g. ~/skaerm.png, ~/anmeldelse.pdf",
                            id="files")
                yield Checkbox("Anonymous - they cannot see who reported it or answer you",
                               value=False, id="anon")
                yield Label("For posts that break the group's rules or the law. In an emergency, "
                            "contact the police: nobody on Unseenet can see who anyone is. "
                            "Screenshots are saved again without where and when they were taken.",
                            classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Report", variant="error", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        chosen = [t for i, t in enumerate(self.targets) if self.query_one(f"#t{i}", Checkbox).value]
        if not chosen:
            self.app.notify("Choose at least one", severity="warning")
            return
        reason = self.query_one("#reason", Select).value
        files = [p.strip() for p in self.query_one("#files", Input).value.split(",") if p.strip()]
        self.dismiss((chosen, self.query_one("#note", TextArea).text,
                      self.query_one("#anon", Checkbox).value,
                      reason if isinstance(reason, str) else "", files))


class ChooseLineDialog(Dialog):
    """In a chat, where no line is selected: choose the line to report. Dismissed with
    its Message-ID."""

    def __init__(self, lines: list[tuple[str, str, str]]):
        super().__init__()
        self.lines = lines                      # (Message-ID, label, its text)

    def compose(self) -> ComposeResult:
        """The latest lines by others, newest first, with the one chosen shown whole."""
        with Vertical(classes="dialog list"):
            yield Label("Report a chat line", classes="title")
            yield Label("Choose the line - the newest by others first.", classes="hint")
            yield Select([(label, mid) for mid, label, _ in self.lines], prompt="Choose a line", id="which")
            yield Label("", id="preview", classes="details", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Report…", variant="error", id="ok")

    @on(Select.Changed, "#which")
    def _show(self, event: Select.Changed) -> None:
        self.query_one("#preview", Label).update(next((t for m, _, t in self.lines if m == event.value), ""))

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        which = self.query_one("#which", Select).value
        if isinstance(which, str):
            self.dismiss(which)


class IssuersDialog(Dialog):
    """The spam report issuers that hide posts for you: those your hierarchies name (on or
    off) and your own, and following one by its key. Dismissed with ``(changes, key,
    label)``: changes ``{key: on}`` for the boxes changed, and a key to follow or ""."""

    def __init__(self, issuers: list[dict], label=str):
        super().__init__()
        self.issuers, self.label = issuers, label          # label: a Group ID as shown

    def compose(self) -> ComposeResult:
        """A box for each issuer, with where it is named; then a key to follow."""
        with Vertical(classes="dialog issuers"):
            yield Label("Spam reports", classes="title")
            yield Label("Issuers publish reports of spam. Their reports hide posts for you only "
                        "while you follow them, and a hidden post always says who hid it. Your "
                        "own posts and your contacts' are never hidden.", classes="hint")
            with VerticalScroll():
                if not self.issuers:
                    yield Label("You follow no issuers, and the hierarchies of your groups name none.",
                                classes="hint")
                for i, it in enumerate(self.issuers):
                    # named by hierarchies' maintainers and groups' owners (0045, 7.1, 7.5)
                    named = ([h + ".*" for h in it["hierarchies"]]
                             + [self.label(g) for g in it.get("groups", [])])
                    where = ("named for " + ", ".join(named)
                             + (", and followed by you everywhere" if it["yours"] else "")
                             if named else "followed by you, in every group")
                    yield Checkbox(f"{it['label']}  -  {where}", value=it["on"], id=f"i{i}")
                yield Label("Follow an issuer")
                yield Input(placeholder="Its key: ed25519:...", id="key")
                yield Input(placeholder="What to call it (optional)", id="label")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        changes = {it["key"]: self.query_one(f"#i{i}", Checkbox).value
                   for i, it in enumerate(self.issuers)
                   if self.query_one(f"#i{i}", Checkbox).value != it["on"]}
        self.dismiss((changes, self.query_one("#key", Input).value.strip(),
                      self.query_one("#label", Input).value.strip()))


class StartIssuerDialog(Dialog):
    """Becoming a spam report issuer: what you report, where reports sent to you land
    (your inbox or a team inbox you read), where to object. Dismissed with
    ``(policy, contact)``."""

    def __init__(self, teams: list[dict] | None = None):
        super().__init__()
        self.teams = teams or []                        # team inboxes you read

    def compose(self) -> ComposeResult:
        """What you report and how, where reports land, and where to object."""
        with Vertical(classes="dialog"):
            yield Label("Become a spam report issuer", classes="title")
            yield Label("Your reports are public and signed with your key. They hide posts only "
                        "for those who choose to follow you.", classes="hint")
            yield Label("What you report, and how")
            yield TextArea("", id="policy", soft_wrap=True)
            yield Select([("Your own inbox (Private posts)", "")]
                         + [(f"{t['name']} - a team inbox you read", t["group"]) for t in self.teams],
                         value="", allow_blank=False, id="land")
            yield Label("Where reports sent to you land. A team inbox lets others read and answer "
                        "them with you; it is also where people object to your reports.", classes="hint")
            yield Input(placeholder="Or where to object, if not there: a web address (optional)", id="contact")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Start", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        land = self.query_one("#land", Select).value
        self.dismiss((self.query_one("#policy", TextArea).text.strip(),
                      (land if isinstance(land, str) and land else "")
                      or self.query_one("#contact", Input).value.strip()))


class PublishReportDialog(Dialog):
    """Publish a report of a post - and, if chosen, its author's key for up to a year
    (UN-RFC 0045 section 4). Dismissed with ``(reason, with_key, days, note)``."""

    def __init__(self, post: str, has_key: bool, reason: str = "spam"):
        super().__init__()
        self.post, self.has_key, self.reason = post, has_key, reason

    def compose(self) -> ComposeResult:
        """Why, the author's key too, and a note for people."""
        with Vertical(classes="dialog"):
            yield Label("Publish a spam report", classes="title")
            yield Label(self.post, classes="hint", markup=False)
            yield Select([(REASON_TEXT.get(r, r), r) for r in REASONS], value=self.reason,
                         allow_blank=False, id="reason")
            if self.has_key:
                yield Checkbox("Also its author: everything they post", value=False, id="key")
                yield Input("365", placeholder="for how many days (at most 365)", id="days", type="integer")
            yield Input(placeholder="A note for people (optional, 200 characters)", id="note",
                        max_length=200)
            yield Label("Your report is public and signed by you; whoever told you is never named in it.",
                        classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Publish", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        with_key = self.has_key and self.query_one("#key", Checkbox).value
        try:
            days = int(self.query_one("#days", Input).value or 365) if self.has_key else 365
        except ValueError:
            days = 365
        self.dismiss((str(self.query_one("#reason", Select).value), with_key,
                      max(1, min(days, 365)), self.query_one("#note", Input).value.strip()))
