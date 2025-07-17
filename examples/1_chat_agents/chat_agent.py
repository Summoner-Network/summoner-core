from summoner.client import SummonerClient
from aioconsole import ainput
from typing import Union
import argparse

agent = SummonerClient(name="ChatAgent")

@agent.receive(route="custom_receive")
async def custom_receive(msg: Union[dict,str]) -> None:
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    print(tag, content, flush=True)
    if content == "/travel":
        await agent.travel_to(host = "testnet.summoner.org", port = 8888)
        return None
    elif content == "/quit":
        await agent.quit()
        return None
    elif content == "/go_home":
        await agent.travel_to(host = agent.default_host, port = agent.default_port)
        return None
    print("r> ", end="", flush=True)

@agent.send(route="custom_send")
async def custom_send() -> str:
    content = await ainput("s> ")
    if content == "/self.travel":
        await agent.travel_to(host = "testnet.summoner.org", port = 8888)
        return None
    elif content == "/self.quit":
        await agent.quit()
        return None
    elif content == "/self.go_home":
        await agent.travel_to(host = agent.default_host, port = agent.default_port)
        return None
    else:
        return content

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(config_path=args.config_path)