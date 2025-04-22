import asyncio
import signal
import os
import sys
from typing import Optional, Callable
from aioconsole import ainput
import inspect

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
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

        self.active_tasks: set[asyncio.Task] = set()

        self.receiving_functions = {}
        self.sending_functions = {}

        # Used after /quit to travel dynamically from servers to servers (if given values)
        self.host: Optional[str] = None
        self.port: Optional[int] = None

    def receive(self, route: str):
        def decorator(fn: Callable[[str], None]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Function for route '{route}' must be async")
            if route in self.receiving_functions:
                self.logger.warning(f"Route '{route}' already exists. Overwriting.")
            self.receiving_functions[route] = fn
            # self.receiving_functions.setdefault(route, fn)
            return fn
        return decorator

    def send(self, route: str):
        def decorator(fn: Callable[[], str]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Function for route '{route}' must be async")
            if route in self.receiving_functions:
                self.logger.warning(f"Route '{route}' already exists. Overwriting.")
            self.sending_functions[route] = fn
            # self.sending_functions.setdefault(route, fn)
            return fn
        return decorator
    
    async def handle_session(self, host='127.0.0.1', port=8888):
        stop_event = asyncio.Event()
        
        while True:
            
            task = asyncio.current_task()
            self.active_tasks.add(task)

            # if self.host and self.port are changed dynamically, then client will travel to corresponding server
            reader, writer = await asyncio.open_connection(host = self.host or host, port = self.port or port)
            self.logger.info("Connected to server.")
            
            async def listen():
                try:
                    while True:
                        data = await reader.readline()
                        if not data:
                            raise ServerDisconnected("Server closed the connection.")
                        message = data.decode()
                        await asyncio.gather(*(fn(message) for fn in self.receiving_functions.values()))
                except (ServerDisconnected, asyncio.CancelledError):
                    stop_event.set()
                    raise

            listen_task = asyncio.create_task(listen())
            
            try:
                while not stop_event.is_set():
                    messages = await asyncio.gather(*(fn() for fn in self.sending_functions.values()))
                    for message in messages:
                        if message == "/quit":
                            stop_event.set()
                            break
                        writer.write((message + "\n").encode())
                    await writer.drain()
            except asyncio.CancelledError as e:
                self.logger.info(f"Client about to disconnect...") # add new line when using Ctrl + C
            finally:
                listen_task.cancel()
                try:
                    await listen_task
                except asyncio.CancelledError:
                    pass
                except ServerDisconnected as e:
                    raise ServerDisconnected(e)
                writer.close()
                await writer.wait_closed()
                self.logger.info("Disconnected from server.")
                self.active_tasks.discard(task)

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
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)

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


if __name__ == "__main__":
    
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive_v1(message):
        if message[:len("Warning:")] == "Warning:":
            print("\r[From server]", message.strip(), flush=True)
        else:
            print("\r[Received]", message.strip(), flush=True)
        print("r> ", end="", flush=True)

    @myagent.send(route="custom_send")
    async def custom_send_v1():
        msg = await ainput("s> ")
        return msg
    
    # async def custom_send_v2():
    #     msg = "hello"
    #     await asyncio.sleep(2)
    #     return msg

    myagent.run(host = "127.0.0.1", port = 8888)
    