import asyncio
import random
from pathlib import Path
from datetime import datetime
import aiosqlite
from summoner.client import SummonerClient
import uuid

state = {
    "transaction_ID": None,
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
                transaction_id TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        await db.commit()

async def add_history(success: int, transaction_id: str):
    db_path = get_db_path()
    timestamp = datetime.now().isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO history (success, transaction_id, timestamp) VALUES (?, ?, ?)",
            (success, transaction_id, timestamp)
        )
        await db.commit()

async def get_statistics():
    db_path = get_db_path()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM history WHERE success = 1") as cur:
            successes = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM history") as cur:
            total = (await cur.fetchone())[0]
    return successes, total

async def get_last_transaction_id():
    db_path = get_db_path()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT transaction_id FROM history ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None

async def show_statistics(self: SummonerClient):
    successes, total = await get_statistics()
    rate = (successes / total) * 100 if total else 0
    last_txid = await get_last_transaction_id()
    print(
        f"\n\033[96m[{self.name}] Negotiation success rate: "
        f"{rate:.2f}% ({successes} successes / {total} total)  "
        f"Last TXID: {last_txid}\033[0m"
    )

async def negotiation(self: SummonerClient):
    while True:
        async with state_lock:
            state["MIN_ACCEPTABLE_PRICE"] = random.randint(60, 90)
            state["PRICE_DECREMENT"] = random.randint(1, 5)
            state["current_offer"] = random.randint(state["MIN_ACCEPTABLE_PRICE"], 100)
            state["agreement"] = "none"
            state["negotiation_active"] = True
            state["transaction_ID"] = str(uuid.uuid4())
            print(
                f"\n\033[94m[{self.name}] New negotiation started. "
                f"MIN: {state['MIN_ACCEPTABLE_PRICE']}, MAX: {state['current_offer']}, "
                f"TXID: {state['transaction_ID']}\033[0m"
            )
        while True:
            async with state_lock:
                if not state["negotiation_active"]:
                    break
            await asyncio.sleep(0.2)

if __name__ == "__main__":
    agent = SummonerClient(name="SellerAgent")

    async def setup():
        global state_lock
        state_lock = asyncio.Lock()
        reset_db()           
        await init_db()      

        @agent.receive(route="offer_receipt")
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

            if content["status"] == "offer":
                offer = content["price"]
                async with state_lock:
                    if offer  >= state["MIN_ACCEPTABLE_PRICE"]:
                        state["current_offer"] = offer
                        state["agreement"] = "interested"
                        print(f"\033[90m[{agent.name}] Interested in offer at price {offer}!\033[0m")
                    else:
                        state["current_offer"] -= state["PRICE_DECREMENT"]
                        print(f"\033[90m[{agent.name}] Decreasing price at {state['current_offer']}!\033[0m")

            if content["status"] == "interested":
                
                async with state_lock:
                    if state["agreement"] in ["accept", "refuse"]:
                        return
                    
                offer = content['price']
                async with state_lock:
                    if offer >= state["MIN_ACCEPTABLE_PRICE"]:
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
                    await add_history(1, txid)
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                elif content["status"] == "accept" and state["agreement"] == "interested" and content["TXID"] == txid:
                    await add_history(1, txid)
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "accept_too"

            if content["status"].startswith("refuse"):
                async with state_lock:
                    txid = state["transaction_ID"]
                if content["status"] == "refuse_too" and state["agreement"] == "refuse"  and content["TXID"] == txid:
                    await add_history(0, txid)
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "none"
                        state["negotiation_active"] = False
                        state["transaction_ID"] = None
                elif content["status"] == "refuse" and state["agreement"] == "interested" and content["TXID"] == txid:
                    await add_history(0, txid)
                    await show_statistics(agent)
                    async with state_lock:
                        state["agreement"] = "refuse_too"


        @agent.send(route="offer_response")
        async def make_offer():
            await asyncio.sleep(3)

            async with state_lock:
                offer = state["current_offer"]
                decision = state["agreement"]
                in_talk = state["negotiation_active"]
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
            
            elif in_talk:
                print(f"\033[93m[{agent.name}] Offering price: {offer}\033[0m")
                return {"status": "offer", "price": offer, "TXID": txid}
            else:
                return {"status": "waiting", "TXID": None}

        agent.loop.create_task(negotiation(agent))

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888)
