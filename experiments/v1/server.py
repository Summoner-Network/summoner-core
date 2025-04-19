import asyncio
import signal

class ClientDisconnected(Exception):
    """Raised when the client disconnects cleanly (e.g., closes the socket)."""
    pass

clients: set[asyncio.streams.StreamWriter] = set()
active_tasks: set[asyncio.Task] = set()

async def handle_client(
        reader: asyncio.streams.StreamReader, 
        writer: asyncio.streams.StreamWriter
        ):
    addr = writer.get_extra_info("peername")
    print(f"{addr} connected.")
    clients.add(writer)
    task = asyncio.current_task()
    active_tasks.add(task)

    try:
        while True:
            data = await reader.readline()
            if not data:
                raise ClientDisconnected("Client closed the connection.")
            message = data.decode()
            print(f"Received from {addr}: {message.strip()}")
            for other_writer in clients:
                if other_writer != writer:
                    other_writer.write(f"[{addr}] {message}".encode())
                    await other_writer.drain()
    
    except ClientDisconnected:
        print(f"{addr} disconnected.")
    
    except (ConnectionResetError, asyncio.IncompleteReadError, asyncio.CancelledError) as e:
        print(f"{addr} error or abrupt disconnect: {e}")
    
    finally:
        
        try:
            writer.write("Warning: Server disconnected.".encode())
            await writer.drain()
        
        except Exception:
            pass
        
        clients.remove(writer)
        writer.close()
        await writer.wait_closed()
        print(f"{addr} connection closed.")
        active_tasks.discard(task)

async def run_server():
    server = await asyncio.start_server(handle_client, "127.0.0.1", 8888)
    print("Server started on port 8888.")
    async with server:
        await server.serve_forever()

def shutdown(loop):
    print("Server is shutting down...")
    for task in asyncio.all_tasks(loop):
        task.cancel()

async def wait_for_tasks_to_finish():
    # Wait for all client handlers to finish
    if active_tasks:
        await asyncio.gather(*active_tasks, return_exceptions=True)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown(loop))

    try:
        loop.run_until_complete(run_server())
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(wait_for_tasks_to_finish())
        loop.close()
        print("Server exited cleanly.")
