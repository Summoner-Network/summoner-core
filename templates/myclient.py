import os
import sys
from summoner.client import SummonerClient
from aioconsole import ainput

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
        tag = ("\r[From server]" if content[:len("Warning:")] == "Warning:" else "\r[Received]")
        print(tag, content, flush=True)
        print("r> ", end="", flush=True)

    @myagent.send(route="custom_send")
    async def custom_send():
        content = await ainput("s> ")
        return content

    myagent.run(host = "http://testnet.summoner.org", port = 8888)