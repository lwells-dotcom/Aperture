# Optic Counting Script for inventory tracking

[User guide can be found in confluence]([https://www.markdownguide.org](https://coreweave.atlassian.net/wiki/spaces/~71202033c11abfc5ac4e7295722dd23b043a53/pages/851247154/Optic+Counting+Script))

## Key Functionality

The script will take build documents as input such as IB, ROCE and CUTSHEETs. It then will take an inventory of the required optics needed to complete those documents and display the count as output. These numbers can be used to ensure all optics required are present for a particular build.

## New Demo Additions

This folder now supports two demo paths:

1. Existing Tkinter GUI, now augmented with:
- Simulated Okta-style PIN verification
- Per-user JSON token issuance
- Strict grounded AI Q&A over parsed sheet context

2. Lightweight Flask web demo, with API endpoints for:
- `POST /api/demo-verify-pin`
- `POST /api/upload-count`
- `POST /api/sheet-qa`
- `GET /api/audit-log`

## Strict Grounded Mode

AI Q&A is constrained to uploaded and parsed sheet context only.
If context is missing data, the assistant is instructed to say what is missing and not guess.

## Column C Location Intelligence

For location-style questions (for example: "in column C where and what"), the app now:
- Parses cutsheet Column C (`A-LOC:CAB:RU`) and links each location to optics found on that row.
- Preserves row references and source file names for traceability.
- Returns grouped "where + what" output by location.

This location parser runs only when the question intent is location-focused (`where`, `location`, `column c`, `loc:cab:ru`, etc.).

## SOX Compliance Mapping

If a Q&A prompt looks compliance-related (SOX/audit/violation keywords), the app now:
- Parses `sarbanes_oxley_act_of_2002.pdf` (or `SARBANES_OXLEY_ACT_OF_2002.PDF`) when present.
- Selects relevant sections and asks the model to map potential violations by section number.
- Returns confidence and evidence limits when data is incomplete.

Optional env settings:
- `SOX_ALWAYS_ON=1` to force compliance mapping on every Q&A prompt.
- `SOX_ACT_PDF_PATH=/absolute/path/to/SARBANES_OXLEY_ACT_OF_2002.PDF` to set the PDF path explicitly.

## Demo Setup

1. Create a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements-demo.txt
```

3. Configure environment variables using a local `.env` file:

```bash
cp .env.example .env
# edit .env with your real OPENAI_API_KEY and secure token secret
```

### Run Tkinter GUI

```bash
python Optic_Count_GUI_Main.py
```

### Run Web Demo

```bash
python demo_web_app.py
# open http://localhost:5050
```

## Notes

- The JSON token is demo auth only and simulates an Okta Verify-style gate.
- For production, replace PIN verification with real Okta flows and proper session management.
- Keep OpenAI API key server-side only.
- `.env` is loaded automatically by the scripts and should never be committed.

### IB Notes

**IB** Doesn't list specific optics and only genericly lists "Twin Optic" so a count will be generated using this provided generic name and not a specific model. In addition for the Node to Leave tabs no optics are mentioned. So for now the script assumes two generic optics per one line in the provided tab.

**IB** does not always list specific optics and may only show generic optic references.
Node-to-leaf sections may require assumptions where optics are not explicitly specified.
