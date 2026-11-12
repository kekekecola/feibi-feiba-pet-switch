import hashlib
import importlib.util
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import pet_registry
import isolated_calls
from request_lifecycle import claim, decide

spec = importlib.util.spec_from_file_location('project_install', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PackageTests(unittest.TestCase):
    def test_syntax(self):
        for path in [*(ROOT / 'app').glob('*.py'), *ROOT.glob('*.py')]:
            py_compile.compile(str(path), doraise=True)

    def test_assets(self):
        from PIL import Image
        installer.verify_assets()
        for form, pet in [('feibi', 'feibi-jiubi'), ('feiba', 'feiba-jiubi')]:
            image = ROOT / 'app' / 'assets' / (form + '.webp')
            with Image.open(image) as sheet:
                self.assertEqual(sheet.size, (1536, 1872))
                self.assertEqual(sheet.mode, 'RGBA')
                self.assertEqual(sheet.getchannel('A').getextrema(), (0, 255))
            self.assertEqual(image.read_bytes(), (ROOT / 'pets' / pet / 'spritesheet.webp').read_bytes())
        with Image.open(ROOT / 'app' / 'assets' / 'take-off.png') as cg:
            self.assertEqual(cg.size, (2880, 248))
            self.assertEqual(cg.getchannel('A').getextrema(), (0, 255))

    def test_timeout_interrupts_worker(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            isolated_calls.run([sys.executable, '-c', 'import time; time.sleep(120)'], timeout=.3)
        self.assertLess(time.monotonic() - started, 5)

    def test_reimport_and_rename(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'roles.json'
            roles = {'feibi': dict(id='pet_old', name='old', description='role description', fingerprint='same')}
            config.write_text(json.dumps(roles), encoding='utf-8')
            with patch.object(pet_registry, 'ROLES', config):
                result = pet_registry.refresh('pet_new', [dict(id='pet_new', spritesheetFingerprint='same')])
                self.assertEqual(result['feibi']['id'], 'pet_new')
                self.assertTrue(pet_registry.matches('feibi', 'renamed role description'))
                pet_registry.observe_names({'feibi': 'renamed role description'})
                self.assertEqual(pet_registry.load()['feibi']['name'], 'renamed')
                result = pet_registry.refresh(None, [dict(id='pet_a', spritesheetFingerprint='same'),
                                                     dict(id='pet_b', spritesheetFingerprint='same')])
                self.assertEqual(result['feibi']['id'], 'pet_new')

    def test_cancel_start_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            command = dict(command_id='a' * 32, action='toggle', issued_at=time.time(), expires_at=time.time() + 10)
            self.assertTrue(decide(directory, command['command_id'], 'cancelled'))
            self.assertFalse(claim(directory, command))
            command['command_id'] = 'b' * 32
            self.assertTrue(claim(directory, command))
            self.assertFalse(decide(directory, command['command_id'], 'cancelled'))

    def test_install_backup_and_task(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            previous = home / 'pets' / 'feibi-jiubi'
            previous.mkdir(parents=True)
            (previous / 'pet.json').write_text('previous metadata', encoding='utf-8')
            installer.install_pets(home)
            backups = list((home / 'pet-backups').glob('feibi-jiubi-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'pet.json').read_text(), 'previous metadata')
            installer.install_pets(home)
            self.assertEqual(len(list((home / 'pet-backups').iterdir())), 1)
        xml = installer.task_xml(Path('C:/example & folder/pythonw.exe'), 'S-1-5-21-example')
        tree = ET.fromstring(xml)
        namespace = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        self.assertEqual(tree.find('.//t:RunLevel', namespace).text, 'LeastPrivilege')
        self.assertEqual(tree.find('.//t:LogonType', namespace).text, 'InteractiveToken')


if __name__ == '__main__':
    unittest.main()
