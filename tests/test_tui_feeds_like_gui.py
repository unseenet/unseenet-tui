"""#34: feeds as in the GUI - how long ago, a long entry cut with "… more" beside the list,
and comments folded: the newest one and your own talks, "View N more" 20 at a time."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from unseenet.client.session import Session  # noqa: E402
from unseenet.core import markup  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app_chat import cut_text  # noqa: E402
from unseenet_tui.helpers import CLIP_LINES, FOLDED  # noqa: E402

from test_tui_my_feed import KDF, _setup  # noqa: E402
from tuihelp import app_ready, stack, wait_for  # noqa: E402


def test_a_long_entry_is_cut():
    short, more = cut_text(markup.body("Kort\n", None))
    assert not more and "Kort" in short.plain
    long_text = "\n\n".join(f"Afsnit {i}" for i in range(20))
    shown, more = cut_text(markup.body(long_text, None))
    assert more and len(shown.plain.split("\n")) == CLIP_LINES
    tree = [{"t": "p", "c": [{"t": "text", "v": "Indledning"}]}, {"t": "more"},
            {"t": "p", "c": [{"t": "text", "v": "Resten"}]}]
    shown, more = cut_text(tree)                         # the author's cut (UN-RFC 0031 3.2.1)
    assert more and shown.plain == "Indledning"
    assert isinstance(shown, Text)


async def test_tui_feed_comments_folded_and_how_long_ago(tmp_path, monkeypatch):
    app, server, addr = await _setup(tmp_path, monkeypatch)
    alice = Session(Vault.open(tmp_path / "a.vault", "pw", **KDF), status=lambda *_: None)
    alice.new_identity("alice", "Alice")
    alice.add_node(addr)
    feed = await alice.start_personal_group("feed")
    entry = await alice.post(feed, "", "En note\n", kind="chat")
    comments = [await alice.post(feed, "", f"Kommentar {i}\n", kind="chat", references=[entry])
                for i in range(4)]
    await alice.close()
    async with app.run_test(size=(160, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.session.subscribe(feed)
        await app.session.fetch_unknown_manifests()
        await app.session.fetch_group(feed)
        app.select_group(feed)
        assert await wait_for(pilot, lambda: app.current_group == feed and entry in app.messages, 300), stack(app)
        table = app.query_one("#threads")
        keys = [r.key.value for r in table.ordered_rows]
        assert keys == [entry, FOLDED + entry, comments[-1]]      # the newest only, 3 behind
        assert "View 3 more comments" in str(table.get_row(FOLDED + entry)[1])
        when = table.get_row(entry)[3]
        assert when == "now" or when.endswith(" min")              # how long ago
        app.show_more_comments(entry)
        await pilot.pause(0.2)
        keys = [r.key.value for r in table.ordered_rows]
        assert keys == [entry, *comments]                         # 20 more: all of them
        app._comments_more.clear()
        app.current_message = app.messages[entry]
        app.action_which_comments()                               # where you wrote: none
        await pilot.pause(0.2)
        assert [r.key.value for r in table.ordered_rows] == [entry, FOLDED + entry]
        await pilot.press("q")
    server.close()
