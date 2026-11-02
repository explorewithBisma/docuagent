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

Order of steps: headers → trim → placeholders → numbers → empty rows/columns →
duplicates → fills. Empty rows are removed *before* duplicates are counted, so
a pile of blank rows is never reported as "duplicates of each other"
("30 empty rows + 80 duplicates", not "109 duplicates"). Cells that are
genuinely unknowable (e.g. a missing Payment Method) stay empty rather than
being invented.

### Nothing disappears silently: removed rows

Every row taken out - a duplicate or a fully empty row - is recorded with the
row number it had in **your uploaded file**, the reason ("Duplicate of row
242" / "Fully empty row"), and its **full original content** (spaces and
placeholders included). You'll find them in a "Removed rows" table on the
Changes tab, in the `.txt` report (row numbers), and on a **Removed Rows**
sheet at the end of the Excel download (one "Removed - <sheet>" sheet per
sheet that lost rows).

### Suspicious values are flagged, never changed

A deterministic check (no AI) looks at numeric columns and flags - with an
orange-red border, a note, a row in the Change Log marked "Flagged (not
changed)", and a per-column "Flagged" count in the quality table:

- **negative values** in columns that shouldn't be negative (age, quantity,
  days, price, cost, amount ...);
- **placeholder codes** such as 999, 9999 or -999 (only when they are rare in
  the column);
- **ages** below 10 or above 100;
- **statistical outliers** - values more than 3× the interquartile range beyond
  the quartiles. This check is skipped for a column when more than 5% of its
  values would be flagged, because that means the column is naturally
  wide-ranging (prices across product types, say) rather than containing a few
  odd values; the Data Quality tab tells you which columns were skipped.

Values are never deleted or altered. Untick **Flag suspicious values** under
Advanced cleaning to turn this off.

### See exactly what changed: the highlighted Excel file

Every cell the cleaning changes is recorded (row, column, old value, new
value, reason), in memory only. The Excel download shows it:

| Color | Meaning |
|---|---|
| Green | calculated exactly from other values in the row |
| Yellow | inferred from a lookup pattern in your file |
| Grey | placeholder (e.g. `ERROR`) cleared to blank |
| Light blue | changed by an approved AI rule |
| Orange-red border (light red fill if nothing else colors it) | flagged as suspicious - **not changed** |

Hover over any changed cell for a note, e.g. `Was: ERROR` or
`Calculated exactly: Quantity 2 x Price Per Unit 3.0`. (Trimmed cells get a
note but no color.) The sheet also has a bold header with a light background,
thin borders, a frozen header row, an AutoFilter, columns sized to their
content, whole numbers without decimals, money columns with 2 decimals, and
phone-like columns stored as **text** so leading zeros and `+` signs survive.

A checkbox (on by default) adds two sheets at the **end** of the workbook -
your own sheets are never changed or reordered:

- **Cleaning Summary** - file name, date, rows before/after, what each step
  did, a per-column before/after quality table, totals by change type, and a
  color legend.
- **Change Log** - one filterable row per changed cell (Row, Column, Old
  value, New value, Reason), plus the flagged cells (reason "Flagged (not
  changed)"). Capped at 50,000 rows; the summary says so if that happens. Cell
  comments are capped at 20,000 (the colors are not).
- **Removed Rows** - the full original content of every removed row (only if
  rows were removed).

**CSV uploads:** the plain CSV download stays as it was, and a second button,
**Download as Excel (with highlights)**, gives the annotated workbook (CSV
files can't store colors or comments).

## Multi-sheet Excel files

If you upload an `.xlsx` file with more than one sheet, you can choose to
clean **all sheets** or just **one specific sheet**. Either way, every sheet
from the original file is kept in the downloaded file under its original
name — a sheet you don't select for cleaning is simply left exactly as it
was, never dropped.

## Cleaning summary report

After clicking "Clean File", the page shows four summary cards (rows,
placeholders removed, cells filled, missing cells before → after) and three
tabs:

- **Preview** - the cleaned data with changed cells colored exactly as in the
  Excel file (toggle "Show only rows with changes").
- **Data Quality** - the before/after table for every column.
- **Changes** - what each step did (duplicates, empty rows, headers renamed ...)
  and a filterable list of every changed cell.

The same summary can be downloaded as a `.txt` report alongside the cleaned file.

## AI-suggested cleaning rules (optional)

Beyond the fixed steps, you can ask an AI to suggest extra, more specific
rules based on what it sees in your column names and a few sample values:
normalizing phone numbers to a standard international format, making
category text consistently cased, mapping variants to a standard value,
parsing dates into one format, or turning currency text ("$1,234.50") into
plain numbers.

The five rule types are `normalize_phone`, `unify_casing`, `parse_date`,
`strip_currency_to_number` and **`map_values`**. `map_values` maps specific
values to one standard value, ignoring case - for example gender
`{"m": "Male", "male": "Male", "f": "Female", "female": "Female"}` turns
`m`, `M`, `male` and `MALE` into `Male`. Values that aren't in the map are left
unchanged and counted ("not in the map"), and every key in the map must really
exist in the column (otherwise the rule is rejected with the missing keys
listed). The AI is told to prefer `map_values` over `unify_casing` when a
column has abbreviations or variants, not just casing differences. For a
category column (see Privacy below) the AI is shown the *complete* list of
distinct values with their counts, so rare variants such as `m` or `MALE` are
visible and can be mapped; for other columns it only sees 8 random samples, so
it can only map the variants that appear there. With "Mask sample values" on,
the AI cannot suggest `map_values` at all (it never sees the real values).

**Privacy:** the AI never receives your full file. For each column it gets the
name, data type, how many values are present/missing, and then **one of two
things**:

- **up to 8 random sample values** (the default), or
- for a **text column with at most 30 distinct values** (gender, order status,
  payment method, city ...), the **complete list of those distinct values with
  how many times each occurs**, so rare variants are visible. This is never done
  for anything that looks like an identifier or personal data - columns named
  like an ID, code, phone, name, email, address or date of birth, columns whose
  values look like phone numbers or email addresses, long free text, or columns
  where nearly every value is different. Those only ever get the 8 samples.

After suggestions come back, the AI panel tells you which columns had their
full value list sent. You can also tick "Mask sample values" to
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

**Keep the AI's suggestions:** as soon as suggestions come back - before you
tick or apply anything - a **Download AI suggestions (.json)** button saves
*all* of them (ticked or not) in the same recipe format, with every rule's
`approved` flag set to `false`, plus the provider, model, locale, file name and
date. Load that file later under "Load a saved recipe" and you get the same
review screen with **nothing pre-ticked** - handy for reviewing on another day
without paying for another AI call.

**Local safety checks on suggestions** (`rule_checks.py`, no AI involved): before a suggested
rule is shown, the whole column is checked on your machine.
- *Date order:* if a numeric date column has values like `25/12/2024` (first part above 12) and none
  with a second part above 12, it is day-first - and the reverse means month-first. A `parse_date`
  rule that says otherwise is corrected, with a note under the rule. If the data cannot decide, the
  locale default is used and the note says so.
- *Loaded recipes and saved AI-suggestion files:* the date-order check runs on them too, but a loaded
  rule is never changed silently. If the data contradicts the saved order, the rule is unticked and a
  warning offers a one-click "Use the corrected date order" fix; otherwise it is kept as saved, with a note.
- *Casing:* a category column holding the same word in different capitalisations (`delivered` /
  `Delivered`) gets a `unify_casing` suggestion marked "Detected locally" - unless a `map_values` or
  `unify_casing` rule already covers it. IDs, names, emails and phone columns are never suggested.
- *Preview:* every suggested rule shows up to 3 before -> after examples and how many cells it
  would convert, leave as they are, or fail - computed on a copy, so nothing changes until you apply.

**Recipes:** once you've approved a set of rules, you can save them as a
reusable `.json` recipe file - the **Download Recipe (.json)** button appears
in the AI panel right after "Apply Approved Rules" succeeds (and a copy labelled
**Download same recipe again (.json)** at the bottom of the page). Loading a recipe later skips the AI call entirely and goes
straight to the same approve/reject review screen, pre-checked - so reusing a
recipe on a new (similarly-shaped) file is fast but still never fully silent.

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
data types, null counts and, per column, either up to 8 random sample values
or (for small categories) the full list of distinct values with counts, all
optionally masked - and both answers pass through the same validation, so a malformed
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
3. Tick/untick whichever of the cleaning steps you want to run, then click
   **"Clean File"**.
4. (Optional) Pick a country/locale, then click **"Suggest Cleaning Rules"**
   - or load a previously saved recipe instead. Review each suggested rule
   and tick the ones you approve, then click **"Apply Approved Rules"**.
5. Review the cards and the Preview / Data Quality / Changes tabs, then click
   **"Download Cleaned File"** (for an Excel upload this is the highlighted
   workbook; for a CSV upload also try **"Download as Excel (with
   highlights)"**), **"Download Cleaning Report (.txt)"**, and/or **"Download
   Recipe (.json)"** to save your approved rules for reuse next time.

## Project structure

```
app.py          - the page itself (UI only)
cleaning.py     - the deterministic cleaning steps (headers, trim, placeholders, numbers, duplicates, empty, fills)
quality.py      - shared helpers: placeholder matching, number parsing, phone detection, before/after profile
derive.py       - finding relationships between columns and filling gaps only when exactly derivable
changes.py      - cell-level change tracking (row, column, old, new, reason), in memory only
xlsx_export.py  - the highlighted Excel workbook: colors, comments, Cleaning Summary and Change Log sheets
io_utils.py     - loading files / building the plain CSV / Excel bytes
report.py       - the on-page cards, color-coded preview, change table, quality tables and the .txt report
locales.py      - country/locale defaults (phone region, date, currency)
rule_engine.py  - the 5 AI-suggestable rule types (incl. map_values), executed locally with pandas
flags.py        - deterministic warnings about implausible numbers (flagged, never changed)
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


---

## Mode B: Build my budget sheet

Start the app with `streamlit run main.py`. The start screen offers two modes: **Clean my Excel/CSV**
(the cleaner described above, unchanged) and **Build my budget sheet**.

Like the cleaner, the budget mode is *file in, better file out* - nothing is stored on the server and
no AI is involved (categories come from an editable keyword map).

**You can start from:**
1. a messy personal budget workbook (a daily expense log: dates only on the first row of each day, empty
   categories, hardcoded totals ...) - the app finds the header row and columns, fills dates down (flagged),
   drops total rows (listed, restorable), guesses each row's Type and Category, and lists the descriptions
   it could not place so you can assign them (and teach the keyword map);
2. **last month's workbook made by this app** - it is recognised by its sheets and settings; the closing balance
   becomes the new opening balance, and categories, budgets, payment methods and the keyword map are kept; the
   Transactions sheet starts empty;
3. nothing (a blank start).

**The workbook you get** (`How to use`, `Dashboard`, `Transactions`, `Settings`, `Keywords`, and `Import Notes` for imports):
- *Transactions*: Date, Description, Type, Category, Amount, Payment method - with dropdowns for Type, Category and
  Payment method, room for 1,000 rows, rows outside the month greyed.
- *Dashboard*: only formulas (SUMIFS ...): income, expenses, loan payments, loans received, **total saved this month**
  (a separate box), net balance, opening and closing balance, spending vs budget per category (red when over), top 3
  categories, daily totals, data checks, and three native charts.
- *Types*: Income, Expense, Loan received, Loan payment, Saving, **Card bill payment**.

**Credit cards, without double counting:** a purchase with a credit card is an **Expense** on the day it happens.
Paying the card bill later is a **Card bill payment**: it is shown on its own line and is *not* part of expenses or of the
net balance. Use **Loan payment** only for real loans / EMIs.

Net balance = Income + Loan received - Expense - Loan payment - Saving. Closing balance = opening balance + net balance.

The code is in the `budget/` package (`importer`, `categorize`, `workbook`, `carryover`, `prepare`, `model`) and
`budget_app.py`; `app.py` (the cleaner) is untouched. The currency symbol is baked into the number formats when the
file is made.

**Tests:** the Dashboard formulas are recalculated without Excel with the `formulas` package
(`pip install -r requirements-dev.txt`) and compared with plain pandas.


### Upload and go (budget mode)

Upload a workbook and the app works out the rest, then shows one **"What I found"** card: currency, month, opening
balance, income, expenses, loan / card payments, categories and budgets - each with where it came from and how sure
the app is - plus a short **Please check** list of only the uncertain things. Click **Looks good, build my sheet**, or
**Edit details** to open the usual sections (everything stays editable).

- *Currency*: the amount header ("Amount (AED)"), symbols in cells, sheet titles, else the locale default.
- *Month*: the most common month in the dates. *Opening balance*: a labelled number such as "May 2026 Closing Balance"
  on another sheet (this month's own "estimated closing" is rejected; other candidates are listed).
- *Budgets*: a Category | Budget table on another sheet (the one that adds up to its own total wins; names match
  ignoring emojis, capitals and punctuation).
- *Income list* and *loan / card payment log* sheets are imported. Payments from a loan / card log are recorded as
  **Loan payment**; the Please-check list always offers a one-click switch to **Card bill payment**
  (Loan payment = paying back a loan or instalment plan; Card bill payment = paying your credit card bill for purchases
  already counted as expenses).
- *Categories*: the file's own (merged into ours when obvious, e.g. "Food/Grocery" -> "Food"), then the keyword map, then a
  typo-tolerant match. The rest can go to the AI with **one click on the card** (off by default): only the distinct
  description texts and your category names are sent - never amounts, dates or other columns. AI categories are marked
  "AI suggested" in the review table and in Import Notes. With no API key the step is skipped.
- A sanity check compares your file's own estimated closing balance with the one calculated here (information only).

**Doughnut chart:** the "Spending by category" chart shows only the category name and its percentage, with the legend
on the right. Categories are ranked by spending; a slice under 3% of the total gets no label (the share is the
*Doughnut chart: hide slices under* setting on the Settings sheet) and is added to one "Small categories (under 3%)" slice. The chart
reads its own small formula table in columns AA:AC of the Dashboard (not hidden: Excel does not draw data from hidden
columns).


---

## The start screen and "Check my file"

`streamlit run main.py` opens a start screen with five tasks: **Clean my file**, **Check my file**, **Match two files**,
**Build my budget** and **Check every month**. The one that is not built yet (*Check every
month*) is greyed out with "Coming soon"; set `SHOW_UNFINISHED_FEATURES = False` in `config.py` to hide them (for example before
sharing the public link).

**Check my file** (Excel Doctor) reads a workbook and reports, in plain language:
- what each sheet seems to do (with the evidence and how sure it is);
- **error values and broken references** (`#REF!`, `#DIV/0!` ..., sheets that no longer exist, links to other files);
- **typed totals** that are not formulas, and numbers typed over a column of formulas;
- **ranges that stop short** of the data (with the money left out), siblings that do something different, subtotals counted twice;
- **SUMIF / COUNTIF criteria that match nothing**, with the closest value that does exist (for example `🍔 Food` vs `Food/Grocery`);
- **figures that disagree**: a typed "total expenses" vs what the transaction list adds up to, a summary total that is lower than its
  rows, the same label with different numbers on two sheets, budgets that differ between two tables;
- small things: numbers or dates stored as text, hidden data, results that were not recalculated.

The report is ordered by importance. You also get a **highlighted copy** of the workbook: problem cells coloured (red / yellow / blue),
a note on each with the reason, and a "Doctor report" sheet at the end with links to the cells. Your numbers are never changed: the copy
is made by patching the .xlsx package directly, so charts, images and everything else stay byte-identical, and the result is checked
(every value and formula equal, every other part identical) before it is offered. If the check fails you get the report only, plus the
text download.

Only SUM, SUMIF, SUMIFS, COUNTIF and COUNTIFS are recalculated (by a small built-in evaluator); other formulas are still checked for
errors, references and typed-over cells and are listed as "not recalculated". The Doctor does not support old `.xls` files or
password-protected workbooks.

Code: `toolkit/` (shared report shapes, ranking, workbook reader, highlighter, screen parts) and `doctor/` (formula reader, evaluator,
checks, report); the page is `doctor_app.py`.

## Near-duplicates (inside "Clean my file")

Finds rows that are probably the same person or record even though they are not identical ("Ali  KHAN" / "Ali Khan", `0300-1234567` / `+92 300 1234567`, "Mohamed" / "Muhammad"). It only suggests: nothing is removed until you choose.

1. Clean your file, then press **Look for near-duplicates** (section "Near-duplicates", below the AI rules).
2. The **What I found** card says what kind of file it is (a list of people or transactions), which columns were compared and why, and which were ignored (dates, amounts, quantities, statuses).
3. Groups are sorted by confidence (High, then Maybe) with the rows side by side; differences are yellow, blanks a merge would fill are purple. For each group choose **Keep all** (default), **Keep one** or **Merge (fill blanks)**, and which row to keep (the most complete is pre-selected).
4. **Accept all high-confidence groups** sets every High group to Merge. A one-line summary ("3 rows will be removed, 1 blank cells filled") is shown before **Apply**.

What it never does: a name alone is never High; two different strong identifiers (email, phone, id) rule a pair out; a value that is already there is never overwritten when merging.

Output: removed rows go to the **Removed Rows** sheet with the reason, e.g. "Near-duplicate of row 12 (name almost same, phone same)". Cells filled by a merge are purple in the Excel file and listed in the Change Log. Exact duplicates are handled by the existing step as before.

**Edit details**: file type (auto / people / transactions), how sure a match must be, the phone country, the gmail dot rule, the role of every column, and the list of names that count as the same name (`dedupe/name_variants.json`, including Mohamed/Mohammed/Muhammad, Abdullah/Abdallah, Fatima/Fatimah, Yousef/Yusuf, Khalid/Khaled) - edit it in the box, one group per line.

Code: `dedupe/` (`columns` roles, `normalize`, `prepare`, `blocking`, `score`, `groups`, `analyze`, `apply`), `dedupe_panel.py` (the page section). Tests: `tests/test_dedupe_*.py` (ground-truth precision/recall on a synthetic customer list, a 20,000-row timing test, the app flow).

**Combining totals and dates when you merge (people lists only).** Columns that summarise a person's activity are combined instead of keeping only the kept row's value: totals, counts, points and balances are added up (Ali Khan: Total Spent 4500 + 1200 = 5700), a "Last ... date" takes the latest and a "First ... / Joined / Created" date the earliest. The rules are suggested from the header and the type, shown per group ("Total Spent 4500 + 1200 = 5700; Last Order Date -> 2026-10-15") and editable under "How to combine columns when merging". Every other column keeps the kept row's value and only its blanks are filled. Combined cells are orange in the Excel file and in the Change Log as "Combined from near-duplicates". Never used for a transaction list.

## Match two files (reconciliation)

Upload two files - a bank statement and your own records, stock and sales, payroll and attendance - and see what matches and what does not.
Your files are never changed; the result is a new Excel workbook.

1. **Upload file A and file B** (CSV or .xlsx; pick the sheet when there are several). Title rows above the header are skipped automatically.
2. **The columns are found for you**, from the headers and the values: the date, the amount (one signed column, or a debit and a credit
   column), the reference / id and the description. The "What I found" card shows each choice with where it came from and how sure it is
   (sure / probably / guess). Change any of them under **Edit details**, together with the days apart allowed (default 3), the amount
   tolerance for a "maybe" (default 0.5%), the date order and the way money in / out is written.
3. **Rows are matched in rounds, strongest evidence first, and each match says why:**
   - *Exact*: the same reference (a different amount is **not** a match - it goes to Mismatches), or the same amount + the same date + a similar description.
   - *Close*: the same amount, dates within N days; the most similar description wins.
   - *Maybe*: the amount within the tolerance (rounding, a small fee) and dates within N days. Always a "maybe": tick the ones you confirm.
   - A row is matched **at most once** (no splits, no many-to-one). What is left is listed as *Only in A* / *Only in B*.
4. **Signs**: if one file writes money out as a minus and the other with a debit column, that is normalised before matching. If the two files
   use opposite signs, or one has no minus signs at all, that is detected and shown on the card (and can be set by hand).
5. **Result**: counts and money for matched, to confirm, only in A, only in B and amount mismatches; a "Please check" list with the biggest
   amounts first; and one Excel file with the sheets *Summary, Matched* (A and B side by side with the reason), *Maybe, Only in A, Only in B,
   Mismatches*, one colour per group.

Code: `reconcile/` (`columns` detection, `prepare`, `match`, `report`, `output`, `pipeline`, `views`) and `match_app.py` (the page). Tests:
`tests/test_reconcile_*.py` (a synthetic bank statement and ledger with a ground-truth table, other header styles and sign conventions, the Excel
result, a 10,000 x 10,000-row timing test and the page flow).

## Bank statement to budget (inside "Build my budget")

Upload a bank statement (CSV or Excel) in **Build my budget** and you get the same "What I found" card, "Please check" list and generated
workbook as for any other upload - with these differences:

- **It is recognised from its running balance.** A sheet with a date, a description, a money column (or a debit and a credit column) and a
  balance column whose numbers add up row by row ("balance after = balance before + amount") is a statement. The columns are found by the same
  detection as "Match two files"; title lines above the header and blank lines inside are fine. The balance column is never used as an amount.
- **Signs and order come from the balance, not from guesses.** Newest-first statements are put in date order. If the balance proves that the file
  writes money out as plus (or the debit and credit columns the other way round), the amounts are turned around - on a copy; your file is untouched.
- **Credits become Income, debits become Expenses**, then the usual order applies: the file's own category, the keyword map, a typo-tolerant match,
  and (only if you press the button) the AI for what is left. Type keywords still apply (salary, loan payment, saving ...).
- **Card bills and transfers between your own accounts are not spending.** "Credit card payment", "Visa bill", "Mastercard bill", "Transfer to own account",
  "Internal transfer" ... become **Card bill payment** (shown on their own Dashboard line, never in expenses or income). A transfer the app cannot place
  ("IBFT transfer Ali Khan") stays an expense and is listed in Please check, so you decide.
- **Opening balance and month.** The month is the most common month of the dates; the opening balance is the statement's balance *before the first
  transaction of that month* (high confidence). The statement's last balance of the month is its closing balance.
- **Sanity check.** The budget's closing balance must equal the statement's. Because the Dashboard does not count Card bill payment rows, those rows are
  added back before comparing and the card says so ("includes -4,300.00 from 4 card bill payment / own-account transfer row(s)"). Any other difference is
  shown on the card, in Please check and in the workbook's Import Notes - usually a missing, left-out or mistyped row.

A statement without a balance column still works through the normal flow (credits = Income, debits = Expenses, keyword Types), only without the balance-based
opening balance and closing-balance check. Code: `budget/statement.py`; tests: `tests/test_statement_budget.py`, `tests/test_statement_app.py`.
