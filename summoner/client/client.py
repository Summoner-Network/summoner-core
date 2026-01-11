import os
import sys
import json
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
from collections import defaultdict
import platform

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.utils import (
    load_config,
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

class ServerDisconnected(Exception):
    """Raised when the server closes the connection."""
    pass

class SummonerClient:

    DEFAULT_MAX_BYTES_PER_LINE = 64 * 1024      # 64 KiB
    DEFAULT_READ_TIMEOUT_SECONDS = None         # Wait for messages to arrive
    DEFAULT_CONCURRENCY_LIMIT = 50

    DEFAULT_RETRY_DELAY = 3
    DEFAULT_PRIMARY_RETRY_LIMIT = 3
    DEFAULT_FAILOVER_RETRY_LIMIT = 2

    DEFAULT_EVENT_BRIDGE_SIZE = 1000
    DEFAULT_MAX_CONSECUTIVE_ERRORS = 3          # Failed attempts to send before disconnecting

    core_version = "1.1.0"

    def __init__(self, name: Optional[str] = None):
        
        # Give a name to the server
        self.name = name if isinstance(name, str) else "<client:no-name>"
        
        # Create a bare logger (no handlers yet)
        self.logger: Logger = get_logger(self.name)

        # Create a new event loop
        self.loop = asyncio.new_event_loop()

        # Set the new loop as the current thread
        asyncio.set_event_loop(self.loop)

        # Protect concurrent access to the set of active tasks
        self.active_tasks: set[asyncio.Task] = set()
        self.tasks_lock = asyncio.Lock()

        # Protect route registration and access for receive/send functions
        self.receiver_index: dict[str, Receiver] = {}
        self.sender_index: dict[str, list[Sender]] = {} # do not use defaultdict(list) because we use .get
        self.routes_lock = asyncio.Lock()

        # Dynamic routing configuration (can be changed at runtime)
        self.host: Optional[str] = None
        self.port: Optional[int] = None
        self._travel = False # Flag to signal intent to travel
        self._quit = False # Flag to signal intent to shutdown the client
        self.connection_lock = asyncio.Lock()

        # Safe registration of decorators (hooks, receivers, senders)
        self._registration_tasks: list[asyncio.Task] = []

        # One-time indexing of parsed routes
        self.receiver_parsed_routes: dict[str, ParsedRoute] = {}
        self.sender_parsed_routes: dict[str, ParsedRoute] = {}

        # Flow representing the underlying finite state machine
        self._flow = Flow()

        # Functions to read and write the flow's active states in memory
        self._upload_states: Optional[Callable[[Any], Awaitable]] = None
        self._download_states: Optional[Callable[[Any], Awaitable]] = None

        self.event_bridge_maxsize = None
        self.max_concurrent_workers = None # Limit the sending rate (will use 50 if None is given)
        self.send_queue_maxsize = None
        self.max_bytes_per_line = None
        self.read_timeout_seconds = None # None is prefered
        self.retry_delay_seconds = None
        self.batch_drain = None

        # Pass Event information from the receiving end to the sending end
        self.event_bridge: Optional[asyncio.Queue[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]]] = None

        self.send_queue: Optional[asyncio.Queue] = None
        self.send_workers_started = False  # To avoid double-starting workers
        self.worker_tasks: list[asyncio.Task] = []
        self.writer_lock = asyncio.Lock()

        # Store validation hooks to be used before sending and after receiving
        self.sending_hooks: dict[tuple[int,...], Callable[[Union[str, dict]], Union[str, dict]]] = {}
        self.receiving_hooks: dict[tuple[int,...], Callable[[Union[str, dict]], Union[str, dict]]] = {}
        self.hooks_lock = asyncio.Lock()

        # ─── DNA capture for merging ─────────────────────────────────────────
        # lists of dicts, each entry records one decorated handler
        self._dna_receivers: list[dict] = []
        self._dna_senders:   list[dict] = []
        self._dna_hooks:     list[dict] = []

        self._dna_upload_states: Optional[dict] = None
        self._dna_download_states: Optional[dict] = None

    # ==== VERSION SPECIFIC ====

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

            if not isinstance(route, str):
                raise TypeError(f"Argument `route` must be string. Provided: {route}")

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
                "source": inspect.getsource(fn),  # optional, for text serialization
            })

            # ----[ Registration Code ]----
            async def register():
                receiver = Receiver(fn=fn, priority=tuple_priority)

                if self._flow.in_use:
                    parsed_route = self._flow.parse_route(route)
                    normalized_route = str(parsed_route)

                async with self.routes_lock:
                    if route in self.receiver_index:
                        self.logger.warning(f"Route '{route}' already exists. Overwriting.")
                    
                    if self._flow.in_use:
                        self.receiver_parsed_routes[normalized_route] = parsed_route
                        self.receiver_index[normalized_route] = receiver
                    else:
                        self.receiver_index[route] = receiver

            # ----[ Safe Registration ]----
            self._schedule_registration(register())

            return fn

        return decorator
    
    # ==== SENDER REGISTRATION ====

    def send(
            self, 
            route: str, 
            multi: bool = False, 
            on_triggers: Optional[set[Signal]] = None,
            on_actions: Optional[set[Type]] = None,
        ):
        route = route.strip()
        def decorator(fn: Callable[[], Awaitable]):
            
            # ----[ Safety Checks ]----
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@send sender '{fn.__name__}' must be async")
            
            sig = inspect.signature(fn)
            if len(sig.parameters) != 0:
                raise TypeError(f"@send '{fn.__name__}' must accept no arguments")
            
            if not multi:
                _check_param_and_return(
                    fn,
                    decorator_name="@send",
                    allow_param=(),   # no args allowed
                    allow_return=(type(None), Any, str, dict),
                    logger=self.logger,
                )
            else:
                _check_param_and_return(
                    fn,
                    decorator_name="@send[multi=True]",
                    allow_param=(),   # no args allowed
                    allow_return=(Any, list, list[str], list[dict], list[Union[str, dict]]),
                    logger=self.logger,
                )
        
            if not isinstance(route, str):
                raise TypeError(f"Argument `route` must be string. Provided: {route}")

            if not isinstance(multi, bool):
                raise TypeError(f"Argument `multi` must be Boolean. Provided: {multi}")

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
            
            # ----[ DNA capture ]----
            self._dna_senders.append({
                "fn": fn,
                "route": route,
                "multi": multi,
                "on_triggers": on_triggers,
                "on_actions": on_actions,
                "source": inspect.getsource(fn),
            })

            # ----[ Registration Code ]----
            async def register():
                
                sender = Sender(fn=fn, multi=multi, actions=on_actions, triggers=on_triggers)
                actions_exist = isinstance(on_actions, set) and bool(on_actions)
                triggers_exist = isinstance(on_triggers, set) and bool(on_triggers)
                
                if self._flow.in_use:
                    parsed_route = self._flow.parse_route(route)
                    normalized_route = str(parsed_route)

                async with self.routes_lock:
                    if self._flow.in_use:
                        self.sender_index.setdefault(normalized_route, [])
                        self.sender_index[normalized_route].append(sender)
                        if route not in self.sender_parsed_routes and actions_exist or triggers_exist:
                            self.sender_parsed_routes[normalized_route] = parsed_route
                    else:
                        self.sender_index.setdefault(route, [])
                        self.sender_index[route].append(sender)

            # ----[ Safe Registration ]----
            # NOTE: register() is run ASAP and _registration_tasks is used to wait all registrations before run_client()
            self._schedule_registration(register())

            return fn

        return decorator

    # ==== DNA PROCESSING ====

    # def dna(self) -> str:
    #     """
    #     Serialize this client's handlers into a JSON string.
    #     Each entry captures:
    #       - type: "receive" | "send" | "hook"
    #       - decorator parameters (route, priority, etc.)
    #       - source: the full text of the async function
    #     """
    #     entries = []

    #     # 1) Receivers
    #     for dna in self._dna_receivers:
    #         entries.append({
    #             "type":    "receive",
    #             "route":   dna["route"],
    #             "priority": dna["priority"],
    #             "source":  inspect.getsource(dna["fn"]),
    #             "module":   dna["fn"].__module__,
    #             "fn_name":  dna["fn"].__name__,
    #         })

    #     # 2) Senders
    #     for dna in self._dna_senders:
    #         entries.append({
    #             "type":        "send",
    #             "route":       dna["route"],
    #             "multi":       dna["multi"],
    #             "on_triggers": [t.name for t in (dna["on_triggers"] or [])],
    #             "on_actions":  [a.__name__ for a in (dna["on_actions"] or [])],
    #             "source":      inspect.getsource(dna["fn"]),
    #             "module":   dna["fn"].__module__,
    #             "fn_name":  dna["fn"].__name__,
    #         })

    #     # 3) Hooks
    #     for dna in self._dna_hooks:
    #         entries.append({
    #             "type":     "hook",
    #             "direction": dna["direction"].name,
    #             "priority": dna["priority"],
    #             "source":   inspect.getsource(dna["fn"]),
    #             "module":   dna["fn"].__module__,
    #             "fn_name":  dna["fn"].__name__,
    #         })

    #     return json.dumps(entries)

    # def dna(self, include_context: bool = False) -> str:
    #     """
    #     Serialize this client's handlers into a JSON string.

    #     If include_context=True, also add a first entry of type "__context__"
    #     containing best-effort imports + cloneable globals + simple recipes
    #     to reduce hardcoded boilerplate in clone scripts.
    #     """
    #     import builtins
    #     import types as _types
    #     import json as _json

    #     def _infer_var_name() -> str:
    #         # try to find the module-global name that points to this client instance
    #         try:
    #             for k, v in getattr(self, "__dict__", {}).get("__globals__", {}).items():
    #                 if v is self:
    #                     return k
    #         except Exception:
    #             pass

    #         # better: look at any decorated handler globals (they share module globals)
    #         handlers = []
    #         for d in self._dna_receivers:
    #             handlers.append(d["fn"])
    #         for d in self._dna_senders:
    #             handlers.append(d["fn"])
    #         for d in self._dna_hooks:
    #             handlers.append(d["fn"])

    #         for fn in handlers:
    #             try:
    #                 for k, v in fn.__globals__.items():
    #                     if v is self:
    #                         return k
    #             except Exception:
    #                 continue
    #         return "agent"

    #     def _jsonable(v) -> bool:
    #         try:
    #             _json.dumps(v)
    #             return True
    #         except Exception:
    #             return False

    #     # Collect handler FNs used for dependency discovery
    #     handler_fns = []
    #     if self._upload_states is not None:
    #         handler_fns.append(self._upload_states)
    #     if self._download_states is not None:
    #         handler_fns.append(self._download_states)
    #     for d in self._dna_receivers:
    #         handler_fns.append(d["fn"])
    #     for d in self._dna_senders:
    #         handler_fns.append(d["fn"])
    #     for d in self._dna_hooks:
    #         handler_fns.append(d["fn"])

    #     # Normal DNA entries (existing behavior)
    #     entries: list[dict] = []

    #     if self._dna_upload_states is not None:
    #         fn = self._dna_upload_states["fn"]
    #         entries.append({
    #             "type":   "upload_states",
    #             "source": self._dna_upload_states.get("source") or inspect.getsource(fn),
    #             "module": fn.__module__,
    #             "fn_name": fn.__name__,
    #         })

    #     if self._dna_download_states is not None:
    #         fn = self._dna_download_states["fn"]
    #         entries.append({
    #             "type":   "download_states",
    #             "source": self._dna_download_states.get("source") or inspect.getsource(fn),
    #             "module": fn.__module__,
    #             "fn_name": fn.__name__,
    #         })

    #     for dna in self._dna_receivers:
    #         entries.append({
    #             "type":    "receive",
    #             "route":   dna["route"],
    #             "priority": dna["priority"],
    #             "source":  dna.get("source") or inspect.getsource(dna["fn"]),
    #             "module":  dna["fn"].__module__,
    #             "fn_name": dna["fn"].__name__,
    #         })

    #     for dna in self._dna_senders:
    #         entries.append({
    #             "type":        "send",
    #             "route":       dna["route"],
    #             "multi":       dna["multi"],
    #             "on_triggers": [t.name for t in (dna["on_triggers"] or [])],
    #             "on_actions":  [a.__name__ for a in (dna["on_actions"] or [])],
    #             "source":      dna.get("source") or inspect.getsource(dna["fn"]),
    #             "module":      dna["fn"].__module__,
    #             "fn_name":     dna["fn"].__name__,
    #         })

    #     for dna in self._dna_hooks:
    #         entries.append({
    #             "type":      "hook",
    #             "direction": dna["direction"].name,
    #             "priority":  dna["priority"],
    #             "source":    dna.get("source") or inspect.getsource(dna["fn"]),
    #             "module":    dna["fn"].__module__,
    #             "fn_name":   dna["fn"].__name__,
    #         })

    #     if not include_context:
    #         return _json.dumps(entries)

    #     # ---- context build (best-effort) ----

    #     # Names we will NOT treat as cloneable globals
    #     inferred_var_name = _infer_var_name()
    #     excluded_names = {inferred_var_name, "__builtins__"}

    #     # recipes we know how to rebuild cheaply
    #     recipes: dict[str, str] = {}
    #     globals_out: dict[str, object] = {}
    #     imports_out: set[str] = set()
    #     missing: list[str] = []

    #     # Always include typing star to make annotation evaluation easy during validation
    #     imports_out.add("from typing import *")

    #     # lock detection without relying on exact class name
    #     try:
    #         import asyncio as _asyncio
    #         _lock_type = type(_asyncio.Lock())
    #     except Exception:
    #         _asyncio = None
    #         _lock_type = None

    #     # ---- context build (best-effort) ----
    #     import ast
    #     import textwrap
    #     from pathlib import Path

    #     known_modules: set[str] = set()

    #     def _annotation_names_from_source(src: str) -> set[str]:
    #         """
    #         Best-effort: extract Name identifiers used in arg/return annotations
    #         so annotation-only deps (ex: -> Event) get picked up.
    #         """
    #         out: set[str] = set()
    #         try:
    #             tree = ast.parse(textwrap.dedent(src))
    #         except Exception:
    #             return out

    #         # Find first (async) function def in that snippet
    #         fn_node = None
    #         for node in tree.body:
    #             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
    #                 fn_node = node
    #                 break
    #         if fn_node is None:
    #             return out

    #         ann_nodes = []
    #         for a in getattr(fn_node.args, "args", []):
    #             if a.annotation is not None:
    #                 ann_nodes.append(a.annotation)
    #         for a in getattr(fn_node.args, "kwonlyargs", []):
    #             if a.annotation is not None:
    #                 ann_nodes.append(a.annotation)
    #         if getattr(fn_node.args, "vararg", None) is not None and fn_node.args.vararg.annotation is not None:
    #             ann_nodes.append(fn_node.args.vararg.annotation)
    #         if getattr(fn_node.args, "kwarg", None) is not None and fn_node.args.kwarg.annotation is not None:
    #             ann_nodes.append(fn_node.args.kwarg.annotation)
    #         if fn_node.returns is not None:
    #             ann_nodes.append(fn_node.returns)

    #         for ann in ann_nodes:
    #             for n in ast.walk(ann):
    #                 if isinstance(n, ast.Name):
    #                     out.add(n.id)
    #         return out

    #     def _import_line_for(name: str, value: object, g: dict[str, object]) -> str | None:
    #         """
    #         More general than before:
    #         - still handles modules
    #         - still handles exported functions/classes
    #         - additionally handles exported constants/type-aliases (ex: Event)
    #         by checking module.<name> is value
    #         - avoids emitting 'from __main__ import X' by rewriting __main__ to file stem
    #         """
    #         # Modules are always importable
    #         if isinstance(value, _types.ModuleType):
    #             mod = value.__name__
    #             known_modules.add(mod)
    #             leaf = mod.split(".")[-1]
    #             if name == leaf:
    #                 return f"import {mod}"
    #             return f"import {mod} as {name}"

    #         # Candidate module to import from
    #         mod = getattr(value, "__module__", None)

    #         # If the object says "__main__", rewrite using the defining file name
    #         if mod == "__main__":
    #             file = g.get("__file__")
    #             if isinstance(file, str) and file.endswith(".py"):
    #                 mod = Path(file).stem  # ex: agent_p1.py -> "agent_p1"

    #         if not isinstance(mod, str) or mod == "builtins":
    #             # We can't infer a stable import origin
    #             mod = None

    #         from importlib import import_module

    #         # 1) If we have a module candidate, try "from mod import <name>"
    #         if mod is not None:
    #             try:
    #                 m = import_module(mod)
    #                 known_modules.add(mod)
    #                 if getattr(m, name, None) is value:
    #                     return f"from {mod} import {name}"
    #             except Exception:
    #                 pass

    #             # Also try "from mod import <value.__name__>" for functions/classes/etc
    #             obj = getattr(value, "__name__", None)
    #             if isinstance(obj, str):
    #                 try:
    #                     m = import_module(mod)
    #                     if getattr(m, obj, None) is value:
    #                         if name == obj:
    #                             return f"from {mod} import {obj}"
    #                         return f"from {mod} import {obj} as {name}"
    #                 except Exception:
    #                     pass

    #         # 2) Fallback: try modules we've already seen (Move/Stay/Test often reveals triggers module)
    #         for km in tuple(known_modules):
    #             try:
    #                 m = import_module(km)
    #                 if getattr(m, name, None) is value:
    #                     return f"from {km} import {name}"
    #             except Exception:
    #                 continue

    #         return None

    #     # Discover dependencies from all handlers
    #     for fn in handler_fns:
    #         g = getattr(fn, "__globals__", {})

    #         # names used in bytecode
    #         names_to_scan = set(getattr(fn, "__code__", None).co_names if hasattr(fn, "__code__") else ())

    #         # plus names used only in annotations (best-effort)
    #         try:
    #             src = inspect.getsource(fn)
    #             names_to_scan |= _annotation_names_from_source(src)
    #         except Exception:
    #             pass

    #         for name in names_to_scan:
    #             if name in excluded_names:
    #                 continue
    #             if name in builtins.__dict__:
    #                 continue
    #             if name not in g:
    #                 continue

    #             value = g[name]

    #             # Skip binding the client itself
    #             if value is self:
    #                 continue
    #             if isinstance(value, SummonerClient):
    #                 missing.append(name)
    #                 continue

    #             # Known rebuildable: asyncio locks
    #             if _lock_type is not None and isinstance(value, _lock_type):
    #                 imports_out.add("import asyncio")
    #                 recipes.setdefault(name, "asyncio.Lock()")
    #                 continue

    #             # Modules / exported objects / exported constants -> imports
    #             line = _import_line_for(name, value, g)
    #             if line is not None:
    #                 imports_out.add(line)
    #                 continue

    #             # JSON-able constants -> globals
    #             if _jsonable(value):
    #                 globals_out.setdefault(name, value)
    #                 continue

    #             # Otherwise unknown
    #             missing.append(name)

    #     context_entry = {
    #         "type": "__context__",
    #         "var_name": inferred_var_name,
    #         "imports": sorted(imports_out),
    #         "globals": globals_out,
    #         "recipes": recipes,
    #         "missing": sorted(set(missing)),
    #     }

    #     return _json.dumps([context_entry] + entries)

    def dna(self, include_context: bool = False) -> str:
        """
        Serialize this client's handlers into a JSON string.

        If include_context=True, also add a first entry of type "__context__"
        containing best-effort imports + cloneable globals + simple recipes
        to reduce hardcoded boilerplate in clone scripts.

        Design principle: the produced DNA should be portable.
        In particular, we do NOT emit imports from '__main__' because that is not stable.
        Those objects should appear in 'missing' and be provided by rebind_globals
        (the "child has its own mitochondria" model).
        """
        import builtins
        import types as _types
        import json as _json
        import inspect
        import ast
        import textwrap
        from pathlib import Path

        # Optional: used for recipe inference
        try:
            from summoner.protocol.process import Node as _Node
        except Exception:
            _Node = None

        # ---- helpers ------------------------------------------------------------

        def _best_source(fn, override: str | None = None) -> str:
            if isinstance(override, str) and override.strip():
                return override
            try:
                return inspect.getsource(fn)
            except Exception:
                # If your code attaches a source fallback, keep using it.
                src = getattr(fn, "__dna_source__", None)
                if isinstance(src, str) and src.strip():
                    return src
                # Last resort: still produce something executable
                name = getattr(fn, "__name__", "handler")
                return f"async def {name}(*args, **kwargs):\n    raise RuntimeError('source unavailable')\n"

        def _infer_var_name() -> str:
            # try to find the module-global name that points to this client instance
            try:
                for k, v in getattr(self, "__dict__", {}).get("__globals__", {}).items():
                    if v is self:
                        return k
            except Exception:
                pass

            # look at any decorated handler globals (they share module globals)
            handlers = []
            if getattr(self, "_upload_states", None) is not None:
                handlers.append(self._upload_states)
            if getattr(self, "_download_states", None) is not None:
                handlers.append(self._download_states)
            for d in getattr(self, "_dna_receivers", []) or []:
                handlers.append(d.get("fn"))
            for d in getattr(self, "_dna_senders", []) or []:
                handlers.append(d.get("fn"))
            for d in getattr(self, "_dna_hooks", []) or []:
                handlers.append(d.get("fn"))

            for fn in handlers:
                if fn is None:
                    continue
                try:
                    for k, v in fn.__globals__.items():
                        if v is self:
                            return k
                except Exception:
                    continue
            return "agent"

        def _jsonable(v) -> bool:
            try:
                _json.dumps(v)
                return True
            except Exception:
                return False

        def _annotation_names_from_source(src: str) -> set[str]:
            """
            Best-effort: extract Name identifiers used in arg/return annotations.
            This is a fallback only; prefer fn.__annotations__ (more robust).
            """
            out: set[str] = set()
            try:
                tree = ast.parse(textwrap.dedent(src))
            except Exception:
                return out

            fn_node = None
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_node = node
                    break
            if fn_node is None:
                return out

            ann_nodes = []
            for a in getattr(fn_node.args, "args", []):
                if a.annotation is not None:
                    ann_nodes.append(a.annotation)
            for a in getattr(fn_node.args, "kwonlyargs", []):
                if a.annotation is not None:
                    ann_nodes.append(a.annotation)
            if getattr(fn_node.args, "vararg", None) is not None and fn_node.args.vararg.annotation is not None:
                ann_nodes.append(fn_node.args.vararg.annotation)
            if getattr(fn_node.args, "kwarg", None) is not None and fn_node.args.kwarg.annotation is not None:
                ann_nodes.append(fn_node.args.kwarg.annotation)
            if fn_node.returns is not None:
                ann_nodes.append(fn_node.returns)

            for ann in ann_nodes:
                for n in ast.walk(ann):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
            return out

        def _recipe_for_value(name: str, value: object) -> str | None:
            """
            Try to produce a safe-ish rebuild recipe for common deterministic objects.
            This is intentionally conservative: only emit recipes we are confident about.
            """
            # Path -> Path("...")
            if isinstance(value, Path):
                return f'Path({value.as_posix()!r})'

            # set/list/tuple of Node -> set(Node(x) for x in [...])
            if _Node is not None:
                if isinstance(value, (set, frozenset, list, tuple)):
                    elems = list(value)
                    if elems and all(isinstance(e, _Node) for e in elems):
                        labels = sorted({str(e) for e in elems})
                        # deterministic reconstruction
                        return f"set(_Node(x) for x in {labels!r})"

            return None

        def _import_line_for(name: str, value: object, g: dict[str, object], known_modules: set[str]) -> str | None:
            """
            Produce an import line when we can.
            Rules:
            - Modules are importable.
            - Exported objects/constants are importable if module.<name> is value.
            - NEVER emit imports from '__main__' (not portable).
            """
            from importlib import import_module

            # Modules are always importable
            if isinstance(value, _types.ModuleType):
                mod = value.__name__
                known_modules.add(mod)
                leaf = mod.split(".")[-1]
                if name == leaf:
                    return f"import {mod}"
                return f"import {mod} as {name}"

            mod = getattr(value, "__module__", None)
            if mod == "__main__":
                # Not stable. Force rebind_globals instead.
                return None

            # 1) Try importing by the global name first: from mod import <name>
            if isinstance(mod, str) and mod and mod != "builtins":
                try:
                    m = import_module(mod)
                    known_modules.add(mod)
                    if getattr(m, name, None) is value:
                        return f"from {mod} import {name}"
                except Exception:
                    pass

                # 2) Try importing by the object's __name__
                obj = getattr(value, "__name__", None)
                if isinstance(obj, str) and obj:
                    try:
                        m = import_module(mod)
                        if getattr(m, obj, None) is value:
                            if name == obj:
                                return f"from {mod} import {obj}"
                            return f"from {mod} import {obj} as {name}"
                    except Exception:
                        pass

            # 3) Fallback: search already-known modules for exported constants
            for km in tuple(known_modules):
                try:
                    m = import_module(km)
                    if getattr(m, name, None) is value:
                        return f"from {km} import {name}"
                except Exception:
                    continue

            return None

        # ---- gather handler fns -------------------------------------------------

        handler_fns = []
        if getattr(self, "_upload_states", None) is not None:
            handler_fns.append(self._upload_states)
        if getattr(self, "_download_states", None) is not None:
            handler_fns.append(self._download_states)
        for d in getattr(self, "_dna_receivers", []) or []:
            handler_fns.append(d.get("fn"))
        for d in getattr(self, "_dna_senders", []) or []:
            handler_fns.append(d.get("fn"))
        for d in getattr(self, "_dna_hooks", []) or []:
            handler_fns.append(d.get("fn"))

        # ---- normal DNA entries -------------------------------------------------

        entries: list[dict] = []

        if getattr(self, "_dna_upload_states", None) is not None:
            fn = self._dna_upload_states["fn"]
            entries.append({
                "type": "upload_states",
                "source": _best_source(fn, self._dna_upload_states.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        if getattr(self, "_dna_download_states", None) is not None:
            fn = self._dna_download_states["fn"]
            entries.append({
                "type": "download_states",
                "source": _best_source(fn, self._dna_download_states.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        for dna in getattr(self, "_dna_receivers", []) or []:
            fn = dna["fn"]
            entries.append({
                "type": "receive",
                "route": dna["route"],
                "priority": dna["priority"],
                "source": _best_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        for dna in getattr(self, "_dna_senders", []) or []:
            fn = dna["fn"]
            entries.append({
                "type": "send",
                "route": dna["route"],
                "multi": dna["multi"],
                "on_triggers": [t.name for t in (dna["on_triggers"] or [])],
                "on_actions": [a.__name__ for a in (dna["on_actions"] or [])],
                "source": _best_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        for dna in getattr(self, "_dna_hooks", []) or []:
            fn = dna["fn"]
            entries.append({
                "type": "hook",
                "direction": dna["direction"].name,
                "priority": dna["priority"],
                "source": _best_source(fn, dna.get("source")),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

        if not include_context:
            return _json.dumps(entries)

        # ---- context build (best-effort) ---------------------------------------

        inferred_var_name = _infer_var_name()
        excluded_names = {inferred_var_name, "__builtins__"}

        recipes: dict[str, str] = {}
        globals_out: dict[str, object] = {}
        imports_out: set[str] = set()
        missing: list[str] = []

        # Helps compilation when annotations mention typing constructs.
        imports_out.add("from typing import *")

        # If we emit Path(...) recipes, we must import it.
        # (We only add Path if we actually emit a Path recipe.)
        path_needed = False

        # lock detection without relying on exact class name
        try:
            import asyncio as _asyncio
            _lock_type = type(_asyncio.Lock())
        except Exception:
            _asyncio = None
            _lock_type = None

        known_modules: set[str] = set()

        for fn in handler_fns:
            if fn is None:
                continue
            g = getattr(fn, "__globals__", {})
            if not isinstance(g, dict):
                continue

            # Names used in bytecode
            names_to_scan = set(getattr(fn, "__code__", None).co_names if hasattr(fn, "__code__") else ())

            # Names used only in annotations (robust path): scan annotation objects
            # This catches the exact failure you saw with `Event`.
            try:
                ann = getattr(fn, "__annotations__", {}) or {}
                for v in ann.values():
                    # typing constructs (Union, list[Node], etc.) often aren't importable constants,
                    # but real classes like Event/Node are.
                    if isinstance(v, type) or inspect.isfunction(v) or inspect.ismodule(v):
                        nm = getattr(v, "__name__", None)
                        if isinstance(nm, str) and nm:
                            # We'll try to import it directly later, so ensure it is in scan set
                            names_to_scan.add(nm)
            except Exception:
                pass

            # Fallback: parse annotation names from source (covers some edge cases)
            try:
                src = _best_source(fn)
                names_to_scan |= _annotation_names_from_source(src)
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

                # Skip binding the client itself
                if value is self:
                    continue

                # Avoid auto-copying other clients
                try:
                    from summoner.client.client import SummonerClient as _SummonerClient
                    if isinstance(value, _SummonerClient):
                        missing.append(name)
                        continue
                except Exception:
                    pass

                # Known rebuildable: asyncio locks
                if _lock_type is not None and isinstance(value, _lock_type):
                    imports_out.add("import asyncio")
                    recipes.setdefault(name, "asyncio.Lock()")
                    continue

                # Try to generate a deterministic recipe for some objects (ex: OBJECTS)
                r = _recipe_for_value(name, value)
                if isinstance(r, str):
                    recipes.setdefault(name, r)
                    if "Path(" in r:
                        path_needed = True
                    # If we emitted Node recipes, we need _Node in scope.
                    # We use _Node in the recipe string above.
                    if "_Node(" in r:
                        imports_out.add("from summoner.protocol.process import Node as _Node")
                    continue

                # Modules / exported objects / exported constants -> imports
                line = _import_line_for(name, value, g, known_modules)
                if line is not None:
                    imports_out.add(line)
                    continue

                # JSON-able constants -> globals
                if _jsonable(value):
                    globals_out.setdefault(name, value)
                    continue

                # Otherwise unknown
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

        return _json.dumps([context_entry] + entries)

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
                                f"failed on payload {payload!r}: {e}"
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

                    # ----[ Empty: Skip and Prevent Client Overwhelming ]----
                    if not batches:
                        _ = await reader.readline() # Space
                        await asyncio.sleep(0.1) # Time
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
                                await self.event_bridge.put((priority,) + event_data)

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
            
            item: Optional[tuple[str, Sender]] = await self.send_queue.get()
            if item is None:
                self.send_queue.task_done()
                break
            
            route, sender = item
            try:
                result = await sender.fn()

                # ----[ Urgent: Handle Aborts ]----
                async with self.connection_lock:
                    if self._quit:
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
                                f"failed on payload {payload!r}: {e}"
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
                    await writer.drain()

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(
                    f"Worker for {sender.fn.__name__} crashed ({consecutive_errors} in a row): {e}",
                    exc_info=True
                )
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

    async def message_sender_loop(
            self, 
            writer: asyncio.StreamWriter, 
            stop_event: asyncio.Event
        ):

        # ----[ Helper: Matches Routes Between Senders and Receivers to Trigger Send ]----
        def _route_accepts(
                sender_pr: ParsedRoute, 
                receiver_pr: ParsedRoute
            ) -> bool:
            source_ok   = all(any(n.accepts(m)  for m in receiver_pr.source)     for n in sender_pr.source)
            label_ok    = all(any(n.accepts(m)  for m in receiver_pr.label)      for n in sender_pr.label)
            target_ok   = all(any(n.accepts(m)  for m in receiver_pr.target)     for n in sender_pr.target)
            return source_ok and label_ok and target_ok

        cancelled = False
        try:

            # ----[ Keep Sending While Actively Listening (No Travel) ]----
            while not stop_event.is_set():
                
                # ----[ Prepare Sender Batch ]----
                    
                async with self.routes_lock:
                    sender_index: dict[str, list[Sender]] = self.sender_index.copy()

                # ----[ Fast upload of pending event data ]----
                if self._flow.in_use:
                    
                    async with self.routes_lock:
                        sender_parsed_routes: dict[str, ParsedRoute] = self.sender_parsed_routes.copy()
                    
                    pending: list[tuple[tuple[int, ...], Optional[str], ParsedRoute, Event]] = []
                    try:
                        while True:
                            pending.append(self.event_bridge.get_nowait())
                    except asyncio.QueueEmpty:
                        pass
                    
                    pending.sort(key=lambda it: hook_priority_order(it[0]))

                # ----[ Build Sender Batch ]---- 
                senders: list[tuple[str, Sender]] = []

                # De-dup set: at most one sender per (route, key-from-recv, recv-handler-name) this cycle.
                emitted: set[tuple[str, Optional[str], str]] = set()

                for route, routed_senders in sender_index.items():
                    for sender in routed_senders:
                        
                        # Non-reactive (no actions/triggers): preserve current behavior
                        if (not self._flow.in_use) or (sender.actions is None and sender.triggers is None):
                            senders.append((route, sender))
                        
                        # Reactive: require matching a pending activation (existential)
                        elif self._flow.in_use and ((sender.actions and isinstance(sender.actions, set)) or 
                                   (sender.triggers and isinstance(sender.triggers, set))):
                            
                            sender_parsed_route = sender_parsed_routes.get(route)
                            if sender_parsed_route is None:
                                continue
                            
                            # Iterate pending in queue order; first match "wins" for this (route,key,fn_name)
                            for (priority, key, parsed_route, event) in pending:
                                if _route_accepts(sender_parsed_route, parsed_route) and sender.responds_to(event):
                                    dedup_key = (route, key, sender.fn.__name__)  # key scopes to the activation thread/peer
                                    if dedup_key not in emitted:
                                        senders.append((route, sender))
                                        emitted.add(dedup_key)
                                    break  # do not enqueue multiple times for this sender this cycle
                                    
                # ----[ Empty: Skip and Prevent Client Overwhelming | Almost full: warning ]----
                if not senders:
                    await asyncio.sleep(0.1) # Time
                    continue
                else:
                    queue_size = self.send_queue.qsize()
                    expected_queue_size = queue_size + len(senders)
                    if expected_queue_size > self.send_queue_maxsize * 0.8:  # 80% full
                        self.logger.warning(f"Queue is about to exceed 80% its capacity; Attempted load size: {expected_queue_size} out of {self.send_queue_maxsize}")

                # ----[ Enqueue Sender Batch | Senders Are Run in Background ]----
                try:
                    for sender in senders:
                        await self.send_queue.put(sender)  # Will block if full (i.e., back-pressure)
                except asyncio.CancelledError:
                    self.logger.info("Sender enqueue loop cancelled mid-batch.")
                    raise

                # ----[ Wait for Sender Batch to Finish]----
                await self.send_queue.join()

                if self.batch_drain:
                    await writer.drain()

                # ----[ Quit or Travel ]----
                async with self.connection_lock:
                    if self._travel or self._quit:
                        stop_event.set()

        except asyncio.CancelledError:
            self.logger.info("Client about to disconnect...")
            cancelled = True
            # do NOT re-raise yet; let finally run first

        finally:
            # Best-effort signal to workers; never block on shutdown
            if self.send_queue is not None:
                # This may result in redundant cancellation if shutdown() is also called,
                # but guarantees all workers get signaled even in abrupt exits.
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

    # ==== HANDLE BOTH SENDING AND RECEIVING ENDS ====

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

                # Deregister this session and its children from active tasks
                async with self.tasks_lock:
                    if task is not None:
                        self.active_tasks.discard(task)

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
        """
        Install SIGINT/SIGTERM handlers onto the loop:
            - SIGINT: interupt signal for Ctrl+C | value = 2
            - SIGTERM: system/process-based termination | value = 15
        """
        if platform.system() != "Windows":
            for sig in (signal.SIGINT, signal.SIGTERM):
                self.loop.add_signal_handler(sig, lambda: self.shutdown())

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
                    f"sleeping {self.retry_delay_seconds}s"
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