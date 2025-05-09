import os
import sys
from summoner.client import SummonerClient
from summoner.client import LuaScriptRunner, evolve_html
from aioconsole import ainput

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

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        global TOOLS
        msg = (msg["content"] if isinstance(msg, dict) else msg)
        if all(key in msg for key in ["payload", "public_key", "signature"]):
            payload = msg.get("payload", None)
            pub_key = msg.get("public_key", None)
            for tool, code in payload.items():
                if pub_key in TOOLS:
                    TOOLS[pub_key].update({tool: code})
                    print("Obtained (more) tools!")
                else:
                    TOOLS[pub_key] = payload
                    print("Obtained some tools!")
            
    @myagent.send(route="custom_send")
    async def custom_send():
        print("Enter a provider public key")
        msg_pkey = await ainput("pubkey>")
        print("Enter a provided tool to use")
        msg_tool = await ainput("tool>")
        print("Enter a URL to fetch (without http:// or https://):")
        msg_args = await ainput("args>")
        out = await RUNNER.run(TOOLS[msg_pkey][msg_tool], [], "", [f"https://{msg_args}"])
        write_string_to_file(f"{msg_args}.html", out)
        return out

    myagent.run(host = "127.0.0.1", port = 8888)