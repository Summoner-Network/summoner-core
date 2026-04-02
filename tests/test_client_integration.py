import json

from summoner.client import ClientMerger, ClientTranslation, SummonerClient
from summoner.protocol import Action
from summoner.protocol.process import Direction


SDK_CONTEXT_VALUE = "sdk-context"


def test_client_registers_all_handler_types_and_exports_dna_schema():
    client = SummonerClient("sdk-setup")

    try:
        @client.upload_states()
        async def upload_states(payload: dict) -> dict:
            return {"state": "ready", "context": SDK_CONTEXT_VALUE}

        @client.download_states()
        async def download_states(payload: dict):
            return {"downloaded": payload, "context": SDK_CONTEXT_VALUE}

        @client.hook(Direction.RECEIVE, priority=(1,))
        async def receive_hook(payload: dict) -> dict:
            return {"hook": "receive", "payload": payload, "context": SDK_CONTEXT_VALUE}

        @client.hook(Direction.SEND, priority=(2,))
        async def send_hook(payload: dict) -> dict:
            return {"hook": "send", "payload": payload, "context": SDK_CONTEXT_VALUE}

        @client.receive(route="request", priority=(3,))
        async def receive_request(payload: str):
            return {"received": payload, "context": SDK_CONTEXT_VALUE}

        @client.send(route="request", multi=False, on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"sent": True, "context": SDK_CONTEXT_VALUE}

        client.loop.run_until_complete(client._wait_for_registration())

        assert client._upload_states is upload_states
        assert client._download_states is download_states
        assert client.receiving_hooks[(1,)] is receive_hook
        assert client.sending_hooks[(2,)] is send_hook
        assert client.receiver_index["request"].fn is receive_request
        assert client.sender_index["request"][0].fn is send_request

        dna_entries = json.loads(client.dna())
        assert [entry["type"] for entry in dna_entries] == [
            "upload_states",
            "download_states",
            "receive",
            "send",
            "hook",
            "hook",
        ]

        send_entry = dna_entries[3]
        assert send_entry["route"] == "request"
        assert send_entry["multi"] is False
        assert send_entry["use_data"] is False
        assert send_entry["on_actions"] == ["Stay"]
        assert send_entry["on_triggers"] == []
    finally:
        client.loop.close()


def test_client_dna_with_context_supports_translation_of_mixed_handlers():
    client = SummonerClient("sdk-context")
    translated = None

    try:
        @client.upload_states()
        async def upload_states(payload: str) -> dict:
            return {"state": payload, "context": SDK_CONTEXT_VALUE}

        @client.download_states()
        async def download_states(payload: dict):
            return {"downloaded": payload, "context": SDK_CONTEXT_VALUE}

        @client.hook(Direction.RECEIVE, priority=(1,))
        async def receive_hook(payload: str) -> dict:
            return {"hook": "receive", "payload": payload, "context": SDK_CONTEXT_VALUE}

        @client.receive(route="request", priority=(2,))
        async def receive_request(payload: str):
            return {"received": payload, "context": SDK_CONTEXT_VALUE}

        @client.send(route="request", on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"sent": True, "context": SDK_CONTEXT_VALUE}

        client.loop.run_until_complete(client._wait_for_registration())

        dna_list = json.loads(client.dna(include_context=True))
        assert dna_list[0]["type"] == "__context__"
        assert dna_list[0]["globals"]["SDK_CONTEXT_VALUE"] == SDK_CONTEXT_VALUE

        translated = ClientTranslation(dna_list, name="translated-context")
        translated.initiate_all()
        translated.loop.run_until_complete(translated._wait_for_registration())

        assert translated.loop.run_until_complete(translated._upload_states("seed")) == {
            "state": "seed",
            "context": SDK_CONTEXT_VALUE,
        }
        assert translated.loop.run_until_complete(translated._download_states({"peer": "a"})) == {
            "downloaded": {"peer": "a"},
            "context": SDK_CONTEXT_VALUE,
        }
        assert translated.loop.run_until_complete(translated.receiving_hooks[(1,)]("payload")) == {
            "hook": "receive",
            "payload": "payload",
            "context": SDK_CONTEXT_VALUE,
        }
        assert translated.loop.run_until_complete(translated.receiver_index["request"].fn("hello")) == {
            "received": "hello",
            "context": SDK_CONTEXT_VALUE,
        }
        assert translated.loop.run_until_complete(translated.sender_index["request"][0].fn()) == {
            "sent": True,
            "context": SDK_CONTEXT_VALUE,
        }
    finally:
        client.loop.close()
        if translated is not None:
            translated.loop.close()


def test_client_merger_initiate_all_combines_sources():
    client_a = SummonerClient("sdk-a")
    client_b = SummonerClient("sdk-b")
    merged = None

    try:
        @client_a.upload_states()
        async def upload_states(payload: str) -> dict:
            return {"from": "a", "payload": payload}

        @client_a.receive(route="request_a", priority=(1,))
        async def receive_a(payload: str):
            return {"route": "a", "payload": payload}

        @client_a.send(route="request_a", on_actions={Action.STAY})
        async def send_a() -> dict:
            return {"sender": "a"}

        @client_b.download_states()
        async def download_states(payload: dict):
            return {"from": "b", "payload": payload}

        @client_b.hook(Direction.SEND, priority=(2,))
        async def send_hook(payload: str) -> dict:
            return {"hook": "b", "payload": payload}

        @client_b.receive(route="request_b", priority=(3,))
        async def receive_b(payload: str):
            return {"route": "b", "payload": payload}

        @client_b.send(route="request_b", on_actions={Action.TEST})
        async def send_b() -> dict:
            return {"sender": "b"}

        client_a.loop.run_until_complete(client_a._wait_for_registration())
        client_b.loop.run_until_complete(client_b._wait_for_registration())

        merged = ClientMerger([client_a, client_b], name="merged", close_subclients=False)
        merged.initiate_all()
        merged.loop.run_until_complete(merged._wait_for_registration())

        assert merged._upload_states is not None
        assert merged._download_states is not None
        assert (2,) in merged.sending_hooks
        assert "request_a" in merged.receiver_index
        assert "request_b" in merged.receiver_index
        assert "request_a" in merged.sender_index
        assert "request_b" in merged.sender_index

        assert merged.loop.run_until_complete(merged._upload_states("seed")) == {
            "from": "a",
            "payload": "seed",
        }
        merged_payload = {"peer": ["request_b"]}
        assert merged.loop.run_until_complete(merged._download_states(merged_payload)) == {
            "from": "b",
            "payload": merged_payload,
        }
        assert merged.loop.run_until_complete(merged.sending_hooks[(2,)]("seed")) == {
            "hook": "b",
            "payload": "seed",
        }
        assert merged.loop.run_until_complete(merged.receiver_index["request_a"].fn("hello")) == {
            "route": "a",
            "payload": "hello",
        }
        assert merged.loop.run_until_complete(merged.receiver_index["request_b"].fn("world")) == {
            "route": "b",
            "payload": "world",
        }
        assert merged.loop.run_until_complete(merged.sender_index["request_a"][0].fn()) == {
            "sender": "a",
        }
        assert merged.loop.run_until_complete(merged.sender_index["request_b"][0].fn()) == {
            "sender": "b",
        }
    finally:
        client_a.loop.close()
        client_b.loop.close()
        if merged is not None:
            merged.loop.close()
