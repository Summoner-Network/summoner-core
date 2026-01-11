from summoner.client import ClientMerger
import argparse
from pathlib import Path

from json_helper import save_dna_to_json

agent = ClientMerger(
    [
        {"dna_path": Path(__file__).resolve().parent / "question_spell_1_dna.json"},
        {"dna_path": Path(__file__).resolve().parent / "question_spell_2_dna.json"},
    ],
    name="Merged",
    allow_context_imports=True,
    verbose_context_imports=False,
)

flow = agent.flow().activate()
flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
agent.initiate_all()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    save_dna_to_json(agent.dna(include_context=True), "question_spell_merge_2_dna.json")

    agent.run(config_path=args.config_path)