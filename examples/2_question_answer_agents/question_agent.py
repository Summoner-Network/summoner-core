from summoner.client import SummonerClient
from typing import Union
import asyncio
import argparse

QUESTIONS = [
    "What is your name?",
    "What is the meaning of life?",
    "Do you like Rust or Python?",
    "How are you today?"
]

tracker_lock = asyncio.Lock()
tracker = {"count": 0}

agent = SummonerClient(name="QuestionAgent")

@agent.receive(route="")
async def receive_response(msg: Union[str, dict]) -> None:
    content = msg["content"] if isinstance(msg, dict) else msg
    print(f"Received: {content}")
    if content != "waiting":
        async with tracker_lock:
            tracker["count"] += 1

@agent.send(route="")
async def send_question() -> str:
    await asyncio.sleep(2)
    return QUESTIONS[tracker["count"] % len(QUESTIONS)]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(host="127.0.0.1", port=8888, config_path=args.config_path)