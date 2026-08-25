import copy
import json
import os
import tempfile
import unittest

import _pathsetup  # noqa: F401
import srcscore as S


def load_raw_policy():
    with open(S.POLICY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class RealPolicyLoadsTests(unittest.TestCase):
    def test_real_policy_loads_and_validates(self):
        policy = S.load_policy()
        self.assertIn("tiers", policy)

    def test_load_policy_raises_on_missing_file(self):
        with self.assertRaises(S.PolicyError):
            S.load_policy("/nonexistent/path/policy.json")

    def test_load_policy_raises_on_malformed_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not valid json")
            with self.assertRaises(S.PolicyError):
                S.load_policy(path)


class ValidatePolicySchemaTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_raw_policy()

    def mutate(self, fn):
        p = copy.deepcopy(self.policy)
        fn(p)
        return p

    def test_missing_required_key_fails(self):
        p = self.mutate(lambda p: p.pop("tiers"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_non_numeric_tier_base_fails(self):
        p = self.mutate(lambda p: p["tiers"]["1"].__setitem__("base", "eighty"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_missing_block_tier_fails(self):
        p = self.mutate(lambda p: p["tiers"].pop("block"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_empty_verdict_bands_fails(self):
        p = self.mutate(lambda p: p["verdicts"].__setitem__("bands", []))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_unordered_verdict_bands_fail(self):
        def swap(p):
            b = p["verdicts"]["bands"]
            b[0], b[1] = b[1], b[0]
        p = self.mutate(swap)
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_lowest_band_must_start_at_zero(self):
        p = self.mutate(lambda p: p["verdicts"]["bands"][-1].__setitem__("min", 5))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_duplicate_domain_across_tiers_fails(self):
        def dup(p):
            p["domains"]["2"].append("nature.com")  # already tier 1
        p = self.mutate(dup)
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_domain_tier_without_tier_def_fails(self):
        def add_ghost_tier(p):
            p["domains"]["ghost"] = ["example.com"]
        p = self.mutate(add_ghost_tier)
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_bad_unregistered_tier_default_fails(self):
        p = self.mutate(lambda p: p["defaults"].__setitem__("unregistered_tier", "99"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_bad_default_field_fails(self):
        p = self.mutate(lambda p: p["defaults"].__setitem__("field", "klingon"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)

    def test_domains_value_must_be_a_list(self):
        p = self.mutate(lambda p: p["domains"].__setitem__("1", "nature.com"))
        with self.assertRaises(S.PolicyError):
            S.validate_policy(p)


if __name__ == "__main__":
    unittest.main()
