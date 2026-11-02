from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The dark green theme: the Streamlit theme config and the stylesheet load, the palette is the specified one, text is readable (WCAG AA), the CSS uses
only stable hooks, the start screen and the pills have the specified shape, and the Excel colours did not change."""
import re
import sys
import tomllib

sys.path.insert(0, str(ROOT))

import streamlit.config as streamlit_config
from streamlit.testing.v1 import AppTest

import changes
import theme
from toolkit import ui

PALETTE = {"bg": "#0B0F0D", "card": "#121A16", "card-border": "#1F2E27", "green": "#0F5132", "emerald": "#10B981", "text": "#E8F0EC", "muted": "#8FA69B",
           "warning": "#F59E0B", "error": "#EF4444"}
CSS = theme.css()
VARIABLES = dict(re.findall(r"--([a-z\-]+):\s*(#[0-9A-Fa-f]{6})", CSS))


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


print("TEST: the Streamlit theme config loads - dark base, the specified palette, Inter / Space Grotesk, rounded buttons; every key is one Streamlit knows")
config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
assert config["base"] == "dark" and config["primaryColor"] == PALETTE["emerald"] and config["backgroundColor"] == PALETTE["bg"]
assert config["secondaryBackgroundColor"] == PALETTE["card"] and config["textColor"] == PALETTE["text"] and config["borderColor"] == PALETTE["card-border"]
assert config["font"].startswith("Inter:https://fonts.googleapis.com/") and config["headingFont"].startswith("Space Grotesk:https://fonts.googleapis.com/")
assert config["buttonRadius"] == "full" and config["baseRadius"] == "1rem"
known = {name.split(".")[-1] for name in streamlit_config.get_options_for_section("theme")}
unknown = set(config) - known
print("   keys:", sorted(config))
assert not unknown, f"Streamlit does not know these theme options: {unknown}"
print("PASSED")

print("TEST: the stylesheet has exactly the specified palette as variables, in one place")
for name, value in PALETTE.items():
    assert VARIABLES[name].upper() == value.upper(), (name, VARIABLES[name], value)
assert "Excel" in CSS.split("*/")[0] and len(re.findall(r"^:root \{", CSS, flags=re.M)) == 1 and "border-radius: var(--radius)" in CSS and "--radius: 16px" in CSS
print("PASSED")

print("TEST: text is readable - every text/background pair the theme uses passes WCAG AA (4.5:1)")
pairs = [("text", "bg"), ("text", "card"), ("muted", "bg"), ("muted", "card"), ("emerald", "bg"), ("emerald", "card"), ("on-emerald", "emerald"), ("text", "green"),
         ("pill-red-text", "pill-red-bg"), ("pill-amber-text", "pill-amber-bg"), ("pill-blue-text", "pill-blue-bg"), ("pill-green-text", "pill-green-bg"),
         ("warning", "card"), ("error", "card")]
ratios = {pair: contrast(VARIABLES[pair[0]], VARIABLES[pair[1]]) for pair in pairs}
print("  ", {f"{a}/{b}": round(r, 1) for (a, b), r in ratios.items()})
assert all(r >= 4.5 for r in ratios.values()), {p: r for p, r in ratios.items() if r < 4.5}
assert re.search(r'stCaptionContainer"\] \{[^}]*opacity: 1', CSS), "captions must not stay faded (muted text at 60% opacity fails AA)"
assert contrast("#FFFFFF", VARIABLES["emerald"]) < 4.5                                   # why the button text is dark: white on emerald would fail
print("PASSED")

print("TEST: the CSS uses stable hooks only - no generated class names, only documented data-testid values, .stApp and .st-key-*")
RULES = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)                                             # (the comments may talk about generated names)
assert not re.search(r"\.css-[0-9a-z]+|emotion-cache|\.e[0-9][0-9a-z]{4,}", RULES)
testids = set(re.findall(r'data-testid="([^"]+)"', CSS))
print("   data-testid values:", sorted(testids))
allowed = {"stHeader", "stAppDeployButton", "stMainBlockContainer", "stVerticalBlockBorderWrapper", "stExpander", "stMetric", "stAlert", "stFileUploaderDropzone", "stDataFrame",
           "stDataEditor", "stCaptionContainer", "stBaseButton-primary", "stBaseButton-secondary"}
assert testids <= allowed, testids - allowed
own = {"topbar", "dot", "sub", "hero", "eyebrow", "hl", "pill", "steps", "step", "n"}
classes = set(re.findall(r"(?<![\w-])\.([a-zA-Z][\w-]*)", RULES))
foreign = {c for c in classes if c not in own and not c.startswith(("pill-", "st-key-", "stApp"))}
assert not foreign, foreign
print("PASSED")

print("TEST: it works on a phone - a small-screen rule set, a fluid headline, a wrapping steps grid, a back button that moves in")
assert "@media (max-width: 640px)" in CSS and "clamp(" in CSS and "repeat(auto-fit, minmax(" in CSS and ".st-key-back_to_start" in CSS.split("@media")[1]
print("PASSED")

print("TEST: the stylesheet is injected once per run by main.py (with the top bar), and the start screen has the specified parts")
at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
at.run()
assert not at.exception, [e.value for e in at.exception]
styles = [m.value for m in at.markdown if m.value.lstrip().startswith("<style>")]
assert len(styles) == 1 and "--emerald: #10B981" in styles[0] and len(styles[0]) > 3000
assert sum(1 for m in at.markdown if 'class="topbar"' in m.value) == 1
hero = next(m.value for m in at.markdown if 'class="hero"' in m.value)
assert re.search(r"<h1>Fix your spreadsheets .*faster and <span class=\"hl\">safer</span></h1>", hero) and "Clean, check, match and budget" in hero
assert [b.label for b in at.button if b.key.startswith("hero_")] == ["Clean a file", "Match two files"]
tools = [b for b in at.button if b.key.startswith("start_")]
assert len(tools) == 5 and {b.label for b in tools} == {"Open"} and [s.value for s in at.subheader][0] == "🧹 Clean my file"
steps = next(m.value for m in at.markdown if 'class="steps"' in m.value)
assert re.findall(r"<b>([A-Za-z]+)</b>", steps) == ["Upload", "Check", "Review", "Download"] and "4 simple steps" in steps
assert any("Files are processed in memory and not stored" in c.value for c in at.caption)
source = (ROOT / "main.py").read_text(encoding="utf-8")
assert 'layout="wide"' in source and 'key=f"tool_{key}"' in source and "GRID = [(2, 1), (1, 1, 1)]" in source
print("PASSED")

print("TEST: every tool page has 'Back to start' when it runs inside the app (the cleaner included), and the cleaner alone does not")
for key in ("start_cleaner", "start_check", "start_match", "start_budget", "start_monthly"):
    page = AppTest.from_file(str(ROOT / "main.py"), default_timeout=120)
    page.run()
    next(b for b in page.button if b.key == key).click()
    page.run()
    assert not page.exception, (key, [e.value for e in page.exception])
    assert any(b.key == "back_to_start" for b in page.button), key
alone = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
alone.run()
assert not any(b.key == "back_to_start" for b in alone.button)
print("PASSED")

print("TEST: pills - importance High / Medium / Low and confidence sure / probably / guess, with the colour classes the CSS styles")
assert ui.severity_pill("high") == '<span class="pill pill-high">High</span>' and ui.severity_pill("medium").count("pill-medium") == 1 and "pill-low" in ui.severity_pill("low")
assert "pill-sure" in ui.confidence_pill("high") and "🟢 sure" in ui.confidence_pill("high")
assert "pill-probably" in ui.confidence_pill("medium") and "pill-guess" in ui.confidence_pill("low") and ui.confidence_pill("nonsense") == ""
assert theme.pill("a<b", "high") == '<span class="pill pill-high">a&lt;b</span>'                                    # text is escaped
for kind in ("high", "medium", "low", "sure", "probably", "guess"):
    assert f".pill-{kind}" in CSS
print("PASSED")

print("TEST: the Excel colours did not change (light, print-friendly, independent of the app theme)")
assert changes.REASON_COLORS == {changes.CALCULATED: "C6E0B4", changes.LOOKUP: "FFE699", changes.PLACEHOLDER: "D9D9D9", changes.AI_RULE: "BDD7EE", changes.MERGED: "E4DFEC",
                                 changes.COMBINED: "F8CBAD"}
assert (changes.FLAG_BORDER_COLOR, changes.FLAG_FILL_COLOR) == ("E26B0A", "F8D7DA")
from reconcile import output as reconcile_output
from toolkit import highlight
assert reconcile_output.COLORS == {"exact": "C6E0B4", "close": "E2EFDA", "maybe": "FFE699", "mismatch": "FFC7CE", "only_a": "F8CBAD", "only_b": "BDD7EE", "confirmed": "C6E0B4"}
assert highlight.SEVERITY_FILL == {"high": "FFFFC7CE", "medium": "FFFFEB9C", "low": "FFDDEBF7"}
for module in ("xlsx_export.py", "reconcile/output.py", "toolkit/highlight.py", "datarules/output.py", "budget/workbook.py"):
    assert "import theme" not in (ROOT / module).read_text(encoding="utf-8"), module                      # the workbooks never see the theme
print("PASSED")
