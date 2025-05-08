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
        """
        Handle "join" function according to JSON-RPC 2.0
        """
        payload = msg.get("payload", None)
        signature = msg.get("signature", None)
        public_key = msg.get("public_key", None)
        if signature and not public_key:
            return
        elif public_key and not signature:
            return
        elif signature and public_key:
            if not verify(public_key, payload, signature):
                return
            else:
                print(f"{public_key}> {payload}")

    @myagent.send(route="custom_send")
    async def custom_send():
        msg = await ainput("s> ")
        return msg

    myagent.run(host = "127.0.0.1", port = 8888)