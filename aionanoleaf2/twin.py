# Copyright 2021, Milan Meulemans.
# Modified and optimized 2025 by loebi-ch
#
# This file is part of aionanoleaf2, the refactored version of aionanoleaf by Milan Meulemans
#
# aionanoleaf2 is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# aionanoleaf2 is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with aionanoleaf2.  If not, see <https://www.gnu.org/licenses/>.

"""Per-panel control: a local mirror of the device that can be pushed to it.

A `DigitalTwin` holds one RGBW colour per panel. Mutating it changes nothing
on the device; `sync()` is what writes the whole buffer out.

Two transports are available and `sync()` picks whichever is active:

* By default it writes a *static effect* over HTTP. This works on every
  panel device and survives until another effect is selected, but a round
  trip costs tens of milliseconds.
* Inside `streaming()` it sends a single UDP datagram instead, which is the
  path to use for animation.

The device offers no way to read back the current colour of a panel, so a
new twin starts with every panel black.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Iterable, Mapping, Sequence, Tuple

from .exceptions import NanoleafException, StreamingUnsupported, UnknownPanel

if TYPE_CHECKING:
    from .nanoleaf import Nanoleaf

_LOGGER = logging.getLogger(__name__)

# UDP port the device listens on once external control v2 is enabled.
STREAM_PORT_V2 = 60222

# External control v2 frame: a uint16 panel count, then one record per panel of
# uint16 panel ID, four colour bytes and a uint16 transition time.
_STREAM_HEADER = struct.Struct(">H")
_STREAM_RECORD = struct.Struct(">HBBBBH")

# The device expresses transitions in tenths of a second.
_DECISECOND = 0.1

# A transition time has to fit the uint16 the wire format reserves for it.
_MAX_TRANSITION_UNITS = 0xFFFF

# Panel IDs are uint16 on the wire.
_MAX_PANEL_ID = 0xFFFF

#: One panel's colour: red, green, blue and white, each 0-255.
RGBW = Tuple[int, int, int, int]


def _coerce_color(color: Sequence[int]) -> RGBW:
    """Normalize an RGB or RGBW sequence, rejecting out-of-range components."""
    if len(color) == 3:
        red, green, blue = color
        white = 0
    elif len(color) == 4:
        red, green, blue, white = color
    else:
        raise ValueError(
            f"Expected an (r, g, b) or (r, g, b, w) sequence, got {len(color)} values"
        )

    components = (red, green, blue, white)
    for component in components:
        if not isinstance(component, int) or isinstance(component, bool):
            raise TypeError(f"Colour components must be ints, got {component!r}")
        if not 0 <= component <= 255:
            raise ValueError(f"Colour components must be 0-255, got {component}")
    return components


def _check_panel_id(panel_id: int) -> int:
    """Return the panel ID if both transports can carry it."""
    if not isinstance(panel_id, int) or isinstance(panel_id, bool):
        raise TypeError(f"Panel IDs must be ints, got {panel_id!r}")
    if not 0 <= panel_id <= _MAX_PANEL_ID:
        raise UnknownPanel(
            f"Panel IDs must be 0-{_MAX_PANEL_ID}, got {panel_id}"
        )
    return panel_id


def _transition_units(transition: float | None) -> int:
    """Convert a transition in seconds to the tenths of a second the API wants."""
    if transition is None:
        return 0
    if transition < 0:
        raise ValueError(f"Transition must not be negative, got {transition}")
    units = round(transition / _DECISECOND)
    if units > _MAX_TRANSITION_UNITS:
        raise ValueError(
            f"Transition must be at most {_MAX_TRANSITION_UNITS * _DECISECOND:.1f}s, "
            f"got {transition}"
        )
    return units


class DigitalTwin:
    """A local RGBW buffer, one entry per panel, writable to the device."""

    def __init__(self, nanoleaf: Nanoleaf, panel_ids: Iterable[int]) -> None:
        """Build a twin for the given panels, all starting black."""
        self._nanoleaf = nanoleaf
        self._stream: _PanelStream | None = None
        # Checked here rather than at sync() time, where an out-of-range ID
        # would be silently formatted into animData by the HTTP path and only
        # rejected by struct on the streaming one.
        self._colors: dict[int, RGBW] = {
            _check_panel_id(panel_id): (0, 0, 0, 0) for panel_id in panel_ids
        }
        if not self._colors:
            raise NanoleafException(
                "Cannot build a digital twin for a device with no panels"
            )

    def __repr__(self) -> str:
        """Return a readable representation."""
        return f"DigitalTwin(panels={len(self._colors)}, streaming={self.is_streaming})"

    @property
    def panel_ids(self) -> tuple[int, ...]:
        """Return the panel IDs this twin covers, in the order frames use."""
        return tuple(self._colors)

    @property
    def is_streaming(self) -> bool:
        """Return True while a streaming session is open."""
        return self._stream is not None

    @property
    def colors(self) -> dict[int, RGBW]:
        """Return a copy of the buffer as panel ID to (r, g, b, w)."""
        return dict(self._colors)

    def get_color(self, panel_id: int) -> RGBW:
        """Return the buffered colour of one panel as (r, g, b, w)."""
        try:
            return self._colors[panel_id]
        except KeyError:
            raise UnknownPanel(
                f"Panel {panel_id} is not part of this twin; "
                f"known panels: {sorted(self._colors)}"
            ) from None

    def set_color(self, panel_id: int, color: Sequence[int]) -> None:
        """Set one panel to an (r, g, b) or (r, g, b, w) colour."""
        if panel_id not in self._colors:
            raise UnknownPanel(
                f"Panel {panel_id} is not part of this twin; "
                f"known panels: {sorted(self._colors)}"
            )
        self._colors[panel_id] = _coerce_color(color)

    def set_colors(self, colors: Mapping[int, Sequence[int]]) -> None:
        """Set several panels at once, leaving the rest untouched.

        Nothing is applied unless every panel ID and colour is valid, so a
        bad entry cannot leave the buffer half updated.
        """
        validated: dict[int, RGBW] = {
            panel_id: _coerce_color(color) for panel_id, color in colors.items()
        }
        unknown = set(validated) - set(self._colors)
        if unknown:
            raise UnknownPanel(
                f"Panels {sorted(unknown)} are not part of this twin; "
                f"known panels: {sorted(self._colors)}"
            )
        self._colors.update(validated)

    def set_all(self, color: Sequence[int]) -> None:
        """Set every panel to the same colour."""
        value = _coerce_color(color)
        for panel_id in self._colors:
            self._colors[panel_id] = value

    async def sync(self, transition: float | None = None) -> None:
        """Write the whole buffer to the device.

        Sends a UDP frame when a streaming session is open, otherwise writes
        a static effect over HTTP. `transition` is in seconds and is rounded
        to the tenth of a second the device works in.
        """
        units = _transition_units(transition)
        if self._stream is not None:
            self._stream.send(self._build_stream_frame(units))
            return
        await self._nanoleaf._write_static_effect(self._build_anim_data(units))

    @asynccontextmanager
    async def streaming(
        self, restore_effect: bool = False
    ) -> AsyncIterator[DigitalTwin]:
        """Put the device in external control mode for the duration of the block.

        Inside it, `sync()` sends one UDP datagram per call rather than an
        HTTP request, which is what makes animation practical.

        The panels keep whatever the last frame set them to on exit. Pass
        `restore_effect=True` to re-select the effect that was active before
        instead.
        """
        if self._stream is not None:
            raise NanoleafException("A streaming session is already open")

        previous_effect = self._nanoleaf.selected_effect

        # The device may accept the mode switch and still leave us unable to
        # stream -- an implausible port, a protocol we do not speak, a socket
        # that will not open. Every one of those has to run the restore, or the
        # panels are left in external control with nobody sending frames.
        try:
            host, port = await self._nanoleaf._enable_external_control()
            stream = await _PanelStream.open(host, port)
        except BaseException:
            await self._restore(restore_effect, previous_effect)
            raise

        self._stream = stream
        _LOGGER.debug("Streaming to %s:%s for %s panels", host, port, len(self._colors))
        try:
            yield self
        finally:
            self._stream = None
            stream.close()
            await self._restore(restore_effect, previous_effect)

    async def _restore(self, restore_effect: bool, effect: str | None) -> None:
        """Re-select the effect that was active before streaming, best effort.

        Runs while an exception from the caller's block may be in flight, so a
        failure here is logged rather than raised: replacing that exception
        would hide what actually went wrong.
        """
        if not restore_effect or effect is None:
            return
        try:
            await self._nanoleaf.set_effect(effect)
        except Exception:
            _LOGGER.warning(
                "Could not restore effect %r after streaming", effect, exc_info=True
            )

    def _build_stream_frame(self, transition_units: int) -> bytes:
        """Encode the buffer as an external control v2 datagram."""
        frame = bytearray(_STREAM_HEADER.pack(len(self._colors)))
        for panel_id, (red, green, blue, white) in self._colors.items():
            frame += _STREAM_RECORD.pack(
                panel_id, red, green, blue, white, transition_units
            )
        return bytes(frame)

    def _build_anim_data(self, transition_units: int) -> str:
        """Encode the buffer as the animData string of a static effect."""
        parts = [str(len(self._colors))]
        for panel_id, (red, green, blue, white) in self._colors.items():
            # panelId, frame count, colour, transition time
            parts += [
                str(panel_id),
                "1",
                str(red),
                str(green),
                str(blue),
                str(white),
                str(transition_units),
            ]
        return " ".join(parts)


class _PanelStream:
    """A UDP sender aimed at a device in external control mode."""

    def __init__(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    @classmethod
    async def open(cls, host: str, port: int) -> _PanelStream:
        """Connect a datagram endpoint to the device's streaming socket."""
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=(host, port)
            )
        except (OSError, OverflowError, ValueError) as err:
            raise StreamingUnsupported(
                f"Could not open a streaming socket to {host}:{port}: {err}"
            ) from err
        return cls(transport)

    def send(self, payload: bytes) -> None:
        """Send one frame."""
        if self._transport.is_closing():
            raise NanoleafException("The streaming session is closed")
        self._transport.sendto(payload)

    def close(self) -> None:
        """Release the socket."""
        self._transport.close()
