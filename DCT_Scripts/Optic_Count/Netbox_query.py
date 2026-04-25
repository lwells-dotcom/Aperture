import os
import re
import csv
import json
import ssl
import urllib.parse
import urllib.request

import certifi

SITE_ID = 384
TARGET_RACKS = {
    84, 85, 88, 93, 94, 95, 132, 134, 135, 136, 137, 162, 163, 166, 169,
    172, 179, 182, 183, 188, 189, 195, 196, 208, 213, 214, 249, 254, 258, 259
}


def _build_headers():
    token = os.environ["NETBOX_API_TOKEN"]
    return {
        "Authorization": f"Token {token}",
        "User-Agent": "lwells-netbox-rack-serial-export/1.0",
        "Accept": "application/json",
    }


def _ssl_ctx():
    return ssl.create_default_context(cafile=certifi.where())


def get_json(url, headers=None, ssl_ctx=None):
    headers = headers or _build_headers()
    ssl_ctx = ssl_ctx or _ssl_ctx()
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ssl_ctx) as r:
        return json.load(r)


def get_all(url, headers=None, ssl_ctx=None):
    headers = headers or _build_headers()
    ssl_ctx = ssl_ctx or _ssl_ctx()
    rows = []
    while url:
        data = get_json(url, headers, ssl_ctx)
        rows.extend(data.get("results", []))
        url = data.get("next")
    return rows


def rack_num(rack_name):
    if not rack_name:
        return None
    m = re.search(r'(\d+)$', str(rack_name).strip())
    return int(m.group(1)) if m else None


def main():
    headers = _build_headers()
    ctx = _ssl_ctx()

    locations = get_all(
        f"https://coreweave.cloud.netboxapp.com/api/dcim/locations/?site_id={SITE_ID}&limit=1000",
        headers, ctx
    )

    dh4_location = None
    for loc in locations:
        name = (loc.get("name") or "").strip().lower()
        slug = (loc.get("slug") or "").strip().lower()
        if name in {"data hall 4", "dh4", "data hall 204", "dh204"} or slug in {"data-hall-4", "dh4", "data-hall-204", "dh204"}:
            dh4_location = loc
            break

    if not dh4_location:
        print("Could not auto-find DH4 location")
        raise SystemExit(1)

    location_id = dh4_location["id"]
    query = urllib.parse.urlencode({"site_id": SITE_ID, "location_id": location_id, "limit": 1000})
    devices = get_all(
        f"https://coreweave.cloud.netboxapp.com/api/dcim/devices/?{query}",
        headers, ctx
    )

    rows = []
    for d in devices:
        rn = rack_num((d.get("rack") or {}).get("name", ""))
        if rn not in TARGET_RACKS:
            continue
        pos = d.get("position")
        try:
            ru = int(pos) if pos is not None and str(pos).strip() else 0
        except ValueError:
            ru = 0
        rows.append({
            "rack": f"R{rn}",
            "ru": ru,
            "serial": (d.get("serial") or "").strip(),
            "name": (d.get("name") or "").strip(),
            "asset_tag": (d.get("asset_tag") or "").strip(),
        })

    rows.sort(key=lambda x: (int(x["rack"][1:]), -x["ru"], x["name"]))

    out_path = os.path.expanduser("~/dh4_requested_rack_serials.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rack", "ru", "serial", "name", "asset_tag"])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
