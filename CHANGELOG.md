# Changelog

All notable changes to unseenet-tui are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the
[Unseenet conventions](https://github.com/unseenet/.github/blob/main/CONVENTIONS.md#versions).

## [Unreleased]

- **Everything with a tag** (unseenet#672): `t` on a post, or *Everything with a tag* in
  the palette, shows every post with it in your groups and feeds, newest first.
- **Who reads on this device** (unseenet#674, UN-RFC 0053 6.1): an age guide in the
  palette sets what is blurred and hidden; nothing of the answer is kept, and it says it
  covers only what is marked.

- **Tags and content warnings** (#50, UN-RFC 0052, 0053): `▌spoiler` in the warning's own
  colour before the subject in the threads list and over it in the reader, with who set
  it; a post you chose to blur shows *Ctrl+P: Show anyway* in place of its text, one you
  chose to hide is a quiet line `⊘ Hidden: gore, from …` - never silently, never your
  own or your contacts'; a rule's `blur` the same. Tags and topics in a grey line under
  the text. A tags field when writing (`#words` in the text count too). *Content
  warnings and labels* in the palette; the group's tags in *Edit group* and *About*.

- **`!` for groups** (unseenet#666): `!dk.snak` names a group, and `!` on a post goes to
  it (was `#`); `#word` in what you write is a tag now.

- **Report reasons from the core's one list** (unseenet#659): *Threats or harassment*
  is `harassment`, readers can report *Phishing* and *Malware*.

- **Steadier tests** (#29): every click waits until its widget is laid out
  (`tuihelp.click`), and a widget read or set right after its dialog appeared waits
  for it to be drawn - on CI's slow Windows runners it often was not yet, and tests
  failed now and then. Also the page preview and the profile's Follow button.

- **Publishing a spam report says so**: the issuer's postage can take minutes; the
  status line says *Publishing …* until it is done. The manual's *Reporting a post*
  is now a whole section: hidden posts, issuers, reports that reach you, becoming an
  issuer, team inboxes.

- **Publish as the issuer beside you** (unseenet#654): *Publish a spam report* works
  with an issuer beside your own identity in the vault, and says as whom.

- **A report sent to you says what to do** (unseenet#650): *Ctrl+P Open reported post*,
  and for an issuer *Publish a spam report*; becoming an issuer asks where reports
  sent to you land - your inbox or a team inbox you read.

- **Report from a chat too** (asked for by the maintainer): in a chat, where no line is
  selected, *Report post* lets you choose the line, newest by others first; a
  group's *?* says how to report there and who acts on it.

- **PDFs in private posts and reports** (unseenet#637): every inbox takes PDFs now;
  the report window says *Screenshots or PDFs*, and saving a PDF warns that it can
  carry harmful content no node could check. PDFs are never shown in the terminal.

- **Team inboxes** (#13, unseenet#341, UN-RFC 0047), as in the graphical client: the
  post that adds you as a reader says *Ctrl+P: Read this inbox with the team*; a
  team inbox is listed under its name; `r` there answers as the team - "Reply as
  Unseenet (operator)" - without your signature; a message on an inbox's behalf
  shows the team, verified or not, never the person. A removed reader is told in
  the title, with *Forget this team inbox*. The owner has *Who reads this inbox*:
  untick to remove, add by key, the team's name.

- **Spam reports** (#11, unseenet#122, UN-RFC 0045), as in the graphical client: a
  post hidden by a report keeps its place and says so - *⊘ Hidden by Spamjægeren:
  spam* in the list, the reader and the chat, the count in the group's title - with
  *Show anyway* and *Stop following this report issuer* in the palette; never your
  own or a contact's. *Spam reports* in the palette: the issuers that hide posts for
  you (those your hierarchies name, on or off) and following one by its key. As an
  issuer: *Become a spam report issuer*, and *Publish a spam report* on a post or on
  a report sent to you (also its author, for up to a year).
- **A report window that is one** (as unseenet-gui#70): the post in short, what is
  wrong as a choice, who gets it, a real field for the note in Markdown, and
  screenshots by path, sent encrypted to each receiver.

- **The palette says what waits for the network**: while Unseenet is not
  connected yet, what needs it says *Waiting for the network* in the palette and,
  chosen, says so instead of failing - ready by itself once connected. What works
  offline (your rules, nodes, contacts, settings, writing) is as always. *Take
  back your shared profile* says "your" too.

- **Help in the rules editor** (unseenet#615): *Help* (or F1) shows the language
  in short - the core's, the same in every client.
- **"Your", as the client speaks to you**: *Your feed*, *Your site*, *Start your
  feed*, *Export your data*, *Show your key*, *Share your profile* - matching *For
  you* beside them; "my" stays only in your own words (*Use my rules*). The rule is
  in the core's *Write a client*.

- **Your own rules** (unseenet#615, UN-RFC 0051): *Your rules* in the palette -
  an editor checked as you type (the line and what is wrong), a suggestion shown
  when you have none (yours only if you save it), and *Use my rules* to turn them
  off and keep them. With rules, a group's threads and the timeline come as they
  say: hidden posts left out, folded ones with *▸ N folded*, dimmed and
  highlighted ones, and each row's score; *Why is this post here?*; *Watch*,
  *Ignore* and neither for a thread. Without rules the lists are as before.
  While your rules are on, what is shown is marked seen.

- **Emoji** (#6), as the graphical client has them: typing `:ta` in the chat
  box or a post offers up to 8 shortcodes under it (Tab or Enter takes one, Up and
  Down choose, Esc closes; Enter does not send while they are shown), and *Insert
  emoji* in the palette picks one by name or category. Both insert the
  `:shortcode:`, on the core's `find_emoji`.

- **Search engines and your site** (#5): a site's settings say whether search
  engines may index it (UN-RFC 0035 section 3), with the warning that their
  copies can outlive the pages - as in the graphical client.
- **Publish a folder as a site** (#19, on unseenet#538): Ctrl+P on a site you
  write in - the folder and whether pages no longer in it go, then what would be
  sent (+ ~ = - per page, and the changes made on the way), then Publish: only
  what changed is sent, with progress.

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
