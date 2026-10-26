import concurrent.futures
import subprocess

started = time.monotonic()
command = ["bash", "scripts/gcp-vm.sh", "kube", "delete", "pod", "-n", "urumi-app",
           "-l", "app.kubernetes.io/component=supplier", "--wait=false", "--grace-period=0", "--force"]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    deletion = executor.submit(subprocess.run, command, cwd=ROOT, capture_output=True, text=True, timeout=25)
    for attempt in range(40):
        time.sleep(.15)
        result = rpc("bob", "tools/call", {"name": "supplier__list_products", "arguments": {}})
        if result[1].get("result", {}).get("isError"):
            emit("Supplier unavailable", {"after_seconds": round(time.monotonic() - started, 2), "reply": result[:2]})
            emit("Shipping while supplier unavailable", rpc("bob", "tools/call", {
                "name": "shipping__list_carriers", "arguments": {}})[:2])
            emit("Dashboard server state", {
                s["slug"]: {"status": s["status"], "last_error": s["last_error"]}
                for s in api("admin", "/api/servers")[1] if s["slug"] in ("supplier", "shipping")
            })
            break
    removed = deletion.result()
    emit("Pod deletion command", command)
    emit("Deletion", {"exit": removed.returncode, "stdout": removed.stdout, "stderr": removed.stderr})
for attempt in range(20):
    result = rpc("bob", "tools/call", {"name": "supplier__list_products", "arguments": {}})
    if not result[1].get("result", {}).get("isError"):
        emit("Supplier recovered", {"http": result[0], "after_seconds": round(time.monotonic() - started, 2)})
        break
    time.sleep(.5)
emit("Final server state", {
    s["slug"]: {"status": s["status"], "last_error": s["last_error"]}
    for s in api("admin", "/api/servers")[1] if s["slug"] in ("supplier", "shipping")
})
