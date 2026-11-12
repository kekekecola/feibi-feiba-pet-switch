"""Command the native F8 bridge and verify its completion receipt."""
import argparse
import json
import math
from pathlib import Path
import sys
import time
import uuid

import control
from request_lifecycle import decide
from native_bridge import write_json

from paths import RUNTIME, VERSION


def status():
    data = control.read_json(RUNTIME / "bridge-status.json")
    data["running"] = bool(data.get("running") and
                           time.time() - data.get("updated_at", 0) < 6 and
                           control.process_alive(data.get("pid")))
    if not data["running"]:
        data["hotkey_registered"] = False
    return data


def main():
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "toggle", "take-off", "put-on", "quit"))
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        parser.error('--timeout must be finite and between 0 and 120 seconds')
    current = status()
    if args.action == "status":
        print(json.dumps(current, ensure_ascii=False))
        return 0
    if not current.get("running"):
        print(json.dumps({"result": "failed", "message": "原生 F8 切换器未运行。"}, ensure_ascii=False))
        return 2
    command_id = uuid.uuid4().hex
    write_json(RUNTIME / "bridge-commands" / (command_id + ".json"),
               {"command_id": command_id, "action": args.action, "issued_at": time.time(),
                "expires_at": time.time() + max(0.1, min(args.timeout, 120))})
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        receipt = control.read_json(RUNTIME / "bridge-acks" / (command_id + ".json"))
        if receipt:
            print(json.dumps(receipt, ensure_ascii=False))
            return 0 if receipt.get("result") == "completed" else 3
        time.sleep(0.08)
    cancelled = decide(RUNTIME / "bridge-decisions", command_id, "cancelled")
    print(json.dumps({"result": "cancelled" if cancelled else "in_progress", "command_id": command_id,
                      "status": status()}, ensure_ascii=False))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
