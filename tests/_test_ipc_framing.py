"""The backend channel's framing: a 4-byte big-endian length, then UTF-8 JSON (an object)."""

import json
import struct
from unittest import TestCase

from pythia.ipc import framing


class FramingTest(TestCase):
    def test_a_message_is_its_length_then_json(self):
        data = framing.encode({"id": 1, "method": "ping"})
        (length,) = struct.unpack(">I", data[:4])
        self.assertEqual(length, len(data) - 4)
        self.assertEqual(json.loads(data[4:]), {"id": 1, "method": "ping"})

    def test_text_is_utf8_not_escaped(self):
        data = framing.encode({"name": "Pokémon ゲーム"})
        self.assertIn("Pokémon ゲーム".encode(), data)
        self.assertEqual(framing.FrameDecoder().feed(data), [{"name": "Pokémon ゲーム"}])

    def test_messages_come_out_in_order_whatever_the_chunks(self):
        stream = b"".join(framing.encode({"n": n}) for n in range(5))
        for size in (1, 3, 7, len(stream)):
            decoder = framing.FrameDecoder()
            got = []
            for start in range(0, len(stream), size):
                got += decoder.feed(stream[start : start + size])
            self.assertEqual([m["n"] for m in got], [0, 1, 2, 3, 4], f"chunks of {size}")
            self.assertEqual(decoder.pending, 0)

    def test_half_a_message_waits(self):
        data = framing.encode({"a": 1})
        decoder = framing.FrameDecoder()
        self.assertEqual(decoder.feed(data[:-1]), [])
        self.assertEqual(decoder.pending, len(data) - 1)
        self.assertEqual(decoder.feed(data[-1:]), [{"a": 1}])

    def test_a_length_over_the_limit_is_refused_before_its_body_arrives(self):
        decoder = framing.FrameDecoder(max_frame=100)
        with self.assertRaises(framing.FrameError):
            decoder.feed(struct.pack(">I", 101))

    def test_an_empty_frame_is_refused(self):
        with self.assertRaises(framing.FrameError):
            framing.FrameDecoder().feed(struct.pack(">I", 0))

    def test_a_body_that_is_not_json_or_not_an_object_is_refused(self):
        for body in (b"not json", b"[1, 2]", b'"text"', b"\xff\xfe"):
            with self.subTest(body=body), self.assertRaises(framing.FrameError):
                framing.FrameDecoder().feed(struct.pack(">I", len(body)) + body)

    def test_a_message_over_the_limit_is_not_encoded(self):
        with self.assertRaises(framing.FrameError):
            framing.encode({"big": "x" * (framing.MAX_FRAME + 1)})

    def test_what_json_cannot_hold_becomes_text(self):
        from pathlib import PurePosixPath

        data = framing.encode({"path": PurePosixPath("/games/a")})
        self.assertEqual(framing.FrameDecoder().feed(data), [{"path": "/games/a"}])
