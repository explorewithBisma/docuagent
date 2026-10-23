"""
locales.py
-----------
A small lookup table of per-country defaults used by the AI-suggested rules:
- which region code `phonenumbers` should assume for phone numbers
- whether dates are usually written day-first (DD/MM) or month-first (MM/DD)
- the currency symbol most likely to show up in money columns

These are only *defaults* passed to the AI as context. The AI can still
suggest different params for a specific column if the sample values clearly
show something else (e.g. a "+1" phone number in an otherwise Pakistani file).
"""

LOCALES = {
    "PK": {"label": "Pakistan", "phone_region": "PK", "day_first": True, "currency_symbol": "Rs"},
    "US": {"label": "United States", "phone_region": "US", "day_first": False, "currency_symbol": "$"},
    "GB": {"label": "United Kingdom", "phone_region": "GB", "day_first": True, "currency_symbol": "£"},
    "IN": {"label": "India", "phone_region": "IN", "day_first": True, "currency_symbol": "₹"},
    "AE": {"label": "United Arab Emirates", "phone_region": "AE", "day_first": True, "currency_symbol": "AED"},
}

DEFAULT_LOCALE = "PK"
