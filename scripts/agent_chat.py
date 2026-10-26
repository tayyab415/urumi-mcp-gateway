"""Chat with the dashboard agent as one of the seeded team members, from a script.

    cd services/gateway && uv run python ../../scripts/agent_chat.py --as bob "what orders came in today?"
    ... --as ana --writes approve "post a restock note to Slack"      # approve held writes
    ... --as bob --new "start over"                                   # forget the saved conversation

It signs in the way the dashboard does (the person's API key as the bearer credential),
keeps the conversation id between calls, answers the approval card by itself according to
--writes (approve | deny, default deny), and prints one compact line per event.
The keys come from ~/.urumi/team-credentials.json, written by `python -m gateway.seed --keys`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx2

CREDS = Path.home() / ".urumi" / "team-credentials.json"
STATE = Path("/tmp/urumi-agent-chat")


def short(value: object, n: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("message")
    p.add_argument("--as", dest="who", required=True, help="bob, chen, dev, ana, or a full email")
    p.add_argument("--writes", choices=["approve", "deny"], default="deny", help="what to answer when a held write asks for approval")
    p.add_argument("--new", action="store_true", help="start a new conversation")
    p.add_argument("--chat", default="http://localhost:8200", help="chat service, or the cluster ingress (http://127.0.0.1:18081 through the tunnel)")
    p.add_argument("--host", help="Host header to send, for the cluster ingress (e.g. app.urumi.test:18081)")
    p.add_argument("--creds", type=Path, default=CREDS)
    args = p.parse_args()

    creds = json.loads(args.creds.read_text())
    email = args.who if "@" in args.who else f"{args.who}@urumi.test"
    key = creds[email]["api_key"]
    STATE.mkdir(exist_ok=True)
    state_file = STATE / f"{email}.json"
    conv = None if args.new or not state_file.exists() else json.loads(state_file.read_text()).get("id")

    headers = {"Authorization": f"Bearer {key}", **({"Host": args.host} if args.host else {})}
    answer: list[str] = []
    with httpx2.Client(base_url=args.chat, headers=headers, timeout=httpx2.Timeout(180, connect=10)) as http:
        with http.stream("POST", "/api/chat", json={"message": args.message, "conversation_id": conv}) as r:
            if r.status_code != 200:
                r.read()
                print(f"HTTP {r.status_code}: {short(r.text)}")
                return 1
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                e = json.loads(line[6:])
                kind = e["type"]
                if kind == "conversation":
                    state_file.write_text(json.dumps({"id": e["id"]}))
                    print(f"[{email}] conversation {e['id'][:8]} model {e.get('model_label')}")
                elif kind == "tools":
                    print(f"TOOLS ({len(e['tools'])}): {', '.join(e['tools'])}")
                elif kind == "tool_call":
                    print(f"CALL   {e['name']} {short(e['input'], 160)}")
                elif kind == "approval_required":
                    print(f"HELD   {e['name']} {short(e['input'], 160)} -> answering {args.writes}")
                    d = http.post("/api/chat/approve", json={"conversation_id": state_file and json.loads(state_file.read_text())["id"],
                                                             "request_id": e["request_id"], "decision": args.writes})
                    if d.status_code != 200:
                        print(f"  approval reply HTTP {d.status_code}: {short(d.text)}")
                elif kind == "tool_result":
                    flag = "ERROR" if e["is_error"] else "ok"
                    print(f"RESULT {e['name']} {flag} {e.get('duration_ms')}ms {short(e['output'])}")
                elif kind == "text":
                    answer.append(e["delta"])
                elif kind == "done":
                    print(f"DONE   {e['steps']} model calls, {e['tool_calls']} tool calls")
                elif kind == "error":
                    print(f"ERROR  {short(e['message'])}")
    print("ANSWER:", short("".join(answer), 1500) if answer else "(no text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
