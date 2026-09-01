from __future__ import annotations

import unittest

from tmem.fuzzy import fuzzy_filter, fuzzy_score


class FuzzyTests(unittest.TestCase):
    def test_missing_character_style_query_matches(self) -> None:
        self.assertIsNotNone(fuzzy_score("dcker", "docker compose logs"))

    def test_wrong_order_does_not_match(self) -> None:
        self.assertIsNone(fuzzy_score("dkocre", "docker"))

    def test_contiguous_match_ranks_before_gappy_match(self) -> None:
        items = ["d-o-c-k-e-r", "docker"]
        self.assertEqual(fuzzy_filter("docker", items, lambda item: item), ["docker", "d-o-c-k-e-r"])


if __name__ == "__main__":
    unittest.main()
