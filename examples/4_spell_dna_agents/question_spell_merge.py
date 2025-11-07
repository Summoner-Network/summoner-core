from summoner.client import SummonerClient, ClientMerger
from aioconsole import ainput
from typing import Union
import argparse
import asyncio
from summoner.protocol.triggers import Move, Stay, Test, Action
from summoner.protocol.process import Node, Direction

from question_spell_1 import agent as agent_1
from question_spell_2 import agent as agent_2

state = "spell"

merge_dicts = [
  {"var_name": "agent", "client": agent_1},
  {"var_name": "agent", "client": agent_2},
]

agent = ClientMerger(merge_dicts, name="Merged")

flow = agent.flow().activate()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")

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
 
agent.initiate_hooks()
agent.initiate_receivers()
agent.initiate_senders()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(config_path=args.config_path)