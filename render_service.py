#!/usr/bin/env python3
"""Render the bridge's systemd unit from the receiver's active configuration."""

import argparse
import math
import re
import shlex
import sys
from pathlib import Path


def active_ogn_config(shellbox_path):
    """Find the config argument of the decoder launched by rtlsdr-ogn."""
    if shellbox_path.is_file():
        for line in shellbox_path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = shlex.split(line)
            if len(fields) >= 5 and Path(fields[3]).name == "ogn-decode":
                return Path(fields[2]) / fields[4]
    return Path("/home/pi/rtlsdr-ogn/Template.conf")


def ogn_values(path):
    if not path.is_file():
        return None, None
    config = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())

    def section_value(section, key):
        block = re.search(rf"(?s)\b{section}\s*:\s*\{{(.*?)\}};", config)
        if not block:
            return None
        value = re.search(rf"\b{key}\s*=\s*(?:\"([^\"]+)\"|([^;\s]+))\s*;", block[1])
        return (value[1] or value[2]) if value else None

    callsign = section_value("APRS", "Call")
    lat = section_value("Position", "Latitude")
    lon = section_value("Position", "Longitude")
    return callsign, (lat, lon) if lat is not None and lon is not None else None


def readsb_values(path):
    callsign = None
    coordinates = None
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in ("CALLSIGN", "RECEIVER_CALLSIGN"):
            parsed = shlex.split(value)
            if parsed:
                callsign = parsed[0]
        if key != "DECODER_OPTIONS":
            continue
        parsed = shlex.split(value)
        options = shlex.split(parsed[0]) if len(parsed) == 1 else parsed
        found = {}
        for index, token in enumerate(options):
            for name in ("lat", "lon"):
                if token == f"--{name}" and index + 1 < len(options):
                    found[name] = options[index + 1]
                elif token.startswith(f"--{name}="):
                    found[name] = token.split("=", 1)[1]
        if "lat" in found and "lon" in found:
            coordinates = (found["lat"], found["lon"])
    return callsign, coordinates


def render(template, callsign, coordinates):
    if not callsign or not re.fullmatch(r"[A-Za-z0-9-]{1,9}", callsign):
        raise ValueError("no valid callsign in OGN APRS.Call or readsb CALLSIGN")
    if coordinates is None:
        raise ValueError("no complete latitude/longitude pair in readsb or OGN configuration")
    try:
        lat, lon = (float(value) for value in coordinates)
    except ValueError as exc:
        raise ValueError("invalid latitude/longitude in receiver configuration") from exc
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude/longitude outside valid range")
    replacements = {"@CALLSIGN@": callsign, "@LAT@": str(lat), "@LON@": str(lon)}
    for marker, value in replacements.items():
        if template.count(marker) != 1:
            raise ValueError(f"service template must contain {marker} exactly once")
        template = template.replace(marker, value)
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--readsb-config", type=Path, required=True)
    parser.add_argument("--shellbox-config", type=Path, required=True)
    parser.add_argument("--ogn-config", type=Path, help="override OGN decoder config discovery")
    args = parser.parse_args()
    try:
        ogn_path = args.ogn_config or active_ogn_config(args.shellbox_config)
        ogn_call, ogn_position = ogn_values(ogn_path)
        readsb_call, readsb_position = readsb_values(args.readsb_config)
        print(render(args.template.read_text(), ogn_call or readsb_call,
                     readsb_position or ogn_position), end="")
    except (OSError, ValueError) as exc:
        print(f"Cannot render OGN bridge service: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
