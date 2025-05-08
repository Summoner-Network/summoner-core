import os
import sys
import asyncio
import random
from pathlib import Path
from datetime import datetime
import aiosqlite
from summoner.client import SummonerClient

state = {
    "current_offer": 1000,
    "agreement": "none",
    "negotiation_active": False,
    "MIN_ACCEPTABLE_PRICE": 1000,
    "PRICE_DECREMENT": 0,
}

state_lock = None

def get_db_path():
    agent_dir = Path(__file__).resolve().parent
    return agent_dir / "negotiation_history.db"

def reset_db():
    db_path = get_db_path()
    if db_path.exists():
        db_path.unlink()

async def init_db():
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                success INTEGER NOT NULL CHECK (success IN (0, 1)),
                timestamp TEXT NOT NULL
            )
        """)
        await db.commit()

async def add_history(success: int):
    db_path = get_db_path()
    timestamp = datetime.now().isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO history (success, timestamp) VALUES (?, ?)",
            (success, timestamp)
        )
        await db.commit()

async def get_statistics():
    db_path = get_db_path()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM history WHERE success = 1") as cursor:
            success_num = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM history") as cursor:
            total = (await cursor.fetchone())[0]
    return success_num, total

async def show_statistics(self: SummonerClient):
    success_num, total = await get_statistics()
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
        global state_lock
        state_lock = asyncio.Lock()
        reset_db()           
        await init_db()      

        @agent.receive(route="buyer_offer")
        async def handle_buyer_offer(msg):
            content = msg["content"]
            print(f"[Received] {content}")

            async with state_lock:
                renew1 = content["status"] in ["offer", "interested"] and state["agreement"] in ["accept_too", "refuse_too"]
                renew2 = content["status"] in ["offer"] and state["agreement"] in ["accept", "refuse"]
                if renew1 or renew2:
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

            if content["status"][:len("accept")] == "accept":
                async with state_lock:
                    if content["status"] == "accept_too" and state["agreement"] == "accept":
                        await add_history(1)
                        await show_statistics(agent)
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                    elif content["status"] == "accept" and state["agreement"] == "interested" and content['price'] == state["current_offer"]:
                        await add_history(1)
                        await show_statistics(agent)
                        state["agreement"] = "accept_too"
                    else:
                        state["agreement"] = "none"

            if content["status"][:len("refuse")] == "refuse":
                async with state_lock:
                    if content["status"] == "refuse_too" and state["agreement"] == "refuse":
                        await add_history(0)
                        await show_statistics(agent)
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                    elif content["status"] == "refuse" and state["agreement"] == "interested" and content['price'] == state["current_offer"]:
                        await add_history(0)
                        await show_statistics(agent)
                        state["agreement"] = "refuse_too"
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
            elif decision[:len("accept")] == "accept":
                print(f"\033[92m[{agent.name}] Accepted offer: {offer}\033[0m")
                return {"status": decision, "price": offer}
            elif decision[:len("refuse")] == "refuse":
                print(f"\033[91m[{agent.name}] Refused offer: {offer}\033[0m")
                return {"status": decision, "price": offer}
            elif in_talk:
                print(f"\033[93m[{agent.name}] Offering price: {offer}\033[0m")
                return {"status": "offer", "price": offer}
            else:
                return {"status": "waiting"}

        agent.loop.create_task(negotiation(agent))

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888)
