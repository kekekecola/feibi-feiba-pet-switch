"""Stable entrypoint for a managed, versioned installation (standard library only)."""
import json
from pathlib import Path
import subprocess
import sys

home = Path(__file__).resolve().parent
data = json.loads((home / 'current.json').read_text(encoding='utf-8'))
if (sys.argv[1:] or ['run'])[0] == 'update' and (home / 'pending-update.json').exists():
    previous = json.loads((home / 'pending-update.json').read_text(encoding='utf-8')).get('previous')
    if previous:
        data = previous  # Use the last working manager to finish interrupted activation.
release = (home / data['release']).resolve()
python = (home / data['python']).resolve()
if not release.is_relative_to((home / 'versions').resolve()) or not python.is_relative_to(home.resolve()):
    raise SystemExit('Invalid installation pointer')
command = [str(python), str(release / 'manager.py'), *(sys.argv[1:] or ['run']), '--home', str(home)]
if (sys.argv[1:] or ['run'])[0] == 'control':
    result = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if sys.stdout:
        sys.stdout.buffer.write(result.stdout)
    if sys.stderr:
        sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)
raise SystemExit(subprocess.call(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
