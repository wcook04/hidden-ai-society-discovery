import json
from pathlib import Path
import subprocess
import sys
import unittest

CLIENT = Path(__file__).resolve().parents[1] / "client"
sys.path.insert(0, str(CLIENT))
from retry_budget import plan_next


class RetryBudgetTests(unittest.TestCase):
    def plan(self, count, elapsed, outcome="ready", **kw):
        return plan_next(requests_started=count, elapsed_seconds=elapsed, outcome=outcome, **kw)

    def test_immediate_success(self):
        self.assertTrue(self.plan(0, 0)["dispatch_allowed"])
        p = self.plan(1, 3, "valid_result", last_request_seconds=3)
        self.assertEqual((p["state"], p["remaining_seconds"]), ("COMPLETED", 27))
        self.assertFalse(p["dispatch_allowed"])

    def test_transient_then_success_and_recheck(self):
        p = self.plan(1, 7, "transient_read", last_request_seconds=7)
        self.assertEqual((p["state"], p["wait_seconds"]), ("WAIT", 2))
        self.assertFalse(p["dispatch_allowed"])
        self.assertEqual(self.plan(1, 8, not_before_seconds=p["earliest_start_seconds"])["state"], "WAIT")
        self.assertTrue(self.plan(1, 9, not_before_seconds=p["earliest_start_seconds"])["dispatch_allowed"])
        done = self.plan(2, 13, "valid_result", last_request_seconds=4)
        self.assertEqual((done["state"], done["remaining_seconds"]), ("COMPLETED", 17))

    def test_retry_after_longer_than_remaining_budget_does_not_wait(self):
        p = self.plan(1, 10, "transient_read", retry_after_seconds=60, last_request_seconds=10)
        self.assertEqual((p["state"], p["earliest_start_seconds"]), ("DEFERRED", 70))
        self.assertEqual(p["wait_seconds"], 0)
        self.assertFalse(p["dispatch_allowed"])

    def test_backoff_and_full_request_reservation(self):
        p = self.plan(1, 5, "transient_read", retry_after_seconds=10)
        self.assertEqual(p["wait_seconds"], 10)
        self.assertEqual(self.plan(1, 18)["state"], "READY")
        self.assertEqual(self.plan(1, 18.001)["state"], "DEFERRED")
        self.assertEqual(self.plan(1, 17, "transient_read")["state"], "DEFERRED")
        self.assertEqual(self.plan(1, 19, not_before_seconds=15)["state"], "DEFERRED")

    def test_no_fifth_request_but_fourth_result_can_complete(self):
        self.assertEqual(self.plan(4, 16)["state"], "DEFERRED")
        self.assertEqual(self.plan(4, 30, "valid_result")["state"], "COMPLETED")

    def test_reported_time_and_count_overruns_block(self):
        for count, elapsed, duration in [(1, 13, 13), (5, 20, 1), (2, 31, 10)]:
            with self.subTest(count=count, elapsed=elapsed):
                p = self.plan(count, elapsed, "valid_result", last_request_seconds=duration)
                self.assertEqual(p["state"], "BLOCKED")
                self.assertTrue(p["budget_exceeded"])
                self.assertFalse(p["dispatch_allowed"])

    def test_uncertain_write_and_permanent_error_never_dispatch(self):
        for elapsed in (5, 31):
            p = self.plan(1, elapsed, "unknown_write")
            self.assertEqual(p["state"], "RECONCILIATION_NEEDED")
            self.assertFalse(p["dispatch_allowed"])
        self.assertEqual(self.plan(1, 1, "blocked")["state"], "BLOCKED")

    def test_invalid_inputs(self):
        base = dict(requests_started=1, elapsed_seconds=7, outcome="transient_read")
        for patch in [dict(requests_started=True), dict(requests_started=-1),
                      dict(requests_started=1.5), dict(elapsed_seconds=float("nan")),
                      dict(retry_after_seconds=float("inf")), dict(retry_after_seconds=-1),
                      dict(request_seconds=31), dict(total_seconds=0), dict(max_requests=0),
                      dict(last_request_seconds=8), dict(outcome="automatic_write"),
                      dict(requests_started=0), dict(not_before_seconds=-1)]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                plan_next(**(base | patch))
        with self.assertRaises(ValueError):
            self.plan(1, 7, "valid_result", retry_after_seconds=60)

    def test_cli_valid_deferred_and_invalid_input(self):
        def run(data):
            return subprocess.run([sys.executable, "-B", str(CLIENT / "retry_budget.py")],
                                  input=data, capture_output=True, timeout=5)
        r = run(b'{"requests_started":1,"elapsed_seconds":10,"outcome":"transient_read","retry_after_seconds":60}')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["state"], "DEFERRED")
        for data in [b'[]', b'{', b'{"token":"private-test-value"}', b'x' * 32769]:
            r = run(data)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(json.loads(r.stdout), {"ok": False, "code": "invalid_budget_input"})
            self.assertNotIn(b'private-test-value', r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
