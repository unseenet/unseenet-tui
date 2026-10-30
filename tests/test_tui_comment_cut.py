"""#100: beside a feed, a long comment is cut by the core's rule (unseenet#781) with
"… [more]", its own words first; Enter shows it whole - as entries are, and as in the GUI."""

from datetime import timedelta

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


@pytest.fixture(autouse=True)
def _history_dates(monkeypatch):
    from unseenet.node import server as node_server
    monkeypatch.setattr(node_server, "POST_PAST", timedelta(days=3650))


async def test_a_long_comment_is_cut(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{port}")
    gid = await s.create_group("dk.noter", "open", min_pow=10, view="feed", comments="open")
    entry = await s.post(gid, "Hej", "Et opslag\n")
    long = "\n\n".join(f"Afsnit {i} om marsvin, hø og vand og alt det andet." for i in range(1, 13))
    comment = await s.post(gid, "", "> Et opslag\n\n" + long + "\nDET SIDSTE\n",
                           references=[entry], kind="chat")
    short = await s.post(gid, "", "Kort og godt\n", references=[entry], kind="chat")
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(140, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.select_group(gid)
        assert await wait_for(pilot, lambda: comment in app.messages and short in app.messages, 400), stack(app)
        _, body = app.article(app.messages[comment], cut=True)
        text = str(body)
        assert "… [more]" in text and "DET SIDSTE" not in text
        assert text.lstrip().startswith("Afsnit 1")                 # its own words first, no quote
        _, whole = app.article(app.messages[comment])                # Enter: the whole of it
        assert "DET SIDSTE" in str(whole) and "… [more]" not in str(whole)
        _, kort = app.article(app.messages[short], cut=True)
        assert "Kort og godt" in str(kort) and "more" not in str(kort)
    server.close()
