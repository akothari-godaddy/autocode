import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from leasequeue import LeaseQueue

class QueueContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'db'; self.q=LeaseQueue(self.path)
    def test_deadline_boundary_fencing_and_retry_order(self):
        self.q.enqueue('a','A'); self.q.enqueue('b','B')
        first=self.q.claim(0,10); second=self.q.claim(0,10)
        self.assertIsNone(self.q.claim(9,10))
        self.assertFalse(self.q.ack('a',first['token'],10))
        again=LeaseQueue(self.path).claim(10,10)
        self.assertEqual(again['id'],'a'); self.assertNotEqual(again['token'],first['token'])
        self.assertFalse(self.q.ack('a',first['token'],11))
        self.assertFalse(self.q.nack('a',first['token'],11))
        self.assertTrue(self.q.nack('a',again['token'],11))
        newer=self.q.claim(11,10); self.assertEqual(newer['id'],'a')
        self.assertTrue(self.q.ack('a',newer['token'],12))
        self.assertFalse(self.q.enqueue('a','A'))
        with self.assertRaises(ValueError): self.q.enqueue('a','different')
        self.assertEqual(self.q.pending(),1)
    def test_concurrent_claims_are_unique(self):
        for i in range(8): self.q.enqueue(str(i),str(i))
        barrier=threading.Barrier(8)
        def claim(i):
            q=LeaseQueue(self.path); barrier.wait(timeout=5); return q.claim(0,5)
        with ThreadPoolExecutor(max_workers=8) as pool: rows=list(pool.map(claim,range(8)))
        self.assertEqual({row['id'] for row in rows},{str(i) for i in range(8)})
        self.assertEqual(len({row['token'] for row in rows}),8)
        self.assertEqual(self.q.pending(),8)
    def test_invalid_time_and_unknown_ack(self):
        self.q.enqueue('a','A')
        for now,lease in ((-1,2),(True,2),(0,0),(0,True),(1.5,2)):
            with self.assertRaises(ValueError): self.q.claim(now,lease)
        self.assertFalse(self.q.ack('missing','token',0))
        self.assertEqual(self.q.claim(0,1)['id'],'a')

    def test_large_injected_times_and_deadlines_preserve_fencing(self):
        now=2**63-1
        self.q.enqueue('a','A')
        first=self.q.claim(now,1)
        self.assertIs(type(first['deadline']),int)
        self.assertEqual(first['deadline'],now+1)
        self.assertIsNone(LeaseQueue(self.path).claim(now,1))
        self.assertFalse(self.q.ack('a',first['token'],now+1))
        large=2**120+19
        second=LeaseQueue(self.path).claim(now+1,large)
        self.assertEqual(second['deadline'],now+1+large)
        self.assertNotEqual(second['token'],first['token'])
        self.assertFalse(self.q.ack('a',first['token'],now+2))
        self.assertTrue(self.q.nack('a',second['token'],now+2))
        third=self.q.claim(large,large)
        self.assertEqual(third['deadline'],2*large)
        self.assertTrue(LeaseQueue(self.path).ack('a',third['token'],large+1))
        self.assertEqual(self.q.pending(),0)
        self.assertIsNone(self.q.claim(2*large,1))

    def test_large_leases_keep_fifo_among_eligible_jobs(self):
        large=2**100+11
        self.q.enqueue('a','A'); self.q.enqueue('b','B')
        first=self.q.claim(0,large)
        self.assertEqual(self.q.claim(large-1,2)['id'],'b')
        reclaimed=LeaseQueue(self.path).claim(large,3)
        self.assertEqual(reclaimed['id'],'a')
        self.assertNotEqual(reclaimed['token'],first['token'])
