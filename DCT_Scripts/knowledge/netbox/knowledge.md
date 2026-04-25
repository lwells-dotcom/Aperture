# NetBox API Knowledge

## Site Naming
- NetBox site names follow patterns like: US-EAST-08A, US-CENTRAL-02A, US-WEST-03A, RNO1
- Internal/facility CFN codes (e.g. US-LZL-01, US-LZL01) do NOT match NetBox site names
- The `asset_location` custom field uses yet another naming scheme: US-CMH01:dh1:122:01, US-PLZ01.DH1.R104.RU26

## Known Site Mappings (CFN -> NetBox slug)
- Ellendale / US-LZL-01 -> `us-central-08a` (and `us-central-08b`)

## API Filter Behavior (as of 2026-03-30)
- `site__name` is NOT a valid filter on our NetBox Cloud instance. It is silently ignored, returning the full unfiltered inventory (~313k devices).
- `site=<slug>` IS the correct filter. It validates input and rejects bad values with an error.
- `name__ic=<substring>` works for case-insensitive device name matching.
- `device_type__model` also appears to be silently ignored (same unfiltered behavior).
- The Glean #netbox channel docs claim `site__name=<CFN>` is the standard pattern. This is incorrect for REST API queries on our instance.

## Device Count Reference
- us-central-08a (Ellendale): 13,807 devices (as of 2026-03-30)
- Total inventory: ~313,000 devices

## Device Type / Module Notes
- MMS4X00-NM-FLT: not found as a device_type or via `q=MMS` search. Likely a module type or inventory item. Still needs investigation under /dcim/module-types/ or /dcim/inventory-items/.
- Optics are not a dedicated NetBox object. They're inferred from device attributes, roles, tags, or naming conventions.
