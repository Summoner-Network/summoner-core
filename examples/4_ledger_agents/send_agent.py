import os
import sys
from summoner.client import SummonerClient
from aioconsole import ainput

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        pass

    @myagent.send(route="custom_send")
    async def custom_send():
        id = os.urandom(32).hex()
        print(f"Sending {id} to the ledger agent...")
        return {
            "function": "submit",
            "parameters": [os.urandom(32).hex(), "Hello, Ledger!"],
        }

    myagent.run(host = "127.0.0.1", port = 8888)