"""Finite structural checks for the attributed runtime-evidence example.

These checks inspect declared fields. They neither execute the reader nor verify
that a reporter's observations are true.
"""

import copy
import json
import unittest
from pathlib import Path


EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "tasks" / "runtime-evidence.json").read_text()
)["peer_review_example"]
EXPECTED_PROPERTY = "root and ancestor directory junctions rejected before HTTP"
EXPECTED_REVISION = "dcef24b3176e2fa2a6cfdd4b41457b768a0700a3"
EXPECTED_TARGET = {"os": "Windows", "python_series": "3.11"}
REQUIRED_CASES = {"native_root", "native_ancestor"}


def has_reported_native_pair(record):
    """Can one reported run support both named cases on the claimed environment?"""
    claim = record["claim"]
    target = claim.get("target")
    required = claim.get("required_cases")
    if (
        claim.get("property") != EXPECTED_PROPERTY
        or claim.get("code_revision") != EXPECTED_REVISION
        or target != EXPECTED_TARGET
        or not isinstance(required, list)
        or len(required) != 2
        or any(type(name) is not str for name in required)
        or set(required) != REQUIRED_CASES
    ):
        return False
    for observation in record["observations"]:
        environment = observation.get("environment") or {}
        python = environment.get("python")
        if not (
            observation.get("method") == "reported_native_execution"
            and observation.get("reported_tested_revision") == claim["code_revision"]
            and environment.get("os") == target["os"]
            and isinstance(python, str)
            and python.startswith(target["python_series"] + ".")
        ):
            continue
        cases = observation.get("cases", {})
        if all(
            (case := cases.get(name, {})).get("setup") == "reported_succeeded"
            and case.get("assertions") == "reported_reached"
            and case.get("verdict") == "reported_pass"
            and type(case.get("http_requests")) is int
            and case["http_requests"] == 0
            for name in required
        ):
            return True
    return False


class RuntimeEvidenceExampleTests(unittest.TestCase):
    def matching_windows_report(self):
        record = copy.deepcopy(EXAMPLE)
        synthetic = record["observations"][1]
        synthetic["id"] = "SYNTHETIC_TEST_ONLY"
        synthetic["reporter"] = "invented by this local test"
        synthetic["source"] = "synthetic:in-memory-fixture-not-a-run"
        synthetic["environment"]["python"] = "3.11.15"
        return record

    def test_published_sources_do_not_establish_windows_311(self):
        self.assertEqual(
            EXAMPLE["assessment"]["status"], "not_established_by_these_sources"
        )
        self.assertFalse(has_reported_native_pair(EXAMPLE))

    def test_mac_python_and_windows_os_are_not_joined_across_reports(self):
        observations = EXAMPLE["observations"]
        self.assertEqual(observations[0]["environment"]["python"], "3.11.15")
        self.assertEqual(observations[1]["environment"]["os"], "Windows")
        self.assertFalse(has_reported_native_pair(EXAMPLE))

    def test_matching_pair_would_qualify_only_as_a_reported_pair(self):
        # Synthetic metadata variant, not a newly observed Windows run.
        self.assertTrue(has_reported_native_pair(self.matching_windows_report()))

    def test_root_pass_with_ancestor_skip_does_not_qualify(self):
        record = self.matching_windows_report()
        record["observations"][1]["cases"]["native_ancestor"] = {
            "setup": "skipped", "assertions": "not_reached", "verdict": "not_run"
        }
        self.assertFalse(has_reported_native_pair(record))

    def test_requirements_cannot_drop_or_duplicate_the_ancestor(self):
        for required in ([], ["native_root"], ["native_root", "native_root"]):
            with self.subTest(required=required):
                record = self.matching_windows_report()
                record["observations"][1]["cases"]["native_ancestor"]["verdict"] = "reported_fail"
                record["claim"]["required_cases"] = required
                self.assertFalse(has_reported_native_pair(record))

    def test_malformed_or_extra_requirements_fail_closed(self):
        for required in (None, "native_root", ["native_root", 42],
                         ["native_root", {"name": "native_ancestor"}],
                         ["native_root", "native_ancestor", "symlink"]):
            with self.subTest(required=required):
                record = self.matching_windows_report()
                record["claim"]["required_cases"] = required
                self.assertFalse(has_reported_native_pair(record))
        record = self.matching_windows_report()
        del record["claim"]["required_cases"]
        self.assertFalse(has_reported_native_pair(record))

    def test_property_cannot_silently_change_to_one_fixture(self):
        record = self.matching_windows_report()
        record["claim"]["property"] = "root directory junction rejected before HTTP"
        self.assertFalse(has_reported_native_pair(record))

    def test_target_cannot_shift_to_the_reported_windows_312(self):
        record = copy.deepcopy(EXAMPLE)
        record["claim"]["target"]["python_series"] = "3.12"
        self.assertFalse(has_reported_native_pair(record))

    def test_claim_revision_cannot_shift(self):
        record = self.matching_windows_report()
        record["claim"]["code_revision"] = "another_revision"
        record["observations"][1]["reported_tested_revision"] = "another_revision"
        self.assertFalse(has_reported_native_pair(record))

    def test_construction_failure_cannot_become_a_reader_pass(self):
        record = self.matching_windows_report()
        record["observations"][1]["cases"]["native_ancestor"]["setup"] = "failed"
        self.assertFalse(has_reported_native_pair(record))

    def test_unreached_assertion_cannot_become_a_pass(self):
        record = self.matching_windows_report()
        record["observations"][1]["cases"]["native_ancestor"]["assertions"] = "not_reached"
        self.assertFalse(has_reported_native_pair(record))

    def test_reader_failure_is_distinct_from_setup_failure(self):
        record = self.matching_windows_report()
        record["observations"][1]["cases"]["native_ancestor"]["verdict"] = "reported_fail"
        self.assertFalse(has_reported_native_pair(record))

    def test_different_revision_cannot_satisfy_claim(self):
        record = self.matching_windows_report()
        record["observations"][1]["reported_tested_revision"] = "another_revision"
        self.assertFalse(has_reported_native_pair(record))

    def test_report_document_revision_is_not_the_tested_head(self):
        record = self.matching_windows_report()
        observation = record["observations"][1]
        self.assertNotEqual(observation["document_revision"],
                            observation["reported_tested_revision"])
        observation["reported_tested_revision"] = observation["document_revision"]
        self.assertFalse(has_reported_native_pair(record))

    def test_missing_tested_head_remains_unknown(self):
        record = self.matching_windows_report()
        del record["observations"][1]["reported_tested_revision"]
        self.assertFalse(has_reported_native_pair(record))

    def test_reviewed_revision_cannot_fill_missing_tested_head(self):
        record = self.matching_windows_report()
        observation = record["observations"][1]
        del observation["reported_tested_revision"]
        observation["reviewed_revision"] = EXPECTED_REVISION
        self.assertFalse(has_reported_native_pair(record))

    def test_bool_cannot_impersonate_zero_http_count(self):
        record = self.matching_windows_report()
        record["observations"][1]["cases"]["native_ancestor"]["http_requests"] = False
        self.assertFalse(has_reported_native_pair(record))


if __name__ == "__main__":
    unittest.main()
