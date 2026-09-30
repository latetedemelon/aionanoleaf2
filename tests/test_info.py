"""get_info() across device families."""
from __future__ import annotations

import pytest

from aionanoleaf2 import Unavailable
from tests.conftest import ESSENTIALS_INFO, ESSENTIALS_STATE


async def test_full_device(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()

    assert nanoleaf.name == "Shapes ABCD"
    assert nanoleaf.model == "NL42"
    assert nanoleaf.hardware_version == "2.1-2"
    assert nanoleaf.is_on is True
    assert nanoleaf.brightness == 50
    assert nanoleaf.color_temperature_max == 6500
    assert nanoleaf.effects_list == ["Nemo", "Forest"]
    assert nanoleaf.selected_effect == "Nemo"
    assert len(nanoleaf.panels) == 2


async def test_essentials_state_fetched_separately(device, make_nanoleaf) -> None:
    """Regression test for upstream issue #7: KeyError 'state'.

    Matter Wi-Fi / Essentials devices return identity fields only; state,
    the effects list and the selected effect all live on sub-resources.
    """
    device.info = dict(ESSENTIALS_INFO)
    device.state = dict(ESSENTIALS_STATE)
    device.effects_list = ["Sunrise", "Candlelight"]
    device.selected_effect = "Sunrise"

    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()

    assert nanoleaf.name == "Ceiling Light"
    assert nanoleaf.brightness == 33
    assert nanoleaf.color_temperature == 2700
    assert nanoleaf.color_temperature_min == 2700
    assert nanoleaf.color_mode == "ct"
    assert nanoleaf.is_on is True
    assert nanoleaf.effects_list == ["Sunrise", "Candlelight"]
    assert nanoleaf.selected_effect == "Sunrise"
    # No panelLayout on these devices, so the panel set stays empty.
    assert nanoleaf.panels == set()
    assert ("GET", "/api/v1/TESTTOKEN/state") in device.requests


async def test_essentials_without_reachable_state_does_not_raise(device, make_nanoleaf) -> None:
    """If even GET state is missing, fall back to defaults rather than crashing."""
    device.info = dict(ESSENTIALS_INFO)
    device.state = None
    device.effects_list = None
    device.selected_effect = None

    nanoleaf = make_nanoleaf(retries=1)
    await nanoleaf.get_info()

    assert nanoleaf.name == "Ceiling Light"
    assert nanoleaf.is_on is False
    assert nanoleaf.effects_list == []
    assert nanoleaf.effect == ""


async def test_wrapped_effects_payloads(device, make_nanoleaf) -> None:
    """Some firmwares wrap the list/selection in an object instead of returning it bare."""
    device.info = dict(ESSENTIALS_INFO)
    device.state = dict(ESSENTIALS_STATE)
    device.effects_list = {"effectsList": ["Sunrise"]}
    device.selected_effect = {"select": "Sunrise"}

    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()

    assert nanoleaf.effects_list == ["Sunrise"]
    assert nanoleaf.selected_effect == "Sunrise"


async def test_is_on_is_a_bool_not_the_raw_value(device, make_nanoleaf) -> None:
    device.info["state"]["on"]["value"] = 1
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    assert nanoleaf.is_on is True


async def test_panels_deduplicate_and_compare_by_value(device, make_nanoleaf) -> None:
    """Polling twice must produce an equal panel set, not a new one."""
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    first = nanoleaf.panels
    await nanoleaf.get_info()
    assert nanoleaf.panels == first


async def test_unavailable_device(session, make_nanoleaf) -> None:
    from aionanoleaf2 import Nanoleaf

    nanoleaf = Nanoleaf(session, "127.0.0.1", auth_token="TESTTOKEN", port=1, retries=1)
    with pytest.raises(Unavailable):
        await nanoleaf.get_info()
