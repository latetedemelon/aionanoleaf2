"""Audio module, layout rotation and effect metadata."""
from __future__ import annotations

import pytest

from aionanoleaf2 import InvalidRhythmMode

RHYTHM_BUILTIN = {
    "rhythmConnected": True,
    "rhythmActive": True,
    "rhythmId": 1,
    "auxAvailable": True,
    "rhythmMode": 0,
}

# Oldest Light Panels firmwares omit rhythmConnected entirely.
RHYTHM_LEGACY = {"rhythmActive": False, "rhythmMode": 1, "auxAvailable": False}


# --------------------------------------------------------------------------
# Reading the audio module
# --------------------------------------------------------------------------

async def test_a_device_with_a_module(device, make_nanoleaf) -> None:
    device.rhythm = dict(RHYTHM_BUILTIN)
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_rhythm() == RHYTHM_BUILTIN
    assert nanoleaf.has_rhythm is True
    assert nanoleaf.rhythm_active is True
    assert nanoleaf.rhythm_aux_available is True
    assert nanoleaf.rhythm_mode == "microphone"
    assert nanoleaf.rhythm_mode_list == ["microphone", "aux"]


async def test_a_device_without_one_reports_absence_not_an_error(device, make_nanoleaf) -> None:
    """The resource 404s on most devices, which is normal rather than a failure."""
    device.rhythm = None
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_rhythm() == {}
    assert nanoleaf.has_rhythm is False
    assert nanoleaf.rhythm_mode is None
    assert nanoleaf.rhythm_mode_list == []


async def test_a_firmware_without_the_connected_flag_still_counts(device, make_nanoleaf) -> None:
    device.rhythm = dict(RHYTHM_LEGACY)
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_rhythm()
    assert nanoleaf.has_rhythm is True
    assert nanoleaf.rhythm_mode == "aux"
    # No aux input reported, so only the microphone is offered.
    assert nanoleaf.rhythm_mode_list == ["microphone"]


async def test_a_disconnected_module_is_not_present(device, make_nanoleaf) -> None:
    device.rhythm = {"rhythmConnected": False, "rhythmMode": 0}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_rhythm()
    assert nanoleaf.has_rhythm is False


async def test_a_non_object_payload_is_ignored(device, make_nanoleaf) -> None:
    device.rhythm = ["unexpected"]
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_rhythm() == {}


async def test_a_boolean_mode_is_not_read_as_a_source(device, make_nanoleaf) -> None:
    """bool is a subclass of int, so it has to be rejected explicitly."""
    device.rhythm = {"rhythmConnected": True, "rhythmMode": True}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_rhythm()
    assert nanoleaf.rhythm_mode is None


async def test_an_unknown_mode_number_reads_as_none(device, make_nanoleaf) -> None:
    device.rhythm = {"rhythmConnected": True, "rhythmMode": 7}
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_rhythm()
    assert nanoleaf.rhythm_mode is None


# --------------------------------------------------------------------------
# Choosing the audio source
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("given", "sent"),
    [("microphone", 0), ("mic", 0), ("MIC", 0), (" aux ", 1), ("aux", 1), (0, 0), (1, 1)],
)
async def test_set_rhythm_mode(device, make_nanoleaf, given, sent) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.set_rhythm_mode(given)
    assert device.bodies[-1] == {"rhythmMode": sent}


async def test_setting_the_mode_updates_the_cached_payload(device, make_nanoleaf) -> None:
    device.rhythm = dict(RHYTHM_BUILTIN)
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_rhythm()
    await nanoleaf.set_rhythm_mode("aux")
    assert nanoleaf.rhythm_mode == "aux"


@pytest.mark.parametrize("mode", ["speaker", "", "0", 2, -1, True, 1.0, None])
async def test_an_unknown_source_is_rejected_locally(device, make_nanoleaf, mode) -> None:
    nanoleaf = make_nanoleaf()
    with pytest.raises(InvalidRhythmMode):
        await nanoleaf.set_rhythm_mode(mode)
    assert device.requests == []


def test_the_rhythm_error_is_also_a_valueerror() -> None:
    assert issubclass(InvalidRhythmMode, ValueError)


# --------------------------------------------------------------------------
# Layout rotation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [90, {"value": 90}, {"value": 90, "max": 360, "min": 0}])
async def test_orientation_is_read_from_either_shape(device, make_nanoleaf, payload) -> None:
    device.orientation = payload
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_global_orientation() == 90
    assert nanoleaf.global_orientation == 90


async def test_an_absent_orientation_reads_as_none(device, make_nanoleaf) -> None:
    device.orientation = None
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_global_orientation() is None


async def test_an_unexpected_orientation_shape_reads_as_none(device, make_nanoleaf) -> None:
    device.orientation = {"unexpected": 1}
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_global_orientation() is None


@pytest.mark.parametrize("angle", [0, 1, 180, 360])
async def test_set_global_orientation(device, make_nanoleaf, angle) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.set_global_orientation(angle)
    assert device.bodies[-1] == {"globalOrientation": {"value": angle}}
    assert nanoleaf.global_orientation == angle


@pytest.mark.parametrize("angle", [-1, 361, 1000])
async def test_an_out_of_range_orientation_is_rejected(device, make_nanoleaf, angle) -> None:
    """The old implementation silently clamped, which hides caller bugs."""
    nanoleaf = make_nanoleaf()
    with pytest.raises(ValueError, match="0-360"):
        await nanoleaf.set_global_orientation(angle)
    assert device.requests == []


@pytest.mark.parametrize("angle", [1.5, "90", True, None])
async def test_a_non_int_orientation_is_rejected(device, make_nanoleaf, angle) -> None:
    nanoleaf = make_nanoleaf()
    with pytest.raises(ValueError):
        await nanoleaf.set_global_orientation(angle)


# --------------------------------------------------------------------------
# Effect metadata and music-sync discovery
# --------------------------------------------------------------------------

DETAILS = {
    "animations": [
        {"animName": "Nemo", "pluginType": "color"},
        {"animName": "Pulse Pop Beats", "pluginType": "rhythm"},
        {"animName": "Streaking Notes", "pluginType": "rhythm"},
        {"animName": "Forest"},
        "not-a-dict",
        {"pluginType": "rhythm"},
    ]
}


async def test_effect_details_are_returned(device, make_nanoleaf) -> None:
    device.effect_details = DETAILS
    nanoleaf = make_nanoleaf()
    details = await nanoleaf.get_effect_details()
    assert [d.get("animName") for d in details] == [
        "Nemo",
        "Pulse Pop Beats",
        "Streaking Notes",
        "Forest",
        None,
    ]


async def test_rhythm_effects_are_the_audio_reactive_ones(device, make_nanoleaf) -> None:
    device.effect_details = DETAILS
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_rhythm_effects() == ["Pulse Pop Beats", "Streaking Notes"]


async def test_a_firmware_that_does_not_answer_gives_nothing(device, make_nanoleaf) -> None:
    device.effect_details = None
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_effect_details() == []
    assert await nanoleaf.get_rhythm_effects() == []


@pytest.mark.parametrize("payload", [[], {"animations": "nope"}, {"other": 1}, "text"])
async def test_an_unexpected_details_shape_gives_nothing(device, make_nanoleaf, payload) -> None:
    device.effect_details = payload
    nanoleaf = make_nanoleaf()
    assert await nanoleaf.get_effect_details() == []


async def test_the_request_uses_the_requestall_command(device, make_nanoleaf) -> None:
    device.effect_details = DETAILS
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_effect_details()
    assert device.effect_writes[-1] == {"command": "requestAll"}
