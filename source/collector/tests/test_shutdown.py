"""The desktop shell stops the collector by closing stdin. Windows has no SIGTERM for a child."""

import io
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from streamdonations.__main__ import _stop_when_stdin_closes

ROOT = Path(__file__).resolve().parents[1]


class _Server:
    def __init__(self):
        self.stopped = threading.Event()

    def shutdown(self):
        self.stopped.set()


class StdinStopTests(unittest.TestCase):
    def test_end_of_file_stops_the_server(self):
        server = _Server()
        _stop_when_stdin_closes(server, io.StringIO("noise\n"))
        self.assertTrue(server.stopped.is_set())

    def test_a_stop_line_stops_the_server(self):
        server = _Server()
        stream = io.StringIO("stop\nnever read\n")
        _stop_when_stdin_closes(server, stream)
        self.assertTrue(server.stopped.is_set())
        self.assertEqual(stream.readline(), "never read\n")

    def test_closing_stdin_lets_serve_clean_up(self):
        home = Path(os.path.expanduser("~"))
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(ROOT),
            "STREAM_DONATIONS_TOKEN": "shutdowntesttoken0123",
            "STREAM_DONATIONS_STOP_ON_STDIN": "1",
            "STREAM_DONATIONS_NOTIFY": "shell",
        })
        import shutil
        import tempfile

        directory = Path(tempfile.mkdtemp(prefix="sd-shutdown-", dir=str(home)))
        try:
            child = subprocess.Popen(
                [sys.executable, "-m", "streamdonations", "serve", "--port", "0", "--data-dir", str(directory)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            try:
                ready = child.stdout.readline()
                if not ready.startswith("STREAM_DONATIONS_READY port="):
                    child.kill()
                    self.fail(ready + child.stderr.read())
                child.stdin.write("stop\n")
                child.stdin.close()
                code = child.wait(timeout=20)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()
                child.stderr.close()
            self.assertEqual(code, 0)
            # Store.close ran: the WAL is checkpointed away, or at least empty.
            wal = directory / "donations.db-wal"
            self.assertTrue(not wal.exists() or wal.stat().st_size == 0)
            self.assertTrue((directory / "donations.db").is_file())
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
