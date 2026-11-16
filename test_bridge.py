import datetime
import gzip
import tempfile
import unittest
from pathlib import Path

from bridge import RegistrationStore, cat21, parse_ddb, parse_ddb_models, parse_ddb_types, parse_packet, parse_registration
from patch_tar1090 import NEW, OLD, patch_formatter, patch_index, patch_plane, patch_text


def sample(source="FLR123ABC", ident="id0A123ABC", tocall="OGFLR", table="/"):
    clock = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S")
    return (f"{source}>{tocall},qAS,OGNTEST:/{clock}h4950.00N{table}01000.00E"
            f"'090/045/A=001234 !W12! {ident} +1fpm")


class BridgeTests(unittest.TestCase):
    def test_ddb_fallback_and_aprs_precedence(self):
        content = ("#DEVICE_TYPE,DEVICE_ID,AIRCRAFT_MODEL,REGISTRATION,CN,TRACKED,IDENTIFIED\n"
                   "'F','123ABC','Glider','D-ABCD','AB','Y','Y'\n"
                   "'O','234BCD','Glider','','CD','Y','Y'\n"
                   "'F','345CDE','Glider','D-HIDDEN','HI','Y','N'\n"
                   "'I','456DEF','Glider','D-ADSB','AD','Y','Y'\n"
                   "'F','567EF0','Glider','D-FIRST','F1','Y','Y'\n"
                   "'O','567EF0','Glider','D-SECOND','S2','Y','Y'\n"
                   "'F','678901','Piper; \"Cub\"','D-CUB','CU','Y','Y'\n").encode()
        self.assertEqual(parse_ddb(content), {"123ABC": "D-ABCD", "234BCD": "CD",
                                              "456DEF": "D-ADSB", "678901": "D-CUB"})
        types = parse_ddb_types(content)
        self.assertEqual(types, {"123ABC": "F", "234BCD": "O", "345CDE": "F",
                                 "456DEF": "I", "678901": "F"})
        models = parse_ddb_models(content, types)
        self.assertEqual(models, {"123ABC": "Glider", "234BCD": "Glider",
                                  "456DEF": "Glider", "678901": "Piper Cub"})
        with tempfile.TemporaryDirectory() as directory:
            store = RegistrationStore(Path(directory), None)
            self.assertTrue(store.update_ddb(content, parse_ddb(content), types, models))
            self.assertFalse(store.update_ddb(content, parse_ddb(content), types, models))
            self.assertTrue(store.update("123ABC", "D-APRS"))
            payload = gzip.decompress(store.db_path.read_bytes()).decode()
            self.assertIn("1123ABC;D-APRS;;00000000;Glider;;;", payload)
            self.assertIn("1234BCD;CD;;00000000;Glider;;;", payload)
            self.assertIn("456DEF;D-ADSB;;00000000;Glider;;;", payload)
            self.assertIn("1678901;D-CUB;;00000000;Piper Cub;;;", payload)
            self.assertNotIn("1345CDE;", payload)
            self.assertNotIn("1567EF0;", payload)
            reloaded = RegistrationStore(Path(directory), None)
            self.assertEqual(reloaded.ddb_entries, parse_ddb(content))
            self.assertEqual(reloaded.ddb_types, types)
            self.assertEqual(reloaded.ddb_models, models)
            self.assertIn("1123ABC;D-APRS;;00000000;Glider;;;",
                          gzip.decompress(reloaded.db_path.read_bytes()).decode())

    def test_only_ddb_icao_type_uses_icao_address(self):
        packet = parse_packet(sample())
        self.assertEqual(cat21(packet)[9], 0x60)
        self.assertEqual(cat21(packet, "I")[9], 0x00)
        self.assertEqual(cat21(packet, "F")[9], 0x60)
        self.assertEqual(cat21(packet, "O")[9], 0x60)
        unknown = parse_packet(sample(source="ICABDA1E8", ident="id0DBDA1E8"))
        self.assertEqual(unknown["addr"], 0xBDA1E8)
        self.assertEqual(cat21(unknown)[9], 0x60)

    def test_registration_status_and_readsb_database(self):
        now = datetime.datetime(2026, 11, 14, 11, 7, 13, tzinfo=datetime.timezone.utc).timestamp()
        line = "APRS <- FLR8C08E8>OGFLR7,qOR:>110713h Reg=D-MXXY"
        self.assertEqual(parse_registration(line, now), ("8C08E8", "D-MXXY"))
        self.assertIsNone(parse_registration(line, now + 3600))
        self.assertIsNone(parse_registration(line.replace("FLR", "ICA", 1), now))
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / "seed.json"
            seed.write_text('{"8C08E8":"D-MXXY"}')
            store = RegistrationStore(Path(directory) / "state", seed)
            payload = gzip.decompress(store.db_path.read_bytes()).decode()
            self.assertGreater(len(payload), 1000)
            self.assertIn("18C08E8;D-MXXY;;00000000;;;;", payload)
            self.assertFalse(store.update("8C08E8", "D-MXXY"))
            self.assertTrue(store.update("8C08E8", "D-MXYZ"))
            self.assertIn("18C08E8;D-MXYZ", gzip.decompress(store.db_path.read_bytes()).decode())

    def test_baro_display_uses_baro_field_only(self):
        html = Path('/usr/local/share/tar1090/html')
        script = next(html.glob('script_*.js')).read_text()
        updated = patch_text(script)
        self.assertEqual(updated.count(NEW), 2)
        self.assertEqual(patch_text(updated), updated)
        self.assertIn('plane.readsbDescription || plane.icaoType || plane.typeLong || ""', updated)
        self.assertIn("createFilter(colors['ogn'], 'OGN'", updated)
        self.assertIn("tableColors.unselected.ogn = tableColors.unselected.ais;", updated)
        self.assertIn("tableColors.selected.ogn = tableColors.selected.ais;", updated)
        self.assertNotIn("tableColors.unselected.ogn = tableColors.unselected.adsb;", updated)
        self.assertIn('const key = this.id.substring("source-filter-".length);', updated)
        self.assertNotIn('jQuery("#sourceFilter li").index(this)', updated)
        index = ("<script src=\"formatter_abc123.js\"></script>"
                 "<script src=\"planeObject_abc123.js\"></script>"
                 "<script src=\"script_abc123.js\"></script>")
        index_updated = patch_index(index)
        self.assertIn('script_abc123.js?ogn-ui=8', index_updated)
        self.assertEqual(patch_index(index_updated), index_updated)
        plane = next(html.glob('planeObject_*.js')).read_text()
        plane_updated = patch_plane(plane)
        self.assertEqual(plane_updated.count('this.icao.startsWith("~")'), 2)
        self.assertIn('this.readsbDescription = `${data.desc}`;', plane_updated)
        self.assertEqual(patch_plane(plane_updated), plane_updated)
        formatter = next(html.glob('formatter_*.js')).read_text()
        formatter_updated = patch_formatter(formatter)
        self.assertIn('case \'ogn\':\n\t\t\treturn "OGN";', formatter_updated)
        self.assertEqual(patch_formatter(formatter_updated), formatter_updated)

    def test_ogn_report_and_cat21(self):
        packet = parse_packet(sample())
        self.assertEqual(packet["addr"], 0x123ABC)
        self.assertAlmostEqual(packet["lat"], 49.833333, places=5)
        frame = cat21(packet)
        self.assertEqual(frame[0], 21)
        self.assertEqual(int.from_bytes(frame[1:3], "big"), len(frame))
        self.assertEqual(frame[3:7], bytes.fromhex("c5114108"))
        self.assertEqual(frame[7:10], bytes((0, 1, 0x60)))
        self.assertEqual(frame[16:19], bytes.fromhex("123abc"))

    def test_only_ogadsb_positions_are_excluded(self):
        self.assertIsNone(parse_packet(sample(source="ICA123ABC", tocall="OGADSB")))
        self.assertIsNone(parse_packet(sample(source="FLR123ABC", tocall="OGADSB")))
        for tocall in ("OGFLR", "OGADSL", "OGNSKY", "OGNAVI", "OGNTRK", "OGNFNT"):
            self.assertEqual(parse_packet(sample(source="ICA123ABC", tocall=tocall))["addr"], 0x123ABC)
        self.assertEqual(parse_packet(sample(source="SPO123ABC", ident="", tocall="OGSPOT"))["addr"], 0x123ABC)
        self.assertEqual(parse_packet(sample(source="FLR123ABC", table="\\"))["addr"], 0x123ABC)
        self.assertIsNone(parse_packet(sample(source="STATION", ident="", tocall="OGNSDR")))
        self.assertIsNone(parse_packet(sample().replace("/A=001234", "/A=001234"), now=0))


if __name__ == "__main__":
    unittest.main()
