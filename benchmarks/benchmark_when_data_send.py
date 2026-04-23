import argparse
import asyncio
import os
import statistics
import sys
import time

from dataclasses import dataclass
from typing import Any


target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.triggers import load_triggers


@dataclass(frozen=True)
class BenchmarkCase:
    schedule: str
    strategy: str


@dataclass
class RuntimeStats:
    filter_evals: int = 0
    sender_calls: int = 0
    accepted: int = 0
    rejected_in_handler: int = 0


class CountingWriter:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.total_bytes = 0
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.messages.append(data)
        self.total_bytes += len(data)

    async def drain(self) -> None:
        self.drain_calls += 1


def _build_payloads(*, messages: int, accept_every: int, payload_bytes: int) -> list[dict[str, Any]]:
    blob = "x" * max(0, payload_bytes)
    payloads: list[dict[str, Any]] = []
    stride = max(1, accept_every)

    for index in range(messages):
        payloads.append(
            {
                "id": index,
                "keep": (index % stride) == 0,
                "blob": blob,
            }
        )

    return payloads


def _expected_accepted(payloads: list[dict[str, Any]]) -> int:
    return sum(1 for payload in payloads if payload["keep"])


def _configure_runtime(
        client: SummonerClient,
        *,
        workers: int,
        queue_size: int,
        bridge_size: int,
    ) -> None:
    client.max_concurrent_workers = workers
    client.send_queue_maxsize = max(1, queue_size)
    client.event_bridge_maxsize = max(1, bridge_size)
    client.max_consecutive_worker_errors = 3
    client.batch_drain = True
    client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
    client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)


async def _wait_for(predicate, *, timeout: float, interval: float = 0.001) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("benchmark case did not finish before timeout")
        await asyncio.sleep(interval)


def _run_case_once(
        case: BenchmarkCase,
        *,
        payloads: list[dict[str, Any]],
        workers: int,
        queue_size: int,
        bridge_size: int,
        timed_every: float,
        timeout: float,
    ) -> dict[str, Any]:
    client = SummonerClient(name=f"bench-{case.schedule}-{case.strategy}")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    stats = RuntimeStats()
    writer = CountingWriter()
    stop_event = asyncio.Event()
    route = "request"

    def payload_filter(data: dict[str, Any]) -> bool:
        stats.filter_evals += 1
        return bool(data["keep"])

    try:
        send_kwargs: dict[str, Any] = {
            "route": route,
            "on_actions": {Action.STAY},
            "on_triggers": {Trigger.go},
            "use_data": True,
            "data_mode": "snapshot",
        }
        if case.schedule == "timed":
            send_kwargs["every"] = timed_every
            send_kwargs["run_while"] = True
        elif case.schedule != "untimed":
            raise ValueError(f"Unknown schedule {case.schedule!r}")

        if case.strategy == "sdk_when_data":
            send_kwargs["when_data"] = payload_filter

            @client.send(**send_kwargs)
            async def sender(data: dict[str, Any]) -> dict:
                stats.sender_calls += 1
                stats.accepted += 1
                return {"id": data["id"], "blob": data["blob"]}

        elif case.strategy == "handler_guard":
            @client.send(**send_kwargs)
            async def sender(data: dict[str, Any]) -> Any:
                stats.sender_calls += 1
                stats.filter_evals += 1
                if not data["keep"]:
                    stats.rejected_in_handler += 1
                    return None
                stats.accepted += 1
                return {"id": data["id"], "blob": data["blob"]}

        else:
            raise ValueError(f"Unknown strategy {case.strategy!r}")

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(
            client,
            workers=workers,
            queue_size=queue_size,
            bridge_size=bridge_size,
        )

        expected_accepted = _expected_accepted(payloads)
        expected_filter_evals = len(payloads)
        expected_sender_calls = (
            expected_accepted if case.strategy == "sdk_when_data" else len(payloads)
        )

        async def drive_case() -> dict[str, Any]:
            message_task = asyncio.create_task(client.message_sender_loop(writer, stop_event))
            client._start_send_workers(writer, stop_event)

            started_at = time.perf_counter()
            try:
                parsed_route = client.flow().parse_route(route)
                for payload in payloads:
                    await client._enqueue_sender_event(
                        ((1,), "tape:peer-a", parsed_route, Action.STAY(Trigger.go, data=payload))
                    )

                await _wait_for(
                    lambda: (
                        stats.filter_evals >= expected_filter_evals
                        and stats.sender_calls >= expected_sender_calls
                        and len(writer.messages) >= expected_accepted
                    ),
                    timeout=timeout,
                )
                elapsed = time.perf_counter() - started_at
            finally:
                message_task.cancel()
                await asyncio.gather(message_task, return_exceptions=True)
                await client._cleanup_workers()

            return {
                "elapsed_seconds": elapsed,
                "filter_evals": stats.filter_evals,
                "sender_calls": stats.sender_calls,
                "accepted": stats.accepted,
                "rejected_in_handler": stats.rejected_in_handler,
                "written_messages": len(writer.messages),
                "written_bytes": writer.total_bytes,
                "drain_calls": writer.drain_calls,
            }

        return client.loop.run_until_complete(drive_case())
    finally:
        client.loop.close()


def run_case(
        case: BenchmarkCase,
        *,
        messages: int,
        accept_every: int,
        payload_bytes: int,
        rounds: int,
        warmup: int,
        workers: int,
        queue_size: int,
        bridge_size: int,
        timed_every: float,
        timeout: float,
    ) -> dict[str, Any]:
    payloads = _build_payloads(
        messages=messages,
        accept_every=accept_every,
        payload_bytes=payload_bytes,
    )
    durations: list[float] = []
    last_result: dict[str, Any] | None = None

    for round_index in range(warmup + rounds):
        result = _run_case_once(
            case,
            payloads=payloads,
            workers=workers,
            queue_size=queue_size,
            bridge_size=bridge_size,
            timed_every=timed_every,
            timeout=timeout,
        )
        last_result = result
        if round_index >= warmup:
            durations.append(result["elapsed_seconds"])

    assert last_result is not None

    best = min(durations)
    mean = statistics.mean(durations)
    accepted = _expected_accepted(payloads)

    return {
        "schedule": case.schedule,
        "strategy": case.strategy,
        "messages": len(payloads),
        "accepted_expected": accepted,
        "best_seconds": best,
        "mean_seconds": mean,
        "messages_per_second": len(payloads) / best,
        "accepted_per_second": accepted / best if accepted else 0.0,
        **last_result,
    }


def _format_rate(value: float) -> str:
    return f"{value:,.0f}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark reactive sender admission with SDK-side when_data "
            "versus handler-side early return."
        )
    )
    parser.add_argument("--messages", type=int, default=20000, help="Payloads to feed into each case.")
    parser.add_argument(
        "--accept-every",
        type=int,
        default=4,
        help="Keep one payload out of every N. Example: 4 -> keep 25%%.",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=256,
        help="Approximate payload blob size echoed by accepted sends.",
    )
    parser.add_argument("--rounds", type=int, default=5, help="Measured rounds per case.")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup rounds per case.")
    parser.add_argument("--workers", type=int, default=1, help="Send workers to use.")
    parser.add_argument(
        "--queue-size",
        type=int,
        default=0,
        help="Optional send queue maxsize. Default scales with messages.",
    )
    parser.add_argument(
        "--bridge-size",
        type=int,
        default=0,
        help="Optional event bridge maxsize. Default scales with messages.",
    )
    parser.add_argument(
        "--timed-every",
        type=float,
        default=0.01,
        help="Cadence for the timed benchmark rows.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-round timeout in seconds.",
    )
    parser.add_argument(
        "--include-timed",
        action="store_true",
        help="Include timed reactive sender cases in addition to untimed cases.",
    )
    args = parser.parse_args(argv)

    queue_size = args.queue_size if args.queue_size > 0 else max(8, args.messages * 2 + 8)
    bridge_size = args.bridge_size if args.bridge_size > 0 else max(8, args.messages * 2 + 8)

    cases = [
        BenchmarkCase(schedule="untimed", strategy="sdk_when_data"),
        BenchmarkCase(schedule="untimed", strategy="handler_guard"),
    ]
    if args.include_timed:
        cases.extend(
            [
                BenchmarkCase(schedule="timed", strategy="sdk_when_data"),
                BenchmarkCase(schedule="timed", strategy="handler_guard"),
            ]
        )

    results = [
        run_case(
            case,
            messages=max(1, args.messages),
            accept_every=max(1, args.accept_every),
            payload_bytes=max(0, args.payload_bytes),
            rounds=max(1, args.rounds),
            warmup=max(0, args.warmup),
            workers=max(1, args.workers),
            queue_size=queue_size,
            bridge_size=bridge_size,
            timed_every=max(0.0001, args.timed_every),
            timeout=max(0.1, args.timeout),
        )
        for case in cases
    ]

    print(
        "schedule  strategy        messages  accepted  filter_evals  sender_calls  written_msgs  written_kib  best_s   mean_s   msg/s    accepted/s"
    )
    print(
        "--------  --------------  --------  --------  ------------  ------------  ------------  -----------  -------  -------  -------  ----------"
    )
    for result in results:
        print(
            f"{result['schedule']:<8}  "
            f"{result['strategy']:<14}  "
            f"{result['messages']:>8}  "
            f"{result['accepted']:>8}  "
            f"{result['filter_evals']:>12}  "
            f"{result['sender_calls']:>12}  "
            f"{result['written_messages']:>12}  "
            f"{(result['written_bytes'] / 1024):>11.1f}  "
            f"{result['best_seconds']:>7.4f}  "
            f"{result['mean_seconds']:>7.4f}  "
            f"{_format_rate(result['messages_per_second']):>7}  "
            f"{_format_rate(result['accepted_per_second']):>10}"
        )

    print()
    print("delta vs handler_guard")
    print("schedule  sender_calls_saved  wire_bytes_saved  best_time_saved_s  best_time_ratio")
    print("--------  ------------------  ----------------  -----------------  ---------------")

    for schedule in sorted({result["schedule"] for result in results}):
        sdk = next(
            result for result in results
            if result["schedule"] == schedule and result["strategy"] == "sdk_when_data"
        )
        handler = next(
            result for result in results
            if result["schedule"] == schedule and result["strategy"] == "handler_guard"
        )
        sender_calls_saved = handler["sender_calls"] - sdk["sender_calls"]
        wire_bytes_saved = handler["written_bytes"] - sdk["written_bytes"]
        best_time_saved = handler["best_seconds"] - sdk["best_seconds"]
        best_time_ratio = (
            handler["best_seconds"] / sdk["best_seconds"]
            if sdk["best_seconds"] > 0
            else float("inf")
        )
        print(
            f"{schedule:<8}  "
            f"{sender_calls_saved:>18}  "
            f"{wire_bytes_saved:>16}  "
            f"{best_time_saved:>17.4f}  "
            f"{best_time_ratio:>15.3f}"
        )

    print()
    print("Interpretation:")
    print("- `sender_calls_saved` approximates avoided SendInvocation queueing and worker execution.")
    print("- `wire_bytes_saved` is usually 0 versus handler-side early return, because both approaches suppress rejected sends.")
    print("- The main win from `when_data` is local orchestration efficiency: fewer sender coroutine calls, less worker occupancy, and lower queue pressure.")
    print("- Timed rows still buffer reactive payloads before admission, so their gain begins at sender-task admission rather than event buffering.")
    print("- Tiny runs are timing-noisy; use larger `--messages` values when comparing elapsed time.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
