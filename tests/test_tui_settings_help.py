"""#83 (as unseenet-gui#178): Help in Edit group - the core's words for the settings the
dialog has, from F1 or the Help button, and back to the form as it was."""
import pytest

pytest.importorskip("textual")
pytest.importorskip("unseenet.client.settings_help")      # the core's help (unseenet#711)

from textual.widgets import Input, Static  # noqa: E402

from unseenet_tui.dialogs.groups import EditGroupDialog, SettingsHelpDialog  # noqa: E402

from test_tui_own_set import DATA, _app  # noqa: E402
from tuihelp import app_ready, press, stack, wait_for  # noqa: E402


async def test_help_for_the_settings_shown(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(EditGroupDialog(dict(DATA)))
        assert await wait_for(pilot, lambda: isinstance(app.screen, EditGroupDialog)), stack(app)
        await pilot.pause(0.2)
        edit = app.screen
        edit.query_one("#title", Input).value = "Nyt navn"
        await press(pilot, app, "#help")
        assert await wait_for(pilot, lambda: isinstance(app.screen, SettingsHelpDialog)), stack(app)
        text = str(app.screen.query_one("#settings-help Static", Static).render())
        assert "never change" in text and "Shown as" in text and "Affects:" in text
        assert "Team name" not in text                           # an open group has none
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: app.screen is edit), stack(app)
        assert edit.query_one("#title", Input).value == "Nyt navn"   # the form as it was
        await pilot.press("f1")                                  # F1 too
        assert await wait_for(pilot, lambda: isinstance(app.screen, SettingsHelpDialog)), stack(app)
        await pilot.press("escape")


def test_an_inbox_has_its_own():
    keys = EditGroupDialog({**DATA, "type": "inbox"}).help_keys
    assert "team_name" in keys and "tags" not in keys and "report_issuers" not in keys
