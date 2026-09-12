"""Normalization for Sweet Shelves."""

import datetime
from decimal import Decimal, InvalidOperation


def _coerce_int(value, default=0):
    try:
        if value is None:
            return default
        if isinstance(value, str):
            s = value.strip().lower()
            if s in ('', 'n/a', 'na', 'null'):
                return default
        return int(float(value))
    except Exception:
        return default


def _parse_iso_utc_naive(raw):
    txt = (str(raw or '').strip())
    if not txt:
        return None
    try:
        if 'T' in txt:
            if txt.endswith('Z'):
                dt = datetime.datetime.fromisoformat(txt.replace('Z', '+00:00'))
            else:
                dt = datetime.datetime.fromisoformat(txt)
        else:
            dt = datetime.datetime.fromisoformat(txt)
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _sold_order_sale_datetime(order):
    """Return the best available timestamp identifying this specific sale."""
    for field in ('paid_time', 'sold_date', 'sale_date', 'created_at'):
        try:
            value = order.get(field) if isinstance(order, dict) else order[field]
        except Exception:
            value = None
        parsed = _parse_iso_utc_naive(value)
        if parsed is not None:
            return parsed
    return None


def _sold_removal_event_is_current(order, removal):
    """Reject barcode history that predates the sold-order occurrence."""
    sold_at = _sold_order_sale_datetime(order)
    if sold_at is None:
        return True
    try:
        removed_value = removal.get('removed_at') if isinstance(removal, dict) else removal['removed_at']
    except Exception:
        removed_value = None
    removed_text = str(removed_value or '').strip()
    removed_at = None
    if removed_text:
        try:
            parsed_removed = datetime.datetime.fromisoformat(removed_text.replace('Z', '+00:00'))
            if parsed_removed.tzinfo is None:
                # Rack-history events are written with datetime.now(), so their
                # otherwise-naive timestamps are local warehouse time.
                try:
                    from zoneinfo import ZoneInfo
                    parsed_removed = parsed_removed.replace(tzinfo=ZoneInfo('America/New_York'))
                except Exception:
                    parsed_removed = parsed_removed.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
            removed_at = parsed_removed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        except Exception:
            removed_at = None
    return removed_at is not None and removed_at >= sold_at


def _normalize_upc(upc):
    if upc is None:
        return ''
    text = str(upc).strip()
    # Barcodes are identifiers: floating-point conversion silently rounds long
    # numeric codes. Remove only leading zeros and an all-zero decimal part.
    whole, separator, fraction = text.partition('.')
    if whole.isdigit() and (not separator or not fraction.strip('0')):
        return whole.lstrip('0') or '0'
    if not whole and separator and fraction and not fraction.strip('0'):
        return '0'
    return text


def _format_upc_display(upc):
    """Display helper: numeric UPCs are shown as UPC-A width (12 digits)."""
    s = _normalize_upc(upc)
    if s and s.isdigit() and len(s) <= 12:
        return s.zfill(12)
    return s


def _normalize_scanned_upc(upc):
    """
    Scanner helper:
    - Preserve original code unless it has extra leading zeros beyond UPC-A width.
    - Keep non-digit values unchanged.
    """
    s = _normalize_upc(upc)
    if not s or not s.isdigit():
        return s
    if len(s) > 12 and s.startswith('0'):
        stripped = s.lstrip('0')
        if stripped and len(stripped) <= 12:
            return stripped
    return s


def _strip_leading_zeros_numeric(value):
    """Legacy-match helper: strip leading zeros only for fully numeric codes."""
    s = _normalize_upc(value)
    if s and s.isdigit():
        stripped = s.lstrip('0')
        return stripped if stripped else '0'
    return s


def _normalize_upc_preserve_suffix_for_match(value):
    """
    Strip leading zeros from numeric UPCs while preserving -suffix variants.
    Example: 0719978859014-24 -> 719978859014-24
    """
    s = _normalize_upc(value)
    if not s:
        return s
    if '-' in s:
        base, suffix = s.split('-', 1)
        return f"{_strip_leading_zeros_numeric(base)}-{suffix}"
    return _strip_leading_zeros_numeric(s)


def _barcode_identity_variants(value):
    """
    Spellings of one physical warehouse barcode that differ only in leading-zero
    padding. A -suffix names one specific unit, so it is always preserved and
    the base UPC is never offered as a match for a suffixed code.
    """
    raw = str(value or '').strip()
    if not raw:
        return []
    if '-' in raw:
        base, suffix = raw.split('-', 1)
    else:
        base, suffix = raw, ''
    base = base.strip()
    suffix = suffix.strip()
    out = []

    def add(candidate_base):
        candidate = f'{candidate_base}-{suffix}' if suffix else candidate_base
        if candidate and candidate not in out:
            out.append(candidate)

    add(base)
    if base.isdigit():
        stripped = base.lstrip('0') or '0'
        add(stripped)
        if len(stripped) <= 12:
            add(stripped.zfill(12))
            add(stripped.zfill(13))
    return out


def _normalize_lot_number(value):
    return str(value or '').strip()


def _normalize_listing_source(value, default='system'):
    """Normalize listing source into one of: system, user, listing_center."""
    raw = (str(value or '').strip().lower().replace('-', '_').replace(' ', '_'))
    if not raw:
        return default
    if raw in ('listingagent', 'listing_agent', 'listing_center', 'listingcenter', 'list_manager', 'listing_centre', 'listingcentre'):
        return 'listing_center'
    if raw in ('user', 'manual', 'list_status_api', 'list_status', 'mail_center', 'mailcenter'):
        return 'user'
    if raw in ('system', 'auto', 'automated', 'sync', 'migration', 'unknown'):
        return 'system'
    return default or 'system'


def _normalize_prep_row_status(value):
    raw = str(value or '').strip().lower().replace(' ', '_')
    return raw if raw in ('good', 'bad', 'return', 'unchecked') else ''


def _is_items_to_list_suffixed_upc(upc):
    upc_n = _normalize_upc_preserve_suffix_for_match(upc)
    if not upc_n or '-' not in upc_n:
        return False
    base, suffix = upc_n.rsplit('-', 1)
    return bool(base and suffix)


def _items_to_list_bucket_status(upc, good_qty=0, bad_qty=0):
    try:
        good_qty_n = int(good_qty or 0)
    except Exception:
        good_qty_n = 0
    try:
        bad_qty_n = int(bad_qty or 0)
    except Exception:
        bad_qty_n = 0

    is_suffixed = _is_items_to_list_suffixed_upc(upc)
    if good_qty_n > 0 and (not is_suffixed or bad_qty_n <= 0):
        return 'good'
    if is_suffixed and bad_qty_n > 0:
        return 'bad'
    return ''


def _items_to_list_effective_status(upc, explicit_status='', good_qty=0, bad_qty=0):
    status_n = _normalize_prep_row_status(explicit_status)
    if status_n == 'good':
        return status_n
    if status_n in ('bad', 'return'):
        if _is_items_to_list_suffixed_upc(upc):
            return status_n
        # Base UPCs are reserved for canonical non-special rows. Ignore corrupted
        # BAD/RETURN states and fall back to quantity buckets so the visible row stays canonical.
        status_n = ''
    if status_n == 'unchecked':
        return ''
    if status_n:
        return status_n
    return _items_to_list_bucket_status(upc, good_qty=good_qty, bad_qty=bad_qty)


def _items_to_list_asset_scope(upc, row_status=''):
    """Scope Items-to-List notes/photos by status+barcode; suffixed barcodes are already unique."""
    upc_n = _normalize_upc_preserve_suffix_for_match(upc)
    status_n = _normalize_prep_row_status(row_status)
    if not upc_n:
        return '', ''
    if '-' in upc_n:
        return upc_n, ''
    return upc_n, status_n


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    return s in ('1', 'true', 'yes', 'y', 'on')


def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.UTC).isoformat()


def _strict_inventory_quantity(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        parsed = Decimal(str(value).strip())
        if not parsed.is_finite() or parsed != parsed.to_integral_value():
            return None
        return int(parsed)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None
