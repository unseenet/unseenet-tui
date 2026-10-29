"""Comments on one thread in the terminal client (#90, UN-RFC 0030 sections 3.1-3.2,
unseenet#732), against a local node: a new thread's own time for comments, Close
comments with a note from the palette and the note in the thread's head, and Open
comments again."""

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Select  # noqa: E402

from unseenet.client.session import Session  # noqa: E402
from unseenet_tui.app import ComposeDialog, ConfirmDialog, PinDialog, UnseenetApp  # noqa: E402
from unseenet_tui.dialogs.compose import comment_days_options  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402

from tuihelp import laid_out, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


def test_the_choices_never_pass_the_groups():
    assert [v for _, v in comment_days_options(14)] == ["group", "0", "1", "7"]
    assert [v for _, v in comment_days_options(None)] == ["group", "0", "1", "7", "30"]
    assert comment_days_options(None)[0][0] == "Comments: as the group (no limit)"


async def test_tui_close_and_open_comments(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{port}")
    gid = await s.create_group("dk.debat", "open", min_pow=10, comment_days=14)
    root = await s.post(gid, "Emne", "Hvad synes I?\n")
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: app.current_group == gid, 300)
        assert await wait_for(pilot, lambda: root in app.messages, 400)
        app.current_message = app.messages[root]

        app.action_close_comments()
        assert await wait_for(pilot, lambda: isinstance(app.screen, PinDialog)), stack(app)
        assert await wait_for(pilot, lambda: laid_out(app, "#value"))
        app.screen.query_one("#value", Input).value = "Nok for nu"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: not app.session.comments_state(gid, root)["open"],
                              400), stack(app)
        await app.session.fetch_group(gid)
        state = app.session.comments_state(gid, root)
        assert state["reason"] == "closed" and state["note"] == "Nok for nu"
        perm = app.session.write_permission(gid, reply_to=app.messages[root], kind=None)
        assert perm.code == "comments_closed" and "Nok for nu" in perm.reason

        app.current_message = app.messages[root]
        app.action_close_comments()
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmDialog)), stack(app)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.comments_state(gid, root)["open"],
                              400), stack(app)
    server.close()


async def test_tui_new_thread_offers_its_own_time(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{port}")
    gid = await s.create_group("dk.ny", "open", min_pow=10, comment_days=14)
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: app.current_group == gid, 300)
        g = app.session.group_info(gid)
        app.compose_post(g)
        assert await wait_for(pilot, lambda: isinstance(app.screen, ComposeDialog)), stack(app)
        assert await wait_for(pilot, lambda: laid_out(app, "#comment_days"))
        dlg = app.screen
        dlg.query_one("#subject", Input).value = "Kort tråd"
        dlg.query_one("#body").text = "Svar i dag\n"
        dlg.query_one("#comment_days", Select).value = "1"
        dlg.action_send()
        async def posted():
            await app.session.fetch_group(gid)
            return any(m.subject == "Kort tråd" for m in app.session.cache.get(gid, {}).values())
        for _ in range(60):
            if await posted():
                break
            await pilot.pause(0.1)
        mine = [m for m in app.session.cache.get(gid, {}).values() if m.subject == "Kort tråd"]
        assert mine and mine[0].comment_days == 1
    server.close()
