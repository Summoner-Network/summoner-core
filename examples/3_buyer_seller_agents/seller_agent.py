import os
import sys
import asyncio
import random

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client import SummonerClient

state = {
    "last_offer": None,
    "negotiation_active": True,
    "MIN_PRICE": None,
    "MAX_ACCEPTABLE_PRICE": None,
    "price_increment": None,
}

success_history = []
failure_history = []

async def show_statistics():
    total = len(success_history) + len(failure_history)
    success_rate = (len(success_history) / total) * 100 if total else 0
    print(f"\n\033[96m[SellerAgent] Negotiation success rate: {success_rate:.2f}% ({len(success_history)} successes / {total} total)\033[0m")

async def negotiation():
    while True:
        state["MIN_PRICE"] = random.randint(80, 100)
        state["MAX_ACCEPTABLE_PRICE"] = random.randint(110, 130)
        state["price_increment"] = random.randint(2, 5)

        state["last_offer"] = None
        state["negotiation_active"] = True

        print(f"\n\033[94m[SellerAgent] New negotiation started. MIN: {state['MIN_PRICE']}, MAX: {state['MAX_ACCEPTABLE_PRICE']}\033[0m")

        while state["negotiation_active"]:
            await asyncio.sleep(1)

if __name__ == "__main__":
    agent = SummonerClient(name="SellerAgent", option="python")

    @agent.receive(route="buyer_offer")
    async def handle_buyer_offer(msg):
        content = msg["content"]
        print(f"\033[92m[Seller received] {content}\033[0m")

        if content["status"] == "offer":
            state["last_offer"] = content["price"]

    @agent.send(route="offer_response")
    async def respond_to_offer():
        await asyncio.sleep(3)

        if state["last_offer"] is None:
            return {"status": "waiting"}

        buyer_offer = state["last_offer"]
        state["last_offer"] = None

        if buyer_offer >= state["MIN_PRICE"]:
            print(f"\033[96m[SellerAgent] Accepting offer: {buyer_offer}\033[0m")
            success_history.append(1)
            state["negotiation_active"] = False
            await show_statistics()
            return {"status": "accepted", "price": buyer_offer}

        counteroffer = min(state["MAX_ACCEPTABLE_PRICE"], buyer_offer + state["price_increment"])

        if counteroffer <= state["MAX_ACCEPTABLE_PRICE"]:
            print(f"\033[96m[SellerAgent] Countering with: {counteroffer}\033[0m")
            return {"status": "counteroffer", "price": counteroffer}

        print("\033[96m[SellerAgent] Terminating negotiation.\033[0m")
        failure_history.append(0)
        state["negotiation_active"] = False
        await show_statistics()
        return {"status": "terminate"}

    agent.loop.create_task(negotiation())
    agent.run(host="127.0.0.1", port=8888)
