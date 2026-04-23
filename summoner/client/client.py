import os
import sys
import json
import copy
from typing import (
    Optional, 
    Callable, 
    Union, 
    Awaitable, 
    Any, 
    Type,
    )
import asyncio
import signal
import inspect
from collections import defaultdict, deque
from dataclasses import dataclass, field
import platform

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.utils import (
    load_config,
    is_jsonable,
    extract_annotation_identifiers,
    resolve_import_statement,
    get_callable_source,
    rebuild_expression_for,
    )
from summoner.logger import (
    get_logger, 
    configure_logger, 
    Logger,
    )
from summoner.protocol.triggers import (
    Signal, 
    Event, 
    Action
    )
from summoner.protocol.process import (
    StateTape, 
    ParsedRoute, 
    Node, 
    Sender, 
    Receiver, 
    Direction,
    ClientIntent,
    )
from summoner.protocol.flow import Flow
from summoner.protocol.validation import (
    hook_priority_order, 
    _check_param_and_return,
)
from summoner.protocol.payload import (
    wrap_with_types, 
    recover_with_types,
    RelayedMessage
)

from summoner._version import __version__ as core_version

class ServerDisconnected(Exception):
    """Raised when the server closes the connection."""
    pass


# ==== CLIENT-INTERNAL SENDER RUNTIME TYPES ====
#
# These types stay in `client.py` on purpose: they model the local scheduling
# and worker runtime of one Summoner client instance, not protocol-level send
# semantics shared across modules.

@dataclass
class SendInvocation:
    """Internal queue item for one sender invocation."""
    route: str
    sender: Sender
    data: Any = None
    done: Optional[asyncio.Future] = None


@dataclass
class TimedSenderRuntime:
    """Internal per-sender scheduler state for timed senders."""
    armed: bool = False
    running: bool = False
    pending_payloads: deque[Any] = field(default_factory=deque)
    next_run_at: Optional[float] = None
    in_flight_done: Optional[asyncio.Future] = None
    run_while_task: Optional[asyncio.Task] = None

class SummonerClient:

    DEFAULT_MAX_BYTES_PER_LINE = 64 * 1024      # 64 KiB
    DEFAULT_READ_TIMEOUT_SECONDS = None         # Wait for messages to arrive
    DEFAULT_CONCURRENCY_LIMIT = 50

    DEFAULT_RETRY_DELAY = 3
    DEFAULT_PRIMARY_RETRY_LIMIT = 3
    DEFAULT_FAILOVER_RETRY_LIMIT = 2

    DEFAULT_EVENT_BRIDGE_SIZE = 1000
    DEFAULT_MAX_CONSECUTIVE_ERRORS = 3          # Failed attempts to send before disconnecting

    core_version = core_version

    def __init__(self, name: Optional[str] = None):
        
        # Give the client a name
        self.name = name if isinstance(name, str) else "<client:no-name>"
        
        # Create a bare logger with no handlers yet
        self.logger: Logger = get_logger(self.name)

        # Create a new event loop
        self.loop = asyncio.new_event_loop()

        # Set the current event loop for this thread
        asyncio.set_event_loop(self.loop)

        # Protect access to active tasks
        self.active_tasks: set[asyncio.Task] = set()
        self.tasks_lock = asyncio.Lock()

        # Protect route registration and lookup for receivers and senders
        self.receiver_index: dict[str, Receiver] = {}
        self.sender_index: dict[str, list[Sender]] = {} # Do not use defaultdict(list) because we rely on .get().
        self.routes_lock = asyncio.Lock()

        # Store routing information that can change at runtime
        self.host: Optional[str] = None
        self.port: Optional[int] = None
        self._travel = False # Flag the intent to travel.
        self._quit = False # Flag the intent to shut down the client.
        self.connection_lock = asyncio.Lock()

        # Track decorator registration tasks
        self._registration_tasks: list[asyncio.Task] = []

        # Cache parsed routes
        self.receiver_parsed_routes: dict[str, ParsedRoute] = {}
        self.sender_parsed_routes: dict[str, ParsedRoute] = {}
        self.snapshot_capture_sender_index: dict[str, list[Sender]] = {}
        self.snapshot_capture_sender_parsed_routes: dict[str, ParsedRoute] = {}

        # Store the client's flow
        self._flow = Flow()

        # Store callbacks that read and write active states
        self._upload_states: Optional[Callable[[Any], Awaitable]] = None
        self._download_states: Optional[Callable[[Any], Awaitable]] = None

        self.event_bridge_maxsize = None
        self.max_concurrent_workers = None # Limit sender concurrency. Uses the configured default when None.
        self.send_queue_maxsize = None
        self.max_bytes_per_line = None
        self.read_timeout_seconds = None # Wait indefinitely when None.
        self.retry_delay_seconds = None
        self.batch_drain = None

        # Pass events from receivers to senders
        self.event_bridge: Optional[asyncio.Queue[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]]] = None

        # Sender-side orchestration runtime. These structures belong to the
        # client implementation rather than the protocol layer because they
        # track local batching, timed admission, and worker handoff state.
        self.send_queue: Optional[asyncio.Queue] = None
        self.timed_event_buffer: deque[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]] = deque()
        self.timed_sender_state: dict[str, TimedSenderRuntime] = {}
        self.timed_sender_lock = asyncio.Lock()
        self.timed_sender_wakeup = asyncio.Event()
        self._next_sender_registration_id = 0
        self.send_workers_started = False  # To avoid double-starting workers
        self.worker_tasks: list[asyncio.Task] = []
        self.writer_lock = asyncio.Lock()

        # Store validation hooks to be used before sending and after receiving
        self.sending_hooks: dict[tuple[int,...], Callable[[Union[str, dict]], Union[str, dict]]] = {}
        self.receiving_hooks: dict[tuple[int,...], Callable[[Union[str, dict]], Union[str, dict]]] = {}
        self.hooks_lock = asyncio.Lock()

        # Store DNA entries for cloning and merging.
        # Each list records decorated handlers of one kind.
        self._dna_receivers: list[dict] = []
        self._dna_senders:   list[dict] = []
        self._dna_hooks:     list[dict] = []

        self._dna_upload_states: Optional[dict] = None
        self._dna_download_states: Optional[dict] = None

    # ==== CLIENT SETUP ====

    def _apply_config(self, config: dict[str,Union[str,dict[str,Union[str,dict]]]]):

        self.host                   = config.get("host") # default is None
        self.port                   = config.get("port") # default is None

        logger_cfg                  = config.get("logger", {})
        configure_logger(self.logger, logger_cfg)

        hp_config                   = config.get("hyper_parameters", {})

        reconn_cfg                  = hp_config.get("reconnection", {})
        self.retry_delay_seconds    = reconn_cfg.get("retry_delay_seconds", self.DEFAULT_RETRY_DELAY)
        self.primary_retry_limit    = reconn_cfg.get("primary_retry_limit",    self.DEFAULT_PRIMARY_RETRY_LIMIT)
        self.default_host           = reconn_cfg.get("default_host",           self.host)
        self.default_port           = reconn_cfg.get("default_port",           self.port)
        self.default_retry_limit    = reconn_cfg.get("default_retry_limit",    self.DEFAULT_FAILOVER_RETRY_LIMIT)

        receiver_cfg                = hp_config.get("receiver", {})
        self.max_bytes_per_line     = receiver_cfg.get("max_bytes_per_line", self.DEFAULT_MAX_BYTES_PER_LINE)
        self.read_timeout_seconds   = receiver_cfg.get("read_timeout_seconds", self.DEFAULT_READ_TIMEOUT_SECONDS)
        
        sender_cfg                  = hp_config.get("sender", {})
        self.max_concurrent_workers = sender_cfg.get("concurrency_limit", self.DEFAULT_CONCURRENCY_LIMIT)
        self.batch_drain            = bool(sender_cfg.get("batch_drain", True))
        self.send_queue_maxsize     = sender_cfg.get("queue_maxsize", self.max_concurrent_workers)
        self.event_bridge_maxsize   = sender_cfg.get("event_bridge_maxsize", self.DEFAULT_EVENT_BRIDGE_SIZE)
        self.max_consecutive_worker_errors = sender_cfg.get("max_worker_errors", self.DEFAULT_MAX_CONSECUTIVE_ERRORS)
        
        if (not isinstance(self.max_consecutive_worker_errors, int) or self.max_consecutive_worker_errors < 1):
            raise ValueError("sender.max_worker_errors must be an integer ≥ 1")

        if not isinstance(self.max_concurrent_workers, int) or self.max_concurrent_workers <= 0:
            raise ValueError("sender.concurrency_limit must be an integer ≥ 1")
        
        if not isinstance(self.send_queue_maxsize, int) or self.send_queue_maxsize <= 0:
            raise ValueError("sender.queue_maxsize must be an integer ≥ 1")
        
        if self.send_queue_maxsize < self.max_concurrent_workers:
            self.logger.warning(f"queue_maxsize < concurrency_limit; back-pressure will throttle producers at {self.send_queue_maxsize}")

    def initialize(self):
        self._flow.compile_arrow_patterns()

    def flow(self) -> Flow:
        return self._flow
    
    # ==== CLIENT CONTROL ====

    async def travel_to(self, host, port):
        async with self.connection_lock:
            self.host = host
            self.port = port
            self._travel = True

    async def quit(self):
        async with self.connection_lock:
            self._quit = True
    
    async def _reset_client_intent(self):
        """Clear any pending quit or travel so we start fresh next time."""
        async with self.connection_lock:
            self._quit = False
            self._travel = False

    # ==== STATE HOOKS ====

    def upload_states(self):
        """
        Decorator to supply a function that returns the current state snapshot.
        Must be used before client.run().
        """
        def decorator(fn: Callable[[], Awaitable]):
            
            # ----[ Safety Checks ]----
            
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@upload_states handler '{fn.__name__}' must be async")
            
            _check_param_and_return(
                fn,
                decorator_name="@upload_states",
                allow_param=(type(None), str, dict, Any),   # the payload
                allow_return=(type(None), str, Any, Node, list, dict,
                                list[str],  dict[str, str],  dict[str, list[str]],
                                list[Node], dict[str, Node], dict[str, list[Node]],
                                dict[str, Union[str, list[str]]],
                                dict[str, Union[Node, list[Node]]],
                                dict[str, Union[str, list[str], Node, list[Node]]],
                                ), # the payload-dependent tape
                logger=self.logger,
            )
            
            # if self.loop.is_running():
            #     raise RuntimeError("@upload_states() must be registered before client.run()")
            
            if self._upload_states is not None:
                self.logger.warning("@upload_states handler overwritten")

            # ----[ DNA capture ]----
            self._dna_upload_states = {
                "fn": fn,
                "source": inspect.getsource(fn),
            }

            self._upload_states = fn

            return fn

        return decorator

    def download_states(self):
        """
        Decorator to supply a function that receives a StateTape.
        Must be used before client.run().
        """
        def decorator(fn: Callable[[Any], Awaitable]):
            
            # ----[ Safety Checks ]----

            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@download_states handler '{fn.__name__}' must be async")
            
            _check_param_and_return(
                fn,
                decorator_name="@download_states",
                allow_param=(type(None), Node, Any, list, dict, 
                                list[Node], 
                                dict[str, Node], 
                                dict[str, list[Node]], 
                                dict[str, Union[Node, list[Node]]], 
                                dict[Optional[str], Node], 
                                dict[Optional[str], list[Node]],
                                dict[Optional[str], Union[Node, list[Node]]],
                                ),
                allow_return=(type(None), Any),
                logger=self.logger,
            )

            # if self.loop.is_running():
            #     raise RuntimeError("@download_states() must be registered before client.run()")
            
            if self._download_states is not None:
                self.logger.warning("@download_states handler overwritten")

            # ----[ DNA capture ]----
            self._dna_download_states = {
                "fn": fn,
                "source": inspect.getsource(fn),
            }

            self._download_states = fn

            return fn

        return decorator
    

    # ==== REGISTRATION HELPER ====

    def _schedule_registration(self, register_coro: Awaitable):
        """
        Schedule `register_coro` onto self.loop.
        If the loop isn't running yet, create the task immediately.
        If it is, use call_soon_threadsafe to be thread-safe.
        """
        if self.loop.is_running():
            def _cb():
                task = self.loop.create_task(register_coro)
                self._registration_tasks.append(task)
            self.loop.call_soon_threadsafe(_cb)
        else:
            task = self.loop.create_task(register_coro)
            self._registration_tasks.append(task)

    # ==== HOOK REGISTRATION ====

    def hook(
            self, 
            direction: Direction, 
            priority: Union[int, tuple[int, ...]] = ()
        ):
        def decorator(fn: Callable[[Optional[Union[str, dict]]], Optional[Union[str, dict]]]): 
            
            # ----[ Safety Checks ]---- 
            
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@hook handler '{fn.__name__}' must be async")
            
            _check_param_and_return(
                fn,
                decorator_name="@hook",
                allow_param=(Any, str, dict),
                allow_return=(type(None), str, dict, Any),
                logger=self.logger,
            )
            
            if not isinstance(direction, Direction):
                raise TypeError(f"Direction for hook must be either Direction.SEND or Direction.RECEIVE")
            
            if isinstance(priority, int):
                tuple_priority = (priority,)
            elif isinstance(priority, tuple) and all(isinstance(p, int) for p in priority):
                tuple_priority = priority
            else:
                raise ValueError(f"Priority must be an integer or a tuple of integers (got type {type(priority).__name__}: {priority!r})")

            # ----[ DNA capture ]----
            self._dna_hooks.append({
                "fn": fn,
                "direction": direction,
                "priority": tuple_priority,
                "source": inspect.getsource(fn),
            })

            # ----[ Registration Code ]----
            async def register():
                async with self.hooks_lock:
                    if direction == Direction.RECEIVE:
                        self.receiving_hooks[tuple_priority] = fn
                    elif direction == Direction.SEND:
                        self.sending_hooks[tuple_priority] = fn

            # ----[ Safe Registration ]----
            # NOTE: register() is run ASAP and _registration_tasks is used to wait all registrations before run_client()
            self._schedule_registration(register())

            return fn

        return decorator

    # ==== RECEIVER REGISTRATION ====

    def receive(
            self, 
            route: str, 
            priority: Union[int, tuple[int, ...]] = ()
        ):
        if not isinstance(route, str):
            raise TypeError(f"Argument `route` must be string. Provided: {route}")
        route = route.strip()
        def decorator(fn: Callable[[Union[str, dict]], Awaitable[Optional[Event]]]):
            
            # ----[ Safety Checks ]----
            
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@receive handler '{fn.__name__}' must be async")
            
            sig = inspect.signature(fn)
            if len(sig.parameters) != 1:
                raise TypeError(f"@receive '{fn.__name__}' must accept exactly one argument (payload)")
            
            _check_param_and_return(
                fn,
                decorator_name="@receive",
                allow_param=(Any, str, dict),
                allow_return=(type(None), Event, Any),
                logger=self.logger,
            )

            if isinstance(priority, int):
                tuple_priority = (priority,)
            elif isinstance(priority, tuple) and all(isinstance(p, int) for p in priority):
                tuple_priority = priority
            else:
                raise ValueError(f"Priority must be an integer or a tuple of integers (got type {type(priority).__name__}: {priority!r})")

            # ----[ DNA capture ]----
            self._dna_receivers.append({
                "fn": fn,
                "route": route,
                "priority": tuple_priority,
                "source": inspect.getsource(fn),  # for text serialization
            })

            # ----[ Registration Code ]----
            async def register():
                receiver = Receiver(fn=fn, priority=tuple_priority)
                
                parsed_route = None
                normalized_route = route

                if self._flow.in_use:
                    try:
                        parsed_route = self._flow.parse_route(route)
                        normalized_route = str(parsed_route)
                    except Exception as e:
                        self.logger.warning(
                            f"@receive: could not parse route {route!r} while flow is enabled; "
                            f"registering raw route. Error: {type(e).__name__}: {e}"
                        )
                        parsed_route = None
                        normalized_route = route

                async with self.routes_lock:
                    if route in self.receiver_index:
                        self.logger.warning(f"Route '{route}' already exists. Overwriting.")
                    
                    if self._flow.in_use and parsed_route is not None:
                        self.receiver_parsed_routes[normalized_route] = parsed_route
                    self.receiver_index[normalized_route] = receiver

            # ----[ Safe Registration ]----
            self._schedule_registration(register())

            return fn

        return decorator
    
    # ==== SENDER REGISTRATION ====

    def _allocate_sender_registration_id(self) -> str:
        registration_id = f"sender:{self._next_sender_registration_id}"
        self._next_sender_registration_id += 1
        return registration_id

    def _normalize_data_mode(self, use_data: bool, data_mode: Optional[str]) -> Optional[str]:
        if not use_data:
            return None
        if data_mode is None:
            return "live"
        if data_mode not in {"live", "snapshot"}:
            raise ValueError(
                "Argument `data_mode` must be `None`, 'live', or 'snapshot'. "
                f"Provided: {data_mode!r}"
        )
        return data_mode

    def _serialize_callable_spec(
            self,
            spec_name: str,
            spec: Any,
            *,
            allow_bool: bool = False,
        ) -> tuple[str, Optional[bool], Optional[str], Optional[str]]:
        if spec is None:
            return ("none", None, None, None)
        if allow_bool and isinstance(spec, bool):
            return ("bool", spec, None, None)
        if callable(spec):
            module_name = getattr(spec, "__module__", None)
            qualname = getattr(spec, "__qualname__", None)
            serialized_name = None
            source = None
            if isinstance(module_name, str) and module_name and isinstance(qualname, str) and qualname:
                serialized_name = f"{module_name}:{qualname}"
            else:
                fallback_name = getattr(spec, "__name__", None)
                if isinstance(fallback_name, str) and fallback_name:
                    serialized_name = fallback_name
            try:
                source = inspect.getsource(spec)
            except Exception:
                source = getattr(spec, "__dna_source__", None)
                if not (isinstance(source, str) and source.strip()):
                    source = None
            return ("callable", None, serialized_name, source)
        if allow_bool:
            allowed = "`None`, a bool, or a callable"
        else:
            allowed = "`None` or a callable"
        raise TypeError(
            f"Argument `{spec_name}` must be {allowed}. Provided: {spec!r}"
        )

    def _is_async_callable(self, fn: Any) -> bool:
        if inspect.iscoroutinefunction(fn):
            return True
        call = getattr(fn, "__call__", None)
        return inspect.iscoroutinefunction(call)

    def _validate_callable_accepts_positional_args(
            self,
            spec_name: str,
            fn: Callable[..., Any],
            arg_count: int,
        ) -> None:
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            return

        probe_args = [object()] * arg_count
        try:
            signature.bind(*probe_args)
        except TypeError as e:
            raise TypeError(
                f"Argument `{spec_name}` callable must accept {arg_count} positional "
                f"argument(s). Provided: {fn!r}"
            ) from e

    def _serialize_run_while_spec(
            self,
            run_while: Any,
        ) -> tuple[str, Optional[bool], Optional[str], Optional[str]]:
        return self._serialize_callable_spec(
            "run_while",
            run_while,
            allow_bool=True,
        )

    def _serialize_when_data_spec(
            self,
            when_data: Any,
        ) -> tuple[str, Optional[bool], Optional[str], Optional[str]]:
        return self._serialize_callable_spec("when_data", when_data)

    def _validate_when_data_spec(
            self,
            when_data: Any,
            *,
            use_data: bool,
        ) -> None:
        if when_data is None:
            return
        if not use_data:
            raise ValueError("Argument `when_data` requires `use_data=True`")
        if not callable(when_data):
            raise TypeError(
                "Argument `when_data` must be `None` or a callable receiving the "
                f"sender payload. Provided: {when_data!r}"
            )
        if self._is_async_callable(when_data):
            raise TypeError(
                "Argument `when_data` must be a synchronous callable returning "
                f"bool. Provided: {when_data!r}"
            )
        self._validate_callable_accepts_positional_args("when_data", when_data, 1)

    def send(
            self, 
            route: str, 
            multi: bool = False, 
            on_triggers: Optional[set[Signal]] = None,
            on_actions: Optional[set[Type]] = None,
            use_data: bool = False,
            data_mode: Optional[str] = None,
            every: Optional[float] = None,
            run_while: Any = None,
            when_data: Any = None,
        ):
        if not isinstance(route, str):
            raise TypeError(f"Argument `route` must be string. Provided: {route}")
        route = route.strip()
        def decorator(fn: Callable[..., Awaitable]):
            
            # ----[ Safety Checks ]----
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@send sender '{fn.__name__}' must be async")
            
            expected_params = 1 if use_data else 0
            decorator_name = "@send"
            if multi and use_data:
                decorator_name = "@send[multi=True,use_data=True]"
            elif multi:
                decorator_name = "@send[multi=True]"
            elif use_data:
                decorator_name = "@send[use_data=True]"

            if not multi:
                _check_param_and_return(
                    fn,
                    decorator_name=decorator_name,
                    allow_param=(Any,) if use_data else (),   # one data arg when enabled
                    allow_return=(type(None), Any, str, dict),
                    logger=self.logger,
                    expected_params=expected_params,
                    skip_param_type_check=use_data,
                )
            else:
                _check_param_and_return(
                    fn,
                    decorator_name=decorator_name,
                    allow_param=(Any,) if use_data else (),   # one data arg when enabled
                    allow_return=(Any, list, list[str], list[dict], list[Union[str, dict]]),
                    logger=self.logger,
                    expected_params=expected_params,
                    skip_param_type_check=use_data,
                )
        
            if not isinstance(multi, bool):
                raise TypeError(f"Argument `multi` must be Boolean. Provided: {multi}")

            if not isinstance(use_data, bool):
                raise TypeError(f"Argument `use_data` must be Boolean. Provided: {use_data}")

            if every is not None:
                if isinstance(every, bool) or not isinstance(every, (int, float)):
                    raise TypeError(f"Argument `every` must be `None` or a positive number. Provided: {every!r}")
                if every <= 0:
                    raise ValueError(f"Argument `every` must be positive. Provided: {every!r}")

            if on_triggers is not None and (
                not isinstance(on_triggers, set) or
                not all(isinstance(sig, Signal) for sig in on_triggers)
            ):
                raise TypeError(f"Argument `on_triggers` must be `None` or a set of `Signal` instances. Provided: {on_triggers!r}")

            if on_actions is not None and (
                not isinstance(on_actions, set) or
                not all(isinstance(act, type) and issubclass(act, Event) and act in {Action.MOVE, Action.STAY, Action.TEST} for act in on_actions)
            ):
                raise TypeError(f"Argument `on_actions` must be `None` or a set of Action event classes: {{Action.MOVE, Action.STAY, Action.TEST}}. Provided: {on_actions!r}")

            reactive_filters = (
                (isinstance(on_triggers, set) and bool(on_triggers)) or
                (isinstance(on_actions, set) and bool(on_actions))
            )

            if use_data and not reactive_filters:
                raise ValueError("Argument `use_data=True` requires a non-empty `on_triggers` or `on_actions` set so the sender has queued event data to receive")

            if use_data and not self._flow.in_use:
                raise RuntimeError("Argument `use_data=True` requires `client.flow().activate()` before sender registration")

            if every is not None and reactive_filters and not self._flow.in_use:
                raise RuntimeError(
                    "Timed reactive senders require `client.flow().activate()` before sender registration"
                )

            if run_while is not None and every is None:
                raise ValueError("Argument `run_while` requires `every`")

            if data_mode is not None and not use_data:
                raise ValueError("Argument `data_mode` requires `use_data=True`")

            self._validate_when_data_spec(when_data, use_data=use_data)
            normalized_data_mode = self._normalize_data_mode(use_data, data_mode)
            run_while_kind, run_while_value, run_while_name, run_while_source = self._serialize_run_while_spec(run_while)
            when_data_kind, when_data_value, when_data_name, when_data_source = self._serialize_when_data_spec(when_data)
            registration_id = self._allocate_sender_registration_id()
            
            # ----[ DNA capture ]----
            self._dna_senders.append({
                "fn": fn,
                "route": route,
                "multi": multi,
                "on_triggers": on_triggers,
                "on_actions": on_actions,
                "use_data": use_data,
                "data_mode": normalized_data_mode,
                "every": every,
                "run_while": run_while,
                "run_while_kind": run_while_kind,
                "run_while_value": run_while_value,
                "run_while_name": run_while_name,
                "run_while_source": run_while_source,
                "when_data": when_data,
                "when_data_kind": when_data_kind,
                "when_data_value": when_data_value,
                "when_data_name": when_data_name,
                "when_data_source": when_data_source,
                "source": inspect.getsource(fn),
            })

            # ----[ Registration Code ]----
            async def register():
                
                sender = Sender(
                    fn=fn,
                    multi=multi,
                    actions=on_actions,
                    triggers=on_triggers,
                    use_data=use_data,
                    data_mode=normalized_data_mode,
                    when_data=when_data,
                    every=every,
                    run_while=run_while,
                    registration_id=registration_id,
                )
                actions_exist = isinstance(on_actions, set) and bool(on_actions)
                triggers_exist = isinstance(on_triggers, set) and bool(on_triggers)
                
                parsed_route = None
                normalized_route = route

                if self._flow.in_use:
                    try:
                        parsed_route = self._flow.parse_route(route)
                        normalized_route = str(parsed_route)
                    except Exception as e:
                        self.logger.warning(
                            f"@send: could not parse route {route!r} while flow is enabled; "
                            f"registering raw route. Error: {type(e).__name__}: {e}"
                        )
                        parsed_route = None
                        normalized_route = route

                async with self.routes_lock:
                    if self._flow.in_use:
                        self.sender_index.setdefault(normalized_route, [])
                        self.sender_index[normalized_route].append(sender)

                        if parsed_route is not None and (actions_exist or triggers_exist):
                            self.sender_parsed_routes.setdefault(normalized_route, parsed_route)
                            if use_data and normalized_data_mode == "snapshot":
                                self.snapshot_capture_sender_index.setdefault(normalized_route, [])
                                self.snapshot_capture_sender_index[normalized_route].append(sender)
                                self.snapshot_capture_sender_parsed_routes.setdefault(
                                    normalized_route,
                                    parsed_route,
                                )
                    else:
                        self.sender_index.setdefault(route, [])
                        self.sender_index[route].append(sender)

            # ----[ Safe Registration ]----
            # NOTE: register() is run ASAP and _registration_tasks is used to wait all registrations before run_client()
            self._schedule_registration(register())

            return fn

        return decorator

    # ==== DNA EXPORT ====

    def _iter_registered_handler_functions(self):
        """
        Iterate over all handler callables that define the client's behavior.

        This is used by dna(include_context=True) to:
          - infer the client binding name (commonly "agent") used in handler source code,
          - scan handler globals for referenced symbols to export as imports/globals/recipes/missing.

        Yields
        ------
        Callable
            Any registered handler function attached to this client. Entries that are
            not present (None) are skipped.
        """
        if self._upload_states is not None:
            yield self._upload_states

        if self._download_states is not None:
            yield self._download_states

        for d in self._dna_receivers:
            fn = d.get("fn")
            if fn is not None:
                yield fn

        for d in self._dna_senders:
            fn = d.get("fn")
            if fn is not None:
                yield fn
            run_while = d.get("run_while")
            if callable(run_while):
                yield run_while
            when_data = d.get("when_data")
            if callable(when_data):
                yield when_data

        for d in self._dna_hooks:
            fn = d.get("fn")
            if fn is not None:
                yield fn

    def _infer_client_binding_name(self) -> str:
        """
        Infer the module-global variable name that refers to this client instance.

        Rationale
        ---------
        User-authored handler sources frequently reference the client via a global
        variable, for example:

            await agent.travel_to(...)
            await agent.quit()

        When cloning or merging from DNA (rehydrating from sources), the evaluation
        sandbox must bind that same name to the reconstructed client instance.
        This method attempts to recover the intended name by scanning the globals
        dict of registered handlers.

        Returns
        -------
        str
            The inferred binding name if found; otherwise "agent".
        """
        for fn in self._iter_registered_handler_functions():
            g = getattr(fn, "__globals__", None)
            if not isinstance(g, dict):
                continue

            # Look for any global variable that points exactly to this client instance.
            for k, v in g.items():
                if v is self and isinstance(k, str):
                    return k

        return "agent"

    def dna(self, include_context: bool = False) -> str:
        """
        Serialize this client's registered behavior into a JSON string ("DNA").

        Purpose
        -------
        DNA is intended to support:
          1) cloning: rehydrate a client from data by re-evaluating handler sources
          2) merging: combine multiple clients into a composite client (ClientMerger)

        Output schema
        -------------
        The returned JSON encodes a list[dict], where each dict is a DNA entry.

        If include_context=False:
          - the list contains only handler entries, ordered as:
              upload_states
              download_states
              receive (one per @receive)
              send    (one per @send)
              hook    (one per @hook)

        If include_context=True:
          - the first entry is a "__context__" header that helps rehydration,
            followed by the same handler entries as above.

        Context header fields
        ---------------------
        - var_name:
            The preferred global name used to reference the client in handler sources.
            Typically "agent".
        - imports:
            Import statements that appear safe and stable to replay in a rehydration
            sandbox.
        - globals:
            JSON-serializable constants referenced by handler sources.
        - recipes:
            Conservative reconstruction expressions for a small set of known patterns
            (for example asyncio.Lock()).
        - missing:
            Symbols referenced by handlers that cannot be imported or serialized.
            Rehydration must provide these explicitly.

        Portability rule
        ----------------
        DNA is meant to be replayable across environments. Unstable runtime bindings
        (for example '__main__' imports or live objects that cannot be rebuilt) should
        end up in "missing", not embedded implicitly.

        Notes
        -----
        - This captures decorated handlers and their source code.
        - Flow construction and trigger enum creation are not captured unless they are
          referenced and executed within decorated handler sources.
        """
        import builtins

        # Collect handler functions once so we can reuse them for context analysis.
        handler_fns = list(self._iter_registered_handler_functions())

        # ----------------------------
        # Handler DNA entries
        # ----------------------------
        entries: list[dict] = []

        # Upload state hook, if present
        if self._dna_upload_states is not None:
            fn = self._dna_upload_states["fn"]
            entries.append({
                "type": "upload_states",
                "source": get_callable_source(fn, self._dna_upload_states.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        # Download state hook, if present
        if self._dna_download_states is not None:
            fn = self._dna_download_states["fn"]
            entries.append({
                "type": "download_states",
                "source": get_callable_source(fn, self._dna_download_states.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        # All receivers
        for dna in self._dna_receivers:
            fn = dna["fn"]
            raw_route = dna["route"]

            try:
                if self._flow.in_use:
                    route_key = str(self._flow.parse_route(raw_route))
                else:
                    route_key = raw_route
            except Exception:
                route_key = raw_route
            route_key = "".join(str(route_key).split())

            entries.append({
                "type": "receive",
                "route": raw_route, # original route string
                "route_key": route_key, # stable route representative
                "priority": dna["priority"],
                "source": get_callable_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        # All senders
        for dna in self._dna_senders:
            fn = dna["fn"]
            raw_route = dna["route"]

            try:
                if self._flow.in_use:
                    route_key = str(self._flow.parse_route(raw_route))
                else:
                    route_key = raw_route
            except Exception:
                route_key = raw_route
            route_key = "".join(str(route_key).split())

            entry = {
                "type": "send",
                "route": raw_route,          # original route string
                "route_key": route_key,     # stable route representative
                "multi": dna["multi"],
                "use_data": dna["use_data"],
                "data_mode": dna["data_mode"],
                "every": dna["every"],
                "run_while_kind": dna["run_while_kind"],
                "run_while_value": dna["run_while_value"],
                "run_while_name": dna["run_while_name"],
                "run_while_source": dna.get("run_while_source", None),
                "when_data_kind": dna.get("when_data_kind", "none"),
                "when_data_value": dna.get("when_data_value", None),
                "when_data_name": dna.get("when_data_name", None),
                "when_data_source": dna.get("when_data_source", None),
                # Serialize triggers/actions by name so they can be re-resolved later.
                "on_triggers": [t.name for t in (dna["on_triggers"] or [])],
                "on_actions": [a.__name__ for a in (dna["on_actions"] or [])],
                "source": get_callable_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            }
            entries.append(entry)

        # All hooks
        for dna in self._dna_hooks:
            fn = dna["fn"]
            entries.append({
                "type": "hook",
                "direction": dna["direction"].name,
                "priority": dna["priority"],
                "source": get_callable_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        # Fast path: return only handler entries.
        if not include_context:
            return json.dumps(entries)

        # ----------------------------
        # Context header (best-effort)
        # ----------------------------

        # Identify the name used to reference this client in handler globals.
        inferred_var_name = self._infer_client_binding_name()

        # Avoid exporting bindings that would conflict with the client variable itself.
        excluded_names = {inferred_var_name, "__builtins__"}

        recipes: dict[str, str] = {}
        globals_out: dict[str, object] = {}
        imports_out: set[str] = set()
        missing: list[str] = []

        # Helps compilation when annotations mention typing constructs.
        imports_out.add("from typing import *")

        path_needed = False

        # Identify the runtime type of asyncio.Lock() so we can detect it.
        try:
            lock_type = type(asyncio.Lock())
        except Exception:
            lock_type = None

        # Used by resolve_import_statement to avoid emitting repeated imports.
        known_modules: set[str] = set()

        for fn in handler_fns:
            g = getattr(fn, "__globals__", None)
            if not isinstance(g, dict):
                continue

            # Names referenced by the function body.
            names_to_scan = set(getattr(fn, "__code__", None).co_names if hasattr(fn, "__code__") else ())

            # Names referenced only via annotations.
            try:
                ann = getattr(fn, "__annotations__", {}) or {}
                for v in ann.values():
                    if isinstance(v, type) or inspect.isfunction(v) or inspect.ismodule(v):
                        nm = getattr(v, "__name__", None)
                        if isinstance(nm, str) and nm:
                            names_to_scan.add(nm)
            except Exception:
                pass

            # Fallback: parse identifiers from annotation syntax in the source.
            try:
                src = get_callable_source(fn)
                names_to_scan |= extract_annotation_identifiers(src)
            except Exception:
                pass

            for name in names_to_scan:
                if name in excluded_names:
                    continue
                if name in builtins.__dict__:
                    continue
                if name not in g:
                    continue

                value = g[name]

                # Skip binding the client itself.
                if value is self:
                    continue

                # Avoid auto-copying other clients. These must be supplied explicitly.
                if isinstance(value, SummonerClient):
                    missing.append(name)
                    continue

                # Recognize asyncio.Lock() instances and rebuild them by recipe.
                if lock_type is not None and isinstance(value, lock_type):
                    imports_out.add("import asyncio")
                    recipes.setdefault(name, "asyncio.Lock()")
                    continue

                # Try to rebuild via a deterministic expression when possible.
                r = rebuild_expression_for(value, node_type=Node)
                if isinstance(r, str):
                    recipes.setdefault(name, r)
                    if "Path(" in r:
                        path_needed = True
                    if "{}(".format(Node.__name__) in r:
                        imports_out.add("from summoner.protocol.process import Node")
                    continue

                # Try to emit a stable import statement for this symbol.
                line = resolve_import_statement(name, value, known_modules)
                if line is not None:
                    imports_out.add(line)
                    continue

                # If the object can be JSON-encoded, inline it in globals.
                if is_jsonable(value):
                    globals_out.setdefault(name, value)
                    continue

                # Otherwise, rehydration must provide this name explicitly.
                missing.append(name)

        if path_needed:
            imports_out.add("from pathlib import Path")

        context_entry = {
            "type": "__context__",
            "var_name": inferred_var_name,
            "imports": sorted(imports_out),
            "globals": globals_out,
            "recipes": recipes,
            "missing": sorted(set(missing)),
        }

        return json.dumps([context_entry] + entries)

    # ==== RECEIVER EXECUTION ====

    async def _read_line_safe(
    self,
    reader: asyncio.StreamReader,
    *,
    limit: int = 64*1024,
    timeout: Optional[float] = None
    ) -> bytes:
        """
        Read one line up to `limit` bytes, optionally with a `timeout`.
        - If timeout is None, blocks indefinitely via reader.readline().
        - If timeout is set, waits up to `timeout` seconds.
        - On lines longer than `limit`: logs, drops them, and retries.
        - On EOF: raises ServerDisconnected.
        """
        while True:
            try:
                coro = reader.readline()
                if timeout is not None:
                    data = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    data = await coro
            except asyncio.TimeoutError:
                # no data yet → back off a bit to avoid busy-spin
                await asyncio.sleep(0.01)
                continue

            if not data:
                # clean EOF
                raise ServerDisconnected("EOF during read")

            if len(data) > limit:
                self.logger.warning(f"Incoming line exceeded {limit} bytes; dropping")
                # skip the rest of this overly long line (up to the next separator)
                # Note: reader.readline() read the entire line already, so nothing left —
                # if you instead used readuntil you'd skip here by:
                # await reader.readuntil(separator)
                continue

            return data
        

    async def message_receiver_loop(
            self,
            reader: asyncio.StreamReader,
            stop_event: asyncio.Event
        ):
        
        # ----[ Wrapper: Interpret Protocol-Only Errors as None ]----
        async def _safe_call(fn: Callable[[Any], Awaitable], payload: Any) -> Any:
            try:
                return await fn(payload)
            except BlockingIOError:
                self.logger.warning("Receiver function raised BlockingIOError; skipping.")
                return None
            except Exception as e:
                self.logger.exception(f"Receiver function {fn.__name__} raised an unexpected error: {e}")
                raise
                        
        try:
            
            # ----[ Constantly Listen ]----
            while not stop_event.is_set():
                
                async with self.connection_lock:
                    if self._quit or self._travel:
                        stop_event.set()
                        break
                
                # ----[ Prepare Receiver Batches ]----
                async with self.routes_lock:
                    receiver_index: dict[str, Receiver] = self.receiver_index.copy()
                
                if self._flow.in_use:
                    async with self.routes_lock:
                        receiver_parsed_routes: dict[str, ParsedRoute] = self.receiver_parsed_routes.copy()


                # ----[ Empty: Skip and Prevent Client Overwhelming ]----
                if not receiver_index:
                    data = await self._read_line_safe(
                        reader, 
                        limit=self.max_bytes_per_line, 
                        timeout=0.1,
                        )
                    # if not data:
                    #     raise ServerDisconnected("EOF while dropping messages")
                    continue
                
                # ----[ Build and Run Receiver Batches ]----
                try:
                    
                    # ----[ Build: Get Messages ]----
                    
                    data = await self._read_line_safe(
                        reader, 
                        limit=self.max_bytes_per_line, 
                        timeout=self.read_timeout_seconds,
                        )
                    # data = await reader.readline()
                    # if not data:
                    #     raise ServerDisconnected("Server closed the connection.")

                    payload: RelayedMessage = recover_with_types(data.decode())

                    # ----[ Build: Validation ]----
                    async with self.hooks_lock:
                        receiving_hooks = self.receiving_hooks.copy()

                    for priority, receiving_hook in sorted(receiving_hooks.items(), key=lambda kv: hook_priority_order(kv[0])):
                        try:
                            new_payload = await receiving_hook(payload)

                            if new_payload is None:
                                payload = None
                                break
                            
                        except Exception as e:
                            self.logger.error(
                                f"Receiving hook {receiving_hook.__name__} (priority={priority}) "
                                f"failed on payload {payload!r}: {e}",
                                exc_info=True
                            )
                            new_payload = payload
                        payload = new_payload
                    
                    # if *any* hook returned None, skip the rest of processing
                    if payload is None:
                        continue
                
                    # ----[ Build: Organize Batches by Priority ]----
                    batches: dict[tuple[int, ...], list[Callable[[Any], Awaitable]]] = {}
                    if self._flow.in_use:
                        raw_states = (await self._upload_states(payload)) if self._upload_states is not None else None
                        tape = StateTape(raw_states)
                        activation_index = tape.collect_activations(receiver_index=receiver_index, parsed_routes=receiver_parsed_routes)
                        batches = {priority: [activation.fn for activation in activations] for priority, activations in activation_index.items()}
                    else:
                        for _, receiver in receiver_index.items():
                            batches.setdefault(receiver.priority, [])
                            batches[receiver.priority].append(receiver.fn)

                    # ----[ Empty: No Matching Receiver For This Payload ]----
                    if not batches:
                        continue
                    
                    # ----[ Exec: Prepare Passage Receiver → Sender ]----
                    if self._flow.in_use:
                        event_buffer: dict[tuple[int, ...], list[tuple[Optional[str], ParsedRoute, Event]]]  = defaultdict(list)

                    # ----[ Exec: Run Batches in Order ]----
                    for priority, batch_fns in sorted(batches.items(), key=lambda kv: kv[0]):
                        
                        # ----[ Before: Run Batch ]----
                        # label = "default priority" if priority == () else f"priority {priority}"
                        # self.logger.info(f"Running batch at {label}, {len(batch_fns)} receivers")

                        tasks = [_safe_call(fn, payload) for fn in batch_fns]
                        events: list[Optional[Event]] = await asyncio.gather(*tasks)

                        # ----[ After: Handle Returns ]----
                        if self._flow.in_use:
                            activations = activation_index[priority]
                            
                            local_tape = tape.refresh()
                            to_extend: dict[str, list[Node]] = defaultdict(list)
                            for act, event in zip(activations, events):
                                to_extend[act.key].extend(act.route.activated_nodes(event))
                            local_tape.extend(to_extend)

                            buffer_entries = [(act.key, act.route, event) for act, event in zip(activations, events)]
                            event_buffer[priority].extend(buffer_entries)
                                                        
                            if self._download_states is not None:
                                await self._download_states(local_tape.revert())

                    # ----[ Final: Pass Data Over To Senders ]----
                    if self._flow.in_use:
                        
                        for priority, event_list in sorted(event_buffer.items(), key=lambda kv: kv[0]):
                            for event_data in event_list:
                                # this will block if the bridge is full, slowing down readers
                                await self._enqueue_sender_event((priority,) + event_data)

                        event_buffer = {}
                    
                except ServerDisconnected as e:
                    # Intentionally propagate this so reconnection logic can trigger
                    self.logger.info(f"Graceful disconnect from server: {e}")
                    raise
                
                except (ConnectionResetError, BrokenPipeError) as e:
                    self.logger.warning(f"Socket-level failure (client likely to blame): {e}")
                    break

        except (ServerDisconnected, asyncio.CancelledError):
            stop_event.set()
            raise

    # ==== SEND DATA HELPERS ====

    def _capture_send_data(self, value: Any, data_mode: Optional[str]) -> Any:
        if data_mode in (None, "live"):
            return value
        if data_mode == "snapshot":
            return copy.deepcopy(value)
        raise TypeError(f"Unknown data_mode: {data_mode!r}")

    def _materialize_send_data(self, value: Any, data_mode: Optional[str]) -> Any:
        if data_mode in (None, "live"):
            return value
        if data_mode == "snapshot":
            return copy.deepcopy(value)
        raise TypeError(f"Unknown data_mode: {data_mode!r}")

    def _passes_when_data(self, sender: Sender, route: str, payload: Any) -> bool:
        spec = sender.when_data
        if spec is None:
            return True
        if not callable(spec):
            self.logger.warning(
                f"Invalid when_data specification for '{sender.fn.__name__}' "
                f"on route '{route}': {spec!r}"
            )
            return False

        try:
            value = spec(payload)
            if inspect.isawaitable(value):
                self.logger.warning(
                    f"when_data predicate for '{sender.fn.__name__}' on route '{route}' "
                    "returned an awaitable; async when_data is not supported"
                )
                return False
            return bool(value)
        except Exception as e:
            self.logger.warning(
                f"when_data predicate failed for '{sender.fn.__name__}' on route '{route}': "
                f"{type(e).__name__}: {e}"
            )
            return False

    # ==== TIMED SENDER GUARD HELPERS ====

    async def _await_run_while_value(self, awaitable: Any) -> bool:
        value = await awaitable
        return bool(value)

    def _wake_timed_scheduler(self, _task: asyncio.Task) -> None:
        self.timed_sender_wakeup.set()

    def _poll_run_while(self, sender: Sender, runtime: TimedSenderRuntime) -> Optional[bool]:
        spec = sender.run_while

        if spec is None:
            return True
        if isinstance(spec, bool):
            return spec
        if not callable(spec):
            raise TypeError(f"Invalid run_while specification: {spec!r}")

        task = runtime.run_while_task
        if task is not None:
            if not task.done():
                return None
            runtime.run_while_task = None
            try:
                return bool(task.result())
            except Exception as e:
                self.logger.warning(f"run_while predicate failed: {type(e).__name__}: {e}")
                return False

        try:
            value = spec()
            if inspect.isawaitable(value):
                # Async guards may legitimately take time to confirm whether the
                # sender may proceed. Keep that wait local to this sender by
                # tracking a dedicated task instead of stalling the whole timed
                # scheduler.
                task = self.loop.create_task(self._await_run_while_value(value))
                task.add_done_callback(self._wake_timed_scheduler)
                runtime.run_while_task = task
                return None
            return bool(value)
        except Exception as e:
            self.logger.warning(f"run_while predicate failed: {type(e).__name__}: {e}")
            return False

    # ==== SEND ROUTING / ORCHESTRATION HELPERS ====

    @staticmethod
    def _route_accepts(sender_pr: ParsedRoute, receiver_pr: ParsedRoute) -> bool:
        source_ok = all(any(n.accepts(m) for m in receiver_pr.source) for n in sender_pr.source)
        label_ok = all(any(n.accepts(m) for m in receiver_pr.label) for n in sender_pr.label)
        target_ok = all(any(n.accepts(m) for m in receiver_pr.target) for n in sender_pr.target)
        return source_ok and label_ok and target_ok

    @staticmethod
    def _has_reactive_filters(sender: Sender) -> bool:
        return bool(
            (sender.actions and isinstance(sender.actions, set)) or
            (sender.triggers and isinstance(sender.triggers, set))
        )

    @staticmethod
    def _event_snapshot_data(event: Event) -> tuple[bool, Any]:
        if getattr(event, "_has_snapshot_data", False):
            return True, getattr(event, "_snapshot_data", None)
        return False, None

    async def _snapshot_sender_registry(self) -> tuple[dict[str, list[Sender]], dict[str, ParsedRoute]]:
        async with self.routes_lock:
            sender_index = {
                route: list(routed_senders)
                for route, routed_senders in self.sender_index.items()
            }
            sender_parsed_routes = self.sender_parsed_routes.copy()
        return sender_index, sender_parsed_routes

    async def _snapshot_capture_registry(self) -> tuple[dict[str, list[Sender]], dict[str, ParsedRoute]]:
        async with self.routes_lock:
            sender_index = {
                route: list(routed_senders)
                for route, routed_senders in self.snapshot_capture_sender_index.items()
            }
            sender_parsed_routes = self.snapshot_capture_sender_parsed_routes.copy()
        return sender_index, sender_parsed_routes

    def _drain_pending_events(self) -> list[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]]:
        pending: list[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]] = []
        if not self._flow.in_use:
            return pending

        try:
            while True:
                pending.append(self.event_bridge.get_nowait())
        except asyncio.QueueEmpty:
            pass

        pending.sort(key=lambda it: hook_priority_order(it[0]))
        return pending

    async def _needs_snapshot_capture_for_event(
            self,
            parsed_route: ParsedRoute,
            event: Event,
        ) -> bool:
        if not self._flow.in_use:
            return False

        sender_index, sender_parsed_routes = await self._snapshot_capture_registry()

        for route, routed_senders in sender_index.items():
            sender_parsed_route = sender_parsed_routes.get(route)
            if sender_parsed_route is None:
                continue
            if not self._route_accepts(sender_parsed_route, parsed_route):
                continue

            for sender in routed_senders:
                if sender.responds_to(event):
                    return True

        return False

    async def _enqueue_sender_event(
            self,
            item: tuple[tuple[int, ...], Optional[str], ParsedRoute, Event],
        ) -> None:
        priority, key, parsed_route, event = item

        if (
            self._flow.in_use
            and isinstance(event, Event)
            and (not getattr(event, "_has_snapshot_data", False))
            and await self._needs_snapshot_capture_for_event(parsed_route, event)
        ):
            try:
                event._snapshot_data = copy.deepcopy(event.data)
                event._has_snapshot_data = True
            except Exception as e:
                self.logger.warning(
                    f"Failed to capture sender snapshot data for event on route "
                    f"'{parsed_route}': {type(e).__name__}: {e}"
                )

        await self.event_bridge.put(item)

        if self._flow.in_use:
            # Timed senders keep their own admission feed so they can be armed
            # promptly without changing the untimed batch-loop contract.
            async with self.timed_sender_lock:
                self.timed_event_buffer.append(item)
            self.timed_sender_wakeup.set()

    def _make_send_invocation(
            self,
            route: str,
            sender: Sender,
            *,
            data: Any = None,
            track_completion: bool = False,
        ) -> SendInvocation:
        done = self.loop.create_future() if track_completion else None
        return SendInvocation(route=route, sender=sender, data=data, done=done)

    async def _wait_for_send_invocations(self, invocations: list[SendInvocation]) -> None:
        tracked = [
            invocation.done
            for invocation in invocations
            if invocation.done is not None
        ]
        if tracked:
            await asyncio.gather(*tracked, return_exceptions=True)

    def _ensure_timed_runtime(self, sender: Sender) -> TimedSenderRuntime:
        registration_id = sender.registration_id or sender.fn.__name__
        runtime = self.timed_sender_state.get(registration_id)
        if runtime is None:
            runtime = TimedSenderRuntime()
            self.timed_sender_state[registration_id] = runtime
        return runtime

    def _arm_timed_senders_from_pending_locked(
            self,
            sender_index: dict[str, list[Sender]],
            sender_parsed_routes: dict[str, ParsedRoute],
            pending: list[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]],
            now: float,
        ) -> None:
        if not pending:
            return

        for route, routed_senders in sender_index.items():
            sender_parsed_route = sender_parsed_routes.get(route)
            if sender_parsed_route is None:
                continue

            for sender in routed_senders:
                if sender.every is None or (not self._has_reactive_filters(sender)):
                    continue

                runtime = self._ensure_timed_runtime(sender)

                for (_priority, _key, parsed_route, event) in pending:
                    if not (
                        self._route_accepts(sender_parsed_route, parsed_route)
                        and sender.responds_to(event)
                    ):
                        continue

                    if not runtime.armed:
                        runtime.armed = True
                        runtime.running = True
                        runtime.next_run_at = now

                    if sender.use_data:
                        try:
                            has_snapshot_data, snapshot_data = self._event_snapshot_data(event)
                            runtime.pending_payloads.append(
                                snapshot_data
                                if sender.data_mode == "snapshot" and has_snapshot_data
                                else self._capture_send_data(event.data, sender.data_mode)
                            )
                        except Exception as e:
                            self.logger.warning(
                                f"Failed to capture timed sender data for '{sender.fn.__name__}' "
                                f"on route '{route}': {type(e).__name__}: {e}"
                            )
                    else:
                        break

    async def _wait_for_timed_wakeup(self, timeout: float) -> None:
        if timeout <= 0:
            return
        try:
            await asyncio.wait_for(self.timed_sender_wakeup.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return
        finally:
            self.timed_sender_wakeup.clear()

    # ==== SENDER EXECUTION ====

    def _start_send_workers(
            self,
            writer: asyncio.StreamWriter, 
            stop_event: asyncio.Event
        ):
        if not self.send_workers_started:
            for _ in range(self.max_concurrent_workers):
                worker_task = self.loop.create_task(self._send_worker(writer, stop_event))
                self.worker_tasks.append(worker_task)
            self.send_workers_started = True

    async def _send_worker(
            self,
            writer: asyncio.StreamWriter, 
            stop_event: asyncio.Event
        ):
        consecutive_errors = 0

        while True:
            
            item = await self.send_queue.get()
            if item is None:
                self.send_queue.task_done()
                break

            invocation_done = None
            if isinstance(item, SendInvocation):
                route = item.route
                sender = item.sender
                sender_data = item.data
                invocation_done = item.done
            else:
                route, sender, sender_data = item

            try:
                if sender.use_data:
                    result = await sender.fn(sender_data)
                else:
                    result = await sender.fn()

                # ----[ Urgent: Handle Aborts ]----
                async with self.connection_lock:
                    if self._quit:
                        if invocation_done is not None and not invocation_done.done():
                            invocation_done.set_result(None)
                        stop_event.set()
                        break

                # reset on any success
                consecutive_errors = 0

                # ----[ Unpack: Handle Multi Sends ]----
                payloads = result if sender.multi else [result]
                for payload in payloads:
                    
                    if payload is None:
                        continue
                    
                    # ----[ Unpack: Validation ]----
                    async with self.hooks_lock:
                        sending_hooks = self.sending_hooks.copy()

                    for priority, sending_hook in sorted(sending_hooks.items(), key=lambda kv: hook_priority_order(kv[0])):
                        try:
                            new_payload = await sending_hook(payload)
                            
                            if new_payload is None:
                                payload = None
                                break
                            
                        except Exception as e:
                            self.logger.error(
                                f"[route={route}] Sending hook {sending_hook.__name__} (priority={priority}) "
                                f"failed on payload {payload!r}: {e}",
                                exc_info=True
                            )
                            new_payload = payload
                        payload = new_payload

                    # if *any* hook returned None, skip the rest of processing
                    if payload is None:
                        continue

                    # NOTE: If "\n" is present, the payload is assumed to be structured
                    # message = (
                    #     json.dumps(payload)
                    #     if (not isinstance(payload, str) or "\n" in payload)
                    #     else payload
                    # )
                    # message = ensure_trailing_newline(message).encode()

                    message = wrap_with_types(payload, version=self.core_version).encode()

                    # ----[ Unpack: Post Messages ]----
                    async with self.writer_lock:
                        writer.write(message)

                # No concurrency on batch_drain (initialized in run())
                if not self.batch_drain:
                    async with self.writer_lock:
                        await writer.drain()

                if invocation_done is not None and not invocation_done.done():
                    invocation_done.set_result(None)

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(
                    f"Worker for {sender.fn.__name__} crashed ({consecutive_errors} in a row): {e}",
                    exc_info=True
                )
                if invocation_done is not None and not invocation_done.done():
                    invocation_done.set_result(e)
                # if 3 workers in a row have crashed, abort the session
                if consecutive_errors >= self.max_consecutive_worker_errors:
                    self.logger.critical(f"{self.max_consecutive_worker_errors} consecutive worker failures; shutting down sender loop")
                    stop_event.set()
                    break

            finally:
                self.send_queue.task_done()

    async def _cleanup_workers(self):
        for w in self.worker_tasks:
            w.cancel()
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()
        self.send_workers_started = False

    async def _message_sender_batch_loop(
            self,
            writer: asyncio.StreamWriter,
            stop_event: asyncio.Event,
        ) -> None:
        """
        Preserve the legacy sender contract for untimed senders.

        Untimed senders remain round-based: each loop pass collects the current
        work, dispatches a batch, and waits for that batch to finish before
        starting the next untimed round. This keeps pre-`every` behavior stable.
        """
        while not stop_event.is_set():
            sender_index, sender_parsed_routes = await self._snapshot_sender_registry()
            pending = self._drain_pending_events()

            invocations: list[SendInvocation] = []
            emitted: set[tuple[str, Optional[str], str]] = set()

            for route, routed_senders in sender_index.items():
                for sender in routed_senders:
                    if sender.every is not None:
                        continue

                    sender_is_reactive = self._has_reactive_filters(sender)

                    if (not self._flow.in_use) or (not sender_is_reactive):
                        if sender.use_data:
                            self.logger.warning(
                                f"Skipping sender '{sender.fn.__name__}' on route '{route}': "
                                "use_data=True requires a reactive sender running with flow enabled"
                            )
                            continue
                        invocations.append(
                            self._make_send_invocation(
                                route,
                                sender,
                                track_completion=True,
                            )
                        )
                        continue

                    sender_parsed_route = sender_parsed_routes.get(route)
                    if sender_parsed_route is None:
                        continue

                    for (_priority, key, parsed_route, event) in pending:
                        if not (
                            self._route_accepts(sender_parsed_route, parsed_route)
                            and sender.responds_to(event)
                        ):
                            continue

                        if sender.use_data:
                            try:
                                has_snapshot_data, snapshot_data = self._event_snapshot_data(event)
                                captured_data = (
                                    snapshot_data
                                    if sender.data_mode == "snapshot" and has_snapshot_data
                                    else self._capture_send_data(event.data, sender.data_mode)
                                )
                                payload = self._materialize_send_data(captured_data, sender.data_mode)
                                if not self._passes_when_data(sender, route, payload):
                                    continue
                                invocations.append(
                                    self._make_send_invocation(
                                        route,
                                        sender,
                                        data=payload,
                                        track_completion=True,
                                    )
                                )
                            except Exception as e:
                                self.logger.warning(
                                    f"Failed to prepare sender data for '{sender.fn.__name__}' "
                                    f"on route '{route}': {type(e).__name__}: {e}"
                                )
                            continue

                        dedup_key = (route, key, sender.fn.__name__)
                        if dedup_key not in emitted:
                            invocations.append(
                                self._make_send_invocation(
                                    route,
                                    sender,
                                    track_completion=True,
                                )
                            )
                            emitted.add(dedup_key)
                        break

            if not invocations:
                await asyncio.sleep(0.1)
                continue

            queue_size = self.send_queue.qsize()
            expected_queue_size = queue_size + len(invocations)
            if expected_queue_size > self.send_queue_maxsize * 0.8:
                self.logger.warning(
                    f"Queue is about to exceed 80% its capacity; "
                    f"Attempted load size: {expected_queue_size} out of {self.send_queue_maxsize}"
                )

            try:
                for invocation in invocations:
                    await self.send_queue.put(invocation)
            except asyncio.CancelledError:
                self.logger.info("Sender enqueue loop cancelled mid-batch.")
                raise

            await self._wait_for_send_invocations(invocations)

            if self.batch_drain:
                async with self.writer_lock:
                    await writer.drain()

            async with self.connection_lock:
                if self._travel or self._quit:
                    stop_event.set()

    async def _message_sender_timed_loop(
            self,
            writer: asyncio.StreamWriter,
            stop_event: asyncio.Event,
        ) -> None:
        """
        Timed senders become scheduler-owned obligations after admission.

        Philosophy:
        - The initial event is admission. It arms a timed sender when the system
          can accept the work.
        - Once armed, `every` creates an obligation: the scheduler should keep
          servicing that sender on cadence without being blocked by unrelated
          untimed batches.

        This loop intentionally owns only timed senders so legacy untimed
        behavior stays round-based and unchanged.
        """
        while not stop_event.is_set():
            sender_index, sender_parsed_routes = await self._snapshot_sender_registry()
            invocations: list[SendInvocation] = []
            next_due_times: list[float] = []
            drain_needed = False
            admission_now = self.loop.time()

            async with self.timed_sender_lock:
                buffered_events = list(self.timed_event_buffer)
                self.timed_event_buffer.clear()
                if buffered_events:
                    self._arm_timed_senders_from_pending_locked(
                        sender_index,
                        sender_parsed_routes,
                        buffered_events,
                        admission_now,
                    )

                for route, routed_senders in sender_index.items():
                    for sender in routed_senders:
                        if sender.every is None:
                            continue

                        sender_is_reactive = self._has_reactive_filters(sender)
                        runtime = self._ensure_timed_runtime(sender)

                        if runtime.in_flight_done is not None:
                            if runtime.in_flight_done.done():
                                runtime.in_flight_done = None
                                drain_needed = True
                            else:
                                if runtime.next_run_at is not None:
                                    next_due_times.append(runtime.next_run_at)
                                continue

                        if sender_is_reactive and not runtime.armed:
                            continue
                        run_while_allowed = self._poll_run_while(sender, runtime)
                        if run_while_allowed is None:
                            continue

                        if not run_while_allowed:
                            if sender_is_reactive:
                                runtime.armed = False
                                runtime.running = False
                                runtime.pending_payloads.clear()
                                runtime.next_run_at = None
                                runtime.in_flight_done = None
                                if runtime.run_while_task is not None:
                                    runtime.run_while_task.cancel()
                                    runtime.run_while_task = None
                            else:
                                runtime.running = False
                            continue

                        now = self.loop.time()
                        runtime.running = True

                        if runtime.next_run_at is None:
                            runtime.next_run_at = now

                        if now < runtime.next_run_at:
                            next_due_times.append(runtime.next_run_at)
                            continue

                        timed_batch: list[SendInvocation] = []

                        if sender.use_data:
                            buffered_payloads = list(runtime.pending_payloads)
                            runtime.pending_payloads.clear()

                            for buffered_payload in buffered_payloads:
                                try:
                                    payload = self._materialize_send_data(buffered_payload, sender.data_mode)
                                except Exception as e:
                                    self.logger.warning(
                                        f"Failed to materialize timed sender data for '{sender.fn.__name__}' "
                                        f"on route '{route}': {type(e).__name__}: {e}"
                                    )
                                    continue

                                if not self._passes_when_data(sender, route, payload):
                                    continue

                                timed_batch.append(
                                    self._make_send_invocation(
                                        route,
                                        sender,
                                        data=payload,
                                        track_completion=True,
                                    )
                                )
                        else:
                            timed_batch.append(
                                self._make_send_invocation(
                                    route,
                                    sender,
                                    track_completion=True,
                                )
                            )

                        if timed_batch:
                            runtime.in_flight_done = asyncio.gather(
                                *[
                                    invocation.done
                                    for invocation in timed_batch
                                    if invocation.done is not None
                                ],
                                return_exceptions=True,
                            )
                            invocations.extend(timed_batch)
                        else:
                            runtime.in_flight_done = None

                        runtime.next_run_at = now + float(sender.every)
                        next_due_times.append(runtime.next_run_at)

            if drain_needed and self.batch_drain:
                async with self.writer_lock:
                    await writer.drain()

            if invocations:
                queue_size = self.send_queue.qsize()
                expected_queue_size = queue_size + len(invocations)
                if expected_queue_size > self.send_queue_maxsize * 0.8:
                    self.logger.warning(
                        f"Queue is about to exceed 80% its capacity; "
                        f"Attempted load size: {expected_queue_size} out of {self.send_queue_maxsize}"
                    )

                try:
                    for invocation in invocations:
                        await self.send_queue.put(invocation)
                except asyncio.CancelledError:
                    self.logger.info("Timed sender enqueue loop cancelled mid-batch.")
                    raise

            sleep_for = 0.1
            if next_due_times:
                next_due_at = min(next_due_times)
                sleep_for = max(0.0, min(0.1, next_due_at - self.loop.time()))
            await self._wait_for_timed_wakeup(sleep_for)

            async with self.connection_lock:
                if self._travel or self._quit:
                    stop_event.set()

    async def message_sender_loop(
            self,
            writer: asyncio.StreamWriter,
            stop_event: asyncio.Event
        ):
        cancelled = False
        batch_task: Optional[asyncio.Task] = None
        timed_task: Optional[asyncio.Task] = None

        try:
            self.timed_sender_wakeup.clear()
            batch_task = self.loop.create_task(self._message_sender_batch_loop(writer, stop_event))
            timed_task = self.loop.create_task(self._message_sender_timed_loop(writer, stop_event))
            await asyncio.gather(batch_task, timed_task)

        except asyncio.CancelledError:
            self.logger.info("Client about to disconnect...")
            cancelled = True
            raise

        finally:
            stop_event.set()
            self.timed_sender_wakeup.set()

            tasks_to_finish = [
                task for task in (batch_task, timed_task)
                if task is not None
            ]
            for task in tasks_to_finish:
                if not task.done():
                    task.cancel()
            if tasks_to_finish:
                await asyncio.gather(*tasks_to_finish, return_exceptions=True)

            async with self.timed_sender_lock:
                for runtime in self.timed_sender_state.values():
                    if runtime.run_while_task is not None and not runtime.run_while_task.done():
                        runtime.run_while_task.cancel()
                pending_run_while = [
                    runtime.run_while_task
                    for runtime in self.timed_sender_state.values()
                    if runtime.run_while_task is not None
                ]
            if pending_run_while:
                await asyncio.gather(*pending_run_while, return_exceptions=True)

            if self.send_queue is not None:
                for _ in range(self.max_concurrent_workers):
                    try:
                        if cancelled:
                            self.send_queue.put_nowait(None)
                        else:
                            await self.send_queue.put(None)
                    except (asyncio.QueueFull, RuntimeError):
                        break
                    except asyncio.CancelledError:
                        # swallow during shutdown
                        break

    # ==== SESSION HANDLING ====

    async def handle_session(self, host: str = '127.0.0.1', port: int = 8888):
        """
        Run listener and sender concurrently; whichever exits first (due to disconnect, /quit or /travel)
        triggers session termination. The remaining task is cancelled.
        """

        while True:
            # Shared flag between the two tasks to signal coordinated session termination
            stop_event = asyncio.Event()

            # Always clean up old worker tasks and queues before starting new session; guarantees a fresh worker batch and prevents zombie tasks.
            await self._cleanup_workers()
            self.send_queue = asyncio.Queue(maxsize=self.send_queue_maxsize)
            self.event_bridge = asyncio.Queue(maxsize = self.event_bridge_maxsize)
            self.timed_sender_state.clear()

            # reset any previous travel/quit intent so each session starts fresh;
            # travel is only honored if set after this point, quit likewise
            await self._reset_client_intent()

            listen_task = None
            sender_task = None
            current_task = None
            try:
                # Register this session's task so it can be cancelled during shutdown
                current_task = asyncio.current_task()
                async with self.tasks_lock:
                    self.active_tasks.add(current_task)

                # Use lock when accessing dynamic routing information
                async with self.connection_lock:
                    current_host = self.host or host
                    current_port = self.port or port

                # If self.host and self.port are changed dynamically, then client will travel to corresponding server
                reader, writer = await asyncio.open_connection(host=current_host, port=current_port)
                self.logger.info(f"Connected to server @(host={current_host}, port={current_port})")

                # Launch the background send-worker tasks so they can pull from self.send_queue
                self._start_send_workers(writer, stop_event)

                # NOTE: These two functions run concurrently alogn with self._start_send_workers:
                # - message_receiver_loop waits for messages from the server and handles disconnection.
                # - message_sender_loop waits for client input and handles /quit or /travel.
                # Either of them may call stop_event.set(), which signals a shutdown.
                # Once one completes, the other is cancelled and the connection is closed.
                listen_task = asyncio.create_task(self.message_receiver_loop(reader, stop_event))
                sender_task = asyncio.create_task(self.message_sender_loop(writer, stop_event))

                # Wait until either the listener or sender finishes — the first to complete wins
                done, pending = await asyncio.wait(
                    {listen_task, sender_task},
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Cancel the task that did not complete
                for task in pending:
                    task.cancel()

                # Await the completed task (to raise any exceptions or finalize resources)
                for task in done:
                    try:
                        await task
                    except ServerDisconnected as e:
                        # Propagate server-side disconnection to the reconnection handler
                        raise ServerDisconnected(e)
                    except asyncio.CancelledError:
                        # Normal during shutdown; ignore
                        pass
                    except Exception as e:
                        self.logger.exception(f"Unexpected error during session task: {e}")

                # Cleanly close the connection
                writer.close()
                await writer.wait_closed()
                self.logger.info("Disconnected from server.")
            
            finally:
                
                # Ensure both child tasks are cancelled & awaited even if we were cancelled mid-wait
                for task in (listen_task, sender_task):
                    if task is not None and not task.done():
                        task.cancel()
                
                # Clean up worker used in the sender loop
                await self._cleanup_workers()
                self.timed_sender_state.clear()

                # Deregister this session and its children from active tasks
                async with self.tasks_lock:
                    if current_task is not None:
                        self.active_tasks.discard(current_task)

            # Check whether we should quit or loop back to travel to the next server (agent migration)
            async with self.connection_lock:
                if not self._travel or self._quit:
                    break

    # ==== CLIENT LIFE CYCLE ====

    def shutdown(self):
        self.logger.info("Client is shutting down...")
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
            
    def set_termination_signals(self):
        """Install SIGINT and SIGTERM handlers on the event loop."""
        if platform.system() != "Windows":
            for sig in (signal.SIGINT, signal.SIGTERM):
                self.loop.add_signal_handler(sig, self.shutdown)
        else:
            def _handler(sig, frame):
                # Schedule shutdown on the event loop in a thread-safe way.
                try:
                    self.loop.call_soon_threadsafe(self.shutdown)
                except RuntimeError:
                    pass
            signal.signal(signal.SIGINT, _handler)
            # SIGTERM exists on Windows in Python, but behavior varies by launcher
            if hasattr(signal, "SIGTERM"):
                try:
                    signal.signal(signal.SIGTERM, _handler)
                except Exception:
                    pass

    async def _wait_for_registration(self):
        """
        Await all decorator-scheduled register() tasks.
        Clears the list once done so repeated run() calls work cleanly.
        """
        if self._registration_tasks:
            await asyncio.gather(*self._registration_tasks)
            self._registration_tasks.clear()

    async def _wait_for_tasks_to_finish(self):
        """
        Wait for all client handlers to finish.
        """
        # async with self.tasks_lock:
        #     tasks = list(self.active_tasks)
        # if tasks:
        #     await asyncio.gather(*tasks, return_exceptions=True)
        
        async with self.tasks_lock:
            tasks = list(self.active_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)

    async def _retry_loop(self, host, port, limit, stage = "Primary"):
        attempts = 0
        while True:
            try:
                await self.handle_session(host=host, port=port)
                attempts = 0
                # clean disconnect (/quit or /travel)
                return True
            
            except (ConnectionRefusedError, ServerDisconnected, OSError) as e:
                attempts += 1
                self.logger.error(
                    f"[{type(e).__name__}: {e}] "
                    f"({stage}) retry {attempts} of "
                    f"{limit if limit is not None else '∞'}; "
                    f"sleeping {self.retry_delay_seconds}s",
                )
                await asyncio.sleep(self.retry_delay_seconds)

            # Check retry limit
            if (limit is not None and attempts >= limit):
                self.logger.error(f"{stage} retry limit reached ({limit})")
                return False

    async def _get_client_intent(self) -> ClientIntent:
        async with self.connection_lock:
            if self._quit:
                return ClientIntent.QUIT
            if self._travel:
                return ClientIntent.TRAVEL
            return ClientIntent.ABORT
        
    async def _fallback(self):
        async with self.connection_lock:
            self.host = self.default_host
            self.port = self.default_port

    async def run_client(self, host: str = '127.0.0.1', port: int = 8888):
        primary_stage = True
        while True:
            
            stage = "Primary" if primary_stage else "Default"
            limit = self.primary_retry_limit if primary_stage else self.default_retry_limit
            
            succeeded = await self._retry_loop(host, port, limit, stage)
            
            if succeeded:
                
                intent = await self._get_client_intent()
                
                if intent is ClientIntent.QUIT:
                    break  
                
                elif intent is ClientIntent.TRAVEL:
                    primary_stage = True
                    continue
                
                else:
                    break

            else:
                
                if primary_stage:
                    primary_stage = False
                    await self._fallback()
                    self.logger.warning(f"Falling back to default server at {self.default_host}:{self.default_port}")
                    continue
                
                else:
                    self.logger.critical(
                        f"Cannot connect to fallback {self.default_host}:{self.default_port} after "
                        f"{self.default_retry_limit or '∞'} attempts; exiting"
                        )
                    break
            
    def run(
            self, 
            host: str = '127.0.0.1', 
            port: int = 8888, 
            config_path: Optional[str] = None,
            config_dict: Optional[dict[str, Any]] = None,
        ):
        try:
            
            if config_dict is None:
                # Load config parameters
                client_config = load_config(config_path=config_path, debug=True)
            elif isinstance(config_dict, dict):
                # Shallow copy to avoid external mutation
                client_config = dict(config_dict)  
            else:
                raise TypeError(f"SummonerClient.run: config_dict must be a dict or None, got {type(config_dict).__name__}")
            
            # client_config = load_config(config_path=config_path, debug=True)
            self._apply_config(client_config)

            # Compile Regex information to parse the flow
            self.initialize()

            # install SIGINT/SIGTERM handlers onto the loop
            self.set_termination_signals()

            # Block until every @receive / @send decorator has registered
            self.loop.run_until_complete(self._wait_for_registration())

            # Start the client logic
            self.loop.run_until_complete(self.run_client(host=host, port=port))

        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            try:
                self.loop.run_until_complete(self._wait_for_tasks_to_finish())
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass

            try:
                # The following gather is redundant if all workers are properly awaited above.
                # Left here as an extra safety net in case new worker tasks are added elsewhere.
                if self.worker_tasks:
                    self.loop.run_until_complete(asyncio.gather(*self.worker_tasks, return_exceptions=True))
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            
            self.loop.close()
            self.logger.info("Client exited cleanly.")
