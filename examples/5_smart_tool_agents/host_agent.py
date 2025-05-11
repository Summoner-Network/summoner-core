import os
import sys
from summoner.client import SummonerClient
from summoner.client import LuaScriptRunner
from aioconsole import ainput
import asyncio
from summoner import generate_secret_key

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

fetch_tool = load_file_to_string("fetch.lua")

TOOLS = {"public_key": {"tool": "code"}}
SECRET_KEY = None
def secret_key():
    global SECRET_KEY
    if SECRET_KEY is None:
        SECRET_KEY = generate_secret_key()
    return SECRET_KEY

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python", secret=secret_key)

    @myagent.send(route="custom_send")
    async def custom_send():
        await asyncio.sleep(1)
        return {"fetch": load_file_to_string("fetch.lua")}

    myagent.run(host = "127.0.0.1", port = 8888)