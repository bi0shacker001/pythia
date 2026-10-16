# Pythia: specification

What Pythia does and how its surfaces behave, written down before they are
built. Each section names where it came from. The UX review is tracker
`coordination/pythia/ux-review-2026-10-16.md`. Its items are named A1, A2
and so on, and their implementation is described in the commit messages.

## 1. Decisions the owner made

These are the owner's answers to the UX review's owner calls, as relayed by
the droidtop coordinator for this lane. Later sections follow them.

1. **The primary slot.** A game page has one primary slot, first and
   largest.
   - It holds Play, Install or Update, whichever applies.
   - A local folder plays through Pythia's runners, and Steam comes first
     where a game has a Steam entry.
   - "Open in <launcher>" is an ordinary action, not the primary slot
     (A3, A12).
2. **Update buttons are never removed.** An update action that is not
   built yet stays visible and disabled, with its reason. "Open store page"
   sits beside it; opening a store page is a hand-off (section 3).
3. **droidtop's version model.** A version is a folder (droidtop SPEC 7m),
   with droidtop's segments. A game in several parts follows droidtop SPEC
   7n. Pythia uses this model and does not invent its own (A13, A16).
4. **No marker file.** Pythia never writes into a game's folder to
   recognise it later. An install's identity is its entry in Pythia's own
   data. Markers an earlier Pythia wrote are read and left alone.
5. **Pairing.** Pairing is mutual TLS through droidtop-agent's crates, the
   same as droidtop-agent's own pairing (Droidtop/tracker#556): each device
   has a self-signed certificate over its key, pairing pins the other's
   certificate fingerprint, and every session is mutual TLS 1.3 with those
   certificates. A computer can also pair with another computer (owner,
   2026-10-16: "Computer to computer pairing is ABSOLUTELY a pythia
   feature, it's the whole fucking reason it exists"). droidtop-agent's
   DESIGN.md section 3 and its crates are changed to match.
6. **One entry per game across stores.** A game owned on several stores is
   one library entry with several store ids (Droidtop/tracker#133, droidtop
   SPEC 7m), not one entry per store.
   - Which records are one game follows droidtop SPEC 7m, "What merges,
     and what only suggests": names equal once case and punctuation are
     dropped (droidtop's `GameNaming.nameKey`), or part folders side by
     side, are one game. Names at least 0.6 alike only suggest, one pair
     at a time. Review's Possible duplicates groups the first kind and
     lists each alike pair on its own, so a common word never chains
     unrelated games together (Droidtop/tracker#538, retest B2).
7. **Sync with droidtop starts on only where droidtop is in use.** Owner,
   2026-10-16, on the Windows retest's B3 (Droidtop/tracker#538): "Pythia
   isn't necessarily used with droidtop. It's a game library manager, I
   just happen to be taking advantage of it already knowing the library to
   make it a more convenient agent for droidtop". The owner also called
   serving at once "appropriate behavior for a production install" for a
   droidtop user. So:
   - On a new profile the switch starts on when droidtop-agent is already
     on this computer (it has its files, runs, or starts at sign-in), and
     Pythia then serves at first start: it listens on TCP and UDP 47610,
     answers discovery, announces this computer to the relay, and writes
     droidtop-agent's folder, because it is the agent. Otherwise the
     switch starts off. That first answer is kept as the setting.
   - While it is off Pythia does not listen on the network for handhelds,
     look for or answer them, announce this computer, or touch
     droidtop-agent's folder or its start at sign-in.
   - Turning it on says first, in plain words, what follows: the ports,
     the announcement to a public discovery server, and that
     droidtop-agent is turned off while Pythia serves. Serving starts at
     once.
   - Turning it off writes Pythia's state back into droidtop-agent's
     folder and puts the agent's start at sign-in back only when Pythia
     had taken the agent's place.
   - Every folder Pythia's native module uses comes from Pythia's own
     data folder or the system's folders as this process sees them, so a
     profile moved with PYTHIA_DATA_DIR, APPDATA and LOCALAPPDATA is never
     written outside.
   - Pairing a handheld from Pythia's Devices page is the person's own
     action, so it listens for that pairing only, while the page shows it.

## 2. The pad model (handheld shell)

From UX review A7 (HH2, HH10, HH11; conflicts C2 and C3). A4, A5 and A6
are its prerequisites: one Back, choices that change with the D-pad, and
sizes that scale with the screen.

### 2.1 Zones

The handheld shell has four zones:

- **Rail:** the page list on the left, and the Desktop switch.
- **Strip:** the top bar (Back, search, Add, Desktop) and the current
  page's own toolbar (view, filter and tabs).
- **Content:** the page itself.
- **Dialog:** an open dialog or menu. While one is open it is the only
  zone.

The D-pad moves focus within a zone, to the nearest item in that direction.
It crosses to another zone only at the zone's edge:

- Left from the content's or the strip's left edge reaches the rail.
- Right from the rail reaches the content.
- Up from the content's top reaches the strip item nearest above it: the
  page's own toolbar before the top bar.
- Down from the strip reaches the content item nearest below it.

"Nearest" is measured edge to edge, not centre to centre, so a narrow
choice under a full-width button is below it (Droidtop/tracker#538, retest
B1: Down from "‹ Settings" skipped every choice on Settings > Interface).

Each zone remembers its place on each page, by item: a game, a settings
link or a button's own id. Coming back to a page, or crossing to a zone
with Left or Right, lands on the item you left; Up and Down cross to the
nearest item, as above. Returning from a game to the library lands on that game's
row. When the page is redrawn underneath you, focus stays on the same item.

Entering the shell focuses the first content item, not the rail.

### 2.2 Buttons

Buttons are named by position, with Xbox labels (as on the GPD Win 4).

| Button | Action |
|---|---|
| A | Acts on the focused item. |
| B | Back (A4). With a dialog or menu open, closes it. Back never leaves the shell. |
| X | The focused item's action menu (A11), which holds Info. |
| Y | Search. |
| LB / RB | The previous or next entry in the page's strip: the library's source filter, the settings pages, or a game page's sections. |
| D-pad / left stick | Moves focus (2.1). A held direction repeats after 400 ms, then every 120 ms. On a choice row, Left and Right change the value (A5). |
| Start (Menu) | The Pythia menu: Activity, Save backups, Launcher entries, Duplicates, Add, the error console when its flag is on, Desktop, Quit. |
| Select (View) | Switches List and Grid where a page has both. |
| LT / RT | Reserved and unused, so they can later hold a Quick menu, as droidtop's R2 does. |

### 2.3 Hints

The footer names only the actions that do something on the current page.

- Back appears only when Back would move.
- Actions appears only when the focused item has a menu.
- LB / RB appears only where the page has a strip.
- View appears only where both views exist.

Each hint is also a button that does the same thing.

### 2.4 Dialogs and toasts

Dialogs keep the baseline: Cancel comes first and has focus, and Escape (B)
closes the dialog. Toasts never take focus and sit clear of the hint row.

### 2.5 Nothing hidden in hover

No meaning is carried only by `title` or `:hover`. In the handheld shell a
disabled control stays focusable. Its reason shows as an inline line of
text, and A does nothing on it.

### 2.6 Focus

There is one thick focus ring, scaled with the shell (A6), in its own
colour token (`--focus-ring`, distinct from the accent in every theme). It
shows on every focusable handheld element and wins over `.active`, so the
focused rail page is visible even when it is the current one.

### 2.7 Input

One backend service reads game controllers: `pythia._native.pad`, using
gilrs (XInput and Windows.Gaming.Input on Windows, evdev on Linux).

- It accepts Steam Input's virtual pad as well as the raw device. One press
  seen on both counts once (within 40 ms).
- It names each press and passes it to the shell (`pythia/pad.py`, the
  backend's `padAction`).
- It dispatches only while a Pythia window has the system's focus. A press
  while another app is in front is dropped, not queued. This includes a
  file manager or launcher Pythia handed off to.
- Keyboard and mouse keep working (the Win 4's mouse-emulation mode).
- The page does not also read the browser's Gamepad API.
- Without the native module, controllers are not read, and Settings says
  so.

## 3. Hand-offs

From UX review A3 (G6, conflict C4). A hand-off is any action that leaves
Pythia: Open folder, Open in <launcher>, a store page, a help link.

- In neither shell is a hand-off the first or default focus target.
- In the handheld shell every hand-off asks first, with Cancel focused,
  because the pad does not work in the program it opens.
- On desktop, Open folder does not ask. Open in <launcher> asks the first
  time for each launcher only.

## 4. First run

From UX review A19 (O4; conflict accepted from the newcomer review: the
first run never promises a sync that does not exist).

The first run is a short sequence of steps, shown once, before the shell
opens. In order:

1. **Start in.** Desktop, Handheld or Remember last (A8). The choice is
   already made by auto-detect (the firmware names a handheld, or a game
   controller is the only input), so the person confirms it rather than
   picks it.
2. **"Do you use droidtop on a handheld?"** Yes leads to pairing (section
   7); No and Not now go on. This step is shown only once pairing is built
   (UX review C1). Until then it is left out, and the step count does not
   include it.
3. **Add folders.** Folders and their roles (section 5). Adding a Library
   folder runs Look (A16): nothing is written until the person adds what
   was found.
4. **Connect sources.** Optional: store accounts (Steam, GOG, itch and the
   rest of the library plugins). Skip is as prominent as Connect.

It ends with what to do next: the Library, and Review when Look left
anything there (A18).

- **Back stays inside.** Back (B, Escape, ‹) goes to the previous step and
  never leaves the first run. On step 1 it does nothing.
- **Answered steps are skipped.** A step whose answer is already stored
  (Start in set in Settings, a folder already tracked, a handheld already
  paired) is not shown.
- **The count comes from the steps shown.** "Step 2 of 3" counts only the
  steps this run shows.
- Quitting part way keeps the answers given; the next start resumes at the
  first unanswered step.

## 5. Folders

From UX review A19 (O7).

Settings has one list of folders. Each folder has:

- **A role.** Library: games live here, and a new folder in it is offered
  through Look. Downloads: archives and installers arrive here, and each
  new file waits in Review's New downloads (A18).
- **"Run while watching"** (Downloads only, optional): a program Pythia
  starts with the watch and stops it with (a downloader such as
  F95Checker).

There is one watcher, and it serves both roles. It watches every folder
in the list, whatever the role, and stops when the list is empty or the
downloads manager is turned off.

**A folder that cannot be reached** (an unplugged drive, a network share
that is down) keeps its games. They show as Missing, as droidtop shows a
folder its walk lost (droidtop SPEC, the scan's "missing"). Nothing is
removed until the person removes it or the folder comes back without
them. The folder's row says it cannot be reached, and when it was last
seen.

## 6. Devices

From UX review A19 (AG2).

The Devices page (today "Systems & sync") lists every device this computer
knows. Each device has:

- **A name**, which the person can change on this computer.
- **A type**: a computer (another Pythia, or droidtop-agent; see the open
  point in 1.5) or a droidtop handheld.
- **Last sync**: when the two last exchanged saves or the library, or
  "Never".
- **Pending changes**: what is waiting to go either way, counted (saves,
  library changes).
- **Conflicts**: saves that differ on both sides since the last sync.
  Each conflict is also in Review's Sync conflicts (A18).
- **The device ID**, read-only, under Info. It is the device's PeerId (its
  public key, droidtop-agent docs/DESIGN.md section 3), with its pinned
  certificate fingerprint.

What syncs with a device is shown on that device's own page, generated
from the sync model. It says that game files never sync (UX review AG6).
Pairing is not released without that page (C1).

## 7. Pairing

From UX review A19 (AG5); the owner decision in 1.5. The same protocol is
written in droidtop's docs/SPEC.md (7o, "Pairing with Pythia"), in the
same change.

Pairing uses droidtop-agent's crates, so Pythia and droidtop-agent pair the
same way and share one identity and one trust list per computer
(droidtop-agent docs/DESIGN.md section 3). Every session is mutual TLS 1.3, each
side presenting its device certificate, and pairing is what pins the
other's certificate fingerprint (1.5; DESIGN.md sections 3 and 4). What follows adds a way to find
each other and a code to compare; the typed-code exchange that exists today
stays as the fallback.

### 7.1 Being found

- Pythia can be found on the local network while its Devices page is open
  or Sync with droidtop is on. At no other time does it answer.
- It answers droidtop's query for computers that can pair: a UDP broadcast
  on port 47610 with the magic `DTCQ1` and a fresh nonce. The answer,
  `DTCR1`, carries this computer's PeerId, its name, the pairing TCP port
  (47612) and the program (`pythia`). This mirrors the existing `DTPQ1` and
  `DTPR1` (the computer finding a handheld in pairing mode,
  `droidtop_agent_core::discovery`). Nothing in the answer is trusted; the
  code is what authenticates.
- droidtop lists each answer as "Pythia on <computer name>"
  (droidtop-agent's answers as its own name). droidtop chooses its own
  menu wording.

### 7.2 One code on both screens

When the person picks a computer on the handheld, the two compare a short
code (numeric comparison, as Bluetooth and ZRTP do):

1. The handheld connects to the pairing port and sends its hello: its
   PeerId and a commitment, SHA-256 over the label, its PeerId and a fresh
   32-byte random value.
2. The computer replies with its PeerId and its own fresh random value.
3. The handheld reveals its random value; the computer checks it against
   the commitment.
4. Both derive a 6-digit code: HKDF-SHA256 over the label, both PeerIds and
   both random values, reduced to 000000 to 999999. Both screens show it in
   large type.
5. The person confirms on both devices that the codes match. Each side
   then describes itself, `{name, kind, cert}` (`cert` its certificate's
   fingerprint, as in the typed exchange), with its identity's signature
   over the transcript (label, side, both PeerIds, both random values, the
   description's exact text). Each checks that the fingerprint is the one
   of the key that signed, and pins it.

The label is `droidtop-agent pair-confirm v1`. The commitment means neither
side can choose its value after seeing the other's, so a device in the
middle gets one chance in a million per attempt to match the two codes.
"They don't match" on either side ends the attempt on both. One attempt is
allowed per opening of the pairing screen.

### 7.3 Typing a code instead

The existing exchange stays, both ways, as the fallback: windowcast's
SPAKE2 exchange (`windowcast_pairing::exchange`, label `droidtop-agent pair
v3`, which pins certificate fingerprints the same way). Either the handheld shows a code and the person types it into
Pythia (Pair a handheld), or Pythia shows a code and its addresses and the
person types them into droidtop (Show a code; droidtop's "Use a code from
the computer"). Three wrong codes end the attempt.

### 7.4 Waiting, and when nothing comes

- While Pythia waits, the page says "Waiting for a handheld…" and how long
  it will wait.
- After 60 seconds with no handheld connecting, the waiting state ends with
  what to check: the handheld is on the same network as this computer, and
  droidtop is open on Pair a computer. It offers "Type a code instead"
  (7.3) and "Keep waiting".
- A wrong code, a mismatch, or the handheld leaving says which of these it
  was, in words.

### 7.5 The network, said first

Pythia listens for handhelds on TCP and UDP 47610 and TCP 47612 (and UDP
47611 away from home). The first time pairing or Sync with droidtop would
open them, Pythia says, before anything listens, that it needs to reach
the handheld over the home network and that the system may ask whether to
allow it, and to allow it on private networks. The system's prompt is
expected on Windows; this is inferred from how Windows treats a new
listening program and has not been verified on a test machine.

### 7.6 No camera

No step needs a camera. The QR code droidtop shows of its invitation is an
extra for a computer that has one, never the only way.
