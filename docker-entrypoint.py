#!/usr/bin/env python3
"""Container entry-point for the Summoner SPLT server.

Reads:
  • SUMMONER_CONFIG - path to server_config.json
  • SPLT_HOST / SPLT_PORT - optional override of bind address
  • SUMMONER_NAME - logger/server tag (default: 'splt')
"""

import os
from summoner.server.server import SummonerServer

def main() -> None:
    cfg   = os.getenv("SUMMONER_CONFIG", "/app/templates/server_config.json")
    host  = os.getenv("SPLT_HOST", "0.0.0.0")
    port  = int(os.getenv("SPLT_PORT", "8888"))
    name  = os.getenv("SUMMONER_NAME", "splt")

    SummonerServer(name).run(host=host, port=port, config_path=cfg)

if __name__ == "__main__":  # pragma: no cover
    main()
