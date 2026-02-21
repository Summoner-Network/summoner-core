"""
A client's registered behavior can be serialized into a JSON string ("DNA").
The types and functions that support this are defined here.

Purpose
-------
DNA is intended to support:
    1) cloning: rehydrate a client from data by re-evaluating handler sources
    2) merging: combine multiple clients into a composite client (ClientMerger)

Portability rule
----------------
DNA is meant to be replayable across environments. Unstable runtime bindings
(for example '__main__' imports or live objects that cannot be rebuilt) should
end up in "missing", not embedded implicitly.
"""
#pylint:disable=wrong-import-position

from typing import Any, Optional, Set, Type, TypedDict

import os
import sys

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from summoner.protocol.flow import Flow
from summoner.utils.code_handlers import get_callable_source
from summoner.protocol.triggers import Signal
from summoner.client.client_types import DOWNLOAD_TYPE, RECEIVE_DECORATED_TYPE, SEND_DECORATED_TYPE, HOOK_TYPE, UPLOAD_TYPE
from summoner.protocol.process import Direction


class DNAHook(TypedDict):
    """
    The sort of information kept in a SummonerClient's _dna_hooks list
    """
    fn: HOOK_TYPE
    direction: Direction
    priority: tuple[int, ...]
    source: Optional[str]

def hook_entry_contribution(hook_entry: DNAHook) -> dict[str, Any]:
    """
    The contribution of this entry to the overall DNA dict.
    """
    fn = hook_entry["fn"]
    return {
        "type": "hook",
        "direction": hook_entry["direction"].name,
        "priority": hook_entry["priority"],
        "source": get_callable_source(fn, hook_entry.get("source")),
        "module": fn.__module__,
        "fn_name": fn.__name__,
    }

class DNAReceiver(TypedDict):
    """
    The sort of information kept in a SummonerClient's _dna_receivers list
    """
    fn: RECEIVE_DECORATED_TYPE
    route: str
    priority: tuple[int, ...]
    source: Optional[str]

def receiver_entry_contribution(receiver_entry: DNAReceiver, flow_in_use: Optional[Flow]) -> dict[str, Any]:
    """
    The contribution of this entry to the overall DNA dict.
    """
    fn = receiver_entry["fn"]
    raw_route = receiver_entry["route"]
    
    try:
        if flow_in_use is not None:
            route_key = str(flow_in_use.parse_route(raw_route))
        else:
            route_key = raw_route
    except Exception:
        route_key = raw_route
    route_key = "".join(str(route_key).split())

    return {
        "type": "receive",
        "route": raw_route, # original route string
        "route_key": route_key, # stable route representative
        "priority": receiver_entry["priority"],
        "source": get_callable_source(fn, receiver_entry.get("source")),
        "module": fn.__module__,
        "fn_name": fn.__name__,
    }

class DNASender(TypedDict):
    """
    The sort of information kept in a SummonerClient's _dna_senders list
    """
    fn: SEND_DECORATED_TYPE
    route: str
    multi: bool
    on_triggers: Optional[Set[Any] | Set[Signal]]
    on_actions: Optional[set[Any] | Set[Type]]
    source: Optional[str]

def sender_entry_contribution(sender_entry: DNASender, flow_in_use: Optional[Flow]) -> dict[str, Any]:
    """
    The contribution of this entry to the overall DNA dict.
    """
    fn = sender_entry["fn"]
    raw_route = sender_entry["route"]

    try:
        if flow_in_use is not None:
            route_key = str(flow_in_use.parse_route(raw_route))
        else:
            route_key = raw_route
    except Exception:
        route_key = raw_route
    route_key = "".join(str(route_key).split())

    return {
        "type": "send",
        "route": raw_route,          # original route string
        "route_key": route_key,     # stable route representative
        "multi": sender_entry["multi"],
        # Serialize triggers/actions by name so they can be re-resolved later.
        "on_triggers": [t.name for t in (sender_entry["on_triggers"] or [])],
        "on_actions": [a.__name__ for a in (sender_entry["on_actions"] or [])],
        "source": get_callable_source(fn, sender_entry.get("source")),
        "module": fn.__module__,
        "fn_name": fn.__name__,
    }

class DNA_UPLOAD(TypedDict):
    """
    The sort of information kept in a SummonerClient's _dna_upload_states entry
    """
    fn: UPLOAD_TYPE
    source: str
    
def upload_entry_contribution(upload_entry: DNA_UPLOAD) -> dict[str, Any]:
    """
    The contribution of this entry to the overall DNA dict.
    """
    return {
        "type": "upload_states",
        "source": get_callable_source(upload_entry["fn"], upload_entry["source"]),
        "module": upload_entry["fn"].__module__,
        "fn_name": upload_entry["fn"].__name__,
    }

class DNA_DOWNLOAD(TypedDict):
    """
    The sort of information kept in a SummonerClient's _dna_download_states entry
    """
    fn: DOWNLOAD_TYPE
    source: str

def download_entry_contribution(download_entry: DNA_DOWNLOAD) -> dict[str, Any]:
    """
    The contribution of this entry to the overall DNA dict.
    """
    return {
        "type": "download_states",
        "source": get_callable_source(download_entry["fn"], download_entry["source"]),
        "module": download_entry["fn"].__module__,
        "fn_name": download_entry["fn"].__name__,
    }
