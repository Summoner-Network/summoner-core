import os
import sys
from summoner.client import SummonerClient
from summoner.client import LuaScriptRunner, evolve_html
from aioconsole import ainput

RUNNER = LuaScriptRunner()

def load_file_to_string(filepath):
    """
    Reads the contents of a file and returns it as a string.

    :param filepath: Path to the file to be read.
    :return: The contents of the file as a string.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File not found at {filepath}")
    except Exception as e:
        print(f"An error occurred: {e}")

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

fetch_tool = load_file_to_string("fetch.lua")

TOOLS = {}

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        global TOOLS
        msg = (msg["content"] if isinstance(msg, dict) else msg)
        if all(key in msg for key in ["payload", "public_key", "signature"]):
            payload = msg.get("payload", None)
            for tool, code in payload.items():
                TOOLS[tool] = code
                

    @myagent.send(route="custom_send")
    async def custom_send():
        msg = await ainput("s> ")
        out = await RUNNER.run(fetch_tool, [], "", [f"https://{msg}"])
        write_string_to_file(f"{msg}.html", out)
        return msg

    myagent.run(host = "127.0.0.1", port = 8888)