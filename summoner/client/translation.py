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
#pylint:disable=line-too-long, wrong-import-position, duplicate-code
#pylint:disable=invalid-name, broad-exception-caught,logging-fstring-interpolation

from importlib import import_module
from typing import Optional, Any
import inspect
import asyncio
import types
import re
import uuid

import os
import sys

from summoner.client.just_merger import _resolve_action, _resolve_trigger
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client import SummonerClient
from summoner.protocol.triggers import Action, load_triggers
from summoner.protocol.process import Direction

# pylint:disable=too-many-instance-attributes
class ClientTranslation(SummonerClient):
    """
    Reconstruct a SummonerClient from its DNA list.

    Translation compiles handlers from their recorded source into a fresh sandbox module,
    then registers them on this client via normal decorators.

    Execution environment
    ---------------------
    - Translated handlers do not run inside the original agent modules.
    - They run inside the translation sandbox, with only explicitly imported or rebound
      globals available (for example: Trigger, shared objects, viz, etc.).

    Context behavior
    ----------------
    If the DNA begins with a "__context__" entry, translation may:
      - exec() ctx["imports"] (if allow_context_imports=True)
      - bind ctx["globals"] into sandbox
      - eval() ctx["recipes"] into sandbox

    Template-client cleanup
    -----------------------
    DNA entries carry a "module" field from the original capture.
    Importing those modules can create template SummonerClient instances at import-time.

    This class attempts to find and clean up such template clients so they do not leave
    pending registration tasks or open loops behind.
    """
    # pylint:disable=too-many-arguments, too-many-positional-arguments
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

        # Best-effort cleanup of template clients imported indirectly via DNA modules
        self._cleanup_template_clients_from_modules()

    #pylint:disable=too-many-branches
    def _apply_context(self):
        """
        Apply optional "__context__" entry into the translation sandbox.

        This is best-effort and intended for trusted DNA.
        It may execute code via exec()/eval() if imports/recipes are present.
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
                # pylint:disable=exec-used
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
                # pylint:disable=eval-used
                try:
                    g.setdefault(k, eval(expr, g, {}))
                except Exception as e:
                    self.logger.warning(f"Could not eval recipe for {k}: {expr!r} ({e})")

    #pylint:disable=unused-argument
    def _cleanup_one_template_client(self, client: SummonerClient, label: str):
        """
        Best-effort cleanup for a template client discovered in an imported module.

        If a DNA entry references a module, importing it may construct a SummonerClient
        at module import-time. That client is not meant to run in translation mode.

        Cleanup steps:
          - cancel pending registration tasks
          - if possible, await cancellations by driving the template's loop
          - close the loop when it is not running
        """
        # pylint:disable=protected-access
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
            # pylint:disable=protected-access
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
        For every module referenced in the DNA, attempt to find a template client:

        If the module defines a global variable named self._var_name (for example "agent")
        and it points to a SummonerClient instance other than this translated client,
        then treat it as a template and clean it up.

        This reduces warnings about pending registration tasks and open event loops.
        """
        modules = {entry.get("module") for entry in self._dna_list if isinstance(entry, dict)}
        modules.discard(None)

        seen_ids: set[int] = set()

        for module_name in modules:
            try:
                module = sys.modules.get(module_name) or import_module(module_name) # type: ignore
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

        Decorator lines are stripped so compilation does not implicitly register handlers.
        """
        fn_name = entry["fn_name"]

        module_globals = self._sandbox_globals
        module_globals[self._var_name] = self

        # inject runtime globals (Trigger, viz, shared objects, etc.)
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

        #pylint:disable=exec-used
        exec(compile(func_body, filename=f"<{self._sandbox_module_name}>", mode="exec"), module_globals)

        fn = module_globals.get(fn_name)
        if not isinstance(fn, types.FunctionType):
            raise RuntimeError(f"Failed to construct function '{fn_name}'")
        return fn

    def _apply_with_source_patch(self, decorator, fn, source: str):
        """
        Temporarily override inspect.getsource so SummonerClient decorators record DNA text.

        This is process-global and is intended for single-threaded translation runs.
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
        """
        Replay @send from DNA onto this translated client.

        Triggers and actions are stored by name in DNA, then resolved here:
          - Trigger is resolved using a Trigger class found in sandbox globals, else load_triggers()
          - Action is resolved from the Action container by name
        """
        g = self._sandbox_globals

        # Ensure rebind globals are visible before resolving triggers/actions.
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
                on_triggers=on_triggers, # type: ignore
                on_actions=on_actions,
            )
            self._apply_with_source_patch(dec, fn, entry["source"])

    def initiate_all(self):
        """
        Replay all handler types from DNA in a standard order.

        This should be called before run().
        """
        self.initiate_upload_states()
        self.initiate_download_states()
        self.initiate_hooks()
        self.initiate_receivers()
        self.initiate_senders()

    async def _async_shutdown(self):
        """
        Async shutdown path used by SIGINT/SIGTERM and quit().

        Steps
        -----
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
        Override the base-class SIGINT/SIGTERM handler.

        The base SummonerClient.shutdown() cancels tasks but does not await them.
        In translation mode, a cleaner exit is preferred, so we schedule an async
        shutdown coroutine instead.
        """
        self.logger.info("Client is shutting down…")
        try:
            asyncio.create_task(self._async_shutdown())
        except RuntimeError:
            # If the loop isn't running, ignore.
            pass

    async def quit(self):
        """
        Quit the translated client:
          - set _quit so run_client exits
          - then run the same cleanup as Ctrl+C
        """
        await super().quit()
        await self._async_shutdown()

    def run(self, *args, **kwargs):
        """
        Wrap run() so that Ctrl+C cancels leftover registration tasks and exits cleanly.

        Note: this wrapper is intentionally conservative and does not change the base
        reconnection and session logic.
        """
        try:
            super().run(*args, **kwargs)
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt caught-cancelling registration tasks…")
            for task in list(self._registration_tasks or []):
                task.cancel()
