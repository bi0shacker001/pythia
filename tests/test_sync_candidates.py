"""Real tests for pythia.core.sync.candidates -- pure address logic, no I/O."""

import unittest

from pythia.core.sync import candidates


class NormalizeCandidateTests(unittest.TestCase):
    def test_valid_ipv4(self):
        self.assertEqual("203.0.113.5:24030", candidates.normalize_candidate("203.0.113.5:24030"))

    def test_valid_ipv6(self):
        self.assertEqual("[::1]:24030", candidates.normalize_candidate("[::1]:24030"))
        self.assertEqual("[2606:4700:4700::1111]:24030", candidates.normalize_candidate("[2606:4700:4700::1111]:24030"))

    def test_documentation_range_ipv6_is_rejected_as_non_connectable(self):
        """2001:db8::/32 (RFC 3849) is reserved for documentation -- never
        a real, connectable candidate, same class of trap as RFC 5737's
        IPv4 TEST-NET ranges (2026-08-09, "internal IPv6 direct
        connectivity and dual-stack route selection")."""
        self.assertIsNone(candidates.normalize_candidate("[2001:db8::1]:24030"))

    def test_valid_hostname(self):
        self.assertEqual("device.local:24030", candidates.normalize_candidate("device.local:24030"))

    def test_whitespace_is_trimmed(self):
        self.assertEqual("203.0.113.5:24030", candidates.normalize_candidate("  203.0.113.5:24030  "))

    def test_rejects_missing_port(self):
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5"))

    def test_rejects_port_out_of_range(self):
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:0"))
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:70000"))
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:99999999999"))

    def test_rejects_non_numeric_port(self):
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:abc"))

    def test_rejects_non_string(self):
        self.assertIsNone(candidates.normalize_candidate(None))
        self.assertIsNone(candidates.normalize_candidate(12345))
        self.assertIsNone(candidates.normalize_candidate(["203.0.113.5:24030"]))

    def test_rejects_empty_and_garbage(self):
        self.assertIsNone(candidates.normalize_candidate(""))
        self.assertIsNone(candidates.normalize_candidate("not-a-real-candidate-at-all"))
        self.assertIsNone(candidates.normalize_candidate("javascript:alert(1)"))
        self.assertIsNone(candidates.normalize_candidate("://:::"))

    def test_rejects_embedded_control_characters(self):
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:24030\r\nEvilHeader: 1"))
        self.assertIsNone(candidates.normalize_candidate("203.0.113.5:24030\x00"))

    def test_rejects_malformed_ipv6_bracket_content(self):
        self.assertIsNone(candidates.normalize_candidate("[not-an-ipv6]:24030"))

    def test_rejects_invalid_hostname_labels(self):
        self.assertIsNone(candidates.normalize_candidate("-bad-label.example:24030"))
        self.assertIsNone(candidates.normalize_candidate("has space.example:24030"))

    def test_scoped_link_local_accepted_with_zone(self):
        self.assertEqual("[fe80::1%eth0]:24030", candidates.normalize_candidate("[fe80::1%eth0]:24030"))
        self.assertEqual("[fe80::1%12]:24030", candidates.normalize_candidate("[fe80::1%12]:24030"))

    def test_unscoped_link_local_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[fe80::1]:24030"))

    def test_link_local_with_empty_zone_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[fe80::1%]:24030"))

    def test_zone_on_non_link_local_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[2606:4700:4700::1111%eth0]:24030"))

    def test_unspecified_ipv6_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[::]:24030"))

    def test_multicast_ipv6_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[ff02::1]:24030"))

    def test_ipv4_mapped_ipv6_literal_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[::ffff:1.2.3.4]:24030"))

    def test_loopback_ipv6_is_accepted(self):
        self.assertEqual("[::1]:24030", candidates.normalize_candidate("[::1]:24030"))

    def test_malformed_zone_token_is_rejected(self):
        self.assertIsNone(candidates.normalize_candidate("[fe80::1%bad zone]:24030"))
        self.assertIsNone(candidates.normalize_candidate("[fe80::1%" + "x" * 40 + "]:24030"))


class HasZoneAndStripUnpublishableTests(unittest.TestCase):
    def test_has_zone_true_for_scoped_candidate(self):
        self.assertTrue(candidates.has_zone("[fe80::1%eth0]:24030"))

    def test_has_zone_false_for_unscoped_candidates(self):
        self.assertFalse(candidates.has_zone("[::1]:24030"))
        self.assertFalse(candidates.has_zone("203.0.113.5:24030"))
        self.assertFalse(candidates.has_zone("device.local:24030"))

    def test_strip_unpublishable_candidates_removes_scoped_only(self):
        candidates_list = ["[fe80::1%eth0]:24030", "203.0.113.5:24030", "[2606:4700:4700::1111]:24030"]
        self.assertEqual(
            ["203.0.113.5:24030", "[2606:4700:4700::1111]:24030"],
            candidates.strip_unpublishable_candidates(candidates_list),
        )

    def test_strip_unpublishable_candidates_never_mutates_input(self):
        original = ["[fe80::1%eth0]:24030", "203.0.113.5:24030"]
        snapshot = list(original)
        candidates.strip_unpublishable_candidates(original)
        self.assertEqual(snapshot, original)


class IsPrivateOrLocalTests(unittest.TestCase):
    def test_private_ipv4_is_local(self):
        self.assertTrue(candidates.is_private_or_local("192.168.1.5:24030"))
        self.assertTrue(candidates.is_private_or_local("10.0.0.1:24030"))
        self.assertTrue(candidates.is_private_or_local("172.16.0.1:24030"))

    def test_loopback_is_local(self):
        self.assertTrue(candidates.is_private_or_local("127.0.0.1:24030"))
        self.assertTrue(candidates.is_private_or_local("[::1]:24030"))

    def test_link_local_is_local(self):
        self.assertTrue(candidates.is_private_or_local("169.254.1.1:24030"))

    def test_public_ipv4_is_not_local(self):
        self.assertFalse(candidates.is_private_or_local("203.0.113.5:24030"))
        self.assertFalse(candidates.is_private_or_local("198.51.100.5:24030"))

    def test_unique_local_ipv6_is_local(self):
        self.assertTrue(candidates.is_private_or_local("[fd00::5]:24030"))

    def test_documentation_ipv6_is_not_local(self):
        self.assertFalse(candidates.is_private_or_local("[2001:db8::5]:24030"))

    def test_hostname_is_never_considered_local(self):
        """No DNS lookup is ever performed here -- a hostname's real scope
        is unknown without resolving it, which this pure function
        deliberately never does."""
        self.assertFalse(candidates.is_private_or_local("device.local:24030"))

    def test_malformed_candidate_is_not_local(self):
        self.assertFalse(candidates.is_private_or_local("garbage"))


class MergeCandidatesTests(unittest.TestCase):
    def test_merges_and_dedupes_across_sources(self):
        merged = candidates.merge_candidates(
            ["192.168.1.5:24030"], ["203.0.113.5:24030", "192.168.1.5:24030"], ["203.0.113.5:24030"]
        )
        self.assertEqual(["192.168.1.5:24030", "203.0.113.5:24030"], merged)

    def test_first_occurrence_wins_order(self):
        merged = candidates.merge_candidates(["203.0.113.5:24030"], ["192.168.1.5:24030"])
        self.assertEqual(["203.0.113.5:24030", "192.168.1.5:24030"], merged)

    def test_drops_malformed_entries_from_any_source(self):
        merged = candidates.merge_candidates(["not-real", "203.0.113.5:24030", ""], [None, "192.168.1.5:24030"])
        self.assertEqual(["203.0.113.5:24030", "192.168.1.5:24030"], merged)

    def test_empty_and_none_sources_are_fine(self):
        self.assertEqual([], candidates.merge_candidates())
        self.assertEqual([], candidates.merge_candidates(None, []))

    def test_equivalent_ipv6_forms_dedupe(self):
        merged = candidates.merge_candidates(
            ["[2606:4700:4700:0000:0000:0000:0000:1111]:24030"], ["[2606:4700:4700::1111]:24030"]
        )
        self.assertEqual(1, len(merged))


class RankCandidatesTests(unittest.TestCase):
    def test_local_candidates_ranked_first(self):
        ranked = candidates.rank_candidates(["203.0.113.5:24030", "192.168.1.5:24030", "198.51.100.1:24030"])
        self.assertEqual(["192.168.1.5:24030", "203.0.113.5:24030", "198.51.100.1:24030"], ranked)

    def test_stable_within_each_group(self):
        ranked = candidates.rank_candidates(["10.0.0.2:24030", "10.0.0.1:24030", "203.0.113.9:24030", "203.0.113.5:24030"])
        self.assertEqual(["10.0.0.2:24030", "10.0.0.1:24030", "203.0.113.9:24030", "203.0.113.5:24030"], ranked)

    def test_all_local_or_all_public(self):
        self.assertEqual(["10.0.0.1:24030"], candidates.rank_candidates(["10.0.0.1:24030"]))
        self.assertEqual(["203.0.113.5:24030"], candidates.rank_candidates(["203.0.113.5:24030"]))
        self.assertEqual([], candidates.rank_candidates([]))


class MergeAndRankTests(unittest.TestCase):
    def test_combines_merge_and_rank(self):
        result = candidates.merge_and_rank(
            ["203.0.113.5:24030"],  # global discovery, tried first but public
            ["192.168.1.5:24030"],  # LAN
            ["203.0.113.5:24030", "192.168.1.5:24030"],  # trust store duplicates
        )
        self.assertEqual(["192.168.1.5:24030", "203.0.113.5:24030"], result)


class InterleaveFamiliesTests(unittest.TestCase):
    def test_interleaves_ipv6_and_ipv4_starting_with_ipv6(self):
        result = candidates.interleave_families(
            ["203.0.113.5:24030", "198.51.100.1:24030", "[2606:4700:4700::1111]:24030", "[2001:4860:4860::8888]:24030"]
        )
        self.assertEqual(
            [
                "[2606:4700:4700::1111]:24030",
                "203.0.113.5:24030",
                "[2001:4860:4860::8888]:24030",
                "198.51.100.1:24030",
            ],
            result,
        )

    def test_preserves_relative_order_within_each_family(self):
        result = candidates.interleave_families(["10.0.0.2:24030", "10.0.0.1:24030"])
        self.assertEqual(["10.0.0.2:24030", "10.0.0.1:24030"], result)

    def test_all_one_family_is_unchanged(self):
        self.assertEqual(["203.0.113.5:24030"], candidates.interleave_families(["203.0.113.5:24030"]))
        self.assertEqual(
            ["[2606:4700:4700::1111]:24030"], candidates.interleave_families(["[2606:4700:4700::1111]:24030"])
        )

    def test_empty_list(self):
        self.assertEqual([], candidates.interleave_families([]))


class RankAndInterleaveTests(unittest.TestCase):
    def test_local_first_then_interleaved_within_each_group(self):
        result = candidates.rank_and_interleave(
            ["203.0.113.5:24030", "192.168.1.5:24030", "[2606:4700:4700::1111]:24030", "[fd00::5]:24030"]
        )
        # Local group: [fd00::5]:24030 (v6) then 192.168.1.5:24030 (v4) --
        # IPv6 first within the local partition. Public group: the one
        # public IPv6 then the one public IPv4.
        self.assertEqual(
            ["[fd00::5]:24030", "192.168.1.5:24030", "[2606:4700:4700::1111]:24030", "203.0.113.5:24030"],
            result,
        )

    def test_dedupes_across_sources_before_interleaving(self):
        result = candidates.rank_and_interleave(["203.0.113.5:24030"], ["203.0.113.5:24030"])
        self.assertEqual(["203.0.113.5:24030"], result)


if __name__ == "__main__":
    unittest.main()
