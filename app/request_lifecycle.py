"""Bounded IPC requests and an atomic cancellation/start decision."""
import json
import os
import re
import time


def valid_command(command, command_id, actions):
    if not re.fullmatch(r"[a-f0-9]{32}", command_id):
        return False
    if command.get("command_id") != command_id or command.get("action") not in actions:
        return False
    issued = command.get("issued_at")
    expires = command.get("expires_at", issued + 30 if type(issued) in (int, float) else None)
    return (type(issued) in (int, float) and type(expires) in (int, float)
            and 0 <= issued <= time.time() + 5
            and issued <= expires <= issued + 120)


def decide(directory, command_id, state):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (command_id + ".json")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"state": state, "time": time.time()}, output)
    return True


def claim(directory, command):
    issued = command["issued_at"]
    if time.time() >= command.get("expires_at", issued + 30):
        decide(directory, command["command_id"], "cancelled")
        return False
    return decide(directory, command["command_id"], "started")
