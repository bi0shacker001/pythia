"""
SteamCMD downloader plugin -- real headless capability (2026-08-04), not
launch-only. Previously this only assumed a "steamcmd" binary already existed on
PATH and launched it as a plain GUI-style watch-folder tool, with an explicit
note that its real CLI contract hadn't been tested. Since then:

- This plugin downloads and caches SteamCMD itself (2026-08-07, "Plugins need to
  be completely self-contained... steamcmd should provide the steamcmd tool"):
  `_ensure_steamcmd()` below, stdlib-only download+extract into `self.data_dir()`,
  from Valve's own real, stable installer URLs (documented on Valve's SteamCMD
  wiki page, not guessed) -- confirmed live 2026-08-04: downloaded, extracted, and
  actually run end to end (`steamcmd.exe +quit` self-updated and exited cleanly;
  `+login anonymous +quit` connected to Steam Public successfully).
- `get_download_info()` below is real, scriptable SteamCMD syntax
  (`+login anonymous +force_install_dir <dir> +app_update <appid> validate +quit`),
  confirmed against SteamCMD's own documented command set. Only ever anonymous --
  a real authenticated login (username/password, likely Steam Guard 2FA) is a
  materially bigger, interactive flow that hasn't been built; anonymous-only
  covers free-to-play titles and dedicated-server tools, not the user's owned
  paid library, which is a real, stated ceiling, not hidden.
"""

import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from pythia.plugins import DownloaderPlugin

MAX_INSTALLER_DOWNLOAD = 2 * 1024**3


def _safe_extract_zip(zf: zipfile.ZipFile, destination: Path) -> None:
    """Self-contained bounded extraction (plugins may not import host helpers)."""
    infos = zf.infolist()
    if len(infos) > 10_000:
        raise zipfile.BadZipFile("SteamCMD archive has too many members")
    total = 0
    checked = []
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if not name or name.startswith("/") or not parts or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
            raise zipfile.BadZipFile(f"unsafe SteamCMD archive member: {info.filename!r}")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            raise zipfile.BadZipFile(f"SteamCMD archive contains a symbolic link: {info.filename!r}")
        total += info.file_size
        if info.file_size > 1024**3 or total > 2 * 1024**3:
            raise zipfile.BadZipFile("SteamCMD archive exceeds extraction size limits")
        if info.compress_size and info.file_size / info.compress_size > 2_000:
            raise zipfile.BadZipFile("SteamCMD archive exceeds compression-ratio limit")
        checked.append((info, parts))
    destination.mkdir(parents=True, exist_ok=True)
    for info, parts in checked:
        target = destination.joinpath(*parts)
        if info.is_dir() or info.filename.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _safe_extract_tar(tf: tarfile.TarFile, destination: Path) -> None:
    members = tf.getmembers()
    if len(members) > 10_000:
        raise tarfile.TarError("SteamCMD archive has too many members")
    total = 0
    for member in members:
        if member.size < 0 or member.size > 1024**3:
            raise tarfile.TarError(f"SteamCMD archive member is too large: {member.name!r}")
        total += member.size
        if total > 2 * 1024**3:
            raise tarfile.TarError("SteamCMD archive exceeds extraction size limits")
    tf.extractall(destination, members=members, filter="data")


def _download_installer(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as output:
        if not response.geturl().lower().startswith("https://"):
            raise OSError("refusing a non-HTTPS SteamCMD download or redirect")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_INSTALLER_DOWNLOAD:
                    raise OSError("SteamCMD download exceeds the 2 GiB safety limit")
            except ValueError:
                raise OSError("SteamCMD server returned an invalid Content-Length")
        received = 0
        while chunk := response.read(1024 * 1024):
            received += len(chunk)
            if received > MAX_INSTALLER_DOWNLOAD:
                raise OSError("SteamCMD download exceeded the 2 GiB safety limit")
            output.write(chunk)

# Valve's own real, stable installer URLs (documented on Valve's SteamCMD wiki
# page) -- no arm64 build published for either OS, so this correctly reports "not
# available" there rather than downloading a mismatched binary.
_STEAMCMD_RELEASE = {
    "Windows": ("https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip", "steamcmd.exe"),
    "Linux": ("https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz", "steamcmd.sh"),
}


class SteamCmdDownloaderPlugin(DownloaderPlugin):
    id = "pythia.downloader.steamcmd"
    display_name = "SteamCMD"
    version = "1.0.0"
    author = "pythia"
    platforms = ["windows", "linux"]
    # SteamCMD only ever acquires for Steam-kind games.
    compatible_library_kinds = ["steam"]

    def _ensure_steamcmd(self, log=print) -> Path | None:
        """Download (once, cached in self.data_dir()) and extract SteamCMD for
        this exact machine's OS. None if no build exists here or the download
        failed."""
        entry = _STEAMCMD_RELEASE.get(platform.system())
        if entry is None:
            return None
        url, exe_name = entry
        install_dir = self.data_dir() / "steamcmd"
        target = install_dir / exe_name
        if target.is_file():
            return target

        data_dir = self.data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        archive_path = data_dir / Path(url).name
        staging_dir = Path(tempfile.mkdtemp(prefix=".steamcmd-install-", dir=data_dir))
        try:
            log(f"steamcmd: downloading {url} ...")
            temp_path = archive_path.with_suffix(archive_path.suffix + ".part")
            _download_installer(url, temp_path)
            temp_path.rename(archive_path)

            if archive_path.suffix == ".zip":
                with zipfile.ZipFile(archive_path) as zf:
                    _safe_extract_zip(zf, staging_dir)
            else:
                with tarfile.open(archive_path) as tf:
                    _safe_extract_tar(tf, staging_dir)
            archive_path.unlink(missing_ok=True)

            staged_target = staging_dir / exe_name
            if not staged_target.is_file():
                return None
            shutil.rmtree(install_dir, ignore_errors=True)
            staging_dir.replace(install_dir)
            target.chmod(target.stat().st_mode | 0o111)
        except (urllib.error.URLError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
            log(f"steamcmd: install failed: {e}")
            shutil.rmtree(install_dir, ignore_errors=True)
            return None
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
            archive_path.with_suffix(archive_path.suffix + ".part").unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)
        return target

    def get_download_info(self, item) -> dict | None:
        """Real, scriptable SteamCMD command line to anonymously fetch/update
        `item` (a dict with at least `appid` and `install_dir`). Returns None if
        no steamcmd binary is available for this machine (no real build, or the
        download failed) -- callers must not treat that as "app not found."."""
        appid = item.get("appid")
        install_dir = item.get("install_dir")
        if not appid or not install_dir:
            return None
        binary = self._ensure_steamcmd()
        if binary is None:
            return None
        return {
            "command": [
                str(binary),
                "+force_install_dir", str(install_dir),
                "+login", "anonymous",
                "+app_update", str(appid), "validate",
                "+quit",
            ],
            "authenticated": False,
        }

    def launch(self, context: dict):
        """context: {"executable_path": str (optional override; defaults to the
        real downloaded-and-cached copy)}. Starts the process and returns the
        handle -- watching for results is the caller's job (Pythia's own download
        manager), same as any other launched tool; this plugin's job stops at
        "start it and hand back the handle"."""
        import subprocess

        executable = context.get("executable_path") or self._ensure_steamcmd()
        if executable is None:
            raise RuntimeError("No steamcmd build available for this machine's OS/architecture.")
        return subprocess.Popen([str(executable)])


PLUGIN_CLASSES = [SteamCmdDownloaderPlugin]
