"""AP Invoice Agent: PDF -> reasoned decision. AI reads the PDF; deterministic rules decide."""
import os, re, json, base64, sqlite3, time, difflib, html, datetime as dt
import streamlit as st, pandas as pd, pdfplumber

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
TOL = 0.02  # ASSUMPTION: 2% over-PO tolerance, configurable per company
CONF_MIN = 0.7
VENDORS = ["Acme Supplies Ltd", "Globex Corporation", "Initech Solutions", "Umbrella Logistics"]
POS = {"PO-1001": ("Acme Supplies Ltd", 5000), "PO-1002": ("Globex Corporation", 5000),
       "PO-1003": ("Initech Solutions", 12000), "PO-1004": ("Umbrella Logistics", 2000)}
SAMPLES = [  # file, vendor, invoice no, date, PO, total, layout style
 ("1_happy_path.pdf", "Acme Supplies Ltd", "INV-0042", "2026-10-28", "PO-1001", 5000.0, 0),
 ("2_duplicate_resubmit.pdf", "Acme Supplies Ltd", "INV0042", "2026-10-30", "PO-1001", 5000.0, 1),
 ("3_within_tolerance.pdf", "Globex Corporation", "GX-778", "2026-10-29", "PO-1002", 5080.0, 1),
 ("4_split_part1.pdf", "Initech Solutions", "IN-301", "2026-10-20", "PO-1003", 5000.0, 0),
 ("5_split_part2.pdf", "Initech Solutions", "IN-302", "2026-10-24", "PO-1003", 4000.0, 0),
 ("6_split_part3_overrun.pdf", "Initech Solutions", "IN-303", "2026-10-30", "PO-1003", 4000.0, 0),
 ("7_no_po_reference.pdf", "Umbrella Logistics", "UL-55", "2026-10-27", None, 2000.0, 1),
 ("8_missing_fields.pdf", "Acme Supplies Ltd", None, None, "PO-1001", None, 0),
 ("9_unknown_vendor.pdf", "Shadow Traders LLC", "ST-9", "2026-10-30", None, 1800.0, 0),
  ("10_stale_invoice.pdf", "Initech Solutions", "IN-304", "2026-06-01", "PO-1003", 3000.0, 1)]
SDIR = "samples"

# ---------- Design system ----------
# Direction: a quiet ledger. Serif headings, plain sans body, monospaced tabular figures for money.
# Two palettes (light / dark) drive every colour, including Streamlit's own widgets, so the app
# looks the same whatever theme Streamlit or the OS is using. Status colours always come with an icon and a word.
PALETTES = {
    "light": dict(paper="#f4f5f7", surface="#ffffff", ink="#161b22", muted="#586171", rule="#d9dde3", rule_soft="#e8ebef",
                  line="#b4bcc8", accent="#0b5d63", accent_strong="#08464b", accent_soft="#e2eff0", on_accent="#ffffff",
                  ok="#1b7a4b", ok_bg="#e7f4ec", warn="#96590a", warn_bg="#fbf0dc", warn_edge="#c8780f",
                  bad="#b42318", bad_bg="#fbeae8", info="#2a5fa8", info_bg="#e8eff9", shadow="rgba(22,27,34,.06)", scheme="light"),
    "dark": dict(paper="#0e1216", surface="#161b21", ink="#e9edf2", muted="#9aa5b3", rule="#29313a", rule_soft="#212830",
                 line="#46515e", accent="#4fc3bd", accent_strong="#86dcd7", accent_soft="#143332", on_accent="#04201f",
                 ok="#4cc38a", ok_bg="#10291d", warn="#e6ad50", warn_bg="#2b2210", warn_edge="#e6ad50",
                 bad="#f27d72", bad_bg="#2f1514", info="#7fabea", info_bg="#14233a", shadow="rgba(0,0,0,.35)", scheme="dark"),
}

CSS = r'''
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap');
:root {
    __VARS__
    --serif: "Source Serif 4", Georgia, "Times New Roman", serif;
    --sans: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
    --radius: 10px;
}
/* smooth theme switching: colours ease, layout never moves */
*, *::before, *::after { transition: background-color .35s ease, border-color .35s ease, color .35s ease, fill .35s ease, box-shadow .35s ease; }

html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: var(--paper) !important; color: var(--ink); font-family: var(--sans); -webkit-font-smoothing: antialiased; }
[data-testid="stApp"] { color-scheme: var(--scheme); }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stHeader"] *, [data-testid="stSidebarCollapseButton"] *, [data-testid="stExpandSidebarButton"] * { color: var(--muted) !important; fill: var(--muted) !important; }
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], #MainMenu, footer { display: none !important; }
.block-container { max-width: 1180px; padding: 2.2rem 2rem 4rem; }

/* ---- text ---- */
h1, h2, h3, h4, [data-testid="stHeading"] h3 { font-family: var(--serif) !important; color: var(--ink) !important; letter-spacing: -0.01em !important; font-weight: 600 !important; }
[data-testid="stHeading"] h3, h3 { font-size: 1.3rem !important; margin-top: 1.6rem; padding: 0.2rem 0 0.4rem !important; }
.stMarkdown, .stMarkdown p, .stMarkdown li, [data-testid="stMarkdownContainer"] p, [data-testid="stWidgetLabel"] p, label p, label { color: var(--ink); font-family: var(--sans); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }
.stMarkdown p { max-width: 72ch; line-height: 1.6; }

/* ---- page header ---- */
.page-head { padding: 0 0 1.4rem; margin-bottom: 1.6rem; border-bottom: 1px solid var(--rule); animation: pagein .4s ease both; }
.page-crumb { font-size: 0.84rem; color: var(--muted); margin-bottom: 0.6rem; }
.page-crumb b { color: var(--accent); font-weight: 600; }
.page-head h1 { font-family: var(--serif) !important; font-size: clamp(1.9rem, 3.6vw, 2.7rem) !important; line-height: 1.1 !important; margin: 0 0 0.6rem !important; padding: 0 !important; color: var(--ink) !important; }
.page-head p { margin: 0; color: var(--muted) !important; font-size: 1.02rem; max-width: 62ch; }
@keyframes pagein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

/* ---- sidebar ---- */
[data-testid="stSidebar"], [data-testid="stSidebar"] > div { background: var(--surface) !important; }
[data-testid="stSidebar"] { border-right: 1px solid var(--rule); }
[data-testid="stSidebar"] h1 { font-family: var(--sans) !important; font-size: 0.9rem !important; font-weight: 600 !important; color: var(--muted) !important; margin-top: 1rem; padding-bottom: 0.3rem !important; }
.brand { display: flex; align-items: center; gap: 0.75rem; padding: 0.2rem 0 1rem; border-bottom: 1px solid var(--rule-soft); }
.brand-mark { width: 40px; height: 40px; border-radius: 9px; background: var(--accent); color: var(--on_accent); display: grid; place-items: center; font-family: var(--serif); font-weight: 600; font-size: 1.05rem; flex: none; }
.brand-name { font-family: var(--serif); font-weight: 600; font-size: 1.12rem; line-height: 1.15; color: var(--ink); }
.brand-sub { font-size: 0.8rem; color: var(--muted); }

/* nav as a list, not radio dots */
[data-testid="stRadio"] [role="radiogroup"] { gap: 0.2rem; }
[data-testid="stRadio"] label[data-baseweb="radio"] { padding: 0.6rem 0.8rem; margin: 0; border-radius: 8px; width: 100%; cursor: pointer; }
[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child { display: none; }
[data-testid="stRadio"] label[data-baseweb="radio"] p { color: var(--muted) !important; font-weight: 500; }
[data-testid="stRadio"] label[data-baseweb="radio"]:hover { background: var(--accent-soft); }
[data-testid="stRadio"] label[data-baseweb="radio"]:hover p { color: var(--ink) !important; }
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) p { color: var(--accent) !important; font-weight: 600; }
[data-testid="stToggle"] label p, [data-testid="stCheckbox"] label p { color: var(--ink) !important; font-weight: 500; }
.side-label { font-size: 0.9rem; font-weight: 600; color: var(--muted); margin: 1.1rem 0 0.2rem; }

/* ---- buttons (always visible: outlined secondary, filled primary) ---- */
button[data-testid^="stBaseButton"], div.stButton > button { border-radius: 8px !important; font-weight: 600; font-family: var(--sans); padding: 0.55rem 1.3rem; transition: background-color .2s ease, border-color .2s ease, color .2s ease, transform .12s ease, box-shadow .2s ease; }
button[data-testid="stBaseButton-secondary"], div.stButton > button[kind="secondary"] { background: var(--surface) !important; color: var(--ink) !important; border: 1px solid var(--line) !important; }
button[data-testid="stBaseButton-secondary"]:hover { border-color: var(--accent) !important; color: var(--accent) !important; background: var(--accent-soft) !important; }
button[data-testid="stBaseButton-primary"], div.stButton > button[kind="primary"] { background: var(--accent) !important; color: var(--on_accent) !important; border: 1px solid var(--accent) !important; box-shadow: 0 1px 2px var(--shadow); }
button[data-testid="stBaseButton-primary"]:hover { background: var(--accent_strong) !important; border-color: var(--accent_strong) !important; transform: translateY(-1px); box-shadow: 0 6px 14px var(--shadow); }
button[data-testid^="stBaseButton"]:active { transform: translateY(1px); }
button[data-testid^="stBaseButton"] p, button[data-testid^="stBaseButton"] span { color: inherit !important; }
button:focus-visible, input:focus-visible, textarea:focus-visible, [role="radiogroup"] label:focus-within { outline: 3px solid var(--accent-soft) !important; outline-offset: 2px; box-shadow: 0 0 0 1px var(--accent); }

/* ---- inputs ---- */
[data-testid="stFileUploaderDropzone"] { background: var(--surface) !important; border: 1.5px dashed var(--line) !important; border-radius: var(--radius) !important; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--accent) !important; background: var(--accent-soft) !important; }
[data-testid="stFileUploaderDropzone"] *, [data-testid="stFileUploaderDropzoneInstructions"] * { color: var(--muted) !important; }
[data-testid="stFileUploaderDropzone"] button { background: var(--surface) !important; color: var(--accent) !important; border: 1px solid var(--accent) !important; }
[data-testid="stFileUploaderDropzone"] button * { color: var(--accent) !important; }
[data-testid="stFileUploaderDropzone"] button:hover { background: var(--accent-soft) !important; }
[data-testid="stFileUploaderDropzone"] svg { fill: var(--muted) !important; }
[data-testid="stFileUploaderFile"] * { color: var(--ink) !important; }

div[data-baseweb="select"] > div { background: var(--surface) !important; border: 1px solid var(--line) !important; border-radius: 8px !important; min-height: 2.9rem; }
div[data-baseweb="select"] * { color: var(--ink) !important; }
div[data-baseweb="select"] input::placeholder { color: var(--muted) !important; opacity: 1; }
div[data-baseweb="select"] svg { fill: var(--muted) !important; }
div[data-baseweb="select"] > div:hover { border-color: var(--accent) !important; }
div[data-baseweb="select"]:focus-within > div { border-color: var(--accent) !important; box-shadow: 0 0 0 3px var(--accent-soft) !important; }
div[data-baseweb="popover"], div[data-baseweb="popover"] > div, div[data-baseweb="menu"], ul[role="listbox"] { background: var(--surface) !important; border-radius: 8px; }
ul[role="listbox"] { border: 1px solid var(--rule); }
li[role="option"] { background: transparent !important; color: var(--ink) !important; }
li[role="option"]:hover, li[role="option"][aria-selected="true"] { background: var(--accent-soft) !important; }
li[role="option"] * { color: var(--ink) !important; }
textarea, [data-baseweb="textarea"], [data-baseweb="textarea"] > div { background: var(--surface) !important; color: var(--ink) !important; border-color: var(--line) !important; border-radius: 8px !important; }
textarea { font-family: var(--mono) !important; font-size: 0.88rem !important; -webkit-text-fill-color: var(--ink) !important; }

/* ---- metrics, alerts, run steps ---- */
div[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); padding: 0.9rem 1.1rem; }
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] * { color: var(--muted) !important; }
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] * { font-family: var(--mono) !important; font-weight: 500; font-size: 1.9rem; color: var(--ink) !important; }
div[data-testid="stAlert"], div[data-testid="stAlertContainer"] { background: var(--warn_bg) !important; border: 1px solid var(--rule) !important; border-radius: var(--radius) !important; }
div[data-testid="stAlert"] *, div[data-testid="stAlertContainer"] * { color: var(--ink) !important; }
div[data-testid="stStatus"], div[data-testid="stExpander"] details { background: var(--surface) !important; border: 1px solid var(--rule) !important; border-radius: var(--radius) !important; box-shadow: none !important; }
div[data-testid="stStatus"] summary, div[data-testid="stStatus"] summary *, div[data-testid="stExpander"] summary * { color: var(--ink) !important; font-weight: 500; }
div[data-testid="stStatus"] svg { fill: var(--muted); }
.jsonbox { margin: 0; padding: 0.9rem 1rem; background: var(--paper); border: 1px solid var(--rule-soft); border-radius: 8px; font-family: var(--mono); font-size: 0.84rem; line-height: 1.55; color: var(--ink); overflow-x: auto; white-space: pre-wrap; word-break: break-word; }

/* ---- surfaces ---- */
.empty { background: var(--surface); border: 1.5px dashed var(--line); border-radius: var(--radius); padding: 1.6rem; color: var(--muted); animation: pagein .45s ease both; }
.empty strong { display: block; color: var(--ink); font-family: var(--serif); font-size: 1.15rem; margin-bottom: 0.3rem; font-weight: 600; }

/* ---- decision banner ---- */
.verdict { display: grid; grid-template-columns: auto 1fr; gap: 0 1rem; align-items: start; margin: 1.4rem 0 1rem; padding: 1.2rem 1.4rem; border-radius: var(--radius); border: 1px solid var(--rule); border-left-width: 6px; background: var(--surface); animation: settle .5s cubic-bezier(.2,.8,.2,1) both; }
.verdict .v-icon { font-size: 1.6rem; line-height: 1.2; }
.verdict .v-title { font-family: var(--serif); font-size: 1.55rem; font-weight: 600; line-height: 1.2; margin: 0; }
.verdict .v-reason { margin: 0.4rem 0 0; color: var(--muted); font-size: 0.97rem; }
.verdict.ok { border-left-color: var(--ok); background: var(--ok_bg); } .verdict.ok .v-title { color: var(--ok); }
.verdict.warn { border-left-color: var(--warn_edge); background: var(--warn_bg); } .verdict.warn .v-title { color: var(--warn); }
.verdict.bad { border-left-color: var(--bad); background: var(--bad_bg); } .verdict.bad .v-title { color: var(--bad); }
.verdict.info { border-left-color: var(--info); background: var(--info_bg); } .verdict.info .v-title { color: var(--info); }
@keyframes settle { from { opacity: 0; transform: translateY(8px) scale(.99); } to { opacity: 1; transform: none; } }

/* ---- chips ---- */
.chip { display: inline-block; font-size: 0.78rem; font-weight: 600; padding: 0.14rem 0.6rem; border-radius: 999px; white-space: nowrap; }
.chip.pass, .chip.ok { color: var(--ok); background: var(--ok_bg); } .chip.warn { color: var(--warn); background: var(--warn_bg); }
.chip.fail, .chip.bad { color: var(--bad); background: var(--bad_bg); } .chip.info { color: var(--info); background: var(--info_bg); }

/* ---- check ledger ---- */
.ledger { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); overflow: hidden; margin: 0.4rem 0 1rem; animation: pagein .4s ease both; }
.lrow { display: grid; grid-template-columns: 190px 100px 1fr; gap: 1rem; padding: 0.8rem 1.1rem; border-top: 1px solid var(--rule-soft); align-items: baseline; }
.lrow:first-child { border-top: none; }
.lrow .name { font-weight: 600; color: var(--ink); } .lrow .detail { color: var(--muted); font-size: 0.93rem; }

/* ---- data tables + bars (HTML so they follow the theme) ---- */
.tablewrap { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); overflow: auto; max-height: 380px; margin-bottom: 0.6rem; animation: pagein .4s ease both; }
.dt, .policy { width: 100%; border-collapse: collapse; }
.dt th, .policy th { position: sticky; top: 0; text-align: left; font-weight: 600; color: var(--muted); font-size: 0.84rem; padding: 0.7rem 1rem; background: var(--paper); border-bottom: 1px solid var(--rule); white-space: nowrap; }
.dt td, .policy td { padding: 0.7rem 1rem; border-top: 1px solid var(--rule-soft); font-size: 0.92rem; color: var(--ink); vertical-align: middle; }
.dt tr:first-child td, .policy tr:first-child td { border-top: none; }
.dt tbody tr:hover td { background: var(--accent-soft); }
.dt td.num { font-family: var(--mono); text-align: right; white-space: nowrap; } .dt th.num { text-align: right; }
.dt td.dim { color: var(--muted); }
.meter { display: flex; align-items: center; gap: 0.6rem; min-width: 150px; }
.meter .track { flex: 1; height: 8px; border-radius: 99px; background: var(--rule-soft); overflow: hidden; }
.meter .fill { height: 100%; border-radius: 99px; background: var(--accent); animation: grow .8s cubic-bezier(.2,.8,.2,1) both; }
.meter .pct { font-family: var(--mono); font-size: 0.85rem; color: var(--muted); min-width: 3ch; text-align: right; }
@keyframes grow { from { width: 0; } }
.bars { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); padding: 0.6rem 1.1rem; margin-bottom: 0.6rem; }
.brow { display: grid; grid-template-columns: 200px 1fr 3ch; gap: 1rem; align-items: center; padding: 0.5rem 0; }
.brow .bl { color: var(--ink); font-size: 0.93rem; } .brow .bn { font-family: var(--mono); text-align: right; color: var(--ink); }
.brow .btrack { height: 12px; border-radius: 99px; background: var(--rule-soft); overflow: hidden; }
.brow .bfill { height: 100%; border-radius: 99px; animation: grow .8s cubic-bezier(.2,.8,.2,1) both; }
.bfill.ok { background: var(--ok); } .bfill.warn { background: var(--warn_edge); } .bfill.bad { background: var(--bad); } .bfill.info { background: var(--info); }

/* ---- policy page ---- */
.policy-wrap { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); overflow: hidden; animation: pagein .4s ease both; }
.policy code { font-family: var(--mono); font-size: 0.85em; background: var(--paper); padding: 0.05rem 0.35rem; border-radius: 4px; color: var(--ink); }
.policy td, .policy td * { color: var(--ink); }
.note { color: var(--muted); font-size: 0.92rem; margin-top: 1rem; max-width: 80ch; }
.principle { border-left: 3px solid var(--accent); padding: 0.2rem 0 0.2rem 1rem; margin: 0 0 1.3rem; font-size: 1.02rem; max-width: 70ch; color: var(--ink); }

@media (max-width: 767px) {
    .block-container { padding: 1.2rem 1rem 3rem; }
    .lrow { grid-template-columns: 1fr; gap: 0.25rem; }
    .brow { grid-template-columns: 1fr 3ch; } .brow .bl { grid-column: 1 / -1; }
    .verdict { padding: 1rem; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
'''


def inject_theme(dark=False):
    p = PALETTES["dark" if dark else "light"]
    vars_ = " ".join(f"--{k}: {v}; --{k.replace('_', '-')}: {v};" for k, v in p.items())
    st.markdown("<style>" + CSS.replace("__VARS__", vars_) + "</style>", unsafe_allow_html=True)


def render_page_header(title, subtitle, badge="Operations"):
    st.markdown(f'''
    <div class="page-head">
        <div class="page-crumb">AP Invoice Agent / <b>{html.escape(badge)}</b></div>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(subtitle)}</p>
    </div>
    ''', unsafe_allow_html=True)


VERDICT = {  # decision -> (tone, icon, label)
    "APPROVE": ("ok", "✅", "Approved"), "APPROVE_WITH_NOTE": ("ok", "✅", "Approved with note"),
    "NEEDS_REVIEW": ("warn", "⚠️", "Needs review"), "REJECT": ("bad", "⛔", "Rejected"),
    "REQUEST_INFO": ("info", "✉️", "More information needed")}
CHIP_TEXT = {"pass": "Pass", "warn": "Review", "fail": "Fail"}
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌"}


def render_verdict(decision, reason):
    tone, icon, label = VERDICT[decision]
    st.markdown(f'''
    <div class="verdict {tone}" role="status">
        <div class="v-icon" aria-hidden="true">{icon}</div>
        <div><p class="v-title">{label}</p><p class="v-reason">{html.escape(reason)}</p></div>
    </div>''', unsafe_allow_html=True)


def render_checks(checks):
    rows = "".join(
        f'<div class="lrow"><div class="name">{html.escape(c["check"])}</div>'
        f'<div><span class="chip {c["status"]}">{ICON[c["status"]]} {CHIP_TEXT[c["status"]]}</span></div>'
        f'<div class="detail">{html.escape(c["detail"])}</div></div>' for c in checks)
    st.markdown(f'<div class="ledger">{rows}</div>', unsafe_allow_html=True)


def render_json(d):
    st.markdown(f'<pre class="jsonbox">{html.escape(json.dumps(d, indent=2, default=str))}</pre>', unsafe_allow_html=True)


def html_table(df, cols):
    """cols: (key, label, kind) with kind in text | dim | money | chip | meter. Themed HTML instead of st.dataframe."""
    def cell(v, kind):
        if v is None or (isinstance(v, float) and pd.isna(v)): return '<td class="dim">-</td>'
        if kind == "money": return f'<td class="num">${float(v):,.2f}</td>'
        if kind == "chip":
            tone, _, label = VERDICT.get(v, ("info", "", str(v)))
            return f'<td><span class="chip {tone}">{html.escape(label)}</span></td>'
        if kind == "meter":
            pct = max(0, min(100, float(v)))
            return f'<td><div class="meter"><div class="track"><div class="fill" style="width:{pct:.0f}%"></div></div><span class="pct">{pct:.0f}%</span></div></td>'
        return f'<td class="{"dim" if kind == "dim" else ""}">{html.escape(str(v))}</td>'
    head = "".join(f'<th class="{"num" if k == "money" else ""}">{html.escape(l)}</th>' for _, l, k in cols)
    body = "".join("<tr>" + "".join(cell(r.get(key), kind) for key, _, kind in cols) + "</tr>" for r in df.to_dict("records"))
    st.markdown(f'<div class="tablewrap"><table class="dt"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>', unsafe_allow_html=True)


def render_bars(counts):
    top = max(counts.values()) if counts else 1
    rows = "".join(
        f'<div class="brow"><span class="bl">{VERDICT[d][2]}</span><div class="btrack"><div class="bfill {VERDICT[d][0]}" style="width:{n/top*100:.0f}%"></div></div><span class="bn">{n}</span></div>'
        for d, n in sorted(counts.items(), key=lambda kv: RANK[kv[0]]))
    st.markdown(f'<div class="bars">{rows}</div>', unsafe_allow_html=True)


def make_samples():
    from reportlab.pdfgen import canvas
    os.makedirs(SDIR, exist_ok=True)
    for f, v, no, d, po, tot, sty in SAMPLES:
        p = os.path.join(SDIR, f)
        if os.path.exists(p): continue
        L = [v, "INVOICE"]
        if no: L.append(("Invoice No: " if sty == 0 else "Inv # ") + no)
        if d: L.append(f"Date: {d}")
        if po: L.append(f"PO Reference: {po}")
        L.append("Description: Goods and services as per order")
        if tot:
            L += [f"Subtotal: ${(tot/1.1) - (300 if sty == 2 else 0):,.2f}", f"Tax (10%): ${tot-tot/1.1:,.2f}"]
        c = canvas.Canvas(p); c.setFont("Helvetica" if sty == 0 else "Courier", 13); y = 790
        for l in L: c.drawString(60, y, l); y -= 28
        c.save()

# ---------- DB ----------
def db():
    c = sqlite3.connect("runs.db")
    c.execute("create table if not exists runs(id integer primary key, ts text, file text, vendor text, inv text, total real, po text, decision text, reason text, data text)")
    return c
def prior_runs():
    c = db(); df = pd.read_sql("select * from runs where decision in ('APPROVE','APPROVE_WITH_NOTE')", c); c.close(); return df.to_dict("records")

# ---------- Extraction (AI reads) ----------
PROMPT = ("Extract invoice data. Reply with ONLY JSON: {vendor, invoice_number, invoice_date (YYYY-MM-DD), po_reference, subtotal, tax, total, currency, "
          "confidence (0-1, your honesty about extraction quality), notes}. Use null for anything not clearly present. NEVER guess missing values. Numbers as plain numbers.")
def regex_extract(text):
    g = lambda p: (re.search(p, text) or [None, None])[1]
    t = g(r"(?im)^\s*(?:total due|amount payable[^:\n]*|total)\s*:?\s*\$?\s*([\d,]+\.\d{2})")
    return {"vendor": (text.strip().splitlines() or [None])[0] or None,
            "invoice_number": g(r"(?i)(?:invoice\s*(?:no\.?|number|#)|inv\s*#)\s*:?\s*([A-Z0-9\-]+)"),
            "invoice_date": g(r"(?i)date\s*:?\s*(\d{4}-\d{2}-\d{2})"), "po_reference": g(r"\b(PO-\d+)"),
            "subtotal": (lambda s: float(s.replace(",", "")) if s else None)(g(r"(?im)^\s*subtotal\s*:?\s*\$?\s*([\d,]+\.\d{2})")),
            "tax": (lambda s: float(s.replace(",", "")) if s else None)(g(r"(?im)^\s*tax[^:\n]*:\s*\$?\s*([\d,]+\.\d{2})")),
            "total": float(t.replace(",", "")) if t else None, "confidence": 0.9 if t else 0.5, "notes": "regex fallback"}
def extract(pdf_bytes):
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            m = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=800, messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}},
                {"type": "text", "text": PROMPT}]}])
            r = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip()).strip()
            d = json.loads(r); d["engine"] = f"Claude ({MODEL})"; return d
        except Exception as e:
            err = f"AI extraction failed ({type(e).__name__}); used regex fallback"
    else: err = "No API key; used regex fallback"
    import io
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as p: text = "\n".join(pg.extract_text() or "" for pg in p.pages)
    d = regex_extract(text); d["engine"] = "regex"; d["notes"] = err
    if not text.strip(): d["confidence"] = 0.0; d["notes"] += "; scanned PDF, no text layer"
    return d

# ---------- Rules (code decides) ----------
norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
RANK = {"APPROVE": 0, "APPROVE_WITH_NOTE": 1, "NEEDS_REVIEW": 2, "REJECT": 3, "REQUEST_INFO": 4}
def evaluate(x, prior):
    ck, worst = [], "APPROVE"
    def add(n, s, t, d=None):
        nonlocal worst
        ck.append({"check": n, "status": s, "detail": t})
        if d and RANK[d] > RANK[worst]: worst = d
    def done(po=None, email=None):
        return worst, "; ".join(c["detail"] for c in ck), ck, po, email
    miss = [l for k, l in [("vendor", "vendor name"), ("invoice_number", "invoice number"), ("total", "total amount")] if not x.get(k)]
    if miss:
        add("Completeness", "fail", "Missing required fields: " + ", ".join(miss) + " (not guessing)", "REQUEST_INFO")
        mail = (f"Hi {x.get('vendor') or 'team'},\n\nWe received your invoice but could not process it because the following is missing: "
                f"{', '.join(miss)}.\nPlease resend a corrected invoice and we will process it promptly.\n\nRegards,\nAccounts Payable")
        return done(email=mail)
    add("Completeness", "pass", "All required fields present")
    if x.get("confidence", 1) < CONF_MIN: add("Extraction confidence", "warn", f"Low confidence ({x['confidence']:.0%}); human should verify", "NEEDS_REVIEW")
    v = max(VENDORS, key=lambda a: difflib.SequenceMatcher(None, a.lower(), x["vendor"].lower()).ratio())
    try:
        if x.get("subtotal") is not None and x.get("tax") is not None:
            diff = float(x["subtotal"]) + float(x["tax"]) - x["total"]
            if abs(diff) > 0.05: add("Arithmetic", "warn", f"Subtotal + tax differs from total by ${abs(diff):,.2f}; possible error or altered invoice", "NEEDS_REVIEW")
            else: add("Arithmetic", "pass", "Subtotal + tax equals total")
    except Exception: pass
    if difflib.SequenceMatcher(None, v.lower(), x["vendor"].lower()).ratio() < 0.85:
        add("Approved vendor", "fail", f"'{x['vendor']}' is not on the approved vendor list", "REJECT"); return done()
    x["vendor"] = v; add("Approved vendor", "pass", f"{v} is approved")
    for p in prior:
        if p["vendor"] == v and norm(p["inv"]) == norm(x["invoice_number"]):
            add("Duplicate check", "fail", f"Duplicate of earlier invoice {p['inv']} (run #{p['id']}); numbers match after normalising punctuation", "REJECT"); return done()
    sim = [p for p in prior if p["vendor"] == v and abs(p["total"] - x["total"]) < 0.01]
    if sim: add("Duplicate check", "warn", f"Same vendor and amount as {sim[0]['inv']}; possible re-bill under a new number", "NEEDS_REVIEW")
    else: add("Duplicate check", "pass", "No duplicates found")
    try:
        age = (dt.date.today() - dt.date.fromisoformat(x["invoice_date"])).days
        if age > 90: add("Invoice age", "warn", f"Invoice is {age} days old (limit 90); possible stale or late-billed invoice", "NEEDS_REVIEW")
        else: add("Invoice age", "pass", f"Invoice is {age} days old")
    except Exception: pass
    used = lambda po: sum(p["total"] for p in prior if p["po"] == po)
    rem = lambda po: POS[po][1] - used(po)
    ref, tot, po = x.get("po_reference"), x["total"], None
    if ref and ref in POS and POS[ref][0] == v: po = ref; add("PO match", "pass", f"Matched {po} (explicit reference)")
    elif ref and ref in POS: add("PO match", "fail", f"{ref} belongs to {POS[ref][0]}, not {v}", "NEEDS_REVIEW")
    elif ref: add("PO match", "fail", f"{ref} does not exist", "NEEDS_REVIEW")
    else:
        c = [p for p, (pv, _) in POS.items() if pv == v and rem(p) >= tot - 0.01]
        if len(c) == 1: po = c[0]; add("PO match", "warn", f"No PO on invoice; inferred {po} (only open PO for {v} that fits)", "APPROVE_WITH_NOTE")
        else: add("PO match", "fail", f"No PO on invoice and {len(c)} candidate POs; cannot infer safely", "NEEDS_REVIEW")
    if po:
        r = rem(po)
        if tot <= r + 0.01:
            add("Amount vs PO", "pass", f"${tot:,.2f} within remaining PO balance ${r:,.2f}" + (f"; partial billing, ${r-tot:,.2f} left on {po}" if tot < r - 0.01 else ""))
        elif r > 0 and (tot - r) / r <= TOL:
            add("Amount vs PO", "warn", f"${tot:,.2f} is {(tot-r)/r:.1%} over remaining ${r:,.2f}; within {TOL:.0%} tolerance", "APPROVE_WITH_NOTE")
        else:
            add("Amount vs PO", "fail", f"${tot:,.2f} exceeds remaining balance ${r:,.2f} on {po} (${used(po):,.2f} already billed of ${POS[po][1]:,.2f}); over {TOL:.0%} tolerance", "NEEDS_REVIEW")
    return done(po)

# ---------- UI ----------
st.set_page_config("AP Invoice Agent", "🧾", layout="wide"); make_samples()
st.sidebar.markdown('''
<div class="brand">
    <div class="brand-mark" aria-hidden="true">AP</div>
    <div><div class="brand-name">Invoice Agent</div><div class="brand-sub">Accounts payable</div></div>
</div>
''', unsafe_allow_html=True)
st.sidebar.title("Navigate")
page = st.sidebar.radio("View", ["Process invoice", "Dashboard", "Rules & assumptions"], label_visibility="collapsed")
st.sidebar.markdown('<div class="side-label">Appearance</div>', unsafe_allow_html=True)
dark = st.sidebar.toggle("Dark mode", key="dark")
inject_theme(dark)
# st.sidebar.caption("AI: " + (f"Claude ({MODEL})" if os.getenv("ANTHROPIC_API_KEY") else "no API key, regex fallback"))
if st.sidebar.button("Reset demo data"): db().execute("delete from runs").connection.commit(); st.rerun()

if page == "Process invoice":
    render_page_header(
        "Process an invoice",
        "The AI reads the PDF. Fixed policy rules then decide whether to approve, review, or reject it.",
        badge="Process invoice"
    )
    a, b = st.columns(2)
    up = a.file_uploader("Upload invoice PDF", type="pdf")
    pick = b.selectbox("...or choose a demo invoice", [""] + sorted(os.listdir(SDIR)))
    run = st.button("▶ Run", type="primary")
    if run and not (up or pick):
        st.warning("Upload a PDF or choose a demo invoice, then select Run.")
    elif run:
        name, raw = (up.name, up.read()) if up else (pick, open(os.path.join(SDIR, pick), "rb").read())
        st.subheader("Live run")
        with st.status("1. Ingest", state="complete"): st.write(f"{name}, {len(raw)/1024:.1f} KB")
        with st.status("2. Extract (AI reads the document)", expanded=True) as s:
            x = extract(raw); time.sleep(.4); render_json(x); s.update(state="complete")
        x_total = x.get("total"); x["total"] = float(x_total) if x_total else None
        decision, reason, checks, po, email = evaluate(x, prior_runs())
        for i, c in enumerate(checks, 3):
            with st.status(f"{i}. {c['check']} {ICON[c['status']]}", state="complete" if c["status"] != "fail" else "error"): st.write(c["detail"])
            time.sleep(.35)
        render_verdict(decision, reason)
        if email: st.text_area("Drafted email to vendor", email, height=200)
        c = db(); c.execute("insert into runs(ts,file,vendor,inv,total,po,decision,reason,data) values(?,?,?,?,?,?,?,?,?)",
            (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, x.get("vendor"), x.get("invoice_number"), x.get("total"), po, decision, reason,
             json.dumps({"extracted": x, "checks": checks, "email": email}))); c.commit(); c.close()
    else:
        st.markdown('''<div class="empty"><strong>No invoice processed yet</strong>
        Upload a PDF or pick a demo invoice above, then select Run. Each step of the review appears here as it happens.</div>''', unsafe_allow_html=True)

elif page == "Dashboard":
    render_page_header(
        "Performance dashboard",
        "See how invoices have been decided so far and how much of each purchase order is billed.",
        badge="Dashboard"
    )
    c = db(); df = pd.read_sql("select * from runs order by id desc", c); c.close()
    if df.empty:
        st.markdown('''<div class="empty"><strong>No runs yet</strong>
        Process an invoice from the Process invoice view and its decision will show up here.</div>''', unsafe_allow_html=True)
    else:
        m = st.columns(4); m[0].metric("Runs", len(df)); m[1].metric("Auto-approved", int(df.decision.str.startswith("APPROVE").sum()))
        m[2].metric("Needs a human", int(df.decision.isin(["NEEDS_REVIEW", "REQUEST_INFO"]).sum())); m[3].metric("Rejected", int((df.decision == "REJECT").sum()))
        st.subheader("Decisions")
        render_bars(df.decision.value_counts().to_dict())
        st.subheader("All runs")
        html_table(df, [("id", "Run", "dim"), ("ts", "Time", "dim"), ("file", "File", "text"), ("vendor", "Vendor", "text"), ("inv", "Invoice", "text"), ("total", "Total", "money"), ("po", "PO", "text"), ("decision", "Decision", "chip")])
        st.subheader("Inspect a run")
        rid = st.selectbox("Run", df.id); r = df[df.id == rid].iloc[0]
        render_verdict(r.decision, r.reason)
        render_checks(json.loads(r.data)["checks"])
        st.subheader("PO balances")
        bal = pd.DataFrame([{"PO": p, "vendor": v, "amount": a, "billed": sum(x.total for x in df.itertuples() if x.po == p and x.decision.startswith("APPROVE"))} for p, (v, a) in POS.items()])
        bal["% billed"] = (bal["billed"] / bal["amount"] * 100).round(0)
        html_table(bal, [("PO", "PO", "text"), ("vendor", "Vendor", "text"), ("amount", "PO amount", "money"), ("billed", "Billed", "money"), ("% billed", "Used", "meter")])
else:
    render_page_header(
        "Rules & assumptions",
        "AI handles the messy PDF. Plain code makes every money decision, so each one can be audited.",
        badge="Policy"
    )
    st.markdown(f'''
    <div class="principle">The AI never decides. It extracts fields and reports how confident it is. The rules below decide, and every run records which ones fired.</div>
    <div class="policy-wrap"><table class="policy">
      <thead><tr><th>When</th><th>Outcome</th></tr></thead>
      <tbody>
        <tr><td>Vendor name, invoice number, or total is missing</td><td>Request info, with a drafted email to the vendor instead of guessing</td></tr>
        <tr><td>Vendor is not on the approved list (fuzzy match)</td><td>Reject</td></tr>
        <tr><td>Same vendor and same invoice number after normalising (<code>INV-0042</code> = <code>INV0042</code>)</td><td>Reject as duplicate</td></tr>
        <tr><td>Same vendor and amount under a new invoice number</td><td>Needs review</td></tr>
        <tr><td>Invoice is more than 90 days old</td><td>Needs review</td></tr>
        <tr><td>PO is named but does not exist or belongs to another vendor</td><td>Needs review</td></tr>
        <tr><td>No PO named</td><td>Inferred only if exactly one open PO fits, with a note. Otherwise needs review</td></tr>
        <tr><td>Amount is over the <em>remaining</em> PO balance by up to <strong>{TOL:.0%}</strong></td><td>Approve with note</td></tr>
        <tr><td>Amount is over the remaining PO balance by more than <strong>{TOL:.0%}</strong></td><td>Needs review</td></tr>
        <tr><td>Extraction confidence is below <strong>{CONF_MIN:.0%}</strong></td><td>Needs review</td></tr>
      </tbody>
    </table></div>
    <p class="note">Assumptions: {TOL:.0%} tolerance, USD, English invoices. The remaining PO balance tracks split invoices across runs.
    Limits: no email ingestion or ERP connection, single currency.</p>
    ''', unsafe_allow_html=True)