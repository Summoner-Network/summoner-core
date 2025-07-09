import os
import sys
from typing import (
    Union, 
    get_type_hints, 
    get_origin, 
    get_args,
    )
import inspect
target_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if target_path not in sys.path:
    sys.path.insert(0, target_path)
from logger import Logger



def hook_priority_order(priority: tuple) -> tuple:
    
    if priority != () and isinstance(priority[0], str):
        # SDK priority: sort first, e.g., ('kobold', 0) → (0, 'kobold', 0)
        return (0, priority)
    else:
        # User priority: sort after SDK, e.g., (0, 1) → (1, 0, 1)
        return (1, priority)

def _normalize_annotation(raw):
    """
    Turn raw signature annotations into real types:
      - inspect.Signature.empty  → None
      - literal None              → type(None)
      - forward-refs, etc.        → resolved via get_type_hints
    """
    if raw is inspect.Signature.empty:
        return None
    if raw is None:
        return type(None)
    return raw

def _valid_type_hint(hint, allowed: tuple[type, ...]) -> bool:
    """
    True if `hint` is one of `allowed`, or a Union[...] thereof.
    """
    if hint in allowed:
        return True
    origin = get_origin(hint)
    if origin is Union:
        return all(_valid_type_hint(arg, allowed) for arg in get_args(hint))
    return False

def _check_param_and_return(fn,
                            decorator_name: str,
                            allow_param: tuple[type, ...],
                            allow_return: tuple[type, ...],
                            logger: Logger):
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)

    # parameter check
    params = list(sig.parameters.values())

    expected_params = 1 if decorator_name in ("@hook", "@receive", "@upload_states", "@download_states") else 0
    
    if len(params) != expected_params:
        raise TypeError(f"{decorator_name} '{fn.__name__}' must have "
                        f"{expected_params} parameter(s), not {len(params)}")

    if expected_params == 1:
        raw_param = params[0].annotation
        param_hint = _normalize_annotation(raw_param) or hints.get(params[0].name, None)
        if param_hint is None:
            logger.warning(
                f"{decorator_name} '{fn.__name__}' missing parameter annotation; skipping type check"
            )
        elif not _valid_type_hint(param_hint, allow_param):
            raise TypeError(
                f"{decorator_name} '{fn.__name__}' parameter should be one of {allow_param}, "
                f"not {param_hint!r}"
            )

    # return check
    raw_ret = sig.return_annotation
    ret_hint = _normalize_annotation(raw_ret) or hints.get('return', None)
    if raw_ret is inspect.Signature.empty:
        logger.warning(
            f"{decorator_name} '{fn.__name__}' missing return annotation; skipping type check"
        )
    elif not _valid_type_hint(ret_hint, allow_return):
        raise TypeError(
            f"{decorator_name} '{fn.__name__}' must return one of {allow_return}, not {ret_hint!r}"
        )