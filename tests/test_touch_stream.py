"""Touch data stream decoding and source filtering."""
from __future__ import annotations

import asyncio

import pytest

from aionanoleaf2.events import NO_PANEL_ID, TouchStreamEvent
from aionanoleaf2.nanoleaf import (
    _NanoleafTouchProtocol,
    _normalize_address,
    parse_touch_stream,
)


def record(panel_id: int, touch_type: int, strength: int, swiped: int = NO_PANEL_ID) -> bytes:
    return (
        panel_id.to_bytes(2, "big")
        + bytes([(touch_type << 4) | strength])
        + swiped.to_bytes(2, "big")
    )


def packet(*records: bytes) -> bytes:
    return len(records).to_bytes(2, "big") + b"".join(records)


def test_single_panel() -> None:
    (event,) = parse_touch_stream(packet(record(100, 1, 3)))
    assert (event.panel_id, event.touch_type_id, event.strength) == (100, 1, 3)
    assert event.touch_type == "Down"
    assert event.panel_id_2 is None


@pytest.mark.parametrize("panel_id", [0, 1, 255, 12345, 40000, 65534])
def test_panel_id_round_trips(panel_id: int) -> None:
    """The old bit-string parser lost leading zero bits for small IDs."""
    (event,) = parse_touch_stream(packet(record(panel_id, 2, 5)))
    assert event.panel_id == panel_id
    assert event.strength == 5


def test_multiple_simultaneous_touches() -> None:
    """A packet announcing several panels must yield one event per panel."""
    events = parse_touch_stream(packet(record(100, 1, 3), record(200, 3, 5)))
    assert [(e.panel_id, e.touch_type, e.strength) for e in events] == [
        (100, "Down", 3),
        (200, "Up", 5),
    ]


def test_three_panels() -> None:
    events = parse_touch_stream(
        packet(record(10, 1, 1), record(20, 2, 2), record(30, 3, 3))
    )
    assert [e.panel_id for e in events] == [10, 20, 30]


def test_swipe_destination_panel() -> None:
    (event,) = parse_touch_stream(packet(record(10, 4, 2, swiped=77)))
    assert event.panel_id_2 == 77


def test_no_swipe_destination_is_none() -> None:
    """0xFFFF means "no destination"; the sentinel used to be written 2 ^ 16."""
    assert TouchStreamEvent(1, 1, 1, NO_PANEL_ID).panel_id_2 is None
    assert TouchStreamEvent(1, 1, 1, 0xFFFF).panel_id_2 is None
    assert TouchStreamEvent(1, 1, 1, 18).panel_id_2 == 18


def test_strength_and_type_are_nibbles() -> None:
    (event,) = parse_touch_stream(packet(record(1, 0xF, 0xF)))
    assert event.touch_type_id == 0xF
    assert event.strength == 0xF


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x01", b"\x00\x01\x00\x64"])
def test_short_and_truncated_packets(data: bytes) -> None:
    assert parse_touch_stream(data) == []


def test_extra_trailing_bytes_are_ignored() -> None:
    events = parse_touch_stream(packet(record(5, 1, 1)) + b"\xde\xad")
    assert len(events) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.168.1.28", "192.168.1.28"),
        ("[fe80::1]", "fe80::1"),
        ("fe80::1%eth0", "fe80::1"),
        ("fe80:0000::0001", "fe80::1"),
        ("::ffff:192.168.1.28", "192.168.1.28"),
        ("nanoleaf.local", None),
        ("", None),
    ],
)
def test_normalize_address(value: str, expected: str | None) -> None:
    assert _normalize_address(value) == expected


async def collect(allowed: frozenset[str], addr: tuple) -> list[TouchStreamEvent]:
    received: list[TouchStreamEvent] = []

    async def callback(event: TouchStreamEvent) -> None:
        received.append(event)

    protocol = _NanoleafTouchProtocol(allowed, callback)
    protocol.datagram_received(packet(record(9, 1, 1)), addr)
    await asyncio.sleep(0)
    return received


async def test_accepts_matching_ipv4_source() -> None:
    assert len(await collect(frozenset({"192.168.1.28"}), ("192.168.1.28", 60222))) == 1


async def test_rejects_foreign_source() -> None:
    assert await collect(frozenset({"192.168.1.28"}), ("10.0.0.5", 60222)) == []


async def test_accepts_ipv6_source_for_bracketed_host() -> None:
    """The host is stored bracketed for URLs; packets arrive bare."""
    allowed = frozenset({"fe80::1"})
    assert len(await collect(allowed, ("fe80::1", 60222, 0, 2))) == 1


async def test_accepts_zone_suffixed_source() -> None:
    assert len(await collect(frozenset({"fe80::1"}), ("fe80::1%eth0", 60222, 0, 2))) == 1


async def test_accepts_ipv4_mapped_source() -> None:
    """A dual-stack socket reports IPv4 peers as ::ffff:a.b.c.d."""
    allowed = frozenset({"192.168.1.28"})
    assert len(await collect(allowed, ("::ffff:192.168.1.28", 60222, 0, 0))) == 1


async def test_rejects_unparseable_source() -> None:
    assert await collect(frozenset({"192.168.1.28"}), ("not-an-ip", 1)) == []
