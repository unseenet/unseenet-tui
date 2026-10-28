"""#52: read more (UN-RFC 0031 3.2.1) in the terminal - the line put in while writing, and a
post shown whole without it."""

import pytest

pytest.importorskip("textual")

from unseenet.client import richtext  # noqa: E402
from unseenet.core import markup  # noqa: E402
from unseenet_tui.dialogs.compose import with_read_more  # noqa: E402


def test_the_line_goes_in_on_its_own():
    assert with_read_more("Indledning resten", 0, 10) == ("Indledning\n\n...\n\nresten", (4, 0))
    assert with_read_more("Først\n\nSå", 1, 0) == ("Først\n\n...\n\nSå", (4, 0))
    assert with_read_more("", 0, 0) == ("...\n\n", (2, 0))
    text, _ = with_read_more("Kort\nLangt", 1, 0)
    assert markup.read_more(text) == ("Kort", "Langt")


def test_shown_whole_without_the_line():
    shown = str(richtext.blocks(markup.body("Kort\n\n...\n\nResten", None)))
    assert "..." not in shown and "Resten" in shown
