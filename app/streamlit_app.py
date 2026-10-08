"""Vendor Invoice Review — a finance-facing UI over the detection rules.

Run:  pip install -e ".[ui]"  &&  streamlit run app/streamlit_app.py

Two data sources: the built-in synthetic dataset, or a file the finance team
exports themselves (CSV / Excel — e.g. SAP FBL1N vendor line items). Nothing
is uploaded anywhere: the file is read in memory by this process only.
"""
from __future__ import annotations

import copy
import datetime
import html
import io
from importlib import resources

import altair as alt
import pandas as pd
import streamlit as st
import yaml

from invoice_anomaly_demo.csv_import import (
    REQUIRED_FIELDS,
    dataframe_to_invoices,
    guess_column_mapping,
    invoices_to_dataframe,
)
from invoice_anomaly_demo.review import Finding, ReviewSummary, run_review
from invoice_anomaly_demo.synthetic_data import generate_demo_dataset

st.set_page_config(page_title="Vendor Invoice Review", page_icon="🧾", layout="wide")

SEVERITY_COLORS = {"high": "#c0392b", "medium": "#d68910", "low": "#7f8c8d"}
FIELD_LABELS = {
    "vendor_id": "Vendor number",
    "vendor_name": "Vendor name (optional)",
    "invoice_id": "Invoice / document number",
    "posting_date": "Posting date",
    "amount": "Amount",
}

st.markdown(
    """
    <style>
      .finding {border:1px solid rgba(128,128,128,.25); border-left-width:6px;
                border-radius:8px; padding:14px 18px; margin-bottom:6px;}
      .finding h4 {margin:0 0 4px 0; font-size:1.05rem;}
      .finding .meta {opacity:.75; font-size:.9rem; margin-bottom:6px;}
      .badge {display:inline-block; color:#fff; border-radius:4px; padding:1px 8px;
              font-size:.75rem; font-weight:600; margin-right:8px; vertical-align:middle;}
      .email {border:1px solid rgba(128,128,128,.3); border-radius:8px; padding:0; overflow:hidden;}
      .email .hdr {background:rgba(128,128,128,.08); padding:10px 16px; font-size:.9rem;}
      .email .body {padding:16px;}
      .email table {border-collapse:collapse; width:100%; font-size:.9rem;}
      .email td, .email th {border-bottom:1px solid rgba(128,128,128,.2); padding:6px 8px; text-align:left;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- helpers
@st.cache_data
def load_default_rules() -> dict:
    text = resources.files("invoice_anomaly_demo").joinpath("config/rules.yaml").read_text("utf-8")
    return yaml.safe_load(text)


@st.cache_data
def sample_invoices(seed: int):
    ds = generate_demo_dataset(seed=seed)
    invoices = [i for v in ds.history.values() for i in v] + [
        i for v in ds.todays_activity.values() for i in v
    ]
    return invoices, ds.today


def money(value, currency: str) -> str:
    return f"{currency} {float(value):,.0f}".strip()


@st.cache_data(show_spinner=False)
def read_upload(name: str, data: bytes) -> pd.DataFrame:
    buf = io.BytesIO(data)
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(buf, dtype=str)
    # SAP/Excel CSVs are often ; separated or UTF-8 with BOM
    return pd.read_csv(buf, dtype=str, sep=None, engine="python", encoding="utf-8-sig")


def findings_table(findings: list[Finding], currency: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Severity": f.severity.upper(),
                "Finding": f.label,
                "Vendor": f.vendor_id,
                "Vendor Name": f.vendor_name,
                "Posting Date": f.posting_date.isoformat(),
                "Invoices": ", ".join(i.invoice_id for i in f.invoices),
                f"Amount Under Review ({currency})": float(f.amount_under_review),
                f"Potential Overpayment ({currency})": float(f.potential_overpayment),
                "Why": f.message,
            }
            for f in findings
        ]
    )


def vendor_chart(invoices, finding: Finding, currency: str):
    ledger = [i for i in invoices if i.vendor_id == finding.vendor_id]
    flagged = {i.invoice_id for i in finding.invoices}
    df = pd.DataFrame(
        [
            {
                "Date": i.posting_date,
                "Amount": float(i.amount),
                "Invoice": i.invoice_id,
                "Status": "Flagged" if i.invoice_id in flagged else "Normal",
            }
            for i in ledger
        ]
    )
    return (
        alt.Chart(df)
        .mark_circle(size=60)
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y("Amount:Q", title=currency),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(domain=["Normal", "Flagged"], range=["#95a5a6", "#c0392b"]),
                legend=alt.Legend(orient="top", title=None),
            ),
            size=alt.condition(alt.datum.Status == "Flagged", alt.value(160), alt.value(40)),
            tooltip=["Invoice", alt.Tooltip("Date:T"), alt.Tooltip("Amount:Q", format=",.0f")],
        )
        .properties(height=220)
    )


def email_html(summary: ReviewSummary, currency: str, company: str) -> str:
    rows = "".join(
        f"<tr><td><span class='badge' style='background:{SEVERITY_COLORS.get(f.severity, '#777')}'>"
        f"{f.severity.upper()}</span></td><td>{html.escape(f.label)}</td>"
        f"<td>{html.escape(f.vendor_name)}</td><td>{f.posting_date}</td>"
        f"<td style='text-align:right'>{money(f.amount_under_review, currency)}</td></tr>"
        for f in summary.findings[:10]
    )
    more = (
        f"<p style='opacity:.7'>+ {len(summary.findings) - 10} more in the attached report.</p>"
        if len(summary.findings) > 10
        else ""
    )
    body = f"""
    <div class="email">
      <div class="hdr">
        <b>From:</b> Invoice Monitoring &lt;alerts@{html.escape(company.lower().replace(' ', '') or 'company')}.com&gt;<br>
        <b>To:</b> Finance Controller<br>
        <b>Subject:</b> {len(summary.findings)} invoice(s) need review — {money(summary.amount_under_review, currency)} under review
      </div>
      <div class="body">
        <p>Good morning,</p>
        <p>Today's check compared new invoices against the billing history of
        <b>{summary.vendors_scanned:,}</b> vendors ({summary.invoices_scanned:,} invoices).
        The following need a look before payment:</p>
        <table><tr><th></th><th>Finding</th><th>Vendor</th><th>Date</th><th style='text-align:right'>Amount</th></tr>{rows}</table>
        {more}
        <p>Potential duplicate payments: <b>{money(summary.potential_overpayment, currency)}</b>.</p>
        <p style="opacity:.7">Each vendor is compared with its own billing history, so routine patterns
        (instalments, month-end batches) are not flagged.</p>
      </div>
    </div>
    """
    # Markdown treats indented lines as code blocks -- flatten before rendering.
    return " ".join(line.strip() for line in body.splitlines() if line.strip())


# ---------------------------------------------------------------- sidebar
defaults = load_default_rules()
rules_config = copy.deepcopy(defaults)
r = rules_config["rules"]

with st.sidebar:
    st.header("Data")
    source = st.radio("Source", ["Sample data (synthetic)", "Upload your export"], label_visibility="collapsed")
    company = st.text_input("Company name (for the email preview)", "Your Company")
    currency = st.text_input("Currency", "EGP")

    st.header("Scan mode")
    mode = st.radio(
        "Mode",
        ["Daily monitoring", "Audit the whole file"],
        index=0 if source.startswith("Sample") else 1,
        help="Daily monitoring: only invoices posted on/after the chosen date can raise a finding; "
        "everything before is history. Audit: every invoice is checked against the history around it.",
    )

    with st.expander("Rule settings", expanded=False):
        st.caption("Defaults come from rules.yaml. Changes apply to this session only.")
        r["duplicate"]["enabled"] = st.checkbox("Duplicate check", r["duplicate"]["enabled"])
        r["duplicate"]["normal_repeat_rate"] = (
            st.slider(
                "Ignore repeats if vendor repeats on more than (%) of their multi-invoice days",
                0, 50, int(r["duplicate"]["normal_repeat_rate"] * 100),
            )
            / 100
        )
        r["amount_spike"]["enabled"] = st.checkbox("Amount spike check", r["amount_spike"]["enabled"])
        r["amount_spike"]["percent_above"] = st.slider(
            "Flag if above vendor's previous high by (%)", 10, 300, int(r["amount_spike"]["percent_above"]), 10
        )
        r["amount_spike"]["comparison_months"] = st.slider(
            "Compare against last (months)", 3, 24, int(r["amount_spike"]["comparison_months"])
        )
        r["same_day_count"]["enabled"] = st.checkbox("Same-day volume check", r["same_day_count"]["enabled"])
        r["same_day_count"]["max_documents_per_day"] = st.slider(
            "Minimum invoices in a day before it counts", 2, 30, int(r["same_day_count"]["max_documents_per_day"])
        )
        warmup_days = st.slider(
            "Audit warm-up (days of history before alerting)", 0, 180, 90, 15,
            help="In audit mode, each vendor's first N days are used as context only.",
        )

# ---------------------------------------------------------------- data
invoices = []
import_note = None

if source.startswith("Sample"):
    invoices, default_since = sample_invoices(7)
    st.sidebar.download_button(
        "Download sample file (CSV)",
        invoices_to_dataframe(invoices).to_csv(index=False).encode("utf-8"),
        "sample_vendor_invoices.csv",
        "text/csv",
    )
else:
    uploaded = st.sidebar.file_uploader("CSV or Excel", type=["csv", "xlsx", "xls"])
    if uploaded is None:
        st.title("Vendor Invoice Review")
        st.info(
            "Upload a vendor invoice export — for example SAP **FBL1N** vendor line items for the last "
            "12 months. You need one row per invoice with: vendor number, invoice/document number, "
            "posting date and amount. The file stays on this machine."
        )
        st.stop()

    raw = read_upload(uploaded.name, uploaded.getvalue())
    guess = guess_column_mapping(list(raw.columns))
    with st.sidebar.expander("Column mapping", expanded=any(guess[f] is None for f in REQUIRED_FIELDS)):
        options = ["—"] + list(raw.columns)
        mapping = {}
        for fld, label in FIELD_LABELS.items():
            pre = guess.get(fld)
            choice = st.selectbox(label, options, index=options.index(pre) if pre in options else 0, key=f"map_{fld}")
            mapping[fld] = None if choice == "—" else choice
        dayfirst = st.checkbox("Dates are day-first (31/12/2025)", True)
        use_abs = st.checkbox("Treat negative amounts as invoices (SAP credit side)", True)

    missing = [FIELD_LABELS[f] for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        st.warning("Choose a column for: " + ", ".join(missing))
        st.stop()

    result = dataframe_to_invoices(raw, mapping, dayfirst=dayfirst, use_absolute_amounts=use_abs)
    invoices = result.invoices
    if result.skipped:
        import_note = result
    if not invoices:
        st.error("No usable rows in this file. Check the column mapping.")
        st.stop()
    default_since = max(i.posting_date for i in invoices)

new_since = None
if mode == "Daily monitoring":
    new_since = st.sidebar.date_input(
        "Check invoices posted on/after",
        default_since,
        min_value=min(i.posting_date for i in invoices),
        max_value=max(i.posting_date for i in invoices),
    )

summary = run_review(invoices, rules_config, new_since=new_since, warmup_days=warmup_days)

# ---------------------------------------------------------------- header
st.title("Vendor Invoice Review")
scope = (
    f"Invoices posted on/after **{new_since}**, each compared with that vendor's own history"
    if new_since
    else f"Full audit of **{summary.period_start} → {summary.period_end}**"
)
st.caption(f"{scope} · {summary.invoices_scanned:,} invoices · {summary.vendors_scanned:,} vendors")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Amount under review", money(summary.amount_under_review, currency))
c2.metric("Potential duplicate payments", money(summary.potential_overpayment, currency))
c3.metric("Findings", f"{len(summary.findings):,}")
c4.metric("Vendors flagged", f"{summary.vendors_flagged:,} of {summary.vendors_scanned:,}")

if import_note:
    with st.expander(f"{len(import_note.skipped)} row(s) skipped while reading the file"):
        st.dataframe(pd.DataFrame(import_note.skipped, columns=["Row", "Reason"]), hide_index=True)

tab_findings, tab_email, tab_export = st.tabs(["Findings", "Email preview", "Export"])

# ---------------------------------------------------------------- findings
with tab_findings:
    if not summary.findings:
        st.success("No findings. Every invoice in scope matches its vendor's normal pattern.")
    else:
        labels = sorted({f.label for f in summary.findings})
        picked = st.multiselect("Show", labels, default=labels, label_visibility="collapsed")
        shown = [f for f in summary.findings if f.label in picked]
        for idx, f in enumerate(shown[:200]):
            color = SEVERITY_COLORS.get(f.severity, "#777")
            inv_ids = ", ".join(html.escape(i.invoice_id) for i in f.invoices)
            ids_row = (
                ""
                if "Invoices:" in f.message
                else f'<div class="meta" style="margin-top:6px">Invoices: {inv_ids}</div>'
            )
            extra = (
                f" · potential overpayment <b>{money(f.potential_overpayment, currency)}</b>"
                if f.potential_overpayment
                else ""
            )
            st.markdown(
                f"""<div class="finding" style="border-left-color:{color}">
                <h4><span class="badge" style="background:{color}">{f.severity.upper()}</span>{html.escape(f.label)}</h4>
                <div class="meta">{html.escape(f.vendor_name)} ({html.escape(f.vendor_id)}) · {f.posting_date}
                · <b>{money(f.amount_under_review, currency)}</b>{extra}</div>
                <div>{html.escape(f.message)}</div>
                {ids_row}</div>""",
                unsafe_allow_html=True,
            )
            with st.expander("Show this vendor's history"):
                st.altair_chart(vendor_chart(invoices, f, currency), width="stretch")
        if len(shown) > 200:
            st.caption(f"Showing the first 200 of {len(shown)}. Use Export for the full list.")

# ---------------------------------------------------------------- email
with tab_email:
    st.caption("What the finance controller would receive each morning.")
    if summary.findings:
        st.markdown(email_html(summary, currency, company), unsafe_allow_html=True)
    else:
        st.info("No email is sent on days with no findings.")

# ---------------------------------------------------------------- export
with tab_export:
    table = findings_table(summary.findings, currency)
    st.dataframe(table, hide_index=True, width="stretch")
    stamp = datetime.date.today().isoformat()
    col_a, col_b = st.columns(2)
    col_a.download_button(
        "Download CSV", table.to_csv(index=False).encode("utf-8-sig"), f"invoice_findings_{stamp}.csv", "text/csv"
    )
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        table.to_excel(xw, index=False, sheet_name="Findings")
    col_b.download_button(
        "Download Excel",
        xbuf.getvalue(),
        f"invoice_findings_{stamp}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
