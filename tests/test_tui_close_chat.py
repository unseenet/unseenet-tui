"""Closing a chat (UN-RFC 0044 section 4.2, closed_chats): off the list of chats on
every device, back by itself with the next chat line, and when you write."""

from datetime import timedelta

import pytest

import unseenet.client.session as sess
from unseenet.client.localapi import LocalAPI
from unseenet.client.session import Session
from unseenet.core import crypto
from unseenet.core import state as st
from unseenet.core.store import Store
from unseenet.core.vault import Vault
from unseenet.node.server import Node, NodeConfig

KDF = {"ops": 1, "mem": 8192 * 1024}


@pytest.fixture
async def two(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    monkeypatch.setattr(Node, "STATE_EVERY", timedelta(0))
    import unseenet.client.session.state as sstate
    monkeypatch.setattr(sstate, "WRITE_EVERY", 0.0)
    monkeypatch.setattr(sess, "DEFAULT_POW", 8)
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    addr = "127.0.0.1:%d" % server.sockets[0].getsockname()[1]
    made = []

    def person(name, seed=None):
        s = Session(Vault.open(tmp_path / f"{name}.vault", "pw", **KDF), status=lambda *_: None)
        if seed:
            s.data["identities"]["me"] = {"seed": seed, "display": "Me"}
            s.data["default_identity"] = "me"
        else:
            s.new_identity(name, name.title())
        s.add_node(addr)
        made.append(s)
        return s
    yield person
    for s in made:
        await s.close()
    server.close()


async def test_tui_close_chat_and_contact_from_the_profile(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from tuihelp import app_ready, press, stack, wait_for
    from unseenet_tui.app import CONTACT, ConfirmDialog, InputDialog, ProfileDialog, UnseenetApp
    monkeypatch.setattr(sess, "DEFAULT_POW", 8)
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    addr = "127.0.0.1:%d" % server.sockets[0].getsockname()[1]
    me = Session(Vault.open(tmp_path / "me.vault", "pw", **KDF), status=lambda *_: None)
    me.new_identity("me", "Me"); me.add_node(addr); me.data["starter_groups"] = 99
    await me.ensure_inbox()
    sonia = Session(Vault.open(tmp_path / "s.vault", "pw", **KDF), status=lambda *_: None)
    sonia.new_identity("sonia", "Sonia"); sonia.add_node(addr)
    await sonia.ensure_inbox()
    await sonia.send_chat("contact:" + me.identity().key_text, "Hej")
    key = sonia.identity().key_text
    await sonia.close()
    await me.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(tmp_path / "me.vault")
    async with app.run_test(size=(140, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        assert await wait_for(pilot, lambda: key in app.session.chat_partners(), 300)
        app.action_show_profile(key)                       # her profile: Add contact
        assert await wait_for(pilot, lambda: isinstance(app.screen, ProfileDialog), 200)
        await press(pilot, app, "#contact")
        assert await wait_for(pilot, lambda: isinstance(app.screen, InputDialog), 100)
        app.screen.query_one("#value").value = "Sonia"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.contact_name(key) == "Sonia", 100)
        app.select_group(CONTACT + key)                    # the chat with her: Close chat
        assert await wait_for(pilot, lambda: app.current_group == CONTACT + key, 200)
        app.action_close_chat()
        assert await wait_for(pilot, lambda: app.session.chat_closed(key), 200)
        assert key not in app.session.chat_partners()
        app.action_show_profile(key)                       # Remove contact, after asking
        assert await wait_for(pilot, lambda: isinstance(app.screen, ProfileDialog), 200)
        assert str(app.screen.query_one("#contact").label) == "Remove contact"
        await press(pilot, app, "#contact")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmDialog), 100)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.contact_name(key) is None, 100)
        await pilot.press("q")
    server.close()
