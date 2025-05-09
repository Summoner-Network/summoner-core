import os
import sys
from summoner.client import SummonerClient
from summoner import verify, generate_secret_key
from aioconsole import ainput

SECRET_KEY = None
def secret_key():
    global SECRET_KEY
    if SECRET_KEY is None:
        SECRET_KEY = generate_secret_key()
    return SECRET_KEY

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python", secret=secret_key)

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        msg = (msg["content"] if isinstance(msg, dict) else msg)
        if all(key in msg for key in ["payload", "public_key", "signature"]):
            payload = msg.get("payload", None)
            public_key = msg.get("public_key", None)
            print(f"{public_key}> {payload}")
        else:
            print(f"anonymous> {msg}")
                

    @myagent.send(route="custom_send")
    async def custom_send():
        msg = await ainput("s> ")
        return msg

    myagent.run(host = "127.0.0.1", port = 8888)