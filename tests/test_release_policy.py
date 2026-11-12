import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('retention',Path(__file__).resolve().parents[1]/'tools/retire_releases.py')
retention=importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class ReleasePolicyTests(unittest.TestCase):
    def release(self,number,**values):
        return dict(dict(id=number,tag_name='v1.3.'+str(number),draft=False,prerelease=False,
                         assets=[{'id':number,'name':'feibi-feiba-pet-switch-v1.3.'+str(number)+'-windows.zip','state':'uploaded'}]),**values)

    def test_latest_draft_and_prerelease_are_not_retired(self):
        latest=self.release(3)
        releases=[self.release(1),latest,self.release(2,draft=True),self.release(0,prerelease=True),self.release(4)]
        plan=retention.retirement_plan(releases,latest)
        self.assertEqual([r['id'] for r,_ in plan],[1])

    def test_no_deletion_before_latest_upload(self):
        with self.assertRaises(ValueError):
            retention.retirement_plan([self.release(1)],self.release(3,assets=[]))

    def test_unrelated_assets_are_retained(self):
        old=self.release(1)
        old['assets'].append({'id':99,'name':'notes.pdf','state':'uploaded'})
        _,assets=retention.retirement_plan([old],self.release(3))[0]
        self.assertEqual([a['id'] for a in assets],[1])
