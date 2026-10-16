"""The ledger stays in the home directory, never in the application folder."""

import os
import tempfile
import unittest
from pathlib import Path

from streamdonations.paths import DataPathError, resolve_data_dir, user_data_dir


class DataDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # resolve(): Windows temp paths can be 8.3 short names.
        self.home = Path(self.tmp.name).resolve() / "home"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _legacy(self) -> Path:
        project = self.home / "Projects" / "stream-donations"
        marker = project / "source" / "collector" / "streamdonations"
        marker.mkdir(parents=True)
        (marker / "youtube.py").write_text("# marker\n", encoding="utf-8")
        database = project / "data" / "donations.db"
        database.parent.mkdir()
        database.write_bytes(b"legacy-db")
        Path(str(database) + "-wal").write_bytes(b"wal")
        return database

    def test_platform_directories_are_under_home(self):
        self.assertEqual(
            user_data_dir(home=self.home, platform="linux", environ={}),
            self.home / ".local" / "share" / "stream-donations",
        )
        self.assertEqual(
            user_data_dir(home=self.home, platform="darwin", environ={}),
            self.home / "Library" / "Application Support" / "stream-donations",
        )
        roaming = self.home / "AppData" / "Roaming"
        self.assertEqual(
            user_data_dir(home=self.home, platform="win32", environ={"APPDATA": str(roaming)}),
            roaming / "stream-donations",
        )

    def test_xdg_outside_home_is_ignored(self):
        path = user_data_dir(home=self.home, platform="linux", environ={"XDG_DATA_HOME": "/tmp/outside"})
        self.assertEqual(path, self.home / ".local" / "share" / "stream-donations")

    def test_app_tree_path_migrates_the_old_database(self):
        legacy = self._legacy()
        resolved = resolve_data_dir(
            environ={"STREAM_DONATIONS_DATA": str(legacy.parent)},
            home=self.home,
            platform="linux",
            legacy=[legacy],
        )
        self.assertEqual(resolved, self.home / ".local" / "share" / "stream-donations")
        self.assertEqual((resolved / "donations.db").read_bytes(), b"legacy-db")
        self.assertEqual((resolved / "donations.db-wal").read_bytes(), b"wal")
        self.assertFalse(legacy.exists())
        if os.name != "nt":  # Windows has no Unix permission bits.
            self.assertEqual((resolved / "donations.db").stat().st_mode & 0o777, 0o600)

    def test_default_home_path_still_migrates_when_the_env_points_there(self):
        legacy = self._legacy()
        home_dir = self.home / ".local" / "share" / "stream-donations"
        resolved = resolve_data_dir(
            environ={"STREAM_DONATIONS_DATA": str(home_dir)},
            home=self.home,
            platform="linux",
            legacy=[legacy],
        )
        self.assertEqual(resolved, home_dir)
        self.assertEqual((home_dir / "donations.db").read_bytes(), b"legacy-db")
        self.assertFalse(legacy.exists())

    def test_custom_home_directory_is_kept_and_not_overwritten(self):
        legacy = self._legacy()
        custom = self.home / "ledgers"
        resolved = resolve_data_dir(
            explicit=str(custom),
            home=self.home,
            platform="linux",
            legacy=[legacy],
            environ={},
        )
        self.assertEqual(resolved, custom.resolve())
        self.assertTrue(legacy.is_file())

    def test_path_outside_home_is_refused(self):
        with self.assertRaises(DataPathError):
            resolve_data_dir(
                explicit="/tmp/not-home",
                home=self.home,
                platform="linux",
                legacy=[],
                environ={},
            )


if __name__ == "__main__":
    unittest.main()
