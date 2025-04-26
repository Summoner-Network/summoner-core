import os
import sys
import asyncio
import random

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.client import SummonerClient

state = {
    "current_offer": None,
    "agreement": False,
    "negotiation_active": True,
    "MAX_PRICE": None,
    "MIN_ACCEPTABLE_PRICE": None,
    "price_decrement": None,
}

success_history = []
failure_history = []

async def show_statistics():
    total = len(success_history) + len(failure_history)
    success_rate = (len(success_history) / total) * 100 if total else 0
    print(f"\n\033[96m[BuyerAgent] Negotiation success rate: {success_rate:.2f}% ({len(success_history)} successes / {total} total)\033[0m")

async def negotiation():
    while True:
        state["MAX_PRICE"] = random.randint(80, 90)
        state["MIN_ACCEPTABLE_PRICE"] = random.randint(65, 75)
        state["price_decrement"] = random.randint(3, 7)

        state["current_offer"] = state["MAX_PRICE"]
        state["agreement"] = False
        state["negotiation_active"] = True

        print(f"\n\033[94m[BuyerAgent] New negotiation started. MAX: {state['MAX_PRICE']}, MIN: {state['MIN_ACCEPTABLE_PRICE']}\033[0m")

        while state["negotiation_active"]:
            await asyncio.sleep(1)

if __name__ == "__main__":
    agent = SummonerClient(name="BuyerAgent", option="python")

    @agent.receive(route="offer_response")
    async def handle_seller_response(msg):
        content = msg["content"]
        print(f"\033[92m[Buyer received] {content}\033[0m")

        if content["status"] == "accepted":
            print(f"[BuyerAgent] Agreement reached at price {content['price']}!")
            success_history.append(1)
            state["agreement"] = True
            state["negotiation_active"] = False
            await show_statistics()
        elif content["status"] == "counteroffer":
            counter = content["price"]
            if state["MIN_ACCEPTABLE_PRICE"] <= counter <= state["current_offer"]:
                state["current_offer"] = counter
            else:
                state["current_offer"] -= state["price_decrement"]

    @agent.send(route="buyer_offer")
    async def make_offer():
        await asyncio.sleep(2)

        if state["agreement"]:
            return {"status": "done"}

        if state["current_offer"] < state["MIN_ACCEPTABLE_PRICE"]:
            print("\033[92m[BuyerAgent] Cannot negotiate further.\033[0m")
            failure_history.append(0)
            state["negotiation_active"] = False
            await show_statistics()
            return {"status": "terminate"}

        offer = state["current_offer"]
        print(f"\033[92m[BuyerAgent] Offering price: {offer}\033[0m")
        return {"status": "offer", "price": offer}

    agent.loop.create_task(negotiation())
    agent.run(host="127.0.0.1", port=8888)
