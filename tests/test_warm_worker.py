from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from isolated_calls import WarmWorker
import cg_ipc
from request_lifecycle import claim

SERVER = '''import sys,json,os,time
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 if r['action']=='slow':time.sleep(30)
 print(json.dumps({'job':r['job'],'result':{'pid':os.getpid()}}),flush=True)
'''


class WarmWorkerTests(unittest.TestCase):
    def test_two_calls_reuse_one_process(self):
        worker=WarmWorker([sys.executable,'-u','-c',SERVER])
        try:
            first=worker.run('toggle','a'*32,3)
            second=worker.run('toggle','b'*32,3)
            self.assertEqual(first['pid'],second['pid'])
        finally:worker.close()

    def test_timeout_ends_worker_and_next_call_restarts(self):
        worker=WarmWorker([sys.executable,'-u','-c',SERVER])
        try:
            first=worker.run('toggle','a'*32,3)
            with self.assertRaises(TimeoutError):worker.run('slow','b'*32,.15)
            self.assertIsNone(worker.process)
            self.assertNotEqual(worker.run('toggle','c'*32,3)['pid'],first['pid'])
        finally:worker.close()

    def test_direct_ipc_receipt_and_failure(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(cg_ipc,'RUNTIME',Path(folder)):
            for outcome,code in [('completed',0),('cancelled',1)]:
                ticket=cg_ipc.Ticket('play-take-off',hold=True)
                command=json.loads(next((Path(folder)/'commands').glob(ticket.command_id+'.json')).read_text())
                self.assertTrue(command['hold'])
                ticket.path.parent.mkdir(exist_ok=True)
                ticket.path.write_text(json.dumps({'command_id':ticket.command_id,'result':outcome}))
                output,_=ticket.communicate(1)
                self.assertEqual(ticket.returncode,code)
                self.assertEqual(json.loads(output)['result'],outcome)

    def test_cancelled_queued_cg_cannot_start_later(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(cg_ipc,'RUNTIME',Path(folder)):
            ticket=cg_ipc.Ticket('play-take-off',hold=True)
            command=json.loads((Path(folder)/'commands'/(ticket.command_id+'.json')).read_text())
            ticket.terminate()
            self.assertFalse(claim(Path(folder)/'cg-decisions',command))
