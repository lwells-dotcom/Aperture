# Cutsheet Cleanup Plan

Two-phase approach: Claude in Excel builds the rules, then the Atlas pipeline applies them automatically on every upload.

---

## Phase 1: Claude in Excel — Build the Normalization Rules

Open `MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx` in Excel with Claude in Excel active.

### Prompt 1: Status Column Cleanup

```
Look at the STATUS column on the CUTSHEET tab. There are 6 real status values and ~165 junk values that are section headers or labels pasted into the wrong column. Here's what I need:

The 6 canonical statuses are:
- COMPLETE (maps from: "Cable Is Ran: Complete", "Human Verified")
- NOT_TERMINATED (maps from: "Cable Is Ran: Not Terminated")  
- NOT_RUN (maps from: "Cable Not Run")
- NOT_RUN_PRIORITY (maps from: "Cable Not Run: Priority")
- ADDITION (maps from: "Addition")
- BLANK (maps from: any blank/empty cell)

Everything else (CON-01 Grid C1, DH202 :: C2, TIER-1 TO TIER-0 C2, RACK 51 NET + CON, etc.) is a section header that an engineer pasted into the STATUS column. These rows have NO optic data in A-OPTIC or Z-OPTIC — they're visual dividers, not real cable rows.

Create a new column called STATUS_CANONICAL right after the STATUS column. For each row:
1. If STATUS matches one of the 6 real values above, write the canonical version
2. If STATUS is blank, write BLANK
3. If STATUS doesn't match any known value AND A-OPTIC and Z-OPTIC are both empty, write SECTION_HEADER
4. If STATUS doesn't match any known value BUT has optic data, write UNKNOWN (flag for manual review)

Then create a new tab called STATUS_MAP with two columns: ORIGINAL_STATUS and CANONICAL_STATUS, listing every unique mapping you applied. This becomes the config file for the Atlas app.
```

### Prompt 2: Optic Count Methodology

```
Still on the CUTSHEET tab. Look at columns A-OPTIC and Z-OPTIC.

Key facts about this data:
- Each row is ONE physical cable connection between two endpoints (A-side and Z-side)
- A-OPTIC is the optic installed on the A-side device
- Z-OPTIC is the optic installed on the Z-side device  
- They are TWO SEPARATE physical optics even when they have the same type name
- When A-OPTIC = "OSFP-800G-2DR4" and Z-OPTIC = "QSFP112-400G-DR4", that's 1 OSFP-800G-2DR4 AND 1 QSFP112-400G-DR4 (two optics, two different types)
- When both are "OSFP-800G-2DR4", that's 2 OSFP-800G-2DR4 optics (not 1)

There are 11,284 rows where A-OPTIC and Z-OPTIC are different types. The most common is A: OSFP-800G-2DR4 / Z: QSFP112-400G-DR4 with 10,872 rows.

Create a new tab called OPTIC_SUMMARY with these columns:
- OPTIC_TYPE
- A_SIDE_COUNT (count of this type in A-OPTIC column, excluding SECTION_HEADER rows)
- Z_SIDE_COUNT (count of this type in Z-OPTIC column, excluding SECTION_HEADER rows)
- TOTAL (A + Z, this is the real physical optic count)

Then add a second table on the same tab with the total:
- Total A-side optics: [sum]
- Total Z-side optics: [sum]  
- Grand total optics: [sum]

This is the source of truth. Each side is counted independently. No deduplication.
```

### Prompt 3: Strip Section Headers

```
On the CUTSHEET tab, every row where STATUS_CANONICAL = "SECTION_HEADER" is a visual divider, not cable data. There are about 154 of these.

Create a new tab called CUTSHEET_CLEAN that is an exact copy of CUTSHEET but with:
1. All SECTION_HEADER rows removed
2. The STATUS column replaced with STATUS_CANONICAL values
3. The original STATUS value preserved in a new column called STATUS_ORIGINAL (last column)

This clean tab is what the Atlas app should parse. No junk rows, canonical statuses, all real cable data preserved.
```

---

## Phase 2: Atlas Pipeline Integration

After Claude in Excel generates the STATUS_MAP tab and CUTSHEET_CLEAN tab, we build this into the code.

### What to build (for a CC terminal session):

**File: `cutsheet_preprocessor.py` (new file)**

```
Create a new file cutsheet_preprocessor.py in Optic_Count/ that does automated 
cutsheet normalization at upload time. It should:

1. Load the STATUS_MAP from a config. Start with a hardcoded dict built from the 
   STATUS_MAP tab Claude in Excel generates. Later this becomes a JSON config file 
   per site. The canonical statuses are: COMPLETE, NOT_TERMINATED, NOT_RUN, 
   NOT_RUN_PRIORITY, ADDITION, BLANK, SECTION_HEADER, UNKNOWN.

2. Expose a function: normalize_cutsheet_df(df: pd.DataFrame) -> pd.DataFrame
   - Adds STATUS_CANONICAL column using the mapping
   - Rows with no optic data and unrecognized status get SECTION_HEADER
   - Rows with optic data and unrecognized status get UNKNOWN
   - Returns the dataframe with SECTION_HEADER rows removed
   - Logs how many rows were stripped and any UNKNOWN statuses found

3. Expose a function: count_optics_independently(df: pd.DataFrame) -> dict
   - Counts A-OPTIC and Z-OPTIC as SEPARATE columns
   - Returns {"optic_type": {"a_side": N, "z_side": N, "total": N}, ...}
   - Skips blank/NaN values
   - This replaces the COALESCE approach entirely

4. Expose a function: preprocess_upload(filepath: str) -> dict
   - Reads the xlsx, finds the CUTSHEET tab
   - Runs normalize_cutsheet_df
   - Runs count_optics_independently  
   - Returns {"clean_df": df, "optic_counts": dict, "rows_stripped": int, 
     "unknown_statuses": list}

Then update atlas_web_app.py upload_count route to call preprocess_upload() 
before anything else. The clean_df gets passed to count_all_files_gui and 
build_sheet_context instead of the raw file. The optic_counts go directly into 
the Postgres context so the COALESCE query is no longer needed for the optic 
summary — we have pre-computed accurate counts.
```

---

## What This Gets You

- Engineers upload the same messy cutsheet they always have. Zero behavior change.
- Section header rows stop polluting status counts (was off by 155 rows).
- Optic counts are accurate to the row because A and Z sides are counted independently.
- The COALESCE undercount bug (open item #1) is fixed at the source, not patched in SQL.
- New site formats just need a STATUS_MAP update (Claude in Excel generates it in 2 minutes).
- Parse time drops because we skip ~154 junk rows before any processing starts.
