import asyncio
import tempfile
import unittest
from pathlib import Path

from pythia.core.sync.protocol import Frame, FrameDecoder, encode_frame, encode_json
from pythia.core.sync.transfer import TransferError
from pythia.core.sync.transfer_session import receive_game, send_game


class _MemoryConnection:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.peer = None

    async def send_frame(self, frame):
        wire = encode_frame(frame, relayed=False)
        decoded = FrameDecoder(relayed=False).feed(wire)
        await self.peer.incoming.put(decoded[0])

    async def send_json(self, message_type, value, *, stream_id=0, flags=0):
        decoded = FrameDecoder(relayed=False).feed(
            encode_json(message_type, value, stream_id=stream_id, flags=flags, relayed=False)
        )
        await self.peer.incoming.put(decoded[0])

    async def recv_frame(self, *, timeout=None):
        return await asyncio.wait_for(self.incoming.get(), timeout or 2)


def _pair():
    a, b = _MemoryConnection(), _MemoryConnection()
    a.peer, b.peer = b, a
    return a, b


class TransferSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_and_cache_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            source.mkdir()
            (source / "game.bin").write_bytes(b"abc" * 2_000_000)
            cache, destination = Path(td) / "cache", Path(td) / "destination"

            sender, receiver = _pair()
            sent, received = await asyncio.gather(
                send_game(sender, source, stream_id=7),
                receive_game(receiver, destination, cache, stream_id=7),
            )
            self.assertEqual((destination / "game.bin").read_bytes(), (source / "game.bin").read_bytes())
            self.assertEqual(sent["blocks_sent"], 2)
            self.assertEqual(received["blocks_received"], 2)

            sender, receiver = _pair()
            sent, received = await asyncio.gather(
                send_game(sender, source, stream_id=8),
                receive_game(receiver, Path(td) / "copy", cache, stream_id=8),
            )
            self.assertEqual(sent["blocks_sent"], 0)
            self.assertEqual(received["blocks_received"], 0)

    async def test_receiver_rejects_unrequested_block(self):
        sender, receiver = _pair()
        async def malicious_sender():
            from pythia.core.sync import protocol
            await sender.send_json(protocol.MessageType.MANIFEST, {
                "version": 1, "block_size": 4,
                "files": [{"path": "x", "size": 4, "blocks": ["0" * 64]}],
            }, stream_id=3)
            await sender.recv_frame()
            await sender.send_frame(Frame(protocol.MessageType.BLOCK_DATA, b"1" * 64 + b"evil", 3))
            return await sender.recv_frame()

        with tempfile.TemporaryDirectory() as td:
            results = await asyncio.gather(
                malicious_sender(), receive_game(receiver, Path(td) / "out", Path(td) / "cache", stream_id=3),
                return_exceptions=True,
            )
        self.assertIsInstance(results[1], TransferError)


if __name__ == "__main__":
    unittest.main()
