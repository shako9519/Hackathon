from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from quarterly_automation.pipeline import (  # noqa: E402
    DEFAULT_RULES,
    REQUIRED_SOURCES,
    apply_reviews,
    process_sources,
)
from quarterly_automation.workbook import workbook_bytes  # noqa: E402


st.set_page_config(page_title="Quarterly Assets | Investment Statistics", page_icon="QS", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
    :root { --ink:#202824; --muted:#68736d; --paper:#f4f5ef; --line:#dce1d8; --green:#176a50; --lime:#d7ec89; --orange:#d57843; }
    html, body, [class*="css"] { font-family:'DM Sans',sans-serif; color:var(--ink); }
    .stApp { background: radial-gradient(ellipse at 92% 0%, #e5edcf 0, transparent 31%), linear-gradient(115deg,#f6f7f1 0%,#edf1e8 100%); }
    [data-testid="stHeader"] { background:transparent; }
    [data-testid="stSidebar"] { background:#202824; }
    [data-testid="stSidebar"] * { color:#edf1e8 !important; }
    .block-container { padding-top:2.2rem; max-width:1440px; }
    .eyebrow { color:var(--green); font:500 11px 'DM Mono',monospace; text-transform:uppercase; letter-spacing:1.3px; }
    .hero { display:flex; justify-content:space-between; align-items:flex-end; gap:24px; border-bottom:1px solid var(--line); padding:5px 0 22px; margin-bottom:20px; }
    .hero h1 { font:700 34px/1.1 'Manrope',sans-serif; letter-spacing:0; margin:7px 0 0; }
    .hero p { margin:7px 0 0; color:var(--muted); font-size:14px; }
    .hero-mark { font:500 12px 'DM Mono',monospace; color:var(--green); padding:9px 12px; border:1px solid #b9c9b8; background:#f7f9f2; }
    .source-row { border-bottom:1px solid var(--line); padding:12px 0 7px; }
    .source-label { font-weight:600; font-size:14px; margin-bottom:2px; }
    .source-hint { color:var(--muted); font-size:12px; }
    div[data-testid="stMetric"] { background:#fbfcf8; border:1px solid var(--line); border-top:3px solid var(--green); border-radius:3px; padding:14px 16px; }
    div[data-testid="stMetricLabel"] { color:var(--muted); font-size:12px; }
    div[data-testid="stMetricValue"] { font-family:'Manrope',sans-serif; }
    div[data-testid="stTabs"] button { font-weight:600; }
    div[data-testid="stDataFrame"] { border:1px solid var(--line); }
    .stButton>button, .stDownloadButton>button { border-radius:3px; font-weight:600; }
    .stButton>button[kind="primary"] { background:var(--green); border-color:var(--green); }
    .side-note { border-top:1px solid #58625b; margin-top:24px; padding-top:14px; color:#c4cdc5; font-size:12px; line-height:1.6; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_sample(role: str) -> pd.DataFrame | None:
    path = ROOT / "sample_data" / f"{role}.csv"
    return pd.read_csv(path, dtype=str) if path.exists() else None


def read_upload(upload) -> pd.DataFrame:
    if upload.name.lower().endswith(".csv"):
        return pd.read_csv(upload, dtype=str)
    return pd.read_excel(upload, dtype=str)


def read_rules() -> dict:
    path = ROOT / "config" / "rules.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else DEFAULT_RULES


def source_picker(role: str, label: str, hint: str, required: bool) -> pd.DataFrame | None:
    left, middle, right = st.columns([1.0, 1.5, 2.6], vertical_alignment="center")
    with left:
        st.markdown(f'<div class="source-label">{label}{" *" if required else ""}</div><div class="source-hint">{hint}</div>', unsafe_allow_html=True)
    with middle:
        upload = st.file_uploader("Choose a file", type=["xlsx", "xls", "csv"], key=f"upload_{role}", label_visibility="collapsed")
    with right:
        if upload is not None:
            try:
                frame = read_upload(upload)
                st.success(f"{len(frame):,} rows · {len(frame.columns)} columns", icon="✓")
                return frame
            except Exception as error:
                st.error(f"Could not read file: {error}")
                return None
        sample = load_sample(role)
        if sample is not None and st.toggle("Use demo data", value=True, key=f"sample_{role}"):
            st.caption(f"Demo source · {len(sample):,} rows")
            return sample.copy()
        st.caption("Waiting for file" if required else "Optional source")
        return None


def show_exception_review(master: pd.DataFrame, exceptions: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    reviewer = st.text_input("Reviewer name", key="reviewer_name", placeholder="Name for the audit record")
    if exceptions.empty:
        st.success("No records need review. The processed list is ready to export.")
        return master, reviewer
    exceptions = exceptions.copy()
    if "ReviewerOrigin" not in exceptions:
        exceptions["ReviewerOrigin"] = exceptions["Origin"].replace("Review", "")
    if "ReviewerClassification" not in exceptions:
        exceptions["ReviewerClassification"] = exceptions["Classification"]
    if "ReviewerNotes" not in exceptions:
        exceptions["ReviewerNotes"] = ""
    st.caption("Edit the suggested decisions in the table. Each reviewed decision is written to the audit log.")
    choices = {
        "ReviewerOrigin": ["Domestic", "Foreign", "Review"],
        "ReviewerClassification": ["Building", "Machine", "Intangible Asset"],
    }
    editable_columns = [
        column for column in ["AiB", "PSP", "ExceptionReason", "PreviousQuarterDecision", "ReviewerOrigin", "ReviewerClassification", "ReviewerNotes"]
        if column in exceptions.columns
    ]
    editor_config = {key: st.column_config.SelectboxColumn(key, options=value) for key, value in choices.items() if key in editable_columns}
    editor_config["ExceptionReason"] = st.column_config.TextColumn("Why it needs review", disabled=True, width="large")
    editor_config["PreviousQuarterDecision"] = st.column_config.TextColumn("Prior decision", disabled=True)
    editor_config["AiB"] = st.column_config.TextColumn(disabled=True)
    editor_config["PSP"] = st.column_config.TextColumn(disabled=True)
    reviewed = st.data_editor(
        exceptions[editable_columns],
        column_config=editor_config,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="exception_editor",
    )
    if st.button("Apply review decisions", type="primary", icon=":material/check:"):
        revised, review_audit = apply_reviews(master, reviewed, reviewer)
        st.session_state.master = revised
        st.session_state.review_audit = review_audit
        st.session_state.exceptions = revised.loc[revised["ExceptionReason"].ne("")].copy()
        st.success(f"Applied {len(review_audit)} review decision(s).")
    return st.session_state.get("master", master), reviewer


st.markdown(
    '<div class="hero"><div><div class="eyebrow">Quarterly investment statistics · workflow 01</div><h1>Asset classification run</h1><p>Bring SAP exports together, apply the rules, and resolve only the records that need a human decision.</p></div><div class="hero-mark">FILE-DRIVEN · AUDITABLE</div></div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Run setup")
    quarter = st.selectbox("Reporting period", ["Q1", "Q2", "Q3", "Q4"])
    year = st.number_input("Financial year", min_value=2020, max_value=2100, value=2026, step=1)
    st.markdown('<div class="side-note">SAP extraction is intentionally decoupled. Upload standard exports or use the included demo records; all classification logic runs locally.</div>', unsafe_allow_html=True)

input_tab, review_tab, output_tab = st.tabs(["01  Source files", "02  Review exceptions", "03  Results & export"])

with input_tab:
    st.markdown("#### Source files")
    st.caption("Required exports are marked *. XLSX and CSV are accepted; the first worksheet is used for Excel files.")
    labels = {
        "current_movements": ("Current-quarter movements", "Quarter postings · AiB, PSP"),
        "asset_master": ("ANLA · asset master", "Asset class and cost center · AiB"),
        "projects": ("CN43N · projects", "Project type · PSP"),
        "cji3": ("CJI3 · actual line items", "Vendor and GL account · AiB, PSP"),
        "vendors": ("LFA1 · vendor master", "Country lookup · Vendor"),
        "cost_centers": ("Cost-center plan", "Optional enrichment columns · CostCenter"),
        "annual_movements": ("Full-year movements", "Optional annual reporting extract"),
        "previous_quarter": ("Previous-quarter master", "Optional history comparison · AiB, PSP"),
    }
    source_frames: dict[str, pd.DataFrame] = {}
    for role, (label, hint) in labels.items():
        frame = source_picker(role, label, hint, role in REQUIRED_SOURCES)
        if frame is not None:
            source_frames[role] = frame

    st.markdown("#### Run controls")
    rules = read_rules()
    with st.expander("Classification rules", expanded=False):
        st.caption("Edit these values in config/rules.json to match process-owner decisions.")
        excluded = st.text_input("CJI3 GL accounts to exclude (comma-separated)", ", ".join(rules.get("excluded_gl_accounts", [])))
        rules["excluded_gl_accounts"] = [value.strip() for value in excluded.split(",") if value.strip()]
        rules["domestic_country_code"] = st.text_input("Domestic country code", rules.get("domestic_country_code", "HU")).strip().upper()
        startup = st.text_input("Start Up project types (comma-separated)", ", ".join(rules.get("startup_project_types", [])))
        rules["startup_project_types"] = [value.strip().upper() for value in startup.split(",") if value.strip()]
    ready = all(role in source_frames for role in REQUIRED_SOURCES)
    if st.button("Process quarter", type="primary", icon=":material/play_arrow:", disabled=not ready, use_container_width=False):
        with st.spinner("Validating sources and applying classification rules..."):
            result = process_sources(source_frames, rules, f"{quarter} {year}")
        if result.errors:
            for error in result.errors:
                st.error(error)
        else:
            st.session_state.master = result.master
            st.session_state.exceptions = result.exceptions
            st.session_state.audit = result.audit
            st.session_state.review_audit = pd.DataFrame()
            st.session_state.summary = result.summary
            st.session_state.run_complete = True
            st.success(f"Processed {result.summary['processed']:,} records. Review the flagged items in the next tab.")

with review_tab:
    if not st.session_state.get("run_complete"):
        st.info("Process a quarter first. Results will appear here for review.")
    else:
        current_master = st.session_state.master
        current_exceptions = st.session_state.exceptions
        show_exception_review(current_master, current_exceptions)

with output_tab:
    if not st.session_state.get("run_complete"):
        st.info("Process a quarter to build the result workbook.")
    else:
        master = st.session_state.master
        exceptions = master.loc[master["ExceptionReason"].ne("")].copy()
        exceptions["ReviewerOrigin"] = exceptions["Origin"].replace("Review", "")
        exceptions["ReviewerClassification"] = exceptions["Classification"]
        exceptions["ReviewerNotes"] = ""
        exceptions["ReviewedBy"] = ""
        summary = dict(st.session_state.summary)
        summary["exceptions"] = len(exceptions)
        summary["auto_classified"] = len(master) - len(exceptions)
        summary["reviewed"] = len(st.session_state.get("review_audit", pd.DataFrame()))
        columns = st.columns(5)
        for column, label, key in zip(columns, ["Records processed", "Auto-classified", "Needs review", "Changed vs prior", "CJI3 rows excluded"], ["processed", "auto_classified", "exceptions", "changed_since_previous", "excluded_cji3_rows"]):
            column.metric(label, f"{summary.get(key, 0):,}")
        st.markdown("#### Master list")
        st.dataframe(master, hide_index=True, use_container_width=True, height=360)
        audit = pd.concat([st.session_state.audit, st.session_state.get("review_audit", pd.DataFrame())], ignore_index=True)
        output = workbook_bytes(master, exceptions, audit, summary)
        st.download_button(
            "Download quarterly workbook",
            data=output,
            file_name=f"Investment_Statistics_{quarter}_{year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            icon=":material/download:",
        )