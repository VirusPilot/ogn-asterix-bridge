#!/usr/bin/env python3
"""Forward OGN APRS aircraft positions to readsb as ASTERIX CAT021."""

import argparse
import asyncio
import csv
import gzip
import io
import json
import logging
import math
import os
import re
import struct
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

LOG = logging.getLogger("ogn-asterix")
POSITION = re.compile(
    r"^(?P<source>[A-Z0-9-]+)>(?P<tocall>[A-Z0-9]+),[^:]+:/"
    r"(?P<hh>\d{2})(?P<mm>\d{2})(?P<ss>\d{2})h"
    r"(?P<latdeg>\d{2})(?P<latmin>\d{2}\.\d{2})(?P<ns>[NS])"
    r"[/\\A-Z0-9](?P<londeg>\d{3})(?P<lonmin>\d{2}\.\d{2})(?P<ew>[EW])"
    r"(?P<symbol>.)"
    r"(?:(?P<track>\d{3})/(?P<speed>\d{3}))?"
)
ALTITUDE = re.compile(r"/A=(\d{6})\b")
OGN_ID = re.compile(r"\bid([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\b")
HEX_SOURCE = re.compile(r"^[A-Z]{2,6}([0-9A-F]{6})$", re.I)
REGISTRATION = re.compile(
    r"^(?P<source>(?:FLR|OGN|FNT)[0-9A-F]{6})>[^:]+:>"
    r"(?P<hh>\d{2})(?P<mm>\d{2})(?P<ss>\d{2})h\s+"
    r"Reg=(?P<registration>[A-Z0-9][A-Z0-9-]{1,11})\b", re.I
)
REGISTRATION_LIFETIME = 30 * 86400
DDB_URL = "https://ddb.glidernet.org/download/"
DDB_REFRESH = 86400
DDB_MAX_BYTES = 8 * 1024 * 1024


def parse_packet(line, now=None):
    """Return a non-OGADSB position, or None for an unusable/old report."""
    now = time.time() if now is None else now
    match = POSITION.search(line.strip())
    if not match:
        return None
    source = match["source"].upper()
    # The APRS tocall identifies ADS-B; an ICA source name also occurs on
    # SafeSky, ADS-L, and other non-ADS-B traffic.
    if match["tocall"].upper() == "OGADSB":
        return None
    ident = OGN_ID.search(line)
    source_hex = HEX_SOURCE.match(source)
    if ident:
        addr = int(ident[2], 16)
    elif source_hex:
        addr = int(source_hex[1], 16)
    else:
        return None
    if not addr:
        return None
    seconds = int(match["hh"]) * 3600 + int(match["mm"]) * 60 + int(match["ss"])
    if seconds >= 86400:
        return None
    midnight = math.floor(now / 86400) * 86400
    age = min(abs(now - (midnight + seconds + offset)) for offset in (-86400, 0, 86400))
    if age > 120:
        return None
    lat = int(match["latdeg"]) + float(match["latmin"]) / 60
    lon = int(match["londeg"]) + float(match["lonmin"]) / 60
    lat *= -1 if match["ns"] == "S" else 1
    lon *= -1 if match["ew"] == "W" else 1
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    alt_match = ALTITUDE.search(line)
    return {
        "addr": addr, "lat": lat, "lon": lon,
        "alt_ft": int(alt_match[1]) if alt_match else None,
        "track": int(match["track"]) if match["track"] else None,
        "speed": int(match["speed"]) if match["speed"] else None,
        "source": source,
    }


def parse_registration(line, now=None):
    """Parse an OGN status packet such as APRS <- FLR8C08E8>...:>110713h Reg=D-MXXY."""
    line = line.strip()
    if "APRS <- " in line:
        line = line.split("APRS <- ", 1)[1]
    match = REGISTRATION.match(line)
    if not match:
        return None
    now = time.time() if now is None else now
    seconds = int(match["hh"]) * 3600 + int(match["mm"]) * 60 + int(match["ss"])
    if seconds >= 86400:
        return None
    midnight = math.floor(now / 86400) * 86400
    if min(abs(now - (midnight + seconds + offset)) for offset in (-86400, 0, 86400)) > 120:
        return None
    return match["source"][-6:].upper(), match["registration"].upper()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
            os.chmod(temp.name, 0o644)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def parse_ddb(content):
    """Read public device registrations; registration takes priority over CN."""
    rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), quotechar="'")
    result = {}
    ambiguous = set()
    for row in rows:
        if len(row) < 7 or row[0] not in ("F", "I", "O") or row[6] != "Y":
            continue
        address = row[1].upper()
        if not re.fullmatch(r"[0-9A-F]{6}", address):
            continue
        registration = next((value.strip().upper() for value in (row[3], row[4])
                             if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", value.strip(), re.I)), None)
        if not registration:
            continue
        # readsb's non-ICAO address does not encode F versus O. Avoid guessing.
        if address in result and result[address] != registration:
            ambiguous.add(address)
        else:
            result[address] = registration
    for address in ambiguous:
        result.pop(address, None)
    return result


def parse_ddb_types(content):
    """Read address types from all DDB devices, including unidentified ones."""
    rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), quotechar="'")
    result = {}
    ambiguous = set()
    for row in rows:
        if len(row) < 7 or row[0] not in ("F", "I", "O"):
            continue
        address = row[1].upper()
        if not re.fullmatch(r"[0-9A-F]{6}", address):
            continue
        if address in result and result[address] != row[0]:
            ambiguous.add(address)
        else:
            result[address] = row[0]
    for address in ambiguous:
        result.pop(address, None)
    return result


def parse_ddb_models(content, types):
    """Read public aircraft models for unambiguous DDB device addresses."""
    rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), quotechar="'")
    result = {}
    ambiguous = set()
    for row in rows:
        if len(row) < 7 or row[6] != "Y":
            continue
        address = row[1].upper()
        if types.get(address) != row[0]:
            continue
        # readsb exports this text as JSON and uses semicolon-delimited CSV.
        plain = unicodedata.normalize("NFKD", row[2]).encode("ascii", "ignore").decode("ascii")
        model = " ".join("".join(char if 32 <= ord(char) < 127 and char not in ';"\\'
                                 else " " for char in plain).split())[:54]
        if not model:
            continue
        if address in result and result[address] != model:
            ambiguous.add(address)
        else:
            result[address] = model
    for address in ambiguous:
        result.pop(address, None)
    return result


def download_ddb(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ogn-asterix-bridge/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(DDB_MAX_BYTES + 1)
    if len(content) > DDB_MAX_BYTES:
        raise ValueError("OGN device database exceeds size limit")
    entries = parse_ddb(content)
    types = parse_ddb_types(content)
    models = parse_ddb_models(content, types)
    if len(entries) < 1000:
        raise ValueError("OGN device database is unexpectedly small")
    return content, entries, types, models


class RegistrationStore:
    def __init__(self, directory, seed_path=None):
        self.directory = Path(directory)
        self.state_path = self.directory / "registrations.json"
        self.db_path = self.directory / "aircraft.csv.gz"
        self.ddb_path = self.directory / "ddb.csv"
        self.ddb_entries = {}
        self.ddb_types = {}
        self.ddb_models = {}
        self.entries = {}
        try:
            saved = json.loads(self.state_path.read_text())
            self.entries = {
                addr: entry for addr, entry in saved.items()
                if re.fullmatch(r"[0-9A-F]{6}", addr)
                and isinstance(entry, dict)
                and re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", entry.get("registration", ""))
                and isinstance(entry.get("updated"), (int, float))
                and time.time() - entry["updated"] < REGISTRATION_LIFETIME
            }
        except (OSError, ValueError, TypeError):
            pass
        if self.ddb_path.exists():
            try:
                cached_ddb = self.ddb_path.read_bytes()
                self.ddb_entries = parse_ddb(cached_ddb)
                self.ddb_types = parse_ddb_types(cached_ddb)
                self.ddb_models = parse_ddb_models(cached_ddb, self.ddb_types)
            except (OSError, UnicodeError, csv.Error) as exc:
                LOG.warning("Cannot load cached OGN device database: %s", exc)
        if seed_path and not self.state_path.exists():
            try:
                seeds = json.loads(Path(seed_path).read_text())
                for address, registration in seeds.items():
                    if (re.fullmatch(r"[0-9A-F]{6}", address)
                            and re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", registration)
                            and address not in self.entries):
                        self.entries[address] = {"registration": registration, "updated": time.time()}
            except (OSError, ValueError, TypeError) as exc:
                LOG.warning("Cannot read registration seeds: %s", exc)
        atomic_write(self.state_path, (json.dumps(self.entries, sort_keys=True) + "\n").encode())
        self.write_db()

    def write_db(self):
        # readsb requires >1000 uncompressed bytes and at least one database row.
        # 0x2000000 is outside readsb's ICAO and non-ICAO address ranges.
        lines = ["#" + " " * 1024, "2000000;;;;;;;"]
        combined = dict(self.ddb_entries)
        combined.update({address: entry["registration"] for address, entry in self.entries.items()})
        for address in sorted(combined.keys() | self.ddb_models.keys()):
            db_address = address if self.ddb_types.get(address) == "I" else f"1{address}"
            registration = combined.get(address, "")
            model = self.ddb_models.get(address, "")
            lines.append(f"{db_address};{registration};;00000000;{model};;;")
        payload = ("\n".join(lines) + "\n").encode("ascii")
        old_mtime = int(self.db_path.stat().st_mtime) if self.db_path.exists() else 0
        atomic_write(self.db_path, gzip.compress(payload, mtime=0))
        # readsb checks whole-second mtime every 30 seconds.
        timestamp = max(int(time.time()), old_mtime + 1)
        os.utime(self.db_path, (timestamp, timestamp))

    def update(self, address, registration):
        unchanged = self.entries.get(address, {}).get("registration") == registration
        self.entries[address] = {"registration": registration, "updated": time.time()}
        atomic_write(self.state_path, (json.dumps(self.entries, sort_keys=True) + "\n").encode())
        if unchanged:
            return False
        self.write_db()
        LOG.info("Registration %s: %s", address, registration)
        return True

    def update_ddb(self, content, entries, types, models):
        if entries == self.ddb_entries and types == self.ddb_types and models == self.ddb_models:
            return False
        atomic_write(self.ddb_path, content)
        self.ddb_entries = entries
        self.ddb_types = types
        self.ddb_models = models
        self.write_db()
        LOG.info("Loaded %d OGN device address types, %d registrations, %d models",
                 len(types), len(entries), len(models))
        return True


def cat21(packet, address_type=None):
    """Encode the CAT021 subset readsb's ASTERIX decoder accepts."""
    fspec = [0xC4, 0x10, 0x00, 0x00]  # DSI, descriptor, position, address
    # Only a DDB I row establishes that this is a valid ICAO address.
    # Unlisted devices stay non-ICAO so they cannot masquerade as ADS-B.
    descriptor = 0x00 if address_type == "I" else 0x60
    data = bytearray((0, 1, descriptor))  # SAC/SIC, ASTERIX address type
    for coordinate in (packet["lat"], packet["lon"]):
        value = round(coordinate * (1 << 23) / 180)
        data.extend((value & 0xFFFFFF).to_bytes(3, "big"))
    data.extend(packet["addr"].to_bytes(3, "big"))
    if packet["alt_ft"] is not None:
        fspec[2] |= 0x40  # I021/140 geometric height, 6.25 ft units
        units = round(packet["alt_ft"] / 6.25)
        data.extend(struct.pack(">h", max(-32768, min(32767, units))))
    if packet["track"] is not None and packet["speed"] is not None:
        fspec[3] |= 0x08  # I021/160 ground vector
        speed = round(packet["speed"] * (1 << 14) / 3600)
        heading = round(packet["track"] % 360 * (1 << 16) / 360)
        data.extend(struct.pack(">HH", min(speed, 0x7FFF), heading))
    while len(fspec) > 1 and fspec[-1] == 0:
        fspec.pop()
    for index in range(len(fspec) - 1):
        fspec[index] |= 1
    length = 3 + len(fspec) + len(data)
    return struct.pack(">BH", 21, length) + bytes(fspec) + data


class Bridge:
    def __init__(self, args):
        self.args = args
        self.clients = set()
        self.registrations = RegistrationStore(args.registration_dir, args.registration_seeds)

    def handle_registration(self, line):
        result = parse_registration(line)
        if result:
            self.registrations.update(*result)

    async def client(self, reader, writer):
        self.clients.add(writer)
        try:
            await reader.read()  # keep socket until readsb disconnects
        finally:
            self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def aprs(self):
        while True:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(self.args.aprs_host, self.args.aprs_port)
                login = (f"user {self.args.callsign} pass -1 vers ogn-asterix 1.0 "
                         f"filter r/{self.args.lat}/{self.args.lon}/{self.args.radius_km}\r\n")
                writer.write(login.encode("ascii"))
                await writer.drain()
                LOG.info("Connected to OGN APRS")
                while line := await reader.readline():
                    if len(line) > 4096:
                        continue
                    message = line.decode("ascii", "replace")
                    self.handle_registration(message)
                    report = parse_packet(message)
                    if not report:
                        continue
                    ddb_type = self.registrations.ddb_types.get(f"{report['addr']:06X}")
                    frame = cat21(report, ddb_type)
                    for client in tuple(self.clients):
                        try:
                            client.write(frame)
                            await client.drain()
                        except (OSError, ConnectionError):
                            self.clients.discard(client)
                            client.close()
            except (OSError, ValueError) as exc:
                LOG.warning("APRS connection: %s", exc)
            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            await asyncio.sleep(10)

    async def monitor_decoder(self):
        """Read the local decoder's APRS status output; the range feed omits it."""
        while True:
            writer = None
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.args.decoder_port)
                LOG.info("Connected to local OGN decoder monitor")
                while line := await reader.readline():
                    if len(line) <= 4096:
                        self.handle_registration(line.decode("ascii", "replace"))
            except (OSError, ValueError) as exc:
                LOG.warning("OGN decoder monitor: %s", exc)
            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            await asyncio.sleep(10)

    async def refresh_ddb(self):
        while True:
            try:
                content, entries, types, models = await asyncio.to_thread(download_ddb, self.args.ddb_url)
                self.registrations.update_ddb(content, entries, types, models)
                await asyncio.sleep(DDB_REFRESH)
            except (OSError, ValueError, UnicodeError, csv.Error) as exc:
                LOG.warning("OGN device database download: %s", exc)
                await asyncio.sleep(3600)

    async def run(self):
        server = await asyncio.start_server(self.client, "127.0.0.1", self.args.asterix_port)
        async with server:
            LOG.info("ASTERIX listening on 127.0.0.1:%d", self.args.asterix_port)
            await asyncio.gather(self.aprs(), self.monitor_decoder(), self.refresh_ddb())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--callsign", required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-km", type=int, default=50)
    parser.add_argument("--aprs-host", default="aprs.glidernet.org")
    parser.add_argument("--aprs-port", type=int, default=14580)
    parser.add_argument("--asterix-port", type=int, default=30991)
    parser.add_argument("--registration-dir", default="/var/lib/ogn-asterix-bridge")
    parser.add_argument("--registration-seeds", default="/home/pi/ogn-asterix-bridge/registration-seeds.json")
    parser.add_argument("--decoder-port", type=int, default=50001)
    parser.add_argument("--ddb-url", default=DDB_URL)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Bridge(args).run())


if __name__ == "__main__":
    main()
