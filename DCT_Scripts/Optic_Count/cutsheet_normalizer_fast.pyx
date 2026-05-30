# cython: language_level=3, boundscheck=False, wraparound=False
"""
cutsheet_normalizer_fast.pyx — Cython-compiled inner loop for normalize_cutsheet.

Compiled from a Python row-loop that called pd.isna() + str.strip() + casefold()
for every cell across thousands of cutsheet rows. The fast path replaces the
per-row pandas overhead with C-typed string handling while preserving the
exact dict shape returned by the pure-Python implementation.

If this extension is not built, cutsheet_normalizer.py transparently falls
back to the original Python loop.
"""

cdef set _EMPTY_TOKENS = {"nan", "-", "none", "null", ""}


cdef inline str cell_norm(object value):
    """Equivalent of _normalize_cell — strip + drop sentinel tokens."""
    cdef str text
    if value is None:
        return ""
    # NaN sentinel detection without importing pandas; NaN != NaN.
    if value != value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.casefold() in _EMPTY_TOKENS:
        return ""
    return text


cdef inline str dns_norm(object value):
    """Equivalent of _normalize_dns — cell_norm + lowercase."""
    cdef str text = cell_norm(value)
    if not text:
        return ""
    return text.lower()


cdef inline str model_norm(object value, object normalize_model_fn):
    """Equivalent of _normalize_model_cell — cell_norm + profile lookup."""
    cdef str text = cell_norm(value)
    if not text:
        return ""
    return normalize_model_fn(text)


cpdef tuple process_rows(
    list records,
    dict a_cols,
    dict z_cols,
    dict aux_cols,
    object normalize_model_fn,
    object parse_breakout_key_fn,
):
    """
    Compiled per-row loop. Mirrors the body of normalize_cutsheet.

    records: list of dicts (from DataFrame.to_dict('records'))
    a_cols / z_cols: {"device","loc","model","locode","port","optic"} -> column names
    aux_cols: column names for status, cable, and breakout columns
    normalize_model_fn: cutsheet_profiles.normalize_model
    parse_breakout_key_fn: _parse_breakout_key (kept Python-side for parity)

    Returns (devices_dict, connections_list) with the same shape the pure
    Python implementation produces.
    """
    cdef dict devices = {}
    cdef list connections = []
    cdef set breakout_seen = set()

    cdef object status_col = aux_cols["status"]
    cdef object cable_col = aux_cols["cable"]
    cdef object a_brk_loc_space = aux_cols["a_brk_loc_space"]
    cdef object a_brk_loc_nl = aux_cols["a_brk_loc_nl"]
    cdef object a_brk_slot_space = aux_cols["a_brk_slot_space"]
    cdef object a_brk_slot_nl = aux_cols["a_brk_slot_nl"]
    cdef object z_brk_loc_space = aux_cols["z_brk_loc_space"]
    cdef object z_brk_loc_nl = aux_cols["z_brk_loc_nl"]
    cdef object z_brk_slot_space = aux_cols["z_brk_slot_space"]
    cdef object z_brk_slot_nl = aux_cols["z_brk_slot_nl"]

    cdef object a_device_col = a_cols["device"]
    cdef object a_loc_col = a_cols["loc"]
    cdef object a_model_col = a_cols["model"]
    cdef object a_locode_col = a_cols["locode"]
    cdef object a_port_col = a_cols["port"]
    cdef object a_optic_col = a_cols["optic"]

    cdef object z_device_col = z_cols["device"]
    cdef object z_loc_col = z_cols["loc"]
    cdef object z_model_col = z_cols["model"]
    cdef object z_locode_col = z_cols["locode"]
    cdef object z_port_col = z_cols["port"]
    cdef object z_optic_col = z_cols["optic"]

    cdef dict row
    cdef str section, status
    cdef str a_dns, a_loc, a_model, a_locode
    cdef str z_dns, z_loc, z_model, z_locode
    cdef bint a_present, z_present
    cdef tuple key
    cdef dict device_entry
    cdef str a_port, z_port, a_optic, z_optic, cable
    cdef str a_brk_loc, a_brk_slot, z_brk_loc, z_brk_slot
    cdef bint is_a_breakout, is_z_breakout, a_breakout_new
    cdef str bkey

    for row in records:
        section = row.get("_section", "UNKNOWN") or "UNKNOWN"
        status = cell_norm(row.get(status_col))

        a_dns = dns_norm(row.get(a_device_col))
        a_loc = cell_norm(row.get(a_loc_col))
        a_model = model_norm(row.get(a_model_col), normalize_model_fn)
        a_locode = cell_norm(row.get(a_locode_col))
        a_present = (a_loc != "") or (a_dns != "")

        z_dns = dns_norm(row.get(z_device_col))
        z_loc = cell_norm(row.get(z_loc_col))
        z_model = model_norm(row.get(z_model_col), normalize_model_fn)
        z_locode = cell_norm(row.get(z_locode_col))
        z_present = (z_loc != "") or (z_dns != "")

        if a_present:
            key = (a_dns, a_loc, a_model)
            device_entry = devices.get(key)
            if device_entry is None:
                device_entry = {
                    "dns_name": a_dns,
                    "loc_cab_ru": a_loc,
                    "model": a_model,
                    "locode": a_locode,
                    "sections": set(),
                    "seen_as": set(),
                    "connection_count": 0,
                }
                devices[key] = device_entry
            device_entry["sections"].add(section)
            device_entry["seen_as"].add("A")
            device_entry["connection_count"] += 1

        if z_present:
            key = (z_dns, z_loc, z_model)
            device_entry = devices.get(key)
            if device_entry is None:
                device_entry = {
                    "dns_name": z_dns,
                    "loc_cab_ru": z_loc,
                    "model": z_model,
                    "locode": z_locode,
                    "sections": set(),
                    "seen_as": set(),
                    "connection_count": 0,
                }
                devices[key] = device_entry
            device_entry["sections"].add(section)
            device_entry["seen_as"].add("Z")
            device_entry["connection_count"] += 1

        if not a_present and not z_present:
            continue

        a_port = cell_norm(row.get(a_port_col))
        z_port = cell_norm(row.get(z_port_col))
        a_optic = cell_norm(row.get(a_optic_col))
        z_optic = cell_norm(row.get(z_optic_col))
        cable = cell_norm(row.get(cable_col))

        a_brk_loc = cell_norm(row.get(a_brk_loc_space)) or cell_norm(row.get(a_brk_loc_nl))
        a_brk_slot = cell_norm(row.get(a_brk_slot_space)) or cell_norm(row.get(a_brk_slot_nl))
        z_brk_loc = cell_norm(row.get(z_brk_loc_space)) or cell_norm(row.get(z_brk_loc_nl))
        z_brk_slot = cell_norm(row.get(z_brk_slot_space)) or cell_norm(row.get(z_brk_slot_nl))

        is_a_breakout = bool(a_brk_loc)
        is_z_breakout = bool(z_brk_loc)
        a_breakout_new = True
        if is_a_breakout and a_present:
            bkey = parse_breakout_key_fn(a_loc, a_port)
            if bkey in breakout_seen:
                a_breakout_new = False
            else:
                breakout_seen.add(bkey)

        connections.append({
            "section": section,
            "status": status,
            "a_dns": a_dns if a_present else "",
            "a_loc": a_loc if a_present else "",
            "a_model": a_model if a_present else "",
            "a_port": a_port,
            "a_optic": a_optic,
            "z_dns": z_dns if z_present else "",
            "z_loc": z_loc if z_present else "",
            "z_model": z_model if z_present else "",
            "z_port": z_port,
            "z_optic": z_optic,
            "cable": cable,
            "a_breakout": is_a_breakout,
            "a_breakout_loc": a_brk_loc,
            "a_breakout_slot": a_brk_slot,
            "a_breakout_new_optic": a_breakout_new,
            "z_breakout": is_z_breakout,
            "z_breakout_loc": z_brk_loc,
            "z_breakout_slot": z_brk_slot,
        })

    return devices, connections
