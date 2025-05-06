import os
import sys
import hashlib
import asyncio
from summoner.client import SummonerClient
from aioconsole import ainput
from valid_ts import compile_typescript_type, validate

TYPES_DIR = "types"


def hash_type(ts_content: str) -> int:
    """
    Computes a 128-bit integer hash from TypeScript content including comments.

    Args:
        ts_content (str): The full content of the TypeScript file.

    Returns:
        int: A 128-bit integer representing the hash.
    """
    md5_hash = hashlib.md5(ts_content.encode('utf-8')).digest()
    return int.from_bytes(md5_hash, byteorder='big')


async def load_and_compile_types():
    """
    Loads and compiles all TypeScript files in the types directory.

    Returns:
        dict: A mapping from type hashes (128-bit integers) to compiled types.
    """
    compiled_types = {}

    for filename in os.listdir(TYPES_DIR):
        if filename.endswith(".ts"):
            file_path = os.path.join(TYPES_DIR, filename)
            with open(file_path, 'r', encoding='utf-8') as file:
                ts_content = file.read()
                type_hash = hash_type(ts_content)
                success, compiled_type, error = compile_typescript_type(ts_content)
                if not success:
                    print(f"Error compiling {filename}: {error}")
                    continue
                compiled_types[type_hash] = compiled_type
                print(f"Loaded and compiled type '{compiled_type['name']}' with hash {type_hash}")

    return compiled_types


if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option="python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        msg_content = msg["content"] if isinstance(msg, dict) else msg
        tag = "\r[From server]" if msg_content.startswith("Warning:") else "\r[Received]"
        print(tag, msg_content, flush=True)
        print("r> ", end="", flush=True)

    @myagent.send(route="custom_send")
    async def custom_send():
        msg = await ainput("s> ")
        return msg
    
    @myagent.init()
    async def custom_init():
        print("Hello")

    async def main():
        compiled_types = await load_and_compile_types()

        # Example usage: Validate JSON input dynamically
        while True:
            input_json = await ainput("Enter JSON to validate: ")
            type_hash_input = await ainput("Enter type hash: ")

            try:
                type_hash_int = int(type_hash_input)
            except ValueError:
                print("Invalid hash. Must be a 128-bit integer.")
                continue

            if type_hash_int not in compiled_types:
                print("Type hash not found.")
                continue

            valid, error = validate(input_json, compiled_types[type_hash_int])

            if valid:
                print("Valid JSON.")
            else:
                print(f"Invalid JSON: {error}")

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    myagent.run(host="127.0.0.1", port=8888)
