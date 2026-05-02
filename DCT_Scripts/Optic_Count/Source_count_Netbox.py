import csv
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from utils import natural_key as _natural_key

log = logging.getLogger(__name__)

NETBOX_URL = "https://coreweave.cloud.netboxapp.com"
NETBOX_TOKEN = os.getenv("NETBOX_API_TOKEN", "").strip().strip('"').strip("'")
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

INTERFACE_TYPE_LABELS = {
    "TYPE_VIRTUAL": ("Virtual", "Virtual"),
    "TYPE_BRIDGE": ("Virtual", "Bridge"),
    "TYPE_LAG": ("Virtual", "Link Aggregation Group (LAG)"),
    "TYPE_100BASE_TX": ("Ethernet (Copper)", "100BASE-TX (100ME)"),
    "TYPE_1000BASE_T": ("Ethernet (Copper)", "1000BASE-T (1GE)"),
    "TYPE_2_5GBASE_T": ("Ethernet (Copper)", "2.5GBASE-T (2.5GE)"),
    "TYPE_5GBASE_T": ("Ethernet (Copper)", "5GBASE-T (5GE)"),
    "TYPE_10GBASE_T": ("Ethernet (Copper)", "10GBASE-T (10GE)"),
    "TYPE_10GBASE_CX4": ("Ethernet (Copper)", "10GBASE-CX4 (10GE)"),
    "TYPE_GBIC": ("Ethernet (Copper)", "GBIC (1GE)"),
    "TYPE_SFP": ("Ethernet (Copper)", "SFP (1GE)"),
    "TYPE_1GE_FIXED": ("Ethernet (Fixed)", "1GE (Fixed)"),
    "TYPE_1GE_GBIC": ("Ethernet (Fixed)", "1GE (GBIC)"),
    "TYPE_1GE_SFP": ("Ethernet (Fixed)", "1GE (SFP)"),
    "TYPE_2_5GE_FIXED": ("Ethernet (Fixed)", "2.5GE (Fixed)"),
    "TYPE_5GE_FIXED": ("Ethernet (Fixed)", "5GE (Fixed)"),
    "TYPE_10GE_FIXED": ("Ethernet (Fixed)", "10GE (Fixed)"),
    "TYPE_10GE_CX4": ("Ethernet (Fixed)", "10GE (CX4)"),
    "TYPE_10GE_SFP_PLUS": ("Ethernet (Fixed)", "10GE (SFP+)"),
    "TYPE_10GE_XFP": ("Ethernet (Fixed)", "10GE (XFP)"),
    "TYPE_10GE_XENPAK": ("Ethernet (Fixed)", "10GE (XENPAK)"),
    "TYPE_10GE_X2": ("Ethernet (Fixed)", "10GE (X2)"),
    "TYPE_25GE_SFP28": ("Ethernet (Fixed)", "25GE (SFP28)"),
    "TYPE_40GE_FIXED": ("Ethernet (Fixed)", "40GE (Fixed)"),
    "TYPE_40GE_MXC": ("Ethernet (Fixed)", "40GE (MXC)"),
    "TYPE_40GE_QSFP_PLUS": ("Ethernet (Fixed)", "40GE (QSFP+)"),
    "TYPE_50GE_QSFP28": ("Ethernet (Fixed)", "50GE (QSFP28)"),
    "TYPE_100GE_FIXED": ("Ethernet (Fixed)", "100GE (Fixed)"),
    "TYPE_100GE_CFP": ("Ethernet (Fixed)", "100GE (CFP)"),
    "TYPE_100GE_CFP2": ("Ethernet (Fixed)", "100GE (CFP2)"),
    "TYPE_100GE_CFP4": ("Ethernet (Fixed)", "100GE (CFP4)"),
    "TYPE_100GE_CPAK": ("Ethernet (Fixed)", "100GE (Cisco CPAK)"),
    "TYPE_100GE_QSFP28": ("Ethernet (Fixed)", "100GE (QSFP28)"),
    "TYPE_200GE_CFP2": ("Ethernet (Fixed)", "200GE (CFP2)"),
    "TYPE_200GE_QSFP56": ("Ethernet (Fixed)", "200GE (QSFP56)"),
    "TYPE_400GE_QSFP_DD": ("Ethernet (Fixed)", "400GE (QSFP-DD)"),
    "TYPE_400GE_OSFP": ("Ethernet (Fixed)", "400GE (OSFP)"),
    "TYPE_400GE_CFP2": ("Ethernet (Fixed)", "400GE (CFP2)"),
    "TYPE_800GE_QSFP_DD": ("Ethernet (Fixed)", "800GE (QSFP-DD)"),
    "TYPE_800GE_OSFP": ("Ethernet (Fixed)", "800GE (OSFP)"),
    "TYPE_10GBASE_X_SFP_PLUS": ("Ethernet (Optical)", "10GBASE-X (10GE SFP+)"),
    "TYPE_10GBASE_X_XFP": ("Ethernet (Optical)", "10GBASE-X (10GE XFP)"),
    "TYPE_10GBASE_X_XENPAK": ("Ethernet (Optical)", "10GBASE-X (10GE XENPAK)"),
    "TYPE_10GBASE_X_X2": ("Ethernet (Optical)", "10GBASE-X (10GE X2)"),
    "TYPE_25GBASE_X_SFP28": ("Ethernet (Optical)", "25GBASE-X (25GE SFP28)"),
    "TYPE_40GBASE_X_QSFP_PLUS": ("Ethernet (Optical)", "40GBASE-X (40GE QSFP+)"),
    "TYPE_50GBASE_X_SFP28": ("Ethernet (Optical)", "50GBASE-X (50GE SFP28)"),
    "TYPE_100GBASE_X_QSFP28": ("Ethernet (Optical)", "100GBASE-X (100GE QSFP28)"),
    "TYPE_200GBASE_X_QSFP56": ("Ethernet (Optical)", "200GBASE-X (200GE QSFP56)"),
    "TYPE_400GBASE_X_QSFP_DD": ("Ethernet (Optical)", "400GBASE-X (400GE QSFP-DD)"),
    "TYPE_400GBASE_X_OSFP": ("Ethernet (Optical)", "400GBASE-X (400GE OSFP)"),
    "TYPE_800GBASE_X_OSFP": ("Ethernet (Optical)", "800GBASE-X (800GE OSFP)"),
    "TYPE_INFINIBAND_SDR": ("InfiniBand", "SDR (2 Gbps)"),
    "TYPE_INFINIBAND_DDR": ("InfiniBand", "DDR (4 Gbps)"),
    "TYPE_INFINIBAND_QDR": ("InfiniBand", "QDR (8 Gbps)"),
    "TYPE_INFINIBAND_FDR10": ("InfiniBand", "FDR10 (10 Gbps)"),
    "TYPE_INFINIBAND_FDR": ("InfiniBand", "FDR (13.5 Gbps)"),
    "TYPE_INFINIBAND_EDR": ("InfiniBand", "EDR (25 Gbps)"),
    "TYPE_INFINIBAND_HDR": ("InfiniBand", "HDR (50 Gbps)"),
    "TYPE_INFINIBAND_NDR": ("InfiniBand", "NDR (100 Gbps)"),
    "TYPE_INFINIBAND_XDR": ("InfiniBand", "XDR (250 Gbps)"),
    "TYPE_FC_SFP": ("Fibre Channel", "SFP (1/2/4/8GFC)"),
    "TYPE_FC_SFP_PLUS": ("Fibre Channel", "SFP+ (8/16/32GFC)"),
    "TYPE_FC_QSFP": ("Fibre Channel", "QSFP28 (32GFC)"),
    "TYPE_T1": ("Serial", "T1 (1.544 Mbps)"),
    "TYPE_E1": ("Serial", "E1 (2.048 Mbps)"),
    "TYPE_T3": ("Serial", "T3 (45 Mbps)"),
    "TYPE_E3": ("Serial", "E3 (34 Mbps)"),
    "TYPE_CISCO_FLEXSTACK": ("Serial", "Cisco FlexStack"),
    "TYPE_CISCO_FLEXSTACK_PLUS": ("Serial", "Cisco FlexStack Plus"),
    "TYPE_CISCO_STACKWISE": ("Serial", "Cisco StackWise"),
    "TYPE_CISCO_STACKWISE_PLUS": ("Serial", "Cisco StackWise Plus"),
    "TYPE_CISCO_STACKWISE_480": ("Serial", "Cisco StackWise-480"),
    "TYPE_CISCO_STACKWISE_1T": ("Serial", "Cisco StackWise-1T"),
    "TYPE_JUNIPER_VCP": ("Serial", "Juniper VCP"),
    "TYPE_EXTREME_SUMMITSTACK": ("Serial", "Extreme SummitStack"),
    "TYPE_EXTREME_SUMMITSTACK_128": ("Serial", "Extreme SummitStack-128"),
    "TYPE_EXTREME_SUMMITSTACK_256": ("Serial", "Extreme SummitStack-256"),
    "TYPE_EXTREME_SUMMITSTACK_512": ("Serial", "Extreme SummitStack-512"),
    "TYPE_XDSL": ("DSL", "xDSL"),
    "TYPE_DOCSIS": ("Cable", "DOCSIS"),
    "TYPE_GPON": ("PON", "GPON (2.5 Gbps / 1.25 Gbps)"),
    "TYPE_XG_PON": ("PON", "XG-PON (10 Gbps / 2.5 Gbps)"),
    "TYPE_XGS_PON": ("PON", "XGS-PON (10 Gbps)"),
    "TYPE_NG_PON2": ("PON", "NG-PON2 (TWDM-PON) (4x10 Gbps)"),
    "TYPE_EPON": ("PON", "EPON (1 Gbps)"),
    "TYPE_10G_EPON": ("PON", "10G-EPON (10 Gbps)"),
    "TYPE_IEEE802_11A": ("Wireless", "IEEE 802.11a"),
    "TYPE_IEEE802_11G": ("Wireless", "IEEE 802.11b/g"),
    "TYPE_IEEE802_11N": ("Wireless", "IEEE 802.11n"),
    "TYPE_IEEE802_11AC": ("Wireless", "IEEE 802.11ac"),
    "TYPE_IEEE802_11AD": ("Wireless", "IEEE 802.11ad"),
    "TYPE_IEEE802_11AX": ("Wireless", "IEEE 802.11ax"),
    "TYPE_IEEE802_11AY": ("Wireless", "IEEE 802.11ay"),
    "TYPE_IEEE802_15_1": ("Wireless", "IEEE 802.15.1 (Bluetooth)"),
    "TYPE_OTHER_WIRELESS": ("Wireless", "Other (Wireless)"),
    "TYPE_GSM": ("Cellular", "GSM"),
    "TYPE_CDMA": ("Cellular", "CDMA"),
    "TYPE_LTE": ("Cellular", "LTE"),
    "TYPE_4G": ("Cellular", "4G"),
    "TYPE_5G": ("Cellular", "5G"),
    "TYPE_OC3": ("SONET", "OC-3/STM-1"),
    "TYPE_OC12": ("SONET", "OC-12/STM-4"),
    "TYPE_OC48": ("SONET", "OC-48/STM-16"),
    "TYPE_OC192": ("SONET", "OC-192/STM-64"),
    "TYPE_OC768": ("SONET", "OC-768/STM-256"),
    "TYPE_OC1920": ("SONET", "OC-1920/STM-640"),
    "TYPE_OC3840": ("SONET", "OC-3840/STM-1234"),
}


def _iface_label(enum_value):
    entry = INTERFACE_TYPE_LABELS.get(enum_value)
    if entry:
        return entry
    label = enum_value.replace("TYPE_", "").replace("_", " ").title()
    return ("Other", label)


def _graphql_request(query, timeout=600):
    import ssl, certifi  # noqa: E401
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(
        f"{NETBOX_URL}/graphql/",
        data=json.dumps({"query": query}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {NETBOX_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Required by NetBox ops for load attribution per the NETDEV/576094416
            # "Interacting with NetBox APIs" example.
            "User-Agent": "atlas-optic-count/1.0",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _graphql_with_retry(query, timeout=600, attempts=4, base_delay=1.5):
    """
    Wrap _graphql_request with retry on transient gateway errors (502/503/504,
    429, URLError). Exponential backoff with small jitter.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return _graphql_request(query, timeout=timeout)
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code not in (429, 502, 503, 504):
                raise
        except urllib.error.URLError as e:
            last_exc = e
        delay = base_delay * (2 ** i) + random.uniform(0, 0.5)
        log.warning(
            "NetBox transient error (attempt %d/%d), sleeping %.1fs",
            i + 1,
            attempts,
            delay,
        )
        time.sleep(delay)
    raise RuntimeError(f"NetBox unreachable after {attempts} attempts: {last_exc}")


# NetBox caps GraphQL responses at 1000 records/query (per NETO11Y/1617002534
# postmortem 2026-04-22; the cap was temporarily bumped to 4000 but is
# returning to 1000). Above the cap, results are silently truncated.
GRAPHQL_PAGE_SIZE = 1000


def _graphql_paginated(field_name, build_query, page_size=GRAPHQL_PAGE_SIZE, timeout=600):
    """
    Page through a NetBox GraphQL list query using `start`-cursor pagination.

    `build_query(start, limit)` must return a query string that selects `id`
    on each row of `field_name` and includes `pagination: {start: <id>, limit: <n>}`
    on that field. We loop, advancing `start` to max(id) seen, until a batch
    comes back shorter than `page_size`.

    NetBox 4.5.2's `start` is keyed on the indexed `id` column (per NO-1662),
    so this avoids the OFFSET full-table-scan that hits the NetBox DB OOM.

    Returns the concatenated list of rows. Errors propagate.
    """
    rows = []
    start = 0
    while True:
        body = _graphql_with_retry(build_query(start, page_size), timeout=timeout)
        if "errors" in body:
            raise RuntimeError(json.dumps(body["errors"]))
        batch = (body.get("data") or {}).get(field_name) or []
        if not batch:
            return rows
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        max_id = 0
        for row in batch:
            try:
                rid = int(row.get("id") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid > max_id:
                max_id = rid
        if max_id <= start:
            log.warning(
                "Pagination stalled on %s at start=%d (no id > start in batch); "
                "returning %d rows", field_name, start, len(rows)
            )
            return rows
        start = max_id


def _resolve_site_slug(label):
    """Map user input (NetBox site name or slug) to canonical site slug."""
    label = (label or "").strip()
    if not label:
        raise ValueError("site label is empty")
    body = _graphql_with_retry('{ site_list { name slug } }', timeout=30)
    if "errors" in body:
        raise RuntimeError(json.dumps(body["errors"]))
    sites = (body.get("data") or {}).get("site_list") or []
    for s in sites:
        slug = s.get("slug") or ""
        if slug == label:
            return slug
    low = label.lower()
    for s in sites:
        slug = s.get("slug") or ""
        name = (s.get("name") or "")
        if slug.lower() == low or name.lower() == low:
            return slug
    raise RuntimeError(
        f"Unknown NetBox site: {label!r} (no matching name or slug among {len(sites)} sites)"
    )


def _test_netbox_reachable():
    import ssl, certifi  # noqa: E401
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(f"{NETBOX_URL}/api/", method="GET")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            if resp.status == 200:
                return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):  # 401 = auth required, 403 = forbidden — server is up
            return True
    except urllib.error.URLError:
        pass
    return False


def _query_single_site(site_slug, active_only=True, include_optic_locations=False):
    safe_site = site_slug.replace('"', '\\"')
    ACTIVE_STATUSES = {"active", "provisioned"}

    def build_devices(start, limit):
        return f"""
        {{
          device_list(
            filters: {{ site: {{ slug: {{ exact: "{safe_site}" }} }} }}
            pagination: {{ start: {start}, limit: {limit} }}
          ) {{
            id
            name
            serial
            status
            device_type {{ display }}
            location {{ slug }}
            rack {{ name }}
            position
          }}
        }}
        """

    def build_interfaces(start, limit):
        return f"""
        {{
          interface_list(
            filters: {{
              device: {{ site: {{ slug: {{ exact: "{safe_site}" }} }} }}
              NOT: {{ type: {{ exact: TYPE_VIRTUAL }} }}
            }}
            pagination: {{ start: {start}, limit: {limit} }}
          ) {{
            id
            name
            type
            device {{
              name
              status
              location {{ slug }}
              rack {{ name }}
              position
            }}
          }}
        }}
        """

    devices_data = _graphql_paginated("device_list", build_devices)
    interfaces_data = _graphql_paginated("interface_list", build_interfaces)
    data = {"device_list": devices_data, "interface_list": interfaces_data}

    device_type_counts = {}
    device_rows = []
    for device in data.get("device_list", []):
        status = device.get("status") or ""
        if active_only and status not in ACTIVE_STATUSES:
            continue
        model = (device.get("device_type") or {}).get("display") or "Unknown"
        device_type_counts[model] = device_type_counts.get(model, 0) + 1
        raw_pos = device.get("position")
        device_rows.append((
            device.get("name") or "",
            model,
            device.get("serial") or "",
            site_slug,
            (device.get("location") or {}).get("slug") or "",
            (device.get("rack") or {}).get("name") or "",
            int(float(raw_pos)) if raw_pos is not None else "",
            status,
        ))

    interface_type_counts = {}
    interface_rows = []
    for iface in data.get("interface_list", []):
        iface_type = iface.get("type") or "Unknown"
        if iface_type.upper() == "TYPE_VIRTUAL":
            continue
        dev = iface.get("device") or {}
        device_status = dev.get("status") or ""
        if active_only and device_status not in ACTIVE_STATUSES:
            continue
        interface_type_counts[iface_type] = interface_type_counts.get(iface_type, 0) + 1
        if include_optic_locations:
            iface_name = iface.get("name") or ""
            dev_name = dev.get("name") or ""
            location_slug = (dev.get("location") or {}).get("slug") or ""
            rack_name = (dev.get("rack") or {}).get("name") or ""
            raw_pos = dev.get("position")
            u_pos = int(float(raw_pos)) if raw_pos is not None else ""
            _, optic_label = _iface_label(iface_type)
            iface_location = f"{location_slug} {rack_name} U{u_pos} / {iface_name}".strip()
            interface_rows.append((
                dev_name,
                iface_name,
                optic_label,
                site_slug,
                location_slug,
                rack_name,
                u_pos,
                device_status,
                iface_location,
            ))

    return device_type_counts, device_rows, interface_type_counts, interface_rows


def get_site_inventory(site_name, output_queue, active_only=True, include_optic_locations=False):
    if not NETBOX_TOKEN:
        output_queue.put("Error: NETBOX_API_TOKEN environment variable is not set.\n")
        output_queue.put(None)
        return

    if not _test_netbox_reachable():
        output_queue.put("Error: NetBox is not reachable.\n")
        output_queue.put(None)
        return

    output_queue.put("NetBox is reachable.\n")
    try:
        site_slug = _resolve_site_slug(site_name)
    except (RuntimeError, ValueError) as e:
        output_queue.put(f"Error: {e}\n")
        output_queue.put(None)
        return

    output_queue.put(f"Querying inventory for site: {site_slug}...\n\n")

    try:
        try:
            device_type_counts, device_rows, interface_type_counts, interface_rows = _query_single_site(
                site_slug,
                active_only=active_only,
                include_optic_locations=include_optic_locations,
            )

            lines = []
            total_devices = sum(device_type_counts.values())
            lines.append(f"=== Device Count ({total_devices} total) ===")
            for model, count in sorted(device_type_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {model}: {count}")

            total_ifaces = sum(interface_type_counts.values())
            lines.append(f"\n=== Interface Count ({total_ifaces} total) ===")
            for enum_val, count in sorted(interface_type_counts.items(), key=lambda x: x[1], reverse=True):
                _cat, label = _iface_label(enum_val)
                lines.append(f"  {label}: {count}")

            output_queue.put("\n".join(lines) + "\n")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            summary_filename = os.path.join(DOWNLOADS_DIR, f"netbox_{site_slug}_{timestamp}.csv")
            try:
                with open(summary_filename, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Section", "Category", "Name", "Count"])
                    writer.writerow(["Meta", "", "Site slug", site_slug])
                    writer.writerow(["Meta", "", "Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
                    writer.writerow([])
                    for model, count in sorted(device_type_counts.items(), key=lambda x: x[1], reverse=True):
                        writer.writerow(["Devices", "Device Model", model, count])
                    writer.writerow([])
                    for enum_val, count in sorted(interface_type_counts.items(), key=lambda x: x[1], reverse=True):
                        category, label = _iface_label(enum_val)
                        writer.writerow(["Interfaces", category, label, count])
                output_queue.put(f"\nSummary saved to: {summary_filename}\n")
            except OSError as e:
                output_queue.put(f"\nCould not save summary file: {e}\n")

            detail_filename = os.path.join(DOWNLOADS_DIR, f"netbox_{site_slug}_{timestamp}_devices.csv")
            try:
                with open(detail_filename, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Device Name", "Model", "Serial Number", "Facility", "Room", "Rack", "U Position", "Status"])
                    for dev_name, model, serial, facility, room, rack, u_pos, status in sorted(device_rows, key=lambda x: x[0]):
                        writer.writerow([dev_name, model, serial, facility, room, rack, u_pos, status])
                output_queue.put(f"Device detail saved to: {detail_filename}\n")
            except OSError as e:
                output_queue.put(f"\nCould not save device detail file: {e}\n")

            if include_optic_locations and interface_rows:
                optics_filename = os.path.join(DOWNLOADS_DIR, f"netbox_{site_slug}_{timestamp}_optics.csv")
                try:
                    with open(optics_filename, "w", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(["Switch Name", "Interface Name", "Optic Type", "Facility", "Room", "Rack", "U Position", "Status", "Interface Location"])
                        for row in sorted(interface_rows, key=lambda x: (_natural_key(x[0]), _natural_key(x[1]))):
                            writer.writerow(list(row))
                    output_queue.put(f"Optic locations saved to: {optics_filename}\n")
                except OSError as e:
                    output_queue.put(f"\nCould not save optics file: {e}\n")

        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
            output_queue.put(f"Query failed: {e}\n")
    finally:
        output_queue.put(None)


def get_all_sites_inventory(output_queue, active_only=True, include_optic_locations=False):
    if not NETBOX_TOKEN:
        output_queue.put("Error: NETBOX_API_TOKEN environment variable is not set.\n")
        output_queue.put(None)
        return

    if not _test_netbox_reachable():
        output_queue.put("Error: NetBox is not reachable.\n")
        output_queue.put(None)
        return

    output_queue.put("NetBox is reachable.\n")
    output_queue.put("Fetching site list...\n")

    try:
        site_body = _graphql_with_retry("{ site_list { name slug } }", timeout=30)
        if "errors" in site_body:
            output_queue.put(f"Could not fetch site list: {site_body['errors']}\n")
            output_queue.put(None)
            return
        all_sites = sorted(
            s["slug"] for s in site_body.get("data", {}).get("site_list", []) if s.get("slug")
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        output_queue.put(f"Failed to fetch site list: {e}\n")
        output_queue.put(None)
        return

    total_sites = len(all_sites)
    output_queue.put(f"Found {total_sites} sites. Starting parallel queries (10 workers)...\n\n")

    site_devices = {}
    site_device_rows = {}
    site_ifaces = {}
    site_iface_rows = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    completed_count = 0
    lock = threading.Lock()

    failed_sites = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_site = {executor.submit(_query_single_site, site, active_only, include_optic_locations): site for site in all_sites}
        for future in as_completed(future_to_site):
            site_name = future_to_site[future]
            with lock:
                completed_count += 1
                progress = completed_count
            try:
                dev_counts, dev_rows, iface_counts, iface_rows = future.result()
                site_devices[site_name] = dev_counts
                site_device_rows[site_name] = dev_rows
                site_ifaces[site_name] = iface_counts
                site_iface_rows[site_name] = iface_rows
                output_queue.put(
                    f"[{progress}/{total_sites}] {site_name} — "
                    f"{sum(dev_counts.values())} devices, "
                    f"{sum(iface_counts.values())} interfaces\n"
                )
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                output_queue.put(f"[{progress}/{total_sites}] ERROR on {site_name}: {e} — will retry\n")
                failed_sites.append(site_name)
                site_devices[site_name] = {}
                site_device_rows[site_name] = []
                site_ifaces[site_name] = {}
                site_iface_rows[site_name] = []

    try:
        if failed_sites:
            output_queue.put(f"\nRetrying {len(failed_sites)} failed site(s)...\n")
            for site_name in failed_sites:
                time.sleep(10)
                output_queue.put(f"  Retrying {site_name}...\n")
                try:
                    dev_counts, dev_rows, iface_counts, iface_rows = _query_single_site(site_name, active_only=active_only, include_optic_locations=include_optic_locations)
                    site_devices[site_name] = dev_counts
                    site_device_rows[site_name] = dev_rows
                    site_ifaces[site_name] = iface_counts
                    site_iface_rows[site_name] = iface_rows
                    output_queue.put(
                        f"  OK {site_name} — "
                        f"{sum(dev_counts.values())} devices, "
                        f"{sum(iface_counts.values())} interfaces\n"
                    )
                except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
                    output_queue.put(f"  FAILED again {site_name}: {e}\n")

        grand_devices = sum(sum(m.values()) for m in site_devices.values())
        grand_ifaces = sum(sum(t.values()) for t in site_ifaces.values())
        lines = [
            f"\n=== All Sites Complete: {total_sites} sites | "
            f"{grand_devices} devices | {grand_ifaces} interfaces ===\n"
        ]
        for site_name in all_sites:
            dev_counts = site_devices.get(site_name, {})
            iface_counts = site_ifaces.get(site_name, {})
            lines.append(
                f"--- {site_name} "
                f"({sum(dev_counts.values())} devices, {sum(iface_counts.values())} interfaces) ---"
            )
            for model, count in sorted(dev_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {model}: {count}")
            for enum_val, count in sorted(iface_counts.items(), key=lambda x: x[1], reverse=True):
                _cat, label = _iface_label(enum_val)
                lines.append(f"  {label}: {count}")
            lines.append("")

        # Final combined total across all sites
        combined_devices = {}
        combined_ifaces = {}
        for site_name in all_sites:
            for model, count in site_devices.get(site_name, {}).items():
                combined_devices[model] = combined_devices.get(model, 0) + count
            for enum_val, count in site_ifaces.get(site_name, {}).items():
                combined_ifaces[enum_val] = combined_ifaces.get(enum_val, 0) + count

        lines.append(f"=== FINAL TOTAL: {grand_devices} devices | {grand_ifaces} interfaces ===")
        for model, count in sorted(combined_devices.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {model}: {count}")
        lines.append("")
        for enum_val, count in sorted(combined_ifaces.items(), key=lambda x: x[1], reverse=True):
            _cat, label = _iface_label(enum_val)
            lines.append(f"  {label}: {count}")

        output_queue.put("\n".join(lines))

        summary_filename = os.path.join(DOWNLOADS_DIR, f"netbox_ALL_SITES_count_{timestamp}.csv")
        try:
            with open(summary_filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Site", "Section", "Category", "Name", "Count"])
                writer.writerow(["", "Meta", "", "Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
                writer.writerow([])
                for site_name in all_sites:
                    for model, count in sorted(site_devices.get(site_name, {}).items(), key=lambda x: x[1], reverse=True):
                        writer.writerow([site_name, "Devices", "Device Model", model, count])
                    for enum_val, count in sorted(site_ifaces.get(site_name, {}).items(), key=lambda x: x[1], reverse=True):
                        category, label = _iface_label(enum_val)
                        writer.writerow([site_name, "Interfaces", category, label, count])
                    writer.writerow([])
                writer.writerow([])
                for model, count in sorted(combined_devices.items(), key=lambda x: x[1], reverse=True):
                    writer.writerow(["Full Count", "Devices", "Device Model", model, count])
                for enum_val, count in sorted(combined_ifaces.items(), key=lambda x: x[1], reverse=True):
                    category, label = _iface_label(enum_val)
                    writer.writerow(["Full Count", "Interfaces", category, label, count])
            output_queue.put(f"\nSummary saved to: {summary_filename}\n")
        except OSError as e:
            output_queue.put(f"\nCould not save summary file: {e}\n")

        detail_filename = os.path.join(DOWNLOADS_DIR, f"netbox_ALL_SITES_{timestamp}_devices.csv")
        try:
            with open(detail_filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Site", "Device Name", "Model", "Serial Number", "Facility", "Room", "Rack", "U Position", "Status"])
                for site_name in all_sites:
                    for dev_name, model, serial, facility, room, rack, u_pos, status in sorted(site_device_rows.get(site_name, []), key=lambda x: x[0]):
                        writer.writerow([site_name, dev_name, model, serial, facility, room, rack, u_pos, status])
            output_queue.put(f"Device detail saved to: {detail_filename}\n")
        except OSError as e:
            output_queue.put(f"\nCould not save device detail file: {e}\n")

        if include_optic_locations:
            optics_filename = os.path.join(DOWNLOADS_DIR, f"netbox_ALL_SITES_{timestamp}_optics.csv")
            try:
                with open(optics_filename, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Site", "Switch Name", "Interface Name", "Optic Type", "Facility", "Room", "Rack", "U Position", "Status", "Interface Location"])
                    for site_name in all_sites:
                        for row in sorted(site_iface_rows.get(site_name, []), key=lambda x: (_natural_key(x[0]), _natural_key(x[1]))):
                            writer.writerow([site_name] + list(row))
                output_queue.put(f"Optic locations saved to: {optics_filename}\n")
            except OSError as e:
                output_queue.put(f"\nCould not save optics file: {e}\n")
    finally:
        output_queue.put(None)


if __name__ == "__main__":
    import queue
    q = queue.Queue()
    threading.Thread(target=get_site_inventory, args=("US-WEST-09A", q), daemon=True).start()
    while True:
        msg = q.get()
        if msg is None:
            break
        print(msg, end="")
