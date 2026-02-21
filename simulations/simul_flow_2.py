from summoner.protocol.flow import Flow
from typing import Coroutine, Tuple, Optional, Callable, Any
from summoner.protocol.triggers import (
    Signal,
    Action, 
    Event, 
    Move, 
    Stay, 
    Test, 
    extract_signal, 
)
from summoner.protocol.process import Node, ParsedRoute, StateTape, TapeType, Receiver, Sender, TapeActivation
from collections import defaultdict
import random
import pprint
import asyncio
import time
from queue import Queue, Empty

flow = Flow()
flow.activate() #in use
Trigger = flow.triggers()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
flow.add_arrow_style(stem="=", brackets=("{", "}"), separator=";", tip=")")
flow.compile_arrow_patterns() # regex

# List of available Action classes
ACTIONS = [Move, Stay, Test]

# List of available Action attributes
ACTION_ATTRIBUTES = [Action.MOVE, Action.STAY, Action.TEST]

# Possible trigger attribute names
TRIGGER_NAMES = [
    "OK",
    "acceptable",
    "extra_ok",
    "all_good",
    "error",
    "minor",
    "major"
]

def generate_receiver_fn(arrow, trigger_class):
    async def fn(msg):
        t0 = time.time()
        # await asyncio.sleep(2)
        print(f"\033[96m{arrow}\033[0m", "...", msg, str(time.time()-t0)[:4])

        if random.random() < 0.25:
            return None
    
        ActionClass = random.choice(ACTIONS)
        signal_name = random.choice(TRIGGER_NAMES)
        signal = getattr(trigger_class, signal_name)
        return ActionClass(signal)
    
    return fn

def random_priority() -> tuple[int, ...]:
    n = random.randint(1, 3)  # Random length between 1 and 3
    return tuple(random.randint(0, 2) for _ in range(n))  # Elements from [0, 2]

def upload_states(flow: Flow, routes: list[str]) -> dict[str, list[Node]]:
    possible_states = []
    
    for route in routes:
        parsed_route = flow.parse_route(route)
        if parsed_route.source:
            possible_states.extend(parsed_route.source)

    result: dict[str, list[Node]] = {}
    num_keys = random.randint(2, 5)  # Arbitrary number of keys to generate

    for i in range(num_keys):
        sample_size = random.randint(1, min(3, len(possible_states))) if possible_states else 0
        result[f"k{i}"] = random.sample(possible_states, sample_size) if sample_size > 0 else []

    return result


test_routes = [
    "/all",
    "A --> B",
    "/all =={f;h;t}==) B; C",
    "/not(E, F);  =={/not(B)}==) /not(E,R)",
    "E =={ f }==)",
    "A, C, D --[ f, g, h ]--> B, K, F",
    "A, C, D --[ ]--> /not(F, A)",
    " X =={ f; h }==) Y ; Z ",
    "F",
    " f =={ mu; theta }==) B",
    " ==) F ",
    " --[ t]--> A ",
    "x--[]-->",
    # "--[ E ]-->"
]

receiver_index: dict[str, Receiver] = {}
receiver_parsed_routes: dict[str, ParsedRoute] = {}

for route in test_routes:
    if route in receiver_index:
        raise Exception(f"Route '{route}' already exists. Overwriting.")
    parsed_route = flow.parse_route(route)
    route_str = str(parsed_route)
    receiver_index.setdefault(
        route_str, 
        Receiver(
            fn=generate_receiver_fn(route_str, Trigger),
            priority=random_priority()
        ))
    receiver_parsed_routes.setdefault(route_str, flow.parse_route(route))

print("\n\nreceiver_index")
pprint.pprint(receiver_index)
print("\n\nreceiver_parsed_routes")
pprint.pprint(receiver_parsed_routes)

raw_states = upload_states(flow, test_routes) if random.random() < 0.9 else None
print("\n\nraw_states")
pprint.pprint(raw_states)

tape = StateTape(raw_states)
activation_index: dict[tuple[int, ...], list[TapeActivation]] = tape.collect_activations(receiver_index=receiver_index, parsed_routes=receiver_parsed_routes)
batches: dict[tuple[int, ...], list[Callable[[Any],Coroutine[Any,Any,Any]]]] = {priority: [activation.fn for activation in activations] for priority, activations in activation_index.items()}

print("\n\nactivation_index")
pprint.pprint(activation_index)
print("\n\nbatches")
pprint.pprint(batches)

async def _safe_call(fn, payload):
    try:
        return await fn(payload)
    except BlockingIOError:
        print("Receiver function raised BlockingIOError; skipping.")
        return None
    except Exception as e:
        print(f"Receiver function {fn.__name__} raised an unexpected error: {e}")
        raise


print("\n\nRun batches → events")
payload = {"content": "hello"}

async def receiver_test():
    event_buffer: dict[tuple[int, ...], list[tuple[Optional[str], ParsedRoute, Optional[Event]]]]  = defaultdict(list)

    for priority, batch_fns in sorted(batches.items(), key=lambda kv: kv[0]):
        label = "default priority" if priority == () else f"priority {priority}"
        print(f"\n\n\033[95m\033[1mRunning batch at {label}, {len(batch_fns)} receivers\033[0m")

        tasks = [_safe_call(fn, payload) for fn in batch_fns]
        events: list[Optional[Event]] = await asyncio.gather(*tasks)
        print("→", events)

        activations = activation_index[priority]

        local_tape = tape.refresh()
        to_extend: dict[Optional[str], list[Node]] = defaultdict(list)
        for act, event in zip(activations, events):
            to_extend[act.key].extend(act.route.activated_nodes(event))
        to_extend = dict(to_extend)
        print(local_tape.states)
        print(to_extend)
        local_tape.extend(to_extend)
        print(local_tape.states)
        

        buffer_entries = [(act.key, act.route, event) for act, event in zip(activations, events)]
        event_buffer[priority].extend(buffer_entries)

        print(f"\n\033[95mdownload_states{priority}:\033[0m")
        pprint.pprint(local_tape.revert())

    return event_buffer

event_buffer = asyncio.run(receiver_test())
print(f"\n\033[93mevent_buffer:\033[0m")
pprint.pprint(event_buffer)

event_bridge = Queue()
for priority, event_list in sorted(event_buffer.items(), key=lambda kv: kv[0]):
    for event_data in event_list:
        event_bridge.put_nowait((priority,) + event_data)

# ============================================ #

def random_action_attr_subset():
    if random.random() < 0.25: 
        return None
    if random.random() < 0.25: 
        return set()
    return set(random.sample(ACTION_ATTRIBUTES, random.randint(1, len(ACTION_ATTRIBUTES))))

def random_trigger_subset(trigger_class):
    if random.random() < 0.25:
        return None
    if random.random() < 0.25: 
        return set()
    all_triggers = [getattr(trigger_class, signal_name) for signal_name in TRIGGER_NAMES]
    triggers = set(random.sample(all_triggers, random.randint(1, 3)))
    return triggers

def generate_sender_fn(arrow, actions, triggers):
    async def fn():
        t0 = time.time()
        await asyncio.sleep(random.randint(1, 4))
        print("[Sender]", f"\033[96m{arrow}\033[0m", "...",  str(time.time()-t0)[:4])
        print("\t", actions)
        print("\t", triggers)
        return "Sender"
    return fn

sender_index: dict[str, list[Sender]] = {}
sender_parsed_routes: dict[str, ParsedRoute] = {}

for route in test_routes:
    parsed_route = flow.parse_route(route)
    route_str = str(parsed_route)

    triggers=random_trigger_subset(Trigger)
    if triggers is not None and (
        not isinstance(triggers, set) or
        not all(isinstance(sig, Signal) for sig in triggers)
    ):
        raise TypeError(f"Argument `on_triggers` must be a set of `Signal` instances. Provided: {triggers!r}")
    
    actions=random_action_attr_subset()
    if actions is not None and (
        not isinstance(actions, set) or
        not all(isinstance(act, type) and issubclass(act, Event) and act in {Action.MOVE, Action.STAY, Action.TEST} for act in actions)
    ):
        raise TypeError(f"Argument `on_actions` must be a set of Action event classes: {{Action.MOVE, Action.STAY, Action.TEST}}. Provided: {actions!r}")

    sender = Sender(
            fn=generate_sender_fn(route_str, actions, triggers),
            multi=False,
            actions=actions,
            triggers=triggers
            )
    sender_index.setdefault(route_str, [])
    sender_index[route_str].append(sender)
    
    actions_exist = isinstance(actions, set) and bool(actions)
    triggers_exist = isinstance(triggers, set) and bool(triggers)
    if route not in sender_parsed_routes and actions_exist or triggers_exist:
        sender_parsed_routes.setdefault(route_str, flow.parse_route(route))

print("\n\nsender_index")
pprint.pprint(sender_index)
print("\n\nsender_parsed_routes")
pprint.pprint(sender_parsed_routes)

pending = []
try:
    while True:
        pending.append(event_bridge.get_nowait())
except Empty:
    pass

senders: list[tuple[str, Sender]] = []
for route, routed_senders in sender_index.items():
    for sender in routed_senders:
        
        if sender.actions is None and sender.triggers is None:
            senders.append((route, sender))
        
        elif (sender.actions and isinstance(sender.actions, set)) or (sender.triggers and isinstance(sender.triggers, set)):
            
            sender_parsed_route = sender_parsed_routes.get(route)
            if sender_parsed_route is None:
                continue
            
            for (priority, key, parsed_route, event) in pending:
                if sender_parsed_route == parsed_route and sender.responds_to(event):
                    senders.append((route, sender))

# async def _send_worker(send_queue: asyncio.Queue[Optional[Sender]]):
#     while True:
#         sender = await send_queue.get()
#         if sender is None:
#             send_queue.task_done()
#             break
#         try:
#             result = await sender.fn()
#             print(f"\t[Result] -> {result}")
#         finally:
#             send_queue.task_done()

# --- Worker (mirrors real one) ---
async def _send_worker(send_queue: asyncio.Queue[Optional[tuple[str, Sender]]]):
    consecutive_errors = 0
    while True:
        item = await send_queue.get()
        if item is None:
            send_queue.task_done()
            break

        route, sender = item
        try:
            # simulate a crash on a particular sender if needed
            result = await sender.fn()
            print(f"\t[{route=}] {sender.fn.__name__} -> {result}")
            consecutive_errors = 0

        except Exception as e:
            consecutive_errors += 1
            print(f"\t[{route}] {sender.fn.__name__} crashed ({consecutive_errors} in a row): {e}")
            if consecutive_errors >= 3:
                print("Too many errors, aborting worker.")
                break

        finally:
            send_queue.task_done()


async def _start_send_workers(num_workers, send_queue):
    for _ in range(num_workers):
        asyncio.create_task(_send_worker(send_queue))


# --- start workers and test runner ---
async def sender_test():
    send_queue: asyncio.Queue[Optional[Tuple[str,Sender]]] = asyncio.Queue(maxsize=50)
    num_workers = 50

    await _start_send_workers(num_workers, send_queue)

    # enqueue all (route, sender)
    for route, sender in senders:
        await send_queue.put((route, sender))

    # wait for all jobs to finish
    await send_queue.join()

    # send sentinels so workers can exit
    for _ in range(num_workers):
        await send_queue.put(None)

# run the simulation
asyncio.run(sender_test())
