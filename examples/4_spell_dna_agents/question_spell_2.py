from summoner.client import SummonerClient
from typing import Union
import argparse
import asyncio
from summoner.protocol.triggers import Move
from summoner.protocol.process import Node

from json_helper import save_dna_to_json

QUESTIONS = [
    "What is your name?",
    "What is the meaning of life?",
    "Do you like Rust or Python?",
    "How are you today?"
]

tracker_lock = asyncio.Lock()
tracker = 0
state = "spell"

agent = SummonerClient(name="QuestionSpell")

flow = agent.flow().activate()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")

Trigger = flow.triggers()

@agent.upload_states()
async def upload(msg):
    global state
    print("[upload]", state)
    return state

@agent.download_states()
async def download(possible_state):
    global state
    print("[download]", possible_state, state)
    if set(possible_state) == {Node("spell")}:
        state = "spell"
    if set(possible_state) == {Node("effect")}:
        state = "effect"
    print("[download] -->", state)

@agent.receive(route="effect --> spell")
async def receive_response(msg: Union[str, dict]) -> None:
    global tracker
    content = msg["content"] if isinstance(msg, dict) else msg
    print(f"Received[{tracker}]: {content}")
    if content != "waiting":
        async with tracker_lock:
            if tracker >= 6:
                await agent.travel_to(host = agent.default_host, port = agent.default_port)
                tracker = 0
                return Move(Trigger.ok)
            else:
                tracker += 1
                
@agent.send(route="effect")
async def send_question() -> str:
    global tracker
    await asyncio.sleep(2)
    async with tracker_lock:
        return QUESTIONS[tracker % len(QUESTIONS)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    save_dna_to_json(agent.dna(include_context=True), "question_spell_2_dna.json")

    agent.run(config_path=args.config_path)