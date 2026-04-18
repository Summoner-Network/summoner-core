import asyncio
import json

import pytest

from summoner.client import ClientMerger, ClientTranslation
from summoner.client.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.process import Direction
from summoner.protocol.triggers import load_triggers
from .helpers import DummyWriter


def test_send_use_data_requires_one_argument():
    client = SummonerClient("send-data")
    client.flow().activate()

    try:
        with pytest.raises(TypeError):
            @client.send(route="request", on_actions={Action.STAY}, use_data=True)
            async def bad_sender() -> None:
                return None
    finally:
        client.loop.close()


def test_send_use_data_accepts_annotated_parameter_types():
    client = SummonerClient("send-data")
    client.flow().activate()

    try:
        @client.send(route="request", on_actions={Action.STAY}, use_data=True)
        async def good_sender(data: int) -> None:
            return None

        client.loop.run_until_complete(client._wait_for_registration())
        assert client.sender_index["request"][0].fn is good_sender
    finally:
        client.loop.close()


def test_send_default_dna_writes_use_data_false():
    client = SummonerClient("send-data")

    try:
        @client.send(route="request")
        async def plain_sender() -> None:
            return None

        client.loop.run_until_complete(client._wait_for_registration())
        assert client._dna_senders[0]["use_data"] is False
        assert client._dna_senders[0]["data_mode"] is None
        assert client._dna_senders[0]["every"] is None
        assert client._dna_senders[0]["run_while_kind"] == "none"
        assert client._dna_senders[0]["run_while_source"] is None
        dna_entries = json.loads(client.dna())
        assert dna_entries[0]["use_data"] is False
        assert dna_entries[0]["data_mode"] is None
        assert dna_entries[0]["every"] is None
        assert dna_entries[0]["run_while_kind"] == "none"
        assert dna_entries[0]["run_while_source"] is None
    finally:
        client.loop.close()


def test_client_translation_replays_use_data_sender():
    source = SummonerClient("source")
    source.flow().activate()

    translated = None

    try:
        @source.send(route="request", on_actions={Action.STAY}, use_data=True)
        async def source_sender(data: dict) -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        translated = ClientTranslation(json.loads(source.dna()), name="translated")
        translated.flow().activate()
        translated.initiate_senders()
        translated.loop.run_until_complete(translated._wait_for_registration())

        sender = translated.sender_index["request"][0]
        assert sender.use_data is True
    finally:
        source.loop.close()
        if translated is not None:
            translated.loop.close()


def test_client_translation_tolerates_legacy_sender_dna_without_use_data_field():
    source = SummonerClient("source")
    translated = None

    try:
        @source.send(route="request")
        async def source_sender() -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        dna_entries = json.loads(source.dna())
        assert dna_entries[0]["use_data"] is False
        del dna_entries[0]["use_data"]

        translated = ClientTranslation(dna_entries, name="translated")
        translated.initiate_senders()
        translated.loop.run_until_complete(translated._wait_for_registration())

        sender = translated.sender_index["request"][0]
        assert sender.use_data is False
    finally:
        source.loop.close()
        if translated is not None:
            translated.loop.close()


def test_client_merger_requires_flow_activation_for_use_data_sender():
    source = SummonerClient("source")
    source.flow().activate()

    merged = None

    try:
        @source.send(route="request", on_actions={Action.STAY}, use_data=True)
        async def source_sender(data: dict) -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        merged = ClientMerger([source], name="merged", close_subclients=False)

        with pytest.raises(RuntimeError):
            merged.initiate_senders()
    finally:
        source.loop.close()
        if merged is not None:
            merged.loop.close()


def test_client_merger_tolerates_legacy_imported_client_sender_defaults():
    source = SummonerClient("source")
    merged = None

    try:
        @source.send(route="request")
        async def source_sender() -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        del source._dna_senders[0]["multi"]
        del source._dna_senders[0]["on_triggers"]
        del source._dna_senders[0]["on_actions"]
        del source._dna_senders[0]["use_data"]

        merged = ClientMerger([source], name="merged", close_subclients=False)
        merged.initiate_senders()
        merged.loop.run_until_complete(merged._wait_for_registration())

        sender = merged.sender_index["request"][0]
        assert sender.multi is False
        assert sender.triggers is None
        assert sender.actions is None
        assert sender.use_data is False
    finally:
        source.loop.close()
        if merged is not None:
            merged.loop.close()


def test_receive_validation_is_not_globally_weakened():
    client = SummonerClient("send-data")

    try:
        with pytest.raises(TypeError):
            @client.receive(route="request")
            async def bad_receiver(payload: int) -> None:
                return None
    finally:
        client.loop.close()


def test_send_use_data_requires_reactive_sender():
    client = SummonerClient("send-data")
    client.flow().activate()

    try:
        with pytest.raises(ValueError):
            @client.send(route="request", use_data=True)
            async def bad_sender(data: dict) -> None:
                return None
    finally:
        client.loop.close()


def test_send_rejects_non_string_route_cleanly():
    client = SummonerClient("send-data")

    try:
        with pytest.raises(TypeError):
            client.send(route=None)
    finally:
        client.loop.close()


def test_receive_rejects_non_string_route_cleanly():
    client = SummonerClient("send-data")

    try:
        with pytest.raises(TypeError):
            client.receive(route=None)
    finally:
        client.loop.close()


def test_send_use_data_requires_non_empty_reactive_filters():
    client = SummonerClient("send-data")
    client.flow().activate()

    try:
        with pytest.raises(ValueError):
            @client.send(route="request", on_actions=set(), use_data=True)
            async def bad_sender(data: dict) -> None:
                return None
    finally:
        client.loop.close()


def test_send_use_data_passes_queued_event_data_to_sender():
    client = SummonerClient("send-data")
    client.flow().activate()

    seen: list[dict] = []

    try:
        @client.send(route="request", on_actions={Action.STAY}, use_data=True)
        async def good_sender(data: dict) -> dict:
            seen.append(data)
            return {"echo": data}

        client.loop.run_until_complete(client._wait_for_registration())

        writer = DummyWriter()
        stop_event = asyncio.Event()

        client.send_queue = asyncio.Queue()
        client.batch_drain = True
        client.max_consecutive_worker_errors = 3

        sender = client.sender_index["request"][0]
        payload = {"turn": 3, "text": "hello"}

        client.send_queue.put_nowait(("request", sender, payload))
        client.send_queue.put_nowait(None)

        client.loop.run_until_complete(client._send_worker(writer, stop_event))

        assert seen == [payload]
        assert client._dna_senders[0]["use_data"] is True

        dna_entries = json.loads(client.dna())
        assert dna_entries[0]["use_data"] is True

        assert writer.messages
        assert b'"echo"' in writer.messages[0]
        assert b'"hello"' in writer.messages[0]
    finally:
        client.loop.close()


def test_send_multi_worker_writes_all_payloads():
    client = SummonerClient("send-data")

    try:
        @client.send(route="request", multi=True)
        async def good_sender() -> list[dict]:
            return [{"part": 1}, {"part": 2}]

        client.loop.run_until_complete(client._wait_for_registration())

        writer = DummyWriter()
        stop_event = asyncio.Event()

        client.send_queue = asyncio.Queue()
        client.batch_drain = True
        client.max_consecutive_worker_errors = 3

        sender = client.sender_index["request"][0]

        client.send_queue.put_nowait(("request", sender, None))
        client.send_queue.put_nowait(None)

        client.loop.run_until_complete(client._send_worker(writer, stop_event))

        assert len(writer.messages) == 2
        assert b'"part": 1' in writer.messages[0]
        assert b'"part": 2' in writer.messages[1]
    finally:
        client.loop.close()


def test_send_use_data_preserves_one_call_per_queued_event():
    client = SummonerClient("send-data")
    client.flow().activate()

    Trigger = load_triggers(json_dict={"go": None})
    seen: list[dict] = []

    try:
        @client.send(route="request", on_actions={Action.STAY}, use_data=True)
        async def good_sender(data: dict) -> None:
            seen.append(data)
            if len(seen) == 2:
                async with client.connection_lock:
                    client._quit = True
            return None

        client.loop.run_until_complete(client._wait_for_registration())

        writer = DummyWriter()
        stop_event = asyncio.Event()

        client.send_queue = asyncio.Queue()
        client.event_bridge = asyncio.Queue()
        client.batch_drain = True
        client.max_concurrent_workers = 1
        client.max_consecutive_worker_errors = 3
        client.send_queue_maxsize = 8

        parsed_route = client.flow().parse_route("request")
        first = {"turn": 1, "text": "hello"}
        second = {"turn": 2, "text": "again"}

        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.STAY(Trigger.go, data=first)))
        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.STAY(Trigger.go, data=second)))

        worker = client.loop.create_task(client._send_worker(writer, stop_event))
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(worker)

        assert seen == [first, second]
    finally:
        client.loop.close()


def test_send_multi_use_data_on_triggers_only_emits_all_payloads():
    client = SummonerClient("send-data")
    client.flow().activate()

    Trigger = load_triggers(json_dict={"go": None})
    seen: list[int] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_last(payload: dict) -> dict:
            if payload["turn"] == 2 and payload["part"] == 2:
                await client.quit()
            return payload

        @client.send(route="request", on_triggers={Trigger.go}, use_data=True, multi=True)
        async def good_sender(data: dict) -> list[dict]:
            seen.append(data["turn"])
            return [
                {"turn": data["turn"], "part": 1},
                {"turn": data["turn"], "part": 2},
            ]

        client.loop.run_until_complete(client._wait_for_registration())

        writer = DummyWriter()
        stop_event = asyncio.Event()

        client.send_queue = asyncio.Queue()
        client.event_bridge = asyncio.Queue()
        client.batch_drain = True
        client.max_concurrent_workers = 1
        client.max_consecutive_worker_errors = 3
        client.send_queue_maxsize = 8

        parsed_route = client.flow().parse_route("request")
        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.TEST(Trigger.go, data={"turn": 1})))
        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.TEST(Trigger.go, data={"turn": 2})))

        worker = client.loop.create_task(client._send_worker(writer, stop_event))
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(worker)

        assert seen == [1, 2]
        assert len(writer.messages) == 4
        assert b'"turn": 1' in writer.messages[0]
        assert b'"part": 2' in writer.messages[1]
        assert b'"turn": 2' in writer.messages[2]
    finally:
        client.loop.close()


def test_send_without_use_data_keeps_legacy_dedup_behavior():
    client = SummonerClient("send-data")
    client.flow().activate()

    Trigger = load_triggers(json_dict={"go": None})
    seen: list[str] = []

    try:
        @client.send(route="request", on_actions={Action.STAY})
        async def good_sender() -> None:
            seen.append("sent")
            async with client.connection_lock:
                client._quit = True
            return None

        client.loop.run_until_complete(client._wait_for_registration())

        writer = DummyWriter()
        stop_event = asyncio.Event()

        client.send_queue = asyncio.Queue()
        client.event_bridge = asyncio.Queue()
        client.batch_drain = True
        client.max_concurrent_workers = 1
        client.max_consecutive_worker_errors = 3
        client.send_queue_maxsize = 8

        parsed_route = client.flow().parse_route("request")
        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.STAY(Trigger.go)))
        client.event_bridge.put_nowait(((), "peer-a", parsed_route, Action.STAY(Trigger.go)))

        worker = client.loop.create_task(client._send_worker(writer, stop_event))
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(worker)

        assert seen == ["sent"]
    finally:
        client.loop.close()
