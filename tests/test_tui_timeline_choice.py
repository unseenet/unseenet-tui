"""UN-RFC 0034 section 6.2 in the terminal client: a group of threads put into the
timeline (and taken out) from Ctrl+P, a mark on it in the group list and its title, and
"Leave group" as the one word for unsubscribing."""

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.client.threads import TIMELINE  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.helpers import TIMELINE_MARK  # noqa: E402

from test_tui_my_feed import KDF, _setup, _top  # noqa: E402
from tuihelp import app_ready, stack, wait_for  # noqa: E402


def _label(app, gid):
    def leaves(n):
        for c in n.children:
            yield c
            yield from leaves(c)
    node = next((n for n in leaves(app.query_one("#groups").root) if n.data == gid), None)
    return None if node is None else str(node.label)


async def test_tui_puts_a_group_of_threads_into_the_timeline(tmp_path, monkeypatch):
    app, server, addr = await _setup(tmp_path, monkeypatch)
    alice = Session(Vault.open(tmp_path / "a.vault", "pw", **KDF), status=lambda *_: None)
    alice.new_identity("alice", "Alice")
    alice.add_node(addr)
    talk = await alice.create_group("dk.snak", "open")
    await alice.post(talk, "Et emne", "Hvad siger I?\n")
    await alice.close()
    async with app.run_test(size=(160, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.session.subscribe(talk)
        await app.session.fetch_unknown_manifests()
        app.refresh_groups()
        assert await wait_for(pilot, lambda: _label(app, talk) is not None, 300), stack(app)
        assert TIMELINE_MARK not in _label(app, talk)
        assert TIMELINE not in _top(app)                     # nothing in it yet
        app.select_group(talk)
        assert await wait_for(pilot, lambda: app.current_group == talk, 300)
        app.action_toggle_timeline()
        assert app.session.in_timeline(talk)
        assert await wait_for(pilot, lambda: TIMELINE in _top(app)
                              and TIMELINE_MARK in _label(app, talk), 200), stack(app)
        assert "in your timeline" in str(app.query_one("#threads-title").render())
        app.action_toggle_timeline()
        assert not app.session.in_timeline(talk)
        assert await wait_for(pilot, lambda: TIMELINE_MARK not in _label(app, talk), 200)
        names = [c.title for c in app.get_system_commands(app.screen)]
        assert "Leave group" in names and "Unsubscribe from group" not in names
        assert "Add to timeline / take out of timeline" in names
        await pilot.press("q")
    server.close()
