#!/bin/sh
set -eu

# Run on the receiver host as pi. The existing readsb ports remain unchanged.
BRIDGE_DIR=/home/pi/ogn-asterix-bridge
READSB_CONFIG=/etc/default/readsb
SERVICE=/etc/systemd/system/ogn-asterix-bridge.service
RENDERED_SERVICE=$(mktemp)
trap 'rm -f "$RENDERED_SERVICE"' EXIT HUP INT TERM

if [ ! -f "$READSB_CONFIG" ]; then
    echo "readsb configuration missing: $READSB_CONFIG" >&2
    exit 1
fi

python3 "$BRIDGE_DIR/render_service.py" \
    --template "$BRIDGE_DIR/ogn-asterix-bridge.service" \
    --readsb-config "$READSB_CONFIG" \
    --shellbox-config /etc/rtlsdr-ogn.conf > "$RENDERED_SERVICE"
sudo install -m 0644 "$RENDERED_SERVICE" "$SERVICE"
sudo python3 "$BRIDGE_DIR/patch_tar1090.py"
if ! grep -q '127.0.0.1,30991,asterix_in' "$READSB_CONFIG"; then
    sudo sed -i '/^NET_OPTIONS=/ s/"$/ --net-connector=127.0.0.1,30991,asterix_in"/' "$READSB_CONFIG"
fi
# Make the merged aircraft database readsb's only --db-file source.
sudo sed -i '/^DECODER_OPTIONS=/ s# --db-file=[^ " ]*##g' "$READSB_CONFIG"
sudo sed -i '/^DECODER_OPTIONS=/ s#"$# --db-file=/var/lib/ogn-asterix-bridge/aircraft.csv.gz"#' "$READSB_CONFIG"
if ! grep -q -- '--db-file-lt' "$READSB_CONFIG"; then
    sudo sed -i '/^DECODER_OPTIONS=/ s/"$/ --db-file-lt"/' "$READSB_CONFIG"
fi
sudo systemctl daemon-reload
sudo systemctl enable ogn-asterix-bridge.service
sudo systemctl restart ogn-asterix-bridge.service
sudo systemctl restart readsb.service
sudo systemctl status --no-pager ogn-asterix-bridge.service readsb.service
