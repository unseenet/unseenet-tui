"""Content warnings and labels (UN-RFC 0053): what to do with each warning, the
labellers that count and where each is named, and following one (#50)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Select

from unseenet.core.tags import WARNING_LABELS, WARNINGS

from .base import Dialog

CHOICES = [("Show", "show"), ("Blur: Ctrl+P shows it", "blur"), ("Hide: a quiet line", "hide")]


class ContentWarningsDialog(Dialog):
    """Dismissed with ``{"choices": {warning: choice}, "off": [keys], "follow", "label",
    "topics"}``, or None."""

    def __init__(self, choices: dict, labellers: list[dict]):
        super().__init__()
        self.choices, self.labellers = choices, labellers

    def compose(self) -> ComposeResult:
        """A choice per warning, the labellers with a box each, and a key to follow."""
        with Vertical(classes="dialog wide"):
            yield Label("Content warnings and labels", classes="title")
            yield Label("Labels only describe: they hide nothing for anyone who did not choose it, and "
                        "what is covered always says why. Your own posts and your contacts' never are.",
                        classes="hint")
            with VerticalScroll():
                for w in WARNINGS:
                    with Horizontal(classes="row"):
                        yield Label(f"{WARNING_LABELS[w]:<16}", markup=False)
                        yield Select(CHOICES, value=self.choices.get(w, "blur"), allow_blank=False, id=f"w-{w}")
                yield Label("Labellers", classes="title")
                if not self.labellers:
                    yield Label("None yet. A labeller you follow, or one a group or hierarchy names, appears here.",
                                classes="hint")
                for i, lab in enumerate(self.labellers):
                    where = [f"named for {h}.*" for h in lab["hierarchies"]] + \
                        [f"named for {g.rpartition('@')[0]}" for g in lab["groups"]] + \
                        (["followed by you" + (", topics too" if lab["topics"] else "")] if lab["yours"] else [])
                    yield Checkbox(f"{lab['label']}  -  {' · '.join(where)}", value=lab["on"], id=f"l{i}")
                yield Label("Follow a labeller", classes="title")
                yield Input(placeholder="Its key: ed25519:...", id="key")
                yield Input(placeholder="What to call it (optional)", id="label")
                yield Checkbox("Also its topics, for your rules - never chosen for you", id="topics")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        choices = {w: self.query_one(f"#w-{w}", Select).value for w in WARNINGS}
        off = [lab["key"] for i, lab in enumerate(self.labellers)
               if lab["on"] and not self.query_one(f"#l{i}", Checkbox).value]
        self.dismiss({"choices": choices, "off": off,
                      "follow": self.query_one("#key", Input).value.strip(),
                      "label": self.query_one("#label", Input).value.strip(),
                      "topics": self.query_one("#topics", Checkbox).value})


class AgeGuideDialog(Dialog):
    """Who reads on this device (UN-RFC 0053 6.1): dismissed with a profile's id, or None.
    Nothing of the answer is kept - only the settings it gives."""

    def __init__(self, profiles: dict, labels: dict):
        super().__init__()
        self.profiles, self.labels = profiles, labels

    def compose(self) -> ComposeResult:
        """The profiles, what each does, and what the guide cannot do."""
        with Vertical(classes="dialog wide"):
            yield Label("Who reads on this device?", classes="title")
            yield Label("This sets what is blurred and hidden. Nothing about the answer is saved or "
                        "sent anywhere - only the settings it gives, which you can change later under "
                        "Content warnings and labels.", classes="hint")
            yield Select([(f"{self.labels[k]}  -  " + ", ".join(f"{w} {c}" for w, c in v.items()), k)
                          for k, v in self.profiles.items()], prompt="Choose who reads here", id="age")
            yield Label("It can only cover what is marked. Today few posts are: authors warn about "
                        "their own, and labellers are still few. It is not a filter that finds "
                        "everything, so for a child, stay near and use groups you know.", classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Not now", id="cancel")
                yield Button("Use these settings", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        v = self.query_one("#age", Select).value
        if isinstance(v, str):
            self.dismiss(v)
        else:
            self.app.notify("Choose who reads here first", severity="warning")


class LanguageDialog(Dialog):
    """What you write your chat lines and comments in, in a group: a list of languages by
    their own names. Dismissed with a code, "" for none, or None."""

    def __init__(self, current: list[str], group: list[str], note: str):
        super().__init__()
        self.current, self.group, self.note = current, group, note

    def compose(self) -> ComposeResult:
        """The list, the suggestion and the group's first."""
        from .compose import language_options
        with Vertical(classes="dialog"):
            yield Label("What you write in here", classes="title")
            yield Label(self.note + ". Remembered for your lines in this group.", classes="hint", markup=False)
            yield Select(language_options(self.current, self.group), value=self.current[0] if self.current else "",
                         allow_blank=False, id="lang")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Use", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        v = self.query_one("#lang", Select).value
        self.dismiss(v if isinstance(v, str) else "")
