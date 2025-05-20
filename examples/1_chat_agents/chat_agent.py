from summoner.client import SummonerClient
from aioconsole import ainput

if __name__ == "__main__":
    myagent = SummonerClient(name="ChatAgent", option = "python")

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        msg = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg) 
        tag = ("\r[From server]" if isinstance(msg, str) and msg[:len("Warning:")] == "Warning:" else "\r[Received]")
        print(tag, msg, flush=True)
        print("r> ", end="", flush=True)

    @myagent.send(route="custom_send")
    async def custom_send():
        msg = await ainput("s> ")
        return msg

    myagent.run(host = "127.0.0.1", port = 8888)