import pytest

from summoner.client import SummonerClient


def test_apply_config_sets_values_and_defaults():
    client = SummonerClient("sdk-config")

    try:
        client._apply_config(
            {
                "host": "127.0.0.1",
                "port": 9000,
                "logger": {},
                "hyper_parameters": {
                    "reconnection": {
                        "retry_delay_seconds": 7,
                        "primary_retry_limit": 4,
                    },
                    "receiver": {
                        "max_bytes_per_line": 4096,
                        "read_timeout_seconds": 12,
                    },
                    "sender": {
                        "concurrency_limit": 3,
                        "queue_maxsize": 5,
                        "event_bridge_maxsize": 21,
                        "max_worker_errors": 4,
                        "batch_drain": False,
                    },
                },
            }
        )

        assert client.host == "127.0.0.1"
        assert client.port == 9000
        assert client.retry_delay_seconds == 7
        assert client.primary_retry_limit == 4
        assert client.default_host == "127.0.0.1"
        assert client.default_port == 9000
        assert client.max_bytes_per_line == 4096
        assert client.read_timeout_seconds == 12
        assert client.max_concurrent_workers == 3
        assert client.send_queue_maxsize == 5
        assert client.event_bridge_maxsize == 21
        assert client.max_consecutive_worker_errors == 4
        assert client.batch_drain is False
    finally:
        client.loop.close()


def test_apply_config_rejects_invalid_sender_limits():
    client = SummonerClient("sdk-config-invalid")

    try:
        with pytest.raises(ValueError):
            client._apply_config(
                {
                    "logger": {},
                    "hyper_parameters": {
                        "sender": {
                            "concurrency_limit": 0,
                            "queue_maxsize": 1,
                        },
                    },
                }
            )

        with pytest.raises(ValueError):
            client._apply_config(
                {
                    "logger": {},
                    "hyper_parameters": {
                        "sender": {
                            "concurrency_limit": 1,
                            "queue_maxsize": 0,
                        },
                    },
                }
            )

        with pytest.raises(ValueError):
            client._apply_config(
                {
                    "logger": {},
                    "hyper_parameters": {
                        "sender": {
                            "concurrency_limit": 1,
                            "queue_maxsize": 1,
                            "max_worker_errors": 0,
                        },
                    },
                }
            )
    finally:
        client.loop.close()
