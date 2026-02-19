"""
Tests for manipulating client_config
"""
#pylint:disable=line-too-long

from pathlib import Path
from summoner.utils.json_handlers import load_config
from summoner.utils.client_reconnect_configs import ReconnectConfig
from summoner.utils.client_sender_configs import SenderConfig
from summoner.utils.client_hyperparameter_configs import HyperparameterConfig

def test_template_client_reconnection_config():
    """
    Handling of reconnection hyperparameters part
    of the config of client config json files
    """
    tempate_client_config = Path() / "templates" / "client_config.json"
    assert tempate_client_config.is_file(), f"{tempate_client_config} is not a file"
    big_config = load_config(str(tempate_client_config))
    try:
        just_reconnection = big_config["hyper_parameters"]["reconnection"]
    except KeyError as e:
        raise KeyError(f"Had this: {big_config}") from e
    reconnect_config = ReconnectConfig(
        retry_delay_seconds=85.38,
        primary_retry_limit=4380,
        default_host="junk",
        default_port = 390359,
        default_retry_limit=593090,
        )
    problems = reconnect_config.merge_in(**just_reconnection)
    assert len(problems) == 0, \
        "The template was supposed to be a well formed client configuration. So it should have had no problems."
    assert reconnect_config.retry_delay_seconds == just_reconnection["retry_delay_seconds"]
    assert reconnect_config.primary_retry_limit == just_reconnection["primary_retry_limit"]
    assert reconnect_config.default_host == just_reconnection["default_host"]
    assert reconnect_config.default_port == just_reconnection["default_port"]
    assert reconnect_config.default_retry_limit == just_reconnection["default_retry_limit"]

def test_template_client_server_config():
    """
    Handling of sender hyperparameters part
    of the config of client config json files
    """
    tempate_client_config = Path() / "templates" / "client_config.json"
    assert tempate_client_config.is_file(), f"{tempate_client_config} is not a file"
    big_config = load_config(str(tempate_client_config))
    try:
        just_sender = big_config["hyper_parameters"]["sender"]
    except KeyError as e:
        raise KeyError(f"Had this: {big_config}") from e
    sender_config = SenderConfig(
        max_concurrent_workers = 1,
        batch_drain = False,
        send_queue_maxsize = 100,
        event_bridge_maxsize = None,
        max_consecutive_worker_errors = 5,
        )
    problems = sender_config.merge_in(**just_sender)
    assert len(problems) == 0, \
        "The template was supposed to be a well formed client configuration. So it should have had no problems."
    assert sender_config.max_concurrent_workers == just_sender["concurrency_limit"]
    assert sender_config.batch_drain == just_sender["batch_drain"]
    assert sender_config.send_queue_maxsize == just_sender["queue_maxsize"]
    assert sender_config.event_bridge_maxsize == just_sender["event_bridge_maxsize"]
    assert sender_config.max_consecutive_worker_errors == just_sender["max_worker_errors"]

def test_combined():
    """
    Handling of hyperparameters part
    of the config of client config json files
    """
    # Load up with the default's
    sender_config = SenderConfig(
        max_concurrent_workers = 1,
        batch_drain = False,
        send_queue_maxsize = 100,
        event_bridge_maxsize = None,
        max_consecutive_worker_errors = 5,
    )
    reconnect_config = ReconnectConfig(
        retry_delay_seconds=85.38,
        primary_retry_limit=4380,
        default_host="junk",
        default_port = 390359,
        default_retry_limit=593090,
    )

    hyperparameter_config = HyperparameterConfig(
        sender_config,
        reconnect_config,
        max_bytes_per_line = 1024*64,
        read_timeout_seconds = None,
        )

    # The actual config from the file which will override the defaults
    tempate_client_config = Path() / "templates" / "client_config.json"
    assert tempate_client_config.is_file(), f"{tempate_client_config} is not a file"
    big_config = load_config(str(tempate_client_config))
    template_hyperparameter_config = big_config["hyper_parameters"]

    problems = hyperparameter_config.merge_in(**template_hyperparameter_config)
    assert len(problems) == 0, \
        "The template was supposed to be a well formed client configuration. So it should have had no problems."
    assert hyperparameter_config.sender_config.max_concurrent_workers == template_hyperparameter_config["sender"]["concurrency_limit"]
    assert hyperparameter_config.sender_config.batch_drain == template_hyperparameter_config["sender"]["batch_drain"]
    assert hyperparameter_config.sender_config.send_queue_maxsize == template_hyperparameter_config["sender"]["queue_maxsize"]
    assert hyperparameter_config.sender_config.event_bridge_maxsize == template_hyperparameter_config["sender"]["event_bridge_maxsize"]
    assert hyperparameter_config.sender_config.max_consecutive_worker_errors == template_hyperparameter_config["sender"]["max_worker_errors"]

    assert hyperparameter_config.reconnect_config.retry_delay_seconds == template_hyperparameter_config["reconnection"]["retry_delay_seconds"]
    assert hyperparameter_config.reconnect_config.primary_retry_limit == template_hyperparameter_config["reconnection"]["primary_retry_limit"]
    assert hyperparameter_config.reconnect_config.default_host == template_hyperparameter_config["reconnection"]["default_host"]
    assert hyperparameter_config.reconnect_config.default_port == template_hyperparameter_config["reconnection"]["default_port"]
    assert hyperparameter_config.reconnect_config.default_retry_limit == template_hyperparameter_config["reconnection"]["default_retry_limit"]

    assert hyperparameter_config.max_bytes_per_line == template_hyperparameter_config["receiver"]["max_bytes_per_line"]
    assert hyperparameter_config.read_timeout_seconds == template_hyperparameter_config["receiver"]["read_timeout_seconds"]
