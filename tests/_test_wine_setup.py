"""Set up Windows games on Linux (pythia.wine_setup; Droidtop/tracker#629): the
latest Wine build fetched into Pythia's runners folder and used by Play."""

import io
import shutil
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import folder_launch, wine_setup

RELEASE = {
    "tag_name": "10.17",
    "assets": [
        {"name": "wine-10.17-amd64-wow64.tar.xz", "browser_download_url": "https://x/wow64", "size": 1},
        {"name": "wine-10.17-amd64.tar.xz", "browser_download_url": "https://x/plain", "size": 2},
        {"name": "wine-10.17-staging-tkg-amd64.tar.xz", "browser_download_url": "https://x/tkg", "size": 3},
        {"name": "wine-10.17-staging-tkg-amd64-wow64.tar.xz", "browser_download_url": "https://x/tkg-wow", "size": 4},
    ],
}


def tarball(members: dict) -> bytes:
    """An .tar.xz holding ``members`` (name: bytes, or None for a folder)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class WineSetupTest(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        for patch in (
            mock.patch.object(wine_setup.settings, "WINE_DIR", str(self.dir / "wine")),
            mock.patch.object(wine_setup, "supported", return_value=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_staging_with_its_gaming_patches_is_picked_never_wow64(self):
        build = wine_setup.pick(RELEASE)
        self.assertEqual(
            build, {"name": "wine-10.17-staging-tkg-amd64", "url": "https://x/tkg", "size": 3, "version": "10.17"}
        )
        with self.assertRaises(wine_setup.SetupError):
            wine_setup.pick({"assets": [{"name": "notes.txt"}]})

    def test_a_build_is_fetched_unpacked_and_then_used_by_play(self):
        data = tarball(
            {"wine-10.17-staging-tkg-amd64/bin/wine": b"#!/bin/sh\n", "wine-10.17-staging-tkg-amd64/lib": None}
        )
        build = {"name": "wine-10.17-staging-tkg-amd64", "url": "https://x/tkg", "size": len(data), "version": "10.17"}
        seen = []
        with mock.patch.object(wine_setup.urllib.request, "urlopen", return_value=Response(data)):
            done = wine_setup.install(lambda d, t: seen.append((d, t)), build)
        wine = self.dir / "wine" / "wine-10.17-staging-tkg-amd64" / "bin" / "wine"
        self.assertEqual(done, {"name": "wine-10.17-staging-tkg-amd64", "wine": str(wine)})
        self.assertEqual(seen[-1], (len(data), len(data)))
        self.assertEqual(wine_setup.builds(), [done])
        self.assertEqual([p.name for p in (self.dir / "wine").iterdir()], ["wine-10.17-staging-tkg-amd64"])
        with mock.patch.object(folder_launch, "on_windows", return_value=False):
            self.assertEqual(folder_launch.wine(), str(wine))

    def test_an_archive_reaching_outside_its_folder_is_refused_and_leaves_nothing(self):
        data = tarball({"evil/bin/wine": b"x", "../../escape": b"x"})
        build = {"name": "evil", "url": "https://x/e", "size": len(data), "version": "1"}
        with mock.patch.object(wine_setup.urllib.request, "urlopen", return_value=Response(data)):
            with self.assertRaisesRegex(wine_setup.SetupError, "outside"):
                wine_setup.install(None, build)
        self.assertEqual(list((self.dir / "wine").iterdir()), [])
        self.assertFalse((self.dir / "escape").exists())

    def test_a_download_cut_short_is_refused(self):
        data = tarball({"w/bin/wine": b"x"})
        build = {"name": "w", "url": "https://x/w", "size": len(data) + 10, "version": "1"}
        with mock.patch.object(wine_setup.urllib.request, "urlopen", return_value=Response(data)):
            with self.assertRaisesRegex(wine_setup.SetupError, "cut short"):
                wine_setup.install(None, build)

    def test_without_wine_play_offers_the_setup(self):
        root = self.dir / "game"
        root.mkdir()
        (root / "Game.exe").write_bytes(b"MZ")
        with (
            mock.patch.object(folder_launch, "on_windows", return_value=False),
            mock.patch.object(folder_launch.shutil, "which", return_value=None),
        ):
            self.assertIsNone(folder_launch.pick(str(root), "Game"))
            self.assertEqual(folder_launch.why_not(str(root), "Game"), folder_launch.NO_WINE)
