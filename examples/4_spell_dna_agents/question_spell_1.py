from summoner.client import SummonerClient
from typing import Union
import argparse
import asyncio
from summoner.protocol.triggers import Move
from summoner.protocol.process import Node


state = "spell"
is_back = False

agent = SummonerClient(name="QuestionSpell")

flow = agent.flow().activate()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
flow.ready()

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
    global is_back
    msg = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
    tag = ("\r[From server]" if isinstance(msg, str) and msg[:len("Warning:")] == "Warning:" else "\r[Received]")
    print(tag, msg, flush=True)
    if msg == "/travel":
        await agent.travel_to(host = "testnet.summoner.org", port = 8888)
        is_back = True
        return Move(Trigger.ok)
    elif msg == "/quit":
        await agent.quit()
        return None
    elif msg == "/go_home":
        await agent.travel_to(host = agent.default_host, port = agent.default_port)
        is_back = True
        return None
    print("waiting for instructions... ", flush=True)
         

@agent.send(route="spell")
async def send_question() -> str:
    global is_back
    if is_back and agent.host == agent.default_host and agent.port == agent.default_port:
        is_back = False
        return "I am back"
    else:
        await asyncio.sleep(0.1)
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(config_path=args.config_path)