from summoner.client import ClientMerger
import argparse

from question_spell_1 import agent as agent_1
from question_spell_2 import agent as agent_2

from json_helper import save_dna_to_json

agent = ClientMerger([agent_1, agent_2], name="Merged")

flow = agent.flow().activate()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
agent.initiate_all()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    save_dna_to_json(agent.dna(include_context=True), "question_spell_merge_1_dna.json")

    agent.run(config_path=args.config_path)