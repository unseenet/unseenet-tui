"""#81 (as gui#215): from your contacts to a contact's profile - and from the profile,
chat, write a post and rename, as in the graphical client."""

from textual.widgets import Button

from unseenet.client.session import Session
from unseenet_tui.app import ContactsDialog, UnseenetApp
from unseenet_tui.dialogs.people import ProfileDialog
from unseenet.core import crypto
from unseenet.core.vault import Vault

from tuihelp import app_ready, stack, wait_for

KDF = {"ops": 1, "mem": 8192 * 1024}


async def test_contacts_profile_and_chat_from_it(tmp_path, monkeypatch):
    s = Session(Vault.open(tmp_path / "me.vault", "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node("127.0.0.1:1")      # without a node the first-run setup dialog opens on top
    sonia = crypto.Identity.generate().key_text
    s.add_contact(sonia, "Sonia")
    s.save()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(tmp_path / "me.vault")
    async with app.run_test(size=(140, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        async def nothing(*_a, **_k):              # no node here: no profile, no feeds
            return None

        async def no_groups(*_a, **_k):
            return {}
        monkeypatch.setattr(app.session, "prefetch_profiles", nothing)
        monkeypatch.setattr(app.session, "profile_of", nothing)
        monkeypatch.setattr(app.session, "personal_groups_of", no_groups)
        app.action_contacts()
        assert await wait_for(pilot, lambda: isinstance(app.screen, ContactsDialog)), stack(app)
        await pilot.pause()
        assert app.screen.query_one("#open-chat", Button).region.width   # shown (was hidden by #chat's CSS)
        app.screen.query_one("#profile", Button).press()
        assert await wait_for(pilot, lambda: isinstance(app.screen, ProfileDialog), 400), stack(app)
        dlg = app.screen
        assert dlg.key_text == sonia and dlg.contact == "Sonia"
        ids = {b.id for b in dlg.query(Button)}
        assert {"with-chat", "with-post", "with-rename", "contact", "follow"} <= ids, ids
        await pilot.pause()
        chat = dlg.query_one("#with-chat", Button)
        assert chat.display and chat.region.width, chat.region      # shown, not hidden
        chat.press()
        assert await wait_for(pilot, lambda: app.current_group and sonia in app.current_group, 400), \
            (app.current_group, stack(app))
        await pilot.press("q")
