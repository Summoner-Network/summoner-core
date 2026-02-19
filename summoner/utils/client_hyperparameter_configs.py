"""
In `client.py` config["hyper_parameters"]["sender"]
has several logic steps. Isolate that out here.
"""

from dataclasses import dataclass
from typing import List, Optional, TypeAlias

from summoner.utils.client_reconnect_configs import ReconnectConfig, RETRY_DELAY_SECONDS_TYPE, PORT_TYPE
from summoner.utils.client_sender_configs import SenderConfig

TIMEOUT_TYPE: TypeAlias = Optional[float]

class HyperparameterConfig:
    sender_config: SenderConfig
    reconnect_config: ReconnectConfig
    max_bytes_per_line: int
    read_timeout_seconds: TIMEOUT_TYPE
    
    def __init__(self,
                 sender_config: SenderConfig,
                 reconnect_config: ReconnectConfig,
                 max_bytes_per_line: int,
                 read_timeout_seconds: Optional[float]
                 ):
        self.sender_config = sender_config
        self.reconnect_config = reconnect_config
        self.max_bytes_per_line = max_bytes_per_line
        self.read_timeout_seconds = read_timeout_seconds
        
    def __post_init__(self):
        if self.max_bytes_per_line <= 0:\
            raise ValueError("The provided max_bytes_per_line must be an integer ≥ 1")
        if self.read_timeout_seconds is not None and self.read_timeout_seconds <= 0.0:\
            raise ValueError("The provided read_timeout_seconds must be an float ≥ 0.0 if provided")

    def merge_in(self, **kwargs) -> List[str]:
        all_problems = []
        all_problems.extend(self.sender_config.merge_in(**kwargs["sender"]))
        all_problems.extend(self.reconnect_config.merge_in(**kwargs["reconnection"]))
        receiver_cfg = kwargs["receiver"]
        if "max_bytes_per_line" in receiver_cfg:
            if not isinstance((z := receiver_cfg["max_bytes_per_line"]), int):
                all_problems.append(f"The provided max_bytes_per_line was not an integer. It was {type(z)}")
            else:
                if z <= 0:
                    all_problems.append("The provided max_bytes_per_line must be an integer ≥ 1")
                else:
                    self.max_bytes_per_line = z
        if "read_timeout_seconds" in receiver_cfg:
            if not isinstance((z := receiver_cfg["read_timeout_seconds"]), float | int | None):
                all_problems.append(f"The provided read_timeout_seconds was not an optional integer or a float. It was {type(z)}")
            else:
                if z is None:
                    self.read_timeout_seconds = None
                elif z < 0.0:
                    all_problems.append(f"The provided read_timeout_seconds was negative. It was {z}")
                else:
                    self.read_timeout_seconds = z
        return all_problems
