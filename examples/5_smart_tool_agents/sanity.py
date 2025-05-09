from lupa import LuaRuntime
import httpx

def http_request(args):
    print("Called from Lua with args:", args)
    method = args.get("method", "GET")
    url = args["url"]
    headers = args.get("headers", {})
    timeout = args.get("timeout", 10)

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        resp = client.request(method, url, headers=headers)
        return resp.text

lua = LuaRuntime(unpack_returned_tuples=True)

lua_code = '''
function do_query(http_request)
    print("http_request is", type(http_request))
    local result = http_request({
        method = "GET",
        url = "https://httpbin.org/get",
        headers = {
            ["User-Agent"] = "LuaBot"
        }
    })
    return result
end
'''

# Load and get the function
lua.execute(lua_code)
lua_func = lua.globals().do_query

# Pass the Python function in *as an argument*
result = lua_func(http_request)
print("Result:", result)