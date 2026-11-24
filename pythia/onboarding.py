"""
onboard(path) -- identify a single game root and start (or continue) tracking it.

Split into pure detection/matching (find_candidates) and an explicit commit step
(onboard) driven by a `decision` -- interactive prompting belongs in cli.py or the
Qt UI's backend.py, not here. See the Milestone 1 plan for the full rationale.

2026-08-04 port onto the Lutris base: the two write sites (onboard(),
record_ownership()) now call pythia.database.games.save_entry_and_sync_cache()
instead of datadir.save_entry() + a separate pythia.cache.upsert_entry() --
that's the one shared "persist this entry, keep cache.db in sync" primitive
add_game()/update_existing() use too, so there's a single code path for
writing a datadir entry, not two that could drift apart. The plugin lookups
(pythia.plugins.engine/library/metadata) are still lazily imported here
because that tree hasn't been ported onto this base yet -- this module
imports and its pure detection-logic functions work standalone, but preview()/
onboard() themselves aren't actually usable until plugins/ lands.
"""

import difflib
import re
import shutil
import uuid
from pathlib import Path

from pythia import datadir
from pythia.core import instance_marker
from pythia.database import games as games_db
from pythia.datadir import classify_folder_tokens
from pythia.identity import get_system_uuid, new_game_id

NAME_SIMILARITY_THRESHOLD = 0.6

# How a folder name reads, as droidtop's GameNaming reads it (droidtop library-core GameNaming.derive and
# GameTitleParser; droidtop SPEC 7m). droidtop's naming began as a rewrite of this file's and has since been
# corrected against the same real library, so the rules below are its rules again, restated: the name a
# folder yields is the title every device keys the game by (`title2:` and its Lutris slug), and two programs
# that read one folder differently give one game two keys that never join. What droidtop reads past the
# name (mods, language, kind, flags) stays this file's own classify_folder_tokens, which is ahead there.

# A bare trailing number only counts as a version if it's explicitly marked as one
# (a "v", "ver" or "version" prefix) or dotted (contains a '.'/'_' between digit groups, e.g. "0.8.3" or
# "1_3_3") -- a bare integer like the "5"/"6" in "Far Cry 5"/"Far Cry 6" or the "2077"
# in "Cyberpunk 2077" is far more often a title/sequel number than a version, and
# treating it as one was silently merging unrelated games (2026-08-02 real-library
# finding). This won't catch every real case either (a bare build number like
# "Bunnycop-049" now just stays part of the name) -- best-effort, not exhaustive.
# A version's tail may be letters then digits ("0.8.09r1", "1.0b2", "2.0rc3"):
# mytop-project/tracker#538, "0.8.09r1" was cut to "0.8.09r" with "1" as a mod.
_NAME_VERSION_RE = re.compile(
    r"^(.*?)[-_ ](?:(?:v|(?i:ver(?:sion)?)\.?)(\d+(?:[._]\d+)*(?:[a-zA-Z]+\d*)?)"
    r"|(\d+[._]\d+(?:[._]\d+)*(?:[a-zA-Z]+\d*)?))(.*)$"
)

# A dotted version written straight onto a title, a capital V after a lower-case letter: "SomeGameV0.80".
_GLUED_VERSION_RE = re.compile(r"^(.*[a-z])V(\d+(?:\.\d+)+[a-zA-Z]?)$")

# A version straight after a character no English word goes on through (punctuation, another script):
# "タイトルv1.02". "ver" or "version" takes any number, a bare "v" only a dotted one.
_VERSION_AFTER_SYMBOL_RE = re.compile(
    r"^(.*[^A-Za-z0-9\s\-_.])(?:(?i:ver(?:sion)?)\.?(\d+(?:[._]\d+)*[a-zA-Z]?)|[vV](\d+(?:[._]\d+)+[a-zA-Z]?))$"
)

# The words a folder uses when it is a part of a game rather than a game: when a leaf folder is *just*
# one of these plus a number ("Week 1", "Part1", "Chap3+"), the real game name lives on a parent folder
# (2026-08-02 real-library case: BeingADik/Chap3+/10.0-sancho, Fetish Locator/Week 1/2/3, My New
# Family/Part 1, Thief of Hearts/Part1 all fragmented into separate bogus "games").
_LEAF_WORDS = r"chap(?:ter)?|chp|part|week|act|episode|ep|season|vol(?:ume)?|day|disc|disk|book|bk|pt"
_GENERIC_PART_PREFIX_RE = re.compile(rf"^({_LEAF_WORDS})\.?\s*", re.IGNORECASE)

# The part words where they END a name with a title in front ("ThiefofHeartsPart3"). Not "book": a flat
# "name_book1" beside "name_book2" is a numbered title, and those stay separate games.
_TRAILING_PART_RE = re.compile(
    r"[-_ .]?(chap(?:ter)?|chp|part|week|act|episode|ep|season|vol(?:ume)?|day|disc|disk)\.?\s*(\d+)$", re.IGNORECASE
)

# A roman numeral (capitals only) or a spelled-out number straight after a part word, at the end:
# "Act II" is "Act 2", "Book Three" is "Book 3".
_ROMAN_RE = re.compile(rf"^((?i:{_LEAF_WORDS})\.?\s*)([IVX]{{1,4}})\b(?=[\s\-_+.]*$)")
_WORD_NUMBER_RE = re.compile(
    rf"^((?i:{_LEAF_WORDS})\.?\s*)((?i:one|two|three|four|five|six|seven|eight|nine|ten))\b(?=[\s\-_+.]*$)"
)
_ROMAN_VALUES = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"]
_NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]

# Folder names that are an engine's own layout ("game" is Ren'Py's, "www" RPG Maker MV's) or the platform a
# build is filed under, never a game's title: the title is the game they sit in.
_ENGINE_FOLDER_NAMES = {
    "game", "data", "www", "resources", "resource", "res", "lib", "lib64", "libs", "renpy", "contents",
    "assets", "bin", "binaries", "content", "engine", "runtime", "app", "src", "js",
    "win", "win32", "win64", "x86", "x64", "windows", "linux", "mac", "macos", "pc",
    "build", "builds", "release", "releases", "dist",
}  # fmt: skip
# A build stage a release filed its folder under, in capitals and nothing else ("REC", "RTS").
_RELEASE_STAGE_FOLDERS = {"rec", "rts", "rtm"}

# Trailing tokens cut off a name: platform words after any separator, release tags after a dash,
# underscore or dot only ("Game_Name-win64-Final" is "Game_Name"; "Project Alpha" keeps its last word).
_TRAILING_TOKEN_RE = re.compile(r"([-_ .]+)([A-Za-z0-9]+)$")
_TRIM_PLATFORMS = {
    "pc": "pc", "win": "windows", "win32": "windows", "win64": "windows", "windows": "windows",
    "lin": "linux", "lin64": "linux", "linux": "linux", "mac": "mac", "osx": "mac", "macos": "mac",
    "android": "android", "apk": "android", "x86": None, "x64": None, "32bit": None, "64bit": None,
}  # fmt: skip
_TRIM_RELEASE_TAGS = {
    "final", "alpha", "beta", "demo", "repack", "gog", "steam", "epic", "itch", "prototype", "preview", "patched",
    "pre", "rec", "rts", "rtm",
}  # fmt: skip

_BRACKET_RE = re.compile(r"[\[({]([^\[\]{}()]*)[\])}]")
_VERSION_IN_BRACKET_RE = re.compile(r"[vV]?\d+(?:[._]\d+)+[a-zA-Z]?")


def _strip_noise(name: str):
    """``name`` without its bracketed groups ("[GOG]", "(Final)") and, for a name with no spaces, with the
    dots between words read as spaces ("Game.Name.v1.2-GROUP"); droidtop's GameTitleParser.stripNoise. A
    bracket holding a short number keeps it ("Game (2)"), a version stays the version ("[v1.2]"), and a
    year stays as it is. Returns the name and the bracketed text taken out, which still says the folder's
    kind or language ("(Spanish translation)")."""
    dropped: list[str] = []
    had_space = " " in name

    def replace(m):
        inner = m.group(1).strip()
        if re.fullmatch(r"\d{1,3}", inner):
            return f" {inner} "
        if _VERSION_IN_BRACKET_RE.fullmatch(inner):
            return " v" + inner.lstrip("vV") + " "
        if m.group(0).startswith("(") and re.fullmatch(r"(?:19|20)\d\d", inner):
            return f" ({inner}) "
        dropped.append(inner)
        return " "

    s = _BRACKET_RE.sub(replace, name) if any(c in name for c in "[({") else name
    if not had_space:
        s = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", " ", s)
    if s == name:
        return name, ""
    return re.sub(r"\s+", " ", s).strip(" -_.") or name, " ".join(dropped)


def _with_arabic(name: str) -> str:
    """``name`` with a roman numeral or spelled-out number after its part word in digits ("Act II" is
    "Act 2"); every other name unchanged."""
    for pattern, values, fold in ((_ROMAN_RE, _ROMAN_VALUES, False), (_WORD_NUMBER_RE, _NUMBER_WORDS, True)):
        m = pattern.search(name)
        if m:
            word = m.group(2).lower() if fold else m.group(2)
            if word in values:
                return name[: m.start(2)] + str(values.index(word) + 1) + name[m.end(2) :]
    return name


def _is_engine_folder(name: str) -> bool:
    lower = name.strip().lower()
    if lower in _ENGINE_FOLDER_NAMES:
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", lower) if w]
    return len(words) > 1 and all(w in _ENGINE_FOLDER_NAMES for w in words)


def _leaf_kind(raw: str) -> str:
    """What a folder name is when it is not a title: "engine" (the engine's own layout), "release" (a build
    stage), "version" (a bare dotted version, "10.0-sancho"), "part" (a part word and a number,
    "Week 1"), else "title"."""
    name = _with_arabic(_strip_noise(raw)[0])
    if _is_engine_folder(name):
        return "engine"
    if name.strip() == name.strip().upper() and name.strip().lower() in _RELEASE_STAGE_FOLDERS:
        return "release"
    if re.match(r"^\d+\.\d", name):
        return "version"
    stripped = _GENERIC_PART_PREFIX_RE.sub("", name, count=1)
    if stripped != name and re.match(r"^\W*\d", stripped):
        return "part"
    return "title"


def _is_generic_part_leaf(name: str) -> bool:
    """True if ``name`` is the structure of one game rather than a game: a part marker ("Chap3+",
    "Week 2") or a bare version ("12.0-scrappy"); droidtop's GameNaming.isStructuralFolderName."""
    return _leaf_kind(name) in ("part", "version")


def _find_meaningful_ancestor(path: Path, max_levels: int = 2):
    """The nearest ancestor of ``path`` that looks like a real title, walking up past part, version,
    engine and build-stage folders, and the first part folder passed on the way (the chapter in
    BeingADik/Chap3+/10.0-sancho). Bounded to two levels, so it never walks up into a library or engine
    root ("renpy"); consulted only when the leaf has no title of its own. None when there is none."""
    current = path
    part = None
    for _ in range(max_levels):
        current = current.parent
        name = current.name
        if not name:
            continue
        kind = _leaf_kind(name)
        if kind == "title":
            return _strip_noise(name)[0], part
        if kind == "part" and part is None:
            part = _strip_noise(name)[0].strip()
    return None


def _extract_version_only(leaf: str):
    """Pull version/mods/language out of a leaf folder name that's *just* a version
    or part marker (the real name comes from an ancestor folder instead -- see
    _find_meaningful_ancestor) -- unlike _derive_folder, there's no name prefix to
    strip here, and the generic part-word (if any) is stripped rather than kept as a
    mod tag."""
    stripped = _GENERIC_PART_PREFIX_RE.sub("", _with_arabic(leaf), count=1)
    m = re.search(r"(\d+(?:[._]\d+)*[a-zA-Z]*)", stripped)
    if not m:
        return stripped.strip() or leaf, classify_folder_tokens(stripped)
    version = m.group(1).replace("_", ".")
    rest = stripped[: m.start()] + " " + stripped[m.end() :]
    return version, classify_folder_tokens(rest)


def _split_part(leaf: str):
    """A part folder's part ("Part1", "Week 2", "Chap3+"), the version after it when
    there is a dotted one ("Chapter 3 v1.1" -> 1.1), and what else its name says."""
    m = re.match(r"^(.*?\d+\+?)(.*)$", _with_arabic(leaf))
    if not m:
        return leaf.strip(), "", classify_folder_tokens("")
    part, rest = m.group(1).strip(" -_"), m.group(2)
    if re.search(r"\d+[._]\d", rest):
        version, details = _extract_version_only(re.sub(r"^[vV](?=\d)", "", rest.strip(" -_")))
        return part, version, details
    return part, "", classify_folder_tokens(rest)


def _trim_trailing(name: str):
    """``name`` with trailing platform words and release tags cut off, the platform the first platform word
    named, and the words cut; droidtop's GameTitleParser.trimTrailing. The first word is never cut."""
    platform, cut, s = None, [], name
    while True:
        m = _TRAILING_TOKEN_RE.search(s)
        if not m:
            break
        separator, token = m.group(1), m.group(2)
        head = s[: m.start()]
        if not head.strip():
            break
        lower = token.lower()
        is_platform = lower in _TRIM_PLATFORMS
        hard = any(c in "-_." for c in separator)
        if not (is_platform or (hard and (lower in _TRIM_RELEASE_TAGS or re.fullmatch(r"multi\d*", lower)))):
            break
        if is_platform and platform is None:
            platform = _TRIM_PLATFORMS[lower]
        cut.insert(0, token)
        s = head
    return s, platform, cut


def _derive_name_version_mods_language(path: Path):
    """_derive_folder's name, version, mods and language."""
    name, version, details = _derive_folder(path)
    return name, version, details["mods"], details["language"]


def _derive_folder(path: Path):
    """What one folder's name says: the game's name, a version label, and ``details`` (mods, language,
    kind, flags from classify_folder_tokens; ``part``, the part of the game it is, or None; ``platform``
    and ``tags``, what came off the end of the name). Read as droidtop's GameNaming.derive reads it, so
    the two name a folder alike:

    1. A leaf that is a part marker ("Fetish Locator/Week 1") takes its name from the nearest titled
       ancestor, and the leaf is the part.
    2. A leaf that is a bare version ("BeingADik/Chap3+/10.0-sancho") takes the ancestor's name, is a
       version, and a part folder passed on the way is its part.
    3. A leaf that is an engine's own folder ("game", "www") or a build stage ("REC") is the game above it.
    4. Otherwise the leaf is a title, with a version, mods and a language after it, and possibly ending in
       a part marker of its own ("ThiefofHeartsPart3-0.0.9-pc" is part 3 of ThiefofHearts).

    Then platform words and release tags come off the end of the name ("Chubby_Story_Windows" is
    "Chubby_Story"). Best-effort, expected to misclassify sometimes; nothing here reads the disk."""
    # path.name can be "" for a path a real directory listing would never produce
    # (e.g. a leading "//" parses as a UNC-style anchor with no separate name
    # component on Windows); fall back to the whole path so a caller never gets an
    # empty name (2026-08-03, caught by the fuzz harness in tests/test_naming_fuzz.py).
    folder_name = path.name or str(path)
    leaf, dropped = _strip_noise(folder_name)
    name, version, details = _derive_untrimmed(path, folder_name, leaf, dropped)
    name = re.sub(r"\s{2,}", " ", name).strip()
    trimmed, platform, cut = _trim_trailing(name)
    if cut:
        name = trimmed
    details["platform"] = platform
    details["tags"] = cut
    return name, version, details


def _derive_untrimmed(path: Path, folder_name: str, leaf: str, dropped: str):
    kind = _leaf_kind(folder_name)
    if kind != "title":
        ancestor = _find_meaningful_ancestor(path)
        if ancestor is not None:
            ancestor_name, ancestor_part = ancestor
            if kind == "part":
                part, version, details = _split_part(leaf)
            elif kind == "version":
                version, details = _extract_version_only(leaf)
                part = ancestor_part
            else:
                version, details, part = "", classify_folder_tokens(dropped), ancestor_part
            details["part"] = part
            return ancestor_name, version, details

    m = _NAME_VERSION_RE.search(leaf)
    if m:
        name = m.group(1).strip(" -_") or leaf
        version = (m.group(2) or m.group(3)).replace("_", ".")
        details = {**classify_folder_tokens(m.group(4) + " " + dropped), "part": None}
    else:
        glued = _GLUED_VERSION_RE.search(leaf) or _VERSION_AFTER_SYMBOL_RE.search(leaf)
        if glued:
            name, version = glued.group(1), next(g for g in glued.groups()[1:] if g).replace("_", ".")
        else:
            name, version = leaf, ""
        details = {**classify_folder_tokens(dropped), "part": None}
    trailing = _TRAILING_PART_RE.search(name)
    if trailing:
        title = name[: trailing.start()].strip(" -_.")
        if title:
            name = title
            details["part"] = trailing.group(1)[0].upper() + trailing.group(1)[1:] + " " + trailing.group(2)
    return name, version, details


def _own_entries():
    system_uuid = get_system_uuid()
    return [e for e in datadir.list_all_entries() if e.get("system_uuid") == system_uuid]


def find_candidates(detection: dict, name_hint: str, path: Path):
    """Return {"certain": entry_or_None, "suggested": [entries]} -- plausible matches
    for `detection`.

    **Cross-system aware since 2026-08-03** (a real, previously-flagged foundational
    gap: this used to search only `_own_entries()`, so if System B already tracked a
    game, System A onboarding the same install independently would mint a duplicate
    game_id instead of recognizing System B's existing one -- defeating the whole
    point of a shared `datadir/`). External-ID and fuzzy-name matching now search
    every known system's entries (`datadir.list_all_entries()`), not just this
    system's own. Path matching stays own-only on purpose: an on-disk path is
    inherently system-local (different machines, different drive letters/mount
    points), so a path match from another system's file would be coincidence, not a
    real signal.

    **A `certain`/`suggested` entry may belong to a different system than this one.**
    Callers must never treat it as directly mutable/saveable -- see `onboard()`'s
    `_own_or_none()`/`_seed_from()` helpers, which turn a foreign match into a seed
    for a brand-new entry in *this* system's own file (same game_id, carried-over
    name/platform_ids/ownership) rather than ever writing to another system's file,
    which would violate the single-writer-per-file rule everything else in datadir/
    depends on."""
    own = _own_entries()
    all_entries = datadir.list_all_entries()
    path_str = str(Path(path).resolve())

    # Strongest possible signal: this exact on-disk path is already tracked under some
    # game *by this system*. Without this check, re-onboarding the same path (e.g.
    # re-running `scan`) would mint a brand new game every time instead of updating
    # the existing one.
    certain = None
    for entry in own:
        if any(
            variant.get("path") == path_str
            for group in entry.get("versions", [])
            for variant in group.get("variants", [])
        ):
            certain = entry
            break

    # Marker-file match (Milestone 9, 2026-08-04): real physical evidence this
    # exact folder was already onboarded by *another* system and synced here
    # by something outside Pythia (OneDrive, rsync, a NAS mount, ...) -- not a
    # guess from name similarity. Stronger than the external-ID check below,
    # since it's proof of the same synced bytes, not an inference from a
    # platform's own ID scheme. Absence of a marker is not negative evidence
    # (an install onboarded before this milestone, or hand-copied without
    # Pythia's involvement, simply has none yet) -- falls through to the
    # weaker tiers below exactly as before.
    if certain is None:
        marker = instance_marker.read_marker(path)
        if marker and marker.get("game_uuid"):
            certain = next((e for e in all_entries if e.get("game_uuid") == marker["game_uuid"]), None)

    external_id = detection.get("external_id")
    if certain is None and external_id is not None:
        kind = detection["kind"]
        # Cross-system, via platform_ids[] (2026-08-04 redesign -- game_id is now
        # always an internally-minted opaque ID, never a reused platform ID, so a
        # platform match has to look at platform_ids[] instead of comparing
        # game_id directly). Prefer this system's own record if it has one,
        # otherwise take the first match from any system -- if ANY system in the
        # sync group already claimed this external ID under some game_id, every
        # system should recognize and converge on it, not each independently mint
        # its own.
        matches = [
            e
            for e in all_entries
            if any(
                p.get("platform") == kind and str(p.get("id")) == str(external_id) for p in e.get("platform_ids", [])
            )
        ]
        this_uuid = get_system_uuid()
        certain = next((e for e in matches if e.get("system_uuid") == this_uuid), None) or (
            matches[0] if matches else None
        )

    # Part folders of one game (Thief of Hearts/Part1 + Part2, Fetish Locator/Week 1
    # + Week 3): the leaf is a bare part marker and a sibling part folder under the
    # same parent is already tracked by this system, so this is the same game,
    # not a name lookalike (2026-10-14 real-data finding: each part became its own
    # game, then the duplicate finder offered to fold them back together).
    if certain is None and _is_generic_part_leaf(Path(path).name):
        certain = _sibling_part_entry(own, Path(path_str))

    suggested = []
    if certain is None:
        target = name_hint.lower()
        seen_game_ids = set()
        scored = []
        for entry in all_entries:
            gid = entry.get("game_id")
            if gid in seen_game_ids:
                continue  # one suggestion per game_id, not one per system's copy of it
            ratio = difflib.SequenceMatcher(None, target, entry.get("canonical_name", "").lower()).ratio()
            if ratio >= NAME_SIMILARITY_THRESHOLD:
                scored.append((ratio, entry))
                seen_game_ids.add(gid)
        scored.sort(key=lambda t: t[0], reverse=True)
        suggested = [e for _, e in scored]

    return {"certain": certain, "suggested": suggested}


def _sibling_part_entry(own_entries: list, path: Path):
    """This system's entry that already tracks another part folder beside `path`
    (same parent, leaf also a bare part marker), or None."""
    parent = str(path.parent)
    for entry in own_entries:
        for group in entry.get("versions", []):
            for variant in group.get("variants", []):
                other = variant.get("path")
                if not other:
                    continue
                other = Path(other)
                if str(other.parent) == parent and _is_generic_part_leaf(other.name):
                    return entry
    return None


def entry_id_source(entry: dict) -> str:
    return entry.get("game_id_source", "local")


def preview(path: Path | str):
    """Identify `path` and find candidate existing games, without writing anything.
    This is what cli.py/the Qt UI's backend.py call before asking the user for a
    decision. Returns {"status": "ok", "path", "detection", "name", "version_label",
    "candidates"} or {"status": "error", "message": ...}.
    """
    from pythia.plugins import engine as engine_plugins  # noqa: PLC0415
    from pythia.plugins.library import identify, identify_all  # noqa: PLC0415

    path = Path(path).resolve()
    if not path.is_dir():
        return {"status": "error", "message": f"'{path}' does not exist or is not a directory."}

    detection = identify(path)
    if detection is None:
        return {"status": "error", "message": f"Could not identify '{path}' as any known kind of game."}

    # A library plugin only ever finds structural boundaries (where does an install
    # start/stop) -- it never claims engine identity itself, even one with its own
    # structural marker like Ren'Py (2026-08-03 root-cause fix: engine identity and
    # distribution identity are different, independent questions -- a Ren'Py game
    # sold on Steam is both "steam" and "renpy" at once, see plugins/library/
    # (renpy.py removed entirely) and EnginePlugin.refines_kinds's docstring for the
    # full history). So engine identification is *always* attempted here, regardless
    # of what -- if anything -- a library plugin already found; if an engine matches,
    # its kind wins as primary (more actionable for Pythia's purposes than a bare
    # distribution kind), and the original distribution detection is preserved via
    # additional_sources below rather than being merged/lost.
    _engine_plugin, engine_detection = engine_plugins.identify(path, current_kind=detection.get("kind"))
    if engine_detection is not None and engine_detection.get("kind") != detection.get("kind"):
        detection = {**detection, "kind": engine_detection["kind"]}
        if "platforms" in engine_detection:
            detection["platforms"] = engine_detection["platforms"]
        # Also reset: the prior kind's version_label (e.g. a Steam buildid) is a
        # meaningless number under the new engine kind, not a real game version --
        # left alone it would leak through as the displayed version. Cleared back to
        # None so the folder-name heuristic (which every engine plugin already
        # expects to supply the real game version) wins instead, same as an install
        # with no distribution-kind override at all.
        detection["version_label"] = None
        # Replaced, not merged: the prior kind's extra fields (e.g. a Steam AppID)
        # stay correctly scoped to that kind via additional_sources below, rather
        # than ending up mixed into an "extra" dict whose kind no longer matches them.
        detection["extra"] = {k: v for k, v in engine_detection.items() if k not in ("kind", "platforms")}

    # A path can legitimately match more than one library plugin at once -- e.g. a
    # Ren'Py game distributed via Steam matches both the Ren'Py and Steam plugins
    # (2026-08-03 real-library finding: Sunrider Academy). identify() only returns a
    # single winner for engine/version-label purposes, but any *other* match that
    # carries a real platform ID is still worth recording for ownership/platform_ids
    # -- otherwise a Steam-distributed Ren'Py game would silently lose its AppID
    # entirely just because Ren'Py's structural marker outranks Steam's for naming.
    additional_sources = [
        other
        for _plugin, other in identify_all(path)
        if other.get("kind") != detection.get("kind") and other.get("kind") in datadir.PLATFORM_ID_KINDS
    ]

    derived_name, derived_version, details = _derive_folder(path)
    mods, language = details["mods"], details["language"]
    name = detection.get("name") or derived_name
    # No version token anywhere means no version: None, never the folder name
    # (2026-10-14 real-data finding: 102 of 162 scanned games carried their own
    # name as their "version", which every front end then showed as a version).
    version_label = detection.get("version_label") or derived_version or None

    candidates = find_candidates(detection, name, path)

    return {
        "status": "ok",
        "path": path,
        "detection": detection,
        "additional_sources": additional_sources,
        "name": name,
        "version_label": version_label,
        "mods": mods,
        "language": language,
        "kind": details["kind"],
        "flags": details["flags"],
        "part": details.get("part"),
        "candidates": candidates,
    }


def onboard(path: Path | str, decision: dict | None = None):
    """Identify `path` and record it in this system's datadir. See the plan's
    Onboarding flow section for what `decision` accepts. Returns a result dict:
    {"status": "ok", "game_id": N, "candidates": {...}} or {"status": "error", "message": ...}
    """
    pv = preview(path)
    if pv["status"] != "ok":
        return pv
    path, detection, additional_sources, name, version_label, mods, language, candidates = (
        pv["path"],
        pv["detection"],
        pv["additional_sources"],
        pv["name"],
        pv["version_label"],
        pv["mods"],
        pv["language"],
        pv["candidates"],
    )

    action = (decision or {}).get("action")
    import_to_location = (decision or {}).get("import_to_location", False)
    this_uuid = get_system_uuid()
    # A match `find_candidates()`/a `link` decision points at may belong to a
    # *different* system (2026-08-03 cross-system fix -- see find_candidates()'s
    # docstring). `existing_entry` below is only ever set when it's genuinely this
    # system's own record, safe to mutate and save directly; a foreign match instead
    # populates `foreign_seed`, used further down to seed a brand-new entry in this
    # system's own file under the *same* game_id -- never writing to another
    # system's file, which would violate the single-writer-per-file rule.
    foreign_seed = None

    if action == "new":
        game_id, id_source, existing_entry = _mint_new(detection)
    elif action == "link":
        assert decision is not None  # action is "link" only when a decision says so
        game_id = decision["game_id"]
        id_source = "linked"
        existing_entry = _find_own_entry_by_game_id(game_id)
        if existing_entry is None:
            foreign_seed = _find_any_entry_by_game_id(game_id)
            if foreign_seed is None:
                return {
                    "status": "error",
                    "message": f"No existing tracked game with id {game_id} on any known system.",
                }
    else:
        # Automatic: certain match -> link, otherwise new. Used by scan_library() and
        # any caller that doesn't want to prompt.
        certain = candidates["certain"]
        if certain is not None:
            game_id = certain["game_id"]
            id_source = entry_id_source(certain)
            if certain.get("system_uuid") == this_uuid:
                existing_entry = certain
            else:
                existing_entry = None
                foreign_seed = certain
        else:
            game_id, id_source, existing_entry = _mint_new(detection)

    sibling_variant = None
    if existing_entry:
        for group in existing_entry.get("versions", []):
            variants = group.get("variants", [])
            if variants:
                sibling_variant = variants[0]
                break

    if action == "link" and import_to_location and sibling_variant is not None:
        sibling_parent = Path(sibling_variant["path"]).resolve().parent
        if path.parent != sibling_parent:
            new_path = sibling_parent / path.name
            shutil.move(str(path), str(new_path))
            path = new_path

    is_new_entry = existing_entry is None
    entry = existing_entry or {
        "system_uuid": this_uuid,
        "game_id": game_id,
        "game_id_source": id_source,
        # Milestone 9 (2026-08-04, game_uuid-only slice): a real UUID, distinct
        # from the collision-prone numeric game_id, that a marker-file match
        # (see pythia.core.instance_marker) can converge across systems even
        # when no external platform ID exists to converge on. Carried over
        # from a foreign_seed when linking to another system's already-known
        # game, never re-minted for an existing entry.
        "game_uuid": (foreign_seed or {}).get("game_uuid") or str(uuid.uuid4()),
        "canonical_name": (foreign_seed or {}).get("canonical_name") or name,
        # Cross-system seed carries over additively (2026-08-03) -- if another
        # system already knows this game's platform_ids/ownership, this system's
        # brand-new file starts with that context instead of losing it; still just
        # a starting point, not a live link back to the foreign entry.
        "platform_ids": list((foreign_seed or {}).get("platform_ids", [])),
        "ownership": list((foreign_seed or {}).get("ownership", [])),
        "managed_externally": False,
        "version_management_enabled": True,
        "notes": "",
        "versions": [],
        "available_updates": [],
    }
    entry["canonical_name"] = entry.get("canonical_name") or name
    entry["game_uuid"] = entry.get("game_uuid") or str(uuid.uuid4())  # lazy backfill for pre-Milestone-9 entries
    _update_platform_ids_and_ownership(entry, detection)
    for other in additional_sources:
        _update_platform_ids_and_ownership(entry, other)

    source = {"kind": detection["kind"], "extra": detection.get("extra", {})}
    is_new_variant_path = not any(
        variant.get("path") == str(path) for group in entry.get("versions", []) for variant in group.get("variants", [])
    )
    # Nothing is written into the game's folder: its identity is this entry
    # (game_uuid, and the variant's path) in Pythia's own data.
    _merge_version(
        entry,
        path,
        version_label,
        mods,
        language,
        detection.get("platforms", []),
        source,
        kind=pv.get("kind", "game"),
        flags=pv.get("flags", []),
        part=pv.get("part"),
    )

    if is_new_entry and foreign_seed is not None:
        datadir.add_timeline_event(
            entry, "linked_cross_system", f"{version_label or '?'} ({source['kind']}), matched another system's record"
        )
    elif is_new_entry:
        datadir.add_timeline_event(entry, "game_added", f"{version_label or '?'} ({source['kind']})")
    elif is_new_variant_path:
        datadir.add_timeline_event(entry, "version_added", f"{version_label or '?'} ({source['kind']})")

    saved_path = games_db.save_entry_and_sync_cache(entry)

    return {
        "status": "ok",
        "game_id": game_id,
        "candidates": candidates,
        "path": str(saved_path),  # the datadir JSON file this was recorded in
        "game_path": str(path),  # the actual game folder that was onboarded
        "name": entry["canonical_name"],
    }


def _mint_new(detection: dict):
    # 2026-08-04: every game gets a fresh, internally-minted ID -- no exceptions
    # for external-ID-sourced detections. A platform's own ID (Steam AppID, GOG
    # ID, ...) never becomes the internal game_id; it's recorded purely as
    # informational data in platform_ids[] via _update_platform_ids_and_ownership,
    # independently of this.
    return new_game_id(), "local", None


def _find_own_entry_by_game_id(game_id: str):
    for entry in _own_entries():
        if entry.get("game_id") == game_id:
            return entry
    return None


def _find_any_entry_by_game_id(game_id: str):
    """Cross-system lookup (2026-08-03, part of the same find_candidates() fix) --
    used when a `link` decision names a game_id this system doesn't have its own
    file for yet, but another system does. The caller must treat the result as a
    read-only seed, never mutate/save it directly (see onboard()'s own handling)."""
    for entry in datadir.list_all_entries():
        if entry.get("game_id") == game_id:
            return entry
    return None


def add_ownership(game_id: str, platform: str, note: str | None = None):
    """Record ownership of an already-tracked game on `platform` without any local
    install to detect it from (2026-08-03) -- real cases from the user's own library:
    a Patreon subscription with no local Patreon-branded files at all, or owning a
    game on itch.io without having installed it there (only the Steam copy is on
    disk). `_update_platform_ids_and_ownership()` only ever runs as a side effect of
    onboarding a real path; this is the explicit, no-install counterpart. Returns a
    result dict matching onboard()'s shape."""
    entry = _find_own_entry_by_game_id(game_id)
    if entry is None:
        return {"status": "error", "message": f"No existing tracked game with id {game_id} on this system."}

    ownership = entry.setdefault("ownership", [])
    existing = next((o for o in ownership if o.get("platform") == platform), None)
    if existing is not None:
        if note:
            existing["note"] = note
    else:
        record = {"platform": platform}
        if note:
            record["note"] = note
        ownership.append(record)

    games_db.save_entry_and_sync_cache(entry)

    return {"status": "ok", "game_id": game_id, "ownership": entry["ownership"]}


def _resolve_name_via_metadata(kind: str, detection: dict):
    """A real name-resolution fallback for services whose ownership listing has no
    name field at all (GOG's bare product IDs, see plugins/library/gog.py) -- tries
    every enabled MetadataPlugin willing to serve this platform (2026-08-03, closes
    a real, previously-stated fidelity gap). Best-effort: returns None (falls
    through to the existing "Unknown (...)" placeholder) if no metadata plugin has
    an answer, never raises."""
    id_key = datadir.PLATFORM_ID_KINDS.get(kind) if kind else None
    platform_id_value = detection.get("extra", {}).get(id_key) if id_key else None
    if platform_id_value is None:
        return None

    from pythia.plugins import metadata as metadata_plugins  # noqa: PLC0415

    for plugin in metadata_plugins.find_for_platform(kind):
        try:
            name = plugin.resolve_name({"platform": kind, "id": platform_id_value})
        except Exception:
            continue
        if name:
            return name
    return None


def record_ownership(detection: dict, own: list[dict] | None = None) -> tuple[dict, bool]:
    """Fold one ownership detection dict (the shape `list_owned()` produces) into
    this system's tracked games -- the single-item primitive `sync_owned()` loops
    over, factored out (2026-08-03) so a single-thread add (F95's browser-extension
    RPC `/games/add` handler, real logic ported from F95Checker's own
    `callbacks.add_games()`) can reuse the exact same matching/creation logic as a
    bulk account sync, instead of duplicating it. `own` lets a caller doing several
    adds in a row pass the same already-loaded list through repeatedly rather than
    re-reading every datadir file per call; omit it for a one-off call.

    Returns (entry, created) -- `created` is False when this detection matched an
    already-tracked game (additive update only), True when a new entry was minted."""
    own = _own_entries() if own is None else own
    kind = detection["kind"]
    external_id = detection.get("external_id")
    # The real identifying value isn't always the numeric external_id -- a service
    # like Epic Games Store identifies a game by a string AppName, kept in
    # extra[PLATFORM_ID_KINDS[kind]], with external_id always None (2026-08-03,
    # caught before it could duplicate every EGS game on every re-sync: the
    # platform_ids fallback below used to be gated on `external_id is not None`,
    # which a string-identified service can never satisfy).
    id_key = datadir.PLATFORM_ID_KINDS.get(kind) if kind else None
    string_id = detection.get("extra", {}).get(id_key) if id_key else None

    # 2026-08-04: game_id is never a reused platform ID anymore, so matching an
    # already-tracked game for this detection is *only* ever done via
    # platform_ids[] membership, regardless of whether the platform identifies by
    # a numeric external_id or a string_id.
    match_value = external_id if external_id is not None else string_id
    entry = None
    if match_value is not None:
        entry = next(
            (
                e
                for e in own
                if any(
                    p.get("platform") == kind and str(p.get("id")) == str(match_value)
                    for p in e.get("platform_ids", [])
                )
            ),
            None,
        )

    created = entry is None
    if entry is None:
        name = detection.get("name") or _resolve_name_via_metadata(kind, detection)
        entry = {
            "system_uuid": get_system_uuid(),
            "game_id": new_game_id(),
            "game_id_source": "local",
            "canonical_name": name or f"Unknown ({kind} {external_id})",
            "platform_ids": [],
            "ownership": [],
            "managed_externally": False,
            "version_management_enabled": True,
            "notes": "",
            "versions": [],
            "available_updates": [],
        }
        own.append(entry)
        datadir.add_timeline_event(entry, "ownership_recorded", f"via {kind}")

    _update_platform_ids_and_ownership(entry, detection)
    games_db.save_entry_and_sync_cache(entry)

    return entry, created


def sync_owned(plugin) -> dict:
    """Pull `plugin.list_owned()` (an account-level ownership listing, independent of
    any local install -- e.g. ItchLibraryPlugin's real API-key-based itch.io pull,
    2026-08-03) and fold it into this system's tracked games via record_ownership().
    Returns {"status": "ok", "linked": N, "created": N} or an error if the plugin has
    no real list_owned() implementation."""
    owned = plugin.list_owned()
    if owned is None:
        return {
            "status": "error",
            "message": f"'{plugin.id}' has no account connected (or doesn't support list_owned()).",
        }

    own = _own_entries()
    linked = 0
    created = 0
    for detection in owned:
        _entry, was_created = record_ownership(detection, own)
        if was_created:
            created += 1
        else:
            linked += 1

    return {"status": "ok", "linked": linked, "created": created}


def _update_platform_ids_and_ownership(entry: dict, detection: dict):
    """Record what this detection tells us about the game's identity on other
    platforms and the user's claim to it there -- deliberately two separate lists,
    not one (2026-08-03): `platform_ids` ("this game is known as X on platform Y") is
    useful for pulling metadata from that platform's public API even for a platform
    the user doesn't own the game on; `ownership` ("the user actually has a claim to
    it on platform Y") is seeded here from an install of that kind actually existing,
    which is strong evidence of ownership, but is conceptually a different fact --
    e.g. a game owned via a Patreon subscription but f95-downloaded has ownership
    with no platform_id at all. Additive only: a game already known to be owned on
    Steam stays so even after also onboarding an Epic install of the same game_id."""
    kind = detection.get("kind")
    extra = detection.get("extra") or {}
    id_key = datadir.PLATFORM_ID_KINDS.get(kind) if kind else None

    platform_ids = entry.setdefault("platform_ids", [])
    if id_key and extra.get(id_key) and not any(p.get("platform") == kind for p in platform_ids):
        platform_ids.append({"platform": kind, "id": extra[id_key]})

    ownership = entry.setdefault("ownership", [])
    if kind in datadir.PLATFORM_ID_KINDS and not any(o.get("platform") == kind for o in ownership):
        ownership.append({"platform": kind})


def _merge_version(
    entry: dict,
    path: Path,
    version: str,
    mods: list,
    language,
    platforms: list,
    source: dict,
    kind: str = "game",
    flags: list | None = None,
    part: str | None = None,
):
    """Find-or-create the `versions[]` group for `version`, then find-by-path within
    that group's `variants[]` (update in place) or append a new variant. `source`
    (what this specific on-disk install actually is -- see PLATFORM_ID_KINDS'
    docstring in datadir.py) lives per-variant, not on the shared game entry: two
    variants of the same game_id can legitimately have different sources (a Steam
    install and an Epic install of the same game are different on-disk copies)."""
    import datetime

    path_str = str(path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    versions = entry.setdefault("versions", [])
    group = next((v for v in versions if v.get("version") == version), None)
    if group is None:
        group = {"version": version, "variants": []}
        versions.append(group)

    for variant in group["variants"]:
        if variant.get("path") == path_str:
            variant["mods"] = mods
            variant["language"] = language
            # A kind the person set (set_variant_details) is theirs; a rescan only proposes.
            if not variant.get("kind_set_by_person"):
                variant["kind"] = kind
            variant["flags"] = list(flags or [])
            if part:
                variant["part"] = part
            variant["platforms"] = platforms
            variant["source"] = source
            variant["detected_at"] = now
            return

    group["variants"].append(
        {
            "id": new_game_id()[:12],
            "mods": mods,
            "language": language,
            "kind": kind,
            "flags": list(flags or []),
            **({"part": part} if part else {}),
            "path": path_str,
            "platforms": platforms,
            "source": source,
            "detected_at": now,
        }
    )
