# Lonestar Buckeye Woodworks — Invoice Admin: Configuration, Usage & FAQ

_Last updated: August 2026_

## How it's configured

| Setting | Value |
|---|---|
| Google Cloud project | `lonestar-invoice-app` |
| Cloud Run service name | `lonestar-invoice-admin` |
| Region | `us-central1` |
| Runs as (service account) | `lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com` |
| Source code (local) | `LonestarAdminApp` folder on this computer |
| Deploy command | `gcloud run deploy --source .` from inside that folder |

**Secrets** (stored encrypted in Google Secret Manager, never in code or in the
public GitHub repo):

- `ADMIN_PASSWORD` — the password for logging into the admin page
- `FLASK_SECRET_KEY` — random key that signs your login session
- `GMAIL_APP_PASSWORD` — the 16-character Gmail App Password for sending mail

**Plain config** (not secret, set as Cloud Run environment variables):

- `GMAIL_FROM` = `info.lonestarbuckeye@gmail.com`
- `ARCHIVE_CC` = `info.lonestarbuckeye@gmail.com` (always cc'd on every invoice)
- `GOOGLE_SHEET_ID` = the Leads spreadsheet
- `GOOGLE_SHEET_TAB` = `Leads`
- `SHEET_LOGGING_ENABLED` = `true`

**Why it's secure even though the main website's code is public on GitHub:**
this admin tool is a completely separate app from the `lonestarbuckeyes`
GitHub repo — its code was never committed there. Logging in is checked on
the server (not just hidden client-side JavaScript), and the three secrets
above live in Secret Manager, not in any file that gets deployed.

**How it reaches Google Sheets without a key file:** the Cloud Run service
runs *as* the same service account that's shared as an Editor on the Leads
sheet, so it authenticates automatically — there's no private key sitting on
the server to leak.

---

## How to use it

1. **Log in** with the admin password at your admin URL.
2. **Business Header** — company name/tagline, prefilled, rarely needs
   changing.
3. **Invoice Info** — customer name, invoice number (typed manually — see
   FAQ), and date (defaults to today).
4. **Line Items** — click **+ Add line item** for each row. Qty × Unit Price
   auto-calculates the Total live. Most jobs use Qty = 1 and put the whole
   line price in Unit Price (matches how the original paper template works).
5. **Totals box** — "Project Total" is on by default (= the subtotal). Add a
   deposit amount to also show "Deposit Requested" and "Balance Remaining."
   Use **+ Add custom total row** for quote-style invoices with multiple
   options (e.g. "Total with Cork" / "Total with Slate").
6. **Payment Methods** and **Connect With Us** — prefilled with your Zelle,
   Venmo, website, Facebook, and Google review links; edit or uncheck any of
   them per invoice.
7. **Note** — the thank-you message at the bottom of the PDF, fully editable.
8. **Email Invoice** — enter the customer's email as **To**. **Cc** is for
   anyone extra; your own inbox is cc'd automatically every time regardless.
   Subject/body support `{customer}`, `{invoice_no}`, and `{company}`, which
   get filled in automatically.
9. Click **Create & Email Invoice**. It builds the PDF, emails it, and adds a
   row to the Leads sheet — then gives you a **Download PDF** link so you can
   also save or forward a copy yourself.

---

## FAQ

**What if I forget the admin password?**
Generate a new one and store it as a new secret version, then redeploy so
it takes effect:
```
py -3 -c "print('NEW-PASSWORD', end='')" | gcloud secrets versions add ADMIN_PASSWORD --data-file=-
gcloud run services update lonestar-invoice-admin --region us-central1 --update-secrets=ADMIN_PASSWORD=ADMIN_PASSWORD:latest
```

**What if sending the email fails?**
The invoice PDF is still generated and offered as a download — nothing is
lost. The error message will usually explain why (bad Gmail credentials,
network issue, etc.).

**Does every invoice really get logged to the Leads sheet automatically?**
Yes, every time you click Create & Email Invoice, as long as the sheet is
reachable. If logging fails for some reason, you'll see a warning, but the
invoice still gets created and emailed — logging failure never blocks the
actual invoice.

**Why isn't the invoice number auto-filled anymore, like the desktop app?**
Cloud Run doesn't keep local state between visits, so there's nowhere to
remember "the last number." You type it in manually — check your last
invoice or the Leads sheet for the next one. This could be automated later
(e.g. tracked in the Sheet itself) if it becomes annoying.

**What if the Gmail App Password stops working?**
Google can revoke App Passwords (e.g. if you turn 2-Step Verification off
and back on). Generate a fresh one at myaccount.google.com/apppasswords,
then:
```
py -3 -c "import keyring; print(keyring.get_password('LonestarInvoiceApp_Gmail','info.lonestarbuckeye@gmail.com'), end='')" | gcloud secrets versions add GMAIL_APP_PASSWORD --data-file=-
gcloud run services update lonestar-invoice-admin --region us-central1 --update-secrets=GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest
```
(Update the password in Windows Credential Manager first via the desktop
app's "Set App Password" button, if you're using both tools.)

**What does this cost to run?**
Cloud Run and Secret Manager both have generous free tiers. For one shop
owner generating invoices, actual usage should stay entirely within the
free tier — realistically $0/month.

**Can more than one person log in?**
Everyone shares the same single password right now — there's no per-person
account. Fine for one admin; if you ever want separate logins or an audit
trail of who sent what, that's a bigger change (e.g. Google Sign-In
restricted to specific accounts).

**What happens to the PDF after I send it — is it saved anywhere?**
It's generated fresh each time and not stored on the server afterward. The
customer has it (emailed), you have it (cc'd to your own inbox), and the
download link lets you save a copy locally if you want one. If you need a
permanent archive, save downloads to a folder or rely on your email's search.

**What if I rename or reorder columns in the Leads sheet?**
The app finds columns by name (not position), so reordering columns is
safe. If you rename a column the app writes to (Date In, Customer Name,
Email, What They Want, Dimensions / Size, Budget Idea ($)), it'll just leave
that column blank until the code is updated to match the new name.

**How do I update the site later (new features, fixes)?**
Once new code is ready in the `LonestarAdminApp` folder, redeploy with the
same command used originally:
```
cd /d "C:\Users\shah1\OneDrive\Documents\ClaudeWork\LonestarAdminApp"
gcloud run deploy lonestar-invoice-admin --source . --region us-central1 --service-account lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com --allow-unauthenticated --set-secrets ADMIN_PASSWORD=ADMIN_PASSWORD:latest,FLASK_SECRET_KEY=FLASK_SECRET_KEY:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest --set-env-vars GMAIL_FROM=info.lonestarbuckeye@gmail.com,ARCHIVE_CC=info.lonestarbuckeye@gmail.com,GOOGLE_SHEET_ID=1Ab_vkSs_CRYuWSyHnGSgge76OtoSocI6Z_nYoQECg0U,GOOGLE_SHEET_TAB=Leads,SHEET_LOGGING_ENABLED=true
```

**Is the old Windows desktop app still around?**
Yes, untouched, in the `LonestarInvoiceApp` folder — it still works exactly
as before if you ever need it as a fallback (e.g. no internet, or the site
is down).
