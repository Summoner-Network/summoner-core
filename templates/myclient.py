import os
import sys
from summoner.client import SummonerClient
from aioconsole import ainput

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)
        tag = ("\r[From server]" if isinstance(content, str) and content.startswith("Warning:") else "\r[Received]")
        print(f"{tag} {content}", flush=True)
        print("s> ", end="", flush=True)

        if myagent.api and myagent.api.auth_method:
            auth_type = "User" if myagent.api.auth_method == 'password' else "Agent Key"
            print(f"\r[API Status: Logged in as {myagent.api.username} via {auth_type}]", flush=True)
            print("s> ", end="", flush=True)


    @myagent.send(route="custom_send")
    async def custom_send():
        content = await ainput("s> ")
        if content == "narrow":
            if myagent.api:
                try:
                    key = await myagent.api.narrow()
                    if key:
                        print(f"\r[System] Session narrowed. New disposable Agent API Key: {key}", flush=True)
                    else:
                        print("\r[System] Session is already narrowed or running without primary credentials.", flush=True)
                except Exception as e:
                    print(f"\r[Error] Failed to narrow session: {e}", flush=True)
            else:
                print("\r[Error] API client not available.", flush=True)
            
            print("s> ", end="", flush=True)
            return None # Don't send the "narrow" command to the server
        
        return content

    myagent.run(
        host="summoner.network",
        port=8888,
        config_path="client_config.json"
    )

