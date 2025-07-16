from importlib import import_module
from typing import Optional, Any
import inspect
import asyncio
import types
import re

import os, sys
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client.client import SummonerClient
from summoner.protocol.triggers import Signal, Action
from summoner.protocol.process import Direction

class ClientMerger(SummonerClient):
    """
    Merge multiple named SummonerClient instances into a single client, 
    replaying each handler but rebinding its module-level client nameto this merged instance.
    """

    def __init__(
        self,
        named_clients: list[dict[str, SummonerClient]],
        name: Optional[str] = None
    ):
        super().__init__(name=name)
        self.named_clients: list[dict[str, SummonerClient]] = []
        
        # Validate and store the list of dicts {'var_name': var_name, 'client': client}
        for idx, entry in enumerate(named_clients):
            
            if not isinstance(entry, dict):
                raise TypeError(f"Entry #{idx} must be a dict, got {type(entry).__name__}")
            if 'var_name' not in entry or 'client' not in entry:
                raise KeyError(
                    f"Entry #{idx} missing 'var_name' or 'client' key; got keys: {list(entry.keys())}"
                )

            var_name = entry['var_name']
            client   = entry['client']

            if not isinstance(var_name, str):
                raise TypeError(f"Entry #{idx} 'var_name' must be a str, got {type(var_name).__name__}")
            if not isinstance(client, SummonerClient):
                raise TypeError(f"Entry #{idx} 'client' must be SummonerClient, got {type(client).__name__}")

            self.named_clients.append({'var_name': var_name, 'client': client})

        # Cancel any pending registration tasks and close each sub-client's loop
        for entry in self.named_clients:
            
            var_name = entry['var_name']
            client   = entry['client']

            # 1) Cancel registration tasks
            for task in getattr(client, '_registration_tasks', []):
                try:
                    task.cancel()
                except Exception as e:
                    self.logger.warning(f"[{var_name}] Error cancelling registration task: {e}")
            client._registration_tasks.clear()

            # 2) Close the event loop
            try:
                client.loop.close()
            except Exception as e:
                self.logger.warning(f"[{var_name}] Error closing event loop: {e}")

    def _clone_handler(self, fn: types.FunctionType, original_name: str) -> types.FunctionType:
        """
        Clone `fn` so it shares its module globals (so module-level state
        like counters and locks remains shared) but rebinds the
        name `original_name` to this merged client.
        """
        g = fn.__globals__
        try:
            # Redirect the handler's client name to self
            g[original_name] = self
        except Exception as e:
            self.logger.warning(f"Could not bind '{original_name}' to merged client: {e}")

        # Build the cloned function with the same code, defaults, closure
        new_fn = types.FunctionType(
            fn.__code__,
            g,
            name=fn.__name__,
            argdefs=fn.__defaults__,
            closure=fn.__closure__,
        )
        # Preserve metadata
        new_fn.__annotations__ = fn.__annotations__
        new_fn.__doc__         = fn.__doc__
        return new_fn

    def initiate_hooks(self):
        """
        Replay all @hook handlers from each sub-client, rebinding their
        module-level client name to this merged instance.
        """
        for entry in self.named_clients:
            var_name = entry['var_name']
            client   = entry['client']
            for dna in client._dna_hooks:
                fn_clone = self._clone_handler(dna['fn'], var_name)
                try:
                    self.hook(dna['direction'], priority=dna['priority'])(fn_clone)
                except Exception as e:
                    self.logger.warning(
                        f"[{var_name}] Failed to replay hook '{dna['fn'].__name__}': {e}"
                    )

    def initiate_receivers(self):
        """
        Replay all @receive handlers from each sub-client.
        """
        for entry in self.named_clients:
            var_name = entry['var_name']
            client   = entry['client']
            for dna in client._dna_receivers:
                fn_clone = self._clone_handler(dna['fn'], var_name)
                try:
                    self.receive(dna['route'], priority=dna['priority'])(fn_clone)
                except Exception as e:
                    self.logger.warning(
                        f"[{var_name}] Failed to replay receiver '{dna['fn'].__name__}' "
                        f"on route '{dna['route']}': {e}"
                    )

    def initiate_senders(self):
        """
        Replay all @send handlers from each sub-client.
        """
        for entry in self.named_clients:
            var_name = entry['var_name']
            client   = entry['client']
            for dna in client._dna_senders:
                fn_clone = self._clone_handler(dna['fn'], var_name)
                try:
                    self.send(
                        dna['route'],
                        multi=dna['multi'],
                        on_triggers=dna['on_triggers'],
                        on_actions=dna['on_actions'],
                    )(fn_clone)
                except Exception as e:
                    self.logger.warning(
                        f"[{var_name}] Failed to replay sender '{dna['fn'].__name__}' "
                        f"on route '{dna['route']}': {e}"
                    )


class ClientTranslation(SummonerClient):
    """
    Reconstruct a SummonerClient from its DNA list, inlining each handler
    directly into its original module so that module-level globals (e.g. tracker)
    are shared and updated.
    """

    def __init__(
        self,
        dna_list: list[dict[str, Any]],
        name: Optional[str] = None,
        var_name: str = "agent"
    ):
        super().__init__(name=name)
        if not isinstance(dna_list, list):
            raise TypeError("dna_list must be a list of DNA entries")
        self._dna_list = dna_list
        if not isinstance(var_name, str):
            raise TypeError("var_name must be a string")
        self._var_name = var_name

    def _make_from_source(self, entry: dict[str, Any]) -> types.FunctionType:
        module_name = entry["module"]
        fn_name     = entry["fn_name"]

        # 1) load the real module and bind `self` into it
        try:
            module = sys.modules.get(module_name) or import_module(module_name)
        except ImportError as e:
            raise ImportError(f"could not import module '{module_name}': {e}")
        module_globals = module.__dict__
        module_globals[self._var_name] = self

        # 2) strip off decorator lines so we only exec the `def` block
        raw = entry["source"]
        lines = raw.splitlines()
        for idx, line in enumerate(lines):
            pattern = rf"\s*(async\s+)?def\s+{re.escape(fn_name)}\b"
            if re.match(pattern, line):
                func_body = "\n".join(lines[idx:])
                break
        else:
            raise RuntimeError(f"Could not find definition for '{fn_name}'")

        # 3) exec that `def` block directly into module.__dict__
        exec(compile(func_body, filename=f"<{module_name}>", mode="exec"), module_globals)

        # 4) grab the new function object
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
        for entry in self._dna_list:
            if entry.get("type") != "send":
                continue
            fn     = self._make_from_source(entry)
            dec    = self.send(
                       entry["route"],
                       multi=entry.get("multi", False),
                       on_triggers={Signal[t] for t in entry.get("on_triggers", [])} or None,
                       on_actions={getattr(Action, a) for a in entry.get("on_actions", [])} or None,
                     )
            self._apply_with_source_patch(dec, fn, entry["source"])


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
        regs = getattr(self, "_registration_tasks", None) or []
        if regs:
            for t in regs:
                t.cancel()
            await asyncio.gather(*regs, return_exceptions=True)
            regs.clear()

        # 3) wait for in-flight handlers & workers
        await self._wait_for_tasks_to_finish()

        # 4) stop the loop so run_client’s run_until_complete can finish
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
            # if the loop isn’t running, ignore
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
            for task in list(getattr(self, "_registration_tasks", [])):
                task.cancel()
            # swallow the exception so we exit cleanly
            return

