# Excel & CSV Cleaner (Web App)

A simple browser-based tool: upload a messy Excel/CSV file, clean it with
one click, download the result.

## What the cleaning does
- Removes duplicate rows
- Removes completely empty rows and columns
- Trims extra leading/trailing spaces from text cells
- Cleans up column headers (removes extra spaces, fixes blank headers)

Each of these 4 steps can be turned on/off individually on the page (all are
on by default). "Remove duplicate rows" has a warning tooltip, since repeat
entries (e.g. repeat orders) can sometimes be legitimate data, not mistakes.

### For files full of placeholders and blanks (no AI needed)

Real exports are often dirty in a different way: cells say `ERROR`,
`UNKNOWN` or `N/A` instead of being empty, and numbers end up stored as text.
These deterministic steps (under **Advanced cleaning**) handle that:

- **Placeholders → missing.** A cell that is *entirely* a placeholder
  (`ERROR, UNKNOWN, N/A, NA, #N/A, NULL, NONE, NAN, -, --, ?`, ignoring case
  and spaces) becomes a genuinely empty cell. The list is editable - remove a
  word if it is a legitimate value in your data. The report counts what was
  converted, per column.
- **Numbers stored as text → real numbers.** A column becomes numeric when at
  least 90% of its values are plain numbers (`1,500` is fine). Whole-number
  columns become integers. IDs, codes, zip codes and phone numbers are never
  converted (names like *ID/Code/Zip/Phone*, values with leading zeros, and
  10+ digit strings are protected). Values that don't parse stay as text and
  are listed as invalid. Text like `Rs. 1,500/-` is left for the AI currency rule.
- **Fill a missing value only when it can be worked out exactly.** The app
  looks for relationships in your data and lists them as tick boxes:
  - *Exact arithmetic* (ticked by default), e.g. `Total Spent = Quantity x
    Price Per Unit`, accepted only if it holds on **every** row where all
    three values are present. Any one missing value is then calculated; whole-number
    columns are only filled when the answer is a whole number.
  - *Learned lookups* (**unticked by default**), e.g. `Item → Price Per Unit`
    ("every Item always had the same price"). This is a pattern inferred from
    your file, not a law, so it is opt-in and reported separately as
    "inferred". An ID column can never be a lookup key.

  Existing values are never overwritten, and the report says how many cells
  were filled and by which method.
- **Data quality: before vs after.** A per-column table (type, blank,
  placeholders, missing total, invalid, distinct values) is shown before and
  after cleaning, side by side, and written into the `.txt` report.

Order of steps: headers → trim → placeholders → numbers → duplicates → empty
rows/columns → fills. Cells that are genuinely unknowable (e.g. a missing
Payment Method) stay empty rather than being invented.

### Nicer Excel downloads

`.xlsx` downloads get a bold, frozen header row, columns sized to their
content, and phone-like columns stored as **text** so leading zeros and `+`
signs survive (Excel would otherwise turn `0300-1234567` into a number). CSV
downloads are unchanged.

## Multi-sheet Excel files

If you upload an `.xlsx` file with more than one sheet, you can choose to
clean **all sheets** or just **one specific sheet**. Either way, every sheet
from the original file is kept in the downloaded file under its original
name — a sheet you don't select for cleaning is simply left exactly as it
was, never dropped.

## Cleaning summary report

After clicking "Clean File", the page shows a summary of exactly what
changed per sheet: how many duplicate rows, empty rows, and empty columns
were removed, how many cells were trimmed, and which column headers were
renamed (old name → new name). The same summary can be downloaded as a
`.txt` report alongside the cleaned file.

## AI-suggested cleaning rules (optional)

Beyond the 4 fixed steps, you can ask an AI to suggest extra, more specific
rules based on what it sees in your column names and a few sample values:
normalizing phone numbers to a standard international format, making
category text consistently cased, parsing dates into one format, or turning
currency text ("$1,234.50") into plain numbers.

**Privacy:** the AI only ever receives column names, data types, and up to 8
sample values per column — never your full file, never a full column, and
never another column's data. You can also tick "Mask sample values" to
replace every digit with `9`, every uppercase letter with `X` and every
lowercase letter with `x` before sending them (e.g. `"0300-1234567"` becomes
`"9999-9999999"`, `"Active"` becomes `"Xxxxxx"` and `"ACTIVE"` becomes
`"XXXXXX"`) - the AI still sees the *shape* and the casing pattern of the
data, never the real content.

**Nothing runs automatically.** The AI only suggests rules; you review and
tick/untick each one, and only approved rules are executed - locally, with
plain pandas code, never by the AI itself.

A country/locale selector (Pakistan, US, UK, India, UAE) sets sensible
defaults for phone region, date format, and currency symbol, which the AI
can still override per column if the data clearly shows something else.

**Recipes:** once you've approved a set of rules, you can save them as a
reusable `.json` recipe file. Loading a recipe later skips the AI call
entirely and goes straight to the same approve/reject review screen,
pre-checked - so reusing a recipe on a new (similarly-shaped) file is fast
but still never fully silent.

## Setup (one time)

1. Open a terminal in this folder (`excel-cleaner-webapp`).
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
3. (Only needed for the AI-suggested rules feature) Set your Anthropic API
   key as an environment variable - it's read from here, never typed into
   the page itself:
   ```
   # macOS/Linux
   export ANTHROPIC_API_KEY=your-key-here

   # Windows (PowerShell)
   $env:ANTHROPIC_API_KEY = "your-key-here"
   ```
   Without this, the rest of the app (the 4 deterministic cleaning steps)
   works exactly the same - only the "Suggest Cleaning Rules" button needs it.

   Set it in the same terminal you run `streamlit run app.py` from (or set it
   permanently in your system's environment variables, then restart the
   terminal). The app does **not** read a `.env` file automatically.

### Choosing the AI provider: Groq or Anthropic

Two providers are supported. You only need a key for the one you use.

| Provider | Key variable | Default model (top of `ai_rules.py`) | Override without editing code |
|---|---|---|---|
| Groq | `GROQ_API_KEY` | `GROQ_MODEL_NAME = "openai/gpt-oss-120b"` | `GROQ_MODEL` |
| Anthropic | `ANTHROPIC_API_KEY` | `MODEL_NAME = "claude-sonnet-5-5"` | `ANTHROPIC_MODEL` |

**Groq setup:** create a key in the [Groq console](https://console.groq.com/keys), then
```
# macOS/Linux
export GROQ_API_KEY=your-key-here

# Windows (PowerShell)
$env:GROQ_API_KEY = "your-key-here"
```

**Which one is used?**
1. If `AI_PROVIDER` is set to `groq` or `anthropic`, that one.
2. Otherwise, Groq if `GROQ_API_KEY` is set.
3. Otherwise, Anthropic.

So with both keys set, Groq wins unless you set `AI_PROVIDER=anthropic`.
After suggestions come back, the page shows which provider and model
answered. Both providers get the exact same prompt - only column names,
data types, null counts and up to 8 (optionally masked) sample values per
column - and both answers pass through the same validation, so a malformed
or unsupported suggestion is discarded (and listed) instead of reaching the
review screen.

Groq's free tier has rate limits; if you hit one the page tells you to wait
and try again. Groq's JSON mode guarantees valid JSON but not the exact
shape, which is why every suggestion is validated locally.

**Changing the model when one is retired:** providers retire models from
time to time (Groq retired `llama-3.3-70b-versatile`). If you see "the model
… wasn't found … it may have been retired", pick a current model from the
provider's model list ([Groq](https://console.groq.com/docs/models),
[Anthropic](https://docs.anthropic.com/en/docs/about-claude/models)) and set
the matching variable - no code change needed:
```
# Windows (PowerShell)
$env:GROQ_MODEL = "model-name-here"
$env:ANTHROPIC_MODEL = "model-name-here"

# macOS/Linux
export GROQ_MODEL=model-name-here
```
Restart the app afterwards. The page always shows which model actually
answered. (Streamlit secrets work too: add `GROQ_MODEL = "..."` next to your
key.)

**About `openai/gpt-oss-120b`:** it's a reasoning model, so it thinks before
answering. The app asks for low reasoning effort, leaves a large token budget
(16,000) so thinking plus the JSON answer is never cut off, and parses only
the final answer - never the reasoning text. If you switch `GROQ_MODEL` to a
non-reasoning model, those reasoning options are simply not sent.

### Deploying (e.g. Streamlit Community Cloud)

Don't put the key in the code or commit it. In your app's **Settings →
Secrets**, add the key for the provider you use (and optionally
`AI_PROVIDER`):

```toml
GROQ_API_KEY = "your-key-here"
# ANTHROPIC_API_KEY = "your-key-here"
# AI_PROVIDER = "groq"
```

Streamlit exposes top-level secrets as environment variables, so the app
picks it up with no code changes. For local use of the same mechanism, put
that line in `.streamlit/secrets.toml`.

`.gitignore` already excludes `.env`, `.streamlit/secrets.toml` and other
key-bearing files - keep it that way, and never commit a real key.

## Manual test file

`test_data/messy_sample.csv` has Pakistani phone numbers in 3 formats, an
inconsistently cased Status column, 3 date formats and mixed currency
strings - upload it, click **Clean File**, then **Suggest Cleaning Rules**
to try the AI suggestions.

### Try the rule flow without any API key

`test_data/sample_recipe.json` is a ready-made recipe for that file, in the
same format the app saves. It has one rule of each type: phone numbers →
E.164 (Pakistan), Status → Title Case, Signup Date → `YYYY-MM-DD`, and
Amount Paid → plain numbers. Loading a recipe never calls an AI provider, so
no key is needed:

1. Start the app and upload `test_data/messy_sample.csv`, then click **Clean File**.
2. Open **Load a saved recipe instead of asking the AI**, upload
   `test_data/sample_recipe.json`, and click **Load Recipe**.
3. The 4 rules appear already ticked for review. Untick any you want to skip.
4. Click **Apply Approved Rules**. The summary shows how many cells each
   rule converted (8, 5, 8 and 8 on this file), and the preview shows
   `+923001234567`, `Active`, `2025-01-03` and `1500.0` style values.
5. Optionally download the cleaned file, the report, or the recipe again.

Edit the JSON to experiment: change `"style": "title"` to `"upper"`, or
remove a rule. A rule that breaks the rules (unknown country, a column the
file doesn't have) is skipped with a reason in the summary rather than
crashing.

## Run it

```
streamlit run app.py
```

This starts a local web server and should automatically open a new browser
tab at `http://localhost:8501`. If it doesn't open automatically, copy that
address into your browser manually.

To stop the app, go back to the terminal and press `Ctrl+C`.

## How to use the page

1. Click **"Upload your .xlsx or .csv file"** and choose your messy file.
2. (Excel files with multiple sheets only) Choose whether to clean **all
   sheets** or just **one**.
3. Tick/untick whichever of the 4 cleaning steps you want to run, then click
   **"Clean File"**.
4. (Optional) Pick a country/locale, then click **"Suggest Cleaning Rules"**
   - or load a previously saved recipe instead. Review each suggested rule
   and tick the ones you approve, then click **"Apply Approved Rules"**.
5. Review the cleaning summary, then click **"Download Cleaned File"**,
   **"Download Cleaning Report (.txt)"**, and/or **"Download Recipe (.json)"**
   to save your approved rules for reuse next time.

## Project structure

```
app.py          - the page itself (UI only)
cleaning.py     - the deterministic cleaning steps (headers, trim, placeholders, numbers, duplicates, empty, fills)
quality.py      - shared helpers: placeholder matching, number parsing, phone detection, before/after profile
derive.py       - finding relationships between columns and filling gaps only when exactly derivable
io_utils.py     - loading files / building the downloadable bytes (incl. XLSX polish)
report.py       - building the on-page summary, quality tables and the .txt report
locales.py      - country/locale defaults (phone region, date, currency)
rule_engine.py  - the 4 AI-suggestable rule types, executed locally with pandas
ai_rules.py     - talks to Groq or Anthropic to SUGGEST rules (never runs them)
recipes.py      - saving/loading a reusable set of approved rules as JSON
tests/          - run everything with:  python tests/run_all.py
```

## Running the tests

```
python tests/run_all.py
```
Runs every script in `tests/` (logic tests, plus UI tests that drive the real
page with Streamlit's `AppTest` - no browser, no API key, no network). The
`dirty_cafe_sales.csv` checks run only if the file is at
`Downloads/archive/dirty_cafe_sales.csv`.

The uploaded file is only used in memory while the app is running — nothing
is saved on disk, and your original file is never modified.
