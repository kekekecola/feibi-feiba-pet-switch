from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import msaa_controls as msaa


class MsaaCompatibilityTests(unittest.TestCase):
    def collector(self):
        return msaa.ActionCollector({'feibi':'菲比啾比','feiba':'菲八啾比'},
            {'feibi':{'name':'菲比啾比','description':''},'feiba':{'name':'菲八啾比','description':''}})

    def test_standard_selection_roles(self):
        for role in (34,43,44,45):
            c=self.collector()
            c.add((1,0),'菲比啾比',role,'select','first')
            c.add((2,0),'菲八啾比',role,'select','second')
            with patch.object(msaa.pet_registry,'observe_names'):
                self.assertEqual(c.resolve(),{'feibi':'first','feiba':'second'})

    def test_duplicate_enumeration_is_not_a_duplicate_pet(self):
        c=self.collector()
        for _ in range(3):c.add((1,0),'菲比啾比',43,'click','first')
        c.add((2,0),'菲八啾比',43,'click','second')
        with patch.object(msaa.pet_registry,'observe_names'):
            self.assertEqual(len(c.resolve()),2)

    def test_real_duplicates_are_reported_and_not_selected(self):
        c=self.collector()
        for key in (1,2):c.add((key,0),'菲比啾比',43,'click',object())
        c.add((3,0),'菲八啾比',43,'click',object())
        with self.assertRaises(msaa.PetControlsError) as result:c.resolve()
        self.assertEqual(result.exception.details['matches']['菲比啾比'],2)
        self.assertTrue(result.exception.details['duplicates'])

    def test_text_descendants_are_not_action_candidates(self):
        c=self.collector()
        c.add((1,0),'菲比啾比',41,'click',object())
        c.add((2,0),'菲八啾比',40,'click',object())
        with self.assertRaises(msaa.PetControlsError) as result:c.resolve()
        self.assertEqual(len(result.exception.details['missing']),2)

    def test_names_allow_accessibility_line_breaks(self):
        c=self.collector()
        c.add((1,0),'菲比啾比\n新描述',43,'click','first')
        c.add((2,0),'菲八啾比\u3000新描述',45,'select','second')
        with patch.object(msaa.pet_registry,'observe_names'):
            self.assertEqual(len(c.resolve()),2)

    def test_truncated_tree_cannot_confirm_uniqueness(self):
        c=self.collector()
        c.add((1,0),'菲比啾比',43,'click',object())
        c.add((2,0),'菲八啾比',43,'click',object())
        with self.assertRaises(msaa.PetControlsError) as result:c.resolve(limited=True)
        self.assertTrue(result.exception.details['traversal_limited'])
