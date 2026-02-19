"""
TODO: doc payload
"""
#pylint:disable=line-too-long
import json
from json import JSONDecodeError
from typing import Any, Tuple, Dict, List, Union, TypedDict

from summoner.utils import (
    fully_recover_json,
    remove_last_newline,
    ensure_trailing_newline,
    )

# Current envelope version
WRAPPER_VERSION = "0.0.1"

# Registries for versioned parsers and casters
envelope_parsers: Dict[str, Any] = {}
envelope_casters: Dict[str, Any] = {}

def register_envelope_version(
    version: str, parser: Any, caster: Any
) -> None:
    """
    Register a parser/caster pair for a given envelope version.

    Args:
        version: Version string identifier for this envelope format.
        parser:  Function that takes an object and returns (payload, type_tree).
        caster:  Function that takes (payload, type_tree) and returns typed payload.
    """
    envelope_parsers[version] = parser
    envelope_casters[version]  = caster

STR_TYPE = "str"
BOOL_TYPE = "bool"
INT_TYPE = "int"
NUMB_TYPE = "float"
NULL_TYPE = "null"


#pylint:disable=too-many-return-statements
def parse_v0_0_1(obj: Any) -> Tuple[Any, Any]:
    """
    Walk `obj` and build a parallel `type_tree`.  Every leaf in `obj`
    becomes a simple type name; every list/dict is recursed.

    Now supports all JSON-serializable Python types:
      • str, bool, int, float, None
      • list of the above
      • dict with string keys and above as values

    Other types (e.g. custom objects) will be coerced into strings.
    """
    # Primitives → type names
    if isinstance(obj, str):
        return obj, STR_TYPE
    if isinstance(obj, bool):
        return obj, BOOL_TYPE
    if isinstance(obj, int):
        return obj, INT_TYPE
    if isinstance(obj, float):
        return obj, NUMB_TYPE
    if obj is None:
        return None, NULL_TYPE

    # Lists → walk each element
    if isinstance(obj, list):
        payloads: List[Any] = []
        types:    List[Any] = []
        for v in obj:
            p, t = parse_v0_0_1(v)
            payloads.append(p)
            types.append(t)
        return payloads, types

    # Dicts → walk each value
    if isinstance(obj, dict):
        payloads: Dict[str, Any] = {}
        types:    Dict[str, Any] = {}
        for k, v in obj.items():
            p, t = parse_v0_0_1(v)
            payloads[k] = p
            types[k]    = t
        return payloads, types

    # Fallback for anything else: stringify it
    # and record its type as "string"
    s = str(obj)
    return s, STR_TYPE


#pylint:disable=too-many-return-statements, too-many-branches
def cast_v0_0_1(val: Any, expected: Any) -> Any:
    """
    Coerce `val` according to `expected`, but never fail on unknown types.
    Instead, pass the value through unchanged if we don't understand.

    Supported expected descriptors:
      • "string", "boolean", "integer", "number", "null"
      • a list of descriptors  → cast element-wise up to len(expected)
      • a dict of descriptors → cast known keys, keep extras unchanged
      • None or unknown      → identity (return val)
    """
    # 1) Identity for missing/unknown descriptors
    if expected is None:
        return val

    # 2) Primitives
    if expected == STR_TYPE:
        return str(val)
    if expected == BOOL_TYPE:
        # bool(None) or bool("") is False, but we allow that
        return bool(val)
    if expected == INT_TYPE:
        try:
            return int(val)
        except (ValueError, TypeError):
            return val
    if expected == NUMB_TYPE:
        try:
            return float(val)
        except (ValueError, TypeError):
            return val
    if expected == NULL_TYPE:
        # always map to Python None
        return None

    # 3) Lists: cast up to the expected length, keep extras untouched
    if isinstance(expected, list) and isinstance(val, list):
        out_list = []
        for i, item in enumerate(val):
            if i < len(expected):
                out_list.append(cast_v0_0_1(item, expected[i]))
            else:
                out_list.append(item)
        return out_list

    # 4) Dicts: cast keys we know, pass through extra keys
    if isinstance(expected, dict) and isinstance(val, dict):
        out_dict: Dict[str, Any] = {}
        # First, keys described in expected
        for k, exp in expected.items():
            if k in val:
                out_dict[k] = cast_v0_0_1(val[k], exp)
        # Next, any additional keys just get copied
        for k, v in val.items():
            if k not in expected:
                out_dict[k] = v
        return out_dict

    # 5) Unknown descriptor → return the raw value
    return val


# Register version 0.0.1
register_envelope_version("0.0.1", parse_v0_0_1, cast_v0_0_1)
register_envelope_version("1.0.0", parse_v0_0_1, cast_v0_0_1)
register_envelope_version("1.0.1", parse_v0_0_1, cast_v0_0_1)
register_envelope_version("1.1.0", parse_v0_0_1, cast_v0_0_1)
register_envelope_version("1.1.1", parse_v0_0_1, cast_v0_0_1)


def wrap_with_types(
    payload: Any,
    version: str = WRAPPER_VERSION
) -> str:
    """
    Wrap `payload` in a self-describing JSON envelope with type metadata.

    Envelope keys:
      - "_version": envelope version identifier
      - "_payload": the original payload structure
      - "_type":    parallel structure of type names

    Args:
        payload: Any JSON-serializable object (dict, list, primitive).
        version: Version string for the envelope format (defaults to WRAPPER_VERSION).

    Returns:
        A JSON string representing the typed envelope.

    Raises:
        ValueError: if the specified version is not registered.
    """
    parser = envelope_parsers.get(version)
    if parser is None:
        raise ValueError(f"Unsupported wrapper version: {version}")
    payload_data, type_tree = parser(payload)
    envelope: Dict[str, Any] = {
        "_version": version,
        "_payload": payload_data,
        "_type":    type_tree,
    }
    return ensure_trailing_newline(json.dumps(envelope))


class RelayedMessage(TypedDict):
    """
    Outer transport wrapper added by the relay server for peer-to-peer messages.

    This structure is not authored by the sender. The relay injects `remote_addr`
    to identify the origin of the message, while `content` carries the original
    payload as sent by the peer.

    `content` may be:
      - a raw string (warnings, logs, non-JSON messages), or
      - a JSON object, optionally a versioned typed envelope
        (see wrap_with_types / recover_with_types).
    """
    remote_addr: str
    content: Union[str, dict]


def recover_with_types(text: str) -> RelayedMessage:
    """
    Recover and validate a typed payload from a server message.

    Expects `text` to be JSON of the shape:
      {"remote_addr": <sender>, "content": <typed-envelope>}

    If `content` contains our versioned envelope (keys:
    "_version", "_payload", "_type"), this function:
      1. Looks up the registered caster for `_version`.
      2. Coerces each field in `_payload` per `_type`.
      3. Returns a dict: {"remote_addr": <sender>, "content": <typed_payload>}.

    Fallbacks (in order):
      - Invalid JSON or non-JSON warning strings: strip trailing newline and return raw string.
      - Missing outer keys ("remote_addr"/"content"): strip newline and return raw string.
      - Missing envelope keys inside content: return the parsed `{"remote_addr":…, "content":…}` as-is.

    Args:
        text: Raw text received from the server (may include newline).

    Returns:
        A dict with typed "remote_addr" and "content", or a plain string message.

    Raises:
        ValueError: if the outer wrapper is malformed, or the envelope version is unsupported.
    """
    # 1) First, try to recursively recover any nested JSON blobs
    #    This handles cases where content arrives as a JSON-encoded string.
    try:
        obj = fully_recover_json(text)
    except (ValueError, JSONDecodeError):
        # If that fails (e.g. pure warning string), strip newline and relay the raw text
        return remove_last_newline(text)

    # 2) Ensure we have the outer {"remote_addr":…, "content":…} wrapper
    if not (isinstance(obj, dict) and "remote_addr" in obj and "content" in obj):
        # Malformed protocol message; upstream code can catch this if needed
        # raise ValueError("Unsupported message format from server.")
        return obj

    addr    = obj["remote_addr"]
    content = obj["content"]

    # 3) If `content` isn't our typed envelope, just return what we parsed
    #    (content may be a nested dict or any other JSON-serializable structure)
    if not (
        isinstance(content, dict)
        and "_version" in content
        and "_payload" in content
        and "_type" in content
    ):
        return obj

    # 4) We have the versioned envelope—now look up the correct caster
    version = content["_version"]
    caster  = envelope_casters.get(version, envelope_casters[WRAPPER_VERSION])
    if caster is None:
        # Unknown version: hard error so we don't silently mis-interpret data
        raise ValueError(f"Unsupported wrapper version: {version}")

    # 5) Apply the caster to coerce each field per its declared type
    typed_payload = caster(content["_payload"], content["_type"])

    # 6) Return exactly the shape callers expect:
    #    { "remote_addr": <sender_addr>, "content": <fully-typed-payload> }
    return {"remote_addr": addr, "content": typed_payload}
