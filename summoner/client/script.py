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

# TODO: Make it possible for the lua code to get the identity of the running agent

SCAN_CYCLES = {}

async def execute_scan_cycle(domain, entry):
    while True:
        await asyncio.sleep(entry["interval"])
        try:
            script = TOOLS[domain][entry["tool"]]
            await LuaScriptRunner().run(script, entry.get("args", []))
        except Exception as e:
            print(f"[scan_cycle] Error executing '{entry['tool']}' for domain '{domain}': {e}")

def register_scan_cycle(params):
    domain = CURRENT_DOMAIN.get()
    interval = params.get("interval", 1000) / 1000.0  # convert to seconds
    tool_name = params.get("name", "anonymous")
    args = params.get("args", [])
    entry = {"tool": tool_name, "interval": interval, "args": args}

    task = asyncio.create_task(execute_scan_cycle(domain, entry))
    if domain in SCAN_CYCLES:
        SCAN_CYCLES[domain][tool_name] = task
    else:
        SCAN_CYCLES[domain] = {tool_name: task}

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
        env["scan_cycle"] = register_scan_cycle
        return env

    def _sync_run(
        self,
        script: str,
        args: List[Any]
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
                return run_fn(*args)
        except Exception as e:
            print("⚠️ Failed to handle returned tool table:", e)

        raise TypeError("Lua script must return a function or a table with a callable 'run' field.")

    async def run(
        self,
        script: str,
        case_args: Optional[List[Any]] = None
    ) -> Any:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, self._sync_run, script, case_args or [])
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

    if isinstance(opts, dict):
        parsed_opts = opts
    else:
        parsed_opts = _lua_table_to_dict(opts or {})

    method = parsed_opts.get("method", "GET").upper()
    url = parsed_opts.get("url")

    headers = parsed_opts.get("headers")
    if not isinstance(headers, dict):
        headers = _lua_table_to_dict(headers)

    params = parsed_opts.get("params")
    if not isinstance(params, dict):
        params = _lua_table_to_dict(params)

    data = parsed_opts.get("data")
    json_data = parsed_opts.get("json")
    timeout = parsed_opts.get("timeout", 10)
    as_blob = parsed_opts.get("as_blob", False)

    content_type = ""

    try:
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

        response = {
            "ok": 200 <= resp.status_code < 300,
            "status": resp.status_code,
            "statusText": resp.reason_phrase,
            "mimetype": content_type,
            "url": str(resp.url),
            "headers": dict(resp.headers),
            "text": None,
            "json": None,
            "blob": None,
            "error": None,
        }

        is_json = "application/json" in content_type
        is_text = any(t in content_type for t in ["text", "html", "xml"])
        is_code = content_type.startswith("text/lua")
        is_http = content_type.startswith("application/http")

        if is_code:
            try:
                lua_runner = LuaScriptRunner()
                script_meta = asyncio.run(lua_runner.run(resp.text))
                domain = urlparse(url).hostname
                tool_name = getattr(script_meta, 'name', None)

                if tool_name:
                    if domain in TOOLS:
                        TOOLS[domain][tool_name] = resp.text
                    else:
                        TOOLS[domain] = {tool_name: resp.text}
            except Exception as e:
                response["error"] = f"Failed to register Lua tool: {str(e)}"

        if is_http:
            try:
                inner_request = parse_http_request(resp.text, base_url=url)
                return http_request(inner_request)
            except Exception as e:
                response.update({
                    "ok": False,
                    "statusText": "Invalid application/http",
                    "error": f"Failed to parse or recurse: {str(e)}"
                })
                return response

        if is_text:
            response["text"] = resp.text

        if is_json:
            try:
                response["json"] = resp.json()
            except ValueError:
                response["json"] = None

        if as_blob:
            response["blob"] = base64.b64encode(resp.content).decode("utf-8")

        return response

    except httpx.RequestError as e:
        return {
            "ok": False,
            "status": 0,
            "statusText": "NetworkError",
            "mimetype": content_type,
            "url": url or "",
            "headers": {},
            "text": None,
            "json": None,
            "blob": None,
            "error": str(e),
        }
