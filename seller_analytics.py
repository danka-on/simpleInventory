"""Inventory and seller analytics built from local warehouse and sold databases."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import statistics
from collections import defaultdict
from contextlib import closing
from pathlib import Path


KNOWN_BRANDS = (
    "Mackenzie-Childs", "Villeroy & Boch", "Certified International",
    "Creative Co-Op", "Euro Ceramica", "Fitz and Floyd", "Gordon Ramsay",
    "Hampton Forge", "Michael Aram", "Nearly Natural", "RiverRidge Home",
    "Royal Doulton", "Kate Spade", "Marquis by Waterford", "Black+Decker",
    "Zwilling", "Henckels", "Cuisinart", "Pfaltzgraff", "Godinger", "Lenox",
    "Noritake", "Waterford", "Mikasa", "Fortessa", "Gibson", "Elama",
    "Calphalon", "Nambe", "JoyJolt", "Cambridge", "Tableau", "Oake",
)

CATEGORY_RULES = (
    ("Cutlery & Flatware", (
        "knife", "knives", "cutlery", "flatware", "silverware", "self-sharpening",
        "sharpening block",
    )),
    ("Serveware", (
        "serving", "serveware", "platter", "cake stand", "pitcher", "tray",
        "trifle", "lazy susan", "tier rack", "centerpiece", "server", "hostess",
        "butter dish", "nut dish", "salt & pepper", "salt and pepper",
    )),
    ("Dinnerware", (
        "dinnerware", "dinner plate", "salad plate", "pasta bowl", "soup bowl",
        "dinner bowl", "saucer", "place setting", "stoneware", "porcelain",
        " plate", "plates", " bowl", "bowls", "ramekin", "charger",
    )),
    ("Glassware & Barware", (
        "glass", "goblet", "tumbler", "decanter", "whiskey", "wine ", "barware",
        "highball", "lowball", "doff", "champagne", "beverage set", "crystal",
        "parfait dish",
    )),
    ("Small Appliances", (
        "vacuum", "blender", "mixer", "toaster", "coffee maker", "appliance",
        "electric kettle",
    )),
    ("Drinkware", (
        "mug", "cup", "teapot", "tea kettle", "coffee", "travel mug",
    )),
    ("Cookware & Bakeware", (
        "skillet", "saucepan", "saute pan", "frying pan", "dutch oven",
        "cookware", "bakeware", "baker", "casserole", "roaster",
    )),
    ("Table Linens", (
        "tablecloth", "table runner", " runner", "napkin", "placemat", "table linen",
    )),
    ("Bedding & Bath", (
        "duvet", "sheet", "pillow", "comforter", "quilt", "towel", "bath ",
    )),
    ("Home Decor", (
        "frame", "vase", "candle", "holder", "artificial", "floral", "decor",
        "mirror", "figurine", "clock", "lamp", "wall art", "solar powered",
        "harvest", "maple leaf",
    )),
    ("Storage & Organization", (
        "canister", "storage", "organizer", "basket", "container",
    )),
    ("Bags & Accessories", (
        " tote", "satchel", "handbag", "purse", " belt", "wallet",
    )),
    ("Outdoor & Picnic", (
        "picnic", "camping", "beach blanket", "outdoor",
    )),
    ("Home Improvement", (
        "toilet seat", "door stopper", "door sweep", "weather stripping",
    )),
    ("Automotive", (
        "starter motor", "automotive", "vehicle", "car ",
    )),
)

GENERIC_BRAND_STARTS = {
    "new", "the", "set", "sets", "collection", "dinnerware", "glass",
    "glasses", "serving", "oven", "table", "kitchen", "home", "white",
    "black", "classic", "premium", "porcelain", "stoneware", "stainless",
}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        try:
            return dt.datetime.strptime(text[:10], "%Y-%m-%d")
        except (TypeError, ValueError):
            return None


def _canonical_upc(value):
    text = str(value or "").strip().split("-", 1)[0]
    digits = re.sub(r"\D", "", text)
    return digits.lstrip("0") or digits


def _title_key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _item_key(barcode, title):
    upc = _canonical_upc(barcode)
    if upc:
        return f"upc:{upc}"
    normalized_title = _title_key(title)
    return f"title:{normalized_title or 'unknown'}"


def _derive_category(title):
    normalized = f" {_title_key(title)} "
    for category, keywords in CATEGORY_RULES:
        if any(keyword in normalized for keyword in keywords):
            return category
    return "Other / Unclassified"


def _derive_brand(title):
    raw = str(title or "").strip()
    lowered = raw.lower()
    for brand in KNOWN_BRANDS:
        if lowered.startswith(brand.lower()) or f" {brand.lower()} " in f" {lowered} ":
            return brand
    words = re.findall(r"[A-Za-z][A-Za-z&+'-]*", raw)
    if not words or words[0].lower() in GENERIC_BRAND_STARTS:
        return "Unknown"
    first = words[0].strip(" -")
    if len(first) < 3:
        return "Unknown"
    return first.title() if first.isupper() else first


def _median(values):
    return round(float(statistics.median(values)), 1) if values else None


def _safe_quantity(value, default=0):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def _safe_money(value):
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _new_item(key):
    return {
        "_key": key,
        "barcode": "",
        "title": "",
        "current_qty": 0,
        "_age_weight": 0.0,
        "_age_qty": 0,
        "oldest_age_days": 0,
        "aged_180_qty": 0,
        "locations": set(),
        "sold_units": 0,
        "orders": 0,
        "revenue": 0.0,
        "refunds": 0.0,
        "returned_units": 0,
        "_sell_days": [],
        "_fast_units": 0,
        "_slow_units": 0,
        "last_sale_at": None,
        "first_sale_at": None,
        "stores": defaultdict(int),
        "_cost_values": [],
        "_retail_values": [],
    }


def _age_band(days):
    if days <= 30:
        return "0–30 days"
    if days <= 60:
        return "31–60 days"
    if days <= 90:
        return "61–90 days"
    if days < 120:
        return "91–119 days"
    if days <= 180:
        return "120–180 days"
    return "181+ days"


AGE_BAND_NAMES = (
    "0–30 days", "31–60 days", "61–90 days",
    "91–119 days", "120–180 days", "181+ days",
)


def _load_warehouse_age_data(base):
    warehouse_path = Path(base) / "searchRack.db"
    age_batches = defaultdict(list)
    with closing(_connect_readonly(warehouse_path)) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "inventory_age_batches" in tables:
            for row in conn.execute(
                "SELECT searchrack_id, received_at, quantity FROM inventory_age_batches"
            ):
                age_batches[row["searchrack_id"]].append(dict(row))
        warehouse_rows = [
            dict(row) for row in conn.execute(
                """SELECT ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY, CREATED_AT
                   FROM SEARCHRACK
                   WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0"""
            )
        ]
    return warehouse_rows, age_batches


def _warehouse_age_counts(warehouse_rows, age_batches, now):
    counts = {name: 0 for name in AGE_BAND_NAMES}
    for row in warehouse_rows:
        qty = _safe_quantity(row["QUANTITY"])
        batches = age_batches.get(row["ID"]) or [{
            "received_at": row["CREATED_AT"],
            "quantity": qty,
        }]
        accounted = 0
        for batch in batches:
            batch_qty = _safe_quantity(batch.get("quantity"))
            received = _parse_date(batch.get("received_at"))
            if not received or batch_qty <= 0:
                continue
            batch_qty = min(batch_qty, max(0, qty - accounted))
            accounted += batch_qty
            counts[_age_band(max(0, (now - received).days))] += batch_qty
        if accounted < qty:
            received = _parse_date(row["CREATED_AT"]) or now
            counts[_age_band(max(0, (now - received).days))] += qty - accounted
    return counts


def build_inventory_age_summary(base_dir):
    """Return the lightweight age infographic payload used by Warehouse."""
    now = dt.datetime.now()
    warehouse_rows, age_batches = _load_warehouse_age_data(base_dir)
    counts = _warehouse_age_counts(warehouse_rows, age_batches, now)
    return {
        "success": True,
        "generated_at": now.isoformat(),
        "inventory_units": sum(counts.values()),
        "inventory_rows": len(warehouse_rows),
        "age_bands": [
            {"name": name, "units": counts[name]}
            for name in AGE_BAND_NAMES
        ],
    }


def _load_cleanup_price_evidence(base_dir):
    """Load active marketplace prices and the latest sold unit price by UPC."""
    base = Path(base_dir)
    listings = defaultdict(list)

    marketplace_sources = (
        (
            base / "amazonStore.db",
            """SELECT UPC AS upc, PRICE AS price, 'Amazon' AS marketplace,
                      ASIN AS listing_id
               FROM ITEMS
               WHERE LOWER(COALESCE(STATUS, '')) = 'active'
                 AND COALESCE(PRICE, 0) > 0""",
        ),
        (
            base / "ebayStore.db",
            """SELECT UPC AS upc, Price AS price, 'eBay' AS marketplace,
                      ItemID AS listing_id
               FROM INVENTORY
               WHERE LOWER(COALESCE(List_State, '')) = 'active'
                 AND CAST(COALESCE(Price, 0) AS REAL) > 0""",
        ),
    )
    for path, query in marketplace_sources:
        if not path.exists():
            continue
        try:
            with closing(_connect_readonly(path)) as conn:
                rows = conn.execute(query)
                for row in rows:
                    upc = _canonical_upc(row["upc"])
                    price = _safe_money(row["price"])
                    if not upc or price <= 0:
                        continue
                    listings[upc].append({
                        "marketplace": row["marketplace"],
                        "price": round(price, 2),
                        "listing_id": str(row["listing_id"] or ""),
                    })
        except sqlite3.Error:
            # Listing databases are optional evidence; sold history remains usable.
            continue

    latest_sold = {}
    sold_path = base / "sold.db"
    if sold_path.exists():
        try:
            with closing(_connect_readonly(sold_path)) as conn:
                rows = conn.execute(
                    """SELECT barcode, source_upc, source_base_upc, quantity,
                              price, paid_time, store
                       FROM orders
                       WHERE COALESCE(price, 0) > 0
                         AND LOWER(COALESCE(store, '')) != 'test'
                         AND COALESCE(removal_cancelled, 0) = 0"""
                )
                for row in rows:
                    upc = _canonical_upc(
                        row["barcode"] or row["source_upc"] or row["source_base_upc"]
                    )
                    paid = _parse_date(row["paid_time"])
                    if not upc or not paid:
                        continue
                    qty = max(1, _safe_quantity(row["quantity"], 1))
                    unit_price = _safe_money(row["price"]) / qty
                    prior = latest_sold.get(upc)
                    if unit_price > 0 and (prior is None or paid > prior["sold_at"]):
                        latest_sold[upc] = {
                            "price": round(unit_price, 2),
                            "sold_at": paid,
                            "store": str(row["store"] or "").strip().title(),
                        }
        except sqlite3.Error:
            pass

    return listings, latest_sold


def build_inventory_cleanup(
    base_dir,
    clearance_age=180,
    disposal_age=240,
    markdown_lead=60,
):
    """Build an active-inventory cleanup queue with timing and evidence."""
    analytics = build_seller_analytics(base_dir, 0)
    now = _parse_date(analytics.get("generated_at")) or dt.datetime.now()
    markdown_age = max(30, clearance_age - markdown_lead)
    cleanup_items = []
    listing_prices, latest_sold_prices = _load_cleanup_price_evidence(base_dir)

    for source in analytics["groups"]["item"]:
        if source["current_qty"] <= 0:
            continue
        item = dict(source)
        age = max(item["average_age_days"], item["oldest_age_days"])
        sold = item["sold_units"]
        velocity = item["monthly_velocity"]
        supply = item["months_of_supply"]
        return_rate = item["return_rate"] or 0
        last_sale = _parse_date(item["last_sale_at"])
        days_since_sale = max(0, (now - last_sale).days) if last_sale else None
        unit_cost = item["average_cost"]
        known_cost_value = (
            round(unit_cost * item["current_qty"], 2)
            if unit_cost is not None else None
        )
        upc = _canonical_upc(item["barcode"])
        active_listings = sorted(
            listing_prices.get(upc, []),
            key=lambda listing: listing["price"],
            reverse=True,
        )
        current_listing = active_listings[0] if active_listings else None
        latest_sold = latest_sold_prices.get(upc)
        if current_listing:
            decision_price = current_listing["price"]
            price_source = f"Active {current_listing['marketplace']} listing"
        elif latest_sold:
            decision_price = latest_sold["price"]
            price_source = (
                f"Latest {latest_sold['store'] or 'marketplace'} sale"
            )
        elif item["average_price"] > 0:
            decision_price = item["average_price"]
            price_source = "Average sold price"
        else:
            decision_price = None
            price_source = None
        market_value = (
            round(decision_price * item["current_qty"], 2)
            if decision_price is not None else None
        )
        value_protected = (
            (decision_price is not None and decision_price > 40)
            or (market_value is not None and market_value > 100)
            or (unit_cost is not None and unit_cost > 25)
            or (known_cost_value is not None and known_cost_value > 50)
        )
        healthy_demand = (
            sold >= 3
            and velocity >= 0.35
            and return_rate <= 15
            and (supply is None or supply <= 6)
        )

        urgency = min(45, age / max(disposal_age, 1) * 45)
        if sold == 0:
            urgency += 25
        elif sold <= 1:
            urgency += 15
        elif velocity < 0.25:
            urgency += 10
        if supply is None and sold == 0:
            urgency += 10
        elif supply is not None and supply > 12:
            urgency += 15
        elif supply is not None and supply > 6:
            urgency += 8
        if return_rate >= 25:
            urgency += 12
        elif return_rate >= 15:
            urgency += 6
        if days_since_sale is None:
            urgency += 5
        elif days_since_sale > 180:
            urgency += 8
        if unit_cost is not None and unit_cost <= 10:
            urgency += 5
        if market_value is not None and market_value >= 250:
            urgency += 10
        elif market_value is not None and market_value >= 100:
            urgency += 5
        if healthy_demand:
            urgency -= 25
        urgency = round(max(0, min(100, urgency)))

        if (
            age >= disposal_age
            and sold == 0
            and not healthy_demand
            and not value_protected
        ):
            action = "Donate / disposal review"
            next_action_days = 0
            reason = (
                f"{age} days old with no matched sales"
                + (
                    f"; {price_source.lower()} ${decision_price:.2f}"
                    if decision_price is not None else ""
                )
                + (
                    f"; known unit cost ${unit_cost:.2f}"
                    if unit_cost is not None else "; cost needs verification"
                )
            )
            steps = "Verify condition and value, then donate, recycle, bundle, or discard."
        elif (
            age > clearance_age
            and not healthy_demand
            and (
                sold == 0
                or supply is None
                or supply > 6
                or (days_since_sale is not None and days_since_sale > 150)
            )
        ):
            action = "Clear now"
            next_action_days = 0
            reason = f"{age} days old with weak demand evidence"
            steps = "Use an aggressive markdown, liquidation lot, or category bundle."
        elif age >= markdown_age or (supply is not None and supply > 8):
            action = "Markdown / bundle"
            next_action_days = 0
            reason = (
                f"{age} days old"
                + (f"; about {supply:.1f} months of supply" if supply is not None else "")
            )
            steps = "Review listing quality, then test a 15–25% markdown or bundle."
        elif age > 90:
            action = "Watch"
            next_action_days = max(1, markdown_age - age)
            reason = f"{age} days old; approaching the markdown window"
            steps = "Recheck listing, price, and demand at the next review date."
        else:
            action = "Keep"
            next_action_days = max(1, markdown_age - age)
            reason = "Inventory is still inside the normal holding window"
            steps = "No cleanup action currently recommended."

        if healthy_demand and action not in ("Donate / disposal review",):
            action = "Keep"
            next_action_days = 30
            reason = (
                f"Healthy demand override: {sold} sold, "
                f"{velocity:.2f} units/month, {return_rate:.1f}% returns"
            )
            steps = "Keep stocked and review supply again in 30 days."

        if (
            value_protected
            and age >= disposal_age
            and sold == 0
            and action == "Clear now"
        ):
            price_detail = (
                (
                    f"{price_source} is ${decision_price:.2f} per unit "
                    f"(${market_value:.2f} total)"
                )
                if decision_price is not None else
                f"known acquisition cost is ${known_cost_value:.2f} total"
            )
            reason = f"{age} days old with no matched sales, but {price_detail}"
            steps = "Protect the value: verify the listing, then markdown or liquidate before considering disposal."

        recovery_rates = {
            "Keep": 1.00,
            "Watch": 1.00,
            "Markdown / bundle": 0.80,
            "Clear now": 0.55,
            "Donate / disposal review": 0.00,
        }
        recovery_rate = recovery_rates[action]
        expected_recovery_value = (
            round(market_value * recovery_rate, 2)
            if market_value is not None else None
        )
        estimated_value_loss = (
            round(market_value - expected_recovery_value, 2)
            if market_value is not None else None
        )
        if known_cost_value is None:
            estimated_cost_loss = None
        elif expected_recovery_value is not None:
            estimated_cost_loss = round(
                max(0, known_cost_value - expected_recovery_value), 2
            )
        elif action == "Donate / disposal review":
            estimated_cost_loss = known_cost_value
        else:
            estimated_cost_loss = None

        action_date = (now + dt.timedelta(days=next_action_days)).date().isoformat()
        if next_action_days == 0:
            timing = "Now"
        elif next_action_days <= 30:
            timing = "Within 30 days"
        elif next_action_days <= 60:
            timing = "31–60 days"
        elif next_action_days <= 90:
            timing = "61–90 days"
        else:
            timing = "90+ days"

        item.update({
            "cleanup_action": action,
            "cleanup_urgency": urgency,
            "next_action_days": next_action_days,
            "action_date": action_date,
            "timing": timing,
            "cleanup_reason": reason,
            "cleanup_steps": steps,
            "days_since_last_sale": days_since_sale,
            "known_cost_value": known_cost_value,
            "healthy_demand_override": healthy_demand,
            "active_listing_prices": active_listings,
            "current_listing_price": (
                current_listing["price"] if current_listing else None
            ),
            "latest_sold_price": latest_sold["price"] if latest_sold else None,
            "latest_sold_price_at": (
                latest_sold["sold_at"].isoformat() if latest_sold else None
            ),
            "decision_price": decision_price,
            "price_source": price_source,
            "market_value": market_value,
            "expected_recovery_rate": round(recovery_rate * 100),
            "expected_recovery_value": expected_recovery_value,
            "estimated_value_loss": estimated_value_loss,
            "estimated_cost_loss": estimated_cost_loss,
            "value_protected": value_protected,
        })
        cleanup_items.append(item)

    action_order = {
        "Donate / disposal review": 5,
        "Clear now": 4,
        "Markdown / bundle": 3,
        "Watch": 2,
        "Keep": 1,
    }
    cleanup_items.sort(
        key=lambda item: (
            -action_order[item["cleanup_action"]],
            -item["cleanup_urgency"],
            -item["oldest_age_days"],
            item["name"].lower(),
        )
    )

    action_counts = {}
    for action in action_order:
        matching = [item for item in cleanup_items if item["cleanup_action"] == action]
        action_counts[action] = {
            "items": len(matching),
            "units": sum(item["current_qty"] for item in matching),
            "known_cost": round(sum(item["known_cost_value"] or 0 for item in matching), 2),
            "market_value": round(sum(item["market_value"] or 0 for item in matching), 2),
            "estimated_value_loss": round(
                sum(item["estimated_value_loss"] or 0 for item in matching), 2
            ),
        }

    timing_counts = []
    for timing in ("Now", "Within 30 days", "31–60 days", "61–90 days", "90+ days"):
        matching = [item for item in cleanup_items if item["timing"] == timing]
        timing_counts.append({
            "name": timing,
            "items": len(matching),
            "units": sum(item["current_qty"] for item in matching),
        })

    category_buckets = defaultdict(list)
    for item in cleanup_items:
        category_buckets[item["category"]].append(item)
    category_rows = []
    for name, members in category_buckets.items():
        cleanup_members = [
            item for item in members
            if item["cleanup_action"] in (
                "Markdown / bundle", "Clear now", "Donate / disposal review"
            )
        ]
        category_rows.append({
            "name": name,
            "inventory_units": sum(item["current_qty"] for item in members),
            "cleanup_units": sum(item["current_qty"] for item in cleanup_members),
            "cleanup_items": len(cleanup_members),
            "average_age_days": round(
                sum(item["average_age_days"] * item["current_qty"] for item in members)
                / max(1, sum(item["current_qty"] for item in members))
            ),
            "average_urgency": round(
                sum(item["cleanup_urgency"] for item in members) / len(members)
            ),
            "known_cost": round(sum(item["known_cost_value"] or 0 for item in cleanup_members), 2),
            "market_value": round(
                sum(item["market_value"] or 0 for item in cleanup_members), 2
            ),
            "estimated_value_loss": round(
                sum(item["estimated_value_loss"] or 0 for item in cleanup_members), 2
            ),
        })
    category_rows.sort(key=lambda row: (-row["cleanup_units"], -row["average_urgency"]))

    locations = sorted({
        location
        for item in cleanup_items
        for location in item.get("locations", [])
    })
    known_cost_items = [item for item in cleanup_items if item["known_cost_value"] is not None]
    priced_items = [item for item in cleanup_items if item["decision_price"] is not None]
    active_listing_items = [
        item for item in cleanup_items if item["current_listing_price"] is not None
    ]
    cleanup_actions = ("Markdown / bundle", "Clear now", "Donate / disposal review")
    cleanup_candidates = [
        item for item in cleanup_items if item["cleanup_action"] in cleanup_actions
    ]
    return {
        "success": True,
        "generated_at": now.isoformat(),
        "policy": {
            "markdown_age": markdown_age,
            "clearance_age": clearance_age,
            "disposal_age": disposal_age,
            "markdown_lead": markdown_lead,
        },
        "summary": {
            "inventory_items": len(cleanup_items),
            "inventory_units": sum(item["current_qty"] for item in cleanup_items),
            "cleanup_items": len(cleanup_candidates),
            "cleanup_units": sum(item["current_qty"] for item in cleanup_candidates),
            "clear_now_units": (
                action_counts["Clear now"]["units"]
                + action_counts["Donate / disposal review"]["units"]
            ),
            "known_inventory_cost": round(
                sum(item["known_cost_value"] or 0 for item in known_cost_items), 2
            ),
            "known_cleanup_cost": round(
                sum(item["known_cost_value"] or 0 for item in cleanup_candidates), 2
            ),
            "cost_coverage": round(
                len(known_cost_items) / max(1, len(cleanup_items)) * 100, 1
            ),
            "priced_inventory_value": round(
                sum(item["market_value"] or 0 for item in priced_items), 2
            ),
            "priced_cleanup_value": round(
                sum(item["market_value"] or 0 for item in cleanup_candidates), 2
            ),
            "estimated_cleanup_value_loss": round(
                sum(item["estimated_value_loss"] or 0 for item in cleanup_candidates), 2
            ),
            "estimated_cleanup_cost_loss": round(
                sum(item["estimated_cost_loss"] or 0 for item in cleanup_candidates), 2
            ),
            "price_coverage": round(
                len(priced_items) / max(1, len(cleanup_items)) * 100, 1
            ),
            "active_listing_coverage": round(
                len(active_listing_items) / max(1, len(cleanup_items)) * 100, 1
            ),
        },
        "action_counts": action_counts,
        "timing": timing_counts,
        "categories": category_rows,
        "items": cleanup_items,
        "filters": {
            "categories": sorted(category_buckets),
            "brands": sorted({item["brand"] for item in cleanup_items}),
            "locations": locations,
            "actions": list(action_order),
        },
        "data_quality": {
            **analytics["data_quality"],
            "priced_item_types": len(priced_items),
            "active_listing_item_types": len(active_listing_items),
            "price_item_types": len(cleanup_items),
        },
    }


def _price_band(price):
    if price <= 0:
        return "No sales price"
    if price < 25:
        return "Under $25"
    if price < 50:
        return "$25–49"
    if price < 100:
        return "$50–99"
    if price < 200:
        return "$100–199"
    return "$200+"


def _confidence(sold_units):
    if sold_units >= 8:
        return "High"
    if sold_units >= 3:
        return "Medium"
    return "Low"


def _verdict(item):
    sold = item["sold_units"]
    current = item["current_qty"]
    oldest = item["oldest_age_days"]
    median_days = item["median_days_to_sell"]
    return_rate = item["return_rate"]
    supply = item["months_of_supply"]

    if current > 0 and oldest > 180 and sold == 0:
        return "Clearance / disposal review"
    if current > 0 and oldest > 240 and (sold <= 1 or supply is None or supply > 12):
        return "Clearance / disposal review"
    if sold >= 3 and return_rate is not None and return_rate >= 25:
        return "Avoid / investigate"
    if current > 0 and oldest >= 120 and (sold == 0 or (supply is not None and supply > 8)):
        return "Avoid / slow mover"
    if sold >= 3 and (median_days is None or median_days <= 60) and (return_rate or 0) <= 10:
        return "Buy more"
    if sold >= 2 and (return_rate or 0) >= 20:
        return "Return watch"
    return "Monitor"


def _verdict_reason(item):
    verdict = item["verdict"]
    if verdict == "Buy more":
        speed = (
            f"median {item['median_days_to_sell']:.0f} days to sell"
            if item["median_days_to_sell"] is not None else
            f"{item['sold_units']} units sold"
        )
        return f"{speed}; {item['return_rate'] or 0:.0f}% observed return rate"
    if verdict == "Clearance / disposal review":
        sales = "no matched sales" if item["sold_units"] == 0 else f"only {item['sold_units']} unit sold"
        return f"{item['oldest_age_days']} days old; {sales}"
    if verdict.startswith("Avoid"):
        if (item["return_rate"] or 0) >= 25:
            return f"{item['return_rate']:.0f}% observed return rate across {item['sold_units']} sold units"
        return f"{item['oldest_age_days']} days old with weak sales velocity"
    if verdict == "Return watch":
        return f"{item['return_rate']:.0f}% observed return rate; {_confidence(item['sold_units']).lower()} confidence"
    return "Insufficient evidence for a strong action"


def _public_item(item, analysis_months):
    sold_units = item["sold_units"]
    returned_units = item["returned_units"]
    avg_age = (
        item["_age_weight"] / item["_age_qty"]
        if item["_age_qty"] else 0
    )
    median_days = _median(item["_sell_days"])
    velocity = sold_units / max(analysis_months, 1)
    months_supply = item["current_qty"] / velocity if velocity > 0 else None
    average_price = item["revenue"] / sold_units if sold_units else 0
    return_rate = (returned_units / sold_units * 100) if sold_units else None
    title = item["title"] or item["barcode"] or "Unknown item"
    public = {
        "key": item["_key"],
        "barcode": item["barcode"],
        "name": title,
        "brand": _derive_brand(title),
        "category": _derive_category(title),
        "current_qty": item["current_qty"],
        "average_age_days": round(avg_age),
        "oldest_age_days": item["oldest_age_days"],
        "aged_180_qty": item["aged_180_qty"],
        "locations": sorted(item["locations"]),
        "sold_units": sold_units,
        "orders": item["orders"],
        "revenue": round(item["revenue"], 2),
        "average_price": round(average_price, 2),
        "returned_units": returned_units,
        "return_rate": round(return_rate, 1) if return_rate is not None else None,
        "refunds": round(item["refunds"], 2),
        "median_days_to_sell": median_days,
        "fast_sale_rate": round(item["_fast_units"] / sold_units * 100, 1) if sold_units else None,
        "slow_sale_rate": round(item["_slow_units"] / sold_units * 100, 1) if sold_units else None,
        "monthly_velocity": round(velocity, 2),
        "months_of_supply": round(months_supply, 1) if months_supply is not None else None,
        "last_sale_at": item["last_sale_at"].isoformat() if item["last_sale_at"] else None,
        "first_sale_at": item["first_sale_at"].isoformat() if item["first_sale_at"] else None,
        "stores": dict(item["stores"]),
        "average_cost": round(statistics.mean(item["_cost_values"]), 2) if item["_cost_values"] else None,
        "average_retail": round(statistics.mean(item["_retail_values"]), 2) if item["_retail_values"] else None,
        "confidence": _confidence(sold_units),
    }
    public["verdict"] = _verdict(public)
    public["reason"] = _verdict_reason(public)
    return public


def _aggregate_groups(items, field, analysis_months):
    buckets = defaultdict(list)
    for item in items:
        buckets[item[field] or "Unknown"].append(item)
    rows = []
    for name, members in buckets.items():
        current_qty = sum(item["current_qty"] for item in members)
        sold_units = sum(item["sold_units"] for item in members)
        returned_units = sum(item["returned_units"] for item in members)
        revenue = sum(item["revenue"] for item in members)
        age_weight = sum(item["average_age_days"] * item["current_qty"] for item in members)
        sell_days = [
            item["median_days_to_sell"] for item in members
            if item["median_days_to_sell"] is not None
        ]
        return_rate = returned_units / sold_units * 100 if sold_units else None
        monthly_velocity = sold_units / max(analysis_months, 1)
        months_supply = current_qty / monthly_velocity if monthly_velocity > 0 else None
        row = {
            "name": name,
            "current_qty": current_qty,
            "inventory_skus": sum(1 for item in members if item["current_qty"] > 0),
            "average_age_days": round(age_weight / current_qty) if current_qty else 0,
            "oldest_age_days": max((item["oldest_age_days"] for item in members), default=0),
            "aged_180_qty": sum(item["aged_180_qty"] for item in members),
            "sold_units": sold_units,
            "orders": sum(item["orders"] for item in members),
            "revenue": round(revenue, 2),
            "average_price": round(revenue / sold_units, 2) if sold_units else 0,
            "returned_units": returned_units,
            "return_rate": round(return_rate, 1) if return_rate is not None else None,
            "median_days_to_sell": _median(sell_days),
            "monthly_velocity": round(monthly_velocity, 2),
            "months_of_supply": round(months_supply, 1) if months_supply is not None else None,
            "confidence": _confidence(sold_units),
        }
        row["verdict"] = _verdict({
            **row,
            "fast_sale_rate": None,
            "slow_sale_rate": None,
        })
        rows.append(row)
    return sorted(rows, key=lambda row: (-row["sold_units"], row["name"].lower()))


def _store_groups(store_stats, analysis_months):
    rows = []
    for name, stats in store_stats.items():
        sold_units = stats["sold_units"]
        returned_units = stats["returned_units"]
        rows.append({
            "name": name.title(),
            "current_qty": 0,
            "inventory_skus": 0,
            "average_age_days": 0,
            "oldest_age_days": 0,
            "aged_180_qty": 0,
            "sold_units": sold_units,
            "orders": stats["orders"],
            "revenue": round(stats["revenue"], 2),
            "average_price": round(stats["revenue"] / sold_units, 2) if sold_units else 0,
            "returned_units": returned_units,
            "return_rate": round(returned_units / sold_units * 100, 1) if sold_units else None,
            "median_days_to_sell": _median(stats["sell_days"]),
            "monthly_velocity": round(sold_units / max(analysis_months, 1), 2),
            "months_of_supply": None,
            "confidence": _confidence(sold_units),
            "verdict": "Monitor",
        })
    return sorted(rows, key=lambda row: -row["sold_units"])


def build_seller_analytics(base_dir, window_days=0):
    base = Path(base_dir)
    now = dt.datetime.now()
    cutoff = now - dt.timedelta(days=window_days) if window_days else None
    items = {}
    raw_intake_by_upc = defaultdict(list)
    raw_intake_by_lot_upc = defaultdict(list)
    raw_details_by_upc = defaultdict(list)

    raw_path = base / "rawbol.db"
    if raw_path.exists():
        with closing(_connect_readonly(raw_path)) as conn:
            for row in conn.execute(
                """SELECT upc, item_description, lot_number, import_date,
                          avg_cost, original_retail
                   FROM raw_bol_items"""
            ):
                upc = _canonical_upc(row["upc"])
                received = _parse_date(row["import_date"])
                if upc and received:
                    raw_intake_by_upc[upc].append(received)
                    raw_intake_by_lot_upc[(str(row["lot_number"] or "").strip(), upc)].append(received)
                if upc:
                    raw_details_by_upc[upc].append(dict(row))

    warehouse_rows, age_batches = _load_warehouse_age_data(base)
    age_band_counts = _warehouse_age_counts(warehouse_rows, age_batches, now)
    for row in warehouse_rows:
        key = _item_key(row["BARCODE"], row["TITLE"])
        item = items.setdefault(key, _new_item(key))
        qty = _safe_quantity(row["QUANTITY"])
        upc = _canonical_upc(row["BARCODE"])
        item["barcode"] = item["barcode"] or str(row["BARCODE"] or "").strip()
        item["title"] = item["title"] or str(row["TITLE"] or "").strip()
        if not item["title"] and raw_details_by_upc.get(upc):
            item["title"] = str(raw_details_by_upc[upc][0].get("item_description") or "").strip()
        item["current_qty"] += qty
        location = str(row["ITEM_POSITION"] or "").strip()
        if location:
            item["locations"].add(location)

        batches = age_batches.get(row["ID"]) or [{
            "received_at": row["CREATED_AT"],
            "quantity": qty,
        }]
        accounted = 0
        for batch in batches:
            batch_qty = _safe_quantity(batch.get("quantity"))
            received = _parse_date(batch.get("received_at"))
            if not received or batch_qty <= 0:
                continue
            batch_qty = min(batch_qty, max(0, qty - accounted))
            accounted += batch_qty
            age_days = max(0, (now - received).days)
            item["_age_weight"] += age_days * batch_qty
            item["_age_qty"] += batch_qty
            item["oldest_age_days"] = max(item["oldest_age_days"], age_days)
            if age_days > 180:
                item["aged_180_qty"] += batch_qty
        if accounted < qty:
            received = _parse_date(row["CREATED_AT"]) or now
            remainder = qty - accounted
            age_days = max(0, (now - received).days)
            item["_age_weight"] += age_days * remainder
            item["_age_qty"] += remainder
            item["oldest_age_days"] = max(item["oldest_age_days"], age_days)
            if age_days > 180:
                item["aged_180_qty"] += remainder

        for detail in raw_details_by_upc.get(upc, []):
            cost = _safe_money(detail.get("avg_cost"))
            retail = _safe_money(detail.get("original_retail"))
            if cost:
                item["_cost_values"].append(cost)
            if retail:
                item["_retail_values"].append(retail)

    sold_path = base / "sold.db"
    with closing(_connect_readonly(sold_path)) as conn:
        order_rows = [dict(row) for row in conn.execute("SELECT * FROM orders")]
        return_rows = [dict(row) for row in conn.execute("SELECT * FROM returns")]

    orders_by_db_id = {str(row.get("id")): row for row in order_rows}
    trend = defaultdict(lambda: {"sold_units": 0, "revenue": 0.0, "returned_units": 0})
    store_stats = defaultdict(lambda: {
        "sold_units": 0, "orders": 0, "revenue": 0.0,
        "returned_units": 0, "sell_days": [],
    })
    sale_dates = []
    receipt_match_units = 0
    sold_rows_used = 0

    for row in order_rows:
        store = str(row.get("store") or "unknown").strip().lower()
        if store == "test":
            continue
        paid = _parse_date(row.get("paid_time"))
        if not paid or (cutoff and paid < cutoff):
            continue
        qty = max(1, _safe_quantity(row.get("quantity"), 1))
        price = _safe_money(row.get("price"))
        barcode = row.get("barcode") or row.get("source_upc") or row.get("source_base_upc")
        title = str(row.get("title") or "").strip()
        key = _item_key(barcode, title)
        item = items.setdefault(key, _new_item(key))
        item["barcode"] = item["barcode"] or str(barcode or "").strip()
        item["title"] = item["title"] or title
        item["sold_units"] += qty
        item["orders"] += 1
        item["revenue"] += price
        item["stores"][store] += qty
        item["first_sale_at"] = min(filter(None, (item["first_sale_at"], paid)), default=paid)
        item["last_sale_at"] = max(filter(None, (item["last_sale_at"], paid)), default=paid)
        sold_rows_used += 1
        sale_dates.append(paid)
        month = paid.strftime("%Y-%m")
        trend[month]["sold_units"] += qty
        trend[month]["revenue"] += price
        store_stats[store]["sold_units"] += qty
        store_stats[store]["orders"] += 1
        store_stats[store]["revenue"] += price

        upc = _canonical_upc(barcode)
        lot = str(row.get("lot_number") or "").strip()
        candidates = raw_intake_by_lot_upc.get((lot, upc), []) if lot and upc else []
        if not candidates and upc:
            candidates = raw_intake_by_upc.get(upc, [])
        eligible = [received for received in candidates if received <= paid]
        if eligible:
            days_to_sell = max(0, (paid - max(eligible)).days)
            item["_sell_days"].extend([days_to_sell] * qty)
            store_stats[store]["sell_days"].extend([days_to_sell] * qty)
            receipt_match_units += qty
            if days_to_sell <= 30:
                item["_fast_units"] += qty
            if days_to_sell > 120:
                item["_slow_units"] += qty

    dedupe = set()
    duplicate_returns_ignored = 0
    return_units_used = 0
    for row in return_rows:
        original = orders_by_db_id.get(str(row.get("original_order_id") or ""))
        returned = _parse_date(row.get("return_date"))
        if cutoff:
            original_paid = _parse_date(original.get("paid_time")) if original else None
            if original_paid:
                if original_paid < cutoff:
                    continue
            elif not returned or returned < cutoff:
                continue
        barcode = row.get("barcode") or (original.get("barcode") if original else None)
        title = str(row.get("title") or (original.get("title") if original else "") or "").strip()
        qty = max(1, _safe_quantity(row.get("quantity"), 1))
        identity = (
            str(row.get("original_order_id") or row.get("order_id") or ""),
            _canonical_upc(barcode) or _title_key(title),
            str(row.get("return_date") or ""),
            qty,
            round(_safe_money(row.get("refund_amount")), 2),
        )
        if identity in dedupe:
            duplicate_returns_ignored += 1
            continue
        dedupe.add(identity)
        key = _item_key(barcode, title)
        item = items.setdefault(key, _new_item(key))
        item["barcode"] = item["barcode"] or str(barcode or "").strip()
        item["title"] = item["title"] or title
        item["returned_units"] += qty
        item["refunds"] += _safe_money(row.get("refund_amount"))
        return_units_used += qty
        store = str(row.get("store") or (original.get("store") if original else "unknown") or "unknown").lower()
        store_stats[store]["returned_units"] += qty
        if returned:
            trend[returned.strftime("%Y-%m")]["returned_units"] += qty

    if sale_dates:
        period_start = min(sale_dates)
        period_end = max(sale_dates)
        analysis_months = max((period_end - period_start).days / 30.4375, 1)
    else:
        period_start = cutoff or now
        period_end = now
        analysis_months = max(window_days / 30.4375, 1) if window_days else 1

    public_items = [_public_item(item, analysis_months) for item in items.values()]
    public_items.sort(key=lambda item: (-item["sold_units"], -item["current_qty"], item["name"].lower()))

    category_groups = _aggregate_groups(public_items, "category", analysis_months)
    brand_groups = _aggregate_groups(public_items, "brand", analysis_months)
    price_buckets = defaultdict(list)
    for item in public_items:
        price_buckets[_price_band(item["average_price"])].append(item)
    price_items = []
    for band, members in price_buckets.items():
        for member in members:
            clone = dict(member)
            clone["price_band"] = band
            price_items.append(clone)
    price_groups = _aggregate_groups(price_items, "price_band", analysis_months)

    dispose = sorted(
        [item for item in public_items if item["verdict"] == "Clearance / disposal review"],
        key=lambda item: (-item["oldest_age_days"], -item["current_qty"]),
    )
    avoid = sorted(
        [item for item in public_items if item["verdict"].startswith("Avoid")],
        key=lambda item: (-(item["return_rate"] or 0), -item["oldest_age_days"]),
    )
    buy_more = sorted(
        [item for item in public_items if item["verdict"] == "Buy more"],
        key=lambda item: (-item["monthly_velocity"], item["median_days_to_sell"] or 99999),
    )
    return_risk = sorted(
        [item for item in public_items if item["returned_units"] > 0],
        key=lambda item: (-(item["return_rate"] or 0), -item["returned_units"], -item["sold_units"]),
    )

    all_months = []
    if trend:
        cursor = dt.datetime.strptime(min(trend), "%Y-%m")
        last = dt.datetime.strptime(max(trend), "%Y-%m")
        while cursor <= last:
            month = cursor.strftime("%Y-%m")
            all_months.append({
                "month": month,
                "sold_units": trend[month]["sold_units"],
                "revenue": round(trend[month]["revenue"], 2),
                "returned_units": trend[month]["returned_units"],
            })
            cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    total_inventory = sum(item["current_qty"] for item in public_items)
    total_sold = sum(item["sold_units"] for item in public_items)
    total_returned = sum(item["returned_units"] for item in public_items)
    total_revenue = sum(item["revenue"] for item in public_items)
    all_sell_days = [
        value
        for item in items.values()
        for value in item["_sell_days"]
    ]
    return {
        "success": True,
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "period": {
            "start": period_start.isoformat() if period_start else None,
            "end": period_end.isoformat() if period_end else None,
            "months": round(analysis_months, 1),
        },
        "summary": {
            "inventory_units": total_inventory,
            "inventory_skus": sum(1 for item in public_items if item["current_qty"] > 0),
            "aged_180_units": age_band_counts["181+ days"],
            "sold_units": total_sold,
            "sales_orders": sum(item["orders"] for item in public_items),
            "revenue": round(total_revenue, 2),
            "average_sale_price": round(total_revenue / total_sold, 2) if total_sold else 0,
            "returned_units": total_returned,
            "return_rate": round(total_returned / total_sold * 100, 1) if total_sold else None,
            "median_days_to_sell": _median(all_sell_days),
            "receipt_match_units": receipt_match_units,
            "clearance_candidates": len(dispose),
            "buy_more_candidates": len(buy_more),
        },
        "age_bands": [
            {"name": name, "units": units}
            for name, units in age_band_counts.items()
        ],
        "trend": all_months,
        "groups": {
            "category": category_groups,
            "brand": brand_groups,
            "item": public_items,
            "price_band": price_groups,
            "store": _store_groups(store_stats, analysis_months),
        },
        "recommendations": {
            "buy_more": buy_more,
            "avoid": avoid,
            "dispose": dispose,
            "return_risk": return_risk,
        },
        "scatter": [
            {
                "name": item["name"],
                "barcode": item["barcode"],
                "category": item["category"],
                "age": item["average_age_days"],
                "velocity": item["monthly_velocity"],
                "quantity": item["current_qty"],
                "verdict": item["verdict"],
                "return_rate": item["return_rate"],
            }
            for item in public_items
            if item["current_qty"] > 0
        ],
        "filters": {
            "categories": sorted({item["category"] for item in public_items}),
            "brands": sorted({item["brand"] for item in public_items}),
            "stores": sorted(store_stats),
        },
        "data_quality": {
            "warehouse_rows": len(warehouse_rows),
            "sold_rows_used": sold_rows_used,
            "return_records_raw": len(return_rows),
            "return_records_used": len(dedupe),
            "duplicate_returns_ignored": duplicate_returns_ignored,
            "receipt_matched_units": receipt_match_units,
            "receipt_match_rate": round(receipt_match_units / total_sold * 100, 1) if total_sold else 0,
            "latest_sale_at": max(sale_dates).isoformat() if sale_dates else None,
            "notes": [
                "Category and brand are inferred from item titles.",
                "Return records are deduplicated before risk calculations.",
                "Time-to-sell is shown only when a sold UPC can be matched to a BOL intake date.",
                "Recommendations are decision support, not automatic disposal instructions.",
            ],
        },
    }
