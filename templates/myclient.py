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

        # You can now access the authenticated API client!
        if myagent.api:
            print(f"\r[API Status: Logged in as {myagent.api.username}]", flush=True)
            print("r> ", end="", flush=True)


    @myagent.send(route="custom_send")
    async def custom_send():
        content = await ainput("s> ")
        if (content == "narrow"):
            key = await myagent.api.narrow()
            print(f"API KEY: {key}")
        return content

    # The only change is adding the config_path argument here
    myagent.run(
        host="summoner.network",
        port=8888,
        config_path="client_config.json"  # <-- ADD THIS LINE
    )