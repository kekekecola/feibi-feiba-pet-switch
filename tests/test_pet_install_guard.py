import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import install
import manager
from pet_install_guard import inspect


class PetInstallGuardTests(unittest.TestCase):
    def test_double_click_install_updates_existing_managed_copy_without_pet_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            managed = Path(folder).resolve()
            (managed / 'current.json').write_text('{}', encoding='utf-8')
            snapshot = {'exists': True, 'xml': manager.scheduler_xml(managed, managed/'python.exe', 'test', 'run')}
            with patch.object(sys, 'argv', ['install.py', 'install']), \
                 patch.object(install, 'task_snapshot', return_value=snapshot), \
                 patch('pet_install_guard.inspect', side_effect=AssertionError('unexpected pet inspection')), \
                 patch.object(manager, 'update_from_folder', return_value={'version': '1.6.9'}) as update, \
                 patch.object(manager, 'ensure_updater', return_value=True) as ensure:
                install.main()
                update.assert_called_once_with(managed, install.ROOT)
                ensure.assert_called_once_with(managed)

    def test_legacy_update_preserves_pets_and_migrates_runtime_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            previous = base/'old'
            managed = base/'managed'
            config = previous/'app'/'runtime'
            config.mkdir(parents=True)
            (config/'pet-roles.json').write_text('existing mapping', encoding='utf-8')
            (config/'settings.json').write_text('existing settings', encoding='utf-8')
            args = SimpleNamespace(skip_pets=True, feibi_id=None, feiba_id=None, no_start=False)
            previous_task = {'exists':True,'running':True,
                'xml': install.task_xml(previous/'.venv'/'Scripts'/'pythonw.exe', 'test').replace(
                    str(install.ROOT / 'app'), str(previous / 'app'))}
            original_atomic = manager.atomic
            def safe_atomic(path, data):
                if path != manager.ROOT / 'managed-link.json':
                    original_atomic(path, data)
            with patch.object(manager, 'default_home', return_value=managed), \
                 patch('install_transaction.task_snapshot', side_effect=[previous_task, {'exists':False}]), \
                 patch.object(manager, 'prepare_python', return_value=managed/'.venv'/'Scripts'/'python.exe'), \
                 patch.object(manager, 'user_sid', return_value='test'), \
                 patch.object(manager, 'register') as register, \
                 patch.object(manager, 'activate', return_value={'version':'1.6.14'}), \
                 patch.object(install, 'stop_service') as stop, patch.object(install, 'configure') as configure, \
                 patch.object(install, 'install_pets') as install_pets, \
                 patch.object(manager, 'atomic', side_effect=safe_atomic):
                install.apply_install(args, base/'client', previous)
                stop.assert_called_once_with(previous/'.venv'/'Scripts'/'python.exe', previous)
                configure.assert_not_called()
                install_pets.assert_not_called()
                updater_task = register.call_args_list[1].args
                self.assertEqual(updater_task[0], manager.UPDATE_TASK)
                self.assertNotIn('Trigger', updater_task[1])
            self.assertEqual((managed/'runtime'/'settings.json').read_text(), 'existing settings')
            self.assertEqual((managed/'runtime'/'pet-roles.json').read_text(), 'existing mapping')

    def test_valid_local_pair_is_detected_for_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            install.install_pets(home)
            self.assertEqual(inspect(home)['reuse'],
                             {'feibi':'custom:feibi-jiubi','feiba':'custom:feiba-jiubi'})

    def state(self, home, active=True):
        data = {'migrated-cloud-pet-ids-v1':{json.dumps(['account-a','user-a'], separators=(',',':')):
                {'custom:feibi-jiubi':'pet_existing_a','custom:feiba-jiubi':'pet_existing_b'}}}
        if active:
            data['cloud-pet-artwork-cache']={'accountId':'account-a','userId':'user-a'}
        (home/'.codex-global-state.json').write_text(json.dumps({'scope':data}),encoding='utf-8')

    def test_existing_migrated_pair_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);self.state(home)
            result=inspect(home)
            self.assertEqual(result['reuse'],{'feibi':'pet_existing_a','feiba':'pet_existing_b'})
            self.assertEqual(result['conflicts'],[])
            install.install_pets(home,reuse=result['reuse'])
            self.assertFalse((home/'pets').exists())

    def test_other_account_mapping_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);self.state(home)
            path=home/'.codex-global-state.json';data=json.loads(path.read_text())
            data['scope']['cloud-pet-artwork-cache']['accountId']='account-b'
            path.write_text(json.dumps(data))
            self.assertEqual(inspect(home)['reuse'],{})

    def test_unknown_account_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);self.state(home,active=False)
            self.assertTrue(inspect(home)['conflicts'])

    def test_same_name_noncanonical_folder_blocks_new_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);duplicate=home/'pets'/'feibi-from-old-release';duplicate.mkdir(parents=True)
            (duplicate/'pet.json').write_text(json.dumps({'displayName':'菲比啾比'}),encoding='utf-8')
            result=inspect(home)
            self.assertTrue(result['conflicts'])
            with patch.object(install,'task_snapshot') as snapshot:
                with self.assertRaises(RuntimeError):
                    install.apply_install(SimpleNamespace(skip_pets=False), home, None)
                snapshot.assert_not_called()
            self.assertTrue((duplicate/'pet.json').exists())

    def test_repeated_install_keeps_fixed_ids_and_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            install.install_pets(home);install.install_pets(home);install.install_pets(home)
            metadata=list((home/'pets').glob('*/pet.json'))
            self.assertEqual(len(metadata),2)
            self.assertEqual({json.loads(p.read_text(encoding='utf-8'))['id'] for p in metadata},{'feibi-jiubi','feiba-jiubi'})
