import os
import sys
import asyncio
from summoner.client import SummonerClient

ANSWERS = {
    "What is your name?": "I am AnswerBot.",
    "What is the meaning of life?": "42.",
    "Do you like Rust or Python?": "Both have their strengths!",
    "How are you today?": "Functioning as expected."
}
track_lock = asyncio.Lock()
track_questions = {}

if __name__ == "__main__":
    agent = SummonerClient(name="AnswerBot", option="python")

    @agent.receive(route="")
    async def handle_question(msg):
        print(f"Received: {msg}")
        msg = (msg["content"] if isinstance(msg, dict) else msg)
        addr = (msg["addr"] if isinstance(msg, dict) else "")
        if msg in ANSWERS:
            async with track_lock:
                track_questions[addr] = msg

    @agent.send(route="")
    async def respond_to_question():
        await asyncio.sleep(3)
        async with track_lock:
            for k, q in track_questions.items():
                del track_questions[k]
                yield ANSWERS[q]
                return
        yield "waiting"

    agent.run(host="127.0.0.1", port=8888)