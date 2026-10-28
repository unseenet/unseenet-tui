"""#86: a large group shows its newest threads, entries or chat lines first, with a
"View older" row that shows twice as many - the same rows as the GUI (unseenet#724)."""

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.helpers import OLDER, newest_rows  # noqa: E402

from test_tui_my_feed import KDF, _setup  # noqa: E402
from tuihelp import app_ready, stack, wait_for  # noqa: E402


def test_newest_rows_keep_whole_threads_and_the_newest_lines():
    threads = [("a", 0), ("a1", 1), ("b", 0), ("c", 0), ("c1", 1)]    # newest first
    assert newest_rows(threads, 2, chat=False) == ([("a", 0), ("a1", 1), ("b", 0)], True)
    assert newest_rows(threads, 3, chat=False) == (threads, False)
    lines = [(n, 0) for n in range(5)]                                    # a chat: oldest first
    assert newest_rows(lines, 2, chat=True) == ([(3, 0), (4, 0)], True)
    assert newest_rows(lines, 5, chat=True) == (lines, False)


async def test_view_older_shows_twice_as_many(tmp_path, monkeypatch):
    monkeypatch.setattr("unseenet_tui.app_groups.SHOWN_FIRST", 2)
    app, server, addr = await _setup(tmp_path, monkeypatch)
    alice = Session(Vault.open(tmp_path / "a.vault", "pw", **KDF), status=lambda *_: None)
    alice.new_identity("alice", "Alice")
    alice.add_node(addr)
    gid = await alice.create_group("dk.stor", "open")
    posts = [await alice.post(gid, f"Tråd {i}", "tekst\n") for i in range(5)]
    await alice.close()
    async with app.run_test(size=(160, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.session.subscribe(gid)
        await app.session.fetch_group(gid)
        app.select_group(gid)
        assert await wait_for(pilot, lambda: app.current_group == gid and app.messages, 300), stack(app)
        table = app.query_one("#threads")
        keys = [r.key.value for r in table.ordered_rows]
        assert keys[-1] == OLDER and len(keys) == 3              # 2 threads, then View older
        assert "View older threads" in str(table.get_row(OLDER)[1])
        app.show_older()
        await pilot.pause(0.2)
        keys = [r.key.value for r in table.ordered_rows]
        assert keys[-1] == OLDER and len(keys) == 5              # 4 threads
        app.show_older()
        await pilot.pause(0.2)
        keys = [r.key.value for r in table.ordered_rows]
        assert OLDER not in keys and set(keys) == set(posts)     # all 5: no row
