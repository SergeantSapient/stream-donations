"""Version classification: one number, two files, and a changelog note."""

import json
import tempfile
import unittest
from pathlib import Path

from streamdonations.versioning import apply_change, next_version, published_version


def _tree(root: Path, version: str = "1.2.3") -> None:
    init = root / "source/collector/streamdonations"
    electron = root / "source/electron"
    init.mkdir(parents=True)
    electron.mkdir(parents=True)
    (init / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (electron / "package.json").write_text(
        json.dumps({"name": "stream-donations", "version": version, "dependencies": {"electron": "33.4.11"}}) + "\n",
        encoding="utf-8",
    )
    (root / "TODO.md").write_text(
        f"Current app version is `{version}` (`source/collector/streamdonations/__init__.py`).\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\nRules stay above the sections.\n\n"
        f"## {version} — 2026-01-01\n\n- 2026-01-01 — Minor. Earlier note.\n",
        encoding="utf-8",
    )


class VersioningTests(unittest.TestCase):
    def test_classification_moves_one_part_of_the_number(self):
        self.assertEqual(next_version((1, 2, 3), "peripheral"), (1, 2, 3))
        self.assertEqual(next_version((1, 2, 3), "minor"), (1, 2, 4))
        self.assertEqual(next_version((1, 2, 3), "considerable"), (1, 3, 0))
        self.assertEqual(next_version((1, 2, 3), "major"), (2, 0, 0))
        with self.assertRaises(ValueError):
            next_version((1, 0, 0), "wishlist")

    def test_considerable_opens_a_section_and_keeps_the_previous_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            version = apply_change(root, "considerable", "Open archive reads a JSONL export", "2026-10-08")
            self.assertEqual(version, "1.3.0")
            self.assertEqual(published_version(root), "1.3.0")
            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertLess(
                changelog.index("## 1.3.0 — 2026-10-08"),
                changelog.index("## 1.2.3 — 2026-01-01"),
            )
            self.assertIn("- 2026-10-08 — Considerable. Open archive reads a JSONL export.", changelog)
            self.assertIn('"version": "1.3.0"', (root / "source/electron/package.json").read_text(encoding="utf-8"))
            self.assertIn('"electron": "33.4.11"', (root / "source/electron/package.json").read_text(encoding="utf-8"))

    def test_peripheral_stays_on_the_current_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, "1.0.0")
            version = apply_change(root, "peripheral", "Corrected a help sentence", "2026-10-08")
            self.assertEqual(version, "1.0.0")
            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertEqual(changelog.count("## 1.0.0"), 1)
            self.assertIn("- 2026-10-08 — Peripheral. Corrected a help sentence.", changelog)
            self.assertLess(
                changelog.index("Peripheral. Corrected a help sentence."),
                changelog.index("Minor. Earlier note."),
            )

    def test_a_blank_summary_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, "1.0.0")
            with self.assertRaises(ValueError):
                apply_change(root, "minor", "   ", "2026-10-08")

    def test_published_files_agree(self):
        root = Path(__file__).resolve().parents[3]
        self.assertEqual(published_version(root), "1.6.2")
        todo = (root / "TODO.md").read_text(encoding="utf-8")
        self.assertIn("Wishlist items are considerable unless the request says the update is major.", todo)
        self.assertNotIn("Each item below is a **major**", todo)
