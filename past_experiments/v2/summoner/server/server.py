import asyncio
import signal
import os
import sys
from typing import Optional

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
from logger import setup_logger

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
        "active_tasks",
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
        self.active_tasks: set[asyncio.Task] = set()
    
    async def handle_client(
        self,
        reader: asyncio.streams.StreamReader, 
        writer: asyncio.streams.StreamWriter
        ):
        addr = writer.get_extra_info("peername")
        self.logger.info(f"{addr} connected.")
        self.clients.add(writer)
        task = asyncio.current_task()
        self.active_tasks.add(task)

        try:
            while True:
                data = await reader.readline()
                if not data:
                    raise ClientDisconnected("Client closed the connection.")
                message = data.decode()
                self.logger.info(f"Received from {addr}: {message.strip()}")
                for other_writer in self.clients:
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
            
            self.clients.remove(writer)
            writer.close()
            await writer.wait_closed()
            self.logger.info(f"{addr} connection closed.")
            self.active_tasks.discard(task)

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
        self.logger.info(f"Server started on port {port}.")
        async with server:
            await server.serve_forever()

    async def wait_for_tasks_to_finish(self):
        # Wait for all client handlers to finish
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)

    def run(self, host='127.0.0.1', port=8888):
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
