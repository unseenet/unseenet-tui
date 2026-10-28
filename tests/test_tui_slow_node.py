"""#70 (unseenet#704): a node not answering yet is named in the status line; when it
answers, its groups come into the list."""

import pytest

pytest.importorskip("textual")

from unseenet.client import events as ev  # noqa: E402

from test_tui_my_feed import _setup  # noqa: E402
from tuihelp import app_ready, stack, wait_for  # noqa: E402


async def test_tui_says_which_node_is_slow(tmp_path, monkeypatch):
    app, server, addr = await _setup(tmp_path, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        if not hasattr(app.session, "slow"):
            pytest.skip("needs the core's slow nodes (unseenet#704)")
        calls = []
        monkeypatch.setattr(type(app), "refresh_groups", lambda self: calls.append(1))
        app._on_event(ev.Refreshed(available=1, slow=("x.onion:119",)))
        assert app._slow == ("x.onion:119",) and not calls
        app._on_event(ev.Refreshed(available=2))      # it answered: the list again
        assert calls == [1] and app._slow == ()
        await pilot.press("q")
    server.close()
