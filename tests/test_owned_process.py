import sys
import threading
import time
import unittest

from linkexpand.owned_process import run_worker,stop_workers,ACTIVE,LOCK

class WorkerTests(unittest.TestCase):
    def test_shutdown_stops_only_registered_worker(self):
        results=[]
        thread=threading.Thread(target=lambda:results.append(run_worker([sys.executable,'-c','import time; time.sleep(30)'],'',10)))
        thread.start()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with LOCK:running=bool(ACTIVE)
            if running:break
            time.sleep(.01)
        self.assertTrue(running)
        stop_workers();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertNotEqual(results[0].returncode,0)
        with LOCK:self.assertFalse(ACTIVE)

if __name__=='__main__':unittest.main()
