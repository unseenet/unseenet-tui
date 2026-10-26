"""Your own rules in the terminal client (UN-RFC 0051, #615): the editor - your rules as
text, checked as you type with the line and what is wrong, and whether they are used -
and the suggestion shown when you have none, never applied unless you take it."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Label, Static, TextArea

from unseenet.client import scoring

from .dialogs.base import Dialog

# The suggestion shown in the editor when you have no rules - the core's, the same in
# every client (UN-RFC 0051 section 3): yours only when you save it.
EXAMPLE = scoring.SUGGESTION


def problems_text(problems) -> str:
    """The lines that would not be used, one a line; "" when there are none."""
    return "\n".join(f"line {p.line}: {p.message}" for p in problems)


class RulesDialog(Dialog):
    """Edit your rules: returns {"text", "on"} to keep, or None."""

    def __init__(self, text: str, on_: bool, check):
        super().__init__()
        self.text, self.on_, self.check = text, on_, check

    def compose(self) -> ComposeResult:
        """The rules, what is wrong in them as you type, whether they are used, Save."""
        with Vertical(classes="dialog compose"):
            yield Label("Your rules - for your feed and your groups", classes="title")
            yield Label("Only you see them; they run here. Ctrl+S saves, F1 or Help: the rules in short." +
                        ("" if self.text else " This is a suggestion: it is yours only if you save it."),
                        classes="hint")
            yield TextArea(self.text or EXAMPLE, id="rules", soft_wrap=False, show_line_numbers=True)
            yield Static("", id="rules-check", markup=False)
            yield Checkbox("Use my rules (off keeps them, to turn on again)", value=self.on_, id="rules-on")
            with Horizontal(classes="buttons"):
                yield Button("Help", id="help")
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    def on_mount(self) -> None:
        """Size the editor, check what is there, the focus in it."""
        self.query_one("#rules", TextArea).styles.height = 16
        self._check()
        self.query_one("#rules").focus()

    def _check(self) -> None:
        rs = self.check(self.query_one("#rules", TextArea).text)
        text = problems_text(rs.problems)
        self.query_one("#rules-check", Static).update(
            text or f"{len(rs.rules)} rule(s), all understood.")

    @on(TextArea.Changed, "#rules")
    def _changed(self) -> None:
        self._check()

    BINDINGS = Dialog.BINDINGS + [("ctrl+s", "save", "Save"), ("f1", "help", "Help")]

    def action_help(self) -> None:
        """F1: the language in short - the core's help, the same in every client."""
        from .dialogs.base import InfoDialog
        self.app.push_screen(InfoDialog("Your rules - in short", scoring.HELP))

    @on(Button.Pressed, "#help")
    def _help(self) -> None:
        self.action_help()

    def action_save(self) -> None:
        """Ctrl+S: keep the rules and whether they are used."""
        self.dismiss({"text": self.query_one("#rules", TextArea).text,
                      "on": self.query_one("#rules-on", Checkbox).value})

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.action_save()
