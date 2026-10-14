"""
Locale Remulator integration -- a real Windows-only runtime mod (2026-08-07:
"we're gonna include the localeremulator exe, at least to be used in the
background. Not the GUI, though"). Bundles InWILL/Locale_Remulator's own
real, LGPL-3.0 LRProc.exe + LRHookx32.dll + LRHookx64.dll + LRSubMenus.dll
verbatim (see assets/ alongside this file, packaged in by registry.py's
_package_builtins() via this module's sibling locale_remulator.json
manifest's "os_support": ["windows"]) -- see
locale_remulator_assets/LICENSE-LocaleRemulator.txt and
https://github.com/InWILL/Locale_Remulator for the real project this wraps.
Deliberately NOT the GUI (LREditor.exe/LRInstaller.exe) or the Explorer
shell integration (ServerRegistrationManager.exe/SharpShell.dll) -- LRProc.exe
is invoked directly, headless.

Real, verified mechanism (read from Locale_Remulator's actual real source,
not guessed -- see LRProc/LRProc.cpp and LRSubMenu/LRConfig.cs/LRProfile.cs
in the real repo): `LRProc.exe GUID Path Args` reads a profile {CodePage,
LCID, TimeZone, Bias, RunAsAdmin, HookIME, HookLCID} from LRConfig.xml
(looked up by GUID, same directory as LRProc.exe itself), then uses
Microsoft Detours' DetourCreateProcessWithDllExW to launch Path with the
matching-bitness hook DLL injected before its first instruction runs -- the
early-injection technique the hook DLL's own DllMain (DetourRestoreAfterWith)
specifically requires. PROFILES below copies the real default field values
verbatim from LRConfig.cs's own BuildDefaultConfigFile(); the GUIDs are ours
(fixed, generated once), not theirs, since we write our own LRConfig.xml
rather than depending on whatever a fresh LR install would generate.
"""

import subprocess
from pathlib import Path

from pythia.plugins import RuntimeModPlugin

PROFILES = {
    "ja-JP": {
        "guid": "8f3a2e1c-1c1a-4b1a-9b1a-000000000001",
        "name": "Run in Japanese",
        "location": "ja-JP",
        "codepage": 932,
        "lcid": 0x0411,
        "timezone": "Tokyo Standard Time",
        "bias": 540,
        "run_as_admin": False,
        "hook_ime": False,
        "hook_lcid": True,
    },
    "zh-TW": {
        "guid": "8f3a2e1c-1c1a-4b1a-9b1a-000000000002",
        "name": "Run in Taiwanese",
        "location": "zh-TW",
        "codepage": 950,
        "lcid": 0x0404,
        "timezone": "Taipei Standard Time",
        "bias": 480,
        "run_as_admin": False,
        "hook_ime": False,
        "hook_lcid": True,
    },
    "ko-KR": {
        "guid": "8f3a2e1c-1c1a-4b1a-9b1a-000000000003",
        "name": "Run in Korean",
        "location": "ko-KR",
        "codepage": 949,
        "lcid": 0x0412,
        "timezone": "Korea Standard Time",
        "bias": 540,
        "run_as_admin": False,
        "hook_ime": False,
        "hook_lcid": True,
    },
}
DEFAULT_PROFILE = "ja-JP"


class LocaleRemulatorPlugin(RuntimeModPlugin):
    id = "pythia.mod.locale_remulator"
    display_name = "Locale Remulator"
    version = "1.0.0"
    author = "pythia"

    def _extract_assets(self) -> Path:
        """The 4 real, bundled Locale Remulator files -- extracted once to a
        real on-disk cache. Necessary, narrow exception to "plugins are
        never unpacked": a native .exe cannot be executed directly from
        inside a zip the way this plugin's own Python code can (via
        zipimport) -- only binary tool assets go through this, never
        Python code. Idempotent: skipped once LRProc.exe is already there."""
        import zipfile

        from pythia.identity import app_data_dir

        cache_dir = app_data_dir() / "plugins" / "_extracted" / self.id
        if (cache_dir / "LRProc.exe").is_file():
            return cache_dir

        zip_path = Path(__loader__.archive)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name.startswith("assets/") and not name.endswith("/"):
                    rel = name[len("assets/") :]
                    (cache_dir / rel).write_bytes(zf.read(name))
        return cache_dir

    def _ensure_config(self, tool_dir: Path) -> None:
        """LRConfig.xml next to the extracted LRProc.exe -- its ConfigPath is
        always <its own exe directory>/LRConfig.xml (real, verified from
        LRConfig.cs), written in the exact schema its real WriteConfig()
        produces (bool.Parse() on their read side expects .NET's "True"/
        "False" capitalization, matched here), with our own fixed GUIDs so
        this module's own PROFILES dict stays the one source of truth for
        which GUID means which locale -- rewritten every call so editing
        PROFILES here always takes effect, not just on first extraction."""
        config_path = tool_dir / "LRConfig.xml"
        parts = ['<LRConfig><Profiles Type="exe">']
        for p in PROFILES.values():
            parts.append(
                f'<Profile Name="{p["name"]}" Guid="{p["guid"]}">'
                f'<Location>{p["location"]}</Location>'
                f'<CodePage>{p["codepage"]}</CodePage>'
                f'<LCID>{p["lcid"]}</LCID>'
                f'<TimeZone>{p["timezone"]}</TimeZone>'
                f'<Bias>{p["bias"]}</Bias>'
                f'<RunAsAdmin>{p["run_as_admin"]}</RunAsAdmin>'
                f'<HookIME>{p["hook_ime"]}</HookIME>'
                f'<HookLCID>{p["hook_lcid"]}</HookLCID>'
                "</Profile>"
            )
        parts.append("</Profiles></LRConfig>")
        config_path.write_text("".join(parts), encoding="utf-8")

    def wrap_launch(self, exe_path: Path, args: list, working_dir: Path, ctx):
        profile_key = DEFAULT_PROFILE
        if ctx and isinstance(ctx, dict):
            profile_key = ctx.get("config", {}).get("profile", DEFAULT_PROFILE)
        if profile_key not in PROFILES:
            profile_key = DEFAULT_PROFILE
        profile = PROFILES[profile_key]

        tool_dir = self._extract_assets()
        self._ensure_config(tool_dir)

        command = [str(tool_dir / "LRProc.exe"), profile["guid"], str(exe_path), *args]
        return subprocess.Popen(command, cwd=str(working_dir))


PLUGIN_CLASSES = [LocaleRemulatorPlugin]
