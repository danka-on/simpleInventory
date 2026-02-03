#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║        ☢  SWEET SHELVES REACTOR CONSOLE  ☢                  ║
║        Terminal dashboard for inventory metrics              ║
║        Usage: python dashboard.py                            ║
╚══════════════════════════════════════════════════════════════╝
"""

import sqlite3
import os
import sys
import time
import math
from datetime import datetime, timedelta
from pathlib import Path

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.align import Align
    from rich.columns import Columns
    from rich import box
    from rich.progress_bar import ProgressBar
    from rich.style import Style
except ImportError:
    print("Missing 'rich' library. Install with: pip install rich")
    sys.exit(1)

try:
    import psutil
except ImportError:
    print("Missing 'psutil' library. Install with: pip install psutil")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATHS = {
    "rack": BASE_DIR / "searchRack.db",
    "sold": BASE_DIR / "sold.db",
    "bol": BASE_DIR / "bol.db",
    "rawbol": BASE_DIR / "rawbol.db",
    "history": BASE_DIR / "rackhistory.db",
    "sync": BASE_DIR / "sync_settings.db",
    "amazon": BASE_DIR / "amazonStore.db",
    "ebay": BASE_DIR / "ebayStore.db",
}

REFRESH_INTERVAL = 2.5  # seconds
START_TIME = time.time()

# ── Nuclear theme colors ─────────────────────────────────────────
C_TITLE = "bold bright_green"
C_WARN = "bold yellow"
C_DANGER = "bold red"
C_OK = "green"
C_DIM = "bright_black"
C_ACCENT = "cyan"
C_GLOW = "bold bright_cyan"
C_RADIATION = "bold bright_yellow"

WAVE_CHARS = "░▒▓█▓▒░"
SPINNER_FRAMES = ["◜", "◝", "◞", "◟"]
RADIATION_FRAMES = ["☢", "◉", "☢", "◉"]
HEARTBEAT = ["╺", "━", "┃", "━", "╸", " "]


def db_connect(name):
    """Open a read-only SQLite connection."""
    path = DB_PATHS.get(name)
    if not path or not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def safe_query(db_name, sql, params=(), default=None):
    """Run a query safely, return default on failure."""
    conn = db_connect(db_name)
    if not conn:
        return default
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return default
    finally:
        conn.close()


def safe_scalar(db_name, sql, params=(), default=0):
    rows = safe_query(db_name, sql, params, default=[])
    if rows and rows[0]:
        val = rows[0][0]
        return val if val is not None else default
    return default


# ── Data Collection ──────────────────────────────────────────────

def get_inventory_stats():
    total_items = safe_scalar("rack", "SELECT COUNT(*) FROM SEARCHRACK")
    total_qty = safe_scalar("rack", "SELECT SUM(QUANTITY) FROM SEARCHRACK")
    locations = safe_scalar("rack", "SELECT COUNT(DISTINCT ITEM_POSITION) FROM SEARCHRACK")
    duplicates = safe_scalar("rack", """
        SELECT COUNT(*) FROM (
            SELECT BARCODE FROM SEARCHRACK
            GROUP BY BARCODE HAVING COUNT(DISTINCT ITEM_POSITION) > 1
        )
    """)
    zero_pending = safe_scalar("rack", "SELECT COUNT(*) FROM zero_qty_pending_deletion WHERE deletion_cancelled = 0")
    archived = safe_scalar("rack", "SELECT COUNT(*) FROM archived_searchrack")
    return {
        "items": total_items, "qty": total_qty, "locations": locations,
        "duplicates": duplicates, "zero_pending": zero_pending, "archived": archived,
    }


def get_sales_stats():
    def period_stats(days):
        if days == 0:
            date_filter = "date(paid_time) = date('now')"
        else:
            date_filter = f"paid_time >= date('now', '-{days} days')"
        orders = safe_scalar("sold", f"SELECT COUNT(*) FROM orders WHERE {date_filter}")
        revenue = safe_scalar("sold", f"SELECT SUM(CAST(price AS REAL) * CAST(quantity AS INTEGER)) FROM orders WHERE {date_filter}", default=0.0)
        fees = safe_scalar("sold", f"SELECT SUM(CAST(seller_fee AS REAL)) FROM orders WHERE {date_filter}", default=0.0)
        shipping = safe_scalar("sold", f"SELECT SUM(CAST(shipping_cost AS REAL)) FROM orders WHERE {date_filter}", default=0.0)
        return {"orders": orders, "revenue": float(revenue or 0), "fees": float(fees or 0), "shipping": float(shipping or 0)}

    today = period_stats(0)
    week = period_stats(7)
    month = period_stats(30)
    all_time = period_stats(9999)

    total_returns = safe_scalar("sold", "SELECT COUNT(*) FROM returns")
    total_refunded = safe_scalar("sold", "SELECT SUM(CAST(refund_amount AS REAL)) FROM returns", default=0.0)

    store_rows = safe_query("sold", """
        SELECT COALESCE(store,'?') as store, COUNT(*) as cnt
        FROM orders WHERE paid_time >= date('now', '-30 days')
        GROUP BY store
    """, default=[])
    stores = {r["store"]: r["cnt"] for r in store_rows} if store_rows else {}

    unhandled = safe_scalar("sold", "SELECT COUNT(*) FROM orders WHERE isHandled = 0 OR isHandled IS NULL")

    return {
        "today": today, "week": week, "month": month, "all_time": all_time,
        "returns": total_returns, "refunded": float(total_refunded or 0),
        "stores_30d": stores, "unhandled": unhandled,
    }


def get_bol_stats():
    rows = safe_query("bol", """
        SELECT lot_number,
            COUNT(*) as items,
            SUM(CAST(original_qty AS INTEGER)) as orig,
            SUM(CAST(good_qty AS INTEGER)) as good,
            SUM(CAST(bad_qty AS INTEGER)) as bad,
            SUM(CAST(unchecked_qty AS INTEGER)) as unchecked
        FROM bol_items
        WHERE lot_number IS NOT NULL AND lot_number != 'nan'
        GROUP BY lot_number
        ORDER BY lot_number DESC
        LIMIT 8
    """, default=[])

    lots = []
    for r in rows:
        orig = int(r["orig"] or 0)
        good = int(r["good"] or 0)
        bad = int(r["bad"] or 0)
        pct = ((good + bad) / orig * 100) if orig > 0 else 0
        loss = (bad / orig * 100) if orig > 0 else 0
        lots.append({
            "lot": r["lot_number"], "items": r["items"], "orig": orig,
            "good": good, "bad": bad, "pct": pct, "loss": loss,
        })

    total_items = safe_scalar("bol", "SELECT COUNT(*) FROM bol_items")
    listed = safe_scalar("bol", "SELECT COUNT(*) FROM bol_items WHERE listed_amazon = 1")
    return {"lots": lots, "total_items": total_items, "listed_amazon": listed}


def get_marketplace_stats():
    amz_count = safe_scalar("amazon", "SELECT COUNT(*) FROM ITEMS")
    amz_qty = safe_scalar("amazon", "SELECT SUM(CAST(QUANTITY AS INTEGER)) FROM ITEMS")
    ebay_count = safe_scalar("ebay", "SELECT COUNT(*) FROM INVENTORY")
    ebay_qty = safe_scalar("ebay", "SELECT SUM(CAST(Quantity AS INTEGER)) FROM INVENTORY")
    return {
        "amazon": {"listings": amz_count, "qty": int(amz_qty or 0)},
        "ebay": {"listings": ebay_count, "qty": int(ebay_qty or 0)},
    }


def get_system_health():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/") if os.name != "nt" else psutil.disk_usage("C:\\")
    cpu = psutil.cpu_percent(interval=0.1)

    db_sizes = {}
    for name, path in DB_PATHS.items():
        if path.exists():
            db_sizes[name] = path.stat().st_size / (1024 * 1024)

    # Sync times
    sync_rows = safe_query("sync", "SELECT key, value FROM sync_status", default=[])
    sync = {r["key"]: r["value"] for r in sync_rows} if sync_rows else {}

    return {
        "cpu": cpu,
        "mem_pct": mem.percent, "mem_used": mem.used / (1024**3), "mem_total": mem.total / (1024**3),
        "disk_pct": disk.percent, "disk_free": disk.free / (1024**3), "disk_total": disk.total / (1024**3),
        "db_sizes": db_sizes,
        "sync": sync,
    }


def get_activity_feed():
    rows = safe_query("history", """
        SELECT title, barcode, quantity_removed, removed_at, removal_type, item_position
        FROM removed_items
        ORDER BY removed_at DESC
        LIMIT 6
    """, default=[])
    return [dict(r) for r in rows] if rows else []


def get_cost_data():
    """Get total cost invested from rawbol upload logs."""
    total_cost = safe_scalar("rawbol", "SELECT SUM(CAST(total_client_cost AS REAL)) FROM upload_logs", default=0.0)
    total_shipping = safe_scalar("rawbol", "SELECT SUM(CAST(shipping_cost AS REAL)) FROM upload_logs", default=0.0)
    lots_count = safe_scalar("rawbol", "SELECT COUNT(DISTINCT lot_number) FROM upload_logs")
    return {"total_cost": float(total_cost or 0), "shipping": float(total_shipping or 0), "lots": lots_count}


# ── Rendering Helpers ────────────────────────────────────────────

def make_bar(pct, width=20, fill_color="green", empty_color="bright_black"):
    """Create a text-based progress bar."""
    pct = max(0, min(100, pct))
    filled = int(width * pct / 100)
    empty = width - filled
    t = Text()
    t.append("█" * filled, style=fill_color)
    t.append("░" * empty, style=empty_color)
    return t


def pct_color(pct, invert=False):
    """Return color based on percentage threshold."""
    if invert:
        pct = 100 - pct
    if pct >= 90:
        return C_DANGER
    elif pct >= 70:
        return C_WARN
    return C_OK


def wave_text(text, tick, base_style="bright_green"):
    """Create animated wave effect on text."""
    t = Text()
    for i, ch in enumerate(text):
        offset = math.sin((tick * 0.3) + (i * 0.4))
        if offset > 0.5:
            t.append(ch, style="bold bright_white")
        elif offset > 0:
            t.append(ch, style="bold bright_green")
        elif offset > -0.5:
            t.append(ch, style="green")
        else:
            t.append(ch, style="dark_green")
    return t


def format_money(val):
    return f"${val:,.2f}" if val else "$0.00"


def time_ago(timestamp_str):
    """Convert ISO timestamp to 'Xm ago' style."""
    if not timestamp_str:
        return "never"
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00").replace("T", " ").split("+")[0].strip())
        delta = datetime.now() - ts
        mins = int(delta.total_seconds() / 60)
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return "?"


# ── Panel Builders ───────────────────────────────────────────────

def build_header(tick):
    rad = RADIATION_FRAMES[tick % len(RADIATION_FRAMES)]
    spin = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
    hb = HEARTBEAT[tick % len(HEARTBEAT)]

    elapsed = time.time() - START_TIME
    uptime_m = int(elapsed // 60)
    uptime_s = int(elapsed % 60)

    title = wave_text("  S W E E T   S H E L V E S   R E A C T O R   C O N S O L E  ", tick)

    t = Text()
    t.append(f" {rad} ", style=C_RADIATION)
    t.append_text(title)
    t.append(f" {rad} ", style=C_RADIATION)
    t.append("   ")
    t.append(f"{spin} ", style=C_GLOW)
    t.append(f"SESSION {uptime_m:02d}:{uptime_s:02d}", style=C_DIM)
    t.append(f"  {hb} ", style=C_OK)
    t.append(datetime.now().strftime("%H:%M:%S"), style=C_ACCENT)

    return Panel(Align.center(t), style="bright_green", box=box.DOUBLE_EDGE, height=3)


def build_inventory_panel(inv, mkt):
    t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    t.add_column("label", style=C_DIM, width=16)
    t.add_column("val", no_wrap=True)

    # Items in warehouse
    t.add_row("▸ Items", Text.assemble(
        (f"{inv['items']}", C_GLOW), "  ", make_bar(min(inv['items'], 500) / 5, 15)
    ))
    t.add_row("▸ Total Qty", Text.assemble(
        (f"{inv['qty']}", C_GLOW), "  ", make_bar(min(inv['qty'], 500) / 5, 15)
    ))
    t.add_row("▸ Locations", Text.assemble((f"{inv['locations']} active", C_ACCENT)))

    dup_style = C_WARN if inv['duplicates'] > 0 else C_OK
    t.add_row("▸ Duplicates", Text(f"{inv['duplicates']}", style=dup_style))

    zp_style = C_WARN if inv['zero_pending'] > 0 else C_DIM
    t.add_row("▸ Zero-Qty", Text(f"{inv['zero_pending']} pending", style=zp_style))
    t.add_row("▸ Archived", Text(f"{inv['archived']}", style=C_DIM))
    t.add_row("", Text())
    t.add_row("▸ Amazon", Text.assemble(
        (f"{mkt['amazon']['listings']}", C_ACCENT), " listings  ",
        (f"({mkt['amazon']['qty']} qty)", C_DIM),
    ))
    t.add_row("▸ eBay", Text.assemble(
        (f"{mkt['ebay']['listings']}", C_ACCENT), " listings  ",
        (f"({mkt['ebay']['qty']} qty)", C_DIM),
    ))

    return Panel(t, title="[bold bright_cyan]▐ CORE: INVENTORY ▌[/]", border_style="cyan", box=box.HEAVY)


def build_sales_panel(sales, costs, tick):
    t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    t.add_column("label", style=C_DIM, width=14)
    t.add_column("val", no_wrap=True)

    for label, data in [("▸ Today", sales["today"]), ("▸ 7-Day", sales["week"]), ("▸ 30-Day", sales["month"])]:
        rev = data["revenue"]
        t.add_row(label, Text.assemble(
            (format_money(rev), C_GLOW), f"  ({data['orders']} orders)",
        ))

    # Profit estimate (revenue - fees - cost)
    total_rev = sales["all_time"]["revenue"]
    total_fees = sales["all_time"]["fees"] + sales["all_time"]["shipping"]
    total_cost = costs["total_cost"] + costs["shipping"]
    profit = total_rev - total_fees
    margin = (profit / total_rev * 100) if total_rev > 0 else 0
    profit_color = C_OK if profit > 0 else C_DANGER

    t.add_row("", Text())
    t.add_row("▸ Revenue", Text(f"{format_money(total_rev)} all-time", style=C_ACCENT))
    t.add_row("▸ Fees", Text(f"-{format_money(total_fees)}", style=C_WARN))
    t.add_row("▸ Net", Text.assemble(
        (format_money(profit), profit_color), "  ", make_bar(max(margin, 0), 12, "green" if profit > 0 else "red"),
        (f" {margin:.0f}%", C_DIM),
    ))
    t.add_row("▸ Invested", Text(f"{format_money(total_cost)} ({costs['lots']} lots)", style=C_DIM))

    # Returns
    ret_rate = (sales["returns"] / sales["all_time"]["orders"] * 100) if sales["all_time"]["orders"] > 0 else 0
    ret_color = C_DANGER if ret_rate > 10 else C_WARN if ret_rate > 5 else C_OK
    t.add_row("▸ Returns", Text.assemble(
        (f"{sales['returns']}", ret_color), (f" ({ret_rate:.1f}%)", C_DIM),
        "  ", make_bar(ret_rate, 10, "red", "bright_black"),
    ))

    # Store breakdown (30d)
    stores_text = Text()
    for store, cnt in sorted(sales["stores_30d"].items(), key=lambda x: -x[1]):
        label = store[:3].upper() if store else "???"
        stores_text.append(f"{label}:{cnt} ", style=C_ACCENT)
    t.add_row("▸ 30d Stores", stores_text)

    if sales["unhandled"] > 0:
        blink = "bold red" if tick % 2 == 0 else "red"
        t.add_row("▸ Unhandled", Text(f"⚠ {sales['unhandled']} orders", style=blink))

    return Panel(t, title="[bold bright_yellow]▐ REACTOR OUTPUT: SALES ▌[/]", border_style="yellow", box=box.HEAVY)


def build_bol_panel(bol):
    t = Table(show_header=False, box=None, padding=(0, 0), expand=True)
    t.add_column("lot", width=16, style=C_DIM, no_wrap=True)
    t.add_column("bar", no_wrap=True)
    t.add_column("pct", width=6, justify="right")

    for lot in bol["lots"][:6]:
        clamped = min(lot["pct"], 100)
        bar_color = "green" if clamped >= 80 else "yellow" if clamped >= 50 else "red"
        pct_style = C_OK if clamped >= 80 else C_WARN if clamped >= 50 else C_DANGER
        lot_name = (lot["lot"] or "???")[:13]
        t.add_row(
            f"▸ {lot_name}",
            make_bar(clamped, 16, bar_color),
            Text(f"{lot['pct']:.0f}%", style=pct_style),
        )

    t.add_row("", Text(), Text())

    total_good = sum(l["good"] for l in bol["lots"])
    total_bad = sum(l["bad"] for l in bol["lots"])
    total_orig = sum(l["orig"] for l in bol["lots"])
    overall_loss = (total_bad / total_orig * 100) if total_orig > 0 else 0

    summary = Text()
    summary.append(f"Total: {bol['total_items']} items  ", style=C_DIM)
    summary.append(f"Good: {total_good}  ", style=C_OK)
    summary.append(f"Bad: {total_bad}  ", style=C_DANGER)
    summary.append(f"Loss: {overall_loss:.1f}%", style=C_WARN if overall_loss > 5 else C_DIM)
    t.add_row("", summary, Text())

    listed_text = Text()
    listed_text.append(f"Listed AMZ: {bol['listed_amazon']}", style=C_ACCENT)
    t.add_row("", listed_text, Text())

    return Panel(t, title="[bold bright_magenta]▐ FUEL RODS: BOL PREP ▌[/]", border_style="magenta", box=box.HEAVY)


def build_health_panel(health, tick):
    t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    t.add_column("label", style=C_DIM, width=7)
    t.add_column("bar", no_wrap=True)

    # CPU
    cpu_c = pct_color(health["cpu"])
    t.add_row("CPU", Text.assemble(
        make_bar(health["cpu"], 16, cpu_c.split()[-1]), (f" {health['cpu']:.0f}%", cpu_c),
    ))

    # Memory
    mem_c = pct_color(health["mem_pct"])
    t.add_row("MEM", Text.assemble(
        make_bar(health["mem_pct"], 16, mem_c.split()[-1]),
        (f" {health['mem_pct']:.0f}%", mem_c),
        (f" {health['mem_used']:.1f}/{health['mem_total']:.0f}GB", C_DIM),
    ))

    # Disk
    dk_c = pct_color(health["disk_pct"])
    t.add_row("DISK", Text.assemble(
        make_bar(health["disk_pct"], 16, dk_c.split()[-1]),
        (f" {health['disk_pct']:.0f}%", dk_c),
        (f" {health['disk_free']:.0f}GB free", C_DIM),
    ))

    # DB sizes
    t.add_row("", Text())
    db_text = Text()
    db_text.append("DBs: ", style=C_DIM)
    for name, size in sorted(health["db_sizes"].items(), key=lambda x: -x[1])[:4]:
        db_text.append(f"{name}:{size:.1f}MB ", style=C_ACCENT)
    t.add_row("DB", db_text)

    # Sync status
    sync = health["sync"]
    sync_text = Text()
    for key_name, label in [("last_ebay_orders_sync", "eBay"), ("last_amazon_orders_sync", "AMZ")]:
        ts = sync.get(key_name)
        ago = time_ago(ts)
        color = C_OK if "m ago" in ago or "just" in ago else C_WARN if "h ago" in ago else C_DANGER
        sync_text.append(f"{label}:", style=C_DIM)
        sync_text.append(f"{ago} ", style=color)
    t.add_row("SYNC", sync_text)

    # Heartbeat indicator
    pulse = "▁▂▃▄▅▆▇█▇▆▅▄▃▂▁"
    idx = tick % len(pulse)
    pulse_text = Text()
    for i, ch in enumerate(pulse):
        dist = abs(i - idx)
        if dist == 0:
            pulse_text.append(ch, style="bold bright_green")
        elif dist <= 2:
            pulse_text.append(ch, style="green")
        else:
            pulse_text.append(ch, style="dark_green")
    t.add_row("PULSE", pulse_text)

    return Panel(t, title="[bold bright_red]▐ CONTAINMENT: SYSTEM ▌[/]", border_style="red", box=box.HEAVY)


def build_activity_feed(feed, tick):
    t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    t.add_column("time", width=18, style=C_DIM)
    t.add_column("type", width=8)
    t.add_column("detail", ratio=1, no_wrap=True)

    if not feed:
        t.add_row("", Text("No activity recorded yet", style=C_DIM), "")
    else:
        for item in feed[:5]:
            rtype = (item.get("removal_type") or "removed").upper()
            color = "bright_yellow" if "sale" in rtype.lower() else "bright_blue" if "manual" in rtype.lower() else C_DIM
            detail = Text()
            detail.append(f"{item.get('barcode', '?')}", style=C_ACCENT)
            title = item.get("title", "")
            if title:
                detail.append(f'  "{title[:30]}"', style=C_DIM)
            qty = item.get("quantity_removed", "?")
            detail.append(f"  qty:{qty}", style=C_GLOW)
            pos = item.get("item_position")
            if pos:
                detail.append(f"  @{pos}", style="bright_magenta")

            t.add_row(
                str(item.get("removed_at", ""))[:19],
                Text(rtype[:8], style=color),
                detail,
            )

    # Animated scanner line
    scan_width = 40
    pos = tick % (scan_width * 2)
    if pos >= scan_width:
        pos = scan_width * 2 - pos
    scan = Text()
    for i in range(scan_width):
        dist = abs(i - pos)
        if dist == 0:
            scan.append("█", style="bold bright_green")
        elif dist == 1:
            scan.append("▓", style="green")
        elif dist == 2:
            scan.append("▒", style="dark_green")
        elif dist == 3:
            scan.append("░", style="dark_green")
        else:
            scan.append("·", style="bright_black")
    t.add_row("", Text("SCAN", style=C_DIM), scan)

    return Panel(t, title="[bold bright_blue]▐ ACTIVITY FEED ▌[/]", border_style="blue", box=box.HEAVY)


def build_footer(tick):
    spin = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
    t = Text()
    t.append(f"  {spin} MONITORING ", style=C_OK)
    t.append("│", style=C_DIM)
    t.append(" [q] Quit ", style=C_DIM)
    t.append("│", style=C_DIM)
    t.append(f" Refresh: {REFRESH_INTERVAL}s ", style=C_DIM)
    t.append("│", style=C_DIM)

    # Animated radiation border
    wave = ""
    for i in range(30):
        idx = (tick + i) % len(WAVE_CHARS)
        wave += WAVE_CHARS[idx]
    t.append(f" {wave}", style="dark_green")

    return Align.center(t)


# ── Main Layout ──────────────────────────────────────────────────

def build_dashboard(tick):
    """Build the full dashboard layout for one frame."""
    # Collect all data
    inv = get_inventory_stats()
    sales = get_sales_stats()
    bol = get_bol_stats()
    mkt = get_marketplace_stats()
    health = get_system_health()
    feed = get_activity_feed()
    costs = get_cost_data()

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="feed", size=10),
        Layout(name="footer", size=1),
    )

    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )

    layout["left"].split_column(
        Layout(name="inventory", ratio=5),
        Layout(name="bol", ratio=5),
    )

    layout["right"].split_column(
        Layout(name="sales", ratio=6),
        Layout(name="health", ratio=4),
    )

    layout["header"].update(build_header(tick))
    layout["inventory"].update(build_inventory_panel(inv, mkt))
    layout["sales"].update(build_sales_panel(sales, costs, tick))
    layout["bol"].update(build_bol_panel(bol))
    layout["health"].update(build_health_panel(health, tick))
    layout["feed"].update(build_activity_feed(feed, tick))
    layout["footer"].update(build_footer(tick))

    return layout


def main():
    # Force UTF-8 on Windows to handle unicode symbols
    if sys.platform == "win32":
        import io
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    console = Console(force_terminal=True)
    tick = 0

    console.clear()
    console.print("[bold bright_green]Initializing reactor console...[/]")
    time.sleep(0.5)

    try:
        import threading
        stop_event = threading.Event()

        def key_listener():
            """Listen for 'q' key press to quit."""
            try:
                if sys.platform == "win32":
                    import msvcrt
                    while not stop_event.is_set():
                        if msvcrt.kbhit():
                            ch = msvcrt.getch()
                            if ch in (b"q", b"Q", b"\x1b"):  # q, Q, or Esc
                                stop_event.set()
                                return
                        time.sleep(0.1)
                else:
                    import tty, termios
                    old = termios.tcgetattr(sys.stdin)
                    try:
                        tty.setcbreak(sys.stdin.fileno())
                        while not stop_event.is_set():
                            ch = sys.stdin.read(1)
                            if ch in ("q", "Q", "\x1b"):
                                stop_event.set()
                                return
                    finally:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
            except Exception:
                pass

        listener = threading.Thread(target=key_listener, daemon=True)
        listener.start()

        with Live(build_dashboard(tick), console=console, refresh_per_second=4, screen=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(build_dashboard(tick))
                time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        console.clear()
        console.print("[bold bright_green]☢ Reactor console shutdown complete. ☢[/]")


if __name__ == "__main__":
    main()
