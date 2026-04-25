# NetBox API Hypotheses (need more data)

## H1: MMS4X00-NM-FLT lives under /dcim/module-types/ or /dcim/inventory-items/
The model string wasn't found under /dcim/device-types/. It looks like a Mellanox/NVIDIA
network module. Needs testing against module-types and inventory-items endpoints.

## H2: device_type__model is also a dead filter (like site__name)
We saw it return 313k results, same as site__name. Needs confirmation by testing with a
known-valid device type model string to see if it filters or gets ignored.

## H3: Other __lookup filters may also be silently ignored
Only `site=<slug>` and `name__ic` have been confirmed working. Other `field__lookup`
patterns from the Glean docs may also be broken. Test each new filter before trusting it.
