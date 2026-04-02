import json

from summoner.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.process import Direction


def test_iter_registered_handler_functions_yields_all_registered_handlers():
    client = SummonerClient("client-context")

    try:
        @client.upload_states()
        async def upload_states(payload: dict) -> dict:
            return {"payload": payload}

        @client.download_states()
        async def download_states(payload: dict):
            return payload

        @client.hook(Direction.SEND, priority=(1,))
        async def send_hook(payload: dict) -> dict:
            return {"payload": payload}

        @client.receive(route="request", priority=(2,))
        async def receive_request(payload: dict):
            return {"payload": payload}

        @client.send(route="request", on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"sent": True}

        client.loop.run_until_complete(client._wait_for_registration())

        handlers = list(client._iter_registered_handler_functions())
        assert handlers == [
            upload_states,
            download_states,
            receive_request,
            send_request,
            send_hook,
        ]
    finally:
        client.loop.close()


def test_infer_client_binding_name_is_exported_in_context_dna():
    client = SummonerClient("binding-name")
    binding_name = "sdk_bound_client_name"
    previous = globals().get(binding_name)
    globals()[binding_name] = client

    try:
        @client.send(route="request", on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"sent": True}

        client.loop.run_until_complete(client._wait_for_registration())

        assert client._infer_client_binding_name() == binding_name

        dna_list = json.loads(client.dna(include_context=True))
        assert dna_list[0]["type"] == "__context__"
        assert dna_list[0]["var_name"] == binding_name
    finally:
        if previous is None:
            globals().pop(binding_name, None)
        else:
            globals()[binding_name] = previous
        client.loop.close()


def test_reset_client_intent_clears_quit_and_travel_flags():
    client = SummonerClient("intent-reset")

    try:
        client.loop.run_until_complete(client.travel_to("127.0.0.1", 9000))
        client.loop.run_until_complete(client.quit())

        assert client._travel is True
        assert client._quit is True
        assert client.host == "127.0.0.1"
        assert client.port == 9000

        client.loop.run_until_complete(client._reset_client_intent())

        assert client._travel is False
        assert client._quit is False
        assert client.host == "127.0.0.1"
        assert client.port == 9000
    finally:
        client.loop.close()


def test_initialize_compiles_arrow_patterns_when_flow_is_active():
    client = SummonerClient("flow-init")

    try:
        client.flow().activate()
        client.flow().add_arrow_style("-", ("[", "]"), ",", ">")

        assert client.flow()._regex_ready is False
        client.initialize()
        assert client.flow()._regex_ready is True
    finally:
        client.loop.close()
