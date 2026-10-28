"""#76 (unseenet#706): following a person puts their feed and site together under their
name, in People you follow - not on two branches called feed and site."""

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402

from test_tui_my_feed import KDF, _setup  # noqa: E402
from tuihelp import app_ready, stack, wait_for  # noqa: E402


async def test_tui_a_persons_feed_and_site_together(tmp_path, monkeypatch):
    app, server, addr = await _setup(tmp_path, monkeypatch)
    alice = Session(Vault.open(tmp_path / "a.vault", "pw", **KDF), status=lambda *_: None)
    alice.new_identity("alice", "Alice")
    alice.add_node(addr)
    feed = await alice.start_personal_group("feed")
    await alice.post(feed, "Hej", "Fra Alice\n")
    site = await alice.start_personal_group("site")
    await alice.put_page(site, "index", "# Alices side\n")
    key = alice.identity().key_text
    await alice.close()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        if not hasattr(app.session, "followed_people_groups"):
            pytest.skip("needs the core's people_followed (unseenet#706)")
        await app.session.follow_person(key)
        app.session.subscribe(site)
        await app.session.fetch_unknown_manifests()
        app.refresh_groups()
        tree = app.query_one("#groups")

        def people():
            return next((n for n in tree.root.children if n.data == ("folder", "people:")), None)
        assert await wait_for(pilot, lambda: people() is not None and people().children, 400), stack(app)
        [person] = people().children
        assert "Alice" in str(person.label)
        assert {c.data for c in person.children} == {feed, site}
        assert not any(isinstance(n.data, tuple) and n.data[1] in ("feed", "site") for n in tree.root.children)
        await pilot.press("q")
    server.close()
