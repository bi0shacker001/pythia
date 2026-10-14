import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "pythia" / "plugin_sources" / "downloader" / "steamcmd" / "plugin.py"


def load_module():
    spec = importlib.util.spec_from_file_location("test_steamcmd_source", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SteamCmdInstallerSecurityTests(unittest.TestCase):
    def test_oversized_download_is_rejected_before_body(self):
        module = load_module()

        class Response(io.BytesIO):
            headers = {"Content-Length": str(module.MAX_INSTALLER_DOWNLOAD + 1)}
            def geturl(self): return "https://example.invalid/steamcmd.zip"
            def __enter__(self): return self
            def __exit__(self, *args): self.close()

        with tempfile.TemporaryDirectory() as root, mock.patch.object(module.urllib.request, "urlopen", return_value=Response(b"")):
            with self.assertRaisesRegex(OSError, "2 GiB"):
                module._download_installer("https://example.invalid/steamcmd.zip", Path(root) / "part")

    def test_tar_expansion_limit_is_preflighted(self):
        module = load_module()
        member = mock.Mock(size=module.MAX_INSTALLER_DOWNLOAD + 1, name="huge")
        archive = mock.Mock()
        archive.getmembers.return_value = [member]
        with tempfile.TemporaryDirectory() as root, self.assertRaises(module.tarfile.TarError):
            module._safe_extract_tar(archive, Path(root))
        archive.extractall.assert_not_called()


if __name__ == "__main__":
    unittest.main()
