# Lonestar Buckeye Woodworks — Invoice Admin (web version)

A password-gated web app that replaces the Windows desktop invoice generator.
Same capabilities — branded PDF invoice, email to the customer (always cc'd to
`info.lonestarbuckeye@gmail.com`), and a new row logged to the **Leads**
Google Sheet — but reachable from any browser, deployed on Google Cloud Run.

This has already been built and tested locally (real email send + real sheet
row, both verified and cleaned up). What's left is deploying it so it's
reachable at `admin.lonestarbuckeyes.com`.

## Files

- `app.py` — Flask app: login gate, the invoice form page, and the
  `/api/generate-invoice` endpoint.
- `invoice_core.py` — PDF layout, email sending, and Google Sheets logic
  (adapted from the desktop app; same visual template).
- `templates/login.html`, `templates/app.html` — the two pages.
- `logo_b64.txt` — the embedded shop logo.
- `Dockerfile`, `requirements.txt` — container build.
- `lonestar-invoice-app-9ee32acd0801.json` — **do not deploy this file.** It's
  here for local testing only; the `.dockerignore` excludes all `.json`
  files from the container image on purpose. In production, Cloud Run uses
  its own service account identity instead (see below) — the private key
  never needs to leave your machine.

## One-time setup

### 1. Install the gcloud CLI

Download and install from https://cloud.google.com/sdk/docs/install (Windows
installer). After installing, open a **new** terminal and run:

```bash
gcloud auth login
gcloud config set project lonestar-invoice-app
```

The first command opens a browser for you to sign in with your own Google
account — I can't do this step for you, it needs to be your own
interactive login.

### 2. Link a billing account (if not already)

```bash
gcloud billing accounts list
gcloud billing projects link lonestar-invoice-app --billing-account=YOUR_BILLING_ACCOUNT_ID
```

### 3. Enable the required APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

### 4. Create the secrets

These three values must never appear in code or in the Cloud Run env var
list in plain text — Secret Manager keeps them encrypted and access-logged.

**Admin password** (the password for logging into the web app — pick a new
one, don't reuse anything else):

```bash
echo -n "choose-a-strong-password-here" | gcloud secrets create ADMIN_PASSWORD --data-file=-
```

**Flask session signing key** (random, you'll never need to type this
yourself):

```bash
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets create FLASK_SECRET_KEY --data-file=-
```

**Gmail app password** (the same 16-character App Password already saved in
Windows Credential Manager for the desktop app — this pulls it straight from
there so you don't have to retype it):

```bash
python -c "import keyring; print(keyring.get_password('LonestarInvoiceApp_Gmail','info.lonestarbuckeye@gmail.com'))" | gcloud secrets create GMAIL_APP_PASSWORD --data-file=-
```

### 5. Let the Cloud Run service read those secrets

Cloud Run will run as `lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com`
(the same service account already shared as Editor on the Leads sheet), so
grant it access to the three secrets:

```bash
for SECRET in ADMIN_PASSWORD FLASK_SECRET_KEY GMAIL_APP_PASSWORD; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

## Deploy

Run this from inside the `LonestarAdminApp` folder. `--source .` tells Cloud
Run to build the container in the cloud via Cloud Build — you don't need
Docker installed locally at all.

```bash
gcloud run deploy lonestar-invoice-admin \
  --source . \
  --region us-central1 \
  --service-account lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-secrets ADMIN_PASSWORD=ADMIN_PASSWORD:latest,FLASK_SECRET_KEY=FLASK_SECRET_KEY:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest \
  --set-env-vars GMAIL_FROM=info.lonestarbuckeye@gmail.com,ARCHIVE_CC=info.lonestarbuckeye@gmail.com,GOOGLE_SHEET_ID=1Ab_vkSs_CRYuWSyHnGSgge76OtoSocI6Z_nYoQECg0U,GOOGLE_SHEET_TAB=Leads,SHEET_LOGGING_ENABLED=true
```

`--allow-unauthenticated` means Cloud Run itself won't demand a Google
sign-in before reaching the site — that's expected and fine, because the
app's own login page (checked against `ADMIN_PASSWORD` server-side) is the
real gate.

This prints a URL like `https://lonestar-invoice-admin-xxxxx-uc.a.run.app`
when done. Open it and confirm the login page loads before moving on.

## Custom domain: admin.lonestarbuckeyes.com

```bash
gcloud beta run domain-mappings create \
  --service lonestar-invoice-admin \
  --domain admin.lonestarbuckeyes.com \
  --region us-central1
```

This will print one or more DNS records (usually a CNAME pointing at
`ghs.googlehosted.com`, or several A/AAAA records). Add exactly what it
prints to your domain's DNS settings, wherever `lonestarbuckeyes.com` is
registered (not in the GitHub repo — this is a DNS change at your domain
registrar). DNS changes can take anywhere from a few minutes to a few hours
to propagate. Once it resolves, `https://admin.lonestarbuckeyes.com` will
serve the login page directly.

## After deploying

- Log in with the `ADMIN_PASSWORD` you chose above.
- Generate a real test invoice to yourself first before using it on an
  actual customer.
- To change the admin password later: `echo -n "new-password" | gcloud secrets versions add ADMIN_PASSWORD --data-file=-`,
  then redeploy (or just wait — Cloud Run reads the `:latest` secret version
  on each new container start).
- To update the app after future code changes: re-run the same
  `gcloud run deploy` command above from this folder.

## Note on the invoice number field

The desktop app auto-incremented invoice numbers using a local file. Cloud
Run instances don't keep local state between deploys/restarts, so the web
form just leaves the Invoice # field blank with a placeholder — type it in
manually each time (e.g. by checking your last invoice or the Leads sheet).
If you'd like this automated later (e.g. tracked in the Sheet or a small
Cloud Storage file), that's a small follow-up, not a blocker to using the
tool today.
