#!/usr/bin/env python3
"""Correct tar1090 altitude and OGN source labels."""

import argparse
import re
from pathlib import Path

OLD = "format_altitude_long(adjust_baro_alt(selected.altitude), selected.vert_rate, DisplayUnits)"
NEW = "format_altitude_long(adjust_baro_alt(selected.alt_baro), selected.vert_rate, DisplayUnits)"
VERSION = "ogn-ui=8"


def replace_once(text, old, new):
    if text.count(new) == 1:
        return text
    if text.count(old) == 1:
        return text.replace(old, new)
    raise ValueError(f"tar1090 code differs from expected version: {old[:70]}")


def patch_text(text):
    if text.count(OLD) == 2:
        text = text.replace(OLD, NEW)
    elif text.count(NEW) != 2 or OLD in text:
        raise ValueError("tar1090 altitude display code differs from the expected version")
    type_value = '        value: function(plane) { return (plane.readsbDescription || plane.icaoType || plane.typeLong || ""); },\n'
    text = text.replace(
        '        value: function(plane) { return (plane.icaoType || plane.typeLong || ""); },\n',
        type_value,
    )
    text = replace_once(
        text,
        '        value: function(plane) { return (plane.icaoType != null ? plane.icaoType : ""); },\n'
        "        text: 'Type' };",
        type_value + "        text: 'Type' };")
    # Upgrade installations patched by earlier versions as well as fresh ones.
    text = text.replace("tableColors.unselected.ogn = tableColors.unselected.adsb;",
                        "tableColors.unselected.ogn = tableColors.unselected.ais;")
    text = text.replace("tableColors.selected.ogn = tableColors.selected.adsb;",
                        "tableColors.selected.ogn = tableColors.selected.ais;")
    text = replace_once(
        text,
        "let sources = ['adsb', ['uat', 'adsr'], 'mlat', 'tisb', 'modeS', 'other', 'adsc', 'ais'];",
        "let sources = ['adsb', ['uat', 'adsr'], 'mlat', 'tisb', 'modeS', 'other', 'adsc', 'ais', 'ogn'];\n"
        "tableColors.unselected.ogn = tableColors.unselected.ais;\n"
        "tableColors.selected.ogn = tableColors.selected.ais;",
    )
    text = replace_once(
        text,
        "    html += createFilter(colors['adsc'], jaeroLabel, sources[6]);",
        "    html += createFilter(colors['adsc'], jaeroLabel, sources[6]);\n"
        "    html += createFilter(colors['ogn'], 'OGN', sources[8]);",
    )
    text = replace_once(
        text,
        "    html += '<div class=\"legendTitle\" style=\"background-color:' + colors['adsc'] + `;\">${jaeroLabel}</div>`;",
        "    html += '<div class=\"legendTitle\" style=\"background-color:' + colors['adsc'] + `;\">${jaeroLabel}</div>`;\n"
        "    html += '<div class=\"legendTitle\" style=\"background-color:' + colors['ogn'] + ';\">OGN</div>';",
    )
    text = replace_once(
        text,
        '                const index = jQuery("#sourceFilter li").index(this);\n'
        '                if (Array.isArray(sources[index]))\n'
        '                    sources[index].forEach(member => { sourcesFilter.push(member); });\n'
        '                else\n'
        '                    sourcesFilter.push(sources[index]);',
        '                const key = this.id.substring("source-filter-".length);\n'
        '                if (key == "uat")\n'
        '                    sourcesFilter.push("uat", "adsr");\n'
        '                else\n'
        '                    sourcesFilter.push(key);',
    )
    return text


def patch_plane(text):
    text = replace_once(
        text,
        'PlaneObject.prototype.setTypeFlagsReg = function(data) {\n'
        '    if (data.t && data.t != this.icaoType) {',
        'PlaneObject.prototype.setTypeFlagsReg = function(data) {\n'
        '    if (data.desc) {\n'
        '        this.readsbDescription = `${data.desc}`;\n'
        '        this.typeLong = this.readsbDescription;\n'
        '        this.dbinfoLoaded = true;\n'
        '    }\n'
        '    if (data.t && data.t != this.icaoType) {',
    )
    text = replace_once(
        text,
        '    } else if ((lat != null && type == null) || (type && (type.substring(0,4) == "adsb"))) {\n'
        '        this.dataSource = "adsb";',
        '    } else if ((lat != null && type == null) || (type && (type.substring(0,4) == "adsb"))) {\n'
        '        this.dataSource = (this.icao.startsWith("~") && type == "adsb_icao") ? "ogn" : "adsb";',
    )
    text = replace_once(
        text,
        '    } else if (this.addrtype.substring(0,4) == "adsb") {\n'
        '        this.dataSource = "adsb";',
        '    } else if (this.addrtype.substring(0,4) == "adsb") {\n'
        '        this.dataSource = (this.icao.startsWith("~") && this.addrtype == "adsb_icao") ? "ogn" : "adsb";',
    )
    text = replace_once(
        text,
        '    if (this.dataSource == "adsb")\n        return 1;',
        '    if (this.dataSource == "adsb" || this.dataSource == "ogn")\n        return 1;',
    )
    return text


def patch_formatter(text):
    return replace_once(
        text,
        "\t\tcase 'adsb':\n",
        "\t\tcase 'ogn':\n\t\t\treturn \"OGN\";\n\t\tcase 'adsb':\n",
    )


def patch_index(text):
    pattern = r'(src="(?:script|planeObject|formatter)_[^"?]+\.js)(?:\?[^\"]*)?(\")'
    updated, count = re.subn(pattern, lambda m: f"{m[1]}?{VERSION}{m[2]}", text)
    if count != 3:
        raise ValueError("Could not find tar1090's main script, plane object and formatter")
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-dir", type=Path, default=Path("/usr/local/share/tar1090/html"))
    args = parser.parse_args()
    files = {}
    for name, patcher in (("script", patch_text), ("planeObject", patch_plane), ("formatter", patch_formatter)):
        matches = list(args.html_dir.glob(f"{name}_*.js"))
        if len(matches) != 1:
            raise SystemExit(f"Expected one {name} tar1090 file, found {len(matches)}")
        files[matches[0]] = patcher
    index = args.html_dir / "index.html"
    files[index] = patch_index
    updates = {path: patcher(path.read_text()) for path, patcher in files.items()}
    for path, content in updates.items():
        if content != path.read_text():
            path.write_text(content)
            print(f"Patched {path}")


if __name__ == "__main__":
    main()
