status, body, _ = rpc("admin", "tools/call", {
    "name": "store-review-astra__list_products",
    "arguments": {"search": "Review Practice Ball"},
})
emit("Before approval", {"http": status, "total": body["result"]["structuredContent"]["total"]})
