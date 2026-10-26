"""Publishing a folder as a site (#19, on the core's site import, unseenet#538): the
folder and whether pages no longer in it go, then what would be sent - pages new,
changed, unchanged, removed, and every change made on the way - and Publish."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static

from .dialogs.base import Dialog

MARKS = {"new": "+", "changed": "~", "unchanged": "=", "remove": "-", "kept": "·"}
WORDS = {"new": "new", "changed": "changed", "unchanged": "unchanged", "remove": "removed",
         "kept": "not in the folder - kept"}


def plan_text(plan: dict) -> str:
    """The plan as lines: one per page, then the problems and the changes made on the way."""
    lines = []
    for c in plan.get("changes", []):
        files = f"  ({len(c['files'])} picture{'s' if len(c['files']) != 1 else ''})" if c.get("files") else ""
        lines.append(f"{MARKS.get(c['action'], ' ')} /{c['path']}  {c['title']}  - {WORDS.get(c['action'], '')}{files}")
    if plan.get("problems"):
        lines += ["", "Nothing can be sent yet:"] + [f"  {p}" for p in plan["problems"]]
    if plan.get("notes"):
        lines += ["", f"{len(plan['notes'])} change(s) made on the way:"] + [f"  {n}" for n in plan["notes"]]
    return "\n".join(lines)


def to_send(plan: dict) -> int:
    """How many pages publishing would send: new, changed and removed ones."""
    return sum(c["action"] in ("new", "changed", "remove") for c in plan.get("changes", []))


class FolderDialog(Dialog):
    """Which folder, and whether to remove pages no longer in it. Returns
    {"folder", "prune"} or None."""

    def __init__(self, site: str):
        super().__init__()
        self.site = site

    def compose(self) -> ComposeResult:
        """The folder's path, the prune choice, Cancel and Next."""
        with Vertical(classes="dialog"):
            yield Label(f"Publish a folder as {self.site}", classes="title", markup=False)
            yield Label("A folder of Markdown (.md) and HTML files: index.md is the home page; "
                        "pictures are cleaned of their metadata. Only what changed is sent.",
                        classes="hint")
            yield Input(placeholder="The folder, e.g. ~/my-site", id="folder")
            yield Checkbox("Remove pages that are no longer in the folder", id="prune")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Next", variant="primary", id="ok")

    def on_mount(self) -> None:
        """The focus in the folder field."""
        self.query_one("#folder", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        folder = Path(self.query_one("#folder", Input).value.strip()).expanduser()
        if not folder.is_dir():
            self.query_one("#error", Label).update("That is not a folder here.")
            return
        self.dismiss({"folder": str(folder), "prune": self.query_one("#prune", Checkbox).value})


class PlanDialog(Dialog):
    """What publishing would send; Publish only when there is something and no problem.
    Returns True to publish."""

    CANCEL_RESULT = False

    def __init__(self, plan: dict):
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        """The plan, and Cancel and Publish."""
        n = to_send(self.plan)
        with Vertical(classes="dialog"):
            yield Label("What would be sent", classes="title")
            with VerticalScroll():
                yield Static(plan_text(self.plan) or "No pages in the folder.", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(f"Publish {n} page{'s' if n != 1 else ''}" if n else "Nothing to send",
                             variant="primary", id="ok", disabled=not n or bool(self.plan.get("problems")))

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(True)
