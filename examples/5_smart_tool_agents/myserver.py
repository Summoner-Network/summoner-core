import os
import sys
import argparse
from summoner.server import SummonerServer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SummonerServer with a specified config.")
    parser.add_argument('--config', dest='config_path', required=True, help='The relative path to the config file (JSON) for the server (e.g., --config myproject/server_config.json)')
    args = parser.parse_args()

    myserver = SummonerServer(name="MyServer")
    myserver.run(config_path=args.config_path)
