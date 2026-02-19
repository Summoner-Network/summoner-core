"""
In `client.py` config["hyper_parameters"]["sender"]
has several logic steps. Isolate that out here.
"""
#pylint:disable=line-too-long

from dataclasses import dataclass
from typing import List, Optional


@dataclass(slots=True)
class SenderConfig:
    """
    The __post_init__
    enforces the strict requirements beyond types
    of positivity of the integers.
    Also the `merge_in` of dictionaries from json configurations
    will not maintain this property and give a list of strings
    for the logger to output as errors.
    """
    max_concurrent_workers: int
    batch_drain: bool
    send_queue_maxsize: int
    event_bridge_maxsize: Optional[int]
    max_consecutive_worker_errors: int

    def __post_init__(self):
        if self.max_concurrent_workers <= 0:\
            raise ValueError("The provided max_concurrent_workers must be an integer ≥ 1")
        if self.send_queue_maxsize <= 0:\
            raise ValueError("The provided send_queue_maxsize must be an integer ≥ 1")
        if self.max_consecutive_worker_errors <= 0:\
            raise ValueError("The provided max_consecutive_worker_errors must be an integer ≥ 1")

    #pylint:disable=too-many-branches
    def merge_in(self, **kwargs) -> List[str]:
        """
        The logic of _apply_config that is relevant to the sender_cfg.
        In addition rather than give ValueError with only one thing that is wrong,
        if there are multiple problems with the config, then the error should show them all.
        """
        all_problems = []
        if "concurrency_limit" in kwargs:
            if not isinstance((z := kwargs["concurrency_limit"]), int):
                all_problems.append(f"The provided concurrency_limit was not an integer. It was {type(z)}")
            else:
                if z <= 0:
                    all_problems.append("The provided concurrency_limit must be an integer ≥ 1")
                else:
                    self.max_concurrent_workers = z
        if "batch_drain" in kwargs:
            if not isinstance((z := kwargs["batch_drain"]), bool | None):
                all_problems.append(f"The provided batch_drain was not an optional bool. It was {type(z)}")
            else:
                if z is None:
                    self.batch_drain = False
                else:
                    self.batch_drain = z
        if "queue_maxsize" in kwargs:
            if not isinstance((z := kwargs["queue_maxsize"]), int):
                all_problems.append(f"The provided queue_maxsize was not an integer. It was {type(z)}")
            else:
                if z <= 0:
                    all_problems.append("The provided queue_maxsize must be an integer ≥ 1")
                else:
                    self.send_queue_maxsize = z
        if "event_bridge_maxsize" in kwargs:
            if not isinstance((z := kwargs["event_bridge_maxsize"]), int | None):
                all_problems.append(f"The provided event_bridge_maxsize was not an optional int. It was {type(z)}")
            else:
                if z is None:
                    self.event_bridge_maxsize = None
                else:
                    self.event_bridge_maxsize = z
        if "max_worker_errors" in kwargs:
            if not isinstance((z := kwargs["max_worker_errors"]), int):
                all_problems.append(f"The provided max_worker_errors was not an integer. It was {type(z)}")
            else:
                if z <= 0:
                    all_problems.append("The provided max_worker_errors must be an integer ≥ 1")
                else:
                    self.max_consecutive_worker_errors = z
        return all_problems

    def throttle_warning(self) -> Optional[str]:
        """
        The configuration is valid at all times by construction.
        However, this is valid but merits a warning.
        """
        if self.send_queue_maxsize < self.max_concurrent_workers:
            return f"queue_maxsize < concurrency_limit; back-pressure will throttle producers at {self.send_queue_maxsize}"
        return None
