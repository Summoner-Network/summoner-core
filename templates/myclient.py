import os
import sys
import asyncio
from summoner.client import SummonerClient
from aioconsole import ainput
import json

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)
        tag = ("[From server]" if isinstance(content, str) and content.startswith("Warning:") else "[Received]")
        # Let aioconsole handle redrawing the prompt automatically
        print(f"\r{tag} {content}", flush=True)

        if myagent.api and myagent.api.auth_method:
            auth_type = "User" if myagent.api.auth_method == 'password' else "Agent Key"
            # This is an informational line, not a prompt
            print(f"\r[API Status: Logged in as {myagent.api.username} via {auth_type}]", flush=True)


    @myagent.send(route="custom_send")
    async def custom_send():
        content = await ainput("s> ")
        if content == "narrow":
            if myagent.api:
                try:
                    key = await myagent.api.narrow()
                    if key:
                        print(f"[System] Session narrowed. New disposable Agent API Key: {key}", flush=True)
                    else:
                        print("[System] Session is already narrowed or running without primary credentials.", flush=True)
                except Exception as e:
                    print(f"[Error] Failed to narrow session: {e}", flush=True)
            else:
                print("[Error] API client not available.", flush=True)
            
            return None # Don't send the "narrow" command to the server

        elif content == "ping":
            if myagent.api and myagent.api.username:
                try:
                    print(f"[System] Pinging the Fathom ledger...", flush=True)
                    chain_key = {
                        "tenant": myagent.api.username,
                        "chainName": "ping-pong",
                        "shardId": 0
                    }
                    data_payload = {"message": "ping", "timestamp": myagent.loop.time()}
                    
                    # 1. APPEND the block
                    append_response = await myagent.api.chains.append(chain_key, {"data": data_payload})
                    block_index = append_response.get("blockIdx")
                    if not block_index:
                        raise ValueError("Append operation did not return a block index.")

                    # 2. FETCH the full block and its metadata concurrently
                    block_response, metadata_response = await asyncio.gather(
                        myagent.api.chains.get_block(chain_key, block_index),
                        myagent.api.chains.get_block_metadata(chain_key, block_index)
                    )

                    # 3. PRINT the results
                    print("[System] Pong! Full round trip successful.", flush=True)
                    print("="*40, flush=True)
                    print("  Block Metadata:", flush=True)
                    print(json.dumps(metadata_response.get("metadata"), indent=2), flush=True)
                    print("\n  Block Contents:", flush=True)
                    print(json.dumps(block_response.get("block"), indent=2), flush=True)
                    print("="*40, flush=True)

                except Exception as e:
                    print(f"[Error] Ping failed: {e}", flush=True)
            else:
                print("[Error] API client not available for ping.", flush=True)

            return None # Don't send the "ping" command to the server
        
        return content

    myagent.run(
        host="summoner.network",
        port=8888,
        config_path="client_config.json"
    )

