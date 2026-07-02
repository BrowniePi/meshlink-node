import pytest

from node.ble.framing import MAX_FRAME, FrameAssembler, chunk, frame


def test_frame_prefixes_big_endian_length():
    assert frame(b"abc") == b"\x00\x03abc"


def test_chunk_splits_to_size():
    framed = frame(b"x" * 500)  # oversized for the protocol, fine for chunking math
    pieces = chunk(framed, 180)
    assert [len(p) for p in pieces] == [180, 180, 142]
    assert b"".join(pieces) == framed


def test_assembler_single_packet_single_chunk():
    a = FrameAssembler()
    assert a.feed(frame(b"hello")) == [b"hello"]


def test_assembler_packet_split_across_chunks():
    a = FrameAssembler()
    framed = frame(b"a" * 300)
    assert a.feed(framed[:100]) == []
    assert a.feed(framed[100:250]) == []
    assert a.feed(framed[250:]) == [b"a" * 300]


def test_assembler_multiple_packets_in_one_chunk():
    a = FrameAssembler()
    assert a.feed(frame(b"one") + frame(b"two")) == [b"one", b"two"]


def test_assembler_boundary_straddles_length_prefix():
    a = FrameAssembler()
    data = frame(b"one") + frame(b"two")
    assert a.feed(data[:6]) == [b"one"]  # second frame's prefix half-arrived
    assert a.feed(data[6:]) == [b"two"]


def test_assembler_rejects_oversized_frame_and_clears():
    a = FrameAssembler()
    with pytest.raises(ValueError):
        a.feed((MAX_FRAME + 1).to_bytes(2, "big") + b"junk")
    assert a.feed(frame(b"ok")) == [b"ok"]  # recovered after clear
