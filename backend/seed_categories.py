"""Default category taxonomy seeded for every new user.

Each entry: (slug, name, kind, color slot, icon, [children as (slug, name, icon)]).
Colors are palette slot keys c1..c12 resolved by the frontend per theme.
"""

DEFAULT_TAXONOMY = [
    ("income", "Income", "income", "c6", "banknote", [
        ("income.salary", "Salary", "briefcase"),
        ("income.interest", "Interest", "percent"),
        ("income.refunds", "Refunds", "rotate-ccw"),
        ("income.other", "Other Income", "coins"),
    ]),
    ("transfers", "Transfers", "transfer", "c10", "arrow-left-right", [
        ("transfers.card_payment", "Credit Card Payment", "credit-card"),
        ("transfers.internal", "Internal Transfer", "repeat"),
    ]),
    ("groceries", "Groceries", "expense", "c3", "shopping-cart", []),
    ("dining", "Dining", "expense", "c2", "utensils", [
        ("dining.restaurants", "Restaurants", "utensils"),
        ("dining.coffee", "Coffee", "coffee"),
        ("dining.delivery", "Delivery", "bike"),
        ("dining.bars", "Bars", "wine"),
    ]),
    ("transport", "Transport", "expense", "c1", "car", [
        ("transport.fuel", "Fuel", "fuel"),
        ("transport.transit", "Public Transit", "train"),
        ("transport.rideshare", "Rideshare", "car-taxi"),
        ("transport.parking", "Parking & Tolls", "parking"),
        ("transport.maintenance", "Auto Maintenance", "wrench"),
    ]),
    ("housing", "Housing", "expense", "c7", "home", [
        ("housing.rent", "Rent / Mortgage", "key"),
        ("housing.maintenance", "Home Maintenance", "hammer"),
        ("housing.furniture", "Furniture", "armchair"),
    ]),
    ("utilities", "Utilities", "expense", "c9", "zap", [
        ("utilities.electricity", "Electricity", "zap"),
        ("utilities.gas", "Gas", "flame"),
        ("utilities.water", "Water", "droplet"),
        ("utilities.internet", "Internet", "wifi"),
        ("utilities.phone", "Phone", "smartphone"),
    ]),
    ("subscriptions", "Subscriptions", "expense", "c12", "repeat", [
        ("subscriptions.streaming", "Streaming", "tv"),
        ("subscriptions.software", "Software", "monitor"),
        ("subscriptions.memberships", "Memberships", "badge-check"),
    ]),
    ("shopping", "Shopping", "expense", "c4", "shopping-bag", [
        ("shopping.online", "Online", "globe"),
        ("shopping.clothing", "Clothing", "shirt"),
        ("shopping.electronics", "Electronics", "laptop"),
    ]),
    ("health", "Health", "expense", "c8", "heart-pulse", [
        ("health.pharmacy", "Pharmacy", "pill"),
        ("health.doctor", "Doctor", "stethoscope"),
        ("health.fitness", "Fitness", "dumbbell"),
    ]),
    ("insurance", "Insurance", "expense", "c11", "shield", []),
    ("travel", "Travel", "expense", "c5", "plane", [
        ("travel.flights", "Flights", "plane"),
        ("travel.hotels", "Hotels", "bed"),
        ("travel.car_rental", "Car Rental", "car"),
    ]),
    ("entertainment", "Entertainment", "expense", "c2", "clapperboard", [
        ("entertainment.events", "Events", "ticket"),
        ("entertainment.games", "Games", "gamepad"),
        ("entertainment.books", "Books", "book"),
    ]),
    ("education", "Education", "expense", "c1", "graduation-cap", []),
    ("personal_care", "Personal Care", "expense", "c5", "sparkles", []),
    ("gifts", "Gifts & Donations", "expense", "c12", "gift", []),
    ("pets", "Pets", "expense", "c3", "paw-print", []),
    ("cash", "Cash & ATM", "expense", "c10", "wallet", []),
    ("fees", "Fees", "expense", "c8", "receipt", [
        ("fees.bank", "Bank Fees", "landmark"),
        ("fees.interest", "Interest Charges", "percent"),
    ]),
    ("taxes", "Taxes", "expense", "c9", "landmark", []),
]


def seed_for_user(conn, user_id):
    """Add any default category the user does not have yet. Existing categories are never
    renamed or recolored, and a user's own category with the same name under the same parent
    counts as present (so this can never violate the unique name index)."""
    with conn.cursor() as cur:
        for order, (slug, name, kind, color, icon, children) in enumerate(DEFAULT_TAXONOMY):
            parent_id = _find(cur, user_id, None, slug, name)
            if parent_id is None:
                cur.execute(
                    """INSERT INTO categories (user_id, parent_id, name, slug, kind, color, icon, is_system, sort_order)
                       VALUES (%s, NULL, %s, %s, %s, %s, %s, TRUE, %s) RETURNING id""",
                    (user_id, name, slug, kind, color, icon, order),
                )
                parent_id = cur.fetchone()[0]
            for corder, (cslug, cname, cicon) in enumerate(children):
                if _find(cur, user_id, parent_id, cslug, cname) is not None:
                    continue
                cur.execute(
                    """INSERT INTO categories (user_id, parent_id, name, slug, kind, color, icon, is_system, sort_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s)""",
                    (user_id, parent_id, cname, cslug, kind, color, cicon, corder),
                )
        cur.execute(
            """UPDATE users SET preferences = preferences || '{"categories_seeded": true}'::jsonb
               WHERE id = %s""",
            (user_id,),
        )
    conn.commit()


def _find(cur, user_id, parent_id, slug, name):
    """Existing category id by slug, else by (parent, name) so user-made twins are respected."""
    cur.execute("SELECT id FROM categories WHERE user_id = %s AND slug = %s", (user_id, slug))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "SELECT id FROM categories WHERE user_id = %s AND COALESCE(parent_id, 0) = %s AND lower(name) = lower(%s)",
        (user_id, parent_id or 0, name),
    )
    row = cur.fetchone()
    return row[0] if row else None


def default_icons():
    icons = set()
    for _slug, _name, _kind, _color, icon, children in DEFAULT_TAXONOMY:
        icons.add(icon)
        for _s, _n, cicon in children:
            icons.add(cicon)
    return sorted(icons)
