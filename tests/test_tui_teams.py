"""#13: team inboxes in the terminal client (UN-RFC 0047, unseenet#341) - a team inbox
under its name, r answering as the team without your signature, a message on an
inbox's behalf shown as the team, a removed reader told, the owner's readers dialog."""
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core import crypto  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui.dialogs.teams import TeamReadersDialog  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
OWNER = crypto.Identity.generate()
TEAM_KEY = crypto.Identity.generate()
GID = f"operator@{OWNER.fingerprint}"
SONIA = crypto.Identity.generate()


async def _app(tmp_path, monkeypatch, reader=True):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.set_signature("Andreas, Kvistgård")
    s.data["team_keys"] = {GID: {"seed": TEAM_KEY.seed.hex(), "identity": "andreas", "name": ""}}
    s.group_entry(GID).update({"type": "inbox", "manifest": {
        "v": 1, "name": "operator", "type": "inbox", "owner": OWNER.key_text, "serial": 2,
        "team_name": "Unseenet (operator)",
        "readers": [TEAM_KEY.key_text] if reader else [crypto.Identity.generate().key_text]}})
    s.subscribe(GID)
    s.save()
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


def _help(app):
    s = app.session
    s.cache[GID] = {1: Message(number=1, message_id="<s@x>", group=GID, subject="Hjælp", from_name="Sonia",
                               author_key=SONIA.key_text, sender_key=SONIA.key_text, private=True,
                               date=datetime.now(timezone.utc), body="min node er nede\n",
                               reply_inbox=f"inbox@{SONIA.fingerprint}")}


async def test_a_team_inbox_and_replying_as_the_team(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        assert s.group_label(GID) == "Unseenet (operator) (team inbox)"
        assert "Kvistgård" in s.signature()                                  # you have one
        _help(app)
        app.current_group = GID
        app._render_threads()
        assert "you reply as the team" in str(app.query_one("#threads-title").render())
        app.current_message = app.messages["<s@x>"]
        app.action_reply()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "ComposeDialog"), stack(app)
        await pilot.pause(0.2)
        heads = " ".join(str(w.render()) for w in app.screen.query("Label"))
        assert "Reply as Unseenet (operator)" in heads
        assert "Kvistgård" not in app.screen.query_one("#body").text        # never your signature
        app.pop_screen()
        await pilot.press("q")


async def test_a_message_on_an_inboxs_behalf_is_the_team(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        m = Message(number=2, message_id="<a@x>", group=app.session.my_inbox, subject="Re: Hjælp",
                    from_name="Andreas", author_key=TEAM_KEY.key_text, sender_key=TEAM_KEY.key_text,
                    private=True, body="vi ser på det\n", team={"group": GID})
        assert app._who(m) == "Unseenet (operator)"
        head, _ = app.article(m)
        assert "on the inbox's behalf, verified" in head and "Andreas" not in head.split("\n")[1]
        await pilot.press("q")


async def test_a_removed_reader_is_told(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch, reader=False)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        _help(app)
        app.current_group = GID
        app._render_threads()
        assert "removed you as a reader" in str(app.query_one("#threads-title").render())
        app.action_forget_team()
        await pilot.pause(0.2)
        assert app.session.team_inboxes() == []
        await pilot.press("q")


async def test_the_readers_dialog(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(TeamReadersDialog(["ed25519:one", "ed25519:two"], "Unseenet (operator)"), got.append)
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "TeamReadersDialog"), stack(app)
        await pilot.pause(0.2)
        app.screen.query_one("#r0").value = False
        app.screen.query_one("#add").value = "ed25519:helper"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert got[0] == (["ed25519:one"], "ed25519:helper", "Unseenet (operator)")
        await pilot.press("q")
