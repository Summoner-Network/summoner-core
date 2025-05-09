import asyncio
from typing import Any, Optional, List
from lupa import lua52
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin

class OutOfGasError(RuntimeError):
    """Raised when a script exceeds its instruction budget."""
    pass

class OutOfMemoryError(RuntimeError):
    """Raised when a script exceeds its memory budget."""
    pass

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

        # Inject safe global functions into the sandbox env only — not globals
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

        # Your approved helper
        env["http_request"] = http_request

        # Optional: make 'print' safer or redirectable
        env["print"] = lambda *args: print("[sandbox]", *args)

        return env

    def _sync_run(
        self,
        script: str,
        tool_args: List[Any]
    ) -> Any:
        lua = self._make_runtime()
        env = self._make_sandbox_env(lua)

        # Load script in sandbox
        chunk_loader = lua.globals().load(script, "sandboxed", "t", env)
        loaded_chunk = chunk_loader()

        if not callable(loaded_chunk):
            raise TypeError("Lua script did not return a function.")

        return loaded_chunk(*tool_args)

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

def http_request(method: str, url: str, opts):
    try:
        opts = _lua_table_to_dict(opts or {})
        headers = _lua_table_to_dict(opts.get("headers"))
        params = _lua_table_to_dict(opts.get("params"))
        data = opts.get("data")
        json_data = opts.get("json")
        timeout = opts.get("timeout", 10)
        evolve = opts.get("evolve", False)

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                data=data,
                json=json_data,
            )
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "text": resp.text if not evolve else evolve_html(resp.text, url),
            "json": resp.json() if "application/json" in resp.headers.get("content-type", "") else None,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}

def evolve_html(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    styles = []

    for link_tag in soup.find_all("link", rel="stylesheet"):
        href = link_tag.get("href")
        if href:
            full_url = urljoin(base_url, href)
            try:
                r = httpx.get(full_url, timeout=5)
                if r.status_code == 200:
                    styles.append(r.text)
            except Exception as e:
                print(f"Failed to fetch style from {full_url}: {e}")
        link_tag.decompose()

    if styles:
        style_tag = soup.new_tag("style")
        style_tag.string = "\n".join(styles)
        soup.head.append(style_tag)

    return str(soup)