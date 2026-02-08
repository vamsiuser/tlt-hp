import os
import json
from io import BytesIO
from datetime import date, timedelta, datetime

import pandas as pd
import streamlit as st

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

# PNG
import matplotlib.pyplot as plt

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

#whats app
import urllib.parse
import webbrowser

from google.oauth2.service_account import Credentials
import streamlit as st
import gspread


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="HP Bunk Daily Calculator", layout="wide")

GSHEET_ID = "1zW5y3xMNCFd5cvbaIy7VKkOD3aUDtAHbEXNytqWNYTE"

SUMMARY_SHEET = "Summary"
SETTINGS_SHEET = "Settings"
LEDGER_SHEET = "Ledger"
LEDGER_LOG_SHEET = "Ledger_Log"

DATA_DIR = "hp_bunk_data"
EXCEL_FILE = os.path.join(DATA_DIR, "hp_bunk_daily.xlsx")
os.makedirs(DATA_DIR, exist_ok=True)


# =========================
# HELPERS
# =========================

def whatsapp_share(message: str):
    text = urllib.parse.quote(message)
    url = f"https://wa.me/?text={text}"
    webbrowser.open_new_tab(url)
    
def whatsapp_url(message: str) -> str:
    text = urllib.parse.quote(message)
    return f"https://wa.me/?text={text}"
    
def n(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def money(x) -> float:
    return round(float(x), 2)


def liters(x) -> float:
    return round(float(x), 3)


def date_str(d: date) -> str:
    return d.isoformat()


def parse_date(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def col_letter(num: int) -> str:
    s = ""
    while num:
        num, r = divmod(num - 1, 26)
        s = chr(65 + r) + s
    return s


def safe_float_cell(v) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, str) and v.strip() == "":
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def clean_rows(rows, key1: str, key2: str):
    """Keep only meaningful rows: non-empty name + amount > 0, normalize amount to 2 decimals."""
    out = []
    for r in rows or []:
        name = (r.get(key1) or "").strip()
        amt = safe_float_cell(r.get(key2))
        if name and amt > 0:
            out.append({key1: name, key2: money(amt)})
    return out


# =========================
# GOOGLE (connection cached, NOT data)
# =========================
@st.cache_resource
def _open_spreadsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if "gcp_service_account" not in st.secrets:
        st.error("❌ Missing Streamlit secret: [gcp_service_account].")
        st.stop()

    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        client = gspread.authorize(creds)
       
        # Try opening sheet
        sh = client.open_by_key(GSHEET_ID)
        return sh

    except Exception as e:
        st.error("❌ Failed to open Google Sheet by key.")
        import traceback
        st.text(traceback.format_exc())
        st.stop()

    
def get_sh():
    try:
        sh = _open_spreadsheet()
        if sh is None:
            st.error("❌ Failed to open Google Sheet. Please check credentials + sheet access.")
            st.stop()
        return sh
    except Exception as e:
        st.error("❌ Google Sheets connection failed.")
        st.exception(e)
        st.stop()


def ensure_headers(ws, headers):
    first = ws.row_values(1)
    if first != headers:
        ws.update("A1", [headers])


def safe_worksheet(sh, name: str, headers: list[str]):
    if sh is None:
        st.error("❌ Spreadsheet handle is None. Google auth/open_by_key failed.")
        st.stop()

    try:
        ws = sh.worksheet(name)
    except Exception:
        st.error(f"❌ Worksheet '{name}' not found.")
        st.info(
            f"Create a sheet tab named '{name}' manually and set row 1 headers:\n\n"
            + ", ".join(headers)
        )
        st.stop()

    ensure_headers(ws, headers)
    return ws


# =========================
# SETTINGS (NO PIN)
# =========================
def read_settings_from_google() -> dict:
    sh = get_sh()
    ws = safe_worksheet(sh, SETTINGS_SHEET, ["Key", "Value"])

    rows = ws.get_all_records()
    d = {"employees": [], "customers": [], "expense_names": [], "oil_prices": []}

    for r in rows:
        k = (r.get("Key") or "").strip()
        v = r.get("Value")
        if not k:
            continue

        if k in ("employees", "customers", "expense_names", "oil_prices"):
            try:
                parsed = json.loads(v) if isinstance(v, str) else v
                if k == "oil_prices":
                    parsed = [float(x) for x in parsed]
                d[k] = list(parsed)
            except Exception:
                # ignore bad rows
                pass

    d["employees"] = [str(x).strip() for x in d.get("employees", []) if str(x).strip()]
    d["customers"] = [str(x).strip() for x in d.get("customers", []) if str(x).strip()]
    d["expense_names"] = [str(x).strip() for x in d.get("expense_names", []) if str(x).strip()]
    d["oil_prices"] = sorted(list({float(x) for x in d.get("oil_prices", [])})) if d.get("oil_prices") else []
    return d


def write_settings_to_google(settings: dict):
    sh = get_sh()
    ws = safe_worksheet(sh, SETTINGS_SHEET, ["Key", "Value"])

    payload = [
        ["employees", json.dumps(settings.get("employees", []), ensure_ascii=False)],
        ["customers", json.dumps(settings.get("customers", []), ensure_ascii=False)],
        ["expense_names", json.dumps(settings.get("expense_names", []), ensure_ascii=False)],
        ["oil_prices", json.dumps(settings.get("oil_prices", []), ensure_ascii=False)],
    ]

    ws.clear()
    ws.update("A1", [["Key", "Value"]])
    ws.update("A2", payload)


# =========================
# SUMMARY MODEL
# =========================
def summary_headers():
    return [
        "date",
        "employee_name",
        "notes",
        "p_open", "p_close", "p_test", "p_rate",
        "d_open", "d_close", "d_test", "d_rate",
        "petrol_liters_sold", "petrol_amount",
        "diesel_liters_sold", "diesel_amount",
        "oil_packets", "oil_price", "oil_amount",
        "qr_amount", "advance_paid", "owner_phonepay_amount", "yesterday_balance_amount",
        "customer_credit_total", "debt_collections_total", "other_expenses_total",
        "total_sales", "cash_to_deposit",
        "details_json",
    ]


def build_summary_row(report: dict) -> dict:
    details = {
        "customer_credit_rows": clean_rows(report.get("customer_credit_rows", []), "Customer", "Amount"),
        "debt_collection_rows": clean_rows(report.get("debt_collection_rows", []), "Customer", "Amount"),
        "other_expense_rows": clean_rows(report.get("other_expense_rows", []), "Expense", "Amount"),
    }

    return {
        "date": report["date"],
        "employee_name": report.get("employee_name", ""),
        "notes": report.get("notes", ""),

        "p_open": report.get("p_open", 0.0),
        "p_close": report.get("p_close", 0.0),
        "p_test": report.get("p_test", 0.0),
        "p_rate": report.get("p_rate", 0.0),

        "d_open": report.get("d_open", 0.0),
        "d_close": report.get("d_close", 0.0),
        "d_test": report.get("d_test", 0.0),
        "d_rate": report.get("d_rate", 0.0),

        "petrol_liters_sold": report.get("petrol_liters_sold", 0.0),
        "petrol_amount": report.get("petrol_amount", 0.0),
        "diesel_liters_sold": report.get("diesel_liters_sold", 0.0),
        "diesel_amount": report.get("diesel_amount", 0.0),

        "oil_packets": report.get("oil_packets", 0),
        "oil_price": report.get("oil_price", 0.0),
        "oil_amount": report.get("oil_amount", 0.0),

        "qr_amount": report.get("qr_amount", 0.0),
        "advance_paid": report.get("advance_paid", 0.0),
        "owner_phonepay_amount": report.get("owner_phonepay_amount", 0.0),
        "yesterday_balance_amount": report.get("yesterday_balance_amount", 0.0),

        "customer_credit_total": report.get("customer_credit_total", 0.0),
        "debt_collections_total": report.get("debt_collections_total", 0.0),
        "other_expenses_total": report.get("other_expenses_total", 0.0),

        "total_sales": report.get("total_sales", 0.0),
        "cash_to_deposit": report.get("cash_to_deposit", 0.0),

        "details_json": json.dumps(details, ensure_ascii=False),
    }


def fetch_summary_by_date(d: date):
    sh = get_sh()
    ws = safe_worksheet(sh, SUMMARY_SHEET, summary_headers())

    colA = ws.col_values(1)
    dates = colA[1:] if len(colA) > 1 else []
    ds = date_str(d)

    if ds not in dates:
        return None, None

    row_no = dates.index(ds) + 2
    values = ws.row_values(row_no)
    headers = summary_headers()
    if len(values) < len(headers):
        values = values + [""] * (len(headers) - len(values))

    row = dict(zip(headers, values))
    return row, row_no


def upsert_summary_to_google(report: dict):
    sh = get_sh()
    ws = safe_worksheet(sh, SUMMARY_SHEET, summary_headers())

    headers = summary_headers()
    ds = report["date"]

    colA = ws.col_values(1)
    dates = colA[1:] if len(colA) > 1 else []

    row_data = build_summary_row(report)
    values = [row_data.get(h, "") for h in headers]
    last_col = col_letter(len(headers))

    if ds in dates:
        row_no = dates.index(ds) + 2
        ws.update(f"A{row_no}:{last_col}{row_no}", [values], value_input_option="USER_ENTERED")
        return "updated"

    ws.append_row(values, value_input_option="USER_ENTERED")
    return "appended"


# =========================
# LEDGER (Standalone system)
# =========================
def ledger_headers():
    return ["Customer", "Outstanding"]


def ledger_log_headers():
    return [
        "Log_Timestamp",
        "Entry_Date",
        "Type",            # CREDIT or PAYMENT
        "Customer",
        "Amount",
        "Balance_Before",
        "Balance_After",
        "Employee",
        "Notes",
    ]


def load_ledger() -> pd.DataFrame:
    sh = get_sh()
    ws = safe_worksheet(sh, LEDGER_SHEET, ledger_headers())
    df = pd.DataFrame(ws.get_all_records())
    if df.empty:
        return pd.DataFrame(columns=["Customer", "Outstanding"])
    df["Customer"] = df.get("Customer", "").astype(str).str.strip()
    df["Outstanding"] = pd.to_numeric(df.get("Outstanding", 0), errors="coerce").fillna(0.0)
    df = df[df["Customer"] != ""].copy()
    df = df.sort_values(["Outstanding", "Customer"], ascending=[False, True]).reset_index(drop=True)
    return df


def save_ledger(df: pd.DataFrame):
    sh = get_sh()
    ws = safe_worksheet(sh, LEDGER_SHEET, ledger_headers())

    out = df.copy()
    if out.empty:
        ws.clear()
        ws.update("A1", [ledger_headers()])
        return

    out["Customer"] = out["Customer"].astype(str).str.strip()
    out["Outstanding"] = pd.to_numeric(out["Outstanding"], errors="coerce").fillna(0.0)
    out = out[out["Customer"] != ""].copy()
    out = out.sort_values(["Outstanding", "Customer"], ascending=[False, True]).reset_index(drop=True)

    ws.clear()
    ws.update("A1", [ledger_headers()])
    ws.update("A2", out[["Customer", "Outstanding"]].values.tolist())


def append_ledger_log(entry_date: date, typ: str, customer: str, amount: float,
                      before: float, after: float, employee: str, notes: str):
    sh = get_sh()
    ws = safe_worksheet(sh, LEDGER_LOG_SHEET, ledger_log_headers())

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        date_str(entry_date),
        typ,
        customer,
        money(amount),
        money(before),
        money(after),
        employee,
        notes,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")


def load_ledger_logs(limit: int = 5000) -> pd.DataFrame:
    sh = get_sh()
    ws = safe_worksheet(sh, LEDGER_LOG_SHEET, ledger_log_headers())
    df = pd.DataFrame(ws.get_all_records())
    if df.empty:
        return pd.DataFrame(columns=ledger_log_headers())
    if "Log_Timestamp" in df.columns:
        df["Log_Timestamp"] = pd.to_datetime(df["Log_Timestamp"], errors="coerce")
        df = df.sort_values("Log_Timestamp", ascending=False)
    return df.head(limit).reset_index(drop=True)


def apply_ledger_transaction(ledger_df: pd.DataFrame, customer: str, typ: str, amount: float) -> tuple[pd.DataFrame, float, float]:
    """Returns (new_df, before, after).
       CREDIT: increases outstanding
       PAYMENT: decreases outstanding (not below 0)
    """
    customer = (customer or "").strip()
    if not customer:
        raise ValueError("Customer is empty")
    if amount <= 0:
        raise ValueError("Amount must be > 0")

    df = ledger_df.copy()
    if df.empty:
        df = pd.DataFrame(columns=["Customer", "Outstanding"])

    df["Customer"] = df.get("Customer", "").astype(str).str.strip()
    df["Outstanding"] = pd.to_numeric(df.get("Outstanding", 0), errors="coerce").fillna(0.0)

    exists = df["Customer"].eq(customer).any()
    before = float(df.loc[df["Customer"].eq(customer), "Outstanding"].iloc[0]) if exists else 0.0

    if typ == "CREDIT":
        after = before + amount
    elif typ == "PAYMENT":
        after = max(0.0, before - amount)
    else:
        raise ValueError("Type must be CREDIT or PAYMENT")

    if exists:
        df.loc[df["Customer"].eq(customer), "Outstanding"] = after
    else:
        df = pd.concat([df, pd.DataFrame([{"Customer": customer, "Outstanding": after}])], ignore_index=True)

    df = df[df["Customer"].astype(str).str.strip() != ""].copy()
    df = df.sort_values(["Outstanding", "Customer"], ascending=[False, True]).reset_index(drop=True)
    return df, before, after


# =========================
# EXCEL
# =========================
def upsert_excel(report: dict):
    row = build_summary_row(report)
    headers = summary_headers()
    new_df = pd.DataFrame([[row.get(h, "") for h in headers]], columns=headers)

    if os.path.exists(EXCEL_FILE):
        try:
            old = pd.read_excel(EXCEL_FILE, sheet_name="Summary")
        except Exception:
            old = pd.DataFrame(columns=headers)
    else:
        old = pd.DataFrame(columns=headers)

    if not old.empty and "date" in old.columns:
        old["date"] = old["date"].astype(str)
        old = old[old["date"] != str(report["date"])]

    out = pd.concat([old, new_df], ignore_index=True)
    out["date"] = out["date"].astype(str)
    out = out.sort_values("date").reset_index(drop=True)

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="w") as writer:
        out.to_excel(writer, sheet_name="Summary", index=False)


# =========================
# YESTERDAY LOAD
# =========================
def load_yesterday_closing_to_opening(today_d: date):
    yday = today_d - timedelta(days=1)
    row, _ = fetch_summary_by_date(yday)
    if not row:
        st.warning("Yesterday data not found in Google Sheet.")
        return

    st.session_state["p_open"] = safe_float_cell(row.get("p_close"))
    st.session_state["d_open"] = safe_float_cell(row.get("d_close"))
    st.session_state["p_rate"] = safe_float_cell(row.get("p_rate"))
    st.session_state["d_rate"] = safe_float_cell(row.get("d_rate"))

    st.success("Loaded yesterday closing into today opening.")
    st.rerun()

def reset_daily_entry_state():
    # basic fields
    st.session_state["employee_name"] = ""
    st.session_state["notes"] = ""

    # fuel
    st.session_state["p_open"] = 0.0
    st.session_state["p_close"] = 0.0
    st.session_state["p_test"] = 5.0
    st.session_state["p_rate"] = 0.0

    st.session_state["d_open"] = 0.0
    st.session_state["d_close"] = 0.0
    st.session_state["d_test"] = 5.0
    st.session_state["d_rate"] = 0.0

    # oil + payments
    st.session_state["oil_packets"] = 0
    st.session_state["oil_price"] = 0.0
    st.session_state["qr_amount"] = 0.0
    st.session_state["advance_paid"] = 0.0
    st.session_state["owner_phonepay_amount"] = 0.0
    st.session_state["yesterday_balance_amount"] = 0.0

    # tables
    st.session_state["credit_df"] = pd.DataFrame([{"Customer": "", "Amount": 0.0}])
    st.session_state["debt_df"] = pd.DataFrame([{"Customer": "", "Amount": 0.0}])
    st.session_state["exp_df"] = pd.DataFrame([{"Expense": "", "Amount": 0.0}])


# =========================
# PDF / PNG
# =========================
def pdf_bytes(report: dict) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    PRIMARY = HexColor("#111111")
    GRAY = HexColor("#555555")
    LINE = HexColor("#DDDDDD")
    RED = HexColor("#C62828")
    GREEN = HexColor("#2E7D32")

    def text(x, y, s, size=11, bold=False, color=PRIMARY):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, s)

    def rtext(x, y, s, size=11, bold=False, color=PRIMARY):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawRightString(x, y, s)

    def hline(y):
        c.setStrokeColor(LINE)
        c.setLineWidth(1)
        c.line(15 * mm, y, W - 15 * mm, y)

    def row_line(label, value, big=False, color=PRIMARY):
        nonlocal y
        size = 14 if big else 12
        text(20 * mm, y, label, size=size)
        rtext(W - 20 * mm, y, value, size=size, bold=big, color=color)
        y -= 7 * mm

    y = H - 20 * mm
    text(15 * mm, y, "HP PETROL BUNK", size=18, bold=True)
    y -= 8 * mm
    text(15 * mm, y, "Daily Sales Statement", size=14, bold=True, color=GRAY)

    y -= 10 * mm
    hline(y)
    y -= 10 * mm

    text(15 * mm, y, "Date:", bold=True)
    text(35 * mm, y, report["date"])
    rtext(W - 15 * mm, y, f"Employee: {report.get('employee_name','')}", bold=True)

    y -= 8 * mm
    hline(y)
    y -= 10 * mm

    text(15 * mm, y, "FUEL SALES", size=13, bold=True)
    y -= 8 * mm
    row_line("Petrol Liters Sold", f"{report['petrol_liters_sold']:.3f} L")
    row_line("Petrol Amount", f"₹ {report['petrol_amount']:.2f}")
    row_line("Diesel Liters Sold", f"{report['diesel_liters_sold']:.3f} L")
    row_line("Diesel Amount", f"₹ {report['diesel_amount']:.2f}")

    y -= 6 * mm
    hline(y)
    y -= 8 * mm

    text(15 * mm, y, "OTHER SALES", size=13, bold=True)
    y -= 8 * mm
    row_line("2T Oil Packets", f"{int(report.get('oil_packets', 0))}")
    row_line("2T Oil Price", f"₹ {report.get('oil_price', 0.0):.2f}")
    row_line("2T Oil Amount", f"₹ {report['oil_amount']:.2f}")

    y -= 6 * mm
    hline(y)
    y -= 8 * mm

    text(15 * mm, y, "CASH FLOW", size=13, bold=True)
    y -= 8 * mm
    row_line("Total Sales", f"₹ {report['total_sales']:.2f}", big=True)

    row_line("QR / UPI", f"- ₹ {report['qr_amount']:.2f}", color=RED)
    row_line("Advance Paid", f"- ₹ {report['advance_paid']:.2f}", color=RED)
    row_line("Owner PhonePay", f"- ₹ {report.get('owner_phonepay_amount', 0.0):.2f}", color=RED)
    row_line("Expenses", f"- ₹ {report['other_expenses_total']:.2f}", color=RED)
    row_line("Credit Given", f"- ₹ {report['customer_credit_total']:.2f}", color=RED)

    row_line("Collections", f"+ ₹ {report['debt_collections_total']:.2f}", color=GREEN)
    row_line("Yesterday Balance", f"+ ₹ {report.get('yesterday_balance_amount', 0.0):.2f}", color=GREEN)

    y -= 4 * mm
    hline(y)
    y -= 10 * mm

    text(15 * mm, y, "CASH TO DEPOSIT", size=16, bold=True, color=GREEN)
    rtext(W - 15 * mm, y, f"₹ {report['cash_to_deposit']:.2f}", size=20, bold=True, color=GREEN)

    c.showPage()
    c.save()
    return buf.getvalue()


def png_bytes(report: dict) -> bytes:
    fig = plt.figure(figsize=(7.5, 9.5), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")

    def put(y, left, right=None, size=12, bold=False):
        ax.text(0.05, y, left, fontsize=size,
                fontweight=("bold" if bold else "normal"),
                va="top", family="DejaVu Sans")
        if right is not None:
            ax.text(0.95, y, right, fontsize=size,
                    fontweight=("bold" if bold else "normal"),
                    va="top", ha="right", family="DejaVu Sans")

    def line(y):
        ax.plot([0.05, 0.95], [y, y], linewidth=1)

    y = 0.97
    put(y, "HP PETROL BUNK", size=18, bold=True); y -= 0.045
    put(y, "Daily Sales Statement", size=13, bold=True); y -= 0.04

    put(y, "Date", report["date"], bold=True); y -= 0.03
    put(y, "Employee", report.get("employee_name", ""), bold=True); y -= 0.03
    
    if report.get("notes"):
        put(y, "Notes", str(report.get("notes"))[:80]); y -= 0.03

    y -= 0.01
    line(y); y -= 0.03

    put(y, "FUEL SALES", size=13, bold=True); y -= 0.035

    put(y, f"Petrol ({p_rate})", f"{report['petrol_liters_sold']:.3f} L (O:{report['p_open']:.3f} C:{report['p_close']:.3f} T:{report['p_test']:.3f}) | ₹ {report['petrol_amount']:.2f}"); y -= 0.028

    put(y, f"Diesel ({d_rate})", f"{report['diesel_liters_sold']:.3f} L (O:{report['d_open']:.3f} C:{report['d_close']:.3f} T:{report['d_test']:.3f}) | ₹ {report['diesel_amount']:.2f}"); y -= 0.04
    
    put(y, "2T oil SALES", size=13, bold=True); y -= 0.035
    put(y, "Packets", f"{int(report.get('oil_packets', 0))} | price (₹ {report.get('oil_price', 0.0):.2f}) - Total ₹ {report['oil_amount']:.2f}"); y -= 0.028

    line(y); y -= 0.03
    put(y, "TOTAL SALES", f"₹ {report['total_sales']:.2f}", size=14, bold=True); y -= 0.04

    put(y, "DEDUCTIONS / ADJUSTMENTS", size=13, bold=True); y -= 0.035
    put(y, "QR / UPI", f"- ₹ {report['qr_amount']:.2f}"); y -= 0.028
    put(y, "Advance Paid", f"- ₹ {report['advance_paid']:.2f}"); y -= 0.028
    put(y, "Owner PhonePay", f"- ₹ {report.get('owner_phonepay_amount', 0.0):.2f}"); y -= 0.028
    put(y, "Expenses", f"- ₹ {report['other_expenses_total']:.2f}"); y -= 0.028
    put(y, "Credit Given", f"- ₹ {report['customer_credit_total']:.2f}"); y -= 0.028
    put(y, "Collections", f"+ ₹ {report['debt_collections_total']:.2f}"); y -= 0.028
    put(y, "Yesterday Balance", f"+ ₹ {report.get('yesterday_balance_amount', 0.0):.2f}"); y -= 0.04

    line(y); y -= 0.03
    put(y, "CASH TO DEPOSIT", f"₹ {report['cash_to_deposit']:.2f}", size=15, bold=True); y -= 0.03

    out = BytesIO()
    fig.savefig(out, format="png", bbox_inches="tight")
    plt.close(fig)
    return out.getvalue()


# =========================
# APP STATE (INIT)
# =========================
if "settings" not in st.session_state:
    st.session_state.settings = {}

if "settings_loaded" not in st.session_state:
    st.session_state.settings_loaded = False

# Auto-load settings globally ONCE (fixes dropdown dependency)
if not st.session_state.settings_loaded:
    st.session_state.settings = read_settings_from_google()
    st.session_state.settings_loaded = True

if "edit_mode" not in st.session_state:
    st.session_state.edit_mode = False

# form tables
if "credit_df" not in st.session_state:
    st.session_state.credit_df = pd.DataFrame([{"Customer": "", "Amount": 0.0}])
if "debt_df" not in st.session_state:
    st.session_state.debt_df = pd.DataFrame([{"Customer": "", "Amount": 0.0}])
if "exp_df" not in st.session_state:
    st.session_state.exp_df = pd.DataFrame([{"Expense": "", "Amount": 0.0}])


# =========================
# UI
# =========================
st.title("⛽ HP Petrol Bunk — Daily Sales Calculator")
st.caption("Daily Entry is once per day. Ledger is a separate complete management tab.")

tab_entry, tab_ledger, tab_reports = st.tabs(["🧾 Daily Entry", "📒 Ledger", "📊 Reports"])


# =========================
# SIDEBAR SETTINGS (NO PIN)
# =========================
with st.sidebar:
    st.header("⚙️ Settings (Google)")
    st.caption("Settings auto-load at app start. Use refresh/save if needed.")

    if st.button("🔄 Refresh Settings from Google", width='stretch'):
        st.session_state.settings = read_settings_from_google()
        st.success("Settings refreshed.")

    settings = st.session_state.settings

    st.divider()
    st.subheader("Lists")

    emp_text = st.text_area("Employees (one per line)", value="\n".join(settings.get("employees", [])), height=110)
    cust_text = st.text_area("Customers (one per line)", value="\n".join(settings.get("customers", [])), height=150)
    exp_text = st.text_area("Expense Names (one per line)", value="\n".join(settings.get("expense_names", [])), height=150)
    oil_text = st.text_area("2T Oil Prices (one per line)", value="\n".join([str(x) for x in settings.get("oil_prices", [])]), height=110)

    if st.button("💾 Save Settings to Google", width='stretch'):
        new_settings = {
            "employees": [x.strip() for x in emp_text.splitlines() if x.strip()],
            "customers": [x.strip() for x in cust_text.splitlines() if x.strip()],
            "expense_names": [x.strip() for x in exp_text.splitlines() if x.strip()],
            "oil_prices": sorted(list({float(x.strip()) for x in oil_text.splitlines() if x.strip()})),
        }
        write_settings_to_google(new_settings)
        st.session_state.settings = new_settings
        st.success("Saved Settings.")


# =========================
# DAILY ENTRY TAB
# =========================
with tab_entry:
    settings = st.session_state.settings

    st.subheader("Step 1 — Select date & Fetch (manual)")
    c1, c2, c3, c4 = st.columns([1.1, 1.3, 1.4, 1.2])

    with c1:
        entry_date = st.date_input("Date", value=date.today(), key="entry_date")

    with c2:
        if st.button("📥 Fetch Data", width='stretch'):
            row, _ = fetch_summary_by_date(entry_date)

            if not row:
                st.session_state.edit_mode = False
                st.info("No data exists for this date. Enter new data and Save.")
                reset_daily_entry_state()
                st.info("No data exists for this date. Enter new data and Save.")
                st.rerun()
            else:
                st.session_state.edit_mode = True

                st.session_state["employee_name"] = str(row.get("employee_name") or "")
                st.session_state["notes"] = str(row.get("notes") or "")

                st.session_state["p_open"] = safe_float_cell(row.get("p_open"))
                st.session_state["p_close"] = safe_float_cell(row.get("p_close"))
                st.session_state["p_test"] = safe_float_cell(row.get("p_test") or 5.0)
                st.session_state["p_rate"] = safe_float_cell(row.get("p_rate"))

                st.session_state["d_open"] = safe_float_cell(row.get("d_open"))
                st.session_state["d_close"] = safe_float_cell(row.get("d_close"))
                st.session_state["d_test"] = safe_float_cell(row.get("d_test") or 5.0)
                st.session_state["d_rate"] = safe_float_cell(row.get("d_rate"))

                st.session_state["oil_packets"] = int(float(row.get("oil_packets") or 0))
                st.session_state["oil_price"] = safe_float_cell(row.get("oil_price"))
                st.session_state["qr_amount"] = safe_float_cell(row.get("qr_amount"))
                st.session_state["advance_paid"] = safe_float_cell(row.get("advance_paid"))
                st.session_state["owner_phonepay_amount"] = safe_float_cell(row.get("owner_phonepay_amount"))
                st.session_state["yesterday_balance_amount"] = safe_float_cell(row.get("yesterday_balance_amount"))

                dj = row.get("details_json", "") or ""
                try:
                    details = json.loads(dj) if dj else {}
                except Exception:
                    details = {}

                credit_rows = details.get("customer_credit_rows", []) or []
                debt_rows = details.get("debt_collection_rows", []) or []
                exp_rows = details.get("other_expense_rows", []) or []

                st.session_state.credit_df = pd.DataFrame(credit_rows) if credit_rows else pd.DataFrame([{"Customer": "", "Amount": 0.0}])
                st.session_state.debt_df = pd.DataFrame(debt_rows) if debt_rows else pd.DataFrame([{"Customer": "", "Amount": 0.0}])
                st.session_state.exp_df = pd.DataFrame(exp_rows) if exp_rows else pd.DataFrame([{"Expense": "", "Amount": 0.0}])

                st.success("Loaded existing data (EDIT MODE).")
                st.rerun()

    with c3:
        if st.button("↩️ Load Yesterday Closing", width='stretch'):
            load_yesterday_closing_to_opening(entry_date)

    with c4:
        if st.session_state.edit_mode:
            st.warning("EDIT MODE")
        else:
            st.info("NEW ENTRY MODE")

    st.divider()
    st.subheader("Step 2 — Enter / Review data")

    a1, a2 = st.columns([1.2, 2.2])
    with a1:
        emp_list = settings.get("employees", []) if settings.get("employees") else [""]
        emp_current = st.session_state.get("employee_name", "")
        emp_index = emp_list.index(emp_current) if emp_current in emp_list else 0
        employee_name = st.selectbox("Employee", options=emp_list, index=emp_index, key="employee_name")
    with a2:
        notes = st.text_input("Notes (optional)", value=st.session_state.get("notes", ""), key="notes")

    # Fuel
    st.divider()
    st.subheader("Fuel Readings")
    p_col, d_col = st.columns(2)

    with p_col:
        st.markdown("### Petrol")
        p_open = st.number_input("Opening Reading (Petrol)", value=float(st.session_state.get("p_open", 0.0)), step=0.001, format="%.3f", key="p_open")
        p_close = st.number_input("Closing Reading (Petrol)", value=float(st.session_state.get("p_close", 0.0)), step=0.001, format="%.3f", key="p_close")
        p_test = st.number_input("Test/Own Use (Petrol) Liters", value=float(st.session_state.get("p_test", 5.0)), step=0.001, format="%.3f", key="p_test")
        p_rate = st.number_input("Petrol Rate (₹/L)", value=float(st.session_state.get("p_rate", 0.0)), step=0.01, format="%.2f", key="p_rate")

    with d_col:
        st.markdown("### Diesel")
        d_open = st.number_input("Opening Reading (Diesel)", value=float(st.session_state.get("d_open", 0.0)), step=0.001, format="%.3f", key="d_open")
        d_close = st.number_input("Closing Reading (Diesel)", value=float(st.session_state.get("d_close", 0.0)), step=0.001, format="%.3f", key="d_close")
        d_test = st.number_input("Test/Own Use (Diesel) Liters", value=float(st.session_state.get("d_test", 5.0)), step=0.001, format="%.3f", key="d_test")
        d_rate = st.number_input("Diesel Rate (₹/L)", value=float(st.session_state.get("d_rate", 0.0)), step=0.01, format="%.2f", key="d_rate")

    p_liters_sold = n(p_close) - n(p_open) - n(p_test)
    d_liters_sold = n(d_close) - n(d_open) - n(d_test)
    p_amt = p_liters_sold * n(p_rate)
    d_amt = d_liters_sold * n(d_rate)

    # Oil
    st.divider()
    st.subheader("Other Sales")
    o1, o2, o3 = st.columns([1, 1.2, 1])

    with o1:
        oil_packets = st.number_input("2T Oil Packets Sold (count)", value=int(st.session_state.get("oil_packets", 0)), step=1, key="oil_packets")

    with o2:
        oil_prices = settings.get("oil_prices", []) or [0.0]
        default_oil = float(st.session_state.get("oil_price", oil_prices[0] if oil_prices else 0.0))

        # ensure current value is selectable
        if default_oil not in oil_prices:
            oil_prices = sorted(list(set(oil_prices + [default_oil])))

        oil_price = st.selectbox(
            "2T Oil Price (₹)",
            options=oil_prices,
            index=oil_prices.index(default_oil) if default_oil in oil_prices else 0,
            key="oil_price",
        )

    with o3:
        oil_amount = int(oil_packets) * n(oil_price)
        st.metric("2T Oil Amount (₹)", money(oil_amount))

    # Payments
    st.divider()
    st.subheader("Deductions / Payments / Balances")
    qr_amount = st.number_input("QR Code / UPI Amount (₹)", value=float(st.session_state.get("qr_amount", 0.0)), step=100.0, format="%.2f", key="qr_amount")
    advance_paid = st.number_input("Advance Paid (₹)", value=float(st.session_state.get("advance_paid", 0.0)), step=100.0, format="%.2f", key="advance_paid")
    owner_phonepay_amount = st.number_input("Owner PhonePay Amount (₹)", value=float(st.session_state.get("owner_phonepay_amount", 0.0)), step=100.0, format="%.2f", key="owner_phonepay_amount")
    yesterday_balance_amount = st.number_input("Yesterday Balance Amount (₹)", value=float(st.session_state.get("yesterday_balance_amount", 0.0)), step=100.0, format="%.2f", key="yesterday_balance_amount")

    # Multiple Entries
    st.divider()
    st.subheader("Multiple Entries")
    st.caption("These rows are saved and fetched via details_json (credit/collections/expenses).")

    with st.form("entries_form", clear_on_submit=False):
        t1, t2, t3 = st.tabs([
            "Given to customer (subtract)",
            "Collected from customer (add)",
            "Other Expenses (subtract)",
        ])

        cust_options = settings.get("customers", []) or [""]
        exp_options = settings.get("expense_names", []) or [""]

        with t1:
            st.session_state.credit_df = st.data_editor(
                st.session_state.credit_df,
                num_rows="dynamic",
                width='stretch',
                hide_index=True,
                height=300,
                column_config={
                    "Customer": st.column_config.SelectboxColumn("Customer", options=cust_options),
                    "Amount": st.column_config.NumberColumn("Amount (₹)", min_value=0.0, step=10.0, format="%.2f"),
                },
                key="credit_editor_form",
            )

        with t2:
            st.session_state.debt_df = st.data_editor(
                st.session_state.debt_df,
                num_rows="dynamic",
                width='stretch',
                hide_index=True,
                height=300,
                column_config={
                    "Customer": st.column_config.SelectboxColumn("Customer", options=cust_options),
                    "Amount": st.column_config.NumberColumn("Amount (₹)", min_value=0.0, step=10.0, format="%.2f"),
                },
                key="debt_editor_form",
            )

        with t3:
            st.session_state.exp_df = st.data_editor(
                st.session_state.exp_df,
                num_rows="dynamic",
                width='stretch',
                hide_index=True,
                height=300,
                column_config={
                    "Expense": st.column_config.SelectboxColumn("Expense", options=exp_options),
                    "Amount": st.column_config.NumberColumn("Amount (₹)", min_value=0.0, step=10.0, format="%.2f"),
                },
                key="exp_editor_form",
            )

        save_clicked = st.form_submit_button("💾 Save (Google + Excel)")

    # Totals (clean totals from editors)
    credit_total = money(pd.to_numeric(st.session_state.credit_df.get("Amount", 0), errors="coerce").fillna(0).sum())
    debt_total = money(pd.to_numeric(st.session_state.debt_df.get("Amount", 0), errors="coerce").fillna(0).sum())
    other_exp_total = money(pd.to_numeric(st.session_state.exp_df.get("Amount", 0), errors="coerce").fillna(0).sum())

    total_sales = (p_amt + d_amt) + oil_amount
    cash_to_deposit = (
        total_sales
        - (n(qr_amount) + n(advance_paid) + credit_total + other_exp_total + n(owner_phonepay_amount))
        + debt_total
        + n(yesterday_balance_amount)
    )

    st.divider()
    st.subheader("Results")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Petrol Liters Sold", liters(p_liters_sold))
    k2.metric("Diesel Liters Sold", liters(d_liters_sold))
    k3.metric("Total Sales (₹)", money(total_sales))
    k4.metric("Cash to Deposit (₹)", money(cash_to_deposit))

    # Block save if negative sales
    if save_clicked and (p_liters_sold < 0 or d_liters_sold < 0):
        st.error("❌ Save blocked: Petrol or Diesel liters sold is NEGATIVE. Fix readings/test values.")
        st.stop()

    report = {
        "date": date_str(entry_date),
        "employee_name": employee_name,
        "notes": notes,

        "p_open": float(p_open),
        "p_close": float(p_close),
        "p_test": float(p_test),
        "p_rate": float(p_rate),

        "d_open": float(d_open),
        "d_close": float(d_close),
        "d_test": float(d_test),
        "d_rate": float(d_rate),

        "petrol_liters_sold": float(p_liters_sold),
        "petrol_amount": float(p_amt),
        "diesel_liters_sold": float(d_liters_sold),
        "diesel_amount": float(d_amt),

        "oil_packets": int(oil_packets),
        "oil_price": float(oil_price),
        "oil_amount": float(oil_amount),

        "qr_amount": float(qr_amount),
        "advance_paid": float(advance_paid),
        "owner_phonepay_amount": float(owner_phonepay_amount),
        "yesterday_balance_amount": float(yesterday_balance_amount),

        "customer_credit_total": float(credit_total),
        "debt_collections_total": float(debt_total),
        "other_expenses_total": float(other_exp_total),

        "total_sales": float(total_sales),
        "cash_to_deposit": float(cash_to_deposit),

        # store raw editor rows (build_summary_row will clean them)
        "customer_credit_rows": st.session_state.credit_df.to_dict(orient="records"),
        "debt_collection_rows": st.session_state.debt_df.to_dict(orient="records"),
        "other_expense_rows": st.session_state.exp_df.to_dict(orient="records"),
    }

    if save_clicked:
        action = upsert_summary_to_google(report=report)
        upsert_excel(report)
        st.success(f"✅ Saved (Summary {action} + Excel updated)")

    st.divider()
    wa_msg = (
    f"⛽ HP PETROL BUNK\n"
    f"Daily Sales Statement\n\n"

    f"📅 Date: {report['date']}\n"
    f"👤 Employee: {report.get('employee_name','')}\n"
    f"{report.get('notes','')}\n"

    f"🔹 FUEL SALES\n"
    f"Petrol: {report['petrol_liters_sold']:.3f} L "
    f"(O:{report['p_open']:.3f} C:{report['p_close']:.3f} T:{report['p_test']:.3f}) | "
    f"₹ {report['petrol_amount']:.2f}\n"

    f"Diesel: {report['diesel_liters_sold']:.3f} L "
    f"(O:{report['d_open']:.3f} C:{report['d_close']:.3f} T:{report['d_test']:.3f}) | "
    f"₹ {report['diesel_amount']:.2f}\n\n"

    f"🔹 OTHER SALES\n"
    f"2T Oil: {int(report.get('oil_packets',0))} x ₹{report.get('oil_price',0):.2f} = "
    f"₹ {report['oil_amount']:.2f}\n\n"

    f"💰 TOTAL SALES: ₹ {report['total_sales']:.2f}\n\n"

    f"🔻 DEDUCTIONS / ADJUSTMENTS\n"
    f"QR / UPI: - ₹ {report['qr_amount']:.2f}\n"
    f"Advance Paid: - ₹ {report['advance_paid']:.2f}\n"
    f"Owner PhonePay: - ₹ {report.get('owner_phonepay_amount',0):.2f}\n"
    f"Expenses: - ₹ {report['other_expenses_total']:.2f}\n"
    f"Credit Given: - ₹ {report['customer_credit_total']:.2f}\n"
    f"Collections: + ₹ {report['debt_collections_total']:.2f}\n"
    f"Yesterday Balance: + ₹ {report.get('yesterday_balance_amount',0):.2f}\n\n"

    f"✅ CASH TO DEPOSIT: ₹ {report['cash_to_deposit']:.2f}\n\n"
    f"— HP PETROL BUNK"
)

    # Downloads row
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])

    with c1:
        st.download_button(
            "⬇️ PNG",
            data=png_bytes(report),
            file_name=f"hp_bunk_{report['date']}.png",
            mime="image/png",
            use_container_width=True,
        )

    with c2:
        st.download_button(
            "⬇️ PDF",
            data=pdf_bytes(report),
            file_name=f"hp_bunk_{report['date']}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with c3:
        if os.path.exists(EXCEL_FILE):
            with open(EXCEL_FILE, "rb") as f:
                st.download_button(
                    "⬇️ Excel",
                    data=f.read(),
                    file_name="hp_bunk_daily.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        else:
            st.caption("Excel after first Save")

    with c4:
        st.link_button("📤 WhatsApp", whatsapp_url(wa_msg), use_container_width=True)

# =========================
# LEDGER TAB
# =========================
with tab_ledger:
    st.subheader("📒 Ledger Management (Standalone)")
    st.caption("Use this tab to maintain customer outstanding, add credits/payments, and view logs.")
    if "_ledger_df" not in st.session_state:
        st.session_state["_ledger_df"] = load_ledger()

    top1, top2 = st.columns([1.2, 1.2])

    with top1:
        if st.button("🔄 Load Ledger", width='stretch'):
            st.session_state["_ledger_df"] = load_ledger()
            st.success("Ledger loaded.")

    with top2:
        if st.button("📜 Load Ledger Logs", width='stretch'):
            st.session_state["_ledger_logs_df"] = load_ledger_logs()
            st.success("Ledger logs loaded.")

    ledger_df = st.session_state.get("_ledger_df", pd.DataFrame(columns=["Customer", "Outstanding"]))

    total_outstanding = float(pd.to_numeric(ledger_df.get("Outstanding", 0), errors="coerce").fillna(0).clip(lower=0).sum())
    c1, c2 = st.columns(2)
    c1.metric("Total Outstanding (₹)", f"{money(total_outstanding):.2f}")
    c2.metric("Customers in Ledger", f"{len(ledger_df) if ledger_df is not None else 0}")

    st.divider()

    settings = st.session_state.settings
    cust_options = settings.get("customers", []) or []

    left, right = st.columns([1.2, 1.8])

    with left:
        st.markdown("### Add Transaction")
        entry_d = st.date_input("Entry Date", value=date.today(), key="ledger_entry_date")
        typ = st.selectbox("Type", ["CREDIT (Given)", "PAYMENT (Collected)"], index=0)
        customer = st.selectbox("Customer", options=cust_options if cust_options else [""], index=0, key="ledger_customer")
        amount = st.number_input("Amount (₹)", min_value=0.0, step=10.0, format="%.2f", key="ledger_amount")

        emp_list = settings.get("employees", []) if settings.get("employees") else [""]
        emp = st.selectbox("Employee", options=emp_list, index=0, key="ledger_emp")
        notes = st.text_input("Notes (optional)", key="ledger_notes")

        do_apply = st.button("✅ Apply Transaction", width='stretch')

    with right:
        st.markdown("### Current Customer Status")
        if customer and isinstance(customer, str) and customer.strip():
            tmp = load_ledger() if (ledger_df is None or ledger_df.empty) else ledger_df.copy()
            tmp["Customer"] = tmp.get("Customer", "").astype(str).str.strip()
            tmp["Outstanding"] = pd.to_numeric(tmp.get("Outstanding", 0), errors="coerce").fillna(0.0)
            cur = float(tmp.loc[tmp["Customer"].eq(customer.strip()), "Outstanding"].iloc[0]) if tmp["Customer"].eq(customer.strip()).any() else 0.0
            st.metric("Outstanding (₹)", f"{money(cur):.2f}")
        else:
            st.info("Select a customer to view outstanding.")

    if do_apply:
        if not entry_d or not isinstance(entry_d, (date, pd.Timestamp)):
            st.error("❌ Select a valid date.")
        if not customer or not customer.strip():
            st.error("❌ Select a customer.")
        elif amount <= 0:
            st.error("❌ Amount must be > 0.")
        elif emp is None or not isinstance(emp, str) or not emp.strip():
            st.error("❌ Select an employee.")
        else:
            current_ledger = load_ledger()
            tx_type = "CREDIT" if typ.startswith("CREDIT") else "PAYMENT"
            try:
                new_df, before, after = apply_ledger_transaction(current_ledger, customer.strip(), tx_type, float(amount))
                save_ledger(new_df)
                append_ledger_log(entry_d, tx_type, customer.strip(), float(amount), before, after, emp, notes)
                st.session_state["_ledger_df"] = new_df
                st.success(f"✅ Applied {tx_type} for {customer.strip()} | Before ₹{money(before):.2f} → After ₹{money(after):.2f}")
                
                wa_msg_ledger = (
                    f"*⛽ HP PETROL BUNK ARIMENIPADU*\n\n"
                    f"   *{tx_type} Receipt*\n\n"
                    f"Customer: {customer.strip()}\n"
                    f"Date: {date_str(entry_d)}\n"
                    f"Amount: ₹ {money(amount):.2f}\n\n"
                    f"Outstanding Before: ₹ {money(before):.2f}\n"
                    f"Outstanding After: ₹ {money(after):.2f}\n"
                    f"{'Notes: ' + notes if notes else ''}\n"
                    f"— SASI DHAR"
                )

                st.link_button("📤 WhatsApp", whatsapp_url(wa_msg_ledger), use_container_width=True)
            except Exception as e:
                st.error(f"❌ Failed: {e}")

    st.divider()
    st.markdown("### Ledger Table")
    if ledger_df is None or ledger_df.empty:
        st.info("Ledger is empty. Load ledger or apply a transaction.")
    else:
        all_customers = ["(All)"] + sorted(ledger_df["Customer"].astype(str).str.strip().unique().tolist())

        sel_customer = st.selectbox(
            "Filter customer (Ledger)",
            options=all_customers,
            index=0,
            key="ledger_filter_customer",
        )

        view = ledger_df.copy()
        if sel_customer != "(All)":
            view = view[view["Customer"].astype(str).str.strip().eq(sel_customer)].copy()

        st.dataframe(view, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download Ledger CSV",
            data=view.to_csv(index=False).encode("utf-8"),
            file_name="ledger.csv",
            mime="text/csv",
        )

    st.divider()
    st.markdown("### Ledger Logs")
    logs_df = st.session_state.get("_ledger_logs_df", pd.DataFrame(columns=ledger_log_headers()))
    if logs_df is None or logs_df.empty:
        st.info("No logs loaded. Click 'Load Ledger Logs'.")
    else:
        if "Customer" in logs_df.columns:
            log_customers = ["(All)"] + sorted(
                logs_df["Customer"].astype(str).str.strip().replace("", pd.NA).dropna().unique().tolist()
            )
        else:
            log_customers = ["(All)"]

        sel_log_customer = st.selectbox(
            "Filter customer (Logs)",
            options=log_customers,
            index=0,
            key="ledger_logs_filter_customer",
        )

        logs_view = logs_df.copy()
        if sel_log_customer != "(All)" and "Customer" in logs_view.columns:
            logs_view = logs_view[logs_view["Customer"].astype(str).str.strip().eq(sel_log_customer)].copy()

        st.dataframe(logs_view, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download Ledger Logs CSV",
            data=logs_view.to_csv(index=False).encode("utf-8"),
            file_name="ledger_logs.csv",
            mime="text/csv",
        )


# =========================
# REPORTS TAB
# =========================
with tab_reports:
    st.subheader("Reports (Read-only)")
    st.caption("Click Refresh to load from Google.")

    if "reports_df" not in st.session_state:
        st.session_state.reports_df = None

    if st.button("🔄 Refresh Reports from Google", width='stretch'):
        sh = get_sh()
        ws = safe_worksheet(sh, SUMMARY_SHEET, summary_headers())
        st.session_state.reports_df = pd.DataFrame(ws.get_all_records())
        st.success("Reports loaded.")

    df = st.session_state.reports_df
    if df is None or df.empty:
        st.info("Click Refresh Reports to load data.")
    else:
        df2 = df.copy()
        df2["date"] = pd.to_datetime(df2.get("date", ""), errors="coerce")

        pick = st.date_input("Pick any date in the month", value=date.today(), key="month_pick")
        m1 = pd.Timestamp(pick).replace(day=1)
        m2 = (m1 + pd.offsets.MonthBegin(1))

        month_df = df2[(df2["date"] >= m1) & (df2["date"] < m2)].copy().sort_values("date")
        st.markdown("### Monthly Summary")
        st.dataframe(month_df, width='stretch', hide_index=True)

        def safe_sum(col):
            return float(pd.to_numeric(month_df.get(col, 0), errors="coerce").fillna(0).sum())

        c1, c2, c3 = st.columns(3)
        c1.metric("Month Total Sales", f"₹ {money(safe_sum('total_sales')):.2f}")
        c2.metric("Month Cash Deposit", f"₹ {money(safe_sum('cash_to_deposit')):.2f}")
        c3.metric("Month QR Total", f"₹ {money(safe_sum('qr_amount')):.2f}")
