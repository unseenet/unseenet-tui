"""Sharing in the terminal client (UN-RFC 0060, #110), against a local node: a plain share
is listed as "↻ Mette shared: <the original's subject>" and read as the original, a share
with a comment carries the original as a card, b shares the selected entry of a feed, and
the palette has the rest."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable  # noqa: E402

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui.app_shares import shared_by, shared_text  # noqa: E402

from tuihelp import app_ready, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


def subjects(app) -> dict[str, str]:
    table = app.query_one("#threads", DataTable)
    return {str(k.value): str(table.get_cell(k, "subject")) for k in table.rows}


def test_the_card_and_who_shared():
    when = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    o = Message(0, "<o@x>", "dk.snak@abcd", subject="Mit marsvin hoster", from_name="Andreas",
                body="Hvad gør jeg?\nDet hoster hele tiden.\n", date=when)
    text = shared_text({"state": "ok", "message": o}, "dk.snak").plain.splitlines()
    assert text[0].startswith("┌ ↻ Andreas · dk.snak")
    assert text[1] == "│ Mit marsvin hoster" and text[2] == "│ Hvad gør jeg?"
    assert text[-1] == "└ Ctrl+P: Open the shared post"
    o.edited = when + timedelta(days=2)                            # edited after the share
    assert "edited after it was shared" in shared_text({"state": "ok", "message": o}, "x",
                                                       when + timedelta(days=1)).plain
    assert shared_text(None).plain == "┌ ↻ Fetching the shared post …"
    assert shared_text({"state": "gone", "message": None}).plain == "┌ ↻ No longer in the network"
    share = Message(0, "<s@x>", "feed@x", from_name="Mette", kind="share")
    assert shared_by(share) == "Mette"
    share.shared_by = ["Søren", "Mette"]
    assert shared_by(share) == "Søren and Mette"                  # never a count


@pytest.fixture(autouse=True)
def _history_dates(monkeypatch):
    from unseenet.node import server as node_server
    monkeypatch.setattr(node_server, "POST_PAST", timedelta(days=3650))


async def test_tui_shares(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    a = Session(Vault.open(tmp_path / "a.vault", "pw", **KDF), status=lambda *_: None)
    a.new_identity("andreas", "Andreas")
    a.add_node(f"127.0.0.1:{port}")
    gid = await a.create_group("dk.snak", "open", min_pow=10)
    orig = await a.post(gid, "Mit marsvin hoster", "Hvad gør jeg?\n")
    other = await a.post(gid, "Et andet opslag", "Også godt\n")
    await a.close()

    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("mette", "Mette")
    s.add_node(f"127.0.0.1:{port}")
    s.subscribe(gid)
    msgs = {m.message_id: m for m in await s.fetch_group(gid)}
    feed = await s.start_personal_group("feed")
    plain = await s.share(msgs[orig])
    said = await s.share(msgs[orig], "Det her er præcis det, vi talte om", into=None)
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(150, 44)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.select_group(feed)
        assert await wait_for(pilot, lambda: app.current_group == feed and said in app.messages, 400)
        # sharing again replaced the plain share by the one with a comment (section 4.5):
        # the plain one is gone from the feed
        assert plain not in app.messages
        _, body = app.article(app.messages[said])
        assert await wait_for(pilot, lambda: "Mit marsvin hoster" in str(app.article(app.messages[said])[1]),
                              400), str(body)
        assert "┌ ↻ Andreas" in str(app.article(app.messages[said])[1])

        # b in a feed: shares the selected entry; in a discussion group it is not offered
        app.select_group(gid)
        assert await wait_for(pilot, lambda: app.current_group == gid and other in app.messages, 400)
        app.current_message = app.messages[other]
        assert app.check_action("share", ()) is False                  # not a feed
        app.action_share()
        assert await wait_for(pilot, lambda: any("Shared" in n.message for n in app._notifications),
                              400), [n.message for n in app._notifications]
        app.select_group(feed)
        assert await wait_for(pilot, lambda: any("↻ Mette shared: Et andet opslag" in t
                                                 for t in subjects(app).values()), 400), subjects(app)
        shared_row = next(k for k, t in subjects(app).items() if "Et andet opslag" in t)
        head, body = app.article(app.messages[shared_row])
        assert "↻ Mette shared" in head and "Også godt" in str(body)
        app.current_message = app.messages[shared_row]
        assert app.check_action("share", ()) is True                   # a feed: b shares
        names = [t for t, *_ in app.share_commands()]
        assert "Share into a group" in names and "Stop showing share notices" in names
    server.close()
