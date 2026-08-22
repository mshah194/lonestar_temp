"""
Server-side product catalog — the source of truth for what Stripe actually
charges. Must be kept in sync by hand with products.js in the
lonestarbuckeyes static site repo (same ids, prices and fulfillment); the
browser only sends an id + quantity, never a price, so this file is what
really decides how much a customer pays.

fulfillment "ships"  -> weight-based flat-rate shipping (see
                        WEIGHT_SHIPPING_TIERS/shipping_cents() in app.py),
                        address collected at checkout. Every "ships"
                        product needs a "ship_weight_oz" so that
                        computation has something to work from.
fulfillment "pickup" -> local pickup only near McKinney, TX (75072). A cart
                        of pickup items must include a "zip" that passes the
                        50-mile check in app.py before a Checkout Session is
                        created, and can't be mixed with "ships" items in
                        the same order (see create_checkout_session).

PLACEHOLDER PRICES on the "pickup" items — edit before real launch. All
prices are in cents.

Some products have a "tiers" list for bulk-quantity pricing (currently the
bottle opener) instead of one flat "price" - see tier_price() in app.py,
which is what actually computes the per-unit charge.

Some "pickup" products have a "stain_upcharge" (currently both porch swings)
- an optional flat add-on in cents charged when the customer picks a stain
color, added on top of tier_price() - see stain_addon() in app.py.
"""

PRODUCTS = {
    # Tiered quantity pricing - same schedule as the bottle opener, see its
    # comment for how "tiers"/tier_price() work.
    "keychain-custom": {
        "name": "Custom Engraved Keychain",
        "price": 1000,  # $10.00 each, 1-4 pcs
        "fulfillment": "ships",
        "ship_weight_oz": 1,
        "tiers": [
            {"min_qty": 1, "price": 1000},   # $10.00 each
            {"min_qty": 5, "price": 900},    # $9.00 each
            {"min_qty": 10, "price": 850},   # $8.50 each
            {"min_qty": 20, "price": 800},   # $8.00 each
            {"min_qty": 30, "price": 750},   # $7.50 each
            {"min_qty": 50, "price": 600},   # $6.00 each
        ],
    },
    # Two sizes, same sizeOptions/hiddenFromGrid pattern as the cedar
    # planter - see products.js. Real prices from the owner 2026-08-16, but
    # no confirmed cost basis yet - adjust if needed.
    "cutting-board-12x8": {
        "name": 'Cutting Board (12" x 8")',
        "price": 3200,  # $32.00
        "fulfillment": "ships",
        "ship_weight_oz": 24,  # ~1.5 lb bamboo board
    },
    "cutting-board-15x11": {
        "name": 'Cutting Board (15" x 11")',
        "price": 4400,  # $44.00
        "fulfillment": "ships",
        "ship_weight_oz": 32,  # ~2 lb bamboo board
    },
    # Tiered quantity pricing - same schedule as the keychain/bottle opener.
    "bookmark-custom": {
        "name": "Custom Engraved Bookmark",
        "price": 1000,  # $10.00 each, 1-4 pcs
        "fulfillment": "ships",
        "ship_weight_oz": 1,
        "tiers": [
            {"min_qty": 1, "price": 1000},   # $10.00 each
            {"min_qty": 5, "price": 900},    # $9.00 each
            {"min_qty": 10, "price": 850},   # $8.50 each
            {"min_qty": 20, "price": 800},   # $8.00 each
            {"min_qty": 30, "price": 750},   # $7.50 each
            {"min_qty": 50, "price": 600},   # $6.00 each
        ],
    },
    # Sold per-coaster; "set of N" / custom quantities on the site are just
    # different qtys of the same unit price, so there's no separate bundle
    # SKU to keep in sync here.
    # Same Round/Square shape split as the slate coasters below - same
    # price either shape, two ids so Stripe/the owner know which was bought.
    "coaster-cork-round": {
        "name": "Cork Engraved Coasters (Round)",
        "price": 625,  # $6.25 each -> set of 4 = $25.00
        "fulfillment": "ships",
        "ship_weight_oz": 1.5,
    },
    "coaster-cork-square": {
        "name": "Cork Engraved Coasters (Square)",
        "price": 625,  # $6.25 each -> set of 4 = $25.00
        "fulfillment": "ships",
        "ship_weight_oz": 1.5,
    },
    # Slate coasters come in two shapes (different photo sets on the site)
    # at the same per-coaster price. Kept as two ids so the name Stripe
    # shows - and what the owner sees on the order - says which shape was
    # bought, even though the price is identical.
    "coaster-slate-round": {
        "name": "Slate Engraved Coasters (Round)",
        "price": 750,  # $7.50 each -> set of 4 = $30.00
        "fulfillment": "ships",
        "ship_weight_oz": 6,  # slate is much heavier than cork/wood
    },
    "coaster-slate-square": {
        "name": "Slate Engraved Coasters (Square)",
        "price": 750,  # $7.50 each -> set of 4 = $30.00
        "fulfillment": "ships",
        "ship_weight_oz": 6,
    },
    # Tiered quantity pricing - price per unit drops at each breakpoint.
    # Must stay sorted by ascending min_qty; tier_price() below walks it
    # looking for the highest min_qty the requested qty meets. Keep this in
    # sync with the matching `tieredPricing` array in products.js.
    "bottle-opener-custom": {
        "name": "Custom Engraved Bottle Opener",
        "price": 1000,  # $10.00 each, 1-4 pcs
        "fulfillment": "ships",
        "ship_weight_oz": 3,
        "tiers": [
            {"min_qty": 1, "price": 1000},   # $10.00 each
            {"min_qty": 5, "price": 900},    # $9.00 each
            {"min_qty": 10, "price": 850},   # $8.50 each
            {"min_qty": 20, "price": 800},   # $8.00 each
            {"min_qty": 30, "price": 750},   # $7.50 each
            {"min_qty": 50, "price": 600},   # $6.00 each
        ],
    },

    # --- Local pickup only (McKinney, TX / 75072 area) ------------------
    "console-table-farmhouse": {
        "name": "Farmhouse Console Table",
        "price": 24900,  # PLACEHOLDER — $249.00
        "fulfillment": "pickup",
    },
    "bench-solid-wood": {
        "name": "Modern Solid Wood Bench",
        "price": 14900,  # PLACEHOLDER — $149.00
        "fulfillment": "pickup",
    },
    "porch-swing-classic": {
        "name": "Classic Porch Swing",
        "price": 39900,  # PLACEHOLDER — $399.00
        "fulfillment": "pickup",
        "stain_upcharge": 7900,  # +$79.00 for a stained finish, owner-specified 2026-08-15
    },
    "porch-swing-console": {
        "name": "Porch Swing with Center Console",
        "price": 49900,  # PLACEHOLDER — $499.00
        "fulfillment": "pickup",
        "stain_upcharge": 7900,  # +$79.00 for a stained finish, owner-specified 2026-08-15
    },
    # Cedar planters: real prices from the owner (not placeholders), one per
    # standard size. The site shows these as one listing with a size
    # dropdown (see the `sizeOptions`/`hiddenFromGrid` handling in
    # products.js) but each size still needs its own real entry here since
    # that's what Stripe actually charges.
    "planter-cedar-12x12": {
        "name": 'Cedar Planter (12" x 12" x 12")',
        "price": 3000,  # $30.00
        "fulfillment": "pickup",
    },
    "planter-cedar-16x16": {
        "name": 'Cedar Planter (16" x 16" x 16")',
        "price": 4500,  # $45.00
        "fulfillment": "pickup",
    },
    "planter-cedar-24x16": {
        "name": 'Cedar Planter (24" x 16" x 16")',
        "price": 4500,  # $45.00
        "fulfillment": "pickup",
    },
    "planter-cedar-32x16": {
        "name": 'Cedar Planter (32" x 16" x 16")',
        "price": 5500,  # $55.00
        "fulfillment": "pickup",
    },
    "planter-cedar-48x16": {
        "name": 'Cedar Planter (48" x 16" x 16")',
        "price": 9500,  # $95.00
        "fulfillment": "pickup",
    },
    "planter-cedar-48x24": {
        "name": 'Cedar Planter (48" x 24" x 16")',
        "price": 10500,  # $105.00
        "fulfillment": "pickup",
    },
    "planter-cedar-70x24": {
        "name": 'Cedar Planter (70" x 24" x 16")',
        "price": 12500,  # $125.00
        "fulfillment": "pickup",
    },
    "garden-bench": {
        "name": "Garden Bench",
        "price": 17900,  # PLACEHOLDER — $179.00
        "fulfillment": "pickup",
    },
}
