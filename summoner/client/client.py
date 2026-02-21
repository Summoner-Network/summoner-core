"""
SummonerClient
"""
#pylint:disable=line-too-long, wrong-import-position, too-many-lines
#pylint:disable=logging-fstring-interpolation, broad-exception-caught

import os
import sys
import json
from typing import (
    Awaitable,
    Dict,
    Generator,
    List,
    Optional,
    Callable,
    Set,
    Tuple,
    Union,
    Coroutine,
    Any,
    cast,
    )
import asyncio
import signal
import inspect
from collections import defaultdict
import platform

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client_types import (
    DOWNLOAD_TYPE,
    HOOK_TYPE,
    RECEIVE_DECORATED_TYPE,
    SEND_DECORATED_TYPE,
    SENDING_HOOKS_TYPE,
    RECEIVING_HOOKS_TYPE,
    UPLOAD_TYPE,
)
from summoner.client.dna import (
    DNA_DOWNLOAD,
    DNA_UPLOAD,
    DNAHook,
    DNAReceiver,
    DNASender
)
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

class ServerDisconnected(Exception):
    """Raised when the server closes the connection."""

#pylint:disable=too-many-instance-attributes
class SummonerClient:
    """
    TODO doc client
    """

    DEFAULT_MAX_BYTES_PER_LINE = 64 * 1024      # 64 KiB
    DEFAULT_READ_TIMEOUT_SECONDS = None         # Wait for messages to arrive
    DEFAULT_CONCURRENCY_LIMIT = 50

    DEFAULT_RETRY_DELAY = 3
    DEFAULT_PRIMARY_RETRY_LIMIT = 3
    DEFAULT_FAILOVER_RETRY_LIMIT = 2

    DEFAULT_EVENT_BRIDGE_SIZE = 1000
    DEFAULT_MAX_CONSECUTIVE_ERRORS = 3          # Failed attempts to send before disconnecting

    core_version = "1.1.1"

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
        self.active_tasks: set[asyncio.Task[Any]] = set()
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
        self._registration_tasks: list[asyncio.Task[Any]] = []

        # One-time indexing of parsed routes
        self.receiver_parsed_routes: dict[str, ParsedRoute] = {}
        self.sender_parsed_routes: dict[str, ParsedRoute] = {}

        # Flow representing the underlying finite state machine
        self._flow = Flow()

        # Functions to read and write the flow's active states in memory
        self._upload_states: Optional[UPLOAD_TYPE] = None
        self._download_states: Optional[DOWNLOAD_TYPE] = None

        # Sender HyperParameters
        self.event_bridge_maxsize : Optional[int] = None
        self.max_concurrent_workers : Optional[int] = None # Limit the sending rate (will use 50 if None is given)
        self.send_queue_maxsize : Optional[int] = None
        self.batch_drain: Optional[bool] = None
        # self.max_consecutive_worker_errors is unbound until _apply_config

        # Receiver HyperParameters
        self.max_bytes_per_line : Optional[int] = None
        self.read_timeout_seconds : Optional[float] = None # None is prefered

        # Reconnction HyperParameters
        self.retry_delay_seconds : Optional[float] = None
        # self.primary_retry_limit is unbound until _apply_config
        # self.default_host is unbound until _apply_config
        # self.default_port is unbound until _apply_config
        # self.default_retry_limit is unbound until _apply_config

        # Pass Event information from the receiving end to the sending end
        self.event_bridge: Optional[asyncio.Queue[tuple[tuple[int, ...], Optional[str], ParsedRoute, Optional[Event]]]] = None

        self.send_queue: Optional[asyncio.Queue[Optional[Tuple[str,Sender]]]] = None
        self.send_workers_started = False  # To avoid double-starting workers
        self.worker_tasks: list[asyncio.Task] = []
        self.writer_lock = asyncio.Lock()

        # Store validation hooks to be used before sending and after receiving
        self.sending_hooks: dict[tuple[int,...], SENDING_HOOKS_TYPE] = {}
        self.receiving_hooks: dict[tuple[int,...], RECEIVING_HOOKS_TYPE] = {}
        self.hooks_lock = asyncio.Lock()

        # ─── DNA capture for merging ─────────────────────────────────────────
        # lists of dicts, each entry records one decorated handler
        self._dna_receivers: list[DNAReceiver] = []
        self._dna_senders:   list[DNASender] = []
        self._dna_hooks:     list[DNAHook] = []

        self._dna_upload_states: Optional[DNA_UPLOAD] = None
        self._dna_download_states: Optional[DNA_DOWNLOAD] = None

    # ==== VERSION SPECIFIC ====

    #pylint:disable=attribute-defined-outside-init
    def _apply_config(self, config: dict[str,Union[str,dict[str,Union[str,dict]]]]):
        """
        Given the config which says specific hyperparameters, set the corresponding
        fields here. Making sure they meet the expected constraints.
        If some are not provided in config, they may be given default values.
        """
        self.host                   = config.get("host") # default is None # pyright: ignore[reportAttributeAccessIssue]
        self.port                   = config.get("port") # default is None # pyright: ignore[reportAttributeAccessIssue]

        logger_cfg                  = cast(Dict[str,Any],config.get("logger", {}))
        configure_logger(self.logger, logger_cfg)

        hp_config                   = cast(Dict[str,Any],config.get("hyper_parameters", {}))

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
        """
        Get the regex patterns for flow ready
        """
        self._flow.compile_arrow_patterns()

    def flow(self) -> Flow:
        """
        TODO: doc flow
        """
        return self._flow

    async def travel_to(self, host, port):
        """
        Change the host and port.
        So there is now pending travel.
        """
        async with self.connection_lock:
            self.host = host
            self.port = port
            self._travel = True

    async def quit(self):
        """
        Now a pending quit
        """
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
        def decorator(fn: UPLOAD_TYPE):

            # ----[ Safety Checks ]----

            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@upload_states handler '{fn.__name__}' must be async")

            _check_param_and_return(
                fn,
                decorator_name="@upload_states",
                allow_param=(type(None), str, dict, Any),   # the payload # pyright: ignore[reportArgumentType]
                allow_return=(type(None), str, Any, Node, list, dict, # pyright: ignore[reportArgumentType]
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
        def decorator(fn: DOWNLOAD_TYPE):

            # ----[ Safety Checks ]----

            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@download_states handler '{fn.__name__}' must be async")

            _check_param_and_return(
                fn,
                decorator_name="@download_states",
                allow_param=(type(None), Node, Any, list, dict,  # pyright: ignore[reportArgumentType]
                                list[Node],
                                dict[str, Node],
                                dict[str, list[Node]],
                                dict[str, Union[Node, list[Node]]],
                                dict[Optional[str], Node],
                                dict[Optional[str], list[Node]],
                                dict[Optional[str], Union[Node, list[Node]]],
                                ),
                allow_return=(type(None), Any), # pyright: ignore[reportArgumentType]
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

    def _schedule_registration(self, register_coro: Coroutine[Any,Any,None]):
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
        """
        TODO: doc hook
        """
        def decorator(fn : HOOK_TYPE):
            """
            TODO: doc decorator
            """

            # ----[ Safety Checks ]----

            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@hook handler '{fn.__name__}' must be async")


            _check_param_and_return(
                fn,
                decorator_name="@hook",
                allow_param=(Any, str, dict), # pyright: ignore[reportArgumentType]
                allow_return=(type(None), str, dict, Any), # pyright: ignore[reportArgumentType]
                logger=self.logger,
            )

            if not isinstance(direction, Direction):
                raise TypeError("Direction for hook must be either Direction.SEND or Direction.RECEIVE")

            if isinstance(priority, int):
                tuple_priority = (priority,)
            elif isinstance(priority, tuple) and all(isinstance(p, int) for p in priority):
                tuple_priority = priority
            else:
                raise ValueError(f"Priority must be an integer or a tuple of integers (got type {type(priority).__name__}: {priority!r})")

            # ----[ DNA capture ]----
            self._dna_hooks.append(DNAHook(
                fn = fn,
                direction = direction,
                priority = tuple_priority,
                source = inspect.getsource(fn),
            ))

            async def register():
                """
                ----[ Registration Code ]----
                TODO doc register
                """
                async with self.hooks_lock:
                    if direction == Direction.RECEIVE:
                        self.receiving_hooks[tuple_priority] = cast(RECEIVING_HOOKS_TYPE, fn)
                    elif direction == Direction.SEND:
                        self.sending_hooks[tuple_priority] = cast(SENDING_HOOKS_TYPE, fn)

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
        """
        TODO: doc receive
        """
        route = route.strip()
        def decorator(fn: RECEIVE_DECORATED_TYPE):

            # ----[ Safety Checks ]----

            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"@receive handler '{fn.__name__}' must be async")

            sig = inspect.signature(fn)
            if len(sig.parameters) != 1:
                raise TypeError(f"@receive '{fn.__name__}' must accept exactly one argument (payload)")

            _check_param_and_return(
                fn,
                decorator_name="@receive",
                allow_param=(Any, str, dict), # pyright: ignore[reportArgumentType]
                allow_return=(type(None), Event, Any), # pyright: ignore[reportArgumentType]
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
            self._dna_receivers.append(DNAReceiver(
                fn = fn,
                route = route,
                priority = tuple_priority,
                source = inspect.getsource(fn),  # for text serialization
            ))

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

    def send(
            self,
            route: str,
            multi: bool = False,
            on_triggers: Optional[set[Any]] = None,
            on_actions: Optional[set[Any]] = None,
        ):
        """
        TODO: doc send
        """
        route = route.strip()
        def decorator(fn: SEND_DECORATED_TYPE):

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
                    allow_return=(type(None), Any, str, dict), # pyright: ignore[reportArgumentType]
                    logger=self.logger,
                )
            else:
                _check_param_and_return(
                    fn,
                    decorator_name="@send[multi=True]",
                    allow_param=(),   # no args allowed
                    allow_return=(Any, list, list[str], list[dict], list[Union[str, dict]]), # pyright: ignore[reportArgumentType]
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
            self._dna_senders.append(DNASender(
                fn = fn,
                route = route,
                multi = multi,
                on_triggers = on_triggers,
                on_actions = on_actions,
                source = inspect.getsource(fn),
            ))

            # ----[ Registration Code ]----
            async def register():

                sender = Sender(fn=fn, multi=multi, actions=on_actions, triggers=on_triggers)
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
                    else:
                        self.sender_index.setdefault(route, [])
                        self.sender_index[route].append(sender)

            # ----[ Safe Registration ]----
            # NOTE: register() is run ASAP and _registration_tasks is used to wait all registrations before run_client()
            self._schedule_registration(register())

            return fn

        return decorator

    # ==== DNA PROCESSING ====

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

    #pylint:disable=too-many-locals, too-many-branches, too-many-statements
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
        # TODO: use *_entry_contribution methods to build entries and keep this method cleaner
        
        #pylint:disable=import-outside-toplevel
        import builtins
        
        # TODO: Only used below, so could have not turned into a list right away
        # Keep only as a generator being iterated over
        handler_fns = list(self._iter_registered_handler_functions())

        # ----------------------------
        # Handler DNA entries
        # ----------------------------
        entries: List[Dict[str, Any]] = []

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

            entries.append({
                "type": "send",
                "route": raw_route,          # original route string
                "route_key": route_key,     # stable route representative
                "multi": dna["multi"],
                # Serialize triggers/actions by name so they can be re-resolved later.
                "on_triggers": [t.name for t in (dna["on_triggers"] or [])],
                "on_actions": [a.__name__ for a in (dna["on_actions"] or [])],
                "source": get_callable_source(fn, dna["source"]),
                "module": fn.__module__,
                "fn_name": fn.__name__,
            })

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
        # Collect handler functions once so we can reuse them for context analysis.

        for fn in handler_fns:
            g = getattr(fn, "__globals__", None)
            if not isinstance(g, dict):
                continue

            # Names referenced by the function body.
            if hasattr(fn, "__code__"):
                names_to_scan = set(fn.__code__.co_names)
            else:
                names_to_scan: Set[str] = set()

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
                    #pylint:disable=consider-using-f-string
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

        context_entry : Dict[str, Any] = {
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


    # pylint:disable=too-many-locals, too-many-branches, too-many-statements
    async def message_receiver_loop(
            self,
            reader: asyncio.StreamReader,
            stop_event: asyncio.Event
        ):
        """
        TODO: doc message receiver
        """

        # ----[ Wrapper: Interpret Protocol-Only Errors as None ]----
        async def _safe_call(fn: Callable[[Any],Awaitable[Any]], payload: Any) -> Optional[Any]:
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
                        limit=self.max_bytes_per_line, # max_bytes_per_line is set to an integer by now # pyright: ignore[reportArgumentType]
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
                        limit=self.max_bytes_per_line,  # max_bytes_per_line is set to an integer by now # pyright: ignore[reportArgumentType]
                        timeout=self.read_timeout_seconds,
                        )
                    # data = await reader.readline()
                    # if not data:
                    #     raise ServerDisconnected("Server closed the connection.")

                    pre_payload: RelayedMessage = recover_with_types(data.decode())
                    payload = cast(Union[RelayedMessage, None], pre_payload)

                    # ----[ Build: Validation ]----
                    async with self.hooks_lock:
                        receiving_hooks = self.receiving_hooks.copy()

                    for priority, receiving_hook in sorted(receiving_hooks.items(), key=lambda kv: hook_priority_order(kv[0])):
                        try:
                            new_payload = await receiving_hook(payload)

                            if new_payload is None:
                                payload = None
                                break
                            payload = new_payload

                        except Exception as e:
                            self.logger.error(
                                f"Receiving hook {receiving_hook.__name__} (priority={priority}) "
                                f"failed on payload {payload!r}: {e}",
                                exc_info=True
                            )

                    # if *any* hook returned None, skip the rest of processing
                    if payload is None:
                        continue

                    # ----[ Build: Organize Batches by Priority ]----
                    batches: dict[tuple[int, ...], list[Callable[[Any], Coroutine[Any,Any,Any]]]] = {}
                    if self._flow.in_use:
                        raw_states = (await self._upload_states(payload)) if self._upload_states is not None else None
                        tape = StateTape(raw_states)
                        activation_index = tape.collect_activations(
                            receiver_index=receiver_index, parsed_routes=receiver_parsed_routes) # pyright: ignore[reportPossiblyUnboundVariable]
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
                        event_buffer: dict[tuple[int, ...], list[tuple[Optional[str], ParsedRoute, Optional[Event]]]]  = defaultdict(list)

                    # ----[ Exec: Run Batches in Order ]----
                    for priority, batch_fns in sorted(batches.items(), key=lambda kv: kv[0]):

                        # ----[ Before: Run Batch ]----
                        # label = "default priority" if priority == () else f"priority {priority}"
                        # self.logger.info(f"Running batch at {label}, {len(batch_fns)} receivers")

                        tasks = [_safe_call(fn, payload) for fn in batch_fns]
                        events: list[Optional[Event]] = await asyncio.gather(*tasks)

                        # ----[ After: Handle Returns ]----
                        if self._flow.in_use:
                            activations = activation_index[priority] # pyright: ignore[reportPossiblyUnboundVariable]

                            local_tape = tape.refresh() # pyright: ignore[reportPossiblyUnboundVariable]
                            to_extend: dict[Optional[str], list[Node]] = defaultdict(list)
                            for act, event in zip(activations, events):
                                to_extend[act.key].extend(act.route.activated_nodes(event))
                            local_tape.extend(to_extend)

                            buffer_entries = [(act.key, act.route, event) for act, event in zip(activations, events)]
                            # event buffer is bound because this is within if self._flow_in_use
                            # some of the event's in buffer_entries could have been None
                            event_buffer[priority].extend(buffer_entries) # pyright: ignore[reportPossiblyUnboundVariable]

                            if self._download_states is not None:
                                await self._download_states(local_tape.revert())

                    # ----[ Final: Pass Data Over To Senders ]----
                    if self._flow.in_use:
                        # event buffer is bound because this is within if self._flow_in_use
                        for priority, event_list in sorted(event_buffer.items(), key=lambda kv: kv[0]): # pyright: ignore[reportPossiblyUnboundVariable]
                            for event_data in event_list:
                                # this will block if the bridge is full, slowing down readers
                                to_put = (priority,) + event_data
                                await self.event_bridge.put(to_put) # pyright: ignore[reportOptionalMemberAccess]

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
            if self.max_concurrent_workers is None:
                raise ValueError("_apply_config will make sure that the maximum number of workers is set to an integer ≥ 1")
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

            if self.send_queue is None:
                # pylint:disable=line-too-long
                raise ValueError("send_queue is not initialized; this should not happen because this is protected method and called within handle_session")

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
                            payload = new_payload

                        except Exception as e:
                            self.logger.error(
                                f"[route={route}] Sending hook {sending_hook.__name__} (priority={priority}) "
                                f"failed on payload {payload!r}: {e}",
                                exc_info=True
                            )

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
        """
        Cancel all worker taks
        Gather raising any exceptions caused in the worker_tasks
        Clear them
        Set back to having not started send workers 
        """
        for w in self.worker_tasks:
            w.cancel()
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks.clear()
        self.send_workers_started = False

    #pylint:disable=too-many-nested-blocks, no-else-continue, no-else-break, attribute-defined-outside-init
    async def message_sender_loop(
            self,
            writer: asyncio.StreamWriter,
            stop_event: asyncio.Event
        ):
        """
        TODO: doc message_sender
        """

        # ----[ Helper: Matches Routes Between Senders and Receivers to Trigger Send ]----
        def _route_accepts(
                sender_pr: ParsedRoute,
                receiver_pr: ParsedRoute
            ) -> bool:
            """
            TODO: doc route_accepts
            """
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

                    pending: list[tuple[tuple[int, ...], Optional[str], ParsedRoute, Optional[Event]]] = []
                    try:
                        while True:
                            # event_bridge exists, handle_session did so
                            pending.append(self.event_bridge.get_nowait()) # pyright: ignore[reportOptionalMemberAccess]
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

                            # self._flow_in_use so sender_parsed_routes is bound
                            sender_parsed_routes = cast(Dict[str,ParsedRoute],sender_parsed_routes) # pyright: ignore[reportPossiblyUnboundVariable]
                            sender_parsed_route = sender_parsed_routes.get(route)
                            if sender_parsed_route is None:
                                continue

                            # Iterate pending in queue order; first match "wins" for this (route,key,fn_name)
                            # _flow_in_use so pending is bound
                            for (_priority, key, parsed_route, event) in pending: # pyright: ignore[reportPossiblyUnboundVariable]
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
                    # send_queue exists, handle_session did so
                    queue_size = self.send_queue.qsize() # pyright: ignore[reportOptionalMemberAccess]
                    expected_queue_size = queue_size + len(senders)
                    # self.send_queue_maxsize is set to an integer by now
                    if expected_queue_size > self.send_queue_maxsize * 0.8: # pyright: ignore[reportOptionalOperand] # 80% full
                        self.logger.warning(f"Queue is about to exceed 80% its capacity; Attempted load size: {expected_queue_size} out of {self.send_queue_maxsize}")

                # ----[ Enqueue Sender Batch | Senders Are Run in Background ]----
                try:
                    for sender in senders:
                        # self.send_queue exists, handle_session did so
                        await self.send_queue.put(sender)  # pyright: ignore[reportOptionalMemberAccess] # Will block if full (i.e., back-pressure)
                except asyncio.CancelledError:
                    self.logger.info("Sender enqueue loop cancelled mid-batch.")
                    raise

                # ----[ Wait for Sender Batch to Finish]----
                # self.send_queue exists, handle_session did so
                await self.send_queue.join() # pyright: ignore[reportOptionalMemberAccess]

                if self.batch_drain:
                    async with self.writer_lock:
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
                if self.max_concurrent_workers is None:
                    raise ValueError("_apply_config will make sure that the maximum number of workers is set to an integer ≥ 1")
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
            # The maxsize's are set to integers by now by _apply_config, so these queues will not raise TypeError for maxsize=None
            self.send_queue = asyncio.Queue(maxsize=self.send_queue_maxsize) # pyright: ignore[reportArgumentType]
            self.event_bridge = asyncio.Queue(maxsize = self.event_bridge_maxsize) # pyright: ignore[reportArgumentType]

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
                    # current_task assumed not None
                    # but we are within the try block
                    self.active_tasks.add(current_task) # pyright: ignore[reportArgumentType]

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
                        raise ServerDisconnected(e) #pylint:disable=raise-missing-from
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
                    try:
                        if task is not None: # pyright: ignore[reportPossiblyUnboundVariable]
                            self.active_tasks.discard(task) # pyright: ignore[reportPossiblyUnboundVariable]
                    except NameError:
                        pass


            # Check whether we should quit or loop back to travel to the next server (agent migration)
            async with self.connection_lock:
                if not self._travel or self._quit:
                    break

    # ==== CLIENT LIFE CYCLE ====

    def shutdown(self):
        """
        Cancel all the tasks for this event loop
        """
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
                #pylint:disable=unnecessary-lambda
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
                sleep_time = self.retry_delay_seconds or self.DEFAULT_RETRY_DELAY
                self.logger.error(
                    f"[{type(e).__name__}: {e}] "
                    f"({stage}) retry {attempts} of "
                    f"{limit if limit is not None else '∞'}; "
                    f"sleeping {sleep_time}s",
                )
                await asyncio.sleep(sleep_time)

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
        """
        use the default host and port
        """
        async with self.connection_lock:
            self.host = self.default_host
            self.port = self.default_port

    async def run_client(self, host: str = '127.0.0.1', port: int = 8888):
        """
        TODO: doc run_client
        """
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
        """
        TODO: doc run
        """
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

    def _view_candidates(self) -> Generator[Optional[Callable[..., Coroutine[Any,Any,Any] | Awaitable[Any]]],None,None]:
        if self._upload_states is not None:
            yield self._upload_states
        if self._download_states is not None:
            yield self._download_states
        for d in self._dna_receivers:
            yield d.get("fn")
        for d in self._dna_senders:
            yield d.get("fn")
        for d in self._dna_hooks:
            yield d.get("fn")
