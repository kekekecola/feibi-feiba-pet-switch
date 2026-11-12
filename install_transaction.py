"""Rollback only managed pet/config files and the project's scheduled task."""
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
import json

TASK = 'FeibiFeibaPetSwitch'


def task_snapshot(task=TASK):
    if task not in (TASK, TASK + "Update"):
        raise ValueError("Unknown project task")
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
              "$t=@(Get-ScheduledTask | Where-Object { $_.TaskName -eq 'FeibiFeibaPetSwitch' -and $_.TaskPath -eq '\\' }); "
              "if ($t.Count -eq 0) { @{exists=$false} | ConvertTo-Json -Compress } "
              "else { @{exists=$true; running=($t[0].State -eq 'Running'); "
              "xml=(Export-ScheduledTask -TaskName 'FeibiFeibaPetSwitch')} | ConvertTo-Json -Compress }")
    script = script.replace(TASK, task)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            check=True, capture_output=True, encoding='utf-8', timeout=30)
    return json.loads(result.stdout.lstrip('\ufeff'))


def restore_task(snapshot, task=TASK):
    if task not in (TASK, TASK + "Update"):
        raise ValueError("Unknown project task")
    if snapshot['exists']:
        with tempfile.TemporaryDirectory(prefix='feibi-restore-') as folder:
            path = Path(folder) / 'task.xml'
            path.write_text(snapshot['xml'], encoding='utf-16')
            subprocess.run(['schtasks', '/Create', '/TN', task, '/XML', str(path), '/F'], check=True, timeout=30)
    elif task_snapshot(task)['exists']:
        subprocess.run(['schtasks', '/Delete', '/TN', task, '/F'], check=True, timeout=30)
    if snapshot.get('running'):
        subprocess.run(['schtasks', '/Run', '/TN', task], check=True, timeout=30)


class Transaction:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.files = {}
        self.task_touched = False

    def remember(self, path):
        path = Path(path)
        if path not in self.files:
            self.files[path] = path.read_bytes() if path.exists() else None

    def rollback(self):
        errors = []
        # Restore files before restarting the previous service.
        for path, data in reversed(list(self.files.items())):
            try:
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
            except OSError as error:
                errors.append(str(error))
        if self.task_touched:
            try:
                restore_task(self.snapshot)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError('Rollback incomplete; backups retained: ' + '; '.join(errors))
