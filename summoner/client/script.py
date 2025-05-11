import asyncio
from typing import Any, Optional, List
from lupa import lua52
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import base64
from urllib.parse import urlparse
import contextvars

CURRENT_DOMAIN = contextvars.ContextVar("CURRENT_DOMAIN", default="127.0.0.1")

class OutOfGasError(RuntimeError):
    """Raised when a script exceeds its instruction budget."""
    pass

class OutOfMemoryError(RuntimeError):
    """Raised when a script exceeds its memory budget."""
    pass

TOOLS = {
    "127.0.0.1": {
        "get_file": "code to load file",
    },
    "google.com": {
        "search": "code to search"
    },
    "microsoft.com": {
        "code": "code to code"
    }
}

STATE = {
    "domain": {"key": 3}
}

class LuaScriptRunner:
    def __init__(
        self,
        max_memory_kb: int = 10_240,
        instr_hook_period: int = 1000,
        timeout_s: Optional[float] = 40.0,
    ):
        self.max_memory_kb = max_memory_kb
        self.instr_hook_period = instr_hook_period
        self.timeout_s = timeout_s

    def _make_runtime(self) -> lua52.LuaRuntime:
        lua = lua52.LuaRuntime(unpack_returned_tuples=True, register_eval=False)
        lua.globals()["load_sandboxed"] = lambda code, env: lua.globals().load(code, "sandbox", "t", env)
        return lua

    def _make_sandbox_env(self, lua):
        env = lua.table()
        safe_builtins = [
            "assert", "error", "ipairs", "next", "pairs", "pcall", "select",
            "tonumber", "tostring", "type", "xpcall", "print"
        ]
        for name in safe_builtins:
            env[name] = lua.globals()[name]
        for lib in ["math", "string", "table", "utf8"]:
            env[lib] = lua.globals()[lib]

        def use_tool(domain: str, name: str, args: List[Any], fetch_opts=None):
            global TOOLS
            domain_tools = TOOLS.get(domain)
            if not domain_tools:
                raise ValueError(f"No tools registered for domain: {domain}")
            tool_code = domain_tools.get(name)
            if not tool_code:
                raise ValueError(f"No tool named '{name}' found for domain '{domain}'")
            runner = LuaScriptRunner()
            token = CURRENT_DOMAIN.set(domain)
            try:
                return asyncio.run(runner.run(tool_code, case_args=args))
            finally:
                CURRENT_DOMAIN.reset(token)

        def set_tool(name: str, code: str):
            global TOOLS
            domain = CURRENT_DOMAIN.get()
            if domain in TOOLS:
                TOOLS[domain][name] = code
            else:
                TOOLS[domain] = {name: code}

        def get_kv(key: str):
            domain = CURRENT_DOMAIN.get()
            if domain is None:
                raise RuntimeError("No domain context for get_kv")
            return STATE.setdefault(domain, {}).get(key)

        def set_kv(key: str, value: Any):
            domain = CURRENT_DOMAIN.get()
            if domain is None:
                raise RuntimeError("No domain context for set_kv")
            STATE.setdefault(domain, {})[key] = value

        env["http_request"] = http_request
        env["use_tool"] = use_tool
        env["set_tool"] = set_tool
        env["get_kv"] = get_kv
        env["set_kv"] = set_kv
        env["print"] = lambda *args: print("[sandbox]", *args)
        return env

    def _sync_run(
        self,
        script: str,
        init_args: List[Any],
        case: str,
        case_args: List[Any]
    ) -> Any:
        lua = self._make_runtime()
        env = self._make_sandbox_env(lua)

        chunk_loader = lua.globals().load(script, "sandboxed", "t", env)
        loaded_chunk = chunk_loader()

        try:
            run_fn = loaded_chunk["run"]
            if callable(run_fn):
                try:
                    run_fn.name = loaded_chunk["name"]
                except Exception:
                    pass
                return run_fn(*case_args)
        except Exception as e:
            print("⚠️ Failed to handle returned tool table:", e)

        raise TypeError("Lua script must return a function or a table with a callable 'run' field.")

    async def run(
        self,
        script: str,
        init_args: List[Any] = [],
        case: Optional[str] = None,
        case_args: Optional[List[Any]] = None
    ) -> Any:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, self._sync_run, script, init_args, case, case_args or [])
        return await asyncio.wait_for(fut, timeout=self.timeout_s)

        
def _lua_table_to_dict(obj):
    return {k: obj[k] for k in obj} if hasattr(obj, "__len__") and hasattr(obj, "__getitem__") else {}

def parse_http_request(raw, base_url=None):
    lines = raw.splitlines()
    if not lines:
        raise ValueError("Empty HTTP request")

    request_line = lines[0]
    method, path, _ = request_line.split()

    headers = {}
    body_start = False
    body_lines = []
    for line in lines[1:]:
        if body_start:
            body_lines.append(line)
        elif line.strip() == "":
            body_start = True
        else:
            key, val = line.split(":", 1)
            headers[key.strip()] = val.strip()

    body = "\n".join(body_lines)
    url = path if path.startswith("http") else urlparse(base_url)._replace(path=path).geturl()

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "data": body
    }

def http_request(opts):
    global TOOLS
    content_type = ""

    try:
        opts = _lua_table_to_dict(opts or {})
        method = opts.get("method", "GET").upper()
        url = opts.get("url")
        headers = _lua_table_to_dict(opts.get("headers"))
        params = _lua_table_to_dict(opts.get("params"))
        data = opts.get("data")
        json_data = opts.get("json")
        timeout = opts.get("timeout", 10)
        as_blob = opts.get("as_blob", False)

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
                json=json_data,
            )

        content_type = resp.headers.get("content-type", "")
        is_json = "application/json" in content_type
        is_text = any(t in content_type for t in ["text", "html", "xml"])
        is_code = content_type.startswith("text/lua")
        is_http = content_type.startswith("application/http")

        # Register Lua tool if needed
        if is_code:
            try:
                name = LuaScriptRunner().run(resp.text).name
                domain = urlparse(url).hostname
                if domain in TOOLS:
                    TOOLS[domain][name] = resp.text
                else:
                    TOOLS[domain] = {name: resp.text}
            except Exception:
                pass

        # Recursively resolve HTTP instructions
        if is_http:
            raw_body = resp.text
            try:
                inner_request = parse_http_request(raw_body, base_url=url)
                inner_response = http_request(inner_request)
                return {
                    "ok": True,
                    "mimetype": content_type,
                    "status": resp.status_code,
                    "statusText": resp.reason_phrase,
                    "url": str(resp.url),
                    "headers": dict(resp.headers),
                    "http_recursive": inner_response,
                    "error": None
                }
            except Exception as e:
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "statusText": "Invalid application/http",
                    "mimetype": content_type,
                    "url": str(resp.url),
                    "headers": dict(resp.headers),
                    "error": f"Failed to parse or recurse: {str(e)}"
                }

        # Normal return
        text_body = resp.text if is_text else None
        blob_data = base64.b64encode(resp.content).decode("utf-8") if as_blob else None

        return {
            "ok": 200 <= resp.status_code < 300,
            "mimetype": content_type,
            "status": resp.status_code,
            "statusText": resp.reason_phrase,
            "url": str(resp.url),
            "headers": dict(resp.headers),
            "text": text_body,
            "json": resp.json() if is_json else None,
            "blob": blob_data,
            "error": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "status": 0,
            "statusText": "NetworkError",
            "mimetype": content_type,
            "url": opts.get("url", ""),
            "headers": {},
            "text": None,
            "json": None,
            "blob": None,
            "error": str(e),
        }