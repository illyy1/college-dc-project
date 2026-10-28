"""Threads and a shared resource: one fault, one maintenance call.

The dashboard's microphone and camera threads (and its quiet-period
watchdog) can report the same fault at the same moment. The shared resource
is the fault's open call. Without a lock, two threads can both check "this
fault has no open call" before either has opened one, and then both open a
call: two technicians sent to one fault. FaultCalls makes the check and the
open one step under a single Lock. No network call is made while it is held."""

import threading


class FaultCalls:
    """fault key -> its open maintenance call, guarded by one Lock."""

    def __init__(self):
        self._calls = {}
        self._lock = threading.Lock()

    def open_once(self, fault_key, open_call):
        """(the fault's open call, False), or, if it has none, (open_call()'s
        new call, True). open_call() returns None when no call could be
        opened; the fault then stays without one."""
        with self._lock:
            call = self._calls.get(fault_key)
            if call is not None:
                return call, False
            call = open_call()
            if call is not None:
                self._calls[fault_key] = call
            return call, True

    def adopt(self, fault_key, call):
        """Takes over a call opened earlier (a previous run's)."""
        with self._lock:
            self._calls[fault_key] = call

    def get(self, fault_key):
        with self._lock:
            return self._calls.get(fault_key)

    def pop(self, fault_key):
        """Removes and returns the fault's open call (None if it has none)."""
        with self._lock:
            return self._calls.pop(fault_key, None)


def report_from_threads(fault_calls, fault_key, open_call, threads=8):
    """Starts `threads` threads that all report the same fault, and only then
    waits for them with join. Returns each thread's (call, is_new), in the
    order the threads were started."""
    results = [None] * threads

    def report(index):
        results[index] = fault_calls.open_once(fault_key, open_call)

    workers = [threading.Thread(target=report, args=(index,)) for index in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return results
