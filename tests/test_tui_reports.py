"""#11: spam reports in the terminal client (UN-RFC 0045, unseenet#122) - a post hidden by
a report says so in the list and the reader, Show anyway and Stop following, the issuers
dialog, and the report window with why, a real note and screenshots (unseenet-gui#70)."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import REPORT_REASONS, Message, Session  # noqa: E402
from unseenet.core import crypto  # noqa: E402
from unseenet.core import reports as rpt  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui.dialogs.reports import IssuersDialog, ReportDialog  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
GID = "dk.snak@abcdabcdabcdabcd"
NOW = datetime.now(timezone.utc)
ISSUER = crypto.Identity.generate()


def msg(n, mid, subject, minutes=0, author="ed25519:" + "a" * 52):
    return Message(number=n, message_id=mid, group=GID, subject=subject, from_name="X",
                   author_key=author, date=NOW - timedelta(minutes=60 - minutes), body=subject)


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


def _reported(s):
    """A group with a spam post, and the report of an issuer you follow about it."""
    s.subscribe(GID)
    s.cache[GID] = {1: msg(1, "<s@x>", "Billige ure", 1), 2: msg(2, "<ok@x>", "Velkommen", 2)}
    rgid = rpt.reports_group(ISSUER.public_key)
    s.cache[rgid] = {1: Message(number=1, message_id="<r@x>", group=rgid, subject="Report",
                                author_key=ISSUER.key_text, date=NOW - timedelta(minutes=5),
                                body=rpt.report_body([("<s@x>", GID, "spam")]))}
    s.follow_issuer(ISSUER.key_text, "Spamjægeren")


async def test_a_hidden_post_says_so_and_can_be_shown(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        _reported(s)
        app.current_group = GID
        app._render_threads()
        table = app.query_one("#threads")
        rows = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
        assert any("Hidden by Spamjægeren: spam" in r for r in rows)
        assert not any("Billige ure" in r for r in rows)               # never its subject
        assert "1 hidden by reports" in str(app.query_one("#threads-title").render())
        app.current_message = app.messages["<s@x>"]
        head, body = app.article(app.current_message)
        assert "Hidden by Spamjægeren" in head and "Show anyway" in str(body) and "køb" not in str(body)
        app.action_show_reported()
        await pilot.pause(0.1)
        rows = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
        assert any("Billige ure" in r for r in rows)
        await pilot.press("q")


async def test_stop_following_and_the_issuers_dialog(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        _reported(s)
        app.current_group = GID
        app._render_threads()
        app.current_message = app.messages["<s@x>"]
        app.action_stop_following_issuer()
        await pilot.pause(0.1)
        assert not any(i["key"] == ISSUER.key_text for i in s.report_issuers())
        assert app.messages["<s@x>"].reported is None
        app.push_screen(IssuersDialog([{"key": ISSUER.key_text, "label": "Spamjægeren", "yours": False,
                                        "hierarchies": ["dk"], "groups": ["alt.space.nasa@abcd"], "on": False}],
                                      lambda g: g.split("@")[0]))
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "IssuersDialog"), stack(app)
        await pilot.pause(0.2)
        assert "named for dk.*, alt.space.nasa" in str(app.screen.query_one("#i0").label)
        app.pop_screen()
        await pilot.press("q")


async def test_the_report_window(tmp_path, monkeypatch):
    """As in the graphical client: why, who, a real field for the note, screenshots."""
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        targets = [("The group's owner", "inbox@x", "ed25519:o"), ("Report issuer Spamjægeren", "inbox@y", "ed25519:i")]
        app.push_screen(ReportDialog("Someone: Billige ure", targets, REPORT_REASONS), got.append)
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "ReportDialog"), stack(app)
        await pilot.pause(0.2)
        scr = app.screen
        scr.query_one("#reason").value = "harassment"
        scr.query_one("#t1").value = False
        scr.query_one("#note").load_text("Se **skærmbilledet**.\n\n- truslen")
        assert scr.query_one("#note").size.height >= 5                  # room to write
        scr.query_one("#files").value = "~/skaerm.png"
        await pilot.pause(0.1)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        chosen, note, anonymous, reason, files = got[0]
        assert [t[0] for t in chosen] == ["The group's owner"] and reason == "harassment"
        assert note.startswith("Se **") and files == ["~/skaerm.png"] and anonymous is False
        await pilot.press("q")


def test_the_palette_offers_reports(tmp_path, monkeypatch):
    from unseenet_tui import app_push
    assert {"Spam reports", "Publish a spam report"} <= app_push.NEEDS_NETWORK


async def test_report_a_chat_line_where_none_is_selected(tmp_path, monkeypatch):
    """Asked for by the maintainer: in a chat, Report post lets you choose the line."""
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.group_entry(GID)["view"] = "chat"
        s.set_view(GID, "chat")
        s.cache[GID] = {1: msg(1, "<a@x>", "hej alle", 1), 2: msg(2, "<b@x>", "køb billige ure", 2)}
        app.current_group = GID
        app._render_threads()
        app.current_message = None
        app.action_report()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "ChooseLineDialog"), stack(app)
        await pilot.pause(0.2)
        options = [label for _, label, _ in app.screen.lines]
        assert len(options) == 2 and "køb billige ure" in options[0]          # newest first
        app.pop_screen()
        await pilot.press("q")


def test_the_group_info_says_how_to_report():
    import inspect
    from unseenet_tui.dialogs import groups
    assert "Reporting: select a post" in inspect.getsource(groups.GroupInfoDialog)


async def test_an_issuer_chooses_a_team_inbox_and_a_report_says_what_to_do(tmp_path, monkeypatch):
    from unseenet.client.session import REPORT_MD_TYPE
    from unseenet_tui.dialogs.reports import StartIssuerDialog
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(StartIssuerDialog([{"group": "contact@proj", "name": "Unseenet (operator)", "reader": True}]),
                        got.append)
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "StartIssuerDialog"), stack(app)
        await pilot.pause(0.2)
        app.screen.query_one("#land").value = "contact@proj"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert got[0] == ("", "contact@proj")
        report = Message(number=9, message_id="<r@x>", group=app.session.my_inbox, subject="Report: Ure",
                         from_name="Sonia", author_key=ISSUER.key_text, sender_key=ISSUER.key_text,
                         private=True, content_type=REPORT_MD_TYPE, date=NOW,
                         body=f"**Report of a post**\n\nGroup: `{GID}`  \nMessage-ID: `<s@x>`\n")
        head, _ = app.article(report)
        assert "Ctrl+P Open reported post" in head
        await pilot.press("q")


async def test_the_owner_sets_the_issuers_readers_follow_here(tmp_path, monkeypatch):
    """UN-RFC 0045 7.5 in Edit group: the keys, separated by spaces; a bad one refused."""
    from unseenet_tui.dialogs.groups import EditGroupDialog
    app = await _app(tmp_path, monkeypatch)
    got = []
    other = crypto.Identity.generate().key_text
    data = {"v": 1, "name": "alt.snak", "type": "open", "owner": "ed25519:x", "serial": 1,
            "report_issuers": [ISSUER.key_text]}
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(EditGroupDialog(data), got.append)
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "EditGroupDialog"), stack(app)
        await pilot.pause(0.2)
        box = app.screen.query_one("#report_issuers")
        assert box.value == ISSUER.key_text
        box.value = "nonsense"
        await press(pilot, app, "#ok")
        await pilot.pause(0.2)
        assert not got                                         # refused, the dialog stays
        box.value = f"{ISSUER.key_text} {other}"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert got[0]["report_issuers"] == [ISSUER.key_text, other]
        await pilot.press("q")
