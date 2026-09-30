"""Request behaviour: retries, auth failures, state writes."""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from aionanoleaf2 import InvalidEffect, InvalidToken, Nanoleaf, NoAuthToken, Unavailable


async def test_no_auth_token_raises_before_any_request(session) -> None:
    nanoleaf = Nanoleaf(session, "127.0.0.1")
    with pytest.raises(NoAuthToken):
        await nanoleaf.get_info()


async def test_401_raises_invalid_token_without_retrying() -> None:
    attempts = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        return web.Response(status=401)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = list(runner.addresses)[0][1]

    try:
        async with ClientSession() as session:
            nanoleaf = Nanoleaf(session, "127.0.0.1", auth_token="BAD", port=port)
            with pytest.raises(InvalidToken):
                await nanoleaf.get_info()
    finally:
        await runner.cleanup()

    assert attempts == 1, "an invalid token must not be retried"


async def test_retries_back_off_between_attempts(session, monkeypatch) -> None:
    """A bare retry loop burns all attempts in microseconds, which helps nobody.

    Three attempts sleep twice, doubling each time, and never after the final
    attempt. With the base delay shrunk to 50ms that is ~150ms; before the fix
    the entire loop finished in well under a millisecond.
    """
    monkeypatch.setattr("aionanoleaf2.nanoleaf._RETRY_BACKOFF", 0.05)

    nanoleaf = Nanoleaf(session, "127.0.0.1", auth_token="TOK", port=1, retries=3)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(Unavailable):
        await nanoleaf.get_info()
    elapsed = loop.time() - started

    assert elapsed >= 0.15, f"expected 0.05 + 0.10 of backoff, slept {elapsed:.4f}s"
    assert elapsed < 1.0, f"should not sleep after the last attempt, took {elapsed:.4f}s"


async def test_a_single_attempt_does_not_sleep(session, monkeypatch) -> None:
    monkeypatch.setattr("aionanoleaf2.nanoleaf._RETRY_BACKOFF", 0.5)

    nanoleaf = Nanoleaf(session, "127.0.0.1", auth_token="TOK", port=1, retries=1)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(Unavailable):
        await nanoleaf.get_info()
    assert loop.time() - started < 0.4


async def test_set_state_orders_on_last(device, make_nanoleaf) -> None:
    """The device applies fields in order, so "on" has to come after brightness."""
    nanoleaf = make_nanoleaf()
    await nanoleaf.set_state(on=True, brightness=60, brightness_transition=3)
    assert list(device.bodies[-1]) == ["brightness", "on"]
    assert device.bodies[-1]["brightness"] == {"value": 60, "duration": 3}


async def test_relative_changes_use_increment(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.set_brightness(-10, relative=True)
    assert device.bodies[-1] == {"brightness": {"increment": -10}}


async def test_set_state_without_arguments_makes_no_request(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.set_state()
    assert device.requests == []


async def test_turn_off_with_transition_dims_instead(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.turn_off(transition=5)
    assert device.bodies[-1] == {"brightness": {"value": 0, "duration": 5}}


async def test_unknown_effect_is_rejected_locally(device, make_nanoleaf) -> None:
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    with pytest.raises(InvalidEffect):
        await nanoleaf.set_effect("Not An Effect")
    assert ("PUT", "/api/v1/TESTTOKEN/effects") not in device.requests
