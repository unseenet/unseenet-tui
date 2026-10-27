"""Tags (UN-RFC 0052) and content labels (UN-RFC 0053) in the terminal (#50).

Warnings are shown as ``▌spoiler`` in the warning's own colour - before the subject in
the threads list, as a line over the subject in the reader - always with the word and
who set it. A post you chose to blur shows *Ctrl+P: Show anyway* in place of its text;
one you chose to hide is a quiet line in the list. Nothing is covered silently, and your
own posts and your contacts' never are (the core decides, ``Message.labels``). Topics
are a grey line under the text. A rule's ``blur`` (UN-RFC 0051) covers a post the same.

Part of ``UnseenetApp`` (see app.py)."""

from __future__ import annotations

from rich.text import Text
from textual import work

from unseenet.core.tags import WARNINGS

# one colour per warning, as in the graphical client; the word is always there too
WARNING_STYLE = {"spoiler": "bold medium_purple", "profanity": "bold dark_goldenrod",
                 "violence": "bold red", "gore": "bold red", "sexual": "bold magenta",
                 "nudity": "bold magenta"}
STRIPE = "▌"


def warned_by(by) -> str:
    """Who set a warning, in words."""
    return ", ".join("the author" if b == "author" else "the group" if b == "group" else b for b in by)


class TagsMixin:
    # --- what to do with a post ---
    def label_state(self, msg) -> dict | None:
        """``{"warnings", "topics", "action"}`` for a post: the core's labels, a rule's
        blur, and Show anyway on this device."""
        labels = getattr(msg, "labels", None)
        res = getattr(self, "_scores", {}).get(getattr(msg, "message_id", None))
        ruled = bool(res is not None and "blur" in res.actions)
        if not labels and not ruled:
            return None
        action = labels["action"] if labels else "show"
        own = msg.author_key == self.session.identity().key_text if self.session else False
        if ruled and action == "show" and not own:
            action = "blur"
        if msg.message_id in getattr(self, "_labels_shown", set()):
            action = "show"
        return {"warnings": labels["warnings"] if labels else [], "topics": labels["topics"] if labels else [],
                "action": action, "ruled": ruled}

    def label_covered(self, msg) -> bool:
        """Whether a post is blurred or hidden right now (and not shown anyway)."""
        st = self.label_state(msg)
        return bool(st and st["action"] != "show")

    def warning_text(self, msg) -> Text | None:
        """``▌spoiler ▌gore  from the author, Filmfolk`` - the flags of a post."""
        st = self.label_state(msg)
        if not st or not st["warnings"]:
            return Text(f"{STRIPE}your rules", style="bold") if st and st["ruled"] and st["action"] != "show" else None
        line = Text()
        for w in st["warnings"]:
            line.append(f"{STRIPE}{w['warning']} ", style=WARNING_STYLE.get(w["warning"], "bold"))
        who = sorted({b for w in st["warnings"] for b in w["by"]}, key=lambda b: (b != "author", b != "group", b))
        line.append(f" from {warned_by(who)}", style="dim")
        return line

    def warning_stripe(self, msg) -> Text | None:
        """The stripe before a subject in the threads list."""
        st = self.label_state(msg)
        if not st or not st["warnings"]:
            return None
        w = st["warnings"][0]["warning"]
        return Text(STRIPE, style=WARNING_STYLE.get(w, "bold"))

    def hidden_text(self, msg) -> str:
        """The quiet line a post you chose to hide is: ``⊘ Hidden: gore, from Filmfolk``."""
        st = self.label_state(msg)
        if not st or st["action"] != "hide":
            return ""
        words = ", ".join(w["warning"] for w in st["warnings"]) or "your rules"
        who = warned_by(sorted({b for w in st["warnings"] for b in w["by"]}))
        return f"⊘ Hidden: {words}" + (f", from {who}" if who else "")

    def topics_line(self, msg) -> Text | None:
        """The author's tags, the group's and a labeller's topics, in one grey line."""
        own = [t for t in getattr(msg, "tags", ()) if t not in WARNINGS]
        st = self.label_state(msg)
        topics = [t for t in (st["topics"] if st else []) if t not in own]
        if not own and not topics:
            return None
        line = Text("Tags: ", style="dim")
        line.append("  ".join(own), style="dim")
        if topics:
            line.append(("   " if own else "") + "labelled " + "  ".join(topics), style="dim italic")
        return line

    def show_labelled(self, msg) -> bool:
        """Show a covered post anyway, on this device; True if it was covered."""
        if not self.label_covered(msg):
            return False
        self._labels_shown = getattr(self, "_labels_shown", set()) | {msg.message_id}
        self.refresh_view()
        return True

    # --- everything with a tag (UN-RFC 0052 section 7) ---
    @work(group="dialog", exit_on_error=False)
    async def action_tagged(self, tag: str = "") -> None:
        """Show every post with a tag in your groups and feeds: the selected post's tag
        (asked for when it has several), or one you write."""
        from .dialogs.base import InputDialog
        m = self.current_message
        tags = [t for t in getattr(m, "tags", ()) if t] if m else []
        if not tag:
            tag = await self.push_screen_wait(InputDialog(
                "Everything with a tag", "In your groups and feeds: the authors' tags, the groups', "
                "and topics of labellers you follow for topics.", "a tag, e.g. linux",
                tags[0] if tags else ""))
        tag = (tag or "").strip().lstrip("#").lower()
        if tag:
            await self.select_group("tag:" + tag)

    # --- the settings ---
    @work(group="dialog", exit_on_error=False)
    async def action_content_warnings(self) -> None:
        """Content warnings and labels: show, blur or hide per warning, and the labellers."""
        from .dialogs.tags import ContentWarningsDialog
        s = self.session
        got = await self.push_screen_wait(ContentWarningsDialog(s.warning_choices(), s.labellers()))
        if not got:
            return
        for w, c in got["choices"].items():
            if s.warning_choices().get(w) != c:
                s.set_warning_choice(w, c)
        for key in got["off"]:
            s.unfollow_labeller(key)
        if got.get("follow"):
            try:
                s.follow_labeller(got["follow"], got.get("label", ""), got.get("topics", False))
            except Exception as exc:  # noqa: BLE001  said, not raised
                self.notify(f"Not followed: {exc}", severity="error")
                return
            self.set_status("Reading the labeller ...")
            await s.refresh_labels()
            self.set_status("")
        self.refresh_view()
