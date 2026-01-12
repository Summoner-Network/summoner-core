"""
merger.py

This module provides two related utilities:

1) ClientMerger
   Build a single composite SummonerClient by replaying handlers from multiple sources.

   A "source" can be:
     - an imported SummonerClient instance (live Python objects), or
     - a DNA list (already loaded JSON), or
     - a DNA JSON file path.

   Imported-client sources preserve their original function globals (module-backed),
   but the client binding (for example the name "agent") is rebound to the merged client.

   DNA sources are reconstructed into an isolated sandbox module per source, then
   registered onto the merged client via normal decorators.

2) ClientTranslation
   Reconstruct a fresh SummonerClient from a DNA list, by compiling handler functions
   from their recorded sources into a sandbox module and then registering them.

Trust model:
- Both classes execute code from DNA using exec() and eval() (context imports, recipes,
  and handler bodies). This is intended for trusted DNA (your own agents).
"""

from importlib import import_module
from typing import Optional, Any
from contextlib import suppress
from pathlib import Path
import inspect
import asyncio
import types
import re
import json
import uuid

import os, sys
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client import SummonerClient
from summoner.protocol.triggers import Signal, Action, load_triggers
from summoner.protocol.process import Direction


def _resolve_trigger(TriggerCls, name: str):
    """
    Resolve a trigger name (string) into a trigger instance from TriggerCls.

    Supported formats:
    - Enum-style indexing: TriggerCls["ok"]
    - Attribute access: TriggerCls.ok

    Raises KeyError if the trigger cannot be resolved.
    """
    # Enum-style: SignalCls["ok"]
    try:
        return TriggerCls[name]
    except Exception:
        pass
    # Attribute-style: SignalCls.ok
    try:
        return getattr(TriggerCls, name)
    except Exception:
        pass
    raise KeyError(f"Unknown trigger '{name}' for {TriggerCls}")


def _resolve_action(ActionCls, name: str):
    """
    Resolve an action name (string) into the corresponding Action entry.

    The DNA representation stores action names as strings. Depending on how actions
    are serialized, the name can be:
    - the enum attribute name ("MOVE"),
    - a mixed-case name ("Move"),
    - the underlying class name (Move.__name__ == "Move").

    Raises KeyError if the action cannot be resolved.
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

    Each source can be:
      - an imported SummonerClient (module-backed)
      - a DNA list (loaded from JSON)
      - a DNA file path (JSON)

    Imported-client sources:
      - handlers keep their original module globals
      - the original client binding (var_name such as "agent") is rebound to the merged client
      - optional rebind_globals are injected into handler globals

    DNA sources:
      - handlers are constructed from recorded source code inside a per-source sandbox module
      - var_name is bound to the merged client inside that sandbox, so handler code that references
        "agent" executes against the merged client
      - optional context (imports/globals/recipes) is applied into the sandbox

    Important:
    - This file uses exec()/eval() to apply DNA context and compile functions.
      It assumes DNA is trusted.
    """

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

        # Globals that should be injected into compiled handler namespaces (DNA) and
        # into imported handler globals (imported clients).
        self._rebind_globals = dict(rebind_globals or {})

        # Context controls:
        # - allow_context_imports: execute import lines found in DNA context
        # - verbose_context_imports: log successes as well as failures
        self._allow_context_imports = allow_context_imports
        self._verbose_context_imports = verbose_context_imports

        # If True, imported template clients are "cleaned up" after extraction:
        # registration tasks are cancelled/drained and their event loops are closed.
        # This reduces warnings when importing agent scripts purely as templates.
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

    def _normalize_source(self, entry: Any, idx: int) -> dict[str, Any]:
        """
        Normalize user-provided source specifications into a canonical dict.

        Accepted inputs:
        - SummonerClient instance
        - DNA list (list[dict])
        - dict that contains one of: {"client"}, {"dna_list"}, {"dna_path"}

        Returns a dict with:
        - kind: "client" or "dna"
        - var_name: the name that handler source uses to refer to the client ("agent" by default)
        - client or dna_entries + sandbox globals, depending on kind
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

        This is important for imported-client sources, because handler code might
        reference `agent` (or another name) directly.
        """
        # Look for a module-global name whose value is exactly `client`
        candidates = []
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

    def _cancel_and_drain_loop(self, loop: asyncio.AbstractEventLoop, tasks: list[asyncio.Task], *, label: str) -> None:
        """
        Best-effort helper to cancel tasks and, if possible, run the loop to process cancellations.

        Note:
        - If the loop is running, we cannot safely run_until_complete.
        - This method is conservative and never fails the merge.
        """
        if not loop or loop.is_closed() or not tasks:
            return

        # If someone is actually running that loop, we cannot run_until_complete safely.
        if loop.is_running():
            for t in tasks:
                try:
                    t.cancel()
                except Exception:
                    pass
            return

        # Cancel then give the loop a chance to process cancellations.
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass

        prev_loop = None
        try:
            # Some coroutine code may use get_event_loop(); set context.
            try:
                prev_loop = asyncio.get_event_loop_policy().get_event_loop()
            except Exception:
                prev_loop = None

            asyncio.set_event_loop(loop)
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        except Exception as e:
            # Do not fail merge on template shutdown; just log.
            self.logger.debug(f"[merge] drain failed for {label}: {e}")
        finally:
            try:
                asyncio.set_event_loop(prev_loop)
            except Exception:
                pass

    def _shutdown_imported_clients(self) -> None:
        """
        Imported agent scripts often create a SummonerClient at import-time, which creates an event loop
        and schedules registration tasks.

        When those scripts are imported only as templates for merging, the template clients should not
        be left alive (otherwise you tend to get "coroutine was never awaited" warnings).

        This method cancels and drains pending registration tasks on the template client's loop,
        then closes that loop if it is not running.
        """
        for src in self.sources:
            if src.get("kind") != "client":
                continue

            client: SummonerClient = src["client"]
            var_name: str = src["var_name"]

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

                        # Await cancellation. This is what prevents the warnings.
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
                client._registration_tasks.clear()

    # ----------------------------
    # Context application (DNA)
    # ----------------------------

    def _apply_context(self, ctx: Optional[dict], g: dict, *, label: str) -> dict[str, Any]:
        """
        Apply a DNA context entry (imports, globals, recipes) into a sandbox globals dict.

        This is best-effort:
        - import failures are recorded and logged
        - recipe failures are logged but do not abort merge

        Security note:
        - This executes untrusted code if ctx is untrusted.
        """
        report = {"label": label, "succeeded": [], "failed": [], "skipped": []}

        if not isinstance(ctx, dict):
            return report

        # imports (safe-ish for trusted DNA)
        for line in (ctx.get("imports") or []):
            if not isinstance(line, str) or not line.strip():
                continue

            if not self._allow_context_imports:
                report["skipped"].append(line)
                continue

            try:
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
                    g.setdefault(k, eval(expr, g, {}))
                except Exception as e:
                    self.logger.warning(f"[merge ctx:{label}] recipe failed {k}={expr!r} ({type(e).__name__}: {e})")

        return report

    # ----------------------------
    # Utility: source patch for getsource capture
    # ----------------------------

    def _apply_with_source_patch(self, decorator, fn, source: str):
        """
        The base decorators record handler source using inspect.getsource(fn).
        When functions are constructed from DNA via exec(), inspect.getsource no longer works.

        This helper temporarily patches inspect.getsource so the decorator can store the DNA text.
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
        Clone a function object while keeping its original globals dict.

        Behavior:
        - Mutates fn.__globals__ in-place to rebind original_name (for example "agent") to the merged client.
        - Injects rebind_globals into the same globals dict.
        - Creates a new function object using the original code object and closure.

        This approach preserves the module-backed environment of imported clients,
        but it does mean imported handler globals are modified.
        """
        g = fn.__globals__

        # rebind the client variable name (agent/client/etc)
        try:
            g[original_name] = self
        except Exception as e:
            self.logger.warning(f"Could not bind '{original_name}' to merged client: {e}")

        # rebind any shared globals (viz, etc.)
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
            new_fn.__dna_source__ = fn.__dna_source__

        return new_fn


    # ----------------------------
    # DNA compilation (per-source sandbox)
    # ----------------------------

    def _make_from_source(self, entry: dict[str, Any], g: dict, sandbox_name: str) -> types.FunctionType:
        """
        Build a function object from a DNA entry by executing the function body inside a sandbox globals dict.

        Important:
        - DNA sources typically include decorator lines (for example "@agent.receive(...)").
          We skip all decorators and only exec the "def ..." block, otherwise replay would accidentally
          register handlers while compiling.

        - rebind_globals is injected into g before exec so any required runtime objects
          (for example viz, Trigger, OBJECTS) are visible to the compiled function.
        """
        fn_name = entry["fn_name"]
        raw = entry["source"]
        lines = raw.splitlines()

        # ---------------------------------------------------------------------
        # 1) Find the def line for this function, but SKIP decorators.
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
        # 2) Ensure rebinding happens in the SAME globals dict used by exec().
        # ---------------------------------------------------------------------
        rebind = self._rebind_globals
        if isinstance(rebind, dict) and rebind:
            g.update(rebind)

        # ---------------------------------------------------------------------
        # 3) Execute and retrieve the resulting function object.
        # ---------------------------------------------------------------------
        if "__builtins__" not in g:
            g["__builtins__"] = __builtins__

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

        Imported-client sources carry actual trigger/action objects.
        DNA sources carry strings, which must be resolved using TriggerCls and Action.
        """
        for src in self.sources:
            if src["kind"] == "client":
                client: SummonerClient = src["client"]
                var_name: str = src["var_name"]
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
        This should be called before run().
        """
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()


class ClientTranslation(SummonerClient):
    """
    Reconstruct a SummonerClient from its DNA list.

    Handlers are re-created from their recorded source into a fresh sandbox module
    (with optional context imports) and then registered onto this client.

    This means translated handlers do NOT run inside the original agent modules.
    They instead run in the translation sandbox, with only the explicitly imported
    or rebound globals available (for example: viz, _content, Trigger/Event, etc.).

    In particular, runtime/environmental dependencies are resolved from the
    translated agent’s environment (for example load_triggers() reads TRIGGERS
    next to the translated agent entrypoint), not from the environment of the
    agent that originally produced the DNA.
    """
    def __init__(
        self,
        dna_list: list[dict[str, Any]],
        name: Optional[str] = None,
        var_name: Optional[str] = None,
        rebind_globals: Optional[dict[str, Any]] = None,
        allow_context_imports: bool = True,
        verbose_context_imports: bool = False
    ):
        super().__init__(name=name)

        if not isinstance(dna_list, list):
            raise TypeError("dna_list must be a list of DNA entries")
        
        self._rebind_globals = dict(rebind_globals or {})
        self._allow_context_imports = allow_context_imports
        self._verbose_context_imports = verbose_context_imports

        # Extract optional context entry
        ctx = None
        if dna_list and isinstance(dna_list[0], dict) and dna_list[0].get("type") == "__context__":
            ctx = dna_list[0]
            dna_list = dna_list[1:]

        # Decide binding name:
        # - explicit var_name wins (admin override)
        # - else use context var_name if present
        # - else default to "agent"
        if var_name is None:
            ctx_var = ctx.get("var_name") if isinstance(ctx, dict) else None
            var_name = ctx_var if isinstance(ctx_var, str) and ctx_var else "agent"

        self._dna_list = dna_list
        if not isinstance(var_name, str):
            raise TypeError("var_name must be a string")
        self._var_name = var_name
        self._context = ctx

        # Create a sandbox module for translated code (independent from user imports)
        self._sandbox_module_name = f"summoner_translation_{uuid.uuid4().hex}"
        self._sandbox_module = types.ModuleType(self._sandbox_module_name)
        sys.modules[self._sandbox_module_name] = self._sandbox_module
        self._sandbox_globals = self._sandbox_module.__dict__

        # Bind the client name used in handler source
        self._sandbox_globals[self._var_name] = self

        # Apply context if present
        self._apply_context()

        self._cleanup_template_clients_from_modules()

    def _apply_context(self):
        """
        Apply optional __context__ entry into the translation sandbox.

        This is best-effort and intended for trusted DNA.
        """
        if not isinstance(self._context, dict):
            return

        g = self._sandbox_globals

        # Imports
        for line in self._context.get("imports", []) or []:
            if not isinstance(line, str) or not line.strip():
                continue
            if not self._allow_context_imports:
                continue
            try:
                exec(line, g)
                if self._verbose_context_imports:
                    self.logger.info(f"[translation ctx] import ok: {line}")
            except Exception as e:
                self.logger.warning(f"[translation ctx] import failed: {line!r} ({type(e).__name__}: {e})")

        # Plain globals
        globs = self._context.get("globals", {}) or {}
        if isinstance(globs, dict):
            for k, v in globs.items():
                if isinstance(k, str):
                    g.setdefault(k, v)

        # Recipes
        rec = self._context.get("recipes", {}) or {}
        if isinstance(rec, dict):
            for k, expr in rec.items():
                if not (isinstance(k, str) and isinstance(expr, str)):
                    continue
                # eval in the sandbox global namespace
                try:
                    g.setdefault(k, eval(expr, g, {}))
                except Exception as e:
                    self.logger.warning(f"Could not eval recipe for {k}: {expr!r} ({e})")

    def _cleanup_one_template_client(self, client: SummonerClient, label: str):
        """
        Best-effort: cancel and await pending registration tasks, then close the client's loop.

        This is relevant when a DNA entry references a module that, if imported,
        constructs a template SummonerClient at module import-time.

        We do not want those template clients to remain alive when using translation.
        """
        regs = list(client._registration_tasks or [])
        loop = client.loop

        # cancel registrations
        for t in regs:
            try:
                t.cancel()
            except Exception:
                pass

        # drain cancellations if we can drive that loop
        try:
            if regs and loop is not None and (not loop.is_running()) and (not loop.is_closed()):
                loop.run_until_complete(asyncio.gather(*regs, return_exceptions=True))
        except Exception:
            # best-effort only
            pass

        # clear list
        try:
            client._registration_tasks.clear()
        except Exception:
            pass

        # close the loop (template clients are not meant to run)
        try:
            if loop is not None and (not loop.is_running()) and (not loop.is_closed()):
                loop.close()
        except Exception:
            pass

    def _cleanup_template_clients_from_modules(self):
        """
        For every module referenced in the DNA, if it currently has `var_name`
        bound to a different SummonerClient instance (the imported template),
        clean it up (cancel/drain registrations + close loop).
        """
        modules = {entry.get("module") for entry in self._dna_list if isinstance(entry, dict)}
        modules.discard(None)

        seen_ids: set[int] = set()

        for module_name in modules:
            try:
                module = sys.modules.get(module_name) or import_module(module_name)
            except Exception:
                continue

            g = getattr(module, "__dict__", {})
            template = g.get(self._var_name)

            if isinstance(template, SummonerClient) and template is not self:
                if id(template) in seen_ids:
                    continue
                seen_ids.add(id(template))
                self._cleanup_one_template_client(template, label=f"{module_name}.{self._var_name}")

    def _make_from_source(self, entry: dict[str, Any]) -> types.FunctionType:
        """
        Compile a handler function from its DNA 'source' into the translation sandbox globals.

        Decorator lines are stripped: only the function definition block is executed.
        """
        fn_name = entry["fn_name"]

        module_globals = self._sandbox_globals
        module_globals[self._var_name] = self

        # critical: inject runtime globals (viz, _content, OBJECTS, Trigger, Event, ...)
        if self._rebind_globals:
            module_globals.update(self._rebind_globals)

        if "__builtins__" not in module_globals:
            module_globals["__builtins__"] = __builtins__

        # strip off decorator lines so we only exec the `def` block
        raw = entry["source"]
        lines = raw.splitlines()
        for idx, line in enumerate(lines):
            pattern = rf"\s*(async\s+)?def\s+{re.escape(fn_name)}\b"
            if re.match(pattern, line):
                func_body = "\n".join(lines[idx:])
                break
        else:
            raise RuntimeError(f"Could not find definition for '{fn_name}'")

        exec(compile(func_body, filename=f"<{self._sandbox_module_name}>", mode="exec"), module_globals)

        fn = module_globals.get(fn_name)
        if not isinstance(fn, types.FunctionType):
            raise RuntimeError(f"Failed to construct function '{fn_name}'")
        return fn

    def _apply_with_source_patch(self, decorator, fn, source: str):
        """
        Temporarily override inspect.getsource so that SummonerClient decorators
        record the original DNA source text.
        """
        orig = inspect.getsource
        inspect.getsource = lambda o: source
        try:
            decorator(fn)
        finally:
            inspect.getsource = orig

    def initiate_upload_states(self):
        """Replay @upload_states from DNA onto this translated client."""
        for entry in self._dna_list:
            if entry.get("type") != "upload_states":
                continue
            fn  = self._make_from_source(entry)
            dec = self.upload_states()
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_download_states(self):
        """Replay @download_states from DNA onto this translated client."""
        for entry in self._dna_list:
            if entry.get("type") != "download_states":
                continue
            fn  = self._make_from_source(entry)
            dec = self.download_states()
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_hooks(self):
        """Replay @hook from DNA onto this translated client."""
        for entry in self._dna_list:
            if entry.get("type") != "hook":
                continue
            fn  = self._make_from_source(entry)
            dec = self.hook(
                    Direction[entry["direction"]],
                    priority=tuple(entry.get("priority", ()))
                  )
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_receivers(self):
        """Replay @receive from DNA onto this translated client."""
        for entry in self._dna_list:
            if entry.get("type") != "receive":
                continue
            fn    = self._make_from_source(entry)
            dec   = self.receive(
                      entry["route"],
                      priority=tuple(entry.get("priority", ()))
                    )
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_senders(self):
        """Replay @send from DNA onto this translated client, resolving triggers/actions from names."""
        g = self._sandbox_globals

        # Ensure rebind globals are visible BEFORE resolving triggers/actions.
        if self._rebind_globals:
            g.update(self._rebind_globals)

        TriggerCls = g.get("Trigger")
        if TriggerCls is None:
            TriggerCls = load_triggers()

        for entry in self._dna_list:
            if entry.get("type") != "send":
                continue
            fn = self._make_from_source(entry)
            on_triggers = {_resolve_trigger(TriggerCls, t) for t in entry.get("on_triggers", [])} or None
            on_actions  = {_resolve_action(Action, a) for a in entry.get("on_actions", [])} or None
            dec = self.send(
                entry["route"],
                multi=entry.get("multi", False),
                on_triggers=on_triggers,
                on_actions=on_actions,
            )
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_all(self):
        """Replay all handler types from DNA in a standard order. Call this before run()."""
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()

    async def _async_shutdown(self):
        """
        Async shutdown path used by SIGINT/SIGTERM and quit().

        Steps:
        1) cancel everything (base shutdown)
        2) cancel and await pending decorator-registration coroutines
        3) await in-flight handler/worker tasks
        4) stop the loop so run_until_complete can return
        """
        # 1) cancel everything
        super().shutdown()

        # 2) wait for decorator register() tasks
        regs = self._registration_tasks or []
        if regs:
            for t in regs:
                t.cancel()
            await asyncio.gather(*regs, return_exceptions=True)
            regs.clear()

        # 3) wait for in-flight handlers and workers
        await self._wait_for_tasks_to_finish()

        # 4) stop the loop so run_client's run_until_complete can finish
        self.loop.stop()

    def shutdown(self):
        """
        Overrides the base-class SIGINT/SIGTERM handler.

        The base SummonerClient.shutdown() cancels tasks, but does not await them.
        In translation mode we want a clean exit without pending-task warnings,
        so we schedule an async shutdown coroutine instead.
        """
        self.logger.info("Client is shutting down…")
        try:
            # Schedule the async shutdown helper
            asyncio.create_task(self._async_shutdown())
        except RuntimeError:
            # if the loop isn't running, ignore
            pass
    
    async def quit(self):
        """
        Quit the translated client:
        - set _quit so run_client exits
        - run the same cleanup as Ctrl+C
        """
        # 1) run the base logic so run_client() will break out on _quit
        await super().quit()
        # 2) now run the exact same cleanup we do on SIGINT
        await self._async_shutdown()

    def run(self, *args, **kwargs):
        """
        Wrap `run` so that Ctrl+C cancels any leftover registration tasks
        and shuts down cleanly (no "coroutine was never awaited" warnings).
        """
        try:
            super().run(*args, **kwargs)
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt caught-cancelling registration tasks…")
            for task in list(self._registration_tasks or []):
                task.cancel()
            # swallow the exception so we exit cleanly
            return
