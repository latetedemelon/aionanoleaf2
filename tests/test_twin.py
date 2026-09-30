"""Per-panel control through DigitalTwin."""
from __future__ import annotations

import asyncio
import struct

import pytest

from aionanoleaf2 import (
    DigitalTwin,
    NanoleafException,
    StreamingUnsupported,
    Unavailable,
    UnknownPanel,
)
from aionanoleaf2.twin import STREAM_PORT_V2, _coerce_color, _transition_units

RECORD = struct.Struct(">HBBBBH")


def decode(frame: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """Decode an external control v2 frame into its panel records."""
    (count,) = struct.unpack_from(">H", frame, 0)
    offset = 2
    records = []
    for _ in range(count):
        records.append(RECORD.unpack_from(frame, offset))
        offset += RECORD.size
    assert offset == len(frame), "frame has trailing bytes"
    return records


# --------------------------------------------------------------------------
# Colour and transition coercion
# --------------------------------------------------------------------------

def test_rgb_gets_a_zero_white_channel() -> None:
    assert _coerce_color((1, 2, 3)) == (1, 2, 3, 0)


def test_rgbw_passes_through() -> None:
    assert _coerce_color((1, 2, 3, 4)) == (1, 2, 3, 4)


@pytest.mark.parametrize("color", [(1, 2), (1, 2, 3, 4, 5), ()])
def test_wrong_component_count_is_rejected(color) -> None:
    with pytest.raises(ValueError, match="r, g, b"):
        _coerce_color(color)


@pytest.mark.parametrize("color", [(-1, 0, 0), (256, 0, 0), (0, 0, 0, 300)])
def test_out_of_range_components_are_rejected(color) -> None:
    with pytest.raises(ValueError, match="0-255"):
        _coerce_color(color)


@pytest.mark.parametrize("color", [(1.5, 0, 0), ("ff", 0, 0), (True, 0, 0)])
def test_non_int_components_are_rejected(color) -> None:
    with pytest.raises(TypeError):
        _coerce_color(color)


@pytest.mark.parametrize(
    ("seconds", "units"), [(None, 0), (0, 0), (0.1, 1), (1, 10), (2.5, 25), (0.04, 0)]
)
def test_transition_is_converted_to_deciseconds(seconds, units) -> None:
    assert _transition_units(seconds) == units


def test_negative_transition_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        _transition_units(-1)


def test_transition_beyond_the_wire_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="at most"):
        _transition_units(7000)


# --------------------------------------------------------------------------
# The buffer
# --------------------------------------------------------------------------

@pytest.fixture
def twin(make_nanoleaf) -> DigitalTwin:
    return DigitalTwin(make_nanoleaf(), [1, 2, 3])


def test_every_panel_starts_black(twin: DigitalTwin) -> None:
    assert twin.colors == {1: (0, 0, 0, 0), 2: (0, 0, 0, 0), 3: (0, 0, 0, 0)}


def test_panel_ids_are_reported_in_frame_order(twin: DigitalTwin) -> None:
    assert twin.panel_ids == (1, 2, 3)


def test_set_and_get_one_panel(twin: DigitalTwin) -> None:
    twin.set_color(2, (10, 20, 30))
    assert twin.get_color(2) == (10, 20, 30, 0)
    assert twin.get_color(1) == (0, 0, 0, 0)


def test_set_all(twin: DigitalTwin) -> None:
    twin.set_all((5, 6, 7, 8))
    assert set(twin.colors.values()) == {(5, 6, 7, 8)}


def test_set_colors_leaves_other_panels_alone(twin: DigitalTwin) -> None:
    twin.set_color(3, (9, 9, 9))
    twin.set_colors({1: (1, 1, 1), 2: (2, 2, 2)})
    assert twin.colors == {1: (1, 1, 1, 0), 2: (2, 2, 2, 0), 3: (9, 9, 9, 0)}


def test_set_colors_is_all_or_nothing(twin: DigitalTwin) -> None:
    """A bad entry must not leave the buffer half applied."""
    with pytest.raises(UnknownPanel):
        twin.set_colors({1: (1, 1, 1), 99: (2, 2, 2)})
    assert twin.get_color(1) == (0, 0, 0, 0)

    with pytest.raises(ValueError):
        twin.set_colors({1: (1, 1, 1), 2: (300, 0, 0)})
    assert twin.get_color(1) == (0, 0, 0, 0)


def test_unknown_panel_on_get_and_set(twin: DigitalTwin) -> None:
    with pytest.raises(UnknownPanel):
        twin.get_color(99)
    with pytest.raises(UnknownPanel):
        twin.set_color(99, (1, 1, 1))


def test_unknown_panel_is_also_a_keyerror(twin: DigitalTwin) -> None:
    """So that existing dict-style error handling keeps working."""
    assert issubclass(UnknownPanel, KeyError)


def test_colors_returns_a_copy(twin: DigitalTwin) -> None:
    snapshot = twin.colors
    twin.set_all((1, 2, 3))
    assert snapshot[1] == (0, 0, 0, 0)


def test_a_device_with_no_panels_cannot_be_mirrored(make_nanoleaf) -> None:
    with pytest.raises(NanoleafException, match="no panels"):
        DigitalTwin(make_nanoleaf(), [])


def test_repr_is_useful(twin: DigitalTwin) -> None:
    assert repr(twin) == "DigitalTwin(panels=3, streaming=False)"


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def test_anim_data_layout(twin: DigitalTwin) -> None:
    twin.set_color(1, (255, 0, 0))
    twin.set_color(2, (0, 255, 0, 128))
    # count, then per panel: id, frames, r, g, b, w, transition
    assert twin._build_anim_data(0) == (
        "3 1 1 255 0 0 0 0 2 1 0 255 0 128 0 3 1 0 0 0 0 0"
    )


def test_anim_data_carries_the_transition(twin: DigitalTwin) -> None:
    assert twin._build_anim_data(10).split()[7] == "10"


def test_stream_frame_round_trips(twin: DigitalTwin) -> None:
    twin.set_colors({1: (255, 0, 0), 2: (0, 255, 0, 7), 3: (0, 0, 255)})
    assert decode(twin._build_stream_frame(3)) == [
        (1, 255, 0, 0, 0, 3),
        (2, 0, 255, 0, 7, 3),
        (3, 0, 0, 255, 0, 3),
    ]


def test_stream_frame_size(twin: DigitalTwin) -> None:
    # uint16 count, then per panel: uint16 id + 4 colour bytes + uint16 transition
    assert RECORD.size == 8
    assert len(twin._build_stream_frame(0)) == 2 + 3 * 8


def test_large_panel_ids_survive_encoding(make_nanoleaf) -> None:
    twin = DigitalTwin(make_nanoleaf(), [65534])
    twin.set_color(65534, (1, 2, 3))
    assert decode(twin._build_stream_frame(0)) == [(65534, 1, 2, 3, 0, 0)]


# --------------------------------------------------------------------------
# Building a twin from a device
# --------------------------------------------------------------------------

async def test_digital_twin_fetches_the_layout(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    assert twin.panel_ids == (1, 2)


async def test_digital_twin_accepts_a_subset(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin(panel_ids=[2])
    assert twin.panel_ids == (2,)


async def test_digital_twin_on_a_panelless_device(device, make_nanoleaf) -> None:
    from tests.conftest import ESSENTIALS_INFO, ESSENTIALS_STATE

    device.info = dict(ESSENTIALS_INFO)
    device.state = dict(ESSENTIALS_STATE)
    nanoleaf = make_nanoleaf()
    with pytest.raises(NanoleafException, match="no panels"):
        await nanoleaf.digital_twin()


# --------------------------------------------------------------------------
# sync() over HTTP
# --------------------------------------------------------------------------

async def test_sync_writes_a_static_effect(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    twin.set_color(1, (255, 0, 0))
    await twin.sync()

    write = device.effect_writes[-1]
    assert write["command"] == "display"
    assert write["animType"] == "static"
    assert write["loop"] is False
    assert write["palette"] == []
    assert write["animData"] == "2 1 1 255 0 0 0 0 2 1 0 0 0 0 0"


async def test_sync_with_a_transition(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    await twin.sync(transition=1.5)
    assert device.effect_writes[-1]["animData"].split()[7] == "15"


# --------------------------------------------------------------------------
# Streaming over UDP
# --------------------------------------------------------------------------

async def test_streaming_sends_udp_frames(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {
        "streamControlIpAddr": "127.0.0.1",
        "streamControlPort": udp_sink.port,
        "streamControlProtocol": "UDP",
    }
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()

    async with twin.streaming():
        assert twin.is_streaming
        twin.set_all((1, 2, 3))
        await twin.sync()
        twin.set_all((4, 5, 6))
        await twin.sync(transition=0.2)
        await asyncio.sleep(0.05)

    assert not twin.is_streaming
    assert len(udp_sink.frames) == 2
    assert decode(udp_sink.frames[0]) == [(1, 1, 2, 3, 0, 0), (2, 1, 2, 3, 0, 0)]
    assert decode(udp_sink.frames[1]) == [(1, 4, 5, 6, 0, 2), (2, 4, 5, 6, 0, 2)]


async def test_streaming_enables_external_control_first(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    async with twin.streaming():
        pass
    assert device.effect_writes[0] == {
        "command": "display",
        "animType": "extControl",
        "extControlVersion": "v2",
    }


async def test_sync_falls_back_to_http_after_streaming(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    async with twin.streaming():
        await twin.sync()
    await twin.sync()
    assert device.effect_writes[-1]["animType"] == "static"


async def test_streaming_cannot_be_nested(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    async with twin.streaming():
        with pytest.raises(NanoleafException, match="already open"):
            async with twin.streaming():
                pass


async def test_streaming_releases_the_socket_on_error(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    with pytest.raises(RuntimeError):
        async with twin.streaming():
            raise RuntimeError("boom")
    assert not twin.is_streaming


async def test_restore_effect_reselects_the_previous_effect(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    assert nanoleaf.selected_effect == "Nemo"
    twin = await nanoleaf.digital_twin()

    async with twin.streaming(restore_effect=True):
        await twin.sync()

    assert device.bodies[-1] == {"select": "Nemo"}


async def test_effect_is_not_restored_by_default(device, make_nanoleaf, udp_sink) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()
    async with twin.streaming():
        await twin.sync()
    assert {"select": "Nemo"} not in device.bodies


# --------------------------------------------------------------------------
# Negotiating external control
# --------------------------------------------------------------------------

async def test_a_204_means_the_default_v2_port(device, make_nanoleaf) -> None:
    device.ext_control_response = None
    nanoleaf = make_nanoleaf()
    host, port = await nanoleaf._enable_external_control()
    assert (host, port) == ("127.0.0.1", STREAM_PORT_V2)


async def test_a_reported_port_is_used(device, make_nanoleaf) -> None:
    device.ext_control_response = {"streamControlPort": 12345, "streamControlProtocol": "UDP"}
    nanoleaf = make_nanoleaf()
    _, port = await nanoleaf._enable_external_control()
    assert port == 12345


async def test_the_configured_host_wins_over_the_reported_one(device, make_nanoleaf) -> None:
    """The device reports its own address; ours is the one known to route."""
    device.ext_control_response = {"streamControlIpAddr": "10.9.9.9", "streamControlPort": 1}
    nanoleaf = make_nanoleaf()
    host, _ = await nanoleaf._enable_external_control()
    assert host == "127.0.0.1"


async def test_a_non_udp_protocol_is_refused(device, make_nanoleaf) -> None:
    device.ext_control_response = {"streamControlProtocol": "TCP"}
    nanoleaf = make_nanoleaf()
    with pytest.raises(StreamingUnsupported, match="TCP"):
        await nanoleaf._enable_external_control()


async def test_a_device_that_rejects_external_control(device, make_nanoleaf) -> None:
    device.effects_status = 422
    nanoleaf = make_nanoleaf()
    with pytest.raises(StreamingUnsupported, match="422"):
        await nanoleaf._enable_external_control()


async def test_ipv6_host_is_unbracketed_for_the_socket(session) -> None:
    from aionanoleaf2 import Nanoleaf

    nanoleaf = Nanoleaf(session, "fe80::1%eth0", auth_token="T")
    assert nanoleaf.host == "[fe80::1%eth0]"
    assert nanoleaf._bare_host == "fe80::1%eth0"


# --------------------------------------------------------------------------
# Panel ID validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("panel_id", [-1, 65536, 70000])
def test_panel_ids_must_fit_the_wire_format(make_nanoleaf, panel_id) -> None:
    """The HTTP path would format these into animData unchecked, and the
    streaming path would fail deep inside struct.pack."""
    with pytest.raises(UnknownPanel, match="0-65535"):
        DigitalTwin(make_nanoleaf(), [panel_id])


@pytest.mark.parametrize("panel_id", [1.0, "1", None, True])
def test_panel_ids_must_be_ints(make_nanoleaf, panel_id) -> None:
    with pytest.raises(TypeError, match="Panel IDs must be ints"):
        DigitalTwin(make_nanoleaf(), [panel_id])


@pytest.mark.parametrize("panel_id", [0, 65535])
def test_panel_ids_at_the_bounds_are_accepted(make_nanoleaf, panel_id) -> None:
    twin = DigitalTwin(make_nanoleaf(), [panel_id])
    twin.set_color(panel_id, (1, 2, 3))
    assert decode(twin._build_stream_frame(0)) == [(panel_id, 1, 2, 3, 0, 0)]


# --------------------------------------------------------------------------
# Streaming teardown must not swallow or mask errors
# --------------------------------------------------------------------------

async def test_restore_failure_does_not_mask_the_callers_error(
    device, make_nanoleaf, udp_sink, monkeypatch, caplog
) -> None:
    """A failed restore in the finally block must not replace the real error."""
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()

    async def boom(effect: str) -> None:
        raise Unavailable("device went away")

    monkeypatch.setattr(nanoleaf, "set_effect", boom)

    with pytest.raises(RuntimeError, match="what the caller cares about"):
        async with twin.streaming(restore_effect=True):
            raise RuntimeError("what the caller cares about")

    assert "Could not restore effect" in caplog.text
    assert not twin.is_streaming


async def test_restore_failure_on_a_clean_exit_is_logged_not_raised(
    device, make_nanoleaf, udp_sink, monkeypatch, caplog
) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()

    async def boom(effect: str) -> None:
        raise Unavailable("device went away")

    monkeypatch.setattr(nanoleaf, "set_effect", boom)

    async with twin.streaming(restore_effect=True):
        await twin.sync()

    assert "Could not restore effect" in caplog.text


async def test_a_socket_that_never_opens_still_restores(
    device, make_nanoleaf, monkeypatch
) -> None:
    """External control is already enabled by then, so it must be undone."""
    device.ext_control_response = {"streamControlPort": 60222}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()

    async def refuse(host: str, port: int):
        raise StreamingUnsupported("no socket for you")

    monkeypatch.setattr("aionanoleaf2.twin._PanelStream.open", refuse)

    with pytest.raises(StreamingUnsupported):
        async with twin.streaming(restore_effect=True):
            pytest.fail("the body must not run")

    assert not twin.is_streaming
    assert device.bodies[-1] == {"select": "Nemo"}


async def test_socket_errors_are_wrapped(make_nanoleaf) -> None:
    """A bad port raises OverflowError, which is not an OSError."""
    from aionanoleaf2.twin import _PanelStream

    with pytest.raises(StreamingUnsupported):
        await _PanelStream.open("127.0.0.1", 99999)
    with pytest.raises(StreamingUnsupported):
        await _PanelStream.open("nonexistent.invalid", 60222)


@pytest.mark.parametrize("reported", [0, 99999, -1])
async def test_an_out_of_range_reported_port_is_refused(
    device, make_nanoleaf, reported
) -> None:
    device.ext_control_response = {"streamControlPort": reported}
    nanoleaf = make_nanoleaf()
    with pytest.raises(StreamingUnsupported, match="out-of-range"):
        await nanoleaf._enable_external_control()


async def test_an_unparseable_reported_port_is_refused(device, make_nanoleaf) -> None:
    device.ext_control_response = {"streamControlPort": "not-a-port"}
    nanoleaf = make_nanoleaf()
    with pytest.raises(StreamingUnsupported, match="unusable"):
        await nanoleaf._enable_external_control()


async def test_a_refused_mode_switch_also_restores(device, make_nanoleaf) -> None:
    """The device can accept the extControl write and still report a port we
    cannot use, which leaves it in external control unless we undo it."""
    device.ext_control_response = {"streamControlPort": 99999}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()

    with pytest.raises(StreamingUnsupported, match="out-of-range"):
        async with twin.streaming(restore_effect=True):
            pytest.fail("the body must not run")

    assert device.bodies[-1] == {"select": "Nemo"}
    assert not twin.is_streaming


# --------------------------------------------------------------------------
# Writing a subset, and dimming what gets written
# --------------------------------------------------------------------------

def test_only_restricts_the_frame(twin: DigitalTwin) -> None:
    twin.set_all((10, 20, 30))
    assert decode(twin._build_stream_frame(0, only=[1, 3])) == [
        (1, 10, 20, 30, 0, 0),
        (3, 10, 20, 30, 0, 0),
    ]


def test_only_follows_buffer_order_not_the_callers(twin: DigitalTwin) -> None:
    twin.set_all((1, 1, 1))
    frame = twin._build_stream_frame(0, only=[3, 1])
    assert [record[0] for record in decode(frame)] == [1, 3]


def test_only_restricts_anim_data(twin: DigitalTwin) -> None:
    twin.set_all((5, 5, 5))
    assert twin._build_anim_data(0, only=[2]).split()[0] == "1"


def test_only_with_an_unknown_panel_is_rejected(twin: DigitalTwin) -> None:
    with pytest.raises(UnknownPanel):
        twin._build_anim_data(0, only=[1, 99])


def test_only_with_nothing_selected_is_rejected(twin: DigitalTwin) -> None:
    with pytest.raises(UnknownPanel, match="No panels selected"):
        twin._build_anim_data(0, only=[])


def test_brightness_dims_what_is_written_without_touching_the_buffer(twin: DigitalTwin) -> None:
    twin.set_all((200, 100, 50, 20))
    assert decode(twin._build_stream_frame(0, brightness=50)) == [
        (1, 100, 50, 25, 10, 0),
        (2, 100, 50, 25, 10, 0),
        (3, 100, 50, 25, 10, 0),
    ]
    # The buffer itself is unchanged, so the same twin can be written again.
    assert twin.get_color(1) == (200, 100, 50, 20)


@pytest.mark.parametrize(
    ("brightness", "expected"),
    [
        (None, (200, 100, 50, 0)),
        (100, (200, 100, 50, 0)),
        (0, (0, 0, 0, 0)),
        # 50 * 0.25 is 12.5, and round() breaks ties to even.
        (25, (50, 25, 12, 0)),
    ],
)
def test_brightness_levels(twin: DigitalTwin, brightness, expected) -> None:
    twin.set_all((200, 100, 50))
    assert decode(twin._build_stream_frame(0, brightness=brightness))[0][1:5] == expected


@pytest.mark.parametrize("brightness", [-1, 101, 500])
def test_an_out_of_range_brightness_is_rejected(twin: DigitalTwin, brightness) -> None:
    with pytest.raises(ValueError, match="0-100"):
        twin._build_anim_data(0, brightness=brightness)


@pytest.mark.parametrize("brightness", [True, 1.5, "50"])
def test_a_non_int_brightness_is_rejected(twin: DigitalTwin, brightness) -> None:
    with pytest.raises(TypeError):
        twin._build_anim_data(0, brightness=brightness)


async def test_sync_passes_only_and_brightness_through(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    twin.set_all((100, 100, 100))
    await twin.sync(only=[1], brightness=50)
    assert device.effect_writes[-1]["animData"] == "1 1 1 50 50 50 0 0"


# --------------------------------------------------------------------------
# Temporary display
# --------------------------------------------------------------------------

async def test_show_temporarily_uses_the_device_command_and_restores(
    device, make_nanoleaf
) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()
    twin.set_all((255, 0, 0))

    await twin.show_temporarily(0.01)

    # displayTemp leaves the selected effect alone, so restoring is a re-select.
    assert device.effect_writes[-1]["command"] == "displayTemp"
    assert device.effect_writes[-1]["animType"] == "static"
    assert device.bodies[-1] == {"select": "Nemo"}


async def test_show_temporarily_can_leave_the_colour_up(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()
    await twin.show_temporarily(0.01, restore_effect=False)
    assert {"select": "Nemo"} not in device.bodies


async def test_show_temporarily_honours_only_and_brightness(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    twin.set_all((100, 100, 100))
    await twin.show_temporarily(0.01, only=[2], brightness=50)
    assert device.effect_writes[-1]["animData"] == "1 2 1 50 50 50 0 0"


async def test_a_failed_restore_does_not_mask_a_display_error(
    device, make_nanoleaf, monkeypatch, caplog
) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    twin = await nanoleaf.digital_twin()

    async def boom(effect: str) -> None:
        raise Unavailable("device went away")

    monkeypatch.setattr(nanoleaf, "set_effect", boom)
    monkeypatch.setattr(
        nanoleaf, "_write_static_effect", _raiser(RuntimeError("display failed"))
    )

    with pytest.raises(RuntimeError, match="display failed"):
        await twin.show_temporarily(0.01)
    assert "Could not restore effect" in caplog.text


def _raiser(exc: BaseException):
    async def _raise(*args, **kwargs):
        raise exc

    return _raise


async def test_a_negative_duration_is_rejected(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    with pytest.raises(ValueError, match="negative"):
        await twin.show_temporarily(-1)


async def test_show_temporarily_is_refused_while_streaming(
    device, make_nanoleaf, udp_sink
) -> None:
    device.ext_control_response = {"streamControlPort": udp_sink.port}
    nanoleaf = make_nanoleaf()
    twin = await nanoleaf.digital_twin()
    async with twin.streaming():
        with pytest.raises(NanoleafException, match="streaming"):
            await twin.show_temporarily(0.01)


async def test_an_invalid_write_command_is_rejected(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    with pytest.raises(ValueError, match="displayTemp"):
        await nanoleaf._write_static_effect("1 1 1 0 0 0 0 0", command="wipe")
