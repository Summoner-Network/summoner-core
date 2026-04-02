from summoner.client import ClientMerger, ClientTranslation, SummonerClient
from summoner.protocol import Action


def test_client_translation_applies_context_globals_recipes_and_optional_imports():
    dna_list = [
        {
            "type": "__context__",
            "var_name": "sdk_agent",
            "imports": ["from math import sqrt"],
            "globals": {"SDK_FLAG": "ready"},
            "recipes": {"SDK_NUMBERS": "[1, 2, 3]"},
        }
    ]

    translated = ClientTranslation(dna_list, name="translated", allow_context_imports=False)

    try:
        assert translated._var_name == "sdk_agent"
        assert translated._sandbox_globals["sdk_agent"] is translated
        assert translated._sandbox_globals["SDK_FLAG"] == "ready"
        assert translated._sandbox_globals["SDK_NUMBERS"] == [1, 2, 3]
        assert "sqrt" not in translated._sandbox_globals
    finally:
        translated.loop.close()


def test_client_translation_can_execute_context_imports_when_allowed():
    dna_list = [
        {
            "type": "__context__",
            "var_name": "sdk_agent",
            "imports": ["from math import sqrt"],
        }
    ]

    translated = ClientTranslation(dna_list, name="translated-imports", allow_context_imports=True)

    try:
        assert translated._sandbox_globals["sqrt"](25) == 5
    finally:
        translated.loop.close()


def test_client_merger_normalizes_dna_source_context_and_reports_skipped_imports():
    dna_list = [
        {
            "type": "__context__",
            "var_name": "sdk_agent",
            "imports": ["from math import sqrt"],
            "globals": {"SDK_FLAG": "ready"},
            "recipes": {"SDK_NUMBERS": "[1, 2]"},
        }
    ]

    merged = ClientMerger([dna_list], name="merged-dna", allow_context_imports=False, close_subclients=False)

    try:
        source = merged.sources[0]
        assert source["kind"] == "dna"
        assert source["var_name"] == "sdk_agent"
        assert source["globals"]["sdk_agent"] is merged
        assert source["globals"]["SDK_FLAG"] == "ready"
        assert source["globals"]["SDK_NUMBERS"] == [1, 2]
        assert "sqrt" not in source["globals"]
        assert source["import_report"]["skipped"] == ["from math import sqrt"]
    finally:
        merged.loop.close()


def test_client_merger_infers_imported_client_var_name_and_rebinds_handler_globals():
    source = SummonerClient("source-client")
    merged = None
    binding_name = "sdk_merge_agent"
    previous = globals().get(binding_name)
    globals()[binding_name] = source

    try:
        @source.receive(route="request", priority=(1,))
        async def receive_request(payload: str):
            return {"payload": payload, "client_name": sdk_merge_agent.name}

        @source.send(route="request", on_actions={Action.STAY})
        async def send_request() -> dict:
            return {"client_name": sdk_merge_agent.name}

        source.loop.run_until_complete(source._wait_for_registration())

        merged = ClientMerger([source], name="merged-client", close_subclients=False)
        assert merged.sources[0]["var_name"] == binding_name

        merged.initiate_receivers()
        merged.initiate_senders()
        merged.loop.run_until_complete(merged._wait_for_registration())

        assert merged.loop.run_until_complete(merged.receiver_index["request"].fn("hello")) == {
            "payload": "hello",
            "client_name": "merged-client",
        }
        assert merged.loop.run_until_complete(merged.sender_index["request"][0].fn()) == {
            "client_name": "merged-client",
        }
    finally:
        if previous is None:
            globals().pop(binding_name, None)
        else:
            globals()[binding_name] = previous
        source.loop.close()
        if merged is not None:
            merged.loop.close()
