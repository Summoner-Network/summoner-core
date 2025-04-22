import os
import sys
import argparse

# Add parent directory to sys.path
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.server import SummonerServer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SummonerServer with a specified option.")
    parser.add_argument('--option', dest='option', required=True, help='The option for the server (e.g., --option rust)')
    args = parser.parse_args()

    myserver = SummonerServer(name="MyServer", option=args.option)
    myserver.run(host="127.0.0.1", port=8888)
