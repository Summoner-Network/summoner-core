import argparse
import asyncio
import uuid
from pathlib import Path
from summoner.client import SummonerClient
from db_sdk import (
    configure_db_path,
    init_db,
    create_or_reset_state,
    get_state,
    get_active_agents,
    set_state_fields,
    add_history,
    show_statistics,
    start_negotiation_seller,
)
from summoner.protocol.triggers import Move, Stay, Test, Action
from summoner.protocol.process import Node, Direction
from typing import Union, Optional

# unique identifier for this seller
my_id = str(uuid.uuid4())

def print_statistics(agent: SummonerClient, stats: dict):
    agent_id = stats["agent_id"]
    rate = stats["rate"]
    successes = stats["successes"]
    total = stats["total"]
    last_txid = stats["last_txid"]
    agent.logger.info(
        f"\033[95m[{agent.name}] Agent {agent_id} — Success rate: "
        f"{rate:.2f}% ({successes}/{total}), Last TXID: {last_txid}\033[0m"
    )

agent = SummonerClient(name=f"SellerAgent-{my_id}")
flow = agent.flow().activate()

flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")

Trigger = flow.triggers()

async def setup():
    # configure a per-agent database file
    db_file = Path(__file__).resolve().parent / "db_files" / f"{agent.name}_{my_id[:10]}.db"
    configure_db_path(db_file)
    # initialize SQLite tables for this agent
    await init_db()

@agent.hook(direction=Direction.RECEIVE)
async def validate(msg: dict) -> Optional[dict]:
    if not isinstance(msg, dict) or "content" not in msg: return

    content   = msg["content"]

    if content.get("type") != "buying": return

    buyer_id  = content.get("from")
    seller_to = content.get("to")
    if not buyer_id or (seller_to is not None and seller_to != my_id): return

    # if content.get("status") in ["refuse_too", "accept_too"]:
    #     agent.logger.info(f"\033[90m[_too|CHECK:msg] {content}\033[0m")

    await create_or_reset_state(buyer_id)
    state = await get_state(buyer_id)

    # if content.get("status") in ["refuse_too", "accept_too"]:
    #     agent.logger.info(f"\033[90m[_too|CHECK:sta] {state}\033[0m")

    status = content.get("status")
    if not state["negotiation_active"] and status != "responding": return
    if state["negotiation_active"] and status == "responding": return

    if not state["negotiation_active"] and status == "responding":
        txid = await start_negotiation_seller(buyer_id)
        state = await get_state(buyer_id)
        agent.logger.info(
            f"\033[94m[{agent.name}|{my_id[:5]}] Started with {buyer_id[:10]}. "
            f"MIN={state['limit_acceptable_price']}, "
            f"OFFER={state['current_offer']}, TXID={txid}\033[0m"
        )

    txid   = content.get("TXID")
    if state["negotiation_active"] and txid is not None and txid != state["transaction_id"]:
        return
    
    agent.logger.info(content)
        
    return content
    
@agent.hook(direction=Direction.SEND)
async def sign(msg: Union[dict, str]) -> Optional[Union[dict, str]]:
    # agent.logger.info(f"[hook:send] sign {my_id[:5]}")
    if not isinstance(msg, dict): return
    msg.update({"from": my_id})
    return msg
    
@agent.upload_states()
async def upload(content):
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    current_states = {buyer_id: state["agreement"]}
    # agent.logger.info("[upload]", current_states)
    return current_states

@agent.download_states()
async def download(possible_states):
    agent.logger.info("[download]", possible_states)
    for buyer_id, state_options in possible_states.items():
        
        # Conclude
        if Node("end") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="none",
                            negotiation_active=0,
                            transaction_id=None
                        )
                            
        # Refuse
        elif Node("refuse_too") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="refuse_too",
                        )
        elif Node("refuse") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="refuse",
                        )

        # Accept   
        elif Node("accept_too") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="accept_too",
                        )
        elif Node("accept") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="accept",
                        )
        
        # Negotating
        elif Node("interested") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="interested",
                        )
        elif Node("none") in state_options:
            await set_state_fields(
                            buyer_id,
                            agreement="none",
                        )
        elif state_options != []:
            await set_state_fields(
                            buyer_id,
                            agreement="none",
                            negotiation_active=0,
                            transaction_id=None
                        )

@agent.receive(route="none --> interested")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    price  = content.get("price")
    if status == "offer":
        if price >= state["limit_acceptable_price"]:
            await set_state_fields(
                buyer_id,
                current_offer=price,
                agreement="interested"
            )
            agent.logger.info(f"\033[90m[{agent.name}|{my_id[:5]}] Interested in {buyer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        else:
            new_offer = state["current_offer"] - state["price_shift"]
            await set_state_fields(buyer_id, current_offer=new_offer)
            agent.logger.info(f"\033[90m[{agent.name}|{my_id[:5]}] Decreased for {buyer_id[:10]} at ${new_offer}\033[0m")
            return Stay(Trigger.ok)
    return Test(Trigger.exit)

@agent.receive(route="none --> accept")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    price  = content.get("price")
    if status == "interested":
        if price >= state["limit_acceptable_price"]:
            await set_state_fields(
                buyer_id,
                current_offer=price,
                agreement="accept"
            )
            agent.logger.info(f"\033[90m[{agent.name}|{my_id[:5]}] Will accept {buyer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
    return Test(Trigger.exit)

@agent.receive(route="none --> refuse")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    price  = content.get("price")
    if status == "interested" and price < state["limit_acceptable_price"]:
        await set_state_fields(
            buyer_id, 
            agreement="refuse"
        )
        agent.logger.info(f"\033[90m[{agent.name}|{my_id[:5]}] Will refuse {buyer_id[:10]} at ${price}\033[0m")
        return Move(Trigger.ok)
    return Test(Trigger.exit)
            
@agent.receive(route="interested --> accept_too")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    txid   = content.get("TXID")
    price  = content.get("price")
    if status == "accept" or (status == "interested" and state["current_offer"] == price >= state["limit_acceptable_price"]):
        added = await add_history(buyer_id, 1, txid)
        if added:
            stats = await show_statistics(buyer_id)
            print_statistics(agent, stats)
            await set_state_fields(buyer_id, agreement="accept_too")
            if status == "interested":
                return Move(Trigger.match)
            return Move(Trigger.ok)
        else:
            return Test(Trigger.error)
    return Test(Trigger.exit)
    
@agent.receive(route="interested --> refuse_too")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    status = content.get("status")
    txid   = content.get("TXID")
    if status == "refuse":
        added = await add_history(buyer_id, 0, txid)
        if added:
            stats = await show_statistics(buyer_id)
            print_statistics(agent, stats)
            await set_state_fields(buyer_id, agreement="refuse_too")
            return Move(Trigger.ok)
        else:
            return Test(Trigger.error)
    return Test(Trigger.exit)

@agent.receive(route="interested --> none, end")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    txid   = content.get("TXID")
    price  = content.get("price")
    if txid != state["transaction_id"]:
        return
    if status == "interested" and not(state["current_offer"] == price <= state["limit_acceptable_price"]):
        await set_state_fields(
                buyer_id,
                agreement="none",
                negotiation_active=0,
                transaction_id=None
            )
        return Move(Trigger.ok)
    return Test(Trigger.exit)

@agent.receive(route="accept --> none, end")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    txid   = content.get("TXID")
    if txid != state["transaction_id"]:
            return
    if status == "accept_too":
        added = await add_history(buyer_id, 1, txid)
        if added:
            stats = await show_statistics(buyer_id)
            print_statistics(agent, stats)
            await set_state_fields(
                buyer_id,
                agreement="none",
                negotiation_active=0,
                transaction_id=None
            )
            return Move(Trigger.ok)
        else:
            return Test(Trigger.error)
    return Test(Trigger.exit)
    
@agent.receive(route="refuse --> none, end")
async def handle_offer(content: dict) -> None:
    buyer_id  = content.get("from")
    state = await get_state(buyer_id)
    status = content.get("status")
    txid   = content.get("TXID")
    if txid != state["transaction_id"]:
            return
    if status == "refuse_too":
        added = await add_history(buyer_id, 0, txid)
        if added:
            stats = await show_statistics(buyer_id)
            print_statistics(agent, stats)
            await set_state_fields(
                buyer_id,
                agreement="none",
                negotiation_active=0,
                transaction_id=None
            )
            return Move(Trigger.ok)
        else:
            return Test(Trigger.error)
    return Test(Trigger.exit)

@agent.send(route="offer_response", multi=True)
async def make_offer() -> list[dict]:
    # brief pause before responding
    await asyncio.sleep(3)
    messages = []

    # gather all buyers still negotiating
    buyers = await get_active_agents()
    agent.logger.info("[sending ...]")

    for buyer_id in buyers:
        state = await get_state(buyer_id)
        offer = state["current_offer"]
        decision = state["agreement"]
        active = state["negotiation_active"]
        txid = state["transaction_id"]

        resp = {"from": None, "to": buyer_id, "type": "selling", "status": "offer", "price": None, "TXID": txid}
        
        if decision == "interested":
            agent.logger.info(f"\033[96m[{agent.name}|{my_id[:5]}] Interested by {buyer_id[:10]} at ${offer}\033[0m")
            resp.update({"status": "interested", "price": offer})
        elif decision.startswith("accept"):
            agent.logger.info(f"\033[92m[{agent.name}|{my_id[:5]}] Accept {buyer_id[:10]} at ${offer}\033[0m")
            resp.update({"status": decision, "price": offer})
            if decision == "accept_too":
                await set_state_fields(
                    buyer_id,
                    agreement="none",
                    negotiation_active=0,
                    transaction_id=None
                )
        elif decision.startswith("refuse"):
            agent.logger.info(f"\033[91m[{agent.name}|{my_id[:5]}] Refuse {buyer_id[:10]} at ${offer}\033[0m")
            resp.update({"status": decision, "price": offer})
            if decision == "refuse_too":
                await set_state_fields(
                    buyer_id,
                    agreement="none",
                    negotiation_active=0,
                    transaction_id=None
                )
        elif active:
            agent.logger.info(f"\033[93m[{agent.name}|{my_id[:5]}] Offer to {buyer_id[:10]} at ${offer}\033[0m")
            resp.update({"status": "offer", "price": offer})

        messages.append(resp)
    
    messages.append({"from": None, "to": None, "type": "selling", "status": "waiting", "TXID": None})
    
    return messages

@agent.send(route="none --> interested", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: none --> interested\033[0m")

@agent.send(route="none --> interested", on_actions={Action.STAY})
async def send_event() -> None:
    agent.logger.info("\033[94mSTAYED @ none\033[0m")

@agent.send(route="none --> accept", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: none --> accept\033[0m")

@agent.send(route="none --> refuse", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: none --> refuse\033[0m")

@agent.send(route="interested --> accept_too", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: interested --> accept_too\033[0m")

@agent.send(route="interested --> accept_too", on_triggers={Trigger.match})
async def send_event() -> None:
    agent.logger.info("\033[92mMATCH!\033[0m")

@agent.send(route="interested --> refuse_too", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: interested --> refuse_too\033[0m")

@agent.send(route="accept --> none, end", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: accept --> none, end\033[0m")
    
@agent.send(route="refuse --> none, end", on_actions={Action.MOVE})
async def send_event() -> None:
    agent.logger.info("\033[94mMOVED: refuse --> none, end\033[0m")

@agent.send(route="interested --> accept_too", on_triggers={Trigger.error})
async def send_event() -> None:
    agent.logger.info("\033[91mDOUBLE @ interested --> accept_too\033[0m")

@agent.send(route="interested --> accept_too", on_triggers={Trigger.error})
async def send_event() -> None:
    agent.logger.info("\033[91mDOUBLE @ interested --> accept_too\033[0m")

@agent.send(route="interested --> refuse_too", on_triggers={Trigger.error})
async def send_event() -> None:
    agent.logger.info("\033[91mDOUBLE @ interested --> refuse_too\033[0m")

@agent.send(route="accept --> none, end", on_triggers={Trigger.error})
async def send_event() -> None:
    agent.logger.info("\033[91mDOUBLE @ accept --> none, end\033[0m")
    
@agent.send(route="refuse --> none, end", on_triggers={Trigger.error})
async def send_event() -> None:
    agent.logger.info("\033[91mDOUBLE @ refuse --> none, end\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888, config_path=args.config_path)