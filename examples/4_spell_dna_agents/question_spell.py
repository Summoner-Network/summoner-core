from summoner.client import SummonerClient
from typing import Union
import argparse
import asyncio
from summoner.protocol.triggers import Move
from summoner.protocol.process import Node

import json
from pathlib import Path

def save_dict_to_json(data: dict, filename: str) -> None:
    path = Path(__file__).resolve().parent / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

QUESTIONS = [
    "What is your name?",
    "What is the meaning of life?",
    "Do you like Rust or Python?",
    "How are you today?"
]

tracker_lock = asyncio.Lock()
tracker = 0
state = "spell"
is_back = False

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
        
@agent.receive(route="spell --> effect")
async def custom_receive(msg: Union[dict,str]) -> None:
    msg = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
    tag = ("\r[From server]" if isinstance(msg, str) and msg[:len("Warning:")] == "Warning:" else "\r[Received]")
    print(tag, msg, flush=True)
    if msg == "/travel":
        await agent.travel_to(host = "testnet.summoner.org", port = 8888)
        return Move(Trigger.ok)
    elif msg == "/quit":
        await agent.quit()
        return None
    elif msg == "/go_home":
        await agent.travel_to(host = agent.default_host, port = agent.default_port)
        return None
    print("waiting for instructions... ", flush=True)

@agent.receive(route="effect --> spell")
async def receive_response(msg: Union[str, dict]) -> None:
    global tracker, is_back
    content = msg["content"] if isinstance(msg, dict) else msg
    print(f"Received[{tracker}]: {content}")
    if content != "waiting":
        async with tracker_lock:
            if tracker >= 10:
                await agent.travel_to(host = agent.default_host, port = agent.default_port)
                tracker = 0
                is_back = True
                return Move(Trigger.ok)
            else:
                tracker += 1
                

@agent.send(route="effect")
async def send_question() -> str:
    global tracker
    await asyncio.sleep(2)
    async with tracker_lock:
        return QUESTIONS[tracker % len(QUESTIONS)]
    
@agent.send(route="spell")
async def send_question() -> str:
    global is_back
    if is_back:
        is_back = False
        return "I am back"
    else:
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    save_dict_to_json(json.loads(agent.dna(include_context=True)), "question_spell_dna.json")

    agent.run(config_path=args.config_path)