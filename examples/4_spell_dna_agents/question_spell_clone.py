from summoner.client import ClientTranslation
import argparse
import json
from summoner.protocol.process import Node
from question_spell import agent as agent_tmp

dna_list = json.loads(agent_tmp.dna())
agent = ClientTranslation(dna_list, name="TranslatedSpell", var_name="agent")

state = "spell"

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