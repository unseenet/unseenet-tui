"""#50: tags and content warnings in the terminal (UN-RFC 0052, 0053) - a stripe before
the subject, a quiet line for what you hide, the flags and Show anyway in the reader,
topics under the text, tags while writing, and the settings."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core import crypto  # noqa: E402
from unseenet.core import labels as lb  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, laid_out, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
GID = "dk.film@abcdabcdabcdabcd"
NOW = datetime.now(timezone.utc)
LABELLER = crypto.Identity.generate()


def msg(n, mid, subject, minutes=0, tags=()):
    return Message(number=n, message_id=mid, group=GID, subject=subject, from_name="X",
                   author_key="ed25519:" + "a" * 52, date=NOW - timedelta(minutes=60 - minutes),
                   body=subject + " - han dør til sidst", tags=tuple(tags))


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


def _labelled(s):
    """A film group: one post the author warns about, one a labeller you follow says is gore."""
    s.subscribe(GID)
    s.cache[GID] = {1: msg(1, "<a@x>", "Slutningen", 1, ["spoiler", "serier"]), 2: msg(2, "<b@x>", "Blodigt", 2),
                    3: msg(3, "<c@x>", "Trailer", 3, ["serier"])}
    lgid = lb.labels_group(LABELLER.public_key)
    s.cache[lgid] = {1: Message(number=1, message_id="<l@x>", group=lgid, subject="Labels",
                                author_key=LABELLER.key_text, date=NOW - timedelta(minutes=5),
                                body=lb.label_body([("<b@x>", GID, ["gore"])]))}
    s.follow_labeller(LABELLER.key_text, "Filmfolk")
    s.set_warning_choice("gore", "hide")


async def test_the_list_and_the_reader(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        _labelled(s)
        app.current_group = GID
        app._render_threads()
        table = app.query_one("#threads")
        rows = [table.get_row_at(i)[1] for i in range(table.row_count)]
        plain = [str(r) for r in rows]
        assert any(r.startswith("▌ Slutningen") for r in plain)              # the stripe
        assert any("⊘ Hidden: gore, from Filmfolk" in r for r in plain)     # never silently
        assert not any("Blodigt" in r for r in plain)
        head, body = app.article(app.messages["<a@x>"])
        assert "▌spoiler" in head and "from the author" in head
        assert "hidden: spoiler" in str(body) and "han dør" not in str(body)
        app.current_message = app.messages["<a@x>"]
        app.action_show_reported()                                           # Show anyway
        await pilot.pause(0.1)
        head, body = app.article(app.messages["<a@x>"])
        assert "han dør" in str(body) and "Tags: serier" in str(body)
        await pilot.press("q")


async def test_writing_and_the_settings(tmp_path, monkeypatch):
    from unseenet_tui.dialogs.compose import ComposeDialog
    from unseenet_tui.dialogs.tags import ContentWarningsDialog
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        _labelled(s)
        dlg = ComposeDialog(s.group_info(GID))
        app.push_screen(dlg, got.append)
        assert await wait_for(pilot, lambda: laid_out(app, "#tags")), stack(app)
        app.screen.query_one("#subject").value = "Finalen"
        app.screen.query_one("#body").text = "Om #serier\n"
        app.screen.query_one("#tags").value = "spoiler, finale"
        app.screen.action_send()
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert dlg.tags == ["spoiler", "finale"]
        choices = []
        app.push_screen(ContentWarningsDialog(s.warning_choices(), s.labellers()), choices.append)
        assert await wait_for(pilot, lambda: laid_out(app, "#w-spoiler")), stack(app)
        app.screen.query_one("#w-spoiler").value = "show"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(choices)), stack(app)
        assert choices[0]["choices"]["spoiler"] == "show" and choices[0]["choices"]["gore"] == "hide"
        await pilot.press("q")


async def test_everything_with_a_tag(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        _labelled(app.session)
        app.current_group = GID
        app._render_threads()
        app.current_message = app.messages["<c@x>"]
        app.action_tagged("serier")
        assert await wait_for(pilot, lambda: app.current_group == "tag:serier"), stack(app)
        assert set(app.messages) == {"<a@x>", "<c@x>"}
        assert "# serier" in str(app.query_one("#threads-title").render())
async def test_the_age_guide(tmp_path, monkeypatch):
    """UN-RFC 0053 6.1: a profile's choices become yours; nothing of the answer is kept."""
    import json
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.action_age_guide()
        assert await wait_for(pilot, lambda: laid_out(app, "#age")), stack(app)
        assert "only cover what is marked" in str(app.screen.query("Label").last().render())
        app.screen.query_one("#age").value = "13-17"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.warning_choices()["sexual"] == "hide"), stack(app)
        assert "13-17" not in json.dumps(app.session.data)
        await pilot.press("q")


async def test_room_to_write_and_warnings_behind_one_choice(tmp_path, monkeypatch):
    """#74 (as gui#203): Ctrl+E bigger and remembered, warnings behind one button with
    only the chosen ones shown, Ctrl+B / Ctrl+Q, and the Send button sends."""
    from textual.widgets import Checkbox
    from unseenet_tui.dialogs.compose import ComposeDialog
    from tuihelp import press
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        dlg = ComposeDialog(s.group_info(GID))
        app.push_screen(dlg, got.append)
        assert await wait_for(pilot, lambda: laid_out(app, "#warn-btn")), stack(app)
        assert not app.screen.query_one("#warn-list").has_class("open")     # no colours yet
        app.screen.action_warnings()
        assert app.screen.query_one("#warn-list").has_class("open")
        app.screen.query_one("#w-spoiler", Checkbox).value = True
        await pilot.pause(0.1)
        assert "Spoiler" in str(app.screen.query_one("#warn-btn").label)
        app.screen.action_bigger()
        assert app.screen.query_one(".dialog.compose").has_class("big")
        assert s.client_setting("tui", "compose_big") is True
        body = app.screen.query_one("#body")
        body.text = "hej"
        body.select_all()
        app.screen.action_bold()
        assert body.text.startswith("**hej**")
        body.move_cursor((0, 0))
        app.screen.action_quote()
        assert body.text.startswith("> **hej**")
        app.screen.action_bigger()                       # back: the subject is shown again
        app.screen.query_one("#subject").value = "Finalen"
        app.screen.query_one("#tags").value = "finale"
        await press(pilot, app, "#ok")                   # the button sends (not read more)
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert dlg.tags == ["spoiler", "finale"]
        await pilot.press("q")
