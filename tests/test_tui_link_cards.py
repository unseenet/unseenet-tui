"""tui#102: link cards (UN-RFC 0059) as text - the domain from the link first, the site,
title and description; one line in a chat; the card's picture named among the files;
and the preview asked about before a post is sent."""
from datetime import datetime, timezone

from unseenet.core import cards as cd

from unseenet_tui.screens import attachments_text, card_text

BT = "https://www.bt.dk/krimi/ansat-i-politiet-snoed-fleggaard-nu-er-han-doemt"
CARD = cd.Card(url=BT, title="Ansat i politiet snød Fleggaard: Nu er han dømt",
               desc="Retten i Sønderborg har idømt en mellemleder i politiet en bødestraf.",
               site="B.T.", image="blob1:abc", made=datetime(2026, 10, 30, 12, 31, tzinfo=timezone.utc))


def test_a_card_in_the_read_view():
    text = card_text(CARD).plain.splitlines()
    assert text[0] == "┌ bt.dk · B.T."                         # the domain first, from the link
    assert text[1] == "│ Ansat i politiet snød Fleggaard: Nu er han dømt"
    assert text[2].startswith("│ Retten i Sønderborg")
    assert text[3].startswith("└ preview from the poster · 2026-10-30") and "picture" in text[3]


def test_a_card_in_a_chat_and_a_look_alike():
    assert card_text(CARD, chat=True).plain == "↳ bt.dk - " + CARD.title
    odd = cd.Card(url="https://bаnk.dk/", title="Log ind")
    assert "may look like another address" in card_text(odd).plain


def test_the_card_picture_among_the_files():
    text = attachments_text([{"blob": "blob1:abc", "type": "image/jpeg", "size": 8053,
                              "name": "card.jpg", "dim": [600, 315], "card": True}]).plain
    assert "card.jpg - picture, 8 KiB, 600×315  (the link preview's picture)" in text


def test_the_question_before_sending():
    from unseenet_tui.dialogs.base import CardDialog
    d = CardDialog(card_text(CARD).plain)
    assert d.AUTO_FOCUS == "#ok" and d.CANCEL_RESULT is None       # Esc: without the preview
