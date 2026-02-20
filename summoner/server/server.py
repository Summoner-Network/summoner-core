"""
TODO: doc server
"""
# pylint:disable=wrong-import-position, logging-fstring-interpolation
import asyncio
import signal
import os
import sys
import json
from typing import Optional, Any
import platform
import importlib

# Setup path
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

# Imports
from summoner.utils import (
    remove_last_newline,
    ensure_trailing_newline,
    load_config,
    )
from summoner.logger import get_logger, configure_logger, Logger
from summoner.utils import format_addr

# Priority is insertion order: first = preferred ("latest").
RUST_BACKENDS = {
    "rust_v1.1.0": "rust_server_v1_1_0",
    "rust_v1.0.0": "rust_server_v1_0_0",
    # add more here later...
}

RUST_MODULES = {}   # key: "rust_v1.1.0" -> imported module
RUST_LATEST = None  # imported module for "rust" (best available)

if platform.system() != "Windows":
    for key, modname in RUST_BACKENDS.items():
        try:
            m = importlib.import_module(modname)
            RUST_MODULES[key] = m
            if RUST_LATEST is None:
                RUST_LATEST = m
        except ModuleNotFoundError:
            pass

class ClientDisconnected(Exception):
    """Raised when the client disconnects cleanly (e.g., closes the socket)."""


class SummonerServer:
    """
    TODO: doc server
    """

    __slots__: tuple[str, ...] = (
        "name",
        "logger",
        "loop",
        "clients",
        "clients_lock",
        "active_tasks",
        "tasks_lock",
    )

    def __init__(self, name: Optional[str] = None):
        # Give a name to the server
        self.name = name if isinstance(name, str) else "<server:no-name>"
        self.logger: Logger = get_logger(self.name)

         # Create a new event loop
        self.loop = asyncio.new_event_loop()
        # Set the new loop as the current thread
        asyncio.set_event_loop(self.loop)

        self.clients: set[asyncio.streams.StreamWriter] = set()
        self.clients_lock = asyncio.Lock()

        self.active_tasks: dict[asyncio.Task, str] = {}
        self.tasks_lock = asyncio.Lock()

    async def handle_client(
        self,
        reader: asyncio.streams.StreamReader,
        writer: asyncio.streams.StreamWriter
        ):
        """
        TODO: doc handle_client
        """
        addr = format_addr(writer.get_extra_info("peername"))
        self.logger.info(f"{addr} connected.")

        task = asyncio.current_task()
        async with self.clients_lock:
            self.clients.add(writer)

        async with self.tasks_lock:
            self.active_tasks[task] = addr # type: ignore

        try:
            while True:
                data = await reader.readline()
                if not data:
                    raise ClientDisconnected("Client closed the connection.")
                message = data.decode()
                self.logger.info(f"Received from {addr}: {remove_last_newline(message)}")

                # Create a snapshot of current clients to allow safe iteration
                async with self.clients_lock:
                    clients_snapshot = list(self.clients)

                # Iterate over the snapshot to avoid concurrency issues without long-held locks
                for other_writer in clients_snapshot:
                    if other_writer != writer:
                        payload = json.dumps(
                            {
                                "remote_addr": addr,
                                "content": remove_last_newline(message)
                            }
                        )
                        other_writer.write(ensure_trailing_newline(payload).encode())
                        await other_writer.drain()

        except ClientDisconnected:
            self.logger.info(f"{addr} disconnected.")

        except (ConnectionResetError, asyncio.IncompleteReadError, asyncio.CancelledError) as e:
            self.logger.warning(f"{addr} error or abrupt disconnect: {e}")

        finally:

            try:
                writer.write("Warning: Server disconnected.".encode())
                await writer.drain()
            except Exception: #pylint:disable=broad-exception-caught
                pass

            async with self.clients_lock:
                self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()

            async with self.tasks_lock:
                self.active_tasks.pop(task, None) # type: ignore

            self.logger.info(f"{addr} connection closed.")

    #pylint:disable=duplicate-code
    def shutdown(self):
        """
        Cancel all tasks for this loop
        """
        self.logger.info("Server is shutting down...")
        for task in asyncio.all_tasks(self.loop):
            task.cancel()

    def set_termination_signals(self):
        """
        Upon interrupt signal or system/process-based termination
        do the shutdown method.
        Requires not Windows to have any effect
        """
        # SIGINT = interupt signal for Ctrl+C | value = 2
        # SIGTERM = system/process-based termination | value = 15
        if platform.system() != "Windows":
            for sig in (signal.SIGINT, signal.SIGTERM):
                #pylint:disable=unnecessary-lambda
                self.loop.add_signal_handler(sig, lambda: self.shutdown())

    async def run_server(self, host: str = '127.0.0.1', port: int = 8888):
        """
        TODO: doc run_server
        """
        server = await asyncio.start_server(self.handle_client, host=host, port=port)
        self.logger.info(f"Python server listening on {host}:{port}.")
        async with server:
            await server.serve_forever()

    async def wait_for_tasks_to_finish(self):
        """
        Wait for all client handlers to finish
        """
        async with self.tasks_lock:
            tasks = list(self.active_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 8888,
        config_path: Optional[str] = None,
        config_dict: Optional[dict[str, Any]] = None,
    ):
        """
        TODO: doc run
        """

        if config_dict is None:
            # Load config parameters
            server_config = load_config(config_path=config_path,
                                        debug=True)
        elif isinstance(config_dict, dict):
            # Shallow copy to avoid external mutation
            server_config = dict(config_dict)
        else:
            #pylint:disable=line-too-long
            raise TypeError(
                f"SummonerServer.run: config_dict must be a dict or None, got {type(config_dict).__name__}")

        if platform.system() != "Windows":
            option = server_config.get("version", None)

            def _payload(h, p):
                return {
                    "host": server_config.get("host") or h,
                    "port": server_config.get("port") or p,
                    "logger": server_config.get("logger", {}),
                    **server_config.get("hyper_parameters", {}),
                }

            mod = None

            if option == "rust":
                mod = RUST_LATEST

            elif option in RUST_MODULES:
                mod = RUST_MODULES[option]

            elif isinstance(option, str) and option.startswith("rust"):
                available = ", ".join(["rust"] + list(RUST_MODULES.keys())) if RUST_LATEST else \
                            ", ".join(RUST_MODULES.keys())
                #pylint:disable=line-too-long
                raise RuntimeError(
                    f"Rust backend '{option}' requested but not available. Installed options: {available or '(none)'}"
                )

            if mod is not None:
                try:
                    requested = option if option is not None else "(unset)"
                    #pylint:disable=line-too-long
                    print(f"[DEBUG] Config requested version '{requested}' -> resolved to '{mod.__name__}'")
                    mod.start_tokio_server(self.name, _payload(host, port))
                except KeyboardInterrupt:
                    pass
                return


        try:
            logger_cfg = server_config.get("logger", {})
            configure_logger(self.logger, logger_cfg)

            self.set_termination_signals()
            self.loop.run_until_complete(self.run_server(host=host, port=port))

        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

        finally:
            self.loop.run_until_complete(self.wait_for_tasks_to_finish())

            self.loop.close()
            self.logger.info("Server exited cleanly.")
