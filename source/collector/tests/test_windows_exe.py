"""The self-contained Windows exe is the portable folder with a signed trailer."""

import hashlib
import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


def load_packager():
    path = Path(__file__).resolve().parents[3] / "scripts" / "package-windows.py"
    spec = importlib.util.spec_from_file_location("package_windows", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsExecutableTests(unittest.TestCase):
    def test_pack_appends_a_zip_and_its_digest(self):
        module = load_packager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "pkg"
            (package / "electron").mkdir(parents=True)
            (package / "electron" / "electron.exe").write_bytes(b"MZ")
            (package / "app").mkdir()
            (package / "app" / "main.js").write_text("ok", encoding="utf-8")
            cache = package / "collector" / "streamdonations" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "x.pyc").write_bytes(b"no")
            stub = root / "stub.exe"
            stub.write_bytes(b"STUB")
            dest = root / "SuperBerry.exe"
            module.pack_executable(stub, package, dest)
            data = dest.read_bytes()
            self.assertTrue(data.startswith(b"STUB"))
            length = int.from_bytes(data[-8:], "little")
            digest = data[-40:-8]
            payload = data[4:-40]
            self.assertEqual(len(payload), length)
            self.assertEqual(hashlib.sha256(payload).digest(), digest)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
            self.assertIn("electron/electron.exe", names)
            self.assertIn("app/main.js", names)
            self.assertFalse(any(name.endswith(".pyc") or "__pycache__" in name for name in names))

    def test_launcher_keeps_the_package_inside_its_folder(self):
        text = (
            Path(__file__).resolve().parents[3] / "scripts" / "superberry-win-launcher.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("LocalApplicationData", text)
        self.assertIn("Opening SuperBerry", text)
        self.assertIn('parts[i] == ".."', text)
        self.assertIn("SuperBerry's package contains an unsafe path.", text)
        self.assertIn("/target:winexe", (
            Path(__file__).resolve().parents[3] / "scripts" / "package-windows.py"
        ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
