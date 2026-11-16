# OGN APRS to readsb ASTERIX bridge

This bridge subscribes to `aprs.glidernet.org:14580` near the configured
OGN receiver station. It converts OGN aircraft position reports to ASTERIX CAT021 and
serves the stream on loopback TCP port 30991. The installed `readsb` connects
to that port, so tar1090 displays the aircraft through its normal readsb feed.

## Prerequisites
- running **rtlsdr-ogn** instance (e.g. based on https://github.com/VirusPilot/ogn-pi34)
- running **readsb** instance (e.g. based on: https://github.com/wiedehopf/readsb)

## Installation

Run on the receiver host:

```sh
sh /home/pi/ogn-asterix-bridge/install.sh
```

The installer renders [ogn-asterix-bridge.service](./ogn-asterix-bridge.service)
as a template. It reads APRS `Call` from the active OGN decoder configuration
referenced by `/etc/rtlsdr-ogn.conf`. If no decoder entry is found, it tries
`/home/pi/rtlsdr-ogn/Template.conf`. If OGN has no `Call`, it accepts readsb's
`CALLSIGN` or `RECEIVER_CALLSIGN` setting.

It reads latitude and longitude from readsb's `DECODER_OPTIONS` when both are
set. Otherwise, it uses the OGN `Position` section. Installation stops if it
cannot find a receiver callsign or a complete position. Rerun the installer after
changing these settings.

Check the bridge with:

```sh
sudo journalctl -u ogn-asterix-bridge -f
```

The bridge requires Python 3 and no third-party Python packages.

## Forwarded traffic

The bridge discards position reports with the `OGADSB` APRS tocall. It forwards
valid aircraft positions from the other [OGN APRS tocalls], including positions
whose source callsign begins `ICA`. It does not compare them with readsb's
directly received ADS-B traffic. Receiver status and weather messages without
an aircraft position and device ID cannot be encoded as CAT021 aircraft tracks.
The readsb connector reconnects automatically after either service restarts.

## Altitude display

OGN `/A=` is WGS84 geometric altitude. The bridge sends it only as ASTERIX
I021/140, and readsb publishes it as `alt_geom`. The installer patches
tar1090's two barometric altitude detail fields to read `alt_baro` instead of
its generic altitude fallback. This shows `n/a` for OGN aircraft without
pressure altitude and retains WGS84 altitude in the geometric field.

Rerun the patch if a tar1090 update replaces its JavaScript file:

```sh
sudo python3 /home/pi/ogn-asterix-bridge/patch_tar1090.py
```

## Source label and color

The bridge reads each address's device type from the [OGN Devices Database].
Only `I` establishes a valid ICAO address and is sent without `~`, allowing
readsb to match an aircraft received directly over ADS-B by hex code. `F`
(FLARM), `O` (OGN), and addresses absent from the database are sent as
non-ICAO; readsb shows these with a `~` prefix. This fallback avoids labelling
an unlisted OGN target as ADS-B, even when its APRS source or `id` field
suggests an ICAO address.

The tar1090 patch recognizes `~`-prefixed ASTERIX targets and displays `OGN`
in its Source fields, table, legend, and source filter. An `I` target without
`~` uses tar1090's normal ADS-B source label because readsb has no separate
OGN source marker in its aircraft JSON. Direct ADS-B aircraft keep their ADS-B
label.

When an `I` address is also received directly over ADS-B, readsb merges both
inputs under the same hex code. It assigns injected ASTERIX data lower priority
than direct ADS-B, so a recent ADS-B position normally wins even if an OGN
report arrives later. OGN can still supply a field that direct reception has
not supplied, such as geometric altitude. The ADS-B source label on the merged
tar1090 track does not mean that the OGN report was discarded.

This also applies to a Mode S-only transponder with the same valid ICAO hex
address. Readsb can combine its directly received barometric altitude and
squawk with OGN GPS position and WGS84 geometric altitude. If the OGN position
passes readsb's validity and range checks, tar1090 can plot the combined
track even though Mode S itself supplies no position. The OGN report must have
a tocall other than `OGADSB`, and the OGN database must mark its address `I`
so the bridge sends it without `~`.

Selecting `OGN` in the source filter shows `~`-prefixed OGN tracks whether or
not the optional AIS source is available. Those tracks use the former AIS
source color in the aircraft table, source legend, and source filter, including
the selected-row shade. Map marker colors still follow tar1090's altitude
colors.

## Registration

The bridge reads `Reg=` from OGN APRS status reports, including the local
decoder's `APRS <-` output on TCP port 50001. The APRS position feed does not
reliably include these status reports, so the local decoder connection is
needed for live updates. The current seed maps address `3FEC6F` to
registration `D-MYGX`.

Registrations are saved in
`/var/lib/ogn-asterix-bridge/registrations.json` and exported to readsb's
`aircraft.csv.gz` in the same directory. The installer configures readsb to
use this database. Readsb reloads it periodically, so a new registration can
take about 30 seconds to appear in tar1090. Saved registrations expire after
30 days without another status report.

On first startup, [registration-seeds.json](./registration-seeds.json) preloads
this mapping. Once the saved registration state exists, changes to the seed
file do not update that state. Future APRS status reports can update the
registration.

When no APRS `Reg=` value has been received, the bridge looks up the six-digit
device ID in the [OGN Devices Database]. It uses the database registration,
or its `CN` callsign when registration is empty. Only publicly identified
FLARM, ICAO, and OGN records are used for this fallback. APRS `Reg=` takes
precedence. The bridge downloads the database at startup and daily thereafter,
keeping a local `ddb.csv` copy for network outages.

## Aircraft type

For publicly identified devices, the bridge exports `AIRCRAFT_MODEL` as
readsb's long aircraft description (`desc`). The installer enables
`--db-file-lt` so readsb includes it in aircraft JSON. Tar1090 shows the model
in the Type column and in the selected aircraft's type details. The database
model is free text, so it is not written to readsb's four-character ICAO type
code field (`t`).

[OGN APRS tocalls]: https://github.com/glidernet/ogn-aprs-protocol/blob/master/tocalls.txt
[OGN Devices Database]: https://ddb.glidernet.org/download/
