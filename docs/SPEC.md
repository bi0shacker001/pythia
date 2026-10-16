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
   same as droidtop-agent's own pairing. A computer can also pair with
   another computer.
6. **One entry per game across stores.** A game owned on several stores is
   one library entry with several store ids (Droidtop/tracker#133, droidtop
   SPEC 7m), not one entry per store.

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
- Up from the content's top reaches the strip.
- Down from the strip reaches the content.

Each zone remembers its place on each page, by item: a game, a settings
link or a button's own id. Coming back to a page, or to a zone, lands on the
item you left. Returning from a game to the library lands on that game's
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
