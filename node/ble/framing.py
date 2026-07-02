"""Packet framing shared with the app's BLE transport.

Wire format (must match meshlink-app lib/transport/ble_transport.dart):
each MeshLink packet is prefixed with a 2-byte big-endian length, then the
framed bytes are chunked to the ATT payload size. Chunk boundaries carry no
meaning — the receiver reassembles from the ordered byte stream.
"""
import struct

# Guard against a corrupt length prefix desyncing the stream forever.
# meshlink-core's MAX_PACKET is 460; anything larger is garbage.
MAX_FRAME = 460


def frame(packet: bytes) -> bytes:
    """Prefix a packet with its 2-byte big-endian length."""
    return struct.pack(">H", len(packet)) + packet


def chunk(framed: bytes, chunk_size: int) -> list[bytes]:
    """Split framed bytes into ATT-sized chunks."""
    return [framed[i:i + chunk_size] for i in range(0, len(framed), chunk_size)]


class FrameAssembler:
    """Reassembles complete packets from an ordered chunk stream (one per peer)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes; return every complete packet now available.

        Raises ValueError if the stream is corrupt (length prefix exceeds
        MAX_FRAME) — the caller should drop the buffer/connection.
        """
        self._buf.extend(data)
        packets = []
        while len(self._buf) >= 2:
            (length,) = struct.unpack_from(">H", self._buf)
            if length > MAX_FRAME:
                self._buf.clear()
                raise ValueError(f"frame length {length} exceeds {MAX_FRAME}")
            if len(self._buf) < 2 + length:
                break
            packets.append(bytes(self._buf[2:2 + length]))
            del self._buf[:2 + length]
        return packets

    def reset(self) -> None:
        self._buf.clear()
