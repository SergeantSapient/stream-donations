"""Windows uses the packaged certificate bundle only when one is present."""

import os
import tempfile
import unittest
from pathlib import Path

from streamdonations.trust import bundled_ca_path, use_bundled_ca_bundle


class BundledCaTests(unittest.TestCase):
    def test_the_bundle_sits_beside_the_package(self):
        self.assertEqual(bundled_ca_path().name, "cacert.pem")
        self.assertEqual(bundled_ca_path().parent.name, "collector")

    def test_windows_uses_the_bundle_when_no_cert_env_is_set(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text("pem", encoding="utf-8")
            env = {}
            chosen = use_bundled_ca_bundle(bundle, platform="win32", environ=env)
        self.assertEqual(chosen, str(bundle))
        self.assertEqual(env["SSL_CERT_FILE"], str(bundle))

    def test_an_existing_cert_file_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text("pem", encoding="utf-8")
            env = {"SSL_CERT_FILE": "C:\\already.pem"}
            chosen = use_bundled_ca_bundle(bundle, platform="win32", environ=env)
        self.assertIsNone(chosen)
        self.assertEqual(env["SSL_CERT_FILE"], "C:\\already.pem")

    def test_an_existing_cert_dir_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text("pem", encoding="utf-8")
            env = {"SSL_CERT_DIR": "C:\\store"}
            chosen = use_bundled_ca_bundle(bundle, platform="win32", environ=env)
        self.assertIsNone(chosen)
        self.assertNotIn("SSL_CERT_FILE", env)

    def test_linux_does_not_switch_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text("pem", encoding="utf-8")
            env = {}
            chosen = use_bundled_ca_bundle(bundle, platform="linux", environ=env)
        self.assertIsNone(chosen)
        self.assertNotIn("SSL_CERT_FILE", env)

    def test_a_missing_bundle_changes_nothing(self):
        env = {}
        chosen = use_bundled_ca_bundle(Path(os.devnull) / "missing.pem", platform="win32", environ=env)
        self.assertIsNone(chosen)
        self.assertNotIn("SSL_CERT_FILE", env)


if __name__ == "__main__":
    unittest.main()
