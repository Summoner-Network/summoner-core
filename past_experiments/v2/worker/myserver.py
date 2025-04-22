import os
import sys
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
from summoner.server import SummonerServer

if __name__ == "__main__":
    
    myserver = SummonerServer(name="MyServer", option = "python")
    myserver.run(host = "127.0.0.1", port = 8888)