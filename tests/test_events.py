"""Server-sent event stream handling."""
from __future__ import annotations

import asyncio

import pytest

from aionanoleaf2 import EffectsEvent, StateEvent

SSE = (
    b"id: 1\r\ndata: {\"events\":[{\"attr\":1,\"value\":0}]}\r\n\r\n"
    b"id: 1\r\ndata: {\"events\":[{\"attr\":2,\"value\":77}]}\r\n\r\n"
    b"id: 3\r\ndata: {\"events\":[{\"attr\":1,\"value\":\"Forest\"}]}\r\n\r\n"
    b"id: 1\r\ndata: {\"events\":[{\"attr\":6,\"value\":\"ct\"}]}\r\n\r\n"
)


async def listen_briefly(nanoleaf, **callbacks) -> None:
    task = asyncio.create_task(nanoleaf.listen_events(**callbacks))
    await asyncio.sleep(0.25)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_state_and_effect_events_update_internal_state(device, make_nanoleaf) -> None:
    device.sse_body = SSE
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()

    states: list[StateEvent] = []
    effects: list[EffectsEvent] = []

    async def on_state(event: StateEvent) -> None:
        states.append(event)

    async def on_effects(event: EffectsEvent) -> None:
        effects.append(event)

    await listen_briefly(nanoleaf, state_callback=on_state, effects_callback=on_effects)

    assert [(e.attribute, e.value) for e in states] == [
        ("is_on", 0),
        ("brightness", 77),
        ("color_mode", "ct"),
    ]
    assert [e.effect for e in effects] == ["Forest"]
    assert nanoleaf.brightness == 77
    assert nanoleaf.color_mode == "ct"
    assert nanoleaf.effect == "Forest"


async def test_is_on_stays_a_bool_after_an_event(device, make_nanoleaf) -> None:
    """The API sends 1/0; is_on is documented as a bool, so "is False" must work."""
    device.sse_body = b"id: 1\r\ndata: {\"events\":[{\"attr\":1,\"value\":0}]}\r\n\r\n"
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    assert nanoleaf.is_on is True

    await listen_briefly(nanoleaf)

    assert nanoleaf.is_on is False
    assert isinstance(nanoleaf.is_on, bool)


async def test_malformed_lines_are_skipped(device, make_nanoleaf) -> None:
    device.sse_body = (
        b"\r\n"
        b"id: not-a-number\r\ndata: {}\r\n\r\n"
        b"id: 1\r\nnotdata: junk\r\n\r\n"
        b"id: 1\r\ndata: {not json}\r\n\r\n"
        b"id: 1\r\ndata: {\"events\":[{\"attr\":2,\"value\":42}]}\r\n\r\n"
    )
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    await listen_briefly(nanoleaf)
    assert nanoleaf.brightness == 42


async def test_a_sync_callback_is_rejected_clearly(device, make_nanoleaf) -> None:
    """Callbacks are awaited, so a plain function must fail with a useful message."""
    device.sse_body = b"id: 1\r\ndata: {\"events\":[{\"attr\":2,\"value\":42}]}\r\n\r\n"
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()

    def not_a_coroutine(event) -> None:
        pass

    task = asyncio.create_task(nanoleaf.listen_events(state_callback=not_a_coroutine))
    await asyncio.sleep(0.25)
    assert task.done()
    with pytest.raises(TypeError, match="coroutine functions"):
        task.result()


async def test_callback_tasks_are_referenced(device, make_nanoleaf) -> None:
    """asyncio only weakly references tasks; a slow callback must still finish."""
    device.sse_body = b"id: 1\r\ndata: {\"events\":[{\"attr\":2,\"value\":42}]}\r\n\r\n"
    nanoleaf = make_nanoleaf()
    await nanoleaf.get_info()
    finished = asyncio.Event()

    async def slow(event) -> None:
        await asyncio.sleep(0.05)
        finished.set()

    task = asyncio.create_task(nanoleaf.listen_events(state_callback=slow))
    await asyncio.wait_for(finished.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
