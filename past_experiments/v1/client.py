import asyncio
from aioconsole import ainput
import signal

class ServerDisconnected(Exception):
    """Raised when the server closes the connection."""
    pass

async def custom_send_v1():
    msg = "hello"
    await asyncio.sleep(2)
    return msg

async def custom_send_v2():
    msg = await ainput("s> ")
    return msg

async def custom_receive_v1(message):
    if message[:len("Warning:")] == "Warning:":
        print("\r[From server]", message.strip(), flush=True)
    else:
        print("\r[Received]", message.strip(), flush=True)
    print("r> ", end="", flush=True)


async def handle_session():
    stop_event = asyncio.Event()

    reader, writer = await asyncio.open_connection("127.0.0.1", 8888)
    print("Connected to server.")
    
    async def listen():
        try:
            while True:
                data = await reader.readline()
                if not data:
                    raise ServerDisconnected("Server closed the connection.")
                message = data.decode()
                await custom_receive_v1(message)
        except (ServerDisconnected, asyncio.CancelledError):
            stop_event.set()
            raise

    listen_task = asyncio.create_task(listen())
    
    try:
        while not stop_event.is_set():
            msg = await custom_send_v2()
            if msg == "/quit":
                stop_event.set()
                break
            writer.write((msg + "\n").encode())
            await writer.drain()

    except asyncio.CancelledError as e:
        print() # add new line when using Ctrl + C
    
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
        print("Disconnected from server.")

async def client_main():
    retry_delay = 3
    while True:
        try:
            await handle_session()
            break  # clean disconnect (/quit)
        except ConnectionRefusedError as e:
            print(f"[ConnectionRefusedError: {e}] Retrying in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
        
        except ServerDisconnected as e:
            print(f"[ServerDisconnected: {e}] Retrying in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
        
        except OSError as e:
            print(f"[OSError: {e}] Retrying in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)

def shutdown(loop):
    for task in asyncio.all_tasks(loop):
        task.cancel()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown(loop))

    try:
        loop.run_until_complete(client_main())

    except asyncio.CancelledError:
        pass
    
    finally:
        loop.close()
        print("Client exited cleanly.")