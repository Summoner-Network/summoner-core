"""
Types used for client and client DNA
"""
#pylint:disable=wrong-import-position, invalid-name
from typing import (
Dict,
List,
Optional,
Callable,
TypedDict,
Union,
Coroutine,
)
from typing import Any
import os
import sys

target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
from summoner.protocol.process import Node
from summoner.protocol.triggers import Event
from summoner.protocol.payload import RelayedMessage

#pylint:disable=pointless-string-statement
"""
See the _check_param_and_return_types function in client.py
inside the decorator internal functions for where these types are actually enforced.
This tells us what the expected signatures of client hooks, upload functions,
and decorated send/receive handlers are.
That way we can put those expected signatures in one place here. Though
the actual enforcement of these types is in client.py, not here.
"""

HOOK_ARGUMENT = Any | str | Dict[Any,Any]
HOOK_RETURN = None | str | Dict[Any,Any] | Any
HOOK_TYPE = Callable[[HOOK_ARGUMENT], Coroutine[Any,Any,HOOK_RETURN]]

UPLOAD_RETURN = Union[None | str | Any | Node | List[Any] | Dict[Any,Any]\
    | List[str] | Dict[str, str] | Dict[str, List[str]]\
    | List[Node] | Dict[str, Node] | Dict[str, List[Node]]\
    | Dict[str, Union[str, list[str]]]\
    | Dict[str, Union[Node, list[Node]]]\
    | Dict[str, Union[str, list[str], Node, list[Node]]]
]
UPLOAD_ARGUMENT = None | Any | str | Dict[Any,Any] | TypedDict
UPLOAD_TYPE = Callable[[UPLOAD_ARGUMENT], Coroutine[Any,Any,UPLOAD_RETURN]]

DOWNLOAD_TYPE = Callable[[Any], Coroutine[Any,Any,Optional[Any]]]

SENDING_HOOKS_TYPE = Callable[
    [Optional[Union[str, dict]]],
    Coroutine[Any,Any,Optional[Union[str, dict]]]]
RECEIVING_HOOKS_TYPE = Callable[
    [Optional[Union[str, dict, RelayedMessage]]],
    Coroutine[Any,Any,Optional[Union[str, dict]]]]

SEND_RETURN_SINGLE_TYPE = None | Any | str | Dict[Any,Any]
SEND_RETURN_MULTI_TYPE = Any | List[Any] | List[str] | \
    List[Dict[Any,Any]] | List[Union[str, Dict[Any,Any]]]
SEND_DECORATED_TYPE = Callable[[], Coroutine[Any, Any, SEND_RETURN_SINGLE_TYPE]] |\
    Callable[[], Coroutine[Any, Any, SEND_RETURN_MULTI_TYPE]]

RECEIVE_DECORATED_TYPE = Callable[
    [Any | str | Dict[Any,Any]],
    Coroutine[Any, Any, Any | Event | None]
]
