# aioNanoleaf2 package 
[![PyPI](https://img.shields.io/pypi/v/aionanoleaf2)](https://pypi.org/project/aionanoleaf2/) ![PyPI - Downloads](https://img.shields.io/pypi/dm/aionanoleaf2) [![PyPI - License](https://img.shields.io/pypi/l/aionanoleaf2?color=blue)](https://github.com/loebi-ch/aionanoleaf2/blob/master/LICENSE)

This async Python wrapper for the Nanoleaf API replaces the no longer maintained aioNanoleaf package.

The original aioNanoleaf has been modified to:
- add support for Nanoleaf Essentials devices.
- add support for Screen Mirroring emersion modes (1D, 2D, 3D, 4D).
- add support for IPv6 hosts.
- add per-panel control through a digital twin, over HTTP or UDP streaming.

The package is typed (PEP 561) and needs Python 3.9 or newer.

## Installation

```bash
pip install aionanoleaf2
```

## Quick start

```python
import asyncio
from aiohttp import ClientSession
from aionanoleaf2 import Nanoleaf, Unauthorized

async def main():
    async with ClientSession() as session:
        nanoleaf = Nanoleaf(session, "192.168.1.28")

        # Pairing is a one-off: hold the power button for 5-7 seconds first,
        # or enable the API in the Nanoleaf app, then call this within 30s.
        try:
            await nanoleaf.authorize()
        except Unauthorized as ex:
            print("Pairing failed:", ex)
            return
        print("Keep this token for next time:", nanoleaf.auth_token)

        await nanoleaf.get_info()
        print(f"{nanoleaf.name} ({nanoleaf.model}) is {'on' if nanoleaf.is_on else 'off'}")

        await nanoleaf.turn_on()
        await nanoleaf.set_brightness(60, transition=2)

asyncio.run(main())
```

Once you have a token, pass it in and skip `authorize()`:

```python
nanoleaf = Nanoleaf(session, "192.168.1.28", auth_token="YOUR_TOKEN")
```

`deauthorize()` revokes the token on the device; you will have to pair again.

## Connecting

The host may be an IPv4 address, an IPv6 address or a hostname. IPv6 literals
are bracketed for you, and a link-local zone ID is preserved:

```python
Nanoleaf(session, "192.168.1.28")     # -> http://192.168.1.28:16021
Nanoleaf(session, "nanoleaf.local")   # -> http://nanoleaf.local:16021
Nanoleaf(session, "2001:db8::5")      # -> http://[2001:db8::5]:16021
Nanoleaf(session, "fe80::1%eth0")     # -> http://[fe80::1%eth0]:16021
```

Two other constructor arguments: `port` (default `16021`) and `retries`
(default `3`). Retries apply only to connection errors and timeouts, with
exponential backoff between attempts; an invalid token or an HTTP error fails
immediately.

## Reading the device state

`get_info()` fetches everything in one call and populates the properties
below. Nothing else refreshes them, so call it again when you want fresh
values — or use [events](#events) to be told about changes.

```python
await nanoleaf.get_info()
```

**Identity** — `name`, `model`, `manufacturer`, `serial_no`,
`firmware_version`, `hardware_version`, and the `host` and `port` you
connected to.

**Light state** — `is_on`, `brightness`, `hue`, `saturation`,
`color_temperature`, `color_mode`.

**Ranges** — each of `brightness`, `hue`, `saturation` and
`color_temperature` has a matching `*_min` and `*_max`, since they differ by
model. A white-only device reports a narrow `color_temperature` range, for
instance.

**Effects and layout** — `effects_list`, `effect`, `selected_effect`,
`emersion_list`, `emersion`, `selected_emersion`, `panels`.

`selected_effect` is `effect` filtered through `effects_list`, so it is `None`
whenever the device reports something that is not a saved effect.
`hardware_version` is `None` on devices that do not report one.

`panels` is a set of `Panel` objects with `id`, `x_coordinate`,
`y_coordinate`, `orientation` and `shape` (a `Shape` with a `name` and a
`side_length`). Panels compare and hash by value, so two `get_info()` calls
on an unchanged layout produce equal sets. Devices without a panel layout,
such as Nanoleaf Essentials, report an empty set.

## Controlling the light

```python
await nanoleaf.turn_on()
await nanoleaf.turn_off()
await nanoleaf.identify()                      # flash the panels

await nanoleaf.set_brightness(50)
await nanoleaf.set_brightness(50, transition=3)  # transitions are in seconds
await nanoleaf.set_brightness(-10, relative=True)
await nanoleaf.set_hue(120)
await nanoleaf.set_saturation(80)
await nanoleaf.set_color_temperature(4000)
```

`set_state()` sets several attributes in one request, which avoids the flicker
of applying them one at a time:

```python
await nanoleaf.set_state(on=True, brightness=80, hue=200, saturation=100)
await nanoleaf.set_state(brightness=10, brightness_relative=True)
```

Each attribute has a matching `*_relative` flag, and brightness also takes
`brightness_transition`. Omitted arguments are left alone; calling
`set_state()` with none sends no request at all.

Note that `turn_off(transition=...)` dims to zero brightness over that many
seconds rather than switching the device off, because the device has no
transition on its power state.

### Effects

```python
print(nanoleaf.effects_list)
await nanoleaf.set_effect("Nemo")
```

`set_effect()` checks the name against `effects_list` and raises
`InvalidEffect` if it is not there, so `get_info()` has to have run first.

### Screen Mirroring (emersion)

Only reported by devices whose `model` appears in
`aionanoleaf2.nanoleaf.EMERSION_MODELS` (currently `NL69`). On everything else
`emersion_list` stays empty.

```python
if nanoleaf.emersion_list:
    await nanoleaf.set_emersion("4D")   # "1D", "2D", "3D" or "4D"
```

## Per-panel control (digital twin)

A `DigitalTwin` is a local buffer holding one RGBW colour per panel. Changing
it does nothing on its own; `sync()` writes the whole buffer to the device.

```python
twin = await nanoleaf.digital_twin()

twin.set_all((0, 0, 40))                 # (r, g, b) or (r, g, b, w), each 0-255
twin.set_color(twin.panel_ids[0], (255, 0, 0))
twin.set_colors({1: (0, 255, 0), 2: (0, 0, 255)})

await twin.sync(transition=0.5)          # seconds, rounded to the nearest 0.1
```

`sync()` writes a *static effect* over HTTP. That works on every panel device
and persists until another effect is selected, but each call is a round trip.

Read the buffer back with `twin.get_color(panel_id)` or `twin.colors`, which
returns a copy as a `{panel_id: (r, g, b, w)}` dict.

### Streaming

For animation, open a streaming session. Inside it, `sync()` sends a single
UDP datagram per call instead, which is orders of magnitude cheaper:

```python
async with twin.streaming():
    for frame in animation:
        twin.set_all(frame)
        await twin.sync()
        await asyncio.sleep(1 / 30)
```

On exit the panels keep whatever the last frame set them to. Pass
`streaming(restore_effect=True)` to re-select the effect that was active
beforehand instead; if that restore fails it is logged rather than raised, so
it cannot mask an error from inside the block.

Streaming uses Nanoleaf external control v2, which covers Canvas, Shapes,
Elements and Lines. A device that will not accept the session — including the
older Light Panels, whose v1 framing is not implemented — raises
`StreamingUnsupported`. Its HTTP `sync()` path still works.

### Notes

- The device cannot report the current colour of a panel, so a new twin starts
  with every panel black. Call `set_all()` first if that matters.
- Panels are ordered by ID, and `twin.panel_ids` is that order. Pass
  `digital_twin(panel_ids=[...])` to cover a subset, for example to leave out a
  controller panel.
- Panel IDs and colour components are validated when you set them, not when
  you sync, so mistakes surface at the line that caused them. An unknown or
  out-of-range panel raises `UnknownPanel`, which is also a `KeyError`.
- `set_colors()` validates every entry before applying any of them, so a bad
  entry cannot leave the buffer half updated.
- `twin.is_streaming` tells you which transport `sync()` will use.

## Events

`listen_events()` subscribes to the device's event stream and runs until
cancelled, reconnecting on its own if the connection drops. It also keeps the
state properties up to date.

All callbacks must be coroutine functions; a plain function raises
`TypeError`. Exceptions raised inside a callback are logged and do not stop
the listener.

```python
async def on_state(event):
    print("state:", event.attribute, "->", event.value)

async def on_touch(event):
    print("touch:", event.gesture, "on panel", event.panel_id)

listener = asyncio.create_task(
    nanoleaf.listen_events(state_callback=on_state, touch_callback=on_touch)
)
...
listener.cancel()
```

| Callback | Event | Useful attributes |
| --- | --- | --- |
| `state_callback` | `StateEvent` | `attribute`, `value` |
| `layout_callback` | `LayoutEvent` | `attribute` |
| `effects_callback` | `EffectsEvent` | `effect` |
| `touch_callback` | `TouchEvent` | `gesture`, `panel_id` |
| `touch_stream_callback` | `TouchStreamEvent` | `panel_id`, `touch_type`, `strength`, `panel_id_2` |

`state_callback` reports one attribute at a time: `is_on`, `brightness`,
`hue`, `saturation`, `color_temperature` or `color_mode`.

`touch_stream_callback` is the high-frequency path. Passing it opens a local
UDP socket and asks the device to stream raw touch data to it, which reports
hover and pressure as well as gestures, and yields one event per panel
touched. Override the bind address with the `local_ip` and `local_port`
keyword arguments if the default does not suit your network.

## Exceptions

All of these derive from `NanoleafException`.

| Exception | Raised when |
| --- | --- |
| `Unavailable` | the device could not be reached after all retries |
| `Unauthorized` | `authorize()` was called without the device being in pairing mode |
| `NoAuthToken` | a request needed a token and none was set |
| `InvalidToken` | the device rejected the token (HTTP 401) |
| `InvalidEffect` | the effect name is not in `effects_list` |
| `InvalidEmersion` | the emersion mode is not in `emersion_list` |
| `UnknownPanel` | the panel is not part of the twin, or the ID is out of range |
| `StreamingUnsupported` | the device would not start an external control session |

## Examples

[`examples/`](examples) holds runnable scripts:

- [`manual_smoke_test.py`](examples/manual_smoke_test.py) — connect and print
  everything the device reports.
- [`digital_twin.py`](examples/digital_twin.py) — a static rainbow over HTTP,
  then a rotating one over streaming.

Both take the device address as an argument.

## Development

```bash
pip install -e ".[test]"
pytest                      # no hardware or network needed
mypy aionanoleaf2
flake8 aionanoleaf2 tests
```

The test suite runs against a fake Nanoleaf served over loopback HTTP and a
local UDP socket, so it covers the wire formats without a device attached.
