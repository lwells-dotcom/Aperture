# NetBox API Rules (confirmed)

## R1: Always use `site=<slug>` for site filtering (confirmed 2026-03-30)
`site__name` is silently ignored on our NetBox Cloud instance and returns the full
unfiltered inventory. Only `site=<slug>` (lowercase, hyphenated) validates and filters
correctly. Confirmed with both invalid values (313k returned) and valid site name
US-CENTRAL-08A (still 313k returned, proving the filter itself is dead).

## R2: Never trust unvalidated NetBox filter results (confirmed 2026-03-30)
If a NetBox API query returns an unexpectedly large count (e.g. 313k), the filter is
probably being silently ignored rather than matching broadly. Always sanity-check the
count and spot-check a few results to confirm the filter is actually narrowing.

## R3: CFN codes != NetBox site names (confirmed 2026-03-30)
Internal facility codes (US-LZL-01) and NetBox site names (US-CENTRAL-08A) are different.
Always look up the slug via /dcim/sites/?q=<keyword> or maintain a mapping table.
