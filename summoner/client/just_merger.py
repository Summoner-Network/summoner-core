"""
merger.py

This module provides two related utilities built on top of SummonerClient:

1) ClientMerger
   Build a single composite SummonerClient by replaying handlers from multiple sources.

   A "source" can be:
     - an imported SummonerClient instance (live Python object), or
     - a DNA list (already loaded JSON list[dict]), or
     - a DNA JSON file path.

   Imported-client sources:
     - handlers keep their original module globals (module-backed execution),
     - the original client binding (for example the name "agent") is rebound to the merged client,
     - optional rebind_globals are injected into handler globals.

   DNA sources:
     - handlers are reconstructed by compiling their recorded source text into an isolated
       sandbox module (one sandbox per DNA source),
     - the sandbox binds var_name (for example "agent") to the merged client instance,
       so handler code that references `agent` executes against the composite client,
     - optional context (imports, globals, recipes) is applied into the sandbox.

   Usage pattern:
     - instantiate ClientMerger(...)
     - configure flow / styles as usual on the merged client if desired
     - call agent.initiate_all() to replay handlers onto the merged client
     - call agent.run(...)

2) ClientTranslation
   Reconstruct a fresh SummonerClient from a DNA list.

   Translation compiles handler functions from their recorded source into a fresh sandbox module,
   binds var_name (for example "agent") to the translated client, then registers the handlers
   using the normal decorators.

Security and trust model
------------------------
Both classes execute code from DNA via exec() and eval():

- context imports (ctx["imports"])
- recipes (ctx["recipes"])
- handler bodies (entry["source"])

This is intended for trusted DNA (typically produced by your own agents).
Do not run untrusted DNA.
"""
#pylint:disable=line-too-long, wrong-import-position
#pylint:disable=invalid-name, broad-exception-caught,logging-fstring-interpolation

from typing import Optional, Any
from contextlib import suppress
from pathlib import Path
import inspect
import asyncio
import types
import re
import json
import uuid

import os
import sys
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client import SummonerClient
from summoner.protocol.triggers import Action, load_triggers
from summoner.protocol.process import Direction


def _resolve_trigger(TriggerCls, name: str) -> Any:
    """
    Resolve a trigger name into a trigger instance from TriggerCls.

    DNA stores triggers as strings. This helper supports the two common access patterns:
      - Enum-style indexing: TriggerCls["ok"]
      - Attribute access: TriggerCls.ok

    Parameters
    ----------
    TriggerCls:
        Trigger class or enum-like object returned by flow.triggers() or load_triggers().
    name:
        Trigger name as stored in DNA.

    Returns
    -------
    Any
        The resolved trigger value.

    Raises
    ------
    KeyError
        If the trigger cannot be resolved.
    """
    # Enum-style: TriggerCls["ok"]
    try:
        return TriggerCls[name]
    except Exception:
        pass
    # Attribute-style: TriggerCls.ok
    try:
        return getattr(TriggerCls, name)
    except Exception:
        pass
    raise KeyError(f"Unknown trigger '{name}' for {TriggerCls}")


def _resolve_action(ActionCls, name: str):
    """
    Resolve an action name into the corresponding Action entry.

    DNA stores actions as strings. Depending on how a sender was serialized, the name can be:
      - the enum attribute name ("MOVE")
      - a mixed-case name ("Move")
      - the underlying class name (Move.__name__ == "Move")

    Parameters
    ----------
    ActionCls:
        The Action container used by the protocol layer (typically summoner.protocol.triggers.Action).
    name:
        Action name as stored in DNA.

    Returns
    -------
    Any
        The resolved Action entry.

    Raises
    ------
    KeyError
        If the action cannot be resolved.
    """
    # 1) Try direct attribute match: "MOVE"
    if hasattr(ActionCls, name):
        return getattr(ActionCls, name)

    # 2) Try uppercased: "Move" -> "MOVE"
    up = name.upper()
    if hasattr(ActionCls, up):
        return getattr(ActionCls, up)

    # 3) Try matching the underlying class/function name:
    #    Action.MOVE == Move, where Move.__name__ == "Move"
    for v in ActionCls.__dict__.values():
        if getattr(v, "__name__", None) == name:
            return v

    raise KeyError(f"Unknown action '{name}' for {ActionCls}")


class ClientMerger(SummonerClient):
    """
    Merge multiple sources into one client.

    Each input source can be:
      - an imported SummonerClient instance (module-backed execution),
      - a DNA list (list[dict]),
      - a DNA JSON file path.

    Imported-client sources
    -----------------------
    - Handlers keep their original module globals.
    - The original client binding (var_name such as "agent") is rebound to the merged client.
    - Optional rebind_globals are injected into handler globals.
    - Note: rebinding mutates handler globals. This is intentional.

    DNA sources
    -----------
    - Each DNA source gets its own sandbox module (isolated globals dict).
    - var_name is bound to the merged client in that sandbox, so handler code referencing
      `agent` executes against the composite client.
    - Optional context imports/globals/recipes are executed in the sandbox.

    Execution model
    ---------------
    ClientMerger does not automatically register handlers during __init__.
    You must call initiate_all() (or initiate_* individually) before run().

    Safety
    ------
    This class executes trusted code from DNA via exec()/eval(). Do not run untrusted DNA.
    """

    # pylint:disable=too-many-arguments, too-many-positional-arguments
    def __init__(
        self,
        named_clients: list[Any],  # backward compatible: list[dict] or list[SummonerClient] or list[dna_list]
        name: Optional[str] = None,
        rebind_globals: Optional[dict[str, Any]] = None,
        allow_context_imports: bool = True,
        verbose_context_imports: bool = False,
        close_subclients: bool = True,
    ):
        super().__init__(name=name)

        # Globals injected into:
        # - sandbox globals dicts (DNA sources), and
        # - imported handler globals (imported-client sources).
        # This is how "missing" symbols like Trigger or shared objects are supplied.
        self._rebind_globals = dict(rebind_globals or {})

        # Context controls:
        # - allow_context_imports: execute import lines found in DNA context
        # - verbose_context_imports: log successes as well as failures
        self._allow_context_imports = allow_context_imports
        self._verbose_context_imports = verbose_context_imports

        # If True, imported template clients are cleaned up after extraction:
        # cancel/drain registration tasks and close their event loops when possible.
        # This reduces warnings when importing agent scripts as templates.
        self._close_subclients = close_subclients

        # Normalized sources used later by initiate_* replay methods.
        self.sources: list[dict[str, Any]] = []
        self._import_reports: list[dict[str, Any]] = []

        for idx, entry in enumerate(named_clients):
            src = self._normalize_source(entry, idx)
            self.sources.append(src)

        if self._close_subclients:
            self._shutdown_imported_clients()

    # ----------------------------
    # Source normalization
    # ----------------------------

    #pylint:disable=too-many-branches
    def _normalize_source(self, entry: Any, idx: int) -> dict[str, Any]:
        """
        Normalize a user-provided source specification into a canonical dict.

        Accepted inputs
        ---------------
        - SummonerClient instance
        - DNA list (list[dict])
        - dict containing one of: {"client"}, {"dna_list"}, {"dna_path"}

        Normalized output
        -----------------
        Returns a dict with at least:
        - kind: "client" or "dna"
        - var_name: global name used by handler sources to refer to the client ("agent" by default)

        For kind="client":
        - client: the imported SummonerClient instance

        For kind="dna":
        - dna_entries: handler entries (context removed if present)
        - context: optional __context__ entry
        - sandbox_name: unique module name
        - globals: sandbox globals dict where code is compiled and executed
        - import_report: best-effort report for context imports
        """
        # Allow passing a SummonerClient directly
        if isinstance(entry, SummonerClient):
            entry = {"client": entry}

        # Allow passing a dna_list directly
        if isinstance(entry, list):
            entry = {"dna_list": entry}

        if not isinstance(entry, dict):
            raise TypeError(f"Entry #{idx} must be dict | SummonerClient | dna_list, got {type(entry).__name__}")

        if "client" in entry:
            client = entry["client"]
            if not isinstance(client, SummonerClient):
                raise TypeError(f"Entry #{idx} 'client' must be SummonerClient, got {type(client).__name__}")

            # var_name controls which global name will be rebound to the merged client.
            var_name = entry.get("var_name")
            if var_name is None:
                var_name = self._infer_client_var_name(client) or "agent"
            if not isinstance(var_name, str):
                raise TypeError(f"Entry #{idx} 'var_name' must be str, got {type(var_name).__name__}")

            return {
                "kind": "client",
                "client": client,
                "var_name": var_name,
            }

        # DNA sources
        dna_list = None
        if "dna_list" in entry:
            dna_list = entry["dna_list"]
        elif "dna_path" in entry:
            p = Path(entry["dna_path"])
            dna_list = json.loads(p.read_text(encoding="utf-8"))
        else:
            raise KeyError(f"Entry #{idx} must contain 'client' or 'dna_list' or 'dna_path'")

        if not isinstance(dna_list, list):
            raise TypeError(f"Entry #{idx} DNA must be a list, got {type(dna_list).__name__}")

        # Optional context header is stored in DNA as the first entry.
        ctx = None
        entries = dna_list
        if entries and isinstance(entries[0], dict) and entries[0].get("type") == "__context__":
            ctx = entries[0]
            entries = entries[1:]

        # Determine var_name binding:
        # - explicit var_name in entry wins
        # - else use ctx["var_name"]
        # - else default to "agent"
        var_name = entry.get("var_name")
        if var_name is None:
            ctx_var = ctx.get("var_name") if isinstance(ctx, dict) else None
            var_name = ctx_var if isinstance(ctx_var, str) and ctx_var else "agent"
        if not isinstance(var_name, str):
            raise TypeError(f"Entry #{idx} 'var_name' must be str, got {type(var_name).__name__}")

        # Each DNA source gets its own sandbox module (isolated globals dict).
        sandbox_module_name = f"summoner_merge_{uuid.uuid4().hex}"
        sandbox_module = types.ModuleType(sandbox_module_name)
        sys.modules[sandbox_module_name] = sandbox_module
        g = sandbox_module.__dict__

        # Bind the client name used inside handler source to the merged client.
        # This makes `await agent.travel_to(...)` act on the composite client.
        g[var_name] = self

        # Apply context (imports/globals/recipes) into that sandbox.
        report = self._apply_context(ctx, g, label=f"source#{idx}")
        self._import_reports.append(report)

        return {
            "kind": "dna",
            "dna_entries": entries,
            "context": ctx,
            "var_name": var_name,
            "sandbox_name": sandbox_module_name,
            "globals": g,
            "import_report": report,
        }

    def _infer_client_var_name(self, client: SummonerClient) -> Optional[str]:
        """
        Infer the module-global variable name used by handlers to refer to `client`.

        This is used for imported-client sources so that we can rebind that name
        (for example "agent") to the merged client in handler globals.

        Returns
        -------
        Optional[str]
            The inferred binding name, or None if not found.
        """
        # Look for a module-global name whose value is exactly `client`
        # TODO: can use _view_candidates instead of building a list
        candidates = []
        #pylint:disable=protected-access
        if client._upload_states is not None:
            candidates.append(client._upload_states)
        if client._download_states is not None:
            candidates.append(client._download_states)
        for d in client._dna_receivers:
            candidates.append(d.get("fn"))
        for d in client._dna_senders:
            candidates.append(d.get("fn"))
        for d in client._dna_hooks:
            candidates.append(d.get("fn"))

        for fn in candidates:
            if fn is None:
                continue
            g = getattr(fn, "__globals__", None)
            if not isinstance(g, dict):
                continue
            for k, v in g.items():
                if v is client and isinstance(k, str):
                    return k
        return None

    def _shutdown_imported_clients(self) -> None:
        """
        Best-effort cleanup for imported template clients.

        Why this exists
        --------------
        Many agent scripts create a SummonerClient at import-time. That client creates
        an event loop and schedules registration tasks.

        When agent scripts are imported only as templates for merging, those template
        clients should not be left alive, otherwise you often see:
          - "coroutine was never awaited"
          - "Task was destroyed but it is pending"

        Cleanup approach
        ----------------
        For each imported client:
          1) cancel pending registration tasks
          2) if its loop is not running, drive the loop to await cancellations
          3) close the loop
          4) clear the template's registration list
        """
        for src in self.sources:
            if src.get("kind") != "client":
                continue

            client: SummonerClient = src["client"]
            var_name: str = src["var_name"]

            #pylint:disable=protected-access
            tasks = list(client._registration_tasks or [])
            loop = client.loop

            # Nothing to do.
            if not tasks and loop.is_closed():
                continue

            # 1) cancel tasks
            for t in tasks:
                with suppress(Exception):
                    t.cancel()

            # 2) drain tasks on that loop so they are actually awaited
            if loop is not None and not loop.is_closed():
                if loop.is_running():
                    # Can't safely run_until_complete; also shouldn't close a running loop.
                    self.logger.warning(
                        f"[{var_name}] Imported client loop is running; "
                        f"cannot drain/close registration tasks cleanly."
                    )
                else:
                    old_loop = None
                    try:
                        # Set context so asyncio.gather/futures bind to the right loop.
                        with suppress(Exception):
                            old_loop = asyncio.get_event_loop_policy().get_event_loop()
                        asyncio.set_event_loop(loop)

                        # Await cancellation. This is what prevents warnings.
                        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                    except Exception as e:
                        self.logger.warning(f"[{var_name}] Error draining registration tasks: {e}")
                    finally:
                        # Restore previous loop context (or clear it).
                        with suppress(Exception):
                            asyncio.set_event_loop(old_loop)

                    # 3) close loop after drain
                    try:
                        loop.close()
                    except Exception as e:
                        self.logger.warning(f"[{var_name}] Error closing event loop: {e}")

            # 4) clear list
            with suppress(Exception):
                #pylint:disable=protected-access
                client._registration_tasks.clear()

    # ----------------------------
    # Context application (DNA)
    # ----------------------------

    # pylint:disable=too-many-branches
    def _apply_context(self, ctx: Optional[dict], g: dict, *, label: str) -> dict[str, Any]:
        """
        Apply a DNA context entry (imports, globals, recipes) into a sandbox globals dict.

        This is best-effort:
        - import failures are recorded and logged
        - recipe failures are logged but do not abort merge

        Parameters
        ----------
        ctx:
            The optional "__context__" DNA header entry.
        g:
            The sandbox globals dict (module.__dict__) where code will be executed.
        label:
            Used for logging and for the returned report.

        Returns
        -------
        dict[str, Any]
            A structured report with keys: label, succeeded, failed, skipped.

        Security note
        -------------
        This executes code from ctx (imports and recipes). Use only with trusted DNA.
        """
        report = {"label": label, "succeeded": [], "failed": [], "skipped": []}

        if not isinstance(ctx, dict):
            return report

        # imports (executed inside sandbox namespace)
        for line in (ctx.get("imports") or []):
            if not isinstance(line, str) or not line.strip():
                continue

            if not self._allow_context_imports:
                report["skipped"].append(line)
                continue

            try:
                # pylint:disable=exec-used
                exec(line, g)
                report["succeeded"].append(line)
                if self._verbose_context_imports:
                    self.logger.info(f"[merge ctx:{label}] import ok: {line}")
            except Exception as e:
                report["failed"].append((line, f"{type(e).__name__}: {e}"))
                self.logger.warning(f"[merge ctx:{label}] import failed: {line!r} ({type(e).__name__}: {e})")

        # plain globals (already JSON-friendly)
        globs = ctx.get("globals") or {}
        if isinstance(globs, dict):
            for k, v in globs.items():
                if isinstance(k, str):
                    g.setdefault(k, v)

        # recipes (evaluated inside the sandbox namespace)
        recipes = ctx.get("recipes") or {}
        if isinstance(recipes, dict):
            for k, expr in recipes.items():
                if not (isinstance(k, str) and isinstance(expr, str)):
                    continue
                try:
                    # pylint:disable=eval-used
                    g.setdefault(k, eval(expr, g, {}))
                except Exception as e:
                    self.logger.warning(f"[merge ctx:{label}] recipe failed {k}={expr!r} ({type(e).__name__}: {e})")

        return report

    # ----------------------------
    # Utility: source patch for getsource capture
    # ----------------------------

    def _apply_with_source_patch(self, decorator, fn, source: str):
        """
        Apply a SummonerClient decorator while preserving DNA source text.

        Why patch inspect.getsource
        ---------------------------
        The base decorators record handler source using inspect.getsource(fn).
        When functions are constructed from DNA via exec(), inspect.getsource(fn)
        will typically fail because there is no real source file.

        This helper temporarily patches inspect.getsource so the decorator stores
        the original DNA text. This patch is process-global, so it should be used
        only in controlled, single-threaded contexts (typical CLI usage).
        """
        orig = inspect.getsource
        inspect.getsource = lambda o: source
        try:
            decorator(fn)
        finally:
            inspect.getsource = orig

    # ----------------------------
    # Imported-client handler cloning
    # ----------------------------

    def _clone_handler(self, fn: types.FunctionType, original_name: str) -> types.FunctionType:
        """
        Clone a function object for imported-client sources while preserving module globals.

        Behavior
        --------
        - Mutates fn.__globals__ in-place:
            - rebind original_name (for example "agent") to the merged client
            - inject rebind_globals into the same globals dict
        - Creates a new function object using the original code object and closure.

        This preserves the module-backed environment of imported clients. The cost is that
        imported handler globals are modified, which is intentional for merge semantics.
        """
        g = fn.__globals__

        # rebind the client variable name (agent/client/etc)
        try:
            g[original_name] = self
        except Exception as e:
            self.logger.warning(f"Could not bind '{original_name}' to merged client: {e}")

        # rebind any shared globals (viz, Trigger, etc.)
        for k, v in self._rebind_globals.items():
            try:
                g[k] = v
            except Exception as e:
                self.logger.warning(f"Could not bind global '{k}' in '{fn.__name__}': {e}")

        new_fn = types.FunctionType(
            fn.__code__,
            g,
            name=fn.__name__,
            argdefs=fn.__defaults__,
            closure=fn.__closure__,
        )
        new_fn.__annotations__ = getattr(fn, "__annotations__", {})
        new_fn.__doc__ = getattr(fn, "__doc__", None)

        # if your dna() uses a __dna_source__ fallback, keep it
        if hasattr(fn, "__dna_source__"):
            new_fn.__dna_source__ = fn.__dna_source__ # pyright: ignore[reportFunctionMemberAccess]

        return new_fn


    # ----------------------------
    # DNA compilation (per-source sandbox)
    # ----------------------------

    def _make_from_source(self, entry: dict[str, Any], g: dict, sandbox_name: str) -> types.FunctionType:
        """
        Build a function object from a DNA entry by executing its function body in a sandbox.

        Key rule: strip decorators
        --------------------------
        DNA sources typically include decorator lines (for example "@agent.receive(...)").
        We skip all decorators and only exec the "def ..." block. Otherwise, compiling the
        function would also register it immediately, which would duplicate handlers.

        Globals
        -------
        - rebind_globals is injected into g before exec so required runtime symbols
          (for example Trigger, viz, shared objects) are visible to the compiled function.
        - var_name is already bound to the merged client in g during source normalization.
        """
        fn_name = entry["fn_name"]
        raw = entry["source"]
        lines = raw.splitlines()

        # ---------------------------------------------------------------------
        # 1) Find the def line for this function, skipping decorators.
        # ---------------------------------------------------------------------
        def_idx = None
        for idx, line in enumerate(lines):
            pat = rf"\s*(async\s+)?def\s+{re.escape(fn_name)}\b"
            if re.match(pat, line):
                def_idx = idx
                break
        if def_idx is None:
            raise RuntimeError(f"Could not find def for '{fn_name}'")

        func_body = "\n".join(lines[def_idx:])

        # ---------------------------------------------------------------------
        # 2) Ensure rebinding happens in the same globals dict used by exec().
        # ---------------------------------------------------------------------
        rebind = self._rebind_globals
        if isinstance(rebind, dict) and rebind:
            g.update(rebind)

        # ---------------------------------------------------------------------
        # 3) Execute and retrieve the resulting function object.
        # ---------------------------------------------------------------------
        if "__builtins__" not in g:
            g["__builtins__"] = __builtins__

        # pylint:disable=exec-used
        exec(compile(func_body, filename=f"<{sandbox_name}>", mode="exec"), g)

        fn = g.get(fn_name)
        if not isinstance(fn, types.FunctionType):
            raise RuntimeError(f"Failed to construct function '{fn_name}'")

        return fn


    # ----------------------------
    # Public replay API
    # ----------------------------

    def initiate_upload_states(self):
        """Replay @upload_states from every source onto the merged client."""
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
                #pylint:disable=protected-access
                fn = client._upload_states
                if fn is None:
                    continue
                fn_clone = self._clone_handler(fn, var_name)
                try:
                    self.upload_states()(fn_clone)
                except Exception as e:
                    self.logger.warning(f"[{var_name}] Failed to replay upload_states '{fn.__name__}': {e}")

            else:
                g = src["globals"]
                sandbox = src["sandbox_name"]
                for entry in src["dna_entries"]:
                    if entry.get("type") != "upload_states":
                        continue
                    fn = self._make_from_source(entry, g, sandbox)
                    dec = self.upload_states()
                    self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_download_states(self):
        """Replay @download_states from every source onto the merged client."""
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
                #pylint:disable=protected-access
                fn = client._download_states
                if fn is None:
                    continue
                fn_clone = self._clone_handler(fn, var_name)
                try:
                    self.download_states()(fn_clone)
                except Exception as e:
                    self.logger.warning(f"[{var_name}] Failed to replay download_states '{fn.__name__}': {e}")

            else:
                g = src["globals"]
                sandbox = src["sandbox_name"]
                for entry in src["dna_entries"]:
                    if entry.get("type") != "download_states":
                        continue
                    fn = self._make_from_source(entry, g, sandbox)
                    dec = self.download_states()
                    self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_hooks(self):
        """Replay @hook(Direction, priority=...) from every source onto the merged client."""
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
                #pylint:disable=protected-access
                for dna in client._dna_hooks:
                    fn_clone = self._clone_handler(dna["fn"], var_name)
                    try:
                        self.hook(dna["direction"], priority=dna["priority"])(fn_clone)
                    except Exception as e:
                        self.logger.warning(f"[{var_name}] Failed to replay hook '{dna['fn'].__name__}': {e}")

            else:
                g = src["globals"]
                sandbox = src["sandbox_name"]
                for entry in src["dna_entries"]:
                    if entry.get("type") != "hook":
                        continue
                    fn = self._make_from_source(entry, g, sandbox)
                    direction = Direction[entry["direction"]] if isinstance(entry.get("direction"), str) else entry["direction"]
                    dec = self.hook(direction, priority=tuple(entry.get("priority", ())))
                    self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_receivers(self):
        """Replay @receive(route, priority=...) from every source onto the merged client."""
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
                #pylint:disable=protected-access
                for dna in client._dna_receivers:
                    fn_clone = self._clone_handler(dna["fn"], var_name)
                    try:
                        self.receive(dna["route"], priority=dna["priority"])(fn_clone)
                    except Exception as e:
                        self.logger.warning(
                            f"[{var_name}] Failed to replay receiver '{dna['fn'].__name__}' on route '{dna['route']}': {e}"
                        )

            else:
                g = src["globals"]
                sandbox = src["sandbox_name"]
                for entry in src["dna_entries"]:
                    if entry.get("type") != "receive":
                        continue
                    fn = self._make_from_source(entry, g, sandbox)
                    dec = self.receive(entry["route"], priority=tuple(entry.get("priority", ())))
                    self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_senders(self):
        """
        Replay @send(route, multi, on_triggers, on_actions) from every source onto the merged client.

        Imported-client sources:
        - carry actual trigger/action objects in _dna_senders.

        DNA sources:
        - store trigger/action names as strings.
        - triggers are resolved using TriggerCls:
            - prefer Trigger class provided by sandbox context ("Trigger" in sandbox globals)
            - else fall back to load_triggers()
        - actions are resolved from Action by name via _resolve_action.
        """
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
                #pylint:disable=protected-access
                for dna in client._dna_senders:
                    fn_clone = self._clone_handler(dna["fn"], var_name)
                    try:
                        self.send(
                            dna["route"],
                            multi=dna["multi"],
                            on_triggers=dna["on_triggers"],
                            on_actions=dna["on_actions"],
                        )(fn_clone)
                    except Exception as e:
                        self.logger.warning(
                            f"[{var_name}] Failed to replay sender '{dna['fn'].__name__}' on route '{dna['route']}': {e}"
                        )

            else:
                g = src["globals"]
                sandbox = src["sandbox_name"]

                # Triggers: prefer a Trigger class provided by sandbox context; otherwise load defaults.
                TriggerCls = g.get("Trigger")
                if TriggerCls is None:
                    TriggerCls = load_triggers()

                for entry in src["dna_entries"]:
                    if entry.get("type") != "send":
                        continue
                    fn = self._make_from_source(entry, g, sandbox)
                    on_triggers = {_resolve_trigger(TriggerCls, t) for t in entry.get("on_triggers", [])} or None
                    on_actions = {_resolve_action(Action, a) for a in entry.get("on_actions", [])} or None
                    dec = self.send(
                        entry["route"],
                        multi=entry.get("multi", False),
                        on_triggers=on_triggers,
                        on_actions=on_actions,
                    )
                    self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_all(self):
        """
        Replay all supported handler types in a standard order.

        This should be called before run(). The order matches SummonerClient.dna():
          1) upload_states
          2) download_states
          3) hooks
          4) receivers
          5) senders
        """
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()
