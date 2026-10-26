import subprocess

payload = {
    "base": BASE,
    "fixed": key("bob", "rate-fixed-confirm"),
    "variant": key("bob", "rate-variant-confirm"),
}
result = subprocess.run(
    [str(ROOT / "services/gateway/.venv/bin/python"), str(ROOT / "docs/review/rate_variants_client.py")],
    input=json.dumps(payload),
    capture_output=True,
    text=True,
    timeout=40,
)
emit("exit", result.returncode)
emit("stdout", result.stdout)
emit("stderr", result.stderr)
