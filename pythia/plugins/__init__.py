"""
Plugin architecture: five categories -- library, engine, downloader, source, mod --
each a real, capable interface (2026-08-02: "the APIs need to be VERY powerful," not
thin stubs), dynamically discovered and independently enable-able. Pythia is meant to
be shared/used well beyond one person's setup, so plugins are treated as real,
separately-distributable extension points, not an internal implementation detail:

- **A single plugin can span multiple categories** (2026-08-02: "a single plugin that
  can do downloads, manage library sources, handle mods, some combination of them").
  A plugin class can subclass more than one of the five base classes below (e.g.
  `class MyPlugin(DownloaderPlugin, ModPlugin)`); the registry in
  `pythia.plugins.registry` instantiates every plugin exactly *once* regardless of
  how many categories it implements, then fans that single instance into every
  matching category's filtered view via `isinstance`. This matters beyond just
  avoiding duplicate work: a multi-category plugin plausibly holds shared state (an
  authenticated session, a connection to one service) that must not be split across
  independent instances per category.
- **Dynamic discovery, built-ins included** (2026-08-06, adapted directly from
  Lutris's real runner loader, `lutris/runners/__init__.py`'s `__all__` +
  `import_runner()`): built-in plugins are not a separate, eagerly-imported path --
  `pythia.plugins.registry.BUILTIN_PLUGIN_MODULES` lists their module paths the same
  way Lutris's `__all__` lists runner names, and nothing is imported or instantiated
  until something actually asks for it. Third-party plugins are their own
  installable Python packages that declare an `importlib.metadata` entry point under
  the single `pythia.plugins` group -- one entry point per plugin, however many
  categories it spans, not one per category -- or a drop-in folder manifest under
  `app_data_dir()/plugins/`. All three paths converge on the same lazy class
  registry: a disabled plugin, built-in or third-party, is never instantiated at
  all -- "disabled" means "not loaded," not just "hidden" from a list. A broken or
  incompatible third-party plugin must never take the whole app down: discovery
  swallows and logs load errors per-plugin, it doesn't propagate them. The *only*
  thing that distinguishes a built-in from any other plugin is
  `registry.is_protected()`: its id can't be removed from the Plugins page (there's
  no install record to delete), but it can be disabled like anything else.
- **Opt-in enablement, not a blocklist**: `local.db` stores an `enabled_plugins`
  *allowlist* of plugin ids, not a disabled-list. An empty/unset allowlist means "no
  restriction -- everything discovered is enabled," which is both the sensible
  default (a fresh install just works with every built-in active) and an easy "debug
  mode": clearing the list re-enables everything at once. Enablement is per plugin
  id, not per category -- a multi-category plugin is on or off as a whole.
- **Multiple plugins per category is normal, not exceptional**: there can be more
  than one Steam library plugin, more than one downloader that targets Steam, a
  downloader that works with several different library kinds, etc. Every category's
  registry exposes a plural query (identify_all / find_for_kind / ...) as the real
  primitive; any "pick one winner" convenience sits on top of that, never the other
  way around.
- **Cross-plugin compatibility is declared data, not guesswork**: DownloaderPlugin
  declares which library `kind`s it can acquire for; EnginePlugin declares which
  `kind`s it's willing to refine. This is what lets the registry answer "which
  downloaders work with this Steam game" without hardcoding per-plugin knowledge
  anywhere else in the app.

See the Milestone 4 plan for the research (Lutris runner system, Playnite plugin/
extension system) behind these shapes.

**The "never launches anything" boundary is refined, not dropped (2026-08-02).**
Pythia still never executes a game's own binary directly -- that specific act stays
absolute, with one explicit, narrow exception (2026-08-03): a variant the user has
flagged **direct** (see `variant["direct"]`/`variant["executable"]` in the datadir
schema) needs no runner/compatibility layer at all to run -- most relevantly a
native Windows install that "just runs the executable directly," with nothing else
to configure. This is not the same as untracked: a direct variant keeps its normal
source/version/update/patch tracking exactly like any other variant -- "direct"
describes only how it launches, not whether Pythia manages it. For that specific,
user-declared case only, `pythia.plugins.manager.direct.DirectLaunchManagerPlugin`
runs the executable directly. Deliberately minimal: no runner selection, no
Wine/Proton flags, no process-lifecycle management of any kind -- exactly "run this
path," never more. Every other install continues to go through the two allowed
indirect paths below; this exception does not extend to them.

Two more things sit on the "allowed" side of that line, both grounded in what
Pythia actually is (an update/preparation manager, not a launcher):
- **Running a patcher/updater/installer tool that prepares a game to be ready to
  run** is enablement, the same category as `renpy_patch/patch.py` copying engine
  libs into place -- see `PatcherPlugin.apply()`. The distinction that matters: this
  runs a *preparation* tool (which does not itself play the game), not the game.
- **Handing off to a third-party library manager already pointed at a specific
  game** (e.g. opening `steam://rungameid/<appid>` so Steam itself launches its own
  game) is allowed -- Pythia opens a URI/command belonging to *that tool*, and that
  tool's own launch mechanism is what actually runs the game, not Pythia invoking the
  executable itself. See `LibraryPlugin.open_in_manager()`.
Reading binary files (not just filenames/paths) to determine identity -- version
resources, embedded strings, archive headers -- is also fair game for detection
(2026-08-02); nothing about "never launches" ever implied "never reads."
"""

from pathlib import Path

from pythia import credentials


class PluginMeta:
    """Metadata every plugin declares about itself, independent of what it does --
    mirrors Playnite's extension.yaml (Id/Name/Author/Version) so a plugin can be
    identified/enabled/disabled/version-checked without instantiating or running it.

    `config_schema` is a Lutris-style declarative option list (see the Milestone 4
    plan's Research findings): a list of {"key", "label", "type", "default"} dicts
    that simultaneously define what a plugin's settings persist *and* let a host UI
    (Milestone 5) auto-generate a settings form with zero per-plugin UI code. Empty
    by default -- most built-ins today need no configuration.

    `credential_fields` is the same declarative-schema idea, specialized for secrets
    (2026-08-03, added after ItchLibraryPlugin and SteamLibraryPlugin both grew an
    account-connect flow and the desktop Settings UI ended up hardcoding "itch.io"
    directly into a section title -- a real "we want pluggable modules" bug: any
    plugin with account-level ownership should get a real Settings row for free, not
    require bespoke UI code per plugin). A list of {"key", "label", "secret": bool}
    dicts; `get_credential`/`set_credential` below persist them generically via
    `pythia.credentials` -- **synced** through datadir/credentials/ (2026-08-03,
    changed from an earlier local.db-only design specifically because a login flow
    with real friction, GOG's OAuth dance in particular, would otherwise have to be
    repeated independently on every synced system, defeating the point of Pythia's
    whole cross-system model. See pythia/credentials.py's own docstring for the real,
    accepted tradeoff this involves -- synced secrets, no encryption at rest)."""

    id: str = "base"
    display_name: str = "Unnamed plugin"
    version: str = "0.0.0"
    author: str = "unknown"
    config_schema: list = []
    credential_fields: list = []

    def get_credential(self, key: str) -> str | None:
        return credentials.get_credential(self.id, key)

    def set_credential(self, key: str, value: str):
        credentials.set_credential(self.id, key, value)

    def get_config(self, key: str, default=None):
        """A stored value from this plugin's own generic per-plugin config
        (the same `plugin_config:<id>` storage backend.py's Configure UI
        already reads/writes for every config_schema field) -- how a
        plugin gets a value that may have been overridden by the user,
        without importing pythia.identity itself (2026-08-07: "Plugins
        need to be completely self-contained... They have their own
        configurations to give them what they need")."""
        import json

        from pythia.identity import get_setting

        raw = get_setting(f"plugin_config:{self.id}", None)
        if not raw:
            return default
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return default
        return data.get(key, default) if isinstance(data, dict) else default

    def set_config(self, key: str, value) -> None:
        import json

        from pythia.identity import get_setting, set_setting

        raw = get_setting(f"plugin_config:{self.id}", None)
        try:
            data = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        set_setting(f"plugin_config:{self.id}", json.dumps(data))

    def data_dir(self) -> Path:
        """A private, writable directory this plugin may use for its own
        on-disk state (a downloaded tool binary, extracted assets, a local
        session file) -- Pythia computes a real default; a `"data_dir"`
        entry in this plugin's own stored config overrides it through the
        exact same channel as any other config field, via the Configure
        UI. The plugin calls this and never knows (or needs to know) which
        case applied -- "the plugin will see that information... without
        knowing where it came from, because it doesn't matter" (2026-08-07)."""
        from pythia.identity import app_data_dir

        override = self.get_config("data_dir")
        d = Path(override) if override else app_data_dir() / "plugins" / "_data" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def is_connected(self) -> bool:
        """True once every declared credential field has a value. A plugin with no
        credential_fields at all is never "connected" in this sense (nothing to
        connect) -- distinct from list_owned() returning None, which specifically
        means "not connected yet" for a plugin that DOES have fields."""
        if not self.credential_fields:
            return False
        return all(self.get_credential(f["key"]) for f in self.credential_fields)


class LibraryPlugin(PluginMeta):
    """Detects/tracks games from an existing storefront/engine layout ("source" --
    Steam, GOG, Ubisoft, ...). Read-only against the library; never writes to a
    game's own files, never launches anything. Deliberately doesn't know how to hand
    a game off to a launcher either -- that's ManagerPlugin's job, kept separate on
    purpose (2026-08-02 correction): the source a game came from and the tool capable
    of running it are two different, independently pluggable things. Someone might
    own a Ubisoft game but launch it through Heroic instead of Ubisoft Connect, or a
    GOG game through Lutris instead of GOG Galaxy -- conflating "this is a Steam game"
    with "therefore the Steam client is what launches it" would make that
    unrepresentable."""

    def match(self, path: Path) -> dict | None:
        """Real, structural, path-based identification -- None means "not this
        plugin's kind of install." Default: None, not NotImplementedError
        (2026-08-03, a real bug caught by actually running onboarding against a
        fresh path, not just reviewing code: `identify_all()` calls `match()`
        unconditionally on every enabled library plugin with no try/except, and
        F95LibraryPlugin -- account/watchlist-only, no structural signature at all,
        see its own docstring -- never overrode this, so it inherited the old
        raise-by-default and crashed every single onboard/scan call once it was
        registered). A plugin that's genuinely list_owned()-only, like F95's,
        should never need to override this at all; the default now matches that
        case correctly instead of requiring every subclass to implement it."""
        return None

    def is_root(self, path: Path) -> bool:
        """Structural 'stop recursing here' rule for scanning.py's tree walk.
        Default: no rule of its own (a plugin with no structural marker only ever
        gets matched positionally by the caller, same as the original generic
        detector)."""
        return False

    def shortcut_roots(self, path: Path):
        """Optional optimization for a container this plugin recognizes by shape
        (e.g. a steamapps/ folder) where every relevant child is already known to be
        one of this plugin's roots, so scanning.py can skip walking into each
        child's internals looking for nested roots. Returns a list[Path] or None (no
        shortcut, fall back to normal recursion + is_root())."""
        return None

    def list_owned(self):
        """Enumerate every game this plugin knows about directly -- an account/API/
        manifest-level listing independent of walking a filesystem path at all (e.g.
        a Steam Web API or GOG Galaxy plugin could report owned titles the user
        hasn't even installed locally yet). None means "path-based detection only,"
        true of every built-in today (match()/is_root() are all they support)."""
        return None

    def refresh(self, game_root: Path) -> dict | None:
        """Re-derive this plugin's identification for an already-known root (e.g.
        after a manifest file changed on disk). Default: just re-runs match()."""
        return self.match(game_root)



class ManagerPlugin(PluginMeta):
    """Hands a game off to a launcher application capable of running it -- Steam
    client, Heroic, Lutris, Playnite, GOG Galaxy, ... (2026-08-02, split out of
    LibraryPlugin: the storefront a game came from and the tool that launches it are
    different, independently pluggable things -- see LibraryPlugin's docstring for
    why). Pythia opens a URI/command belonging to *that tool*; the tool's own launch
    mechanism is what actually runs the game, not Pythia invoking the executable
    itself (see the module docstring's "never launches anything" refinement)."""

    #: Which LibraryPlugin `kind`s (sources) this manager knows how to launch. "*"
    #: means source-agnostic. A manager can list several (e.g. Heroic covers both
    #: "epic" and "gog") -- this is what lets more than one manager legitimately
    #: apply to the same game, same "declared compatibility, not guesswork" pattern
    #: as DownloaderPlugin.compatible_library_kinds.
    compatible_sources: list = []

    def is_available(self, ctx) -> bool:
        """Is this manager application actually installed/usable on this system.
        Default False -- a manager plugin should only report True once it's actually
        confirmed the launcher is present, not assume it."""
        return False

    def open(self, detection: dict, ctx) -> bool:
        """Hand this game off to the manager (e.g. Steam's own
        "steam://rungameid/<appid>" URI). Returns whether the hand-off was attempted
        (not whether the user actually pressed play inside the other tool)."""
        raise NotImplementedError


class EnginePlugin(PluginMeta):
    """Engine enablement -- does three distinct jobs, not one (2026-08-02 correction:
    the Ren'Py ARM patcher is a small part of engine enablement, not the whole of it):

    1. detect() -- identification/refinement. A library plugin's job is *structural*
       boundary detection (where does an install start/stop, for scanning.py's tree
       walk); an engine plugin's detect() is what actually tells "kind: unknown" apart
       from "this is RPG Maker MV" or "this is kirikiri," refining the library
       plugin's initial guess rather than duplicating it.
    2. platform_status() -- what's actually needed for *this specific install* to
       work on each platform Pythia cares about, and whether Pythia itself knows how
       to close that gap.
    3. enable() -- attempts remediation only where platform_status() marked it
       fixable. NEVER executes or launches a game -- that boundary is absolute (see
       Milestone 1's plugin bullet) and explicitly excludes any Lutris-style
       play()/process-lifecycle methods.

    capabilities() is separate from platform_status(): it's system-level tooling
    state (is the thing this plugin depends on installed, and what version), not
    per-game-install state.
    """

    #: Which library-plugin `kind`s this engine plugin is willing to try against.
    #: Default None -- no restriction, always tried regardless of whatever
    #: distribution kind (if any) a library plugin already found (2026-08-03
    #: root-cause fix: engine identity and distribution identity are independent
    #: questions -- a Ren'Py game sold on Steam is both "steam" and "renpy" at once,
    #: so gating engine detection on "only if nothing else matched yet" was itself a
    #: bug, not just the original library-plugin kind collision it replaced). Set an
    #: explicit list only if a plugin genuinely needs to restrict itself to specific
    #: starting kinds.
    refines_kinds: list | None = None

    def is_root(self, path: Path) -> bool:
        """Structural 'stop recursing here' rule for scanning.py's tree walk, same
        role as LibraryPlugin.is_root() -- an engine can have its own structural
        marker (Ren'Py's renpy/+game/ folders) independent of any distribution
        source, so scanning.py consults both library and engine plugins for this
        (2026-08-03). Default: no marker of its own."""
        return False

    def shortcut_roots(self, path: Path):
        """See LibraryPlugin.shortcut_roots() -- same optional container-shape
        optimization, offered here too since an engine plugin can own is_root()."""
        return None

    def detect(self, game_root: Path) -> dict | None:
        raise NotImplementedError

    def platform_status(self, game_root: Path, ctx) -> dict:
        """{platform: {"supported": bool, "native": bool, "requires": [str, ...],
        "fixable": bool}} for every platform this plugin knows how to evaluate."""
        raise NotImplementedError

    def enable(self, game_root: Path, target_platforms: list, ctx) -> dict:
        raise NotImplementedError

    def capabilities(self, ctx) -> dict:
        """{"installed": bool, "version": str | None} -- the Lutris
        is_installed()+get_version() pattern, not a vaguer capability dict (see the
        Milestone 4 plan's Research findings)."""
        raise NotImplementedError


class DownloaderPlugin(PluginMeta):
    """Acquires game files. Two acquisition models, a plugin can support either or
    both: a GUI-launch-and-watch path (required -- see launch()) for tools with no
    scriptable interface (f95checker, GOG Galaxy), and an optional headless path
    (search/list_versions/get_download_info) for tools with a real CLI/API (SteamCMD,
    Legendary, lgogdownloader, butler) that Pythia can drive without opening a
    window. Never reimplements or embeds another tool's UI (see downloads.py)."""

    platforms: list = []

    #: Which LibraryPlugin `kind`s this downloader can acquire for. "*" means
    #: library-agnostic (e.g. f95checker isn't tied to any one storefront kind). A
    #: downloader can list several kinds (e.g. a unified store-frontend downloader
    #: that handles both "steam" and "gog") -- this is what lets the registry answer
    #: "which downloaders work with this game" without per-plugin special-casing
    #: anywhere else in the app.
    compatible_library_kinds: list = ["*"]

    def search(self, query: str) -> list:
        """Search this source for titles matching `query`. Empty-list default means
        "no search support, launch()-only tool" (true of GUI-only tools)."""
        return []

    def list_versions(self, item) -> list:
        """Every version/build/platform variant available for a previously-found
        `item` (from search(), or an already-tracked game's `source.extra`)."""
        return []

    def get_download_info(self, item) -> dict | None:
        """Resolve a concrete, headless-executable acquisition step for `item`
        (e.g. a steamcmd/legendary/butler command line, or a direct URL) without
        needing a GUI. None means this plugin can only be driven interactively via
        launch() -- true of every built-in today."""
        return None

    def launch(self, context: dict):
        """Launch this tool as its own window and watch for results (the GUI-driven
        acquisition path -- see downloads.py). Every downloader plugin must support
        at least this, even one that also supports the headless path above."""
        raise NotImplementedError

    def verify(self, path, expected) -> bool | None:
        """Checksum/integrity verification for a downloaded file, where supported.
        None means "not supported," not "failed" -- callers must not treat this as a
        verification failure."""
        return None


class MetadataPlugin(PluginMeta):
    """Pulls information about a game from a platform's public API -- what it's
    called, what platforms it supports, what DLC exists -- using nothing but a known
    `platform_ids` entry (`{"platform": ..., "id": ...}`, see the datadir schema v3
    note). Deliberately its own category, not part of LibraryPlugin or
    DownloaderPlugin (2026-08-03 correction: "metadata and downloads are handled
    completely separately," and metadata pulling is equally separate from local
    install *detection* -- you can look up a platform's public listing for a game
    regardless of whether you own it, have it installed, or even have a
    LibraryPlugin that recognizes that platform's local install shape at all). A
    single module can still implement both LibraryPlugin and MetadataPlugin if an
    author wants to (multi-category plugins are supported, see the registry), but
    they're independently enable-able -- turning off local Steam detection shouldn't
    have to turn off pulling Steam metadata, or vice versa."""

    def platform_support(self, platform_id: dict) -> list | None:
        """What platforms this game's storefront listing offers -- independent of
        what's actually installed locally, and independent of ownership: knowing the
        id is enough to ask "what does this platform say this game supports" even
        for one the user doesn't own it on. None means "not known" (no network
        reached, or this plugin has no data for this platform) -- callers must not
        treat that as "no platforms." """
        return None

    def list_dlc(self, platform_id: dict) -> list | None:
        """What DLC/expansions exist for this game on this platform -- existence
        only, not ownership: a public storefront API can report that DLC exists
        without any way to tell whether the user owns each piece, same limitation as
        platform_support(). Returns a list of {"id", "name"} dicts, or None if not
        known."""
        return None

    def resolve_name(self, platform_id: dict) -> str | None:
        """This platform's real display name for the game, purely from a known ID --
        added 2026-08-03 to close a real, stated fidelity gap: GogLibraryPlugin's
        list_owned() only ever returns bare product IDs (its account-ownership
        endpoint doesn't include names), unlike Steam's/Epic's, which do. None means
        "not known," same contract as the other methods here."""
        return None


class SourcePlugin(PluginMeta):
    """Generic on-demand fetch from something not tied to "a game" -- a mounted NAS
    share, a web interface, cloud storage. Interface only until a real source (the
    user's Nextcloud-hosted ROM collection, etc.) has actually been inspected."""

    def list_available(self):
        raise NotImplementedError

    def browse(self, path=None):
        """Hierarchical listing (e.g. a NAS folder tree) starting at `path` (None =
        root). Default falls back to the flat list_available() for sources with no
        real hierarchy."""
        return self.list_available()

    def search(self, query: str) -> list:
        return []

    def fetch(self, item, dest: Path, log):
        raise NotImplementedError


class PatcherPlugin(PluginMeta):
    """Detects/manages translation and other file-level patches applied on top of an
    already-identified game (2026-08-02: grounded in a real, on-disk example -- MGQ
    Paradox ships a bundled "One-Click Patcher" fan-translation tool that gets its
    own regular updates, tracked separately from mods/language).

    apply() is allowed to run a patcher/updater/installer tool (2026-08-02 revision):
    Pythia is an update manager whose job is making sure games are *ready to run* --
    running a preparation tool (which does not itself play the game) is squarely
    inside that job, the same category renpy_patch/patch.py's file-copy remediation
    already was, just no longer restricted to "copy files only." What stays absolute
    is the game itself: apply() runs the patcher, never the game."""

    def detect(self, game_root: Path) -> dict | None:
        raise NotImplementedError

    def check_updates(self, patch: dict) -> list:
        return []

    def fetch(self, patch: dict, dest: Path, log):
        raise NotImplementedError

    def apply(self, patch: dict, game_root: Path, ctx) -> dict:
        """Run this patch's own preparation tool against `game_root` (e.g. MGQ
        Paradox's "Start Patcher.bat"). Default: not supported -- a plugin that can
        only detect/track a patch without knowing how to actually run its installer
        is still legitimate (real per-patch apply mechanics need to be inspected
        before being implemented, same "don't fabricate" rule as everywhere else)."""
        raise NotImplementedError


class ModPlugin(PluginMeta):
    """Mod identification/acquisition -- same enablement-only boundary as
    EnginePlugin: identifies and fetches mod files, never applies or launches them.
    Exists because variants[].mods[] entries carry a nullable `version` field
    (Milestone 3) that nothing populates without a plugin like this.

    This is specifically an **installation** mod manager: it identifies and
    fetches files that get installed INTO the game's own folder on disk.
    See RuntimeModPlugin below for the other real shape "mod manager" takes
    in this project -- a tool that only modifies a running game in memory,
    never touching its files (2026-08-07, from an earlier, not-yet-coded
    plan: "while mod managers manage mods installed into the game
    (installation mods), these tools manage mods that exist only in memory
    (so, in runtime)"). `is_runtime` is how the two are told apart --
    checked, not just documented, because the two have a real different
    multiplicity rule: a game should have at most ONE installation mod
    manager active (two managers both writing into the same game folder
    could conflict), but as many runtime ones as it wishes (independent
    process-time wrappers don't share that conflict)."""

    #: False for a plain ModPlugin (installation-style); RuntimeModPlugin
    #: below overrides this to True. Checked by whatever enforces the
    #: at-most-one-installation-manager rule -- not just documentation.
    is_runtime: bool = False

    def detect(self, variant: dict) -> list:
        raise NotImplementedError

    def check_updates(self, mod: dict) -> list:
        raise NotImplementedError

    def list_available(self, game_context: dict) -> list:
        """Mods known to exist for a tracked game, not just ones already installed
        (mirrors DownloaderPlugin.search's spirit for mod-specific sources). Empty
        list default = nothing known -- a detect-only plugin isn't required to
        implement this."""
        return []

    def fetch(self, mod: dict, dest: Path, log):
        """Acquire a mod's files -- mirrors SourcePlugin.fetch, since mod
        acquisition is itself a fetch operation. Not every mod plugin needs to
        implement this (a plugin that only detects/version-checks, without being
        able to actually download, is legitimate) -- callers must check for
        NotImplementedError or a documented sentinel, same as any optional method
        here that still needs a real implementation to be useful."""
        raise NotImplementedError


class RuntimeModPlugin(ModPlugin):
    """A mod manager that only modifies a game while it's running, in
    memory -- never writes to the game's own files (e.g. locale emulation,
    or a Wand-style runtime patcher). A real subclass of ModPlugin, not a
    sibling: `categories_of_class()`'s issubclass check against
    CATEGORY_CLASSES["mod"] picks these up automatically, so they show up
    in the Mods capability pane like any other mod plugin, no registry
    changes needed -- `is_runtime` is what lets a caller tell the two
    shapes apart when the multiplicity rule (at most one installation
    manager, any number of runtime ones) actually matters.

    detect()/check_updates()/list_available()/fetch() from ModPlugin still
    apply if a runtime mod has real files of its own to identify/acquire
    (e.g. a locale profile database); wrap_launch() is the part that's
    actually new -- taking over how the game process gets created, instead
    of just tracking mod files."""

    is_runtime = True

    def wrap_launch(self, exe_path: Path, args: list, working_dir: Path, ctx):
        """Create (or take over creating) the game's own process, applying
        this runtime mod's real effect (e.g. injecting a locale-emulation
        hook DLL before the process's first instruction runs). Returns
        whatever real process handle/PID the caller needs to track the
        launched game. Multiple enabled runtime mods for the same instance
        are expected to chain -- exactly how isn't decided yet (real,
        not-yet-designed follow-up, not something to guess at here)."""
        raise NotImplementedError
