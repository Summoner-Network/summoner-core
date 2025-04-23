import asyncio
import signal
import os
import sys
from typing import Optional

# Setup path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Imports
from logger import setup_logger
import rust_server_sdk as rss

class ClientDisconnected(Exception):
    """Raised when the client disconnects cleanly (e.g., closes the socket)."""
    pass

class SummonerServer:
    
    __slots__: tuple[str, ...] = (
        "option",
        "name",
        "logger",
        "loop",
        "clients",
        "clients_lock",
        "active_tasks",
        "tasks_lock",
    )

    def __init__(self, name: Optional[str] = None, option: Optional[str] = None):
        
        # Can be "python" or "rust"
        self.option = option or "python"

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
                self.logger.info(f"Received from {addr}: {message.strip()}")

                # Create a snapshot of current clients to allow safe iteration
                async with self.clients_lock:
                    clients_snapshot = list(self.clients)

                # Iterate over the snapshot to avoid concurrency issues without long-held locks
                for other_writer in clients_snapshot:
                    if other_writer != writer:
                        other_writer.write(f"[{addr}] {message}".encode())
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

    def run(self, host='127.0.0.1', port=8888):
        rust_dispatch = {
            "rss": lambda h, p: rss.start_tokio_server(self.name, h, p),
        }

        if self.option in rust_dispatch:
            try:
                rust_dispatch[self.option](host, port)
            except KeyboardInterrupt:
                self.logger.warning("Rust server received KeyboardInterrupt. Exiting cleanly.")
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


if __name__ == "__main__":
    
    myserver = SummonerServer(name="MyServer", option = "python")
    myserver.run(host = "127.0.0.1", port = 8888)
