import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Optional

from summoner.client import ClientMerger
from summoner.client.client import SummonerClient
from summoner.protocol import Action
from summoner.protocol.process import Direction
from summoner.protocol.triggers import load_triggers
from tests.helpers import DummyWriter


REPO_ROOT = Path(__file__).resolve().parents[1]


async def _wait_for(predicate, timeout: float = 0.5, interval: float = 0.002) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not met before timeout")
        await asyncio.sleep(interval)


async def _run_sender_loop(client: SummonerClient, writer: DummyWriter, stop_event: asyncio.Event, timeout: float = 1.0) -> None:
    await asyncio.wait_for(client.message_sender_loop(writer, stop_event), timeout=timeout)


def _configure_runtime(client: SummonerClient, *, workers: int = 1, queue_size: int = 8) -> None:
    client.max_concurrent_workers = workers
    client.send_queue_maxsize = queue_size
    client.event_bridge_maxsize = max(queue_size, 8)
    client.max_consecutive_worker_errors = 3
    client.batch_drain = True
    client.send_queue = asyncio.Queue(maxsize=client.send_queue_maxsize)
    client.event_bridge = asyncio.Queue(maxsize=client.event_bridge_maxsize)


def _event_for(client: SummonerClient, route: str, trigger: Any, *, data: Any = None):
    parsed_route = client.flow().parse_route(route)
    return ((1,), "tape:peer-a", parsed_route, Action.STAY(trigger, data=data))


def _pythonpath_env(extra_path: Optional[Path] = None) -> dict[str, str]:
    paths = []
    if extra_path is not None:
        paths.append(str(extra_path))
    existing = os.environ.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    paths.append(str(REPO_ROOT))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _run_subprocess(script: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_non_reactive_timed_sender_pauses_and_resumes_without_catch_up_burst():
    client = SummonerClient("timed-pause-resume")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    allowed = True
    call_times: list[float] = []
    orchestrator = None

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_on_second(payload: dict) -> dict:
            if payload["count"] == 2:
                await client.quit()
            return payload

        @client.send(route="tick", every=0.01, run_while=lambda: allowed)
        async def tick_sender() -> dict:
            call_times.append(client.loop.time())
            return {"count": len(call_times)}

        async def orchestrate() -> None:
            nonlocal allowed
            await _wait_for(lambda: len(call_times) >= 1)
            allowed = False
            await asyncio.sleep(0.05)
            allowed = True

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(call_times) == 2
        assert len(writer.messages) == 2
        assert call_times[1] - call_times[0] >= 0.045
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_reactive_timed_sender_disarms_and_requires_new_event_to_restart():
    client = SummonerClient("timed-reactive-rearm")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    enabled = True
    seen: list[int] = []
    orchestrator = None

    try:
        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            every=0.01,
            run_while=lambda: enabled,
        )
        async def timed_sender() -> dict:
            nonlocal enabled
            seen.append(len(seen) + 1)
            enabled = False
            if len(seen) == 2:
                await client.quit()
            return {"count": len(seen)}

        async def orchestrate() -> None:
            nonlocal enabled
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go))
            await _wait_for(lambda: len(seen) >= 1)
            enabled = True
            await asyncio.sleep(0.04)
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go))

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == [1, 2]
        assert len(writer.messages) >= 1
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_reactive_timed_duplicate_arm_does_not_reset_cadence():
    client = SummonerClient("timed-reactive-cadence")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    call_times: list[float] = []
    orchestrator = None

    try:
        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            every=0.1,
            run_while=True,
        )
        async def timed_sender() -> dict:
            call_times.append(client.loop.time())
            if len(call_times) == 2:
                await client.quit()
            return {"count": len(call_times)}

        async def orchestrate() -> None:
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go))
            await _wait_for(lambda: len(call_times) >= 1)
            await asyncio.sleep(0.015)
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go))

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(call_times) == 2
        assert 0.09 <= (call_times[1] - call_times[0]) < 0.14
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_reactive_timed_use_data_snapshot_freezes_mutations_after_buffering():
    client = SummonerClient("timed-snapshot")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[dict[str, Any]] = []
    payload = {"id": 2, "items": []}
    orchestrator = None

    try:
        @client.send(route="slow")
        async def slow_sender() -> None:
            await asyncio.sleep(0.05)
            return None

        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            use_data=True,
            data_mode="snapshot",
            every=0.1,
            run_while=True,
        )
        async def timed_sender(data: dict) -> dict:
            seen.append({"id": data["id"], "items": list(data["items"])})
            if len(seen) == 2:
                await client.quit()
            return {"id": data["id"], "items": list(data["items"])}

        async def orchestrate() -> None:
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go, data={"id": 1, "items": ["seed"]}))
            await _wait_for(lambda: len(seen) >= 1)
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go, data=payload))
            await _wait_for(
                lambda: any(len(runtime.pending_payloads) >= 1 for runtime in client.timed_sender_state.values())
            )
            payload["items"].append("mutated")

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=2, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == [
            {"id": 1, "items": ["seed"]},
            {"id": 2, "items": []},
        ]
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_reactive_timed_use_data_live_observes_mutations_after_buffering():
    client = SummonerClient("timed-live")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[dict[str, Any]] = []
    payload = {"id": 2, "items": []}
    orchestrator = None

    try:
        @client.send(route="slow")
        async def slow_sender() -> None:
            await asyncio.sleep(0.05)
            return None

        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            use_data=True,
            data_mode="live",
            every=0.1,
            run_while=True,
        )
        async def timed_sender(data: dict) -> dict:
            seen.append({"id": data["id"], "items": list(data["items"])})
            if len(seen) == 2:
                await client.quit()
            return {"id": data["id"], "items": list(data["items"])}

        async def orchestrate() -> None:
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go, data={"id": 1, "items": ["seed"]}))
            await _wait_for(lambda: len(seen) >= 1)
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go, data=payload))
            await _wait_for(
                lambda: any(len(runtime.pending_payloads) >= 1 for runtime in client.timed_sender_state.values())
            )
            payload["items"].append("mutated")

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=2, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == [
            {"id": 1, "items": ["seed"]},
            {"id": 2, "items": ["mutated"]},
        ]
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_reactive_timed_use_data_high_volume_backpressure_preserves_all_payloads():
    client = SummonerClient("timed-stress-backpressure")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    seen: list[int] = []
    total_payloads = 20

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_last(payload: dict) -> dict:
            if payload["id"] == total_payloads - 1:
                await client.quit()
            return payload

        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            use_data=True,
            data_mode="snapshot",
            every=0.01,
            run_while=True,
        )
        async def timed_sender(data: dict) -> dict:
            seen.append(data["id"])
            return {"id": data["id"]}

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=2)
        client.event_bridge = asyncio.Queue(maxsize=total_payloads + 1)

        async def enqueue_all() -> None:
            for idx in range(total_payloads):
                await client._enqueue_sender_event(
                    _event_for(client, "request", Trigger.go, data={"id": idx})
                )

        client.loop.run_until_complete(enqueue_all())

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert seen == list(range(total_payloads))
        assert len(writer.messages) == total_payloads
        assert len(client.timed_sender_state) == 1
    finally:
        client.loop.close()



def test_timed_sender_recovers_after_worker_exception_on_previous_tick():
    client = SummonerClient("timed-worker-recovery")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    attempts: list[str] = []

    try:
        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_success(payload: dict) -> dict:
            await client.quit()
            return payload

        @client.send(route="tick", every=0.01)
        async def unstable_sender() -> dict:
            attempts.append("tick")
            if len(attempts) == 1:
                raise RuntimeError("boom")
            return {"ok": True, "attempt": len(attempts)}

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=8)

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(attempts) >= 2
        assert len(writer.messages) == 1
    finally:
        client.loop.close()



def test_slow_async_run_while_does_not_block_reactive_timed_arming():
    client = SummonerClient("timed-run-while-lock-scope")
    client.flow().activate()
    Trigger = load_triggers(json_dict={"go": None})
    writer = DummyWriter()
    stop_event = asyncio.Event()
    gate_started = asyncio.Event()
    timed_call_times: list[float] = []
    armed_at: Optional[float] = None
    orchestrator = None

    try:
        async def slow_gate() -> bool:
            gate_started.set()
            await asyncio.sleep(0.05)
            return False

        @client.hook(Direction.SEND, priority=(1,))
        async def stop_after_first(payload: dict) -> dict:
            if payload["kind"] == "timed":
                await client.quit()
            return payload

        @client.send(route="slowtick", every=0.01, run_while=slow_gate)
        async def slow_sender() -> None:
            return None

        @client.send(
            route="request",
            on_actions={Action.STAY},
            on_triggers={Trigger.go},
            every=0.01,
            run_while=True,
        )
        async def timed_sender() -> dict:
            timed_call_times.append(client.loop.time())
            return {"kind": "timed"}

        async def orchestrate() -> None:
            nonlocal armed_at
            await gate_started.wait()
            armed_at = client.loop.time()
            await client._enqueue_sender_event(_event_for(client, "request", Trigger.go))

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=1, queue_size=8)

        client._start_send_workers(writer, stop_event)
        orchestrator = client.loop.create_task(orchestrate())
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event, timeout=1.5))
        client.loop.run_until_complete(orchestrator)
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert armed_at is not None
        assert len(timed_call_times) == 1
        assert timed_call_times[0] - armed_at < 0.03
    finally:
        if orchestrator is not None and not orchestrator.done():
            orchestrator.cancel()
            client.loop.run_until_complete(asyncio.gather(orchestrator, return_exceptions=True))
        client.loop.close()



def test_untimed_sender_batches_still_wait_for_slow_peer_sender():
    client = SummonerClient("timed-batch-gating")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    fast_call_times: list[float] = []

    try:
        @client.send(route="slow")
        async def slow_sender() -> dict:
            await asyncio.sleep(0.05)
            return {"kind": "slow"}

        @client.send(route="fast")
        async def fast_sender() -> dict:
            fast_call_times.append(client.loop.time())
            if len(fast_call_times) == 2:
                await client.quit()
            return {"kind": "fast", "count": len(fast_call_times)}

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=2, queue_size=8)

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event, timeout=1.5))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(fast_call_times) == 2
        assert fast_call_times[1] - fast_call_times[0] >= 0.04
    finally:
        client.loop.close()



def test_timed_scheduler_is_decoupled_from_slow_untimed_sender_batch():
    client = SummonerClient("timed-batch-decoupled")
    writer = DummyWriter()
    stop_event = asyncio.Event()
    timed_call_times: list[float] = []

    try:
        @client.send(route="slow")
        async def slow_sender() -> dict:
            await asyncio.sleep(0.05)
            return {"kind": "slow"}

        @client.send(route="tick", every=0.01)
        async def timed_sender() -> dict:
            timed_call_times.append(client.loop.time())
            if len(timed_call_times) == 2:
                await client.quit()
            return {"kind": "timed", "count": len(timed_call_times)}

        client.loop.run_until_complete(client._wait_for_registration())
        _configure_runtime(client, workers=2, queue_size=8)

        client._start_send_workers(writer, stop_event)
        client.loop.run_until_complete(_run_sender_loop(client, writer, stop_event, timeout=1.5))
        client.loop.run_until_complete(client._cleanup_workers())

        assert stop_event.is_set()
        assert len(timed_call_times) == 2
        assert timed_call_times[1] - timed_call_times[0] < 0.035
    finally:
        client.loop.close()



def test_client_merger_preserves_lambda_run_while_for_imported_client_sources():
    source = SummonerClient("source-lambda-run-while")
    merged = None
    flag = True

    try:
        @source.send(route="tick", every=0.1, run_while=lambda: flag)
        async def timed_sender() -> None:
            return None

        source.loop.run_until_complete(source._wait_for_registration())

        merged = ClientMerger([source], name="merged", close_subclients=False)
        merged.initiate_senders()
        merged.loop.run_until_complete(merged._wait_for_registration())

        sender = merged.sender_index["tick"][0]
        assert callable(sender.run_while)
        assert sender.run_while() is True
    finally:
        source.loop.close()
        if merged is not None:
            merged.loop.close()



def test_run_while_json_dna_portability_matrix(tmp_path: Path):
    helper_module = tmp_path / "helper_gate.py"
    export_script = tmp_path / "export_timed.py"
    translate_script = tmp_path / "translate_timed.py"
    dna_path = tmp_path / "timed_dna.json"

    helper_module.write_text(
        textwrap.dedent(
            """
            def importable_gate() -> bool:
                return True
            """
        )
    )

    export_script.write_text(
        textwrap.dedent(
            f"""
            from helper_gate import importable_gate
            from summoner.client.client import SummonerClient

            def main_gate() -> bool:
                return True

            client = SummonerClient('portable')

            @client.send(route='importable', every=0.1, run_while=importable_gate)
            async def send_importable() -> None:
                return None

            @client.send(route='main', every=0.1, run_while=main_gate)
            async def send_main() -> None:
                return None

            @client.send(route='lambda', every=0.1, run_while=lambda: True)
            async def send_lambda() -> None:
                return None

            client.loop.run_until_complete(client._wait_for_registration())
            with open({str(dna_path)!r}, 'w') as f:
                f.write(client.dna())
            client.loop.close()
            """
        )
    )

    translate_script.write_text(
        textwrap.dedent(
            f"""
            import json
            from summoner.client import ClientTranslation

            with open({str(dna_path)!r}) as f:
                dna = json.load(f)

            results = []
            for entry in dna:
                route = entry['route']
                client = ClientTranslation([entry], name=f'translate-{{route}}')
                try:
                    client.initiate_senders()
                    client.loop.run_until_complete(client._wait_for_registration())
                    results.append((route, 'ok'))
                except Exception as e:
                    results.append((route, type(e).__name__))
                finally:
                    client.loop.close()

            print(json.dumps(results))
            """
        )
    )

    env = _pythonpath_env(tmp_path)
    export_result = _run_subprocess(export_script, env=env)
    assert export_result.returncode == 0, export_result.stderr

    translate_result = _run_subprocess(translate_script, env=env)
    assert translate_result.returncode == 0, translate_result.stderr

    results = dict(json.loads(translate_result.stdout.strip()))
    assert results == {
        "importable": "ok",
        "main": "ok",
        "lambda": "ValueError",
    }
