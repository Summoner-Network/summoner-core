import aiosqlite
from pathlib import Path
from datetime import datetime
import random
import uuid

# Module-level database path; set via configure_db_path()
_DB_PATH: Path = None

def configure_db_path(path: Path):
    """
    Configure the SQLite file path for this agent.
    """
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    """
    Return the configured DB path, or default under this module.
    """
    if _DB_PATH:
        base = Path(_DB_PATH)
        base.parent.mkdir(parents=True, exist_ok=True)
        return base
    base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "negotiation_history.db"

async def init_db():
    """
    Create 'state' and 'history' tables if they do not exist,
    and ensure history(agent_id, txid) is unique.
    """
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        # state table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS state (
                agent_id TEXT PRIMARY KEY,
                transaction_id TEXT,
                current_offer REAL,
                agreement TEXT,
                negotiation_active INTEGER,
                limit_acceptable_price REAL,
                price_shift REAL
            )
        """)
        
        # history table (without uniqueness on agent_id+txid)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT,
                success INTEGER CHECK(success IN (0,1)),
                txid TEXT,
                timestamp TEXT
            )
        """)
        
        # now enforce one row per (agent_id, txid)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_history_agent_tx
            ON history(agent_id, txid)
        """)
        
        await db.commit()

async def create_or_reset_state(agent_id: str):
    """
    Ensure a 'state' row exists for this agent; do not overwrite existing negotiations.
    """
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        # only insert default row if none exists
        await db.execute(
            """
            INSERT OR IGNORE INTO state (
                agent_id, transaction_id, current_offer,
                agreement, negotiation_active,
                limit_acceptable_price, price_shift
            ) VALUES (?, NULL, 0, 'none', 0, 0, 0)
            """,
            (agent_id,)
        )
        await db.commit()

async def set_state_fields(agent_id: str, **fields):
    """
    Update arbitrary fields for a agent_id in 'state'.
    """
    if not fields:
        return
    columns = ", ".join(f"{col} = ?" for col in fields.keys())
    params = list(fields.values()) + [agent_id]
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute(f"UPDATE state SET {columns} WHERE agent_id = ?", params)
        await db.commit()

async def get_state(agent_id: str) -> dict:
    """
    Return the state row as a dict, or None if missing.
    """
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        cur = await db.execute(
            "SELECT transaction_id, current_offer, agreement, negotiation_active,"
            " limit_acceptable_price, price_shift"
            " FROM state WHERE agent_id = ?",
            (agent_id,)
        )
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "transaction_id": row[0],
        "current_offer": row[1],
        "agreement": row[2],
        "negotiation_active": bool(row[3]),
        "limit_acceptable_price": row[4],
        "price_shift": row[5],
    }

async def get_active_agents() -> list[str]:
    """
    Return list of agent_ids with an active negotiation.
    """
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        cur = await db.execute(
            "SELECT agent_id FROM state WHERE negotiation_active = 1"
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]

async def add_history(agent_id: str, success: int, txid: str) -> bool:
    """
    Insert one record into 'history' unless (agent_id,txid) already exists.
    Returns False if this insertion was skipped (duplicate), False if a new row was inserted.
    """
    timestamp = datetime.now().isoformat()
    db_file = get_db_path()

    async with aiosqlite.connect(str(db_file)) as db:
        cur = await db.execute(
            """
            INSERT INTO history (agent_id, success, txid, timestamp)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id, txid) DO NOTHING
            """,
            (agent_id, success, txid, timestamp)
        )
        await db.commit()

        # rowcount == 1 → inserted; rowcount == 0 → skipped
        added = (cur.rowcount != 0)
        return added

async def get_history(agent_id: str) -> list:
    """
    Return list of (success, txid) for this agent.
    """
    db_file = get_db_path()
    async with aiosqlite.connect(str(db_file)) as db:
        cur = await db.execute(
            "SELECT success, txid FROM history WHERE agent_id = ? ORDER BY id",
            (agent_id,)
        )
        return await cur.fetchall()

async def show_statistics(agent_id: str):
    """
    Print success rate and last txid for agent.
    """
    history = await get_history(agent_id)
    total = len(history)
    successes = sum(s for s,_ in history)
    last_txid = history[-1][1] if total else None
    rate = (successes/total*100) if total else 0
    return {
        "agent_id": agent_id,
        "rate": rate, 
        "successes": successes, 
        "total": total, 
        "last_txid": last_txid
        }

async def start_negotiation_seller(agent_id: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    min_p = random.randint(60, 90)
    dec   = random.randint(1, 5)
    curr  = random.randint(min_p, 100)
    txid  = str(uuid.uuid4())
    await set_state_fields(
        agent_id,
        limit_acceptable_price=min_p,
        price_shift=dec,
        current_offer=curr,
        agreement="none",
        negotiation_active=1,
        transaction_id=txid,
    )
    return txid

async def start_negotiation_buyer(agent_id: str, txid: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    max_p = random.randint(65, 80)
    inc   = random.randint(1, 5)
    curr  = random.randint(1, max_p)
    await set_state_fields(
        agent_id,
        limit_acceptable_price=max_p,
        price_shift=inc,
        current_offer=curr,
        agreement="none",
        negotiation_active=1,
        transaction_id=txid
    )