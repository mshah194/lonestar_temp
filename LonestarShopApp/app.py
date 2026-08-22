"""
Lonestar Buckeye Woodworks - Shop Checkout API

A small, public (unauthenticated) Flask app with two jobs:
  1. Take a cart of product ids/quantities from the static site and turn it
     into a Stripe Checkout Session.
  2. For local-pickup-only items (furniture, swings, planters - too big to
     ship), check whether a customer's ZIP code is within the shop's
     50-mile pickup radius before they're allowed to buy online.

Deliberately separate from LonestarAdminApp (which is password-gated for
internal invoice use only) - this endpoint has to be reachable by any
visitor's browser, so it must never share that app's auth boundary or its
secrets.

Prices (and fulfillment type) are looked up server-side from catalog.py and
recomputed here - the browser only ever sends an id and a quantity, never a
price.

Config comes entirely from environment variables (see bottom of this file).
"""

import json
import math
import os
import re
import urllib.error
import urllib.request

import stripe
from flask import Flask, jsonify, request

from catalog import PRODUCTS

app = Flask(__name__)

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

ALLOWED_ORIGINS = {
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://lonestarbuckeyes.com,https://www.lonestarbuckeyes.com",
    ).split(",")
    if o.strip()
}
SUCCESS_URL = os.environ.get("SUCCESS_URL", "https://lonestarbuckeyes.com/order-success.html")
CANCEL_URL = os.environ.get("CANCEL_URL", "https://lonestarbuckeyes.com/order-canceled.html")

# Weight-based flat-rate shipping tiers, in cents, keyed by the top of each
# band in ounces (inclusive) - a couple of tiers so a single bottle opener
# and a cutting board aren't charged the same to ship. Rough estimate based
# on typical USPS Ground Advantage retail rates for nearby zones as of
# 2026-08 - not a live carrier-rate lookup, so revisit once real shipping
# costs/labels are known. Must stay sorted ascending by weight. Mirrors
# SHIPPING_WEIGHT_TIERS in cart.js, which is what drives the on-page
# estimate shown before checkout - this is what actually gets charged.
WEIGHT_SHIPPING_TIERS = [
    (16, 799),        # up to 1 lb -> $7.99
    (float("inf"), 1399),  # over 1 lb -> $13.99
]


def shipping_cents(total_oz):
    """Flat shipping charge for `total_oz` of combined "ships" items,
    per WEIGHT_SHIPPING_TIERS."""
    for max_oz, cents in WEIGHT_SHIPPING_TIERS:
        if total_oz <= max_oz:
            return cents
    return WEIGHT_SHIPPING_TIERS[-1][1]

# Shop location (ZIP 75072, McKinney, TX) and how far customers can be and
# still buy a pickup-only item online. Straight-line ("as the crow flies")
# distance, not driving distance - keeps this free (no paid routing API).
# 75072 itself isn't in the free ZIP geocoder's database, so these
# coordinates are the average of the three neighboring McKinney ZIPs
# (75069/75070/75071) it does have - accurate to within a mile or two,
# which doesn't matter for a 50-mile radius check. Override with real
# coordinates via env vars if you want more precision later.
SHOP_LAT = float(os.environ.get("SHOP_LAT", "33.2019"))
SHOP_LNG = float(os.environ.get("SHOP_LNG", "-96.6753"))
PICKUP_RADIUS_MILES = float(os.environ.get("PICKUP_RADIUS_MILES", "50"))

ZIP_RE = re.compile(r"^\d{5}$")


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def tier_price(product, qty):
    """Per-unit price for `qty` of `product`, honoring bulk-quantity tiers
    if the product has any (see catalog.py). Falls back to the flat
    `price` for products with no `tiers`."""
    tiers = product.get("tiers")
    if not tiers:
        return product["price"]
    unit_price = tiers[0]["price"]
    for tier in tiers:
        if qty >= tier["min_qty"]:
            unit_price = tier["price"]
    return unit_price


def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8  # Earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def zip_to_latlng(zip_code):
    """Free, no-signup ZIP -> lat/lng lookup via Zippopotam.us. Returns
    (lat, lng) or None if the ZIP is invalid/unknown or the lookup fails."""
    try:
        with urllib.request.urlopen(f"https://api.zippopotam.us/us/{zip_code}", timeout=5) as resp:
            data = json.loads(resp.read())
        place = data["places"][0]
        return float(place["latitude"]), float(place["longitude"])
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError):
        return None


def check_pickup_eligibility(zip_code):
    """Returns (eligible: bool, distance_miles: float|None, error: str|None)."""
    if not zip_code or not ZIP_RE.match(zip_code):
        return False, None, "Please enter a valid 5-digit ZIP code."
    latlng = zip_to_latlng(zip_code)
    if latlng is None:
        return False, None, "We couldn't find that ZIP code. Please double-check it or contact us directly."
    distance = haversine_miles(SHOP_LAT, SHOP_LNG, latlng[0], latlng[1])
    return distance <= PICKUP_RADIUS_MILES, round(distance, 1), None


@app.route("/api/check-pickup-zip", methods=["OPTIONS"])
def preflight_zip_check():
    return "", 204


@app.route("/api/check-pickup-zip", methods=["POST"])
def api_check_pickup_zip():
    payload = request.get_json(force=True, silent=True) or {}
    zip_code = (payload.get("zip") or "").strip()

    eligible, distance, error = check_pickup_eligibility(zip_code)
    if error:
        return jsonify({"error": error}), 400

    return jsonify({
        "eligible": eligible,
        "distance_miles": distance,
        "radius_miles": PICKUP_RADIUS_MILES,
    })


@app.route("/api/create-checkout-session", methods=["OPTIONS"])
def preflight():
    return "", 204


def customization_description(customization):
    """Short human-readable line for the Stripe line item, so the owner sees
    the engraving instructions right on the order/receipt. The actual logo
    FILE (if any) was already emailed via Formspree at add-to-cart time
    (see submitLogoCustomization() in cart.js) - this is just the pointer to
    it. Text and logo are independent, not mutually exclusive - a customer
    can supply either or both, so both parts are included when present.
    Purely descriptive text handed to Stripe's API, not rendered as HTML
    anywhere, so no escaping is needed - just length caps for tidiness."""
    if not isinstance(customization, dict):
        return None
    parts = []
    text = str(customization.get("text") or "").strip()[:60]
    if text:
        font = str(customization.get("font") or "").strip()[:40]
        parts.append(f'"{text}" ({font})' if font else f'"{text}"')
    logo_ref = str(customization.get("logoRef") or "").strip()[:20]
    if logo_ref:
        parts.append(f"logo emailed separately, ref {logo_ref}")
    if not parts:
        return None
    return "Engrave: " + " + ".join(parts)


def stain_addon(product, stain):
    """Returns (upcharge_cents, description) for a selected stain color on a
    product that offers one, or (0, None) if no stain was selected or the
    product has no stain_upcharge at all. The upcharge amount always comes
    from the catalog, never from what the browser sent - only the color
    name is customer-supplied."""
    upcharge = product.get("stain_upcharge")
    if not upcharge or not isinstance(stain, dict):
        return 0, None
    color = str(stain.get("color") or "").strip()[:40]
    if not color:
        return 0, None
    return upcharge, f"Stain: {color} (+${upcharge / 100:.2f})"


@app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session():
    payload = request.get_json(force=True, silent=True) or {}
    raw_items = payload.get("items") or []

    line_items = []
    fulfillments = set()
    ship_weight_oz_total = 0
    for entry in raw_items:
        product = PRODUCTS.get((entry or {}).get("id"))
        try:
            qty = int(entry.get("qty", 0))
        except (TypeError, ValueError, AttributeError):
            qty = 0
        if not product or qty <= 0:
            continue
        fulfillments.add(product["fulfillment"])
        if product["fulfillment"] == "ships":
            ship_weight_oz_total += product.get("ship_weight_oz", 0) * qty

        stain_cents, stain_description = stain_addon(product, (entry or {}).get("stain"))

        description_parts = []
        engrave_description = customization_description((entry or {}).get("customization"))
        if engrave_description:
            description_parts.append(engrave_description)
        if stain_description:
            description_parts.append(stain_description)

        product_data = {"name": product["name"]}
        if description_parts:
            product_data["description"] = " | ".join(description_parts)

        line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": product_data,
                "unit_amount": tier_price(product, qty) + stain_cents,
            },
            "quantity": qty,
        })

    if not line_items:
        return jsonify({"error": "Cart is empty or contains unknown items."}), 400

    if len(fulfillments) > 1:
        return jsonify({
            "error": "This cart mixes shippable items with local-pickup-only items. "
                     "Please check out separately for each."
        }), 400

    is_pickup = fulfillments == {"pickup"}

    session_kwargs = {
        "mode": "payment",
        "line_items": line_items,
        "success_url": f"{SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": CANCEL_URL,
    }

    if is_pickup:
        zip_code = (payload.get("zip") or "").strip()
        eligible, distance, error = check_pickup_eligibility(zip_code)
        if error:
            return jsonify({"error": error}), 400
        if not eligible:
            return jsonify({
                "error": f"That ZIP code is about {distance} miles from our North Texas shop, "
                         f"outside our {PICKUP_RADIUS_MILES}-mile local pickup range. "
                         "Please contact us directly to discuss options."
            }), 400
        session_kwargs["phone_number_collection"] = {"enabled": True}
        session_kwargs["custom_text"] = {
            "submit": {"message": "Local pickup only - we'll contact you to schedule a pickup time in North Texas."}
        }
    else:
        session_kwargs["shipping_address_collection"] = {"allowed_countries": ["US"]}
        session_kwargs["shipping_options"] = [{
            "shipping_rate_data": {
                "type": "fixed_amount",
                "fixed_amount": {"amount": shipping_cents(ship_weight_oz_total), "currency": "usd"},
                "display_name": "Flat rate shipping",
            },
        }]

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"url": session.url})


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


# ---------------------------------------------------------------------------
# Required environment variables:
#   STRIPE_SECRET_KEY    - Stripe secret key (sk_test_... while testing,
#                           sk_live_... once ready for real orders)
#
# Optional (sensible defaults shown):
#   ALLOWED_ORIGINS       = https://lonestarbuckeyes.com,https://www.lonestarbuckeyes.com
#   SUCCESS_URL           = https://lonestarbuckeyes.com/order-success.html
#   CANCEL_URL            = https://lonestarbuckeyes.com/order-canceled.html
#   SHOP_LAT              = 33.2019   (McKinney, TX area - see comment in code)
#   SHOP_LNG              = -96.6753
#   PICKUP_RADIUS_MILES   = 50     (straight-line distance, not driving distance)
#
# Shipping is weight-based (see WEIGHT_SHIPPING_TIERS above) rather than an
# env var - edit that list directly in code to change the rates/breakpoints.
# ---------------------------------------------------------------------------
