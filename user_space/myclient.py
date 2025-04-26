import os
import sys
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
from summoner.client import SummonerClient
from aioconsole import ainput

if __name__ == "__main__":
    
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive_v1(msg):
        msg = (msg["content"] if isinstance(msg, dict) else msg) 
        tag = ("\r[From server]" if msg[:len("Warning:")] == "Warning:" else "\r[Received]")
        print(tag, msg, flush=True)
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