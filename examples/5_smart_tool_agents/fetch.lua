local function fetch(url)
    local response = http_request({
        method = "GET",
        url = url,
        headers = {
            ["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            ["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
            ["Accept-Language"] = "en-US,en;q=0.9",
            ["Referer"] = "https://www.google.com/",
            ["Connection"] = "keep-alive"
        },
        timeout = 10
    })

    if response.error then
        return "Error: " .. response.error
    end

    return response.text
end

return {
    name = "fetch",
    run = fetch
}
