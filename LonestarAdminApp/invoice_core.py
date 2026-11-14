"""
Shared invoice logic for the Lonestar Buckeye Woodworks admin web app.

Pure Python, no web-framework or GUI dependency, so it can be unit-tested and
imported directly by app.py. PDF generation matches the shop's standard
template (logo, brown header bars, tan info boxes) exactly.
"""

import base64
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

import fitz  # PyMuPDF

try:
    import gspread
    import google.auth
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
except ImportError:
    gspread = None
    google = None
    ServiceAccountCredentials = None

try:
    import stripe
except ImportError:
    stripe = None

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

LOGO_PATH = os.path.join(CORE_DIR, "logo_b64.txt")
LOGO_B64 = open(LOGO_PATH).read().strip() if os.path.exists(LOGO_PATH) else ""

PAGE_W, PAGE_H = 595.2756, 841.8898
BROWN = (0.357, 0.227, 0.161)
TAN_BORDER = (0.784, 0.718, 0.651)
TAN_FILL = (0.937, 0.906, 0.871)
WHITE = (1, 1, 1)
BLACK = (0, 0, 0)
ROW_H = 18.0


# ---------------------------------------------------------------------------
# PDF drawing helpers
# ---------------------------------------------------------------------------

def base_y(top, size):
    return top + size * 1.075


def fit_size(txt, max_width, font, base=10.0, min_size=7.0):
    size = base
    while size > min_size and fitz.get_text_length(txt, fontname=font, fontsize=size) > max_width:
        size -= 0.5
    return size


def draw_text(page, x, top, txt, size, font="helv", color=BLACK, max_width=None):
    if max_width is not None:
        size = fit_size(txt, max_width, font, base=size)
    page.insert_text((x, base_y(top, size)), txt, fontsize=size, fontname=font, color=color)


def draw_text_centered(page, top, txt, size, font="helv", color=BLACK):
    w = fitz.get_text_length(txt, fontname=font, fontsize=size)
    x = (PAGE_W - w) / 2
    draw_text(page, x, top, txt, size, font, color)


LINK_BLUE = (0.11, 0.31, 0.62)


def draw_link_text(page, x, top, txt, url, size=10, font="helv", color=LINK_BLUE):
    """Draw text styled as a hyperlink and make it clickable."""
    w = fitz.get_text_length(txt, fontname=font, fontsize=size)
    baseline = base_y(top, size)
    page.insert_text((x, baseline), txt, fontsize=size, fontname=font, color=color)
    page.draw_line((x, baseline + 1.3), (x + w, baseline + 1.3), color=color, width=0.6)
    rect = fitz.Rect(x, top, x + w + 2, top + size * 1.3)
    page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": url})
    return w


def draw_line(page, x0, y0, x1, y1, color=TAN_BORDER, width=1.0):
    page.draw_line((x0, y0), (x1, y1), color=color, width=width)


def draw_rect_fill(page, x0, y0, x1, y1, color):
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=color)


def wrap_text(text, size, font, max_width):
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if fitz.get_text_length(trial, fontname=font, fontsize=size) <= max_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v - round(v)) < 0.001:
        return "${:,.0f}".format(v)
    return "${:,.2f}".format(v)


def build_invoice_pdf_bytes(data):
    """
    data = {
        "company_name": str, "tagline": str,
        "customer": str, "invoice_no": str, "date": str,
        "items": [ {"item":str, "dimensions":str, "qty":str, "unit_price":str, "total":str}, ... ],
        "summary_rows": [ (label, value_str), ... ],
        "zelle": str, "venmo": str,
        "website_url": str, "website_label": str,
        "facebook_url": str, "facebook_label": str,
        "google_review_url": str, "google_review_label": str,
        "note": str,
    }
    Any of zelle/venmo/website_url/facebook_url/google_review_url may be
    left empty (falsy) to omit that line from the invoice.

    Returns the PDF as raw bytes.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    # Logo
    if LOGO_B64:
        logo_bytes = base64.b64decode(LOGO_B64)
        logo_size = 140
        logo_x0 = (PAGE_W - logo_size) / 2
        page.insert_image(fitz.Rect(logo_x0, 78, logo_x0 + logo_size, 78 + logo_size), stream=logo_bytes)
        header_top = 216.7
    else:
        header_top = 100.0

    draw_text_centered(page, header_top, data["company_name"].upper(), 18, font="hebo")
    draw_text_centered(page, header_top + 34.4, data["tagline"], 12, font="hebi")

    # Customer info box
    box_l, box_r = 67.6378, 527.6378
    title_top, title_bot = header_top + 70.3, header_top + 88.3
    rows = [("Customer", data["customer"]), ("Invoice #", data["invoice_no"]), ("Date", data["date"])]

    draw_rect_fill(page, box_l, title_top, box_r, title_bot, BROWN)
    draw_text(page, 73.6, title_top + 2.2, "INVOICE", 10, font="helv", color=WHITE)

    y = title_bot
    for label, val in rows:
        y0 = y
        draw_line(page, box_l, y0, box_r, y0, TAN_BORDER)
        draw_text(page, 73.6, y0 + 2.1, label, 10, font="helv", color=BLACK, max_width=130)
        draw_text(page, 213.6, y0 + 2.1, val, 10, font="helv", color=BLACK, max_width=300)
        y = y0 + ROW_H
    draw_line(page, box_l, y, box_r, y, TAN_BORDER)
    draw_line(page, box_l, title_top, box_l, y, TAN_BORDER)
    draw_line(page, box_r, title_top, box_r, y, TAN_BORDER)
    draw_line(page, 207.6378, title_bot, 207.6378, y, TAN_BORDER)

    # Item table
    tbl_l, tbl_r = 62.6378, 532.6378
    col_x = [tbl_l, 172.6378, 322.6378, 372.6378, 462.6378, tbl_r]
    col_w = [b - a - 6 for a, b in zip(col_x[:-1], col_x[1:])]
    headers = ["Item", "Dimensions", "Qty", "Unit Price", "Total"]

    tbl_top = y + 15.0
    header_bot = tbl_top + ROW_H
    draw_rect_fill(page, tbl_l, tbl_top, tbl_r, header_bot, BROWN)
    for hx, htxt, w in zip(col_x[:-1], headers, col_w):
        draw_text(page, hx + 6, tbl_top + 2.1, htxt, 10, font="helv", color=WHITE, max_width=w)

    yy = header_bot
    for item in data["items"]:
        y0 = yy
        vals = [item["item"], item["dimensions"], item["qty"], item["unit_price"], item["total"]]
        for cx, txt_, w in zip(col_x[:-1], vals, col_w):
            draw_text(page, cx + 6, y0 + 2.1, str(txt_), 10, font="helv", color=BLACK, max_width=w)
        yy = y0 + ROW_H
    tbl_bot = yy

    draw_line(page, tbl_l, tbl_top, tbl_r, tbl_top, BLACK)
    for i in range(0, len(data["items"]) + 1):
        yline = header_bot + i * ROW_H
        draw_line(page, tbl_l, yline, tbl_r, yline, BLACK)
    draw_line(page, tbl_l, tbl_top, tbl_l, tbl_bot, BLACK)
    draw_line(page, tbl_r, tbl_top, tbl_r, tbl_bot, BLACK)
    for cx in col_x[1:-1]:
        draw_line(page, cx, tbl_top, cx, tbl_bot, BLACK)

    # Summary / totals box
    totals_top = tbl_bot + 15.0
    totals_l, totals_r = 62.6378, 532.6378
    fill_r = 412.6378
    summary_rows = data["summary_rows"]

    if summary_rows:
        draw_rect_fill(page, totals_l, totals_top, fill_r, totals_top + ROW_H * len(summary_rows), TAN_FILL)
        yy = totals_top
        for label, val in summary_rows:
            draw_line(page, totals_l, yy, totals_r, yy, TAN_BORDER)
            draw_text(page, 68.6, yy + 2.1, label, 10, font="helv", color=BLACK, max_width=330)
            draw_text(page, 418.6, yy + 2.1, val, 10, font="helv", color=BLACK, max_width=100)
            yy += ROW_H
        totals_bot = yy
        draw_line(page, totals_l, totals_bot, totals_r, totals_bot, TAN_BORDER)
        draw_line(page, totals_l, totals_top, totals_l, totals_bot, TAN_BORDER)
        draw_line(page, totals_r, totals_top, totals_r, totals_bot, TAN_BORDER)
        draw_line(page, fill_r, totals_top, fill_r, totals_bot, TAN_BORDER)
    else:
        totals_bot = tbl_bot

    # Payment methods
    pm_top = totals_bot + 29.5
    y = pm_top
    payment_lines = []
    if data.get("zelle"):
        payment_lines.append("Zelle: " + data["zelle"])
    if data.get("venmo"):
        payment_lines.append("Venmo: " + data["venmo"])
    pay_links = data.get("pay_links") or []
    if payment_lines or pay_links:
        draw_text(page, 78.0, y, "Payment Methods", 12, font="hebi", color=BLACK)
        y += 19.6
        # Clickable "pay by card" links (Stripe payment links), one per payment.
        for pl in pay_links:
            draw_text(page, 78.0, y, "Card:", 10, font="helv", color=BLACK)
            card_x = 78.0 + fitz.get_text_length("Card: ", fontname="helv", fontsize=10)
            w = draw_link_text(page, card_x, y, pl["label"], pl["url"], 10, font="helv")
            if pl.get("note"):
                draw_text(page, card_x + w + 6, y, pl["note"], 10, font="helv", color=BLACK)
            y += 12.0
        for ln in payment_lines:
            draw_text(page, 78.0, y, ln, 10, font="helv", color=BLACK)
            y += 12.0
        y += 7.5  # gap before next section

    # Connect with us (clickable links)
    links = []
    if data.get("website_url"):
        links.append((data.get("website_label") or data["website_url"], data["website_url"]))
    if data.get("facebook_url"):
        links.append((data.get("facebook_label") or "Follow us on Facebook", data["facebook_url"]))
    if data.get("google_review_url"):
        links.append((data.get("google_review_label") or "Leave us a review on Google", data["google_review_url"]))
    if links:
        draw_text(page, 78.0, y, "Connect With Us", 12, font="hebi", color=BLACK)
        y += 19.6
        for label, url in links:
            draw_link_text(page, 78.0, y, label, url, 10, font="helv")
            y += 12.0
        y += 7.5

    # Note
    note_top = y + 12.0
    for i, ln in enumerate(wrap_text(data["note"], 10, "helv", 459.6)):
        draw_text(page, 78.0, note_top + i * 12.0, ln, 10, font="helv", color=BLACK)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ---------------------------------------------------------------------------
# Stripe payment links ("Pay by card" on the invoice)
# ---------------------------------------------------------------------------

STRIPE_MIN_CENTS = 50          # Stripe's minimum charge in USD
LINK_SOURCE_TAG = "lonestar-invoice-admin"


def _cents(amount):
    return int(round(float(amount) * 100))


def deactivate_previous_links(client, invoice_no, scan_limit=300):
    """
    Turn off any still-active payment links this app made earlier for the same
    invoice number, so a re-issued invoice (e.g. the price changed) can't be
    paid at the old amount. Best-effort; returns how many were deactivated.
    """
    if not invoice_no:
        return 0
    count = scanned = 0
    for pl in client.payment_links.list({"active": True, "limit": 100}).auto_paging_iter():
        scanned += 1
        meta = pl.metadata or {}
        if meta.get("source") == LINK_SOURCE_TAG and meta.get("invoice_no") == invoice_no:
            client.payment_links.update(pl.id, {"active": False})
            count += 1
        if scanned >= scan_limit:
            break
    return count


def create_payment_links(secret_key, invoice_no, customer, parts):
    """
    parts = [(kind, label, amount_in_dollars), ...]  e.g.
            [("deposit", "Pay deposit ($150)", 150.0), ("balance", "...", 350.0)]

    Creates one single-use Stripe Payment Link per part and returns
    [{"kind", "label", "amount", "url", "id"}, ...]. Raises on any Stripe error.
    Links never expire on their own, so a customer can use the balance link
    weeks later when the work is finished.
    """
    if stripe is None:
        raise RuntimeError("The 'stripe' package is not installed.")
    client = stripe.StripeClient(secret_key)

    deactivate_previous_links(client, invoice_no)

    out = []
    for kind, label, amount in parts:
        cents = _cents(amount)
        if cents < STRIPE_MIN_CENTS:
            raise ValueError(f"'{label}' is under Stripe's $0.50 minimum.")
        product_name = f"Invoice {invoice_no or '(draft)'} - {kind} - {customer}"[:250]
        price = client.prices.create({
            "currency": "usd",
            "unit_amount": cents,
            "product_data": {"name": product_name},
        })
        meta = {
            "source": LINK_SOURCE_TAG,
            "invoice_no": invoice_no or "",
            "customer": customer[:400],
            "kind": kind,
        }
        link = client.payment_links.create({
            "line_items": [{"price": price.id, "quantity": 1}],
            "metadata": meta,
            "payment_intent_data": {"metadata": meta, "description": product_name},
            # One payment per link, so the deposit can't accidentally be paid twice.
            "restrictions": {"completed_sessions": {"limit": 1}},
        })
        out.append({"kind": kind, "label": label, "amount": amount, "url": link.url, "id": link.id})
    return out


# ---------------------------------------------------------------------------
# Email sending (Gmail SMTP)
# ---------------------------------------------------------------------------

def fill_template(template, values):
    text = template
    for key, val in values.items():
        text = text.replace("{" + key + "}", val)
    return text


def send_invoice_email(from_email, app_password, to_email, cc_email, subject, body, pdf_bytes, pdf_filename):
    """cc_email may be a single address, a comma-separated list, or empty."""
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(from_email, app_password)
        server.send_message(msg)  # recipients auto-derived from To/Cc headers


def test_smtp_login(from_email, app_password):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(from_email, app_password)


def explain_smtp_error(e, from_email=""):
    text = str(e)
    if "BadCredentials" in text or "5.7.8" in text or "5.7.0" in text:
        return (
            "Gmail rejected the login (bad credentials). This is almost always one of:\n\n"
            "1. 2-Step Verification isn't turned on for this Google account.\n"
            "2. The App Password was generated on a different Google account "
            f"than {from_email or 'the configured From address'}.\n"
            "3. The App Password was later revoked/regenerated.\n\n"
            f"Raw error: {text}"
        )
    return f"Sending failed: {text}"


# ---------------------------------------------------------------------------
# Google Sheets order log
# ---------------------------------------------------------------------------

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def extract_sheet_id(url_or_id):
    url_or_id = url_or_id.strip()
    m = SHEET_ID_RE.search(url_or_id)
    return m.group(1) if m else url_or_id


def get_sheets_client(service_account_json=None):
    """
    If service_account_json is given, use that key file (desktop-app style).
    Otherwise, use Application Default Credentials - the identity the Cloud
    Run service itself runs as. That avoids ever needing to ship the private
    key file to the server.
    """
    if service_account_json:
        creds = ServiceAccountCredentials.from_service_account_file(service_account_json, scopes=SHEETS_SCOPES)
    else:
        creds, _ = google.auth.default(scopes=SHEETS_SCOPES)
    return gspread.authorize(creds)


def open_worksheet(sheet_id, tab_name, service_account_json=None):
    client = get_sheets_client(service_account_json)
    sh = client.open_by_key(extract_sheet_id(sheet_id))
    return sh.worksheet(tab_name)


ANCHOR_HEADER = "customer name"  # a column we always fill; used to find the header row and the next empty row


def find_header_row(ws, probe_header=ANCHOR_HEADER, max_rows=15):
    """
    Sheets like this often have a title row and a blank row above the real
    header row, so don't assume row 1 is the header - scan for the row that
    actually contains our anchor column name.
    """
    first_nonempty = None
    for r in range(1, max_rows + 1):
        vals = ws.row_values(r)
        if not vals:
            continue
        if first_nonempty is None:
            first_nonempty = (r, vals)
        if any(v.strip().lower() == probe_header for v in vals):
            return r, vals
    return first_nonempty if first_nonempty else (1, [])


def append_lead_row(sheet_id, tab_name, values_by_header, service_account_json=None):
    """
    values_by_header: dict mapping a (lowercased, stripped) header name to the
    value that should go in that column. Any header in the sheet not present
    in this dict is left blank, so skipped columns stay empty and the row
    still lines up correctly regardless of column order.
    """
    ws = open_worksheet(sheet_id, tab_name, service_account_json)
    header_row_num, header_values = find_header_row(ws)
    row = [values_by_header.get(h.strip().lower(), "") for h in header_values]

    anchor_idx = next((i for i, h in enumerate(header_values) if h.strip().lower() == ANCHOR_HEADER), 0)
    col_vals = ws.col_values(anchor_idx + 1)
    next_row = max(len(col_vals) + 1, header_row_num + 1)

    ws.update(f"A{next_row}", [row], value_input_option="USER_ENTERED")
    return header_values


def test_sheet_connection(sheet_id, tab_name, service_account_json=None):
    ws = open_worksheet(sheet_id, tab_name, service_account_json)
    _, header_values = find_header_row(ws)
    return header_values
