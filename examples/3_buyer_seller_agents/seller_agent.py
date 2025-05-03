import os
import sys
import asyncio
import random
from summoner.client import SummonerClient

state = {
    "current_offer": 1000,
    "agreement": "none",
    "negotiation_active": False,
    "MIN_ACCEPTABLE_PRICE": 1000,
    "PRICE_DECREMENT": 0,
}

state_lock = None
history = []
history_lock = None

async def show_statistics(self: SummonerClient):
    async with history_lock:
        success_num = sum(history)
        total = len(history)
    success_rate = (success_num / total) * 100 if total else 0
    print(f"\n\033[96m[{self.name}] Negotiation success rate: {success_rate:.2f}% ({success_num} successes / {total} total)\033[0m")

async def negotiation(self: SummonerClient):
    while True:
        async with state_lock:
            state["MIN_ACCEPTABLE_PRICE"] = random.randint(60, 90)
            state["PRICE_DECREMENT"] = random.randint(1, 5)
            state["current_offer"] = random.randint(state["MIN_ACCEPTABLE_PRICE"], 100)
            state["agreement"] = "none"
            state["negotiation_active"] = True
            print(f"\n\033[94m[{self.name}] New negotiation started. MIN: {state['MIN_ACCEPTABLE_PRICE']}, MAX: {state['current_offer']}\033[0m")
        while True:
            async with state_lock:
                still_negotiating = state["negotiation_active"]
            if not still_negotiating:
                break
            await asyncio.sleep(1)

if __name__ == "__main__":
    agent = SummonerClient(name="SellerAgent", option="python")

    async def setup():
        global state_lock, history_lock
        state_lock = asyncio.Lock()
        history_lock = asyncio.Lock()

        @agent.receive(route="buyer_offer")
        async def handle_buyer_offer(msg):
            content = msg["content"]
            print(f"[Received] {content}")

            async with state_lock:
                renew = content["status"] in ["offer"] and state["agreement"] in ["accept","refuse"]
                if renew:
                    state["agreement"] = "none"
                    state["negotiation_active"] = False
                    await asyncio.sleep(1)

            if content["status"] == "offer":
                offer = content['price']
                async with state_lock:
                    if offer >= state["MIN_ACCEPTABLE_PRICE"]:
                        state["current_offer"] = offer
                        state["agreement"] = "interested"
                        print(f"\033[90m[{agent.name}] Interested in offer at price {offer}!\033[0m")
                    else:
                        state["current_offer"] -= state["PRICE_DECREMENT"]
                        print(f"\033[90m[{agent.name}] Decreasing price at {state['current_offer']}!\033[0m")

            if content["status"] == "interested":
                offer = content['price']
                async with state_lock:
                    if offer >= state["MIN_ACCEPTABLE_PRICE"]:
                        state["current_offer"] = offer
                        state["agreement"] = "accept"
                        print(f"\033[90m[{agent.name}] Accepting offer at price {offer}!\033[0m")
                    else:
                        state["agreement"] = "refuse"
                        print(f"\033[90m[{agent.name}] Refusing offer at price {offer}!\033[0m")

            if content["status"] == "accept":
                async with state_lock:
                    if state["agreement"] == "accept":
                        async with history_lock:
                            history.append(1)
                        await show_statistics(agent)
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                    elif state["agreement"] == "interested" and content['price'] == state["current_offer"]: #check it is same transaction
                        async with history_lock:
                            history.append(1)
                        await show_statistics(agent)
                        state["agreement"] = "accept"
                    else:
                        state["agreement"] = "none"
   
            if content["status"] == "refuse":
                async with state_lock:
                    if state["agreement"] == "refuse":
                        async with history_lock:
                            history.append(0)
                        await show_statistics(agent)
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                    elif state["agreement"] == "interested" and content['price'] == state["current_offer"]: #check it is same transaction
                        async with history_lock:
                            history.append(0)
                        await show_statistics(agent)
                        state["agreement"] = "refuse"
                    else:
                        state["agreement"] = "none"


        @agent.send(route="offer_response")
        async def respond_to_offer():
            await asyncio.sleep(3)
            
            async with state_lock:
                offer = state["current_offer"]
                decision = state["agreement"]
                in_talk = state["negotiation_active"]

            if decision == "interested":
                print(f"\033[96m[{agent.name}] Interested in offer: {offer}\033[0m")
                return {"status": "interested", "price": offer}
            elif decision == "accept":
                print(f"\033[92m[{agent.name}] Accepted offer: {offer}\033[0m")
                return {"status": "accept", "price": offer}
            elif decision == "refuse":
                print(f"\033[91m[{agent.name}] Refused offer: {offer}\033[0m")
                return {"status": "refuse", "price": offer}
            elif in_talk:
                print(f"\033[93m[{agent.name}] Offering price: {offer}\033[0m")
                return {"status": "offer", "price": offer}
            else:
                return {"status": "waiting"}
                
        agent.loop.create_task(negotiation(agent))

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888)
