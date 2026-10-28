"""#39: Private chats as one fixed item at the top of the group list - the unread chats
under it, at most five, then Show all - and the full list as a dialog, not a "Chats"
folder among the groups."""

import pytest

import unseenet.client.session as sess
from unseenet.client.session import Session
from unseenet.core.store import Store
from unseenet.core.vault import Vault
from unseenet.node.server import Node, NodeConfig

KDF = {"ops": 1, "mem": 8192 * 1024}


async def test_tui_private_chats_item_and_list(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from tuihelp import app_ready, stack, wait_for
    from unseenet_tui.app import CONTACT, UnseenetApp
    from unseenet_tui.dialogs.people import ChatsDialog
    from unseenet_tui.helpers import CHATS
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
        app.refresh_groups()
        tree = app.query_one("#groups")

        def item():
            return next((n for n in tree.root.children if n.data == CHATS), None)
        assert await wait_for(pilot, lambda: item() is not None
                              and "Private chats" in str(item().label), 300), stack(app)
        assert not any(n.data == ("folder", "Chats") for n in tree.root.children)   # no folder

        def unread_under():                                # her line is unread, once fetched
            if "(1)" not in str(item().label):
                app.refresh_groups()
                return False
            return [c.data for c in item().children] == [CONTACT + key]
        assert await wait_for(pilot, unread_under, 300), stack(app)
        app.action_chats()                                 # the whole list
        assert await wait_for(pilot, lambda: isinstance(app.screen, ChatsDialog), 200)
        assert [c[0] for c in app.screen.chats] == [key]
        app.screen.dismiss(("chat", key))
        assert await wait_for(pilot, lambda: app.current_group == CONTACT + key, 300)
        names = [c.title for c in app.get_system_commands(app.screen)]
        assert "Private chats" in names
        # #73: Your contacts, Private posts, Private chats - in that order, alike
        from unseenet_tui.helpers import CONTACTS
        top = [n.data for n in tree.root.children]
        assert top.index(CONTACTS) < top.index(app.session.my_inbox) < top.index(CHATS)
        # the person picker: Tab switches chat/post; her name, Enter opens the chat
        from unseenet_tui.dialogs.people import PersonPicker
        app.select_group(app.session.my_inbox)
        app.action_chat_with()
        assert await wait_for(pilot, lambda: isinstance(app.screen, PersonPicker), 200)
        assert app.screen.kind == "chat" and app.screen.people[0][0] == key
        app.screen.action_switch()
        assert app.screen.kind == "post"
        app.screen.action_switch()
        app.screen.query_one("#pfind").value = "sonia"
        await pilot.pause(0.1)
        app.screen._open()
        assert await wait_for(pilot, lambda: app.current_group == CONTACT + key, 300)
        await pilot.press("q")
    server.close()
