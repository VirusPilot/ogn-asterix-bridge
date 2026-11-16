import tempfile
import unittest
from pathlib import Path

from render_service import active_ogn_config, ogn_values, readsb_values, render


TEMPLATE = "ExecStart=bridge --callsign @CALLSIGN@ --lat @LAT@ --lon @LON@\n"


class RenderServiceTests(unittest.TestCase):
    def test_active_ogn_config_and_readsb_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shellbox.conf").write_text(
                f"50000 pi {root} ./ogn-rf Receiver.conf\n"
                f"50001 pi {root} ./ogn-decode Receiver.conf\n")
            (root / "Receiver.conf").write_text(
                "Position: { Latitude = +49.5; Longitude = +9.5; };\n"
                "APRS: { Call = \"OGNREAL\"; };\n")
            (root / "readsb").write_text(
                'DECODER_OPTIONS="--lat 50.125 --lon=10.25 --max-range 450"\n')
            ogn = active_ogn_config(root / "shellbox.conf")
            self.assertEqual(ogn, root / "Receiver.conf")
            call, ogn_position = ogn_values(ogn)
            self.assertEqual(call, "OGNREAL")
            self.assertEqual(ogn_position, ("+49.5", "+9.5"))
            _, readsb_position = readsb_values(root / "readsb")
            self.assertIn("--callsign OGNREAL --lat 50.125 --lon 10.25",
                          render(TEMPLATE, call, readsb_position))

    def test_ogn_position_and_readsb_callsign_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ogn.conf").write_text(
                "Position:\n{\n  Latitude = -33.5; # station latitude\n"
                "  Longitude = +151.25;\n};\n"
                "APRS:\n{\n  #Call = \"OLD\";\n  Call = \"OGNNEW\";\n};\n")
            (root / "readsb").write_text(
                'CALLSIGN="READSB1"\nDECODER_OPTIONS="--lat -33.5"\n')
            ogn_call, ogn_position = ogn_values(root / "ogn.conf")
            readsb_call, readsb_position = readsb_values(root / "readsb")
            self.assertIsNone(readsb_position)
            self.assertIn("--callsign OGNNEW --lat -33.5 --lon 151.25",
                          render(TEMPLATE, ogn_call or readsb_call,
                                 readsb_position or ogn_position))
            self.assertIn("--callsign READSB1", render(TEMPLATE, readsb_call, ogn_position))

    def test_invalid_or_missing_values_fail(self):
        with self.assertRaisesRegex(ValueError, "callsign"):
            render(TEMPLATE, "BAD CALL", ("49", "9"))
        with self.assertRaisesRegex(ValueError, "latitude/longitude"):
            render(TEMPLATE, "OGNTEST", None)
        with self.assertRaisesRegex(ValueError, "range"):
            render(TEMPLATE, "OGNTEST", ("91", "9"))


if __name__ == "__main__":
    unittest.main()
