import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'app'))
import install as installer
import install_transaction
import maintenance


class ReliabilityTests(unittest.TestCase):
    def test_uninstall_needs_no_assets_or_runtime(self):
        with patch.object(sys, 'argv', ['install.py', 'uninstall']), \
             patch.object(installer, 'verify_assets', side_effect=AssertionError('must not verify')), \
             patch.object(installer, 'stop_service') as stop, \
             patch.object(installer, 'task_snapshot', return_value={'exists': False}), \
             patch.object(installer, 'command') as command:
            installer.main()
            stop.assert_called_once()
            command.assert_not_called()  # Repeat uninstall is harmless.

    def test_stop_continues_after_broken_runtime(self):
        with patch.object(installer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('quit', 40)), \
             patch.object(installer, 'task_snapshot', side_effect=[{'running': True}, {'running': False}]), \
             patch.object(installer, 'command') as command:
            installer.stop_service(Path(sys.executable))
            self.assertEqual(command.call_args.args[0][1], '/End')

    def test_install_failures_restore_both_pets_config_and_task(self):
        for stage in ('copy', 'configure', 'create', 'run'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                home = root / 'home'
                before = {}
                for pet in ('feibi-jiubi', 'feiba-jiubi'):
                    for name in ('pet.json', 'spritesheet.webp'):
                        source = root / 'pets' / pet / name
                        target = home / 'pets' / pet / name
                        source.parent.mkdir(parents=True, exist_ok=True)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source.write_bytes(b'new pet')
                        target.write_bytes(b'old pet')
                        before[target] = target.read_bytes()
                config = root / 'app' / 'runtime' / 'pet-roles.json'
                config.parent.mkdir(parents=True)
                config.write_bytes(b'old config')
                before[config] = b'old config'
                snapshot = {'exists': True, 'running': True, 'xml': '<old-task/>'}
                def configure(args):
                    config.write_bytes(b'new config')
                    if stage == 'configure':
                        raise OSError('configuration fault')
                def command(args, **kwargs):
                    if (stage == 'create' and '/Create' in args) or (stage == 'run' and '/Run' in args):
                        raise OSError('scheduler fault')
                real_copy = installer.shutil.copy2
                def copy(source, destination, **kwargs):
                    if stage == 'copy' and Path(source) == root / 'pets' / 'feiba-jiubi' / 'spritesheet.webp':
                        raise OSError('copy fault')
                    return real_copy(source, destination, **kwargs)
                with patch.object(installer, 'ROOT', root), \
                     patch.object(installer, 'task_snapshot', return_value=snapshot), \
                     patch.object(installer, 'stop_service'), \
                     patch.object(installer, 'configure', side_effect=configure), \
                     patch.object(installer, 'command', side_effect=command), \
                     patch.object(installer.shutil, 'copy2', side_effect=copy), \
                     patch.object(install_transaction, 'restore_task') as restore:
                    with self.assertRaisesRegex(RuntimeError, 'rolled back'):
                        installer.apply_install(SimpleNamespace(skip_pets=False, no_start=False), home,
                                                root / 'python.exe', '<new-task/>')
                    restore.assert_called_once_with(snapshot)
                for path, expected in before.items():
                    self.assertEqual(path.read_bytes(), expected)
                self.assertTrue(list((home / 'pet-backups').iterdir()))

    def test_new_managed_files_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pet.json'
            transaction = install_transaction.Transaction({'exists': False})
            transaction.remember(path)
            path.write_text('partial install')
            transaction.rollback()
            self.assertFalse(path.exists())

    def test_cleanup_protects_active_pending_recent_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = time.time()
            stale = now - maintenance.RETENTION_SECONDS - 10
            def create(relative, modified=stale):
                path = root / relative
                path.parent.mkdir(exist_ok=True)
                path.write_text('{}')
                os.utime(path, (modified, modified))
                return path
            expired = [create('bridge-acks/' + 'a'*32 + '.json'), create('bridge-decisions/' + 'a'*32 + '.json')]
            keep = [create('bridge-acks/' + 'b'*32 + '.json'), create('acks/' + 'c'*32 + '.json'),
                    create('commands/' + 'c'*32 + '.json'), create('bridge-acks/' + 'd'*32 + '.json', now),
                    create('bridge-decisions/' + 'd'*32 + '.json'), create('native-recovery.json'),
                    create('pet-roles.json'), create('native-bridge.log'), create('acks/user-file.json')]
            worker = create('native-worker-123.log')
            maintenance.cleanup(root, active_ids=['b'*32], now=now)
            self.assertTrue(all(not p.exists() for p in expired + [worker]))
            self.assertTrue(all(p.exists() for p in keep))

    def test_log_rotation_is_bounded_and_workers_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous = logging.root.handlers[:]
            logging.root.handlers = []
            try:
                maintenance.configure_logging(root)
                handler = logging.root.handlers[0]
                handler.maxBytes = 200
                for _ in range(20):
                    logging.info('x' * 100)
                self.assertLessEqual(len(list(root.glob('native-bridge.log*'))), 3)
                maintenance.configure_logging(root, worker=True)
                self.assertIn('native-worker-', logging.root.handlers[0].baseFilename)
            finally:
                for handler in logging.root.handlers:
                    handler.close()
                logging.root.handlers = previous


if __name__ == '__main__':
    unittest.main()
