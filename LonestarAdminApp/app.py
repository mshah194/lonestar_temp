"""
Lonestar Buckeye Woodworks - Invoice Admin (web version)

A small password-gated Flask app that replicates the desktop invoice
generator's capabilities from any browser: builds the branded invoice PDF,
emails it to the customer (cc'd to the shop's own inbox for records), and
logs the order to the shop's Google Sheet.

Config comes entirely from environment variables (see README section at the
bottom of this file) - no secrets are stored in code or shipped to the
browser.
"""

import base64
import functools
import hmac
import os
from datetime import date, timedelta

from flask import Flask, request, session, redirect, url_for, render_template, jsonify

import invoice_core as core

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.permanent_session_lifetime = timedelta(hours=12)
app.config.update(
    # Cloud Run serves HTTPS only, so the session cookie should never be sent
    # over plain HTTP. Set SESSION_COOKIE_SECURE=false only for local testing
    # over http://127.0.0.1.
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
GMAIL_FROM = os.environ.get("GMAIL_FROM", "info.lonestarbuckeye@gmail.com")
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ARCHIVE_CC = os.environ.get("ARCHIVE_CC", "info.lonestarbuckeye@gmail.com")
# Optional: when set, invoices can include Stripe "Pay by card" links. Leave it
# unset and the app works exactly as before (Zelle/Venmo only).
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Leads")
SHEET_LOGGING_ENABLED = os.environ.get("SHEET_LOGGING_ENABLED", "true").lower() != "false"
# If set, use this key file (desktop-style). If unset, use the identity Cloud
# Run itself runs as (Application Default Credentials) - the recommended
# path in production, since it means no private key ever touches the server.
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or None

DEFAULTS = {
    "company_name": "Lonestar Buckeye Woodworks",
    "tagline": "Custom Woodworking & Laser Engraving",
    "zelle": "(614) 302-5955",
    "venmo": "@Shah194",
    "website_label": "www.lonestarbuckeyes.com",
    "website_url": "https://www.lonestarbuckeyes.com",
    "facebook_label": "Follow us on Facebook",
    "facebook_url": "https://www.facebook.com/profile.php?id=61572343202832",
    "google_review_label": "Leave us a review on Google",
    "google_review_url": "https://g.page/r/CWdROURqW8jrEBM/review",
    "email_subject_template": "Invoice {invoice_no} from {company}",
    "email_body_template": (
        "Hi {customer},\n\n"
        "Thank you for your order! Please find your invoice attached.\n\n"
        "If you have any questions, feel free to reach out.\n\n"
        "Best,\n{company}"
    ),
}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Not logged in."}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/", methods=["GET"])
def root():
    return redirect(url_for("app_page") if session.get("authed") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if hmac.compare_digest(submitted, ADMIN_PASSWORD):
            session.clear()
            session.permanent = True
            session["authed"] = True
            return redirect(url_for("app_page"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/app")
@login_required
def app_page():
    return render_template("app.html", defaults=DEFAULTS, today=date.today().strftime("%B %d, %Y").replace(" 0", " "))


def plan_card_payments(total_due, deposit):
    """
    Decide which Stripe payment links an invoice needs.
    Returns [(kind, label, amount), ...]; empty if nothing is chargeable.
      - deposit entered (and less than the total): a deposit link + a balance link
      - no deposit (or deposit covers everything): one link for the full amount
    """
    min_dollars = core.STRIPE_MIN_CENTS / 100.0
    if total_due < min_dollars:
        return []
    if deposit and deposit >= min_dollars and (total_due - deposit) >= min_dollars:
        balance = round(total_due - deposit, 2)
        return [
            ("deposit", f"Pay deposit ({core.money(deposit)}) online", round(deposit, 2)),
            ("balance", f"Pay remaining balance ({core.money(balance)}) online", balance),
        ]
    amount = round(deposit if (deposit and deposit >= min_dollars and deposit >= total_due) else total_due, 2)
    return [("full", f"Pay invoice ({core.money(amount)}) online", amount)]


def add_card_links_to_body(body, pay_links):
    """Insert the card links into the email, just above the sign-off paragraph."""
    if not pay_links:
        return body
    lines = ["Pay by card (Apple Pay and Google Pay also work):"]
    for p in pay_links:
        lines.append(f"{p['label']}: {p['url']}")
    if any(p["kind"] == "balance" for p in pay_links):
        lines.append("(The remaining-balance link can be used any time, once your project is complete.)")
    block = "\n".join(lines)
    if "{payment_links}" in body:
        return body.replace("{payment_links}", block)
    head, sep, tail = body.rpartition("\n\n")
    return f"{head}\n\n{block}\n\n{tail}" if sep else f"{body}\n\n{block}"


@app.route("/api/generate-invoice", methods=["POST"])
@login_required
def api_generate_invoice():
    payload = request.get_json(force=True, silent=True) or {}

    customer = (payload.get("customer") or "").strip()
    to_email = (payload.get("to_email") or "").strip()
    raw_items = payload.get("items") or []
    items_in = [i for i in raw_items if (i.get("item") or "").strip()]

    if not customer:
        return jsonify({"ok": False, "error": "Customer name is required."}), 400
    if not to_email:
        return jsonify({"ok": False, "error": "Recipient email is required."}), 400
    if not items_in:
        return jsonify({"ok": False, "error": "At least one line item is required."}), 400

    # Recompute totals server-side rather than trusting client arithmetic.
    items = []
    subtotal = 0.0
    for i in items_in:
        try:
            qty = float(i.get("qty") or 0)
            price = float(str(i.get("unit_price") or 0).replace("$", "").replace(",", ""))
        except ValueError:
            return jsonify({"ok": False, "error": f"Bad quantity/price on item '{i.get('item')}'."}), 400
        line_total = qty * price
        subtotal += line_total
        items.append({
            "item": i.get("item", ""),
            "dimensions": i.get("dimensions", ""),
            "qty": i.get("qty", ""),
            "unit_price": core.money(price),
            "total": core.money(line_total),
        })

    summary_rows = []
    if payload.get("include_project_total", True):
        summary_rows.append(("Project Total", core.money(subtotal)))
    deposit = None
    deposit_raw = str(payload.get("deposit") or "").strip()
    if deposit_raw:
        try:
            deposit = float(deposit_raw.replace("$", "").replace(",", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "Deposit must be a number."}), 400
        summary_rows.append(("Deposit Requested", core.money(deposit)))
        summary_rows.append(("Balance Remaining", core.money(subtotal - deposit)))
    for row in payload.get("custom_totals") or []:
        label = (row.get("label") or "").strip()
        if label:
            summary_rows.append((label, row.get("value", "")))

    # --- Stripe "pay by card" links (deposit + balance, or one full-payment link)
    warnings = []
    pay_links = []
    invoice_no_clean = (payload.get("invoice_no") or "").strip()
    if payload.get("card_links", True) and STRIPE_SECRET_KEY:
        override_raw = str(payload.get("amount_due_override") or "").strip()
        try:
            total_due = float(override_raw.replace("$", "").replace(",", "")) if override_raw else subtotal
        except ValueError:
            return jsonify({"ok": False, "error": "Amount due override must be a number."}), 400
        parts = plan_card_payments(total_due, deposit)
        if parts:
            try:
                pay_links = core.create_payment_links(STRIPE_SECRET_KEY, invoice_no_clean, customer, parts)
                for p in pay_links:
                    if p["kind"] == "balance":
                        p["note"] = "- due when your project is complete"
            except Exception as e:
                warnings.append(f"Card payment links could NOT be created, so this invoice went out "
                                f"without them (Zelle/Venmo only): {e}")
        else:
            warnings.append("No card payment link was added (amount due is under Stripe's $0.50 minimum or zero).")
    elif payload.get("card_links", True) and not STRIPE_SECRET_KEY:
        warnings.append("Card payment links are not configured on the server (no STRIPE_SECRET_KEY).")

    data = {
        "pay_links": pay_links,
        "company_name": (payload.get("company_name") or DEFAULTS["company_name"]).strip(),
        "tagline": (payload.get("tagline") or DEFAULTS["tagline"]).strip(),
        "customer": customer,
        "invoice_no": (payload.get("invoice_no") or "").strip(),
        "date": (payload.get("date") or "").strip(),
        "items": items,
        "summary_rows": summary_rows,
        "zelle": (payload.get("zelle") or "").strip(),
        "venmo": (payload.get("venmo") or "").strip(),
        "website_url": (payload.get("website_url") or "").strip() if payload.get("website_on") else "",
        "website_label": (payload.get("website_label") or "").strip(),
        "facebook_url": (payload.get("facebook_url") or "").strip() if payload.get("facebook_on") else "",
        "facebook_label": (payload.get("facebook_label") or "").strip(),
        "google_review_url": (payload.get("google_review_url") or "").strip() if payload.get("google_review_on") else "",
        "google_review_label": (payload.get("google_review_label") or "").strip(),
        "note": (payload.get("note") or "").strip(),
    }

    try:
        pdf_bytes = core.build_invoice_pdf_bytes(data)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not generate PDF: {e}"}), 500

    safe_customer = "".join(c if c.isalnum() or c in "-_" else "_" for c in customer)
    pdf_filename = f"{safe_customer}_Invoice_{data['invoice_no'] or 'draft'}.pdf"

    if SHEET_LOGGING_ENABLED and SHEET_ID:
        items_joined = "; ".join(i["item"] for i in items if i["item"])
        dims_joined = "; ".join(i["dimensions"] for i in items if i["dimensions"])
        today = date.today()
        values_by_header = {
            "date in": f"{today.month}/{today.day}/{today.year}",
            "customer name": customer,
            "email": to_email,
            "what they want": items_joined,
            "dimensions": dims_joined,
            "dimensions / size": dims_joined,
            "budget idea ($)": core.money(subtotal),
            "budget idea (total estimate)": core.money(subtotal),
            "budget idea": core.money(subtotal),
        }
        try:
            core.append_lead_row(SHEET_ID, SHEET_TAB, values_by_header, SERVICE_ACCOUNT_JSON)
        except Exception as e:
            warnings.append(f"Invoice created, but adding it to the '{SHEET_TAB}' sheet failed: {e}")
    warning = "\n".join(warnings) or None

    placeholders = {"customer": customer, "invoice_no": data["invoice_no"], "company": data["company_name"]}
    subject = core.fill_template((payload.get("subject") or DEFAULTS["email_subject_template"]), placeholders)
    body = core.fill_template((payload.get("body") or DEFAULTS["email_body_template"]), placeholders)
    body = add_card_links_to_body(body, pay_links)

    cc_list = []
    extra_cc = (payload.get("cc_extra") or "").strip()
    if extra_cc:
        cc_list.append(extra_cc)
    if ARCHIVE_CC and ARCHIVE_CC.lower() not in [c.lower() for c in cc_list]:
        cc_list.append(ARCHIVE_CC)
    cc_field = ", ".join(cc_list)

    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    try:
        core.send_invoice_email(GMAIL_FROM, GMAIL_APP_PASSWORD, to_email, cc_field, subject, body,
                                 pdf_bytes, pdf_filename)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": core.explain_smtp_error(e, GMAIL_FROM),
            "pdf_base64": pdf_b64,
            "pdf_filename": pdf_filename,
        }), 502

    return jsonify({
        "ok": True,
        "message": f"Emailed to {to_email}" + (f" (cc: {cc_field})" if cc_field else "") + ".",
        "warning": warning,
        "payment_links": [{"label": p["label"], "url": p["url"]} for p in pay_links],
        "pdf_base64": pdf_b64,
        "pdf_filename": pdf_filename,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


# ---------------------------------------------------------------------------
# Required environment variables:
#   FLASK_SECRET_KEY          - random string, signs the login session cookie
#   ADMIN_PASSWORD            - the password for logging into the admin page
#   GMAIL_APP_PASSWORD        - the Gmail App Password for GMAIL_FROM
#
# Optional (sensible defaults shown):
#   STRIPE_SECRET_KEY         - enables the "Pay by card" Stripe payment links
#                               (same secret the shop backend uses). Unset =
#                               invoices go out Zelle/Venmo only, as before.
#   GMAIL_FROM                = info.lonestarbuckeye@gmail.com
#   ARCHIVE_CC                = info.lonestarbuckeye@gmail.com
#   GOOGLE_SHEET_ID           = (Leads spreadsheet ID)
#   GOOGLE_SHEET_TAB          = Leads
#   SHEET_LOGGING_ENABLED     = true
#   GOOGLE_SERVICE_ACCOUNT_JSON = (path to key file; omit in production and
#                                  instead run the Cloud Run service as the
#                                  service account that has Editor access to
#                                  the sheet)
# ---------------------------------------------------------------------------
