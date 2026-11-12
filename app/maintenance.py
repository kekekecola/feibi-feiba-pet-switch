"""Bounded logs and conservative, age-based IPC history cleanup."""
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import time

RETENTION_SECONDS = 7 * 86400


def configure_logging(runtime, worker=False):
    runtime.mkdir(parents=True, exist_ok=True)
    name = f'native-worker-{os.getpid()}.log' if worker else 'native-bridge.log'
    handler = RotatingFileHandler(runtime / name, maxBytes=512_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(message)s', force=True)


def cleanup(runtime, active_ids=(), now=None):
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    protected = set(active_ids)
    groups = {}
    for folder in ('commands', 'bridge-commands', 'acks', 'bridge-acks', 'bridge-decisions'):
        for path in (runtime / folder).glob('*.json'):
            if not re.fullmatch('[a-f0-9]{32}', path.stem):
                continue
            if folder in ('commands', 'bridge-commands'):
                protected.add(path.stem)
            else:
                groups.setdefault(path.stem, []).append(path)
    removed = 0
    for command_id, paths in groups.items():
        try:
            if command_id not in protected and all(p.stat().st_mtime < cutoff for p in paths):
                for path in paths:
                    path.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            logging.exception('History cleanup deferred')
    for path in runtime.glob('native-worker-*.log*'):
        if not re.fullmatch(r'native-worker-\d+\.log(?:\.[12])?', path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass  # Windows can keep a live worker's log open.
    return removed
