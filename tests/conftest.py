"""Shared fixtures: a fake Nanoleaf device served over real HTTP."""
from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest
from aiohttp import ClientSession, web

from aionanoleaf2 import Nanoleaf

AUTH_TOKEN = "TESTTOKEN"

# A fully featured device (Shapes): everything lives in the root info payload.
FULL_INFO: dict[str, Any] = {
    "name": "Shapes ABCD",
    "serialNo": "S00001",
    "manufacturer": "Nanoleaf",
    "firmwareVersion": "7.1.1",
    "hardwareVersion": "2.1-2",
    "model": "NL42",
    "state": {
        "on": {"value": True},
        "brightness": {"value": 50, "max": 100, "min": 0},
        "hue": {"value": 120, "max": 360, "min": 0},
        "sat": {"value": 80, "max": 100, "min": 0},
        "ct": {"value": 4000, "max": 6500, "min": 1200},
        "colorMode": "effect",
    },
    "effects": {"select": "Nemo", "effectsList": ["Nemo", "Forest"]},
    "panelLayout": {
        "layout": {
            "numPanels": 2,
            "sideLength": 150,
            "positionData": [
                {"panelId": 1, "x": 0, "y": 0, "o": 0, "shapeType": 7},
                {"panelId": 2, "x": 100, "y": 0, "o": 60, "shapeType": 7},
            ],
        },
        "globalOrientation": {"value": 0, "max": 360, "min": 0},
    },
}

# An Essentials / Matter Wi-Fi device: identity fields only. See upstream issue #7.
ESSENTIALS_INFO: dict[str, Any] = {
    "name": "Ceiling Light",
    "serialNo": "S00002",
    "manufacturer": "Nanoleaf",
    "firmwareVersion": "4.0.0",
    "model": "NL71",
}

ESSENTIALS_STATE: dict[str, Any] = {
    "on": {"value": True},
    "brightness": {"value": 33, "max": 100, "min": 0},
    "hue": {"value": 10, "max": 360, "min": 0},
    "sat": {"value": 20, "max": 100, "min": 0},
    "ct": {"value": 2700, "max": 4000, "min": 2700},
    "colorMode": "ct",
}


class FakeNanoleaf:
    """Minimal stand-in for the Nanoleaf local HTTP API."""

    def __init__(self) -> None:
        self.info: dict[str, Any] = copy.deepcopy(FULL_INFO)
        self.state: dict[str, Any] | None = None
        self.effects_list: Any = None
        self.selected_effect: Any = None
        self.sse_body: bytes | None = None
        self.events_status = 200
        # Body to answer an extControl write with. None means 204, which is
        # what a v2 device (Canvas, Shapes, Elements, Lines) sends.
        self.ext_control_response: dict[str, Any] | None = None
        self.effect_writes: list[dict[str, Any]] = []
        self.effects_status = 204
        # Audio module payload; None means the device has no module (404).
        self.rhythm: dict[str, Any] | None = None
        # Layout rotation; None means the resource is absent (404).
        self.orientation: Any = None
        # Answer to the requestAll effects write.
        self.effect_details: Any = None
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self._runner: web.AppRunner | None = None
        self.port = 0
        # Lets open event streams end as soon as the test does, instead of
        # holding graceful shutdown open for aiohttp's default timeout.
        self._closing = asyncio.Event()

    async def start(self, host: str = "127.0.0.1") -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, 0)
        await site.start()
        sockets = list(self._runner.addresses)
        self.port = sockets[0][1]

    async def stop(self) -> None:
        self._closing.set()
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        self.requests.append((request.method, path))
        if request.can_read_body:
            self.bodies.append(await request.json())

        if path.endswith("/new"):
            return web.json_response({"auth_token": AUTH_TOKEN})

        # Everything past this point is token-scoped.
        suffix = path.split(f"/{AUTH_TOKEN}", 1)[-1].strip("/")

        if suffix == "events":
            if self.events_status != 200:
                return web.Response(status=self.events_status)
            resp = web.StreamResponse(
                status=200, headers={"Content-Type": "text/event-stream"}
            )
            await resp.prepare(request)
            if self.sse_body:
                await resp.write(self.sse_body)
            # Hold the stream open like a real device, but let go on teardown.
            await self._closing.wait()
            return resp

        if suffix == "":
            if request.method == "GET":
                return web.json_response(self.info)
            return web.Response(status=204)

        if suffix == "effects" and request.method == "PUT":
            body = self.bodies[-1]
            write = body.get("write", {})
            if write:
                self.effect_writes.append(write)
            if self.effects_status != 204:
                return web.Response(status=self.effects_status)
            if write.get("command") == "requestAll":
                if self.effect_details is None:
                    return web.Response(status=404)
                return web.json_response(self.effect_details)
            if self.ext_control_response is not None:
                return web.json_response(self.ext_control_response)
            return web.Response(status=204)

        if suffix == "rhythm":
            if request.method == "GET":
                if self.rhythm is None:
                    return web.Response(status=404)
                return web.json_response(self.rhythm)
            return web.Response(status=204)

        if suffix == "panelLayout/globalOrientation" and request.method == "GET":
            if self.orientation is None:
                return web.Response(status=404)
            return web.json_response(self.orientation)

        if suffix == "state" and request.method == "GET":
            if self.state is None:
                return web.Response(status=404)
            return web.json_response(self.state)

        if suffix == "effects/effectsList":
            if self.effects_list is None:
                return web.Response(status=404)
            return web.json_response(self.effects_list)

        if suffix == "effects/select" and request.method == "GET":
            if self.selected_effect is None:
                return web.Response(status=404)
            return web.json_response(self.selected_effect)

        return web.Response(status=204)


@pytest.fixture
async def device():
    """Run a fake Nanoleaf on loopback for the duration of a test."""
    fake = FakeNanoleaf()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
async def session():
    async with ClientSession() as client_session:
        yield client_session


@pytest.fixture
def make_nanoleaf(device, session):
    """Build a Nanoleaf pointed at the fake device."""

    def _make(**kwargs: Any) -> Nanoleaf:
        kwargs.setdefault("auth_token", AUTH_TOKEN)
        kwargs.setdefault("port", device.port)
        return Nanoleaf(session, "127.0.0.1", **kwargs)

    return _make


class UdpSink:
    """Collect datagrams sent to an ephemeral local UDP port."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.port = 0
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sink = self

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:
                sink.frames.append(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Protocol, local_addr=("127.0.0.1", 0)
        )
        self._transport = transport
        self.port = transport.get_extra_info("socket").getsockname()[1]

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()


@pytest.fixture
async def udp_sink():
    """A local UDP socket standing in for the device's streaming port."""
    sink = UdpSink()
    await sink.start()
    try:
        yield sink
    finally:
        sink.close()
