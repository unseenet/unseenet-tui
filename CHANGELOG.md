# Changelog

All notable changes to unseenet-tui are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the
[Unseenet conventions](https://github.com/unseenet/.github/blob/main/CONVENTIONS.md#versions).

## [Unreleased]

- **Docstrings** (#20, part 3): every public function and method of the
  terminal client says what it does (149 added), every file what belongs in it;
  `tests/test_docstrings.py` keeps it so, as the core's does. A Developers page
  with the map of the files.

- **UnseenetApp split into mixins by area** (#20, part 2): `app_groups.py`,
  `app_chat.py`, ... `app_pins.py`; `app.py` (547 lines) puts the class together.
  A pure move: the same 191 members.

- **app.py split by area** (#20, part 1): `helpers.py`, `screens.py` and
  `dialogs/` (base, setup, compose, people, groups, moderation, nodes); `app.py`
  keeps `UnseenetApp` and re-exports every moved name. A pure move: the same
  statements, each in one place.

- **Reactions in private chats and posts** (#9, on unseenet#513): the counts
  under private messages, `l` in a private chat (choosing among their lines) and
  on a private post, any emoji - where the core allows it, never on your own.

- **Export my data** (#14, on unseenet#577): Ctrl+P - your posts, comments,
  chat lines and pages as signed, their files, profile and groups, saved in
  Downloads; never your keys; private posts and chats only when chosen, and then
  the file encrypted with a passphrase typed twice.

- **Notifications** (#4): when something lands in For you - private messages,
  replies, mentions - from the core (`Session.notifications`): a desktop
  notification (OSC 9 / OSC 777) in terminals that show them, else the bell, and a
  line in the client. Asked once; it says only "N new in For you" unless you
  choose to show who and what (Ctrl+P); none for what is on screen; only while
  the client runs; never on the bootable client. The same setting as the GUI's.

- **Imported posts say where they were first published** (#3, UN-RFC 0049):
  `← Facebook` after the subject in threads and feeds, the whole "Imported from
  Facebook · originally 3 August 2026" on chat lines, and in the reader the
  time of posting with the origin as the poster's claim. Order, unread counts
  and For you come from the core.

**Needs the core's 0.6 functions** (unseenet#572, #573; tested against its main).

- **Recovery words** (#15, UN-RFC 0050 section 4): shown after a new identity
  is made, with a check of three words, and again with *Show recovery words*
  behind the passphrase; a reminder at most once a week until they are
  written down; *Save as file…* only on another disk than the vault and not in
  a cloud folder, never on the bootable client. *Restore from recovery words*
  at first start and in the palette, checking the words as they are typed,
  then listing the closed groups that did not come back and asking their
  owners to invite you again.
- **Copy to another device** (#15, UN-RFC 0050 section 3): choose identities and
  whether closed groups' keys go too, a QR code drawn in the terminal and the
  code with a countdown, then the two check words to confirm. *Get identity
  from another device* at first start and in the palette, by typed code.
- New dependency: `segno` (QR codes, pure Python).

## [0.1.0] - 2026-10-25

**For unseenet 0.5** (`unseenet>=0.5,<0.6`). The terminal client in its own
repository and package (unseenet#548), the same client that came with
unseenet 0.5 as `unseenet[tui]`, with its history.

- `pipx install unseenet-tui`, then `unseenet-tui` (needs Python 3.11+ and `tor`).
- `unseenet-tui --version` shows its own version and the core's.
- Its manual is still at [unseenet.org/client](https://unseenet.org/client/).
