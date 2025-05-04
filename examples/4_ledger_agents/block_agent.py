import os
import sys
from summoner.client import SummonerClient
from aioconsole import ainput
import asyncio

buffer_lock = asyncio.Lock()
buffer = []

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        if msg["content"]["function"] == "submit":
            async with buffer_lock:
                buffer.append(msg["content"]["parameters"][0])

    @myagent.send(route="custom_send")
    async def custom_send():
        tmp = []
        async with buffer_lock:
            tmp = buffer
            buffer = []
        print("Produced a block with {} transactions.".format(len(tmp)))
        return {
            "function": "sequence",
            "parameters": [tmp]
        }

    myagent.run(host = "127.0.0.1", port = 8888)