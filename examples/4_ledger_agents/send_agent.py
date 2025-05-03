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
        return os.urandom(32).hex()

    myagent.run(host = "127.0.0.1", port = 8888)