# aioNanoleaf2 package 
[![PyPI](https://img.shields.io/pypi/v/aionanoleaf2)](https://pypi.org/project/aionanoleaf2/) ![PyPI - Downloads](https://img.shields.io/pypi/dm/aionanoleaf2) [![PyPI - License](https://img.shields.io/pypi/l/aionanoleaf2?color=blue)](https://github.com/loebi-ch/aionanoleaf2/blob/master/LICENSE)

This async Python wrapper for the Nanoleaf API replaces the no longer maintained aioNanoleaf package.

The original aioNanoleaf has been modified to:
- add support for Nanoleaf Essentials devices.
- add support for Screen Mirroring emersion modes (1D, 2D, 3D, 4D).
- add support for IPv6 hosts.
- add per-panel control through a digital twin, over HTTP or UDP streaming.

## Installation
```bash
pip install aionanoleaf2
```

## Example
```python
from aiohttp import ClientSession
from asyncio import run

import aionanoleaf2

async def test():
    async with ClientSession() as session:
        nanoleaf = aionanoleaf2.Nanoleaf(session, "192.168.1.28")
        try:
            await nanoleaf.authorize()
        except aionanoleaf2.Unauthorized as ex:
            print("Not authorized:", ex)
            return
        await nanoleaf.turn_on()
        await nanoleaf.get_info()
        print("IT'S WORKING!");
        print("Host:", nanoleaf.host)
        print("Port:", nanoleaf.port)
        print("API URL:", nanoleaf._api_url)
        print("Name:", nanoleaf.name)
        print("Manufacturer:", nanoleaf.manufacturer)
        print("Model:", nanoleaf.model)
        print("Serial number:", nanoleaf.serial_no)
        print("Hardware version:", nanoleaf.hardware_version)
        print("Firmware version:", nanoleaf.firmware_version)
        print("On:", nanoleaf.is_on);
        print("Brightness:", f"{nanoleaf.brightness} [{nanoleaf.brightness_min} - {nanoleaf.brightness_max}]")
        print("Hue:", f"{nanoleaf.hue} [{nanoleaf.hue_min} - {nanoleaf.hue_max}]")
        print("Saturation:", f"{nanoleaf.saturation} [{nanoleaf.saturation_min} - {nanoleaf.saturation_max}]")
        print("Color temperature:", f"{nanoleaf.color_temperature} [{nanoleaf.color_temperature_min} - {nanoleaf.color_temperature_max}]")
        print("Color mode:", nanoleaf.color_mode)
        print("Effects:", nanoleaf.effects_list)
        print("Selected effect:", nanoleaf.selected_effect)
        print("Emersions:", nanoleaf.emersion_list)
        print("Selected emersion:", nanoleaf.selected_emersion)
        print("Panels:", len(nanoleaf.panels))
        await nanoleaf.identify()
        await nanoleaf.turn_off()
        await nanoleaf.deauthorize()

run(test())
```

Runnable versions of the snippets below are in [`examples/`](examples).

## Per-panel control (digital twin)

A `DigitalTwin` is a local buffer holding one RGBW colour per panel. Changing
it does nothing on its own; `sync()` writes the whole buffer to the device.

```python
twin = await nanoleaf.digital_twin()

twin.set_all((0, 0, 40))                 # (r, g, b) or (r, g, b, w), each 0-255
twin.set_color(twin.panel_ids[0], (255, 0, 0))
twin.set_colors({1: (0, 255, 0), 2: (0, 0, 255)})

await twin.sync(transition=0.5)          # transition is in seconds
```

`sync()` writes a *static effect* over HTTP. That works on every panel device
and persists until another effect is selected, but each call is a round trip.

### Streaming

For animation, open a streaming session. Inside it `sync()` sends a single UDP
datagram per call instead, which is roughly a thousand times cheaper:

```python
async with twin.streaming():
    for frame in animation:
        twin.set_all(frame)
        await twin.sync()
        await asyncio.sleep(1 / 30)
```

On exit the panels keep whatever the last frame set them to. Pass
`streaming(restore_effect=True)` to re-select the effect that was active
beforehand.

Streaming uses Nanoleaf external control v2, which covers Canvas, Shapes,
Elements and Lines. A device that refuses the session raises
`StreamingUnsupported`.

### Notes

- The device cannot report the current colour of a panel, so a new twin starts
  with every panel black. Call `set_all()` first if that matters.
- Panels are ordered by ID. Pass `digital_twin(panel_ids=[...])` to cover a
  subset, for example to skip a controller panel.
- An unknown panel ID raises `UnknownPanel`, which is also a `KeyError`.
- `set_colors()` validates everything before applying anything, so a bad entry
  cannot leave the buffer half updated.

## Development

```bash
pip install -e ".[test]"
pytest          # 114 tests, no hardware needed
mypy aionanoleaf2
flake8 aionanoleaf2 tests
```
