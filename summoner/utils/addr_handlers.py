from collections.abc import Iterable, Mapping
from typing import Any
import ipaddress

def format_addr(addr: Any, max_items: int = 10) -> str:
    """
    Convert a socket peer address (or similar) to a compact, deterministic string.

    Behavior:
      - IPv4 (host, port) -> "host:port"
      - IPv6 (host, port[, flowinfo, scopeid]) -> "[host%scopeid]:port"  (scopeid only if nonzero)
      - bytes-like -> UTF-8 decoded with replacement
      - str -> returned as-is
      - Mapping -> "{k=v, ...}" with keys sorted, truncated to max_items
      - Other Iterable -> "[a,b,c]" truncated to max_items
      - None -> "None"
      - Fallback -> str(addr)

    Parameters
    ----------
    addr : Any
        Value from e.g. writer.get_extra_info("peername") or similar.
    max_items : int
        Max items to include when formatting mappings/iterables.

    Returns
    -------
    str
        A stable, human-readable representation suitable for logs.
    """
    if addr is None:
        return "None"

    # bytes-like
    if isinstance(addr, (bytes, bytearray, memoryview)):
        return bytes(addr).decode("utf-8", errors="replace")

    # string-like
    if isinstance(addr, str):
        return addr

    # mapping
    if isinstance(addr, Mapping):
        items = sorted(addr.items(), key=lambda kv: str(kv[0]))
        items = items[:max_items]
        body = ", ".join(f"{k}={v}" for k, v in items)
        suffix = ", ..." if len(addr) > max_items else ""
        return "{" + body + suffix + "}"

    # generic iterable (list/tuple/set/generator, etc.), but not strings/bytes
    if isinstance(addr, Iterable):
        try:
            seq = list(addr)
        except Exception:
            return repr(addr)

        # Common socket cases
        if len(seq) >= 2 and isinstance(seq[0], (str, bytes)) and isinstance(seq[1], int):
            host = seq[0].decode() if isinstance(seq[0], (bytes, bytearray, memoryview)) else seq[0]
            port = seq[1]
            scopeid = seq[3] if len(seq) >= 4 and isinstance(seq[3], int) else None
            try:
                ip = ipaddress.ip_address(host)
                if ip.version == 6:
                    host_fmt = host if scopeid in (None, 0) else f"{host}%{scopeid}"
                    return f"[{host_fmt}]:{port}"
                else:
                    return f"{host}:{port}"
            except ValueError:
                # Not an IP literal; fall back to host:port
                return f"{host}:{port}"

        # Generic iterable formatting
        shown = seq[:max_items]
        body = ",".join(map(str, shown))
        suffix = ",..." if len(seq) > max_items else ""
        return f"[{body}{suffix}]"

    # fallback
    try:
        return str(addr)
    except Exception:
        return repr(addr)
