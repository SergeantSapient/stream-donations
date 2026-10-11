"""The kiosk battery line reads the computer, not a mouse or a keyboard."""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from streamdonations.battery import host_battery
from streamdonations.server import App, Handler, ThreadingServer
from streamdonations.store import Store


def _supply(root: Path, name: str, **fields: str) -> None:
    folder = root / name
    folder.mkdir()
    for key, value in fields.items():
        (folder / key).write_text(value + "\n", encoding="utf-8")


class BatteryTests(unittest.TestCase):
    def test_a_missing_supply_directory_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(host_battery(Path(tmp) / "absent"))

    def test_a_device_battery_is_not_the_computer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(
                root,
                "hidpp_battery_11",
                type="Battery",
                scope="Device",
                status="Discharging",
                capacity_level="Full",
                model_name="M720 Triathlon",
            )
            self.assertIsNone(host_battery(root))

    def test_capacity_level_alone_does_not_invent_a_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity_level="Full")
            self.assertIsNone(host_battery(root))

    def test_a_discharging_system_battery_reports_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="42")
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 42})

    def test_an_empty_scope_still_counts_as_the_computer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="61", scope="")
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 61})

    def test_wall_power_hides_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="42")
            _supply(root, "ADP1", type="Mains", online="1")
            self.assertIsNone(host_battery(root))

    def test_an_offline_adapter_leaves_the_battery_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="42")
            _supply(root, "ADP1", type="Mains", online="0")
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 42})

    def test_usb_power_hides_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="42")
            _supply(root, "usb", type="USB", online="1")
            self.assertIsNone(host_battery(root))

    def test_charging_full_and_not_charging_stay_hidden(self):
        for status in ("Charging", "Full", "Not charging", "Unknown"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _supply(root, "BAT0", type="Battery", status=status, capacity="80")
                    self.assertIsNone(host_battery(root))

    def test_energy_is_used_when_capacity_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(
                root,
                "BAT0",
                type="Battery",
                status="Discharging",
                energy_now="250000",
                energy_full="1000000",
            )
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 25})

    def test_charge_is_used_when_capacity_and_energy_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(
                root,
                "BAT0",
                type="Battery",
                status="Discharging",
                charge_now="500000",
                charge_full="2000000",
            )
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 25})

    def test_capacity_wins_over_energy_on_one_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(
                root,
                "BAT0",
                type="Battery",
                status="Discharging",
                capacity="42",
                energy_now="10",
                energy_full="100",
            )
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 42})

    def test_percent_is_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="140")
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 100})

    def test_a_mouse_does_not_hide_or_replace_the_computer_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", scope="System", status="Discharging", capacity="55")
            _supply(
                root,
                "hidpp_battery_11",
                type="Battery",
                scope="Device",
                status="Discharging",
                capacity="10",
            )
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 55})

    def test_two_batteries_are_weighted_by_energy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(
                root,
                "BAT0",
                type="Battery",
                status="Discharging",
                capacity="10",
                energy_now="50",
                energy_full="100",
            )
            _supply(
                root,
                "BAT1",
                type="Battery",
                status="Discharging",
                capacity="90",
                energy_now="100",
                energy_full="100",
            )
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 75})

    def test_two_batteries_without_energy_use_the_average(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="40")
            _supply(root, "BAT1", type="Battery", status="Discharging", capacity="80")
            self.assertEqual(host_battery(root), {"on_battery": True, "percent": 60})

    def test_one_battery_that_is_not_discharging_hides_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _supply(root, "BAT0", type="Battery", status="Discharging", capacity="40")
            _supply(root, "BAT1", type="Battery", status="Full", capacity="100")
            self.assertIsNone(host_battery(root))

    def test_status_includes_a_battery_object_or_nothing(self):
        tmp = tempfile.TemporaryDirectory()
        store = Store(Path(tmp.name) / "donations.db")
        token = "b" * 32
        app = App(store, token, 1)
        server = ThreadingServer(("127.0.0.1", 0), Handler)
        server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/status",
                headers={"Cookie": f"sd_session={token}"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            battery = payload["battery"]
            if battery is None:
                return
            self.assertIs(battery["on_battery"], True)
            self.assertGreaterEqual(battery["percent"], 0)
            self.assertLessEqual(battery["percent"], 100)
            self.assertIsInstance(battery["percent"], int)
        finally:
            server.shutdown()
            server.server_close()
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
