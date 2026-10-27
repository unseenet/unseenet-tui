"""Emoji in the terminal client (#6), as the graphical client has them (unseenet-gui#21),
on the core's functions (unseenet#362): completion while typing ``:ta`` in the chat box
and the post editor, and a picker in the palette (*Insert emoji*). Both insert the
``:shortcode:``, not the glyph (UN-RFC 0031 emoji)."""

from __future__ import annotations

import re

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from unseenet.core import markup

from .dialogs.base import Dialog

_TYPING = re.compile(r"(?:^|[\s(])(:[a-z0-9_+-]{2,})$")
MAX = 8


def typed_query(line: str) -> str | None:
    """The ``:ta`` being typed at the end of ``line`` (up to the cursor), or None."""
    m = _TYPING.search(line)
    return m.group(1) if m else None


def suggestions(line: str) -> list[str]:
    """The shortcodes to offer for what is typed at the end of ``line`` (at most 8)."""
    q = typed_query(line)
    return markup.find_emoji(q, MAX) if q else []


def suggestion_text(codes: list[str], chosen: int) -> str:
    """The line under the field: each emoji with its shortcode, the chosen one marked."""
    table = markup.emoji_table()
    parts = [f"{'▸' if i == chosen else ' '}{table.get(c, '')} :{c}:" for i, c in enumerate(codes)]
    return "  ".join(parts) + "   Tab takes it · Esc closes" if codes else ""


class EmojiCompletion:
    """Mixed into a TextArea: offers shortcodes while ``:ta`` is typed, in ``self.emoji_bar``
    (a Static the owner puts under it). Tab or Enter takes the chosen one, Up and Down
    choose, Esc closes; Enter does not send or break the line while they are shown."""

    emoji_bar: Static | None = None
    emoji_codes: list[str] = []
    emoji_chosen: int = 0

    def _line_to_cursor(self) -> str:
        row, col = self.cursor_location
        return self.document.get_line(row)[:col]

    def refresh_emoji(self) -> None:
        """Look at what is typed before the cursor, and show the shortcodes to offer."""
        self.emoji_codes = suggestions(self._line_to_cursor())
        self.emoji_chosen = 0
        self._draw_emoji()

    def _draw_emoji(self) -> None:
        if self.emoji_bar is not None:
            self.emoji_bar.update(suggestion_text(self.emoji_codes, self.emoji_chosen))
            self.emoji_bar.display = bool(self.emoji_codes)

    def take_emoji(self) -> None:
        """Put the chosen shortcode in place of the ``:ta`` typed."""
        q = typed_query(self._line_to_cursor()) or ""
        code = self.emoji_codes[self.emoji_chosen]
        row, col = self.cursor_location
        self.replace(f":{code}: ", (row, col - len(q)), (row, col))
        self.emoji_codes = []
        self._draw_emoji()

    async def emoji_key(self, event) -> bool:
        """Handle a key while shortcodes are offered; True when it was theirs."""
        if not self.emoji_codes:
            return False
        if event.key in ("tab", "enter"):
            event.stop()
            event.prevent_default()
            self.take_emoji()
            return True
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            self.emoji_chosen = (self.emoji_chosen + (1 if event.key == "down" else -1)) % len(self.emoji_codes)
            self._draw_emoji()
            return True
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.emoji_codes = []
            self._draw_emoji()
            return True
        return False


class EmojiPickerDialog(Dialog):
    """Insert emoji (the palette), or one for a group's own set of reactions (#45): search
    by name, or browse the categories; returns the shortcode chosen, or None."""

    def __init__(self, title: str = "Insert emoji"):
        super().__init__()
        self.title_ = title

    def compose(self) -> ComposeResult:
        """A search field and the list: the matches, or every category's emoji."""
        with Vertical(classes="dialog list"):
            yield Label(self.title_, classes="title", markup=False)
            yield Input(placeholder="Search, e.g. heart, party, cat", id="emoji-search")
            yield OptionList(id="emoji-list")

    def on_mount(self) -> None:
        """Fill the list, the focus in the search."""
        self._fill("")
        self.query_one("#emoji-search", Input).focus()

    def _fill(self, query: str) -> None:
        table = markup.emoji_table()
        box = self.query_one("#emoji-list", OptionList)
        box.clear_options()
        if query.strip():
            codes = markup.find_emoji(query, 60)
            box.add_options([Option(f"{table.get(c, '')}  :{c}:", id=c) for c in codes]
                            or [Option("Nothing found", id="", disabled=True)])
            return
        for cat in markup.emoji_categories():
            box.add_option(Option(cat["name"], disabled=True))
            box.add_options([Option(f"  {table.get(c, '')}  :{c}:", id=c) for c in cat["codes"][:80]])

    @on(Input.Changed, "#emoji-search")
    def _search(self, event: Input.Changed) -> None:
        self._fill(event.value)

    @on(Input.Submitted, "#emoji-search")
    def _first(self) -> None:
        box = self.query_one("#emoji-list", OptionList)
        for i in range(box.option_count):                # Enter in the search: the first match
            o = box.get_option_at_index(i)
            if o.id:
                self.dismiss(o.id)
                return
        box.focus()

    @on(OptionList.OptionSelected, "#emoji-list")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(event.option.id)


class EmojiTextArea(EmojiCompletion, TextArea):
    """A text area with shortcode completion (the post editor's body)."""

    async def _on_key(self, event) -> None:
        if await self.emoji_key(event):
            return
        await super()._on_key(event)
        self.refresh_emoji()
