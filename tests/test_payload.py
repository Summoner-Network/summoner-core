import json

import pytest

from summoner.protocol.payload import recover_with_types, wrap_with_types


def test_wrap_and_recover_typed_payload_round_trip():
    payload = {
        "text": "hello",
        "flag": True,
        "count": 3,
        "ratio": 1.5,
        "items": ["a", 2, None, {"nested": False}],
    }

    wrapped = wrap_with_types(payload, version="1.0.1")
    assert wrapped.endswith("\n")

    relayed = json.dumps({
        "remote_addr": "peer-a",
        "content": json.loads(wrapped),
    })

    assert recover_with_types(relayed) == {
        "remote_addr": "peer-a",
        "content": payload,
    }


def test_recover_with_types_returns_plain_content_for_non_enveloped_message():
    message = json.dumps({
        "remote_addr": "peer-a",
        "content": {"plain": "message"},
    })

    assert recover_with_types(message) == {
        "remote_addr": "peer-a",
        "content": {"plain": "message"},
    }


def test_recover_with_types_returns_raw_text_for_non_json_warning():
    assert recover_with_types("warning: disconnected\n") == "warning: disconnected"


def test_recover_with_types_returns_plain_json_without_outer_wrapper():
    assert recover_with_types('{"status": "ok", "count": 2}\n') == {
        "status": "ok",
        "count": 2,
    }


def test_wrap_with_types_rejects_unknown_version():
    with pytest.raises(ValueError):
        wrap_with_types({"hello": "world"}, version="9.9.9")
