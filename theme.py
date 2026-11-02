"""
theme.py
---------
The look of the app in one place: injects assets/theme.css once per run, draws the slim top bar, and holds the small HTML pieces (pills) that the pages share.
The colours come from .streamlit/config.toml (Streamlit's own theme) and from the variables at the top of assets/theme.css. Nothing here changes what a page does.
"""

from html import escape
from pathlib import Path

import streamlit as st

CSS_PATH = Path(__file__).with_name("assets") / "theme.css"


def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def inject() -> None:
    """Put the stylesheet on the page. Call it once per run, before the pages are drawn (main.py does)."""
    st.markdown(f"<style>\n{css()}\n</style>", unsafe_allow_html=True)


def top_bar() -> None:
    """The slim bar with the app name. (The "Back to start" button of each page is moved into its right end by the CSS.)"""
    st.markdown('<div class="topbar"><span class="dot"></span>Spreadsheet Helper<span class="sub">&nbsp;· files in, answers out</span></div>', unsafe_allow_html=True)


def pill(text: str, kind: str) -> str:
    """HTML for a coloured pill. kind: high | medium | low (importance) or sure | probably | guess (how sure the app is)."""
    return f'<span class="pill pill-{kind}">{escape(text)}</span>'
