import asyncio
import signal
import os
import sys
import json
from typing import Optional

# Setup path
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

# Imports
from utils import (
    remove_last_newline, 
    ensure_trailing_newline,
    load_config,
    )
from logger import setup_logger
import rust_server_sdk as rss
import rust_server_sdk_1 as rss_1
import rust_server_sdk_2 as rss_2

class ClientDisconnected(Exception):
    """Raised when the client disconnects cleanly (e.g., closes the socket)."""
    pass

class SummonerServer:
    
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
        self.logger = setup_logger(self.name)

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
        addr = writer.get_extra_info("peername")
        self.logger.info(f"{addr} connected.")

        task = asyncio.current_task()
        async with self.clients_lock:
            self.clients.add(writer)

        async with self.tasks_lock:
            self.active_tasks[task] = str(addr)

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
                        payload = json.dumps({'addr': addr, 'content': remove_last_newline(message)})
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
            except Exception:
                pass
            
            async with self.clients_lock:
                self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()

            async with self.tasks_lock:
                self.active_tasks.pop(task, None)

            self.logger.info(f"{addr} connection closed.")

    def shutdown(self):
        self.logger.info("Server is shutting down...")
        for task in asyncio.all_tasks(self.loop):
            task.cancel()

    def set_termination_signals(self):
        # SIGINT = interupt signal for Ctrl+C | value = 2
        # SIGTERM = system/process-based termination | value = 15
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, lambda: self.shutdown())

    async def run_server(self, host: str = '127.0.0.1', port: int = 8888):
        server = await asyncio.start_server(self.handle_client, host=host, port=port)
        self.logger.info(f"Python server listening on {host}:{port}.")
        async with server:
            await server.serve_forever()

    async def wait_for_tasks_to_finish(self):
        # Wait for all client handlers to finish
        async with self.tasks_lock:
            tasks = list(self.active_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def run(self, host='127.0.0.1', port=8888, config_path = ""):
        server_config = load_config(config_path=config_path, debug=True)

        rust_dispatch = {
            "rss": lambda h, p: rss.start_tokio_server(self.name, h, p),
            "rss_1": lambda h, p: rss_1.start_tokio_server(self.name, h, p, None, None, None),
            "rss_2": lambda h, p : rss_2.start_tokio_server(
                self.name, 
                {
                "host":h, 
                "port":p, 
                **server_config.get("hyper_parameters", {})
                })
        }

        option = server_config.get("version", None)
        if option in rust_dispatch:
            try:
                rust_dispatch[option](host, port)
            except KeyboardInterrupt:
                pass
            return
        
        else:
            try:
                self.set_termination_signals()
                self.loop.run_until_complete(self.run_server(host=host, port=port))
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                self.loop.run_until_complete(self.wait_for_tasks_to_finish())
                self.loop.close()
                self.logger.info("Server exited cleanly.")
