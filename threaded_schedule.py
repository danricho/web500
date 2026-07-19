import threading
import time
import traceback
from datetime import datetime
import schedule
import queue

class ThreadedSchedule:

  def __init__(self, workers=1, verbose=False):
    self.verbose = verbose
    # OPTIONAL HOOK CALLED AS on_error(job_name, traceback_text) AFTER A JOB RAISES -
    # THE OWNER CAN SURFACE IT (E.G. A TOAST). LOGGING HAPPENS HERE REGARDLESS.
    self.on_error = None
    self.jobqueue = queue.Queue()
    self._stop = False
    self.workers = []
    for i in range(0, workers):
      worker_thread = threading.Thread(target=self.worker_main, args=[i])
      worker_thread.start()
      self.workers.append(worker_thread)

  def printv(self, text):
    CYAN     = '\033[36m'
    RESET    = '\033[39m'
    if self.verbose: 
      print(f"{CYAN}{text}{RESET}")

  def worker_main(self, worker_id):
    self.printv(f"Worker {worker_id} started.")
    while not self._stop:
      # THE QUEUE WAIT AND THE JOB RUN ARE SEPARATE try BLOCKS ON PURPOSE: THE OLD
      # SINGLE BARE except SILENTLY DISCARDED EVERY ERROR RAISED BY QUEUED GAME WORK -
      # A JOB DIED HALF-DONE AND THE LOG SAID NOTHING. JOB ERRORS NOW ALWAYS LOG THE
      # FULL TRACEBACK (FILE/LINE OF THE CAUSE) AND FIRE THE on_error HOOK.
      try:
        job_func = self.jobqueue.get(block=True, timeout=1) # TIMEOUT SO stop() CAN LAND
      except queue.Empty:
        continue
      self.jobqueue.task_done()
      try:
        job_func()
      except Exception:
        job_name = getattr(job_func, "__name__", repr(job_func))
        tb = traceback.format_exc()
        RED, RESET = '\033[31m', '\033[39m'
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}|{RED}WORKER JOB FAILED: {job_name}\n{tb}{RESET}")
        if self.on_error:
          try:
            self.on_error(job_name, tb)
          except Exception:
            pass # THE ERROR HOOK MUST NEVER KILL THE WORKER
    self.printv(f"Worker {worker_id} stopped.")
  
  def stop(self):
    self.printv(f"Stopping ThreadedSchedule.")   
    self._stop = True
  
  def check_for_due(self):
    self.printv(f"Schedule due job checker started.")
    while not self._stop:
      schedule.run_pending()
      time.sleep(0.1)
    self.printv(f"Schedule due job checker stopped.")
      
  def check_for_due_thread(self):
    self.check_for_due_thread = threading.Thread(target=self.check_for_due)
    self.check_for_due_thread.start()  
