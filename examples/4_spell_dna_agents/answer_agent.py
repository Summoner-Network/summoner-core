from summoner.client import SummonerClient
from typing import Union
import asyncio
import argparse

ANSWERS = {
    "What is your name?": "I am AnswerBot.",
    "What is the meaning of life?": "42.",
    "Do you like Rust or Python?": "Both have their strengths!",
    "How are you today?": "Functioning as expected."
}
track_lock = asyncio.Lock()
track_questions = {}

agent = SummonerClient(name="AnswerBot")

@agent.receive(route="effect")
async def handle_question(msg: Union[str, dict]) -> None:
    content = (msg["content"] if isinstance(msg, dict) else msg)
    addr = (msg["addr"] if isinstance(msg, dict) else "")
    print(f"Received: {content}")
    if content in ANSWERS:
        async with track_lock:
            track_questions[addr] = content

@agent.send(route="effect")
async def respond_to_question() -> str:
    await asyncio.sleep(3)
    async with track_lock:
        for k, q in track_questions.items():
            del track_questions[k]
            return ANSWERS[q]
    return "waiting"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(host = "testnet.summoner.org", port = 8888, config_path=args.config_path)