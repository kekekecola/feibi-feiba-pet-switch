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
from pet_install_guard import inspect


class PetInstallGuardTests(unittest.TestCase):
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
                    install.apply_install(SimpleNamespace(skip_pets=False),home,home/'python.exe','')
                snapshot.assert_not_called()
            self.assertTrue((duplicate/'pet.json').exists())

    def test_repeated_install_keeps_fixed_ids_and_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            install.install_pets(home);install.install_pets(home);install.install_pets(home)
            metadata=list((home/'pets').glob('*/pet.json'))
            self.assertEqual(len(metadata),2)
            self.assertEqual({json.loads(p.read_text(encoding='utf-8'))['id'] for p in metadata},{'feibi-jiubi','feiba-jiubi'})
