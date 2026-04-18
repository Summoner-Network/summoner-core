import asyncio
import json
from typing import Any

import pytest

from summoner.client import ClientTranslation
from summoner.client.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.process import Direction
from summoner.protocol.triggers import load_triggers
from .helpers import DummyWriter


def top_level_gate() -> bool:
    return True


RUN_WHILE_FLAG = True


def gate_using_module_flag() -> bool:
    return RUN_WHILE_FLAG


async def async_gate_true() -> bool:
    await asyncio.sleep(0)
    return True


def test_send_timed_validation_requires_every_for_run_while():
    client = SummonerClient("timed-validation")

    try:
        with pytest.raises(ValueError):
            @client.send(route="tick", run_while=True)
            async def bad_sender() -> None:
                return None
    finally:
        client.loop.close()


def test_send_timed_validation_requires_use_data_for_data_mode():
    client = SummonerClient("timed-validation")

    try:
        with pytest.raises(ValueError):
            @client.send(route="tick", data_mode="snapshot")
            async def bad_sender() -> None:
                return None
    finally:
        client.loop.close()


def test_send_timed_reactive_sender_requires_flow_activation():
    client = SummonerClient("timed-validation")
    Trigger = load_triggers(json_dict={"go": None})

    try:
        with pytest.raises(RuntimeError):
            @client.send(route="request", on_actions={Action.STAY}, on_triggers={Trigger.go}, every=0.1)
            async def bad_sender() -> None:
                return None
    finally:
        client.loop.close()


def test_non_reactive_timed_sender_fires_immediately_and_then_repeats():
    client = SummonerClient("timed-non-reactive")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    call_times: list[float] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_second(payload: dict) -> dict:
            if payload["count"] >= 2:
                await client.quit()
            return payload

        @client.send(route="tick", every=0.01)
        async def tick_sender() -> dict:
            call_times.append(client.loop.time())
            return {"count": len(call_times)}

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        start = client.loop.time()
        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(call_times) == 2
        assert call_times[0] - start < 0.05
        assert call_times[1] >= call_times[0]
        assert len(writer.messages) == 2
    finally:
        client.loop.close()


def test_non_reactive_timed_sender_accepts_async_run_while():
    client = SummonerClient("timed-async-run-while")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[str] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_first(payload: dict) -> dict:
            await client.quit()
            return payload

        @client.send(route="tick", every=0.01, run_while=async_gate_true)
        async def tick_sender() -> dict:
            seen.append("tick")
            return {"ok": True}

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == ["tick"]
        assert len(writer.messages) == 1
    finally:
        client.loop.close()


def test_non_reactive_timed_multi_sender_emits_all_payloads_per_tick():
    client = SummonerClient("timed-non-reactive-multi")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    call_counts: list[int] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_second_tick(payload: dict) -> dict:
            if payload["tick"] == 2 and payload["part"] == 2:
                await client.quit()
            return payload

        @client.send(route="tick", every=0.01, multi=True)
        async def tick_sender() -> list[dict]:
            tick = len(call_counts) + 1
            call_counts.append(tick)
            return [
                {"tick": tick, "part": 1},
                {"tick": tick, "part": 2},
            ]

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert call_counts == [1, 2]
        assert len(writer.messages) == 4
    finally:
        client.loop.close()


def test_reactive_timed_use_data_buffers_multiple_payloads_for_same_tick():
    client = SummonerClient("timed-reactive-data")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[dict[str, Any]] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_second(payload: dict) -> dict:
            if payload["id"] == 2:
                await client.quit()
            return payload

        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            use_data=True,
            data_mode="snapshot",
            every=0.01,
            run_while=True,
        )
        async def timed_sender(data: dict) -> dict:
            seen.append({"id": data["id"]})
            return {"id": data["id"]}

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        parsed_route = client.flow().parse_route("request")
        client.loop.run_until_complete(
            client._enqueue_sender_event(
                ((1,), "tape:peer-a", parsed_route, Action.STAY(Trigger.go, data={"id": 1}))
            )
        )
        client.loop.run_until_complete(
            client._enqueue_sender_event(
                ((1,), "tape:peer-a", parsed_route, Action.STAY(Trigger.go, data={"id": 2}))
            )
        )

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == [{"id": 1}, {"id": 2}]
        assert len(writer.messages) == 2
        assert len(client.timed_sender_state) == 1
    finally:
        client.loop.close()


def test_reactive_timed_multi_use_data_on_triggers_only_emits_all_payloads():
    client = SummonerClient("timed-reactive-multi-data")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[int] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_last(payload: dict) -> dict:
            if payload["turn"] == 2 and payload["part"] == 2:
                await client.quit()
            return payload

        @client.send(
            route="request",
            on_triggers={Trigger.go},
            use_data=True,
            data_mode="snapshot",
            every=0.01,
            run_while=True,
            multi=True,
        )
        async def timed_sender(data: dict) -> list[dict]:
            seen.append(data["turn"])
            return [
                {"turn": data["turn"], "part": 1},
                {"turn": data["turn"], "part": 2},
            ]

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        parsed_route = client.flow().parse_route("request")
        client.loop.run_until_complete(
            client._enqueue_sender_event(
                ((1,), "tape:peer-a", parsed_route, Action.TEST(Trigger.go, data={"turn": 1}))
            )
        )
        client.loop.run_until_complete(
            client._enqueue_sender_event(
                ((1,), "tape:peer-a", parsed_route, Action.TEST(Trigger.go, data={"turn": 2}))
            )
        )

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == [1, 2]
        assert len(writer.messages) == 4
    finally:
        client.loop.close()


def test_client_translation_replays_timed_sender_fields():
    source = SummonerClient("timed-source")
    translated = None

    try:
        @source.send(route="tick", every=0.5, run_while=top_level_gate)
        async def timed_sender() -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        dna_entries = json.loads(source.dna())
        assert dna_entries[0]["every"] == 0.5
        assert dna_entries[0]["run_while_kind"] == "callable"
        assert dna_entries[0]["run_while_name"]
        assert "def top_level_gate" in dna_entries[0]["run_while_source"]

        translated = ClientTranslation(dna_entries, name="timed-translated")
        translated.initiate_senders()
        translated.loop.run_until_complete(translated._wait_for_registration())

        sender = translated.sender_index["tick"][0]
        assert sender.every == 0.5
        assert callable(sender.run_while)
        assert sender.run_while() is True
    finally:
        source.loop.close()
        if translated is not None:
            translated.loop.close()


def test_client_translation_replays_run_while_source_with_context_globals():
    source = SummonerClient("timed-source-context")
    translated = None

    try:
        @source.send(route="tick", every=0.5, run_while=gate_using_module_flag)
        async def timed_sender() -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        dna_entries = json.loads(source.dna(include_context=True))
        translated = ClientTranslation(dna_entries, name="timed-translated-context")
        translated.initiate_senders()
        translated.loop.run_until_complete(translated._wait_for_registration())

        sender = translated.sender_index["tick"][0]
        assert callable(sender.run_while)
        assert sender.run_while() is True
    finally:
        source.loop.close()
        if translated is not None:
            translated.loop.close()
