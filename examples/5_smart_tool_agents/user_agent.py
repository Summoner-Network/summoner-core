import os
import sys
from summoner.client import SummonerClient
from summoner.client import LuaScriptRunner
from aioconsole import ainput
import asyncio
from summoner import generate_secret_key

RUNNER = LuaScriptRunner()

def write_string_to_file(filepath, content):
    """
    Writes the given string content to a file.

    :param filepath: Path to the file to be written.
    :param content: The string content to write to the file.
    """
    try:
        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)
    except Exception as e:
        print(f"An error occurred while writing to the file: {e}")

TOOLS = {"public_key": {"tool": "code"}}

SECRET_KEY = None
def secret_key():
    global SECRET_KEY
    if SECRET_KEY is None:
        SECRET_KEY = generate_secret_key()
    return SECRET_KEY

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python", secret=secret_key)

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg_full):
        global TOOLS
        msg = (msg_full["content"] if isinstance(msg_full, dict) else msg_full)
        if all(key in msg for key in ["payload", "public_key", "signature"]):
            payload = msg.get("payload", None)
            pub_key = msg.get("public_key", None)
            for tool, code in payload.items():
                if pub_key in TOOLS:
                    if (
                        (tool in TOOLS[pub_key] and TOOLS[pub_key][tool] != code)
                        or (tool not in TOOLS[pub_key])
                    ):
                        TOOLS[pub_key][tool] = code
                        print(f"Obtained a new tool ({tool}) from {pub_key}")
                else:
                    TOOLS[pub_key] = payload
                    print(f"Obtained a set of tools from {pub_key}")
        await asyncio.sleep(1)
            
    @myagent.send(route="custom_send")
    async def custom_send():
        print("Enter a provider public key")
        msg_pkey = await ainput("pubkey>")
        print(msg_pkey in TOOLS)
        print("Enter a provided tool to use")
        msg_tool = await ainput("tool>")
        print("Enter a URL to fetch (without http:// or https://):")
        msg_args = await ainput("args>")
        out = await RUNNER.run(TOOLS[msg_pkey][msg_tool], [], "", [f"https://{msg_args}"])
        write_string_to_file(f"{msg_args}.html", out)
        return out

    myagent.run(host = "127.0.0.1", port = 8888)
