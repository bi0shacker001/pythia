import struct
import unittest

from pythia.core.sync.protocol import (
    HEADER,
    MAGIC,
    MAX_CONTROL_PAYLOAD,
    PROTOCOL_VERSION,
    Frame,
    FrameDecoder,
    MessageType,
    ProtocolError,
    encode_frame,
    encode_json,
)


class SyncProtocolTests(unittest.TestCase):
    def test_json_roundtrip_across_partial_chunks(self):
        wire = encode_json(MessageType.HELLO, {"name": "Living room"}, stream_id=7)
        decoder = FrameDecoder()
        frames = []
        for byte in wire:
            frames.extend(decoder.feed(bytes([byte])))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].stream_id, 7)
        self.assertEqual(frames[0].json(), {"name": "Living room"})

    def test_multiple_frames_in_one_chunk(self):
        wire = encode_json(MessageType.PING, {"nonce": 1}) + encode_json(
            MessageType.PONG, {"nonce": 1}
        )
        frames = FrameDecoder().feed(wire)
        self.assertEqual([frame.message_type for frame in frames], [MessageType.PING, MessageType.PONG])

    def test_relay_accepts_control_message(self):
        wire = encode_json(MessageType.TRANSFER_OFFER, {"bytes": 42}, relayed=True)
        self.assertEqual(FrameDecoder(relayed=True).feed(wire)[0].json()["bytes"], 42)

    def test_relay_encoder_rejects_every_bulk_message(self):
        for message_type in (
            MessageType.MANIFEST,
            MessageType.BLOCK_REQUEST,
            MessageType.BLOCK_DATA,
            MessageType.BLOCK_ACK,
            MessageType.TRANSFER_COMPLETE,
            MessageType.TRANSFER_CANCEL,
            MessageType.SAVE_DATA,
            MessageType.ARCHIVE_DATA,
        ):
            with self.subTest(message_type=message_type):
                with self.assertRaises(ProtocolError):
                    encode_frame(Frame(message_type, b"x"), relayed=True)

    def test_relay_decoder_rejects_forged_block_data(self):
        wire = HEADER.pack(MAGIC, PROTOCOL_VERSION, MessageType.BLOCK_DATA, 0, 1, 1) + b"x"
        with self.assertRaises(ProtocolError):
            FrameDecoder(relayed=True).feed(wire)

    def test_relay_rejects_oversized_declared_control_payload_before_buffering(self):
        wire = HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            MessageType.HELLO,
            0,
            0,
            MAX_CONTROL_PAYLOAD + 1,
        )
        with self.assertRaises(ProtocolError):
            FrameDecoder(relayed=True).feed(wire)

    def test_bad_magic_version_and_type_are_rejected(self):
        cases = (
            struct.pack("!4sBBHII", b"NOPE", PROTOCOL_VERSION, MessageType.PING, 0, 0, 0),
            HEADER.pack(MAGIC, 99, MessageType.PING, 0, 0, 0),
            HEADER.pack(MAGIC, PROTOCOL_VERSION, 255, 0, 0, 0),
        )
        for wire in cases:
            with self.subTest(wire=wire):
                with self.assertRaises(ProtocolError):
                    FrameDecoder().feed(wire)


if __name__ == "__main__":
    unittest.main()
