from summoner.client import SummonerClient
from aioconsole import ainput
from typing import Union
import argparse

myagent = SummonerClient(name="ChatAgent")

@myagent.receive(route="custom_receive")
async def custom_receive(msg: Union[dict,str]) -> None:
    msg = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
    tag = ("\r[From server]" if isinstance(msg, str) and msg[:len("Warning:")] == "Warning:" else "\r[Received]")
    print(tag, msg, flush=True)
    if msg == "/travel":
        await myagent.travel_to(host = "192.168.1.229", port = 8888)
        return None
    elif msg == "/quit":
        await myagent.quit()
        return None
    elif msg == "/go_home":
        await myagent.travel_to(host = myagent.default_host, port = myagent.default_port)
        return None
    print("r> ", end="", flush=True)

@myagent.send(route="custom_send")
async def custom_send() -> str:
    msg = await ainput("s> ")
    if msg == "/self.travel":
        await myagent.travel_to(host = "192.168.1.229", port = 8888)
        return None
    elif msg == "/self.quit":
        await myagent.quit()
        return None
    elif msg == "/self.go_home":
        await myagent.travel_to(host = myagent.default_host, port = myagent.default_port)
        return None
    else:
        return msg

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    myagent.run(config_path=args.config_path)