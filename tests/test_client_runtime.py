import asyncio
import json
from typing import Any

from summoner.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.payload import wrap_with_types
from summoner.protocol.process import Direction, Node
from summoner.protocol.triggers import load_triggers
from .helpers import DummyWriter


def test_message_receiver_loop_applies_receive_hooks_updates_state_and_bridges_events():
    client = SummonerClient("runtime-receiver")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"ok": None})

    downloads: list[dict[str, list[Node]]] = []
    reader = asyncio.StreamReader()
    stop_event = asyncio.Event()

    try:
        @client.hook(Direction.RECEIVE, priority=(1,))
        async def receive_hook_one(payload: dict) -> dict:
            updated = dict(payload)
            updated["trace"] = [1]
            return updated

        @client.hook(Direction.RECEIVE, priority=(2,))
        async def receive_hook_two(payload: dict) -> dict:
            updated = dict(payload)
            updated["trace"] = list(payload["trace"]) + [2]
            return updated

        @client.upload_states()
        async def upload_states(payload: dict) -> dict[str, str]:
            return {payload["remote_addr"]: "request"}

        @client.download_states()
        async def download_states(payload: dict) -> None:
            downloads.append(payload)
            return None

        @client.receive(route="request", priority=(5,))
        async def receive_request(payload: dict) -> Any:
            await client.quit()
            return Action.STAY(
                Trigger.ok,
                data={
                    "trace": payload["trace"],
                    "content": payload["content"],
                },
            )

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_bytes_per_line = 4096
        client.read_timeout_seconds = 0.1
        client.event_bridge = asyncio.Queue(maxsize=8)

        raw_message = json.dumps(
            {
                "remote_addr": "peer-a",
                "content": json.loads(
                    wrap_with_types({"body": "hello"}, version=client.core_version)
                ),
            }
        ) + "\n"

        reader.feed_data(raw_message.encode())
        reader.feed_eof()

        client.loop.run_until_complete(client.message_receiver_loop(reader, stop_event))

        assert stop_event.is_set()
        assert downloads == [{"peer-a": [Node("request")]}]

        priority, key, route, event = client.event_bridge.get_nowait()
        assert priority == (5,)
        assert key == "tape:peer-a"
        assert str(route) == "request"
        assert isinstance(event, Action.STAY)
        assert event.data == {
            "trace": [1, 2],
            "content": {"body": "hello"},
        }
    finally:
        client.loop.close()


def test_message_sender_loop_applies_send_hooks_and_writes_wrapped_payload():
    client = SummonerClient("runtime-sender")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def send_hook_one(payload: Any) -> dict:
            updated = dict(payload)
            updated["trace"] = ["hook-1"]
            return updated

        @client.hook(Direction.SEND, priority=(2,))
        async def send_hook_two(payload: Any) -> dict:
            updated = dict(payload)
            updated["trace"] = list(payload["trace"]) + ["hook-2"]
            await client.quit()
            return updated

        @client.send(route="request", on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"body": "hello"}

        client.loop.run_until_complete(client._wait_for_registration())

        client.max_concurrent_workers = 1
        client.send_queue_maxsize = 8
        client.event_bridge_maxsize = 8
        client.max_consecutive_worker_errors = 3
        client.batch_drain = True
        client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
        client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)

        parsed_route = client.flow().parse_route("request")
        client.event_bridge.put_nowait(
            ((3,), "tape:peer-a", parsed_route, Action.STAY(Trigger.go))
        )

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(client.message_sender_loop(writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert writer.drain_calls == 1
        assert len(writer.messages) == 1

        envelope = json.loads(writer.messages[0].decode())
        assert envelope["_version"] == client.core_version
        assert envelope["_payload"] == {
            "body": "hello",
            "trace": ["hook-1", "hook-2"],
        }
    finally:
        client.loop.close()
