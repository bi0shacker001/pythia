"""Where the backend channel is, and who may reach it."""

import os
import stat
import tempfile
from pathlib import Path
from unittest import TestCase, mock, skipIf

from pythia.ipc import transport
from tests.ipc_support import Place

POSIX = os.name != "nt"


class AddressTest(TestCase):
    def test_the_address_is_this_persons_alone(self):
        where = transport.address()
        if POSIX:
            self.assertTrue(where.endswith("backend.sock"))
        else:
            self.assertTrue(where.startswith("\\\\.\\pipe\\pythia-backend-"))

    @skipIf(not POSIX, "Unix sockets")
    def test_the_runtime_dir_is_xdg_runtime_dir_when_there_is_one(self):
        env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(transport.runtime_dir(), Path("/run/user/1000/pythia"))

    def test_an_override_wins(self):
        with mock.patch.dict(os.environ, {"PYTHIA_RUNTIME_DIR": "/somewhere"}):
            self.assertEqual(transport.runtime_dir(), Path("/somewhere"))

    def test_a_test_data_dir_keeps_the_channel_inside_it(self):
        env = {"PYTHIA_DATA_DIR": "/data/p"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(transport.runtime_dir(), Path("/data/p/run"))


class ListenTest(TestCase):
    def setUp(self):
        self.place = Place()
        self.addCleanup(self.place.cleanup)

    def test_a_second_listener_on_a_served_address_is_turned_away(self):
        first = transport.listen(self.place.where)
        self.addCleanup(first.close)
        with self.assertRaises(transport.AddressInUse):
            transport.listen(self.place.where)

    def test_connecting_to_nothing_fails_as_an_os_error(self):
        with self.assertRaises(OSError):
            transport.connect(self.place.where, timeout=1)

    @skipIf(not POSIX, "Unix permissions")
    def test_the_socket_is_for_this_user_only_in_a_folder_for_this_user_only(self):
        listener = transport.listen(self.place.where)
        self.addCleanup(listener.close)
        self.assertEqual(stat.S_IMODE(os.stat(self.place.where).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.place.where).parent).st_mode), 0o700)
        self.assertTrue(stat.S_ISSOCK(os.stat(self.place.where).st_mode))

    @skipIf(not POSIX, "Unix permissions")
    def test_a_folder_others_could_enter_is_closed_to_them(self):
        folder = Path(self.place.where).parent
        folder.mkdir(parents=True)
        folder.chmod(0o755)
        listener = transport.listen(self.place.where)
        self.addCleanup(listener.close)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    @skipIf(not POSIX, "Unix permissions")
    def test_a_folder_of_another_account_is_refused(self):
        folder = Path(self.place.where).parent
        with mock.patch("pythia.ipc.transport.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(transport.TransportError):
                transport.listen(str(folder / "b.sock"))

    @skipIf(not POSIX, "Unix sockets")
    def test_a_socket_left_by_a_pythia_that_did_not_end_cleanly_is_replaced(self):
        import socket

        folder = Path(self.place.where).parent
        folder.mkdir(parents=True, mode=0o700)
        dead = socket.socket(socket.AF_UNIX)
        dead.bind(self.place.where)
        dead.close()  # the file stays, nothing listens
        self.assertTrue(Path(self.place.where).exists())
        listener = transport.listen(self.place.where)
        self.addCleanup(listener.close)
        transport.connect(self.place.where).close()

    @skipIf(not POSIX, "Unix sockets")
    def test_closing_removes_the_socket_file(self):
        listener = transport.listen(self.place.where)
        listener.close()
        self.assertFalse(Path(self.place.where).exists())

    @skipIf(not POSIX, "Unix sockets")
    def test_a_path_too_long_for_a_socket_says_so(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            with self.assertRaises(transport.TransportError) as caught:
                transport.listen(os.path.join(folder, "x" * 120, "b.sock"))
        self.assertIn("PYTHIA_RUNTIME_DIR", str(caught.exception))
