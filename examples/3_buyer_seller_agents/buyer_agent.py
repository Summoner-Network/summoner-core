import asyncio
import random
from summoner.client import SummonerClient

state = {
    "transaction_ID": None,
    "current_offer": 0,
    "agreement": "none",
    "negotiation_active": False,
    "MAX_ACCEPTABLE_PRICE": 0,
    "PRICE_INCREMENT": 0,
}

state_lock = None
history = []
history_lock = None

async def show_statistics(self: SummonerClient):
    async with history_lock:
        successes = sum([x[0] for x in history])
        total = len(history)
        last_txid = history[-1][1]
    rate = (successes / total) * 100 if total else 0
    print(
        f"\n\033[96m[{self.name}] Negotiation success rate: "
        f"{rate:.2f}% ({successes} successes / {total} total)  "
        f"Last TXID: {last_txid}\033[0m"
    )

async def negotiation(self: SummonerClient):
    while True:
        async with state_lock:
            state["MAX_ACCEPTABLE_PRICE"] = random.randint(65, 80)
            state["PRICE_INCREMENT"] = random.randint(1, 5)
            state["current_offer"] = random.randint(1, state["MAX_ACCEPTABLE_PRICE"])
            state["agreement"] = "none"
            state["negotiation_active"] = True
            state["transaction_ID"] = None
            print(
                f"\n\033[94m[{self.name}] New negotiation started. "
                f"MIN: {state['current_offer']}, MAX: {state['MAX_ACCEPTABLE_PRICE']}, "
                f"TXID: {state['transaction_ID']}\033[0m"
            )
        while True:
            async with state_lock:
                if not state["negotiation_active"]:
                    break
            await asyncio.sleep(0.2)

if __name__ == "__main__":
    agent = SummonerClient(name="BuyerAgent")

    async def setup():
        global state_lock, history_lock
        state_lock = asyncio.Lock()
        history_lock = asyncio.Lock()

        @agent.receive(route="offer_response")
        async def handle_offer(msg):

            # ── ignore anything that isn't the JSON object we expect ──
            if not isinstance(msg, dict) or "content" not in msg:
                return
            content = msg["content"]
            print(f"[Received] {content}")

            async with state_lock:
                
                if not state["negotiation_active"]:
                    await asyncio.sleep(0.2)
                    return
                
                if state["transaction_ID"] is None:
                    state["transaction_ID"] = content["TXID"]

                elif state["transaction_ID"] != content["TXID"]:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                        await asyncio.sleep(0.2)
                        return

            if content["status"] == "offer":
                offer = content["price"]
                async with state_lock:
                    if offer <= state["MAX_ACCEPTABLE_PRICE"]:
                        state["current_offer"] = offer
                        state["agreement"] = "interested"
                        print(f"\033[90m[{agent.name}] Interested in offer at price {offer}!\033[0m")
                    else:
                        state["current_offer"] += state["PRICE_INCREMENT"]
                        print(f"\033[90m[{agent.name}] Increasing price at {state['current_offer']}!\033[0m")

            if content["status"] == "interested":
                
                async with state_lock:
                    if state["agreement"] in ["accept", "refuse"]:
                        return
                    
                offer = content['price']
                async with state_lock:
                    if offer <= state["MAX_ACCEPTABLE_PRICE"]:
                        state["current_offer"] = offer
                        state["agreement"] = "accept"
                        print(f"\033[90m[{agent.name}] Accepting offer at price {offer}!\033[0m")
                    else:
                        state["agreement"] = "refuse"
                        print(f"\033[90m[{agent.name}] Refusing offer at price {offer}!\033[0m")

            if content["status"].startswith("accept"):
                async with state_lock:
                    txid = state["transaction_ID"]
                if content["status"] == "accept_too" and state["agreement"] == "accept" and content["TXID"] == txid:
                    async with history_lock:
                        history.append((1, txid))
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                elif content["status"] == "accept" and state["agreement"] == "interested" and content["TXID"] == txid:
                    async with history_lock:
                        history.append((1, txid))
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "accept_too"

            if content["status"].startswith("refuse"):
                async with state_lock:
                    txid = state["transaction_ID"]
                if content["status"] == "refuse_too" and state["agreement"] == "refuse"  and content["TXID"] == txid:
                    async with history_lock:
                        history.append((0, txid))
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                elif content["status"] == "refuse" and state["agreement"] == "interested" and content["TXID"] == txid:
                    async with history_lock:
                        history.append((0, txid))
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "refuse_too"


        @agent.send(route="buyer_offer")
        async def make_offer():
            await asyncio.sleep(2)

            async with state_lock:
                offer = state["current_offer"]
                decision = state["agreement"]
                txid = state["transaction_ID"]

            if decision == "interested":
                print(f"\033[96m[{agent.name}] Interested in offer: {offer}\033[0m")
                return {"status": "interested", "price": offer, "TXID": txid}
            
            elif decision.startswith("accept"):
                print(f"\033[92m[{agent.name}] Accepted offer: {offer}\033[0m")
                if decision == "accept_too":
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                return {"status": decision, "price": offer, "TXID": txid}
            
            elif decision.startswith("refuse"):
                print(f"\033[91m[{agent.name}] Refused offer: {offer}\033[0m")
                if decision == "refuse_too":
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                return {"status": decision, "price": offer, "TXID": txid}
            
            else:
                print(f"\033[93m[{agent.name}] Offering price: {offer}\033[0m")
                return {"status": "offer", "price": offer, "TXID": txid}

        agent.loop.create_task(negotiation(agent))

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888)

