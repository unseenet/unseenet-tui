"""tui#102: link cards (UN-RFC 0059) as text - the domain from the link first, the site,
title and description; one line in a chat; the card's picture named among the files;
and the preview asked about before a post is sent; made quietly for a chat line, small."""
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


class _Draft:
    def __init__(self, title):
        self.title, self.picture = title, object()


class _Session:
    def __init__(self, on=True, title="Ansat i politiet snød Fleggaard"):
        self.on, self.title, self.asked = on, title, []

    def link_cards(self):
        return self.on

    def card_url(self, text):
        return BT if BT in text else None

    async def make_card(self, url):
        self.asked.append(url)
        return _Draft(self.title)


def test_a_chat_line_gets_a_small_card():
    import asyncio
    from unseenet_tui.app_chat import chat_card
    s = _Session()
    draft = asyncio.run(chat_card(s, f"Se lige {BT}"))
    assert s.asked == [BT] and draft.title.startswith("Ansat") and draft.picture is None
    assert asyncio.run(chat_card(_Session(on=False), f"Se {BT}")) is None     # previews off
    assert asyncio.run(chat_card(_Session(title=""), f"Se {BT}")) is None      # no title: none
    s = _Session()
    assert asyncio.run(chat_card(s, "ingen link her")) is None and s.asked == []


def test_a_posts_own_picture_first_and_its_card_one_line():
    """UN-RFC 0059 section 8: the read view puts the post's own files first and the card
    as the one line a chat shows, after them."""
    import inspect
    from unseenet_tui import app_chat
    src = inspect.getsource(app_chat)
    assert "card_text(card, chat=True)" in src and src.index("attachments_text(msg.attachments)") < \
        src.index("card_text(card, chat=True)")
