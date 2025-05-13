import asyncio
import signal
import os
import sys
import json
from typing import Optional, Callable, Union
from aioconsole import ainput
import inspect

# Setup path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Imports
from utils import (
    remove_last_newline,
    ensure_trailing_newline,
    fully_recover_json,
)
from logger import setup_logger


class ServerDisconnected(Exception):
    """Raised when the server closes the connection."""
    pass

class SummonerClient:
    
    # __slots__: tuple[str, ...] = (
    # )

    def __init__(self, name: Optional[str] = None, option: Optional[str] = None):
        
        # Can be "python" or "rust"
        self.option = option or "python"

        # Give a name to the server
        self.name = name if isinstance(name, str) else "<client:no-name>"
        self.logger = setup_logger(self.name)

        # Create a new event loop
        self.loop = asyncio.new_event_loop()
        # Set the new loop as the current thread
        asyncio.set_event_loop(self.loop)

        # Protect concurrent access to the set of active tasks
        self.active_tasks: set[asyncio.Task] = set()
        self.tasks_lock = asyncio.Lock()

        # Protect route registration and access for receive/send functions
        self.receiving_functions = {}
        self.sending_functions = {}
        self.routes_lock = asyncio.Lock()

        # Dynamic routing configuration (can be changed at runtime)
        self.host: Optional[str] = None
        self.port: Optional[int] = None
        self.connection_lock = asyncio.Lock()  # Protect host/port updates

    def receive(self, route: str):
        def decorator(fn: Callable[[Union[str, dict]], None]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Function for route '{route}' must be async")
            
            # Protect route registration
            async def register():
                async with self.routes_lock:
                    if route in self.receiving_functions:
                        self.logger.warning(f"Route '{route}' already exists. Overwriting.")
                    self.receiving_functions[route] = fn
                    # self.receiving_functions.setdefault(route, fn)
            
            self.loop.call_soon_threadsafe(asyncio.create_task, register())
            return fn
        return decorator

    def send(self, route: str):
        def decorator(fn: Callable[[], str]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Function for route '{route}' must be async")
            
            # Protect route registration
            async def register():
                async with self.routes_lock:
                    if route in self.sending_functions:
                        self.logger.warning(f"Route '{route}' already exists. Overwriting.")
                    self.sending_functions[route] = fn
                    # self.sending_functions.setdefault(route, fn)

            self.loop.call_soon_threadsafe(asyncio.create_task, register())
            return fn
        return decorator

    async def message_sender_loop(self, writer: asyncio.StreamWriter, stop_event: asyncio.Event):
        try:
            while not stop_event.is_set():
                # Snapshot sending functions to avoid lock during iteration
                async with self.routes_lock:
                    senders = list(self.sending_functions.values())
                payloads = await asyncio.gather(*(fn() for fn in senders))
                for payload in payloads:
                    if isinstance(payload, str) and payload == "/quit":
                        stop_event.set()
                        break
                    
                    message = json.dumps(payload) if not isinstance(payload, str) else payload
                    writer.write(ensure_trailing_newline(message).encode())
                await writer.drain()
        except asyncio.CancelledError as e:
            self.logger.info(f"Client about to disconnect...") # add new line when using Ctrl + C

    async def message_listener_loop(self, reader: asyncio.StreamReader, stop_event: asyncio.Event):
        try:
            # Snapshot receiving functions to avoid lock during iteration
            async with self.routes_lock:
                receivers = list(self.receiving_functions.values())
            while True:
                data = await reader.readline()
                if not data:
                    raise ServerDisconnected("Server closed the connection.")
                
                try:
                    # payload = json.loads(data.decode())
                    payload = fully_recover_json(data.decode())
                except:
                    payload = remove_last_newline(data.decode())

                await asyncio.gather(*(fn(payload) for fn in receivers))
        except (ServerDisconnected, asyncio.CancelledError):
            stop_event.set()
            raise
                
    async def handle_session(self, host='127.0.0.1', port=8888):
        # Run listener and sender concurrently; whichever exits first (due to disconnect or /quit)
        # triggers session termination. The remaining task is cancelled.

        # Shared flag between the two tasks to signal coordinated session termination
        stop_event = asyncio.Event()

        while True:
            # Register this session's task so it can be cancelled during shutdown
            task = asyncio.current_task()
            async with self.tasks_lock:
                self.active_tasks.add(task)

            # Use lock when accessing dynamic routing information
            async with self.connection_lock:
                current_host = self.host or host
                current_port = self.port or port

            # If self.host and self.port are changed dynamically, then client will travel to corresponding server
            reader, writer = await asyncio.open_connection(host=current_host, port=current_port)
            self.logger.info("Connected to server.")

            # These two functions run concurrently:
            # - The listener waits for messages from the server and handles disconnection.
            # - The sender waits for client input and handles /quit.
            # Either of them may call stop_event.set(), which signals a shutdown.
            # Once one completes, the other is cancelled and the connection is closed.
            listen_task = asyncio.create_task(self.message_listener_loop(reader, stop_event))
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

            # Cleanly close the connection
            writer.close()
            await writer.wait_closed()
            self.logger.info("Disconnected from server.")

            # Deregister this session task from active list
            async with self.tasks_lock:
                self.active_tasks.discard(task)

            # Check whether we should continue to the next server (agent migration)
            async with self.connection_lock:
                if self.host is None or self.port is None:
                    break

    def shutdown(self):
        self.logger.info("Client is shutting down...")
        for task in asyncio.all_tasks(self.loop):
            task.cancel()

    def set_termination_signals(self):
        # SIGINT = interupt signal for Ctrl+C | value = 2
        # SIGTERM = system/process-based termination | value = 15
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, lambda: self.shutdown())

    async def run_client(self, host='127.0.0.1', port=8888):
        retry_delay = 3
        while True:
            try:
                await self.handle_session(host=host, port=port)
                break  # clean disconnect (/quit)
            
            except ConnectionRefusedError as e:
                self.logger.error(f"[ConnectionRefusedError: {e}] Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            
            except ServerDisconnected as e:
                self.logger.error(f"[ServerDisconnected: {e}] Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            
            except OSError as e:
                self.logger.error(f"[OSError: {e}] Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)

    async def wait_for_tasks_to_finish(self):
        # Wait for all client handlers to finish
        async with self.tasks_lock:
            tasks = list(self.active_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def run(self, host='127.0.0.1', port=8888):
        try:
            self.set_termination_signals()
            self.loop.run_until_complete(self.run_client(host=host, port=port))
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            self.loop.run_until_complete(self.wait_for_tasks_to_finish())
            self.loop.close()
            self.logger.info("Client exited cleanly.")
