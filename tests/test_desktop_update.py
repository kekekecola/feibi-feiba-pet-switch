import io
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import venv
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'app')]
import manager
import settings
from hotkeys import GlobalF8
import diagnostics
from tray import status_label


def make_archive(files=None):
    contents = {'VERSION':'1.3.0', 'manager.py':'# manager', 'launcher.py':'# launcher',
                'requirements.txt':'same', 'app/native_bridge.py':'# bridge', 'asset.txt':'art'}
    contents['SHA256SUMS'] = hashlib.sha256(b'art').hexdigest() + '  asset.txt\n'
    contents.update(files or {})
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, value in contents.items():
            archive.writestr('feibi-feiba-pet-switch/' + name, value)
    payload = data.getvalue()
    return payload, 'sha256:' + hashlib.sha256(payload).hexdigest()


def initial_pointer(home):
    source = home / 'versions' / 'v1.2.0'
    source.mkdir(parents=True)
    (source / 'requirements.txt').write_bytes(b'same')
    python = home / '.venv' / 'Scripts' / 'python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'')
    current = dict(version='1.2.0', release='versions/v1.2.0', python='.venv/Scripts/python.exe')
    manager.atomic(home / 'current.json', current)
    return current


class DesktopUpdateTests(unittest.TestCase):
    def test_control_output_is_relayed(self):
        output, error = io.BytesIO(), io.BytesIO()
        result = subprocess.CompletedProcess([], 3, stdout=b'{"result":"failed"}\n', stderr=b'reason')
        with patch.object(manager.subprocess, 'run', return_value=result) as run, \
             patch.object(manager.sys, 'stdout', Mock(buffer=output)), \
             patch.object(manager.sys, 'stderr', Mock(buffer=error)):
            self.assertEqual(manager.relay_client(['python', 'client.py']), 3)
        self.assertEqual(output.getvalue(), b'{"result":"failed"}\n')
        self.assertEqual(error.getvalue(), b'reason')
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_stable_launcher_preserves_json_output(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            venv.EnvBuilder(with_pip=False).create(home / '.venv')
            python = home / '.venv' / 'Scripts' / 'python.exe'
            source = home / 'versions' / 'test'
            source.mkdir(parents=True)
            (source / 'manager.py').write_text('print(\'{"version":"test","phase":"idle"}\')', encoding='utf-8')
            (home / 'launcher.py').write_bytes((ROOT / 'launcher.py').read_bytes())
            manager.atomic(home / 'current.json', dict(release='versions/test', python='.venv/Scripts/python.exe'))
            result = subprocess.run([sys.executable, str(home / 'launcher.py'), 'control','status'],
                                    capture_output=True, check=True, timeout=20)
            self.assertEqual(json.loads(result.stdout)['phase'], 'idle')

    def test_requirement_newlines_do_not_rebuild_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            current = initial_pointer(home)
            (home / current['release'] / 'requirements.txt').write_bytes(b'Pillow==12.2.0\r\ncomtypes==1.5.0\r\n')
            source = home / 'versions' / 'new'
            source.mkdir()
            (source / 'requirements.txt').write_bytes(b'Pillow==12.2.0\ncomtypes==1.5.0\n')
            with patch.object(manager, 'run') as run, patch.object(manager.venv, 'EnvBuilder') as builder:
                python = manager.prepare_python(home, source, current)
                self.assertEqual(python, home / current['python'])
                builder.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertNotIn('pip', run.call_args.args[0])

    def test_hotkey_parser(self):
        self.assertEqual(settings.parse_hotkey('alt+ctrl+h'), ('Ctrl+Alt+H', 0x4003, ord('H')))
        self.assertEqual(settings.parse_hotkey('F8')[2], 0x77)
        for value in ('F12','F0','F25','H','Win+H','Ctrl+Ctrl+H','Ctrl++H',None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.parse_hotkey(value)

    def test_failed_rebind_preserves_original(self):
        hotkey = GlobalF8(queue.Queue())
        hotkey.registered, hotkey.active_id = True, 0x46B8
        api = Mock()
        api.RegisterHotKey.return_value = 0
        hotkey.apply_binding(api, 'Ctrl+Alt+H')
        self.assertEqual(hotkey.active, 'F8')
        self.assertTrue(hotkey.registered)
        api.UnregisterHotKey.assert_not_called()
        self.assertIsNotNone(hotkey.error)

    def test_success_registers_before_unregister(self):
        hotkey = GlobalF8(queue.Queue())
        hotkey.registered, hotkey.active_id = True, 0x46B8
        api = Mock()
        api.RegisterHotKey.return_value = 1
        hotkey.apply_binding(api, 'Ctrl+Alt+H')
        self.assertEqual(hotkey.active, 'Ctrl+Alt+H')
        self.assertEqual([call[0] for call in api.mock_calls], ['RegisterHotKey','UnregisterHotKey'])

    def test_bad_settings_can_be_repaired_with_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{broken')
            with patch.object(settings, 'SETTINGS', path):
                settings.save_hotkey('Ctrl+Alt+H')
                self.assertEqual(settings.load()['hotkey'], 'Ctrl+Alt+H')
            self.assertEqual(len(list(path.parent.glob('settings.broken.*.json'))), 1)

    def test_diagnostic_does_not_dump_account_or_preferences(self):
        with patch.object(diagnostics.control, 'read_json', return_value={'selected-avatar-id':'private-id','api_key':'secret'}), \
             patch.object(diagnostics.control, 'process_alive', return_value=False):
            payload = json.dumps(diagnostics.collect())
        self.assertNotIn('private-id', payload)
        self.assertNotIn('secret', payload)
        self.assertEqual(status_label({'phase':'transition'}), '正在切换')
        self.assertEqual(status_label({'phase':'idle','hotkey_error':'conflict'}), '快捷键异常')

    def test_diagnostics_identifies_failure_stage(self):
        state = {'stage':'native_selection','error':'selection not acknowledged'}
        self.assertIn('确认原生宠物选择失败', diagnostics.summary(state, True, True))

    def test_switching_memory_mode_does_not_replace_hotkey(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(settings, 'SETTINGS', Path(folder)/'settings.json'):
                settings.save_hotkey('Ctrl+Alt+H')
                self.assertFalse(settings.load()['memory_saving'])
                settings.save_memory_saving(True)
                self.assertEqual(settings.load(), {'hotkey':'Ctrl+Alt+H','memory_saving':True})

    def test_archive_and_digest(self):
        payload, digest = make_archive()
        with tempfile.TemporaryDirectory() as folder:
            source = manager.extract_archive(payload, Path(folder), digest)
            self.assertEqual((source / 'VERSION').read_text(), '1.3.0')
        for bad in (None, 'sha256:'+'0'*64):
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                manager.extract_archive(payload, Path(folder), bad)

    def test_archive_rejects_traversal_and_bad_assets(self):
        for extra in ({'../escape':'bad'}, {'/absolute':'bad'}, {'C:\\bad':'bad'}, {'asset.txt':'changed'}):
            payload, digest = make_archive(extra)
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                manager.extract_archive(payload, Path(folder), digest)

    def test_pointer_cannot_escape_install(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            manager.atomic(home / 'current.json', dict(version='1.2.0', release='../elsewhere', python='python.exe'))
            with self.assertRaises(ValueError):
                manager.pointer(home)

    def test_older_local_release_does_not_downgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home = root/'managed'
            current = initial_pointer(home)
            source = root/'bundle'
            source.mkdir()
            (source/'VERSION').write_text('1.1.0',encoding='utf-8')
            with patch.object(manager,'verify_source'), patch.object(manager,'activate') as activate:
                self.assertEqual(manager.update_from_folder(home,source),current)
                activate.assert_not_called()

    def test_invalid_local_release_leaves_current_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            current = initial_pointer(home)
            with patch.object(manager,'verify_source',side_effect=ValueError('bad assets')), \
                 patch.object(manager,'stop') as stop:
                with self.assertRaises(ValueError):
                    manager.update_from_folder(home,home/'bad')
                stop.assert_not_called()
            self.assertEqual(manager.pointer(home), current)

    def test_local_update_prepares_before_activation(self):
        payload,digest=make_archive()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            home=root/'managed'
            current=initial_pointer(home)
            source=manager.extract_archive(payload,root/'bundle',digest)
            events=[]
            def prepare(*args):
                events.append('prepare')
                return home/current['python']
            def activate(*args):
                events.append('activate')
                return {'version':'1.3.0'}
            with patch.object(manager,'prepare_python',side_effect=prepare), patch.object(manager,'activate',side_effect=activate):
                self.assertEqual(manager.update_from_folder(home,source)['version'],'1.3.0')
            self.assertEqual(events,['prepare','activate'])

    def test_failed_health_check_rolls_back_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            current = initial_pointer(home)
            source = home / 'versions' / 'v1.3.0'
            source.mkdir()
            (source / 'VERSION').write_text('1.3.0')
            with patch.object(manager, 'stop'), patch.object(manager, 'run') as run, \
                 patch.object(manager, 'healthy', side_effect=RuntimeError('unhealthy')):
                with self.assertRaises(RuntimeError):
                    manager.activate(home, source, home / current['python'], current)
                self.assertEqual(run.call_count, 2)
            self.assertEqual(manager.pointer(home), current)

    def test_update_preserves_existing_helper_when_old_bridge_supports_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            current = initial_pointer(home)
            current['version'] = '1.6.9'
            manager.atomic(home / 'current.json', current)
            source = home / 'versions' / 'new'
            source.mkdir()
            (source / 'VERSION').write_text('1.7.0', encoding='utf-8')
            with patch.object(manager, 'run') as run, patch.object(manager, 'healthy'), \
                 patch('install_transaction.task_snapshot', return_value={'running':False}):
                result = manager.activate(home, source, home / current['python'], current)
            self.assertEqual(result['version'], '1.7.0')
            self.assertIn('quit-for-update', run.call_args_list[0].args[0])

    def test_update_mutex_excludes_second_updater(self):
        with tempfile.TemporaryDirectory() as folder:
            with manager.UpdateLock(Path(folder)):
                with self.assertRaises(RuntimeError):
                    manager.UpdateLock(Path(folder))

    def test_only_stable_semver(self):
        for value in ('v1.2.0-beta','main','../bad',None):
            with self.assertRaises(ValueError):
                manager.version(value)

    def legacy_update_task(self, home, running=False):
        xml=manager.scheduler_xml(home,home/'python.exe','test','run').replace('" run</Arguments>','" update</Arguments>')
        return {'exists':True, 'running':running, 'xml':xml}

    def test_unready_switcher_does_not_reserve_shortcuts(self):
        hotkey = GlobalF8(queue.Queue(), enabled=False)
        user = Mock()
        hotkey.apply_request(user, 'hotkey', 'F7')
        user.RegisterHotKey.assert_not_called()
        hotkey.apply_request(user, 'enabled', True)
        user.RegisterHotKey.assert_called_once()
        self.assertEqual(hotkey.active, 'F7')
        hotkey.apply_request(user, 'enabled', False)
        user.UnregisterHotKey.assert_called_once_with(None, hotkey.active_id)
        self.assertFalse(hotkey.registered)

    def test_retirement_only_deletes_the_old_owned_task_once(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder).resolve()
            with patch('install_transaction.task_snapshot',return_value=self.legacy_update_task(home)) as snapshot, \
                 patch.object(manager,'run') as run:
                self.assertTrue(manager.retire_update_task(home))
                self.assertTrue(manager.retire_update_task(home))
                snapshot.assert_called_once_with(manager.UPDATE_TASK)
                self.assertEqual(run.call_args.args[0],['schtasks','/Delete','/TN',manager.UPDATE_TASK,'/F'])
            self.assertTrue((home/'updates-disabled').exists())

    def test_cleanup_leaves_a_running_old_update_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder).resolve()
            with patch('install_transaction.task_snapshot',return_value=self.legacy_update_task(home, running=True)), \
                 patch.object(manager,'run') as run:
                self.assertFalse(manager.retire_update_task(home))
                run.assert_not_called()
            self.assertFalse((home/'manual-updates-v1').exists())

    def test_cleanup_leaves_a_different_installation_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder).resolve()
            with patch('install_transaction.task_snapshot',return_value=self.legacy_update_task(home/'other')), \
                 patch.object(manager,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'different installation'):
                    manager.retire_update_task(home)
                run.assert_not_called()

    def test_obsolete_automatic_command_does_not_run_an_update(self):
        with tempfile.TemporaryDirectory() as folder:
            for action in ('update','update-on-client-launch'):
                with patch.object(sys,'argv',['manager.py',action,'--home',folder]), patch.object(manager,'run') as run:
                    manager.main()
                    run.assert_not_called()

    def test_interrupted_update_restores_previous_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            previous = initial_pointer(home)
            new = dict(previous, version='1.3.0', release='versions/v1.3.0')
            manager.atomic(home / 'current.json', new)
            manager.atomic(home / 'pending-update.json', dict(previous=previous, new=new))
            with patch.object(manager, 'healthy', side_effect=RuntimeError('dead')), \
                 patch.object(manager, 'stop') as stop, patch.object(manager, 'run'):
                manager.recover_pending(home)
                stop.assert_called_once()
            self.assertEqual(manager.pointer(home), previous)
            self.assertFalse((home / 'pending-update.json').exists())

    def test_good_hotkey_is_separate_from_requested_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with patch.object(settings, 'SETTINGS', home / 'settings.json'), \
                 patch.object(settings, 'LAST_GOOD', home / 'last-good.json'):
                settings.save_hotkey('Ctrl+Alt+H')
                settings.remember_good('F8')
                self.assertEqual(settings.last_good(), 'F8')
                self.assertEqual(settings.load()['hotkey'], 'Ctrl+Alt+H')


if __name__ == '__main__':
    unittest.main()
