"""
In `client.py` config["hyper_parameters"]["reconnection"]
has several logic steps. Isolate that out here 
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(slots=True)
class ReconnectConfig:
    retry_delay_seconds: float
    primary_retry_limit: int
    default_host: Optional[str]
    default_port: Optional[int]
    default_retry_limit: int
    
    def __post_init__(self):
        assert self.retry_delay_seconds >= 0.0,\
            "The provided retry_delay_seconds must be a float ≥ 0.0"
        assert self.primary_retry_limit >= 0,\
            "The provided primary_retry_limit must be an integer ≥ 0"
        assert self.default_retry_limit >= 0,\
            "The provided default_retry_limit must be an integer ≥ 0"

    def merge_in(self, **kwargs) -> List[str]:
        all_problems = []
        if "retry_delay_seconds" in kwargs:
            if not isinstance((z := kwargs["retry_delay_seconds"]), float | int):
                all_problems.append(f"The provided retry_delay_seconds was not an integer or a float. It was {type(z)}")
            else:
                if z < 0.0:
                    all_problems.append(f"The provided retry_delay_seconds was negative. It was {z}")
                else:
                    self.retry_delay_seconds = z
        if "primary_retry_limit" in kwargs:
            if not isinstance((z := kwargs["primary_retry_limit"]), int):
                all_problems.append(f"The provided primary_retry_limit was not an integer. It was {type(z)}")
            else:
                if z < 0:
                    all_problems.append(f"The provided primary_retry_limit was negative. It was {z}")
                else:
                    self.primary_retry_limit = int(z)
        if "default_host" in kwargs:
            if isinstance((z := kwargs["default_host"]), str | None):
                self.default_host = z
            else:
                all_problems.append(f"The provided default_host was not an optional string. It was {type(z)}")
        if "default_port" in kwargs:
            if not isinstance((z := kwargs["default_port"]), int | None):
                all_problems.append(f"The provided default_port was not an optional integer. It was {type(z)}")
            else:
                self.default_port = z
        if "default_retry_limit" in kwargs:
            if not isinstance((z := kwargs["default_retry_limit"]), int):
                all_problems.append(f"The provided default_retry_limit was not an integer. It was {type(z)}")
            else:
                if z < 0:
                    all_problems.append(f"The provided default_retry_limit was negative. It was {z}")
                else:
                    self.default_retry_limit = z
        return all_problems
