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

    For imported clients, handlers keep sharing their original module globals.
    For DNA sources, handlers live in a per-source sandbox module + context globals.
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

        self._rebind_globals = dict(rebind_globals or {})

        self._allow_context_imports = allow_context_imports
        self._verbose_context_imports = verbose_context_imports
        self._close_subclients = close_subclients

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

        ctx = None
        entries = dna_list
        if entries and isinstance(entries[0], dict) and entries[0].get("type") == "__context__":
            ctx = entries[0]
            entries = entries[1:]

        var_name = entry.get("var_name")
        if var_name is None:
            ctx_var = ctx.get("var_name") if isinstance(ctx, dict) else None
            var_name = ctx_var if isinstance(ctx_var, str) and ctx_var else "agent"
        if not isinstance(var_name, str):
            raise TypeError(f"Entry #{idx} 'var_name' must be str, got {type(var_name).__name__}")

        sandbox_module_name = f"summoner_merge_{uuid.uuid4().hex}"
        sandbox_module = types.ModuleType(sandbox_module_name)
        sys.modules[sandbox_module_name] = sandbox_module
        g = sandbox_module.__dict__

        # bind the client name used inside handler source to the *merged* client
        g[var_name] = self

        # apply context (imports/globals/recipes) into that sandbox
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

    # def _shutdown_imported_clients(self) -> None:
    #     for src in self.sources:
    #         if src.get("kind") != "client":
    #             continue
    #         var_name = src["var_name"]
    #         client = src["client"]

    #         # cancel registration tasks
    #         for task in getattr(client, "_registration_tasks", []):
    #             try:
    #                 task.cancel()
    #             except Exception as e:
    #                 self.logger.warning(f"[{var_name}] Error cancelling registration task: {e}")
    #         try:
    #             client._registration_tasks.clear()
    #         except Exception:
    #             pass

    #         # close loop
    #         try:
    #             client.loop.close()
    #         except Exception as e:
    #             self.logger.warning(f"[{var_name}] Error closing event loop: {e}")

    def _shutdown_imported_clients(self) -> None:
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

            # 2) drain tasks on *that* loop so they are actually awaited
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
        report = {"label": label, "succeeded": [], "failed": [], "skipped": []}

        if not isinstance(ctx, dict):
            return report

        # imports (safe)
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

        # plain globals
        globs = ctx.get("globals") or {}
        if isinstance(globs, dict):
            for k, v in globs.items():
                if isinstance(k, str):
                    g.setdefault(k, v)

        # recipes
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
        fn_name = entry["fn_name"]
        raw = entry["source"]
        lines = raw.splitlines()

        # ---------------------------------------------------------------------
        # 1) Find the def line for this function, but SKIP decorators.
        #    Your DNA sources include decorators like "@client.receive(...)".
        #    Executing those in the sandbox is wrong: it can register handlers,
        #    or fail because 'client' is not defined there.
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
        # 2) Ensure rebinding happens in the SAME globals dict used by exec(),
        #    because fn.__globals__ will be exactly this dict.
        #
        #    This is the critical fix for: NameError: name 'viz' is not defined
        # ---------------------------------------------------------------------
        # If you store rebind globals on self, use that. Adjust attribute name
        # if yours differs.
        rebind = self._rebind_globals
        if isinstance(rebind, dict) and rebind:
            # Update in-place so the constructed function sees these names later.
            g.update(rebind)

        # ---------------------------------------------------------------------
        # 3) Execute. (Optionally you can ensure builtins exist.)
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

                TriggerCls = g.get("Trigger")
                if TriggerCls is None:
                    # loads TRIGGERS from the directory of the running script (via sys.argv[0] in your code)
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
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()

    def _normalize_registration_tasks(self) -> None:
        """
        Ensure _registration_tasks contains asyncio.Task objects, not bare coroutines.
        This prevents 'coroutine ... was never awaited' at shutdown.
        """
        regs = list(getattr(self, "_registration_tasks", []) or [])
        if not regs:
            return

        out = []
        loop = getattr(self, "loop", None)

        for r in regs:
            if asyncio.iscoroutine(r):
                # Prefer binding to the client's loop if possible.
                if loop is not None and not loop.is_closed():
                    out.append(loop.create_task(r))
                else:
                    # Best-effort fallback (may raise if no running loop)
                    out.append(asyncio.create_task(r))
            else:
                out.append(r)

        self._registration_tasks = out

    async def _async_shutdown(self):
        """
        Mirror ClientTranslation: cancel + await registration tasks,
        then stop loop to avoid pending-task warnings.
        """
        # 1) base shutdown (your existing cancellation logic)
        super().shutdown()

        # 2) normalize then drain decorator registration tasks
        self._normalize_registration_tasks()
        regs = list(getattr(self, "_registration_tasks", []) or [])
        if regs:
            for t in regs:
                with suppress(Exception):
                    t.cancel()
            with suppress(Exception):
                await asyncio.gather(*regs, return_exceptions=True)
            with suppress(Exception):
                self._registration_tasks.clear()

        # 3) wait for in-flight handler/worker tasks (if SummonerClient provides it)
        with suppress(Exception):
            await self._wait_for_tasks_to_finish()

        # 4) stop loop so run() unwinds cleanly
        with suppress(Exception):
            self.loop.stop()

    def shutdown(self):
        """
        Schedule _async_shutdown from signal handler context.
        """
        self.logger.info("Client is shutting down...")
        try:
            asyncio.create_task(self._async_shutdown())
        except RuntimeError:
            # Loop not running (shutdown before run). Best-effort sync cancel.
            with suppress(Exception):
                self._normalize_registration_tasks()
            for t in list(getattr(self, "_registration_tasks", []) or []):
                with suppress(Exception):
                    t.cancel()
            with suppress(Exception):
                super().shutdown()

    async def quit(self):
        # preserve base semantics
        await super().quit()
        await self._async_shutdown()

    def run(self, *args, **kwargs):
        """
        Optional: keep symmetry with Translation.
        (This is not strictly required if shutdown() is wired correctly,
        but it helps in environments where KeyboardInterrupt bypasses signals.)
        """
        try:
            super().run(*args, **kwargs)
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt caught—cancelling registration tasks…")
            with suppress(Exception):
                self._normalize_registration_tasks()
            for task in list(getattr(self, "_registration_tasks", []) or []):
                with suppress(Exception):
                    task.cancel()
            return
        

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
        Best-effort: cancel & await pending registration tasks, then close the client's loop.
        This mirrors the behavior that makes ClientMerger not leak shutdown warnings.
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
        Temporarily override inspect.getsource so that
        SummonerClient's decorator can record the right text.
        """
        orig = inspect.getsource
        inspect.getsource = lambda o: source
        try:
            decorator(fn)
        finally:
            inspect.getsource = orig

    def initiate_upload_states(self):
        for entry in self._dna_list:
            if entry.get("type") != "upload_states":
                continue
            fn  = self._make_from_source(entry)
            dec = self.upload_states()
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_download_states(self):
        for entry in self._dna_list:
            if entry.get("type") != "download_states":
                continue
            fn  = self._make_from_source(entry)
            dec = self.download_states()
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_hooks(self):
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
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()

    async def _async_shutdown(self):
        """
        1) call the base-class shutdown to cancel all tasks
        2) await any pending decorator-registration coroutines
        3) await all active handler/worker tasks
        4) stop the loop so run_client returns
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

        # 3) wait for in-flight handlers & workers
        await self._wait_for_tasks_to_finish()

        # 4) stop the loop so run_client's run_until_complete can finish
        self.loop.stop()

    def shutdown(self):
        """
        Overrides the base-class SIGINT/SIGTERM handler
        to schedule _async_shutdown() instead of blocking.
        """
        self.logger.info("Client is shutting down…")
        try:
            # Schedule the async shutdown helper
            asyncio.create_task(self._async_shutdown())
        except RuntimeError:
            # if the loop isn't running, ignore
            pass
    
    async def quit(self):
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
            self.logger.info("KeyboardInterrupt caught—cancelling registration tasks…")
            for task in list(self._registration_tasks or []):
                task.cancel()
            # swallow the exception so we exit cleanly
            return

