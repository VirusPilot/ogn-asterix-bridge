// Provenance for ICAO addresses shared by OGN ASTERIX and directly received Mode S.
(function () {
    let snapshot = null;

    window.ognBridgeIsOgn = function (icao, type) {
        if (type !== "adsb_icao" || !icao)
            return false;
        if (icao.startsWith("~"))
            return true;
        if (!snapshot || Date.now() - snapshot.updatedAt > 12000)
            return false;
        const data = snapshot.data;
        const address = icao.toUpperCase();
        return !!(data.raw_ready && data.ogn && data.ogn[address]
            && !(data.direct && data.direct[address]));
    };

    async function refresh() {
        try {
            const response = await fetch("ogn-sources.json?" + Date.now(), {cache: "no-store"});
            if (!response.ok)
                throw new Error("OGN source status unavailable");
            const data = await response.json();
            const updatedAt = snapshot && snapshot.data.now === data.now
                ? snapshot.updatedAt : Date.now();
            snapshot = {data: data, updatedAt: updatedAt};
        } catch (_) {
            snapshot = null;
        }
        setTimeout(refresh, 3000);
    }

    refresh();
})();
