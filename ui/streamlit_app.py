"""DocuAgent Streamlit page. It only talks to the FastAPI backend (API_URL); there is no business logic here.

Run:  streamlit run ui/streamlit_app.py
"""

import os
from typing import Any, Optional

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
LOW_CONFIDENCE = 0.6
TEXT_FIELDS = [("vendor_name", "Vendor name"), ("vendor_tax_id", "Vendor tax ID"), ("invoice_number", "Invoice number"), ("invoice_date", "Invoice date (YYYY-MM-DD)"),
               ("due_date", "Due date (YYYY-MM-DD)"), ("currency", "Currency (e.g. USD)")]
MONEY_FIELDS = [("subtotal", "Subtotal"), ("tax", "Tax"), ("total", "Total")]
LINE_COLUMNS = ["description", "quantity", "unit_price", "amount"]


# ---------------------------------------------------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------------------------------------------------
def call_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    return httpx.request(method, f"{API_URL}{path}", timeout=180, **kwargs)


def error_text(response: httpx.Response) -> str:
    """A readable message from an error response (the backend sends a string, a {message, errors} object or a list of validation errors)."""
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(detail, dict):
        extra = detail.get("errors") or [i["message"] for i in detail.get("issues", [])]
        return detail.get("message", "Something went wrong.") + ("\n\n" + "\n".join(f"- {line}" for line in extra) if extra else "")
    if isinstance(detail, list):
        return "; ".join(f"{'.'.join(map(str, e.get('loc', [])))}: {e.get('msg')}" for e in detail)
    return str(detail)


def backend_is_up() -> bool:
    try:
        return call_api("GET", "/health").status_code == 200
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------
def parse_money(text: str, label: str) -> Optional[float]:
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{label} must be a number (you typed '{text}').")


def flagged_fields(doc: dict) -> dict[str, str]:
    """{field: mark} so the form can show which fields have issues (❌ error, ⚠ warning) - the worst one wins."""
    marks: dict[str, str] = {}
    for issue in doc["issues"]:
        mark = "❌" if issue["severity"] == "error" else "⚠"
        if marks.get(issue["field"]) != "❌":
            marks[issue["field"]] = mark
    return marks


def label_for(field: str, label: str, doc: dict, marks: dict[str, str]) -> str:
    mark = marks.get(field) or ("⚠" if field in (doc["data"].get("low_confidence_fields") or []) else "")
    return f"{mark} {label}".strip()


def show_issues_and_duplicate(doc: dict) -> None:
    duplicate = doc.get("duplicate")
    if duplicate:
        (st.error if duplicate["kind"] == "exact" else st.warning)(
            f"{'DUPLICATE' if duplicate['kind'] == 'exact' else 'Possible duplicate'}: {duplicate['reason']}")
    for issue in doc["issues"]:
        (st.error if issue["severity"] == "error" else st.warning)(f"**{issue['field']}** - {issue['message']}")
    if not doc["issues"] and not duplicate:
        st.success("No problems found.")


def collect_form(doc: dict) -> dict[str, Any]:
    """The values currently in the form as a PATCH body. Raises ValueError for a money field that is not a number."""
    key = doc["id"]
    body: dict[str, Any] = {"doc_type": st.session_state.get(f"doc_type_{key}") or None}
    for field, _ in TEXT_FIELDS:
        body[field] = (st.session_state.get(f"{field}_{key}") or "").strip() or None
    for field, label in MONEY_FIELDS:
        body[field] = parse_money(st.session_state.get(f"{field}_{key}", ""), label)
    lines = st.session_state.get(f"lines_{key}")
    body["line_items"] = [{c: (None if pd.isna(row[c]) else row[c]) for c in LINE_COLUMNS} for _, row in lines.iterrows()
                          if not all(pd.isna(row[c]) or row[c] == "" for c in LINE_COLUMNS)] if isinstance(lines, pd.DataFrame) else []
    return body


# ---------------------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------------------
st.set_page_config(page_title="DocuAgent", page_icon="📄", layout="wide")
st.title("📄 DocuAgent")
st.caption("Upload an invoice or receipt. I read it, check it, flag problems and duplicates, and save it only after you confirm.")

if not backend_is_up():
    st.error(f"The backend is not running at {API_URL}. Start it with:  `uvicorn backend.main:app --reload`")
    st.stop()

review_tab, saved_tab = st.tabs(["Upload & review", "Saved documents"])

with review_tab:
    uploaded = st.file_uploader("Invoice or receipt (PDF, PNG or JPG, up to 10 MB)", type=["pdf", "png", "jpg", "jpeg"])
    if uploaded is not None and st.button("Read this document", type="primary"):
        with st.spinner("Reading the document ..."):
            try:
                response = call_api("POST", "/documents/upload", files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")})
            except httpx.HTTPError as error:
                st.session_state.doc, st.session_state.notice = None, ("error", f"Could not reach the backend: {error}")
            else:
                if response.status_code == 200:
                    body = response.json()
                    st.session_state.doc = body["document"]
                    if body["file_duplicate"]:
                        st.session_state.notice = ("info", body["message"])
                    else:
                        st.session_state.notice = ("warning", body["message"]) if body["message"] else None
                else:
                    st.session_state.doc = None
                    note = error_text(response)
                    if response.status_code in (502, 503):
                        note = "The AI could not read the document right now.\n\n" + note
                    st.session_state.notice = ("error", note)

    notice = st.session_state.get("notice")
    if notice:
        getattr(st, notice[0])(notice[1])

    doc = st.session_state.get("doc")
    if doc:
        key, locked = doc["id"], doc["status"] == "confirmed"
        st.subheader(f"Document #{key} - {doc['status']}")
        st.caption(f"{doc['filename']} · read by {doc.get('provider') or 'unknown'} · [open the original file]({API_URL}/documents/{key}/file)")
        show_issues_and_duplicate(doc)
        marks, data = flagged_fields(doc), doc["data"]

        st.selectbox(label_for("doc_type", "Document type", doc, marks), ["", "invoice", "receipt"], key=f"doc_type_{key}", disabled=locked,
                     index=["", "invoice", "receipt"].index(data.get("doc_type") or ""))
        left, right = st.columns(2)
        for number, (field, label) in enumerate(TEXT_FIELDS):
            (left if number % 2 == 0 else right).text_input(label_for(field, label, doc, marks), value=data.get(field) or "", key=f"{field}_{key}", disabled=locked)
        money = st.columns(3)
        for column, (field, label) in zip(money, MONEY_FIELDS):
            value = data.get(field)
            column.text_input(label_for(field, label, doc, marks), value="" if value is None else f"{value:g}", key=f"{field}_{key}", disabled=locked)
        st.markdown(f"**{label_for('line_items', 'Line items', doc, marks)}**")
        lines = pd.DataFrame(data.get("line_items") or [], columns=LINE_COLUMNS)
        st.session_state[f"lines_{key}"] = st.data_editor(lines, num_rows="dynamic", key=f"lines_editor_{key}", disabled=locked, hide_index=True)

        if not locked:
            errors = [i for i in doc["issues"] if i["severity"] == "error"]
            exact_duplicate = bool(doc.get("duplicate")) and doc["duplicate"]["kind"] == "exact"
            override = st.checkbox("Save anyway, even though the errors above are still there", key=f"override_{key}") if errors else False
            save_col, confirm_col, _ = st.columns([1, 1, 3])
            try:
                if save_col.button("Save changes (check again)"):
                    response = call_api("PATCH", f"/documents/{key}", json=collect_form(doc))
                    if response.status_code == 200:
                        st.session_state.doc = response.json()
                        st.rerun()
                    st.error(error_text(response))
                if confirm_col.button("Confirm & Save", type="primary", disabled=exact_duplicate or (bool(errors) and not override)):
                    patched = call_api("PATCH", f"/documents/{key}", json=collect_form(doc))          # keep the user's edits
                    if patched.status_code != 200:
                        st.error(error_text(patched))
                    else:
                        response = call_api("POST", f"/documents/{key}/confirm", json={"override_issues": bool(override)})
                        if response.status_code == 200:
                            st.session_state.doc = response.json()
                            st.session_state.notice = ("success", f"Saved as document #{key}.")
                            st.rerun()
                        st.session_state.doc = patched.json()
                        st.error(error_text(response))
            except ValueError as error:
                st.error(str(error))
            if exact_duplicate:
                st.caption("Confirm is disabled: this document duplicates a saved one.")
            elif errors and not override:
                st.caption("Fix the errors and press 'Save changes', or tick 'Save anyway'.")

with saved_tab:
    status = st.selectbox("Show", ["all", "confirmed", "draft"])
    if st.button("Refresh"):
        st.rerun()
    response = call_api("GET", "/documents", params={} if status == "all" else {"status": status})
    if response.status_code != 200:
        st.error(error_text(response))
    else:
        rows = response.json()
        if rows:
            table = pd.DataFrame(rows)[["id", "status", "vendor_name", "invoice_number", "invoice_date", "currency", "total", "issue_count", "filename", "created_at"]]
            st.dataframe(table, hide_index=True, width="stretch")
        else:
            st.info("No documents yet.")
