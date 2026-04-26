import argparse
import asyncio
import copy
import json
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
from summoner.protocol.payload import wrap_with_types
from summoner.protocol.triggers import load_triggers


@dataclass(frozen=True)
class BenchmarkCase:
    schedule: str
    strategy: str


@dataclass
class RuntimeStats:
    receiver_calls: int = 0
    sender_calls: int = 0
    accepted: int = 0
    user_queue_puts: int = 0
    user_queue_gets: int = 0
    user_copy_ops: int = 0


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


def _build_payloads(*, messages: int, payload_bytes: int) -> list[dict[str, Any]]:
    blob = "x" * max(0, payload_bytes)
    payloads: list[dict[str, Any]] = []

    for index in range(messages):
        payloads.append(
            {
                "id": index,
                "blob": blob,
                "items": [index, index + 1, index + 2],
                "meta": {"group": index % 8},
            }
        )

    return payloads


def _build_raw_messages(client: SummonerClient, payloads: list[dict[str, Any]]) -> list[bytes]:
    raw_messages: list[bytes] = []

    for payload in payloads:
        raw_messages.append(
            (
                json.dumps(
                    {
                        "remote_addr": "peer-a",
                        "content": json.loads(
                            wrap_with_types(payload, version=client.core_version)
                        ),
                    }
                )
                + "\n"
            ).encode()
        )

    return raw_messages


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
    client.read_timeout_seconds = 0.05
    client.max_bytes_per_line = 4 * 1024 * 1024
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


def _sdk_expected_copy_ops(strategy: str, messages: int) -> int:
    if strategy == "sdk_use_data_snapshot":
        return 2 * messages
    return 0


def _sender_data_mode(strategy: str) -> str:
    if strategy == "sdk_use_data_snapshot":
        return "snapshot"
    return "live"


def _is_queue_shim(strategy: str) -> bool:
    return strategy in {
        "queue_shim_live",
        "queue_shim_copy_put",
        "queue_shim_snapshot_strict",
    }


def _is_sdk_direct(strategy: str) -> bool:
    return strategy in {"sdk_use_data_live", "sdk_use_data_snapshot"}


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
    user_queue: asyncio.Queue[Any] | None = None

    try:
        if _is_queue_shim(case.strategy):
            user_queue = asyncio.Queue(maxsize=max(1, len(payloads) * 2 + 8))

        @client.upload_states()
        async def upload_states(payload: dict) -> dict:
            return {payload["remote_addr"]: route}

        @client.receive(route=route)
        async def receive_request(payload: dict) -> Any:
            stats.receiver_calls += 1
            content = payload["content"]

            if case.strategy == "sdk_use_data_live":
                return Action.STAY(Trigger.go, data=content)

            if case.strategy == "sdk_use_data_snapshot":
                return Action.STAY(Trigger.go, data=content)

            assert user_queue is not None

            if case.strategy == "queue_shim_live":
                await user_queue.put(content)
                stats.user_queue_puts += 1
            elif case.strategy == "queue_shim_copy_put":
                await user_queue.put(copy.deepcopy(content))
                stats.user_queue_puts += 1
                stats.user_copy_ops += 1
            elif case.strategy == "queue_shim_snapshot_strict":
                await user_queue.put(copy.deepcopy(content))
                stats.user_queue_puts += 1
                stats.user_copy_ops += 1
            else:
                raise ValueError(f"Unknown strategy {case.strategy!r}")

            # Carry a lightweight token in the event so the sender still receives
            # one activation per receive, matching how agents would preserve
            # event-by-event semantics without direct payload handoff.
            return Action.STAY(Trigger.go, data=content["id"])

        send_kwargs: dict[str, Any] = {
            "route": route,
            "on_actions": {Action.STAY},
            "on_triggers": {Trigger.go},
            "use_data": True,
            "data_mode": _sender_data_mode(case.strategy),
        }
        if case.schedule == "timed":
            send_kwargs["every"] = timed_every
            send_kwargs["run_while"] = True
        elif case.schedule != "untimed":
            raise ValueError(f"Unknown schedule {case.schedule!r}")

        @client.send(**send_kwargs)
        async def sender(data: Any) -> dict:
            stats.sender_calls += 1

            if _is_sdk_direct(case.strategy):
                payload = data
            elif _is_queue_shim(case.strategy):
                assert user_queue is not None
                try:
                    payload = user_queue.get_nowait()
                except asyncio.QueueEmpty as exc:
                    raise RuntimeError(
                        "queue shim sender was triggered without a queued payload"
                    ) from exc
                stats.user_queue_gets += 1

                if case.strategy == "queue_shim_snapshot_strict":
                    payload = copy.deepcopy(payload)
                    stats.user_copy_ops += 1

                if payload["id"] != data:
                    raise RuntimeError(
                        f"queue shim token/payload mismatch: token={data!r}, "
                        f"payload_id={payload['id']!r}"
                    )
            else:
                raise ValueError(f"Unknown strategy {case.strategy!r}")

            stats.accepted += 1
            return {"id": payload["id"], "blob": payload["blob"]}

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(
            client,
            workers=workers,
            queue_size=queue_size,
            bridge_size=bridge_size,
        )

        raw_messages = _build_raw_messages(client, payloads)
        expected_messages = len(payloads)

        async def drive_case() -> dict[str, Any]:
            reader = asyncio.StreamReader()
            for raw_message in raw_messages:
                reader.feed_data(raw_message)

            receiver_task = asyncio.create_task(client.message_receiver_loop(reader, stop_event))
            sender_task = asyncio.create_task(client.message_sender_loop(writer, stop_event))
            client._start_send_workers(writer, stop_event)

            started_at = time.perf_counter()
            try:
                await _wait_for(
                    lambda: (
                        stats.receiver_calls >= expected_messages
                        and stats.sender_calls >= expected_messages
                        and stats.accepted >= expected_messages
                        and len(writer.messages) >= expected_messages
                        and (user_queue is None or user_queue.qsize() == 0)
                    ),
                    timeout=timeout,
                )
                elapsed = time.perf_counter() - started_at
            finally:
                stop_event.set()
                receiver_task.cancel()
                sender_task.cancel()
                await asyncio.gather(receiver_task, sender_task, return_exceptions=True)
                await client._cleanup_workers()

            return {
                "elapsed_seconds": elapsed,
                "receiver_calls": stats.receiver_calls,
                "sender_calls": stats.sender_calls,
                "accepted": stats.accepted,
                "user_queue_puts": stats.user_queue_puts,
                "user_queue_gets": stats.user_queue_gets,
                "user_copy_ops": stats.user_copy_ops,
                "sdk_expected_copy_ops": _sdk_expected_copy_ops(case.strategy, len(payloads)),
                "written_messages": len(writer.messages),
                "written_bytes": writer.total_bytes,
            }

        return client.loop.run_until_complete(drive_case())
    finally:
        client.loop.close()


def run_case(
        case: BenchmarkCase,
        *,
        messages: int,
        payload_bytes: int,
        rounds: int,
        warmup: int,
        workers: int,
        queue_size: int,
        bridge_size: int,
        timed_every: float,
        timeout: float,
    ) -> dict[str, Any]:
    payloads = _build_payloads(messages=messages, payload_bytes=payload_bytes)
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

    return {
        "schedule": case.schedule,
        "strategy": case.strategy,
        "messages": len(payloads),
        "best_seconds": best,
        "mean_seconds": mean,
        "messages_per_second": len(payloads) / best,
        **last_result,
    }


def _format_rate(value: float) -> str:
    return f"{value:,.0f}"


def _find_result(
        results: list[dict[str, Any]],
        *,
        schedule: str,
        strategy: str,
    ) -> dict[str, Any]:
    return next(
        result for result in results
        if result["schedule"] == schedule and result["strategy"] == strategy
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark fair agent-style receive-to-send payload handoff: "
            "direct SDK use_data versus a user-managed queue shim."
        )
    )
    parser.add_argument("--messages", type=int, default=20000, help="Payloads to feed into each case.")
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=256,
        help="Approximate payload blob size echoed by the sender.",
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
        "--timeout",
        type=float,
        default=10.0,
        help="Per-round timeout in seconds.",
    )
    parser.add_argument(
        "--timed-every",
        type=float,
        default=0.01,
        help="Cadence for the timed benchmark rows.",
    )
    schedule_group = parser.add_mutually_exclusive_group()
    schedule_group.add_argument(
        "--include-timed",
        action="store_true",
        help="Include timed reactive sender cases in addition to untimed cases.",
    )
    schedule_group.add_argument(
        "--timed-only",
        action="store_true",
        help="Run only the timed reactive sender cases.",
    )
    schedule_group.add_argument(
        "--untimed-only",
        action="store_true",
        help="Run only the untimed sender cases. This is also the default.",
    )
    args = parser.parse_args(argv)

    queue_size = args.queue_size if args.queue_size > 0 else max(8, args.messages * 2 + 8)
    bridge_size = args.bridge_size if args.bridge_size > 0 else max(8, args.messages * 2 + 8)

    if args.timed_only:
        schedules = ["timed"]
    elif args.include_timed:
        schedules = ["untimed", "timed"]
    else:
        schedules = ["untimed"]

    strategies = [
        "sdk_use_data_live",
        "sdk_use_data_snapshot",
        "queue_shim_live",
        "queue_shim_copy_put",
        "queue_shim_snapshot_strict",
    ]
    cases = [
        BenchmarkCase(schedule=schedule, strategy=strategy)
        for schedule in schedules
        for strategy in strategies
    ]

    results = [
        run_case(
            case,
            messages=max(1, args.messages),
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
        "schedule  strategy                messages  recv_calls  sender_calls  user_q_put  user_q_get  user_copy  sdk_copy  written_kib  best_s   mean_s   msg/s"
    )
    print(
        "--------  ----------------------  --------  ----------  ------------  ----------  ----------  ---------  --------  -----------  -------  -------  -------"
    )
    for result in results:
        print(
            f"{result['schedule']:<8}  "
            f"{result['strategy']:<22}  "
            f"{result['messages']:>8}  "
            f"{result['receiver_calls']:>10}  "
            f"{result['sender_calls']:>12}  "
            f"{result['user_queue_puts']:>10}  "
            f"{result['user_queue_gets']:>10}  "
            f"{result['user_copy_ops']:>9}  "
            f"{result['sdk_expected_copy_ops']:>8}  "
            f"{(result['written_bytes'] / 1024):>11.1f}  "
            f"{result['best_seconds']:>7.4f}  "
            f"{result['mean_seconds']:>7.4f}  "
            f"{_format_rate(result['messages_per_second']):>7}"
        )

    print()
    print("delta vs queue shims")
    print(
        "schedule  comparison         user_q_ops_saved  user_copy_ops_saved  best_delta_s  mean_delta_s  best_ratio_vs_sdk  mean_ratio_vs_sdk"
    )
    print(
        "--------  -----------------  ----------------  -------------------  ------------  ------------  -----------------  -----------------"
    )

    for schedule in sorted({result["schedule"] for result in results}):
        live_sdk = _find_result(results, schedule=schedule, strategy="sdk_use_data_live")
        live_shim = _find_result(results, schedule=schedule, strategy="queue_shim_live")
        snapshot_sdk = _find_result(results, schedule=schedule, strategy="sdk_use_data_snapshot")
        snapshot_shim = _find_result(results, schedule=schedule, strategy="queue_shim_copy_put")
        snapshot_strict_shim = _find_result(
            results,
            schedule=schedule,
            strategy="queue_shim_snapshot_strict",
        )

        comparisons = [
            ("live", live_sdk, live_shim),
            ("snapshot", snapshot_sdk, snapshot_shim),
            ("snapshot_strict", snapshot_sdk, snapshot_strict_shim),
        ]

        for label, sdk_result, shim_result in comparisons:
            sdk_queue_ops = sdk_result["user_queue_puts"] + sdk_result["user_queue_gets"]
            shim_queue_ops = shim_result["user_queue_puts"] + shim_result["user_queue_gets"]
            queue_ops_saved = shim_queue_ops - sdk_queue_ops
            user_copy_ops_saved = shim_result["user_copy_ops"] - sdk_result["user_copy_ops"]
            best_time_saved = shim_result["best_seconds"] - sdk_result["best_seconds"]
            mean_time_saved = shim_result["mean_seconds"] - sdk_result["mean_seconds"]
            best_time_ratio = (
                shim_result["best_seconds"] / sdk_result["best_seconds"]
                if sdk_result["best_seconds"] > 0
                else float("inf")
            )
            mean_time_ratio = (
                shim_result["mean_seconds"] / sdk_result["mean_seconds"]
                if sdk_result["mean_seconds"] > 0
                else float("inf")
            )
            print(
                f"{schedule:<8}  "
                f"{label:<17}  "
                f"{queue_ops_saved:>16}  "
                f"{user_copy_ops_saved:>19}  "
                f"{best_time_saved:>12.4f}  "
                f"{mean_time_saved:>12.4f}  "
                f"{best_time_ratio:>17.3f}  "
                f"{mean_time_ratio:>17.3f}"
            )

    print()
    print("Interpretation:")
    print("- This benchmark exercises the local path agents actually use: wrapped inbound message, receive handler, emitted action, and sender invocation.")
    print("- `queue_shim_*` is the fair userland comparison: receive stores the payload in an application queue and emits a lightweight token event so the sender still runs once per received message.")
    print("- `queue_shim_copy_put` models the intuitive user-level snapshot approximation: copy once on queue put, then hand the queued object to the sender.")
    print("- `queue_shim_snapshot_strict` is a stricter control row: it copies on queue put and again on queue get to approximate the SDK's stronger per-delivery snapshot isolation more closely.")
    print("- Sender-call counts stay aligned across direct and queue-shim rows, so timing differences are no longer caused by bulk-drain batching shortcuts.")
    print("- `best_delta_s` and `mean_delta_s` are `queue_shim - sdk`, so positive values mean the SDK row was faster and negative values mean the queue shim was faster.")
    print("- When `best` and `mean` disagree on direction, treat the result as parity/noise rather than a reliable win for either side.")
    print("- If sender output is the same, written wire bytes should also stay the same.")
    print("- The main SDK gain is avoiding user-managed queue put/get operations and extra queue bookkeeping while keeping payload handoff inside the runtime.")
    print("- `sdk_use_data_snapshot` still intentionally performs internal copies for stronger delivery semantics; compare it against both snapshot queue rows, not just the intuitive one.")
    if any(result["schedule"] == "timed" for result in results):
        print("- Timed rows feed a burst of inbound messages and measure buffered timed handoff behavior rather than one-interval-per-message latency.")
    print("- Tiny runs are timing-noisy; use larger `--messages` values when comparing elapsed time.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
