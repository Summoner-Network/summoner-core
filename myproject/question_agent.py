import os
import sys
import asyncio

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client import SummonerClient

QUESTIONS = [
    "What is your name?",
    "What is the meaning of life?",
    "Do you like Rust or Python?",
    "How are you today?"
]
tracker = {"count": 0 }

if __name__ == "__main__":
    agent = SummonerClient(name="QuestionAgent", option="python")

    @agent.receive(route="")
    async def receive_response(msg):
        print(f"Received: {msg}")
        msg = (msg["content"] if isinstance(msg, dict) else msg)
        if msg != "waiting...":
            tracker["count"] += 1

    @agent.send(route="")
    async def send_question():
        await asyncio.sleep(2)
        return QUESTIONS[tracker["count"] % len(QUESTIONS)]

    agent.run(host="127.0.0.1", port=8888)