#!/usr/bin/env python3
"""Unit tests for feature_discovery.py.

Run: python3 ~/.ai/bin/feature_discovery_test.py

Phase 0.4 builds the capability table from this parser, and Phase 0.5 matches feature docs with it.
A silent mis-parse here does not fail loudly — it just hands the orchestrator a wrong table, so
every test names the defect it pins.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feature_discovery as fd  # noqa: E402


class TestParseFrontmatter(unittest.TestCase):
    def test_inline_values(self) -> None:
        fm = fd.parse_frontmatter("---\nname: thing\ndescription: Does a thing.\n---\nBody\n")
        self.assertEqual("thing", fm["name"])
        self.assertEqual("Does a thing.", fm["description"])

    def test_folded_block_scalar_is_read_not_stored_as_its_marker(self) -> None:
        """`description: >-` stored the literal ">-" and turned the continuation line into a bogus
        key, so every folded skill reached the capability table described as ">-"."""
        fm = fd.parse_frontmatter(
            "---\n"
            "name: android-navigation\n"
            "description: >-\n"
            "  Universal in-app navigation: follow this repo's existing\n"
            "  NavHost/graph/fragments.\n"
            "---\nBody\n"
        )
        self.assertEqual(["name", "description"], list(fm))
        self.assertIn("Universal in-app navigation", fm["description"])
        self.assertIn("NavHost/graph/fragments.", fm["description"])
        self.assertNotEqual(">-", fm["description"])

    def test_literal_block_scalar(self) -> None:
        fm = fd.parse_frontmatter("---\nname: x\ndescription: |\n  line one\n  line two\n---\n")
        self.assertIn("line one", fm["description"])
        self.assertIn("line two", fm["description"])

    def test_yaml_list_value(self) -> None:
        fm = fd.parse_frontmatter(
            "---\ncovers:\n  - features/a\n  - features/b\n  - features/c\n---\n"
        )
        self.assertEqual(["features/a", "features/b", "features/c"], fm["covers"])

    def test_keys_after_a_block_scalar_are_still_parsed(self) -> None:
        fm = fd.parse_frontmatter(
            "---\ndescription: >-\n  folded text here\nversion: 2\nname: last\n---\n"
        )
        self.assertIn("folded text here", fm["description"])
        self.assertEqual("2", fm["version"])
        self.assertEqual("last", fm["name"])

    def test_nested_mapping_does_not_clobber_top_level_keys(self) -> None:
        """A `metadata:` block containing its own `name:`/`description:` overwrote the skill's real
        ones, so `list_skills` wrote the wrong entry into the capability table Phase 0.4 reads."""
        fm = fd.parse_frontmatter(
            "---\n"
            "name: real-skill\n"
            "description: Does the real thing\n"
            "metadata:\n"
            "  name: internal\n"
            "  description: internal blurb\n"
            "  type: feedback\n"
            "---\nBody\n"
        )
        self.assertEqual("real-skill", fm["name"])
        self.assertEqual("Does the real thing", fm["description"])
        self.assertNotIn("type", fm)

    def test_a_bare_key_can_still_open_a_list_after_the_nested_fix(self) -> None:
        fm = fd.parse_frontmatter("---\ncovers:\n  - features/a\n  - features/b\n---\n")
        self.assertEqual(["features/a", "features/b"], fm["covers"])

    def test_body_after_the_closing_delimiter_is_ignored(self) -> None:
        fm = fd.parse_frontmatter("---\nname: x\n---\nkey: not-frontmatter\n")
        self.assertNotIn("key", fm)

    def test_truncated_read_without_a_closing_delimiter_is_not_frontmatter(self) -> None:
        """Parsing on past a truncated read continued into the markdown body, where an unindented
        `description:` line would overwrite the real field in the capability table."""
        fm = fd.parse_frontmatter("---\nname: real\ndescription: real one\n")
        self.assertEqual({}, fm)

    def test_no_frontmatter(self) -> None:
        self.assertEqual({}, fd.parse_frontmatter("# Just a heading\n"))


class TestInferCovers(unittest.TestCase):
    def test_yaml_list_covers_every_entry(self) -> None:
        """Every feature README in the target repo writes `covers` as a block list. Parsing only the
        inline form silently produced the parent-dir fallback, so a ticket touching a sibling module
        the README genuinely covers was never matched to it."""
        covers = fd.infer_covers("features/flashcards/README.md",
                                 ["features/flashcards", "features/flashcards-old",
                                  "features/flashcards-base"])
        self.assertEqual(3, len(covers))
        self.assertIn("features/flashcards-old", covers)

    def test_inline_comma_separated_still_works(self) -> None:
        covers = fd.infer_covers("features/x/README.md", "features/x, features/y")
        self.assertEqual(["features/x", "features/y"], covers)

    def test_absent_covers_falls_back_to_the_parent_directory(self) -> None:
        self.assertEqual(["features/x"], fd.infer_covers("features/x/README.md", None))
        self.assertEqual(["features/x"], fd.infer_covers("features/x/README.md", ""))


class TestListSkills(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _skill(self, name: str, body: str) -> None:
        os.makedirs(os.path.join(self.dir, name))
        with open(os.path.join(self.dir, name, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(body)

    def test_folded_descriptions_reach_the_capability_table(self) -> None:
        self._skill("folded", "---\nname: folded\ndescription: >-\n  A real description.\n---\n")
        self._skill("inline", "---\nname: inline\ndescription: Plain one.\n---\n")
        rows = {name: desc for name, desc, _ in fd.list_skills(self.dir)}
        self.assertEqual("A real description.", rows["folded"])
        self.assertEqual("Plain one.", rows["inline"])

    def test_name_falls_back_to_the_directory(self) -> None:
        self._skill("no-name", "---\ndescription: Nameless.\n---\n")
        rows = {name for name, _, _ in fd.list_skills(self.dir)}
        self.assertIn("no-name", rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
