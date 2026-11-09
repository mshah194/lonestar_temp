# Lonestar Buckeye Woodworks — Shop Checkout API

A tiny, public Flask app with three jobs:

1. Turn a shopping cart from lonestarbuckeyes.com into a Stripe Checkout
   Session and hand back the URL to redirect the customer to.
2. For local-pickup-only items (furniture, swings, planters — too big to
   ship), check whether a customer's ZIP code is within the shop's 50-mile
   pickup radius before letting them buy online.
3. Receive an uploaded engraving logo file and email it to the shop owner
   (see "Engraving customization" below).

It's the one piece of server logic the static site (GitHub Pages) can't do
on its own — everything else (the cart itself, product listing, drawer UI)
is plain JS/localStorage on the site.

Every product has a `fulfillment` of `"ships"` or `"pickup"` (see
`catalog.py`). A cart can only contain one type at a time — Stripe Checkout
can't apply shipping to one line item and skip it for another, so mixing is
blocked (both in the browser and again here, server-side). A `"pickup"`
checkout requires a `zip` in the request that passes the distance check
before a Checkout Session is created.

This is deliberately a separate Cloud Run service from `LonestarAdminApp`
(the password-gated invoice tool). That app's auth boundary is for internal
use only; this endpoint has to be reachable by any customer's browser, so it
stays unauthenticated. It holds the Stripe secret key, the product catalog,
and (as of the logo-upload fix below) the same Gmail App Password
`LonestarAdminApp` already uses — same GCP project, same service account,
so no new secret infrastructure was needed for that.

## Files

- `app.py` — Flask app: CORS, `/api/create-checkout-session`,
  `/api/check-pickup-zip`, `/api/upload-logo`, `/api/health`. (Not
  `/healthz` - that exact path is reserved at Google's Cloud Run frontend
  layer and never reaches the container; see the comment on the route in
  `app.py`.)
- `catalog.py` — server-side product id → name/price/fulfillment lookup.
  **This is what Stripe actually charges** — the browser only ever sends an
  id and a quantity, never a price. Keep this in sync by hand with
  `products.js` in the `lonestarbuckeyes` site repo (same ids, same prices,
  same fulfillment).
- `env-vars.yaml` — plain (non-secret) env vars passed to `gcloud run
  deploy` via `--env-vars-file`. Edit this directly to change redirect
  URLs, allowed origins, or the logo-upload sender/recipient addresses.
- `Dockerfile`, `requirements.txt`, `.dockerignore` — container build.

## One-time setup

Same GCP project as `LonestarAdminApp` (`lonestar-invoice-app`) — if you've
already done this for that app, skip to step 4.

### 1. Install the gcloud CLI

https://cloud.google.com/sdk/docs/install — then in a **new** terminal:

```bash
gcloud auth login
gcloud config set project lonestar-invoice-app
```

### 2. Enable the required APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

### 3. Create a Stripe account and get the secret key

Sign up at https://stripe.com if you haven't already (needs bank info for
payouts). While testing, use a **test mode** secret key
(`sk_test_...`, from the Stripe Dashboard's Developers → API keys page, with
"Test mode" toggled on) — test-mode Checkout Sessions use Stripe's test card
numbers and never move real money. Switch to the live key
(`sk_live_...`) only once you're ready for real orders.

### 4. Create the Stripe secret in Secret Manager

**macOS/Linux/Git Bash:**
```bash
echo -n "sk_test_your_key_here" | gcloud secrets create STRIPE_SECRET_KEY --data-file=-
```

**Windows PowerShell** — two gotchas here, both silent (no error until you
try to actually use the key):
1. `echo` is aliased to `Write-Output`, which has no `-n` flag, so the bash
   command above stores the literal text `-n "..."` plus a trailing `\r\n`
   as your secret ("Invalid leading whitespace... in header value" error).
2. `Set-Content -Encoding utf8` writes a UTF-8 **byte-order-mark** (an
   invisible character) at the start of the file, which becomes the first
   character of your stored secret (`UnicodeEncodeError:
   'latin-1' codec can't encode character '﻿'`).

Since a Stripe key is always plain ASCII, sidestep both issues by writing
the file with .NET's ASCII encoding directly, which can produce neither
problem:
```powershell
$key = "sk_test_your_key_here"
[System.IO.File]::WriteAllText("$env:TEMP\stripe_key.txt", $key, [System.Text.Encoding]::ASCII)
gcloud secrets create STRIPE_SECRET_KEY --data-file="$env:TEMP\stripe_key.txt"
Remove-Item "$env:TEMP\stripe_key.txt"
```

To rotate the key later (e.g. switching test → live), same platform split,
but `gcloud secrets versions add` instead of `create`:
```bash
echo -n "new-key" | gcloud secrets versions add STRIPE_SECRET_KEY --data-file=-
```
```powershell
$key = "new-key"
[System.IO.File]::WriteAllText("$env:TEMP\stripe_key.txt", $key, [System.Text.Encoding]::ASCII)
gcloud secrets versions add STRIPE_SECRET_KEY --data-file="$env:TEMP\stripe_key.txt"
Remove-Item "$env:TEMP\stripe_key.txt"
```

If `gcloud` itself won't run in PowerShell ("running scripts is disabled on
this system"), that's PowerShell's script execution policy blocking
`gcloud.ps1`, unrelated to any of the above — either call `gcloud.cmd`
explicitly (bypasses the policy, no system changes) or run
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once.

### 5. Let Cloud Run read that secret

Same service account already used by `LonestarAdminApp`:

```bash
gcloud secrets add-iam-policy-binding STRIPE_SECRET_KEY \
  --member="serviceAccount:lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Deploy

Run from inside the `LonestarShopApp` folder:

```bash
gcloud run deploy lonestar-shop-api \
  --source . \
  --region us-central1 \
  --service-account lonestar-app-service-account@lonestar-invoice-app.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-secrets STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest \
  --env-vars-file env-vars.yaml
```

`GMAIL_APP_PASSWORD` is the same secret `LonestarAdminApp` already uses —
no need to create it again, and the service account already has
`secretAccessor` on it from that app's setup. If `/api/upload-logo` ever
starts failing with "Logo upload isn't configured yet," it means this
service was deployed without that `--set-secrets` entry - just redeploy
with the command above.

The plain env vars (`SUCCESS_URL`, `CANCEL_URL`, `ALLOWED_ORIGINS`) live in
`env-vars.yaml` in this folder rather than on the command line as
`--set-env-vars KEY=VALUE,KEY=VALUE` — `ALLOWED_ORIGINS` itself needs a
comma inside its value (two allowed origins), which collides with the
comma `gcloud` uses to separate different `--set-env-vars` entries from
each other ("Bad syntax for dict arg" error). No secrets go in this file —
`STRIPE_SECRET_KEY` stays in Secret Manager via `--set-secrets`, never
written to disk here. Edit `env-vars.yaml` directly if any of these values
ever need to change.

`--allow-unauthenticated` is required here (unlike the admin app) — this
endpoint has to be callable by anonymous customer browsers. There's no
password gate because there's nothing sensitive to protect: it only ever
returns a Stripe-hosted checkout URL, and CORS restricts which site origins
can call it at all.

This prints a URL like `https://lonestar-shop-api-xxxxx-uc.a.run.app`. Test it
first:

```bash
curl -X POST https://lonestar-shop-api-xxxxx-uc.a.run.app/api/create-checkout-session \
  -H "Content-Type: application/json" \
  -H "Origin: https://lonestarbuckeyes.com" \
  -d '{"items":[{"id":"keychain-custom","qty":1}]}'
```

You should get back `{"url": "https://checkout.stripe.com/..."}`. Open that
URL and pay with a [Stripe test card](https://docs.stripe.com/testing)
(e.g. `4242 4242 4242 4242`, any future expiry, any CVC) to confirm the whole
flow before going live.

Test the pickup ZIP check the same way:

```bash
curl -X POST https://lonestar-shop-api-xxxxx-uc.a.run.app/api/check-pickup-zip \
  -H "Content-Type: application/json" \
  -H "Origin: https://lonestarbuckeyes.com" \
  -d '{"zip":"75035"}'
```

You should get back `{"eligible": true, "distance_miles": 6.3, "radius_miles": 50.0}`.
A pickup checkout (`items` all `fulfillment: "pickup"`) needs that same
`zip` field in the `/api/create-checkout-session` body — it's re-checked
here regardless of what the browser already showed the customer.

## Custom domain: shop-api.lonestarbuckeyes.com

```bash
gcloud beta run domain-mappings create \
  --service lonestar-shop-api \
  --domain shop-api.lonestarbuckeyes.com \
  --region us-central1
```

Add the DNS record it prints wherever `lonestarbuckeyes.com` is registered
(not in the GitHub repo). Once it resolves, update `SHOP_API_URL` in
`cart.js` (in the `lonestarbuckeyes` site repo) from the placeholder to
`https://shop-api.lonestarbuckeyes.com`, then push that change so GitHub
Pages picks it up.

## Before going live

- Replace the placeholder prices/products in **both** `catalog.py` here and
  `products.js` in the site repo — they must match. This especially matters
  for the `"pickup"` (furniture/outdoor) items, which currently have
  obviously-fake round-number prices.
- Shipping is a couple of weight-based flat-rate tiers (`WEIGHT_SHIPPING_TIERS`
  in `app.py`: $7.99 up to 1 lb, $13.99 over 1 lb) rather than one flat rate
  for every order - a bottle opener and a cutting board don't cost the same
  to actually mail. Each product needs a `ship_weight_oz` in `catalog.py`
  (and matching `shipWeightOz` in `products.js`, used for the on-page
  estimate). These are rough estimates based on typical USPS Ground
  Advantage rates, not a live carrier lookup - revisit both the tier
  breakpoints/prices and the per-product weights once you know your real
  shipping costs, or switch to Stripe's dynamic shipping rates later if you
  want live carrier-calculated rates instead.
- Double check `SHOP_LAT`/`SHOP_LNG`/`PICKUP_RADIUS_MILES` (see comments in
  `app.py`) reflect where you actually want the 50-mile pickup line drawn.
- Swap `STRIPE_SECRET_KEY` from a `sk_test_...` value to `sk_live_...`
  (`gcloud secrets versions add STRIPE_SECRET_KEY --data-file=-`) once you've
  completed a real test-mode purchase end to end.
- To update the app after future code changes: re-run the same
  `gcloud run deploy` command above from this folder.

## Local pickup / ZIP check

Furniture and outdoor pieces (`fulfillment: "pickup"` in `catalog.py`) are
too big to ship, so they're sold for pickup near McKinney, TX only. Before a
pickup item can be checked out, the customer's ZIP is geocoded via the free
[Zippopotam.us](https://zippopotam.us) API (no signup, no cost) and checked
against a straight-line `PICKUP_RADIUS_MILES` (default 50) from the shop's
coordinates. This happens twice: once in the browser (`/api/check-pickup-zip`,
so the customer gets instant feedback before adding to cart) and again here
when the Checkout Session is actually created, so the check can't be
bypassed by editing the page.

Straight-line distance was chosen over driving distance to keep this free —
driving-distance APIs (Google Maps Distance Matrix, etc.) require billing.
If Zippopotam.us is ever down, the ZIP check fails closed (customers can't
buy a pickup item until it's back) — it's a well-established free service,
but if that ever becomes a real problem, the fix is swapping
`zip_to_latlng()` in `app.py` for a different geocoder.

Ships and pickup items can never share one order — Stripe Checkout applies
one `shipping_address_collection` setting to the whole session, so a mixed
cart is rejected with a 400 both client-side and here.

## Engraving customization (text/font and/or logo upload)

Every laser-engraved product (`customizable: true` in `products.js`) shows a
"Personalize this item" panel before it can be added to cart — required,
since there's no way to fulfill a "custom engraved" order without knowing
what to engrave. Text and logo are independent fields, not an either/or
choice — a customer can fill in either one, or both (e.g. a logo plus "Est.
2024" underneath it); at least one is required. Text engraving costs nothing
extra to handle: the text and chosen font are cheap enough to pass straight
through as a Stripe line item `description`, so **you'll see the exact
engraving text and font choice right on the order/receipt** — no separate
lookup needed.

Logo uploads are different — Stripe Checkout can't carry file attachments —
so the actual file gets POSTed to this backend's `/api/upload-logo`
endpoint the moment the customer clicks "Add to Cart" (before checkout even
starts), which emails it to you via Gmail SMTP (`send_logo_email()` in
`app.py`, using the same `GMAIL_APP_PASSWORD` secret and sending pattern
`LonestarAdminApp` already uses for invoices). That email includes a short
reference code (e.g. `ZIPHKY`) and, if the customer also entered text, that
text too. The **same reference code shows up in the Stripe line item
description** (e.g. `Engrave: "Smith Family" (Bold Block) + logo emailed
separately, ref ZIPHKY`) so you can match a paid order to the emailed
design without cross-referencing two different systems by hand. Files are
capped at 10MB (enforced both client-side in `cart.js` and server-side in
`app.py`) to stay safely under Gmail's message size limit.

**Originally built on Formspree instead** (the same form the contact page
uses) — switched away from it 2026-08-25 after a real customer hit "File
Uploads Not Permitted": Formspree's free plan doesn't support file
attachments at all, only text fields. That's the actual reason this moved
to a dedicated backend endpoint rather than reusing the existing form. If
you ever want logo uploads to go to a different inbox than
`SHOP_OWNER_EMAIL` in `env-vars.yaml`, that's a one-line change there — no
code edit needed.

**Live-tested**: verified end-to-end after the Formspree switch — a file
POST to the deployed endpoint correctly builds and would send the email
(confirmed via the real code path up to Gmail's own login step; a full
real-inbox delivery test is still worth doing once, the same way a real
Stripe test-mode purchase was done before going live).

## Stain finish add-on (porch swings)

Both porch swings (`porch-swing-classic`, `porch-swing-console`) have a
`stain_upcharge` in `catalog.py` (currently $79.00) — an optional flat
add-on, not a required choice like engraving. The product card shows a
checkbox ("Add a stain finish (+$79.00)"); checking it reveals a color
dropdown, curated from Varathane's commonly-stocked Home Depot/Lowes shades
(see `STAIN_COLORS` in `cart.js`). The upcharge amount is never trusted from
the browser — `stain_addon()` in `app.py` always looks it up fresh from
`catalog.py` and adds it to `tier_price()`'s result, same pattern as every
other price on this site. The chosen color is customer-supplied text and
shows up in the Stripe line item description (e.g. `Stain: Dark Walnut
(+$79.00)`), combined with the engraving description if the item also has
one. To add a stain option to another product, give it a `stain_upcharge` in
`catalog.py` and matching `stainOption`/`stainUpcharge` fields in
`products.js` — no other code changes needed.
