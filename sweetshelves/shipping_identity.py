"""Shipping identity for Sweet Shelves."""

import datetime
import os
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from . import (
    config as ss_config, database as ss_database, normalization as ss_normalization, prep_context as
    ss_prep_context, prep_schema as ss_prep_schema, warehouse_allocations as ss_warehouse_allocations,
    warehouse_matching as ss_warehouse_matching,
)


def _ensure_order_removal_allocations_table(cur):
    """Persist explicit ready-to-ship location choices for sold-order removals."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS order_removal_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_row_id INTEGER NOT NULL,
            location_key TEXT NOT NULL,
            location_code TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_order_removal_allocations_order ON order_removal_allocations(order_row_id)')


def _ensure_ready_to_ship_notes_table(cur):
    """Persist Ready to Ship-only shipper notes separate from marketplace/order data."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ready_to_ship_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_row_id INTEGER NOT NULL UNIQUE,
            note TEXT NOT NULL,
            label_filename TEXT DEFAULT '',
            label_original_filename TEXT DEFAULT '',
            label_size_bytes INTEGER DEFAULT 0,
            label_uploaded_at TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('PRAGMA table_info(ready_to_ship_notes)')
    columns = {str(row[1]) for row in cur.fetchall()}
    migrations = {
        'label_filename': "ALTER TABLE ready_to_ship_notes ADD COLUMN label_filename TEXT DEFAULT ''",
        'label_original_filename': "ALTER TABLE ready_to_ship_notes ADD COLUMN label_original_filename TEXT DEFAULT ''",
        'label_size_bytes': "ALTER TABLE ready_to_ship_notes ADD COLUMN label_size_bytes INTEGER DEFAULT 0",
        'label_uploaded_at': "ALTER TABLE ready_to_ship_notes ADD COLUMN label_uploaded_at TEXT DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in columns:
            cur.execute(sql)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_ready_to_ship_notes_order ON ready_to_ship_notes(order_row_id)')


def _ensure_ready_to_ship_unmatched_labels_table(cur):
    """Persist labels that could not be confidently matched to an order."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ready_to_ship_unmatched_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label_filename TEXT NOT NULL,
            label_original_filename TEXT DEFAULT '',
            label_size_bytes INTEGER DEFAULT 0,
            label_uploaded_at TEXT DEFAULT '',
            extracted_text TEXT DEFAULT '',
            match_error TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('PRAGMA table_info(ready_to_ship_unmatched_labels)')
    columns = {str(row[1]) for row in cur.fetchall()}
    migrations = {
        'label_original_filename': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN label_original_filename TEXT DEFAULT ''",
        'label_size_bytes': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN label_size_bytes INTEGER DEFAULT 0",
        'label_uploaded_at': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN label_uploaded_at TEXT DEFAULT ''",
        'extracted_text': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN extracted_text TEXT DEFAULT ''",
        'match_error': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN match_error TEXT DEFAULT ''",
        'updated_at': "ALTER TABLE ready_to_ship_unmatched_labels ADD COLUMN updated_at TEXT DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in columns:
            cur.execute(sql)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_ready_to_ship_unmatched_uploaded ON ready_to_ship_unmatched_labels(label_uploaded_at)')


def _ensure_ready_to_ship_order_labels_table(cur):
    """Persist one or more shipping labels attached to a Ready to Ship order."""
    _ensure_ready_to_ship_notes_table(cur)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ready_to_ship_order_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_row_id INTEGER NOT NULL,
            label_filename TEXT NOT NULL,
            label_original_filename TEXT DEFAULT '',
            label_size_bytes INTEGER DEFAULT 0,
            label_uploaded_at TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('PRAGMA table_info(ready_to_ship_order_labels)')
    columns = {str(row[1]) for row in cur.fetchall()}
    migrations = {
        'label_original_filename': "ALTER TABLE ready_to_ship_order_labels ADD COLUMN label_original_filename TEXT DEFAULT ''",
        'label_size_bytes': "ALTER TABLE ready_to_ship_order_labels ADD COLUMN label_size_bytes INTEGER DEFAULT 0",
        'label_uploaded_at': "ALTER TABLE ready_to_ship_order_labels ADD COLUMN label_uploaded_at TEXT DEFAULT ''",
        'updated_at': "ALTER TABLE ready_to_ship_order_labels ADD COLUMN updated_at TEXT DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in columns:
            cur.execute(sql)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_ready_to_ship_order_labels_order ON ready_to_ship_order_labels(order_row_id)')
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_ready_to_ship_order_labels_file ON ready_to_ship_order_labels(order_row_id, label_filename)')
    cur.execute('''
        INSERT OR IGNORE INTO ready_to_ship_order_labels
            (order_row_id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at)
        SELECT order_row_id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at
        FROM ready_to_ship_notes
        WHERE COALESCE(TRIM(label_filename), '') <> ''
    ''')


def _ready_to_ship_row_value(row, key, default=''):
    if not row:
        return default
    try:
        if hasattr(row, 'keys') and key not in row.keys():
            return default
        value = row[key]
        return default if value is None else value
    except Exception:
        try:
            value = row.get(key, default)
            return default if value is None else value
        except Exception:
            return default


def _ready_to_ship_order_label_payload(row=None, order_id=None):
    if not row:
        return {}
    label_id = ss_normalization._coerce_int(_ready_to_ship_row_value(row, 'id', 0), 0)
    row_order_id = ss_normalization._coerce_int(_ready_to_ship_row_value(row, 'order_row_id', order_id or 0), 0)
    order_id = row_order_id or ss_normalization._coerce_int(order_id, 0)
    filename = str(_ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
    original = str(_ready_to_ship_row_value(row, 'label_original_filename', '') or '').strip()
    uploaded_at = str(_ready_to_ship_row_value(row, 'label_uploaded_at', '') or '').strip()
    size_raw = _ready_to_ship_row_value(row, 'label_size_bytes', 0)
    try:
        size_bytes = max(0, int(size_raw or 0))
    except Exception:
        size_bytes = 0
    label_url = ''
    if filename and order_id:
        label_url = (
            f"/api/ready-to-ship/label/{order_id}/file/{label_id}"
            if label_id else
            f"/api/ready-to-ship/label/{order_id}/file"
        )
    return {
        'id': label_id,
        'label_id': label_id,
        'order_id': order_id,
        'label_filename': filename,
        'label_original_filename': original,
        'label_size_bytes': size_bytes,
        'label_uploaded_at': uploaded_at,
        'label_url': label_url,
        'filename': original or filename,
        'saved': bool(filename)
    }


def _ready_to_ship_legacy_label_row(note_row=None):
    filename = str(_ready_to_ship_row_value(note_row, 'label_filename', '') or '').strip()
    if not filename:
        return None
    return {
        'id': 0,
        'order_row_id': _ready_to_ship_row_value(note_row, 'order_row_id', ''),
        'label_filename': filename,
        'label_original_filename': str(_ready_to_ship_row_value(note_row, 'label_original_filename', '') or '').strip(),
        'label_size_bytes': _ready_to_ship_row_value(note_row, 'label_size_bytes', 0),
        'label_uploaded_at': str(_ready_to_ship_row_value(note_row, 'label_uploaded_at', '') or '').strip()
    }


def _ready_to_ship_label_payload(note_row=None, label_rows=None):
    order_id = _ready_to_ship_row_value(note_row, 'order_row_id', '')
    rows = list(label_rows or [])
    if not rows:
        legacy_row = _ready_to_ship_legacy_label_row(note_row)
        if legacy_row:
            rows.append(legacy_row)
    labels = [
        payload for payload in (
            _ready_to_ship_order_label_payload(row, order_id=order_id) for row in rows
        )
        if payload.get('label_filename')
    ]
    primary = labels[0] if labels else {}
    filename = str(primary.get('label_filename') or '').strip()
    original = str(primary.get('label_original_filename') or '').strip()
    uploaded_at = str(primary.get('label_uploaded_at') or '').strip()
    size_bytes = int(primary.get('label_size_bytes') or 0)
    has_label = bool(filename)
    label_url = str(primary.get('label_url') or '').strip()
    return {
        'ready_to_ship_label_filename': filename,
        'ready_to_ship_label_original_filename': original,
        'ready_to_ship_label_size_bytes': size_bytes,
        'ready_to_ship_label_uploaded_at': uploaded_at,
        'ready_to_ship_label_url': label_url,
        'ready_to_ship_label_id': int(primary.get('label_id') or primary.get('id') or 0),
        'ready_to_ship_labels': labels,
        'ready_to_ship_label_count': len(labels),
        'has_ready_to_ship_label': has_label
    }


def _ready_to_ship_unmatched_label_payload(row=None):
    if not row:
        return {}
    label_id = ss_normalization._coerce_int(_ready_to_ship_row_value(row, 'id', 0), 0)
    filename = str(_ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
    original = str(_ready_to_ship_row_value(row, 'label_original_filename', '') or '').strip()
    uploaded_at = str(_ready_to_ship_row_value(row, 'label_uploaded_at', '') or '').strip()
    match_error = str(_ready_to_ship_row_value(row, 'match_error', '') or '').strip()
    try:
        size_bytes = max(0, int(_ready_to_ship_row_value(row, 'label_size_bytes', 0) or 0))
    except Exception:
        size_bytes = 0
    label_url = f"/api/ready-to-ship/labels/unmatched/{label_id}/file" if label_id and filename else ''
    return {
        'id': label_id,
        'unmatched_label_id': label_id,
        'label_filename': filename,
        'label_original_filename': original,
        'label_size_bytes': size_bytes,
        'label_uploaded_at': uploaded_at,
        'label_url': label_url,
        'filename': original or filename,
        'match_error': match_error,
        'saved': bool(label_id and filename)
    }


def _ready_to_ship_label_dir():
    path = ss_config.BASE_DIR / 'debug_uploads' / 'ready_to_ship_labels'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ready_to_ship_label_path(filename):
    safe_name = os.path.basename(str(filename or '').strip())
    if not safe_name or safe_name != str(filename or '').strip() or not safe_name.lower().endswith('.pdf'):
        return None
    path = (_ready_to_ship_label_dir() / safe_name).resolve()
    root = _ready_to_ship_label_dir().resolve()
    try:
        path.relative_to(root)
    except Exception:
        return None
    return path


def _ready_to_ship_delete_label_file(filename):
    path = _ready_to_ship_label_path(filename)
    if not path:
        return
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        print(f"Warning: failed deleting Ready to Ship label {path}: {exc}")


def _ready_to_ship_extract_pdf_text(pdf_path):
    """Best-effort PDF text extraction for label-to-order matching."""
    text_parts = []

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        for page in reader.pages[:3]:
            try:
                text_parts.append(page.extract_text() or '')
            except Exception:
                pass
    except Exception:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(pdf_path))
            for page in reader.pages[:3]:
                try:
                    text_parts.append(page.extract_text() or '')
                except Exception:
                    pass
        except Exception:
            pass

    if not ''.join(text_parts).strip():
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            for page in list(doc)[:3]:
                text_parts.append(page.get_text('text') or '')
            doc.close()
        except Exception:
            pass

    if not ''.join(text_parts).strip():
        try:
            result = subprocess.run(
                ['pdftotext', '-layout', str(pdf_path), '-'],
                capture_output=True,
                text=True,
                timeout=8
            )
            if result.returncode == 0 and result.stdout:
                text_parts.append(result.stdout)
        except Exception:
            pass

    if not ''.join(text_parts).strip():
        try:
            raw = Path(pdf_path).read_bytes()
            decoded = raw.decode('latin-1', errors='ignore')
            text_parts.append(' '.join(re.findall(r'[A-Za-z0-9][A-Za-z0-9 .,#/\-]{2,}', decoded)))
        except Exception:
            pass

    return '\n'.join(part for part in text_parts if part).strip()


def _ready_to_ship_match_norm(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower())).strip()


def _ready_to_ship_match_compact(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


_READY_TO_SHIP_ADDRESS_TOKEN_ALIASES = {
    'aly': 'alley', 'alley': 'alley',
    'apt': 'apartment', 'apartment': 'apartment',
    'ave': 'avenue', 'av': 'avenue', 'aven': 'avenue', 'avenue': 'avenue',
    'blvd': 'boulevard', 'boul': 'boulevard', 'boulevard': 'boulevard',
    'bldg': 'building', 'building': 'building',
    'cir': 'circle', 'circle': 'circle',
    'ct': 'court', 'court': 'court',
    'ctr': 'center', 'center': 'center', 'centre': 'center',
    'dr': 'drive', 'drive': 'drive',
    'e': 'east', 'east': 'east',
    'fl': 'floor', 'floor': 'floor',
    'hwy': 'highway', 'highway': 'highway',
    'ln': 'lane', 'lane': 'lane',
    'n': 'north', 'north': 'north',
    'pkwy': 'parkway', 'parkway': 'parkway',
    'pl': 'place', 'place': 'place',
    'rd': 'road', 'road': 'road',
    'rm': 'room', 'room': 'room',
    's': 'south', 'south': 'south',
    'sq': 'square', 'square': 'square',
    'st': 'street', 'str': 'street', 'street': 'street',
    'ste': 'suite', 'suite': 'suite',
    'ter': 'terrace', 'terrace': 'terrace',
    'trl': 'trail', 'trail': 'trail',
    'unit': 'unit',
    'w': 'west', 'west': 'west',
    'wy': 'way', 'way': 'way',
}


_READY_TO_SHIP_STREET_GENERIC_TOKENS = {
    'alley', 'apartment', 'avenue', 'boulevard', 'building', 'circle',
    'court', 'center', 'drive', 'east', 'floor', 'highway', 'lane',
    'north', 'parkway', 'place', 'road', 'room', 'south', 'square',
    'street', 'suite', 'terrace', 'trail', 'unit', 'west', 'way'
}


_READY_TO_SHIP_STATE_CODES = {
    'alabama': 'al', 'alaska': 'ak', 'arizona': 'az', 'arkansas': 'ar',
    'california': 'ca', 'colorado': 'co', 'connecticut': 'ct', 'delaware': 'de',
    'district of columbia': 'dc', 'florida': 'fl', 'georgia': 'ga', 'hawaii': 'hi',
    'idaho': 'id', 'illinois': 'il', 'indiana': 'in', 'iowa': 'ia',
    'kansas': 'ks', 'kentucky': 'ky', 'louisiana': 'la', 'maine': 'me',
    'maryland': 'md', 'massachusetts': 'ma', 'michigan': 'mi', 'minnesota': 'mn',
    'mississippi': 'ms', 'missouri': 'mo', 'montana': 'mt', 'nebraska': 'ne',
    'nevada': 'nv', 'new hampshire': 'nh', 'new jersey': 'nj', 'new mexico': 'nm',
    'new york': 'ny', 'north carolina': 'nc', 'north dakota': 'nd', 'ohio': 'oh',
    'oklahoma': 'ok', 'oregon': 'or', 'pennsylvania': 'pa', 'rhode island': 'ri',
    'south carolina': 'sc', 'south dakota': 'sd', 'tennessee': 'tn', 'texas': 'tx',
    'utah': 'ut', 'vermont': 'vt', 'virginia': 'va', 'washington': 'wa',
    'west virginia': 'wv', 'wisconsin': 'wi', 'wyoming': 'wy'
}


_READY_TO_SHIP_STATE_NAMES = {code: name for name, code in _READY_TO_SHIP_STATE_CODES.items()}


def _ready_to_ship_address_norm(value):
    tokens = []
    for token in _ready_to_ship_match_norm(value).split():
        tokens.append(_READY_TO_SHIP_ADDRESS_TOKEN_ALIASES.get(token, token))
    return ' '.join(tokens)


def _ready_to_ship_state_variants(value):
    norm = _ready_to_ship_match_norm(value)
    if not norm:
        return []
    variants = {norm}
    code = _READY_TO_SHIP_STATE_CODES.get(norm)
    if code:
        variants.add(code)
    name = _READY_TO_SHIP_STATE_NAMES.get(norm)
    if name:
        variants.add(name)
    return sorted(variants, key=len, reverse=True)


def _ready_to_ship_insert_unmatched_label(cur, filename, original_filename, size_bytes, extracted_text='', match_error='No address match found'):
    _ensure_ready_to_ship_unmatched_labels_table(cur)
    now_iso = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    cur.execute('''
        INSERT INTO ready_to_ship_unmatched_labels
            (label_filename, label_original_filename, label_size_bytes, label_uploaded_at, extracted_text, match_error)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        filename,
        original_filename,
        int(size_bytes or 0),
        now_iso,
        str(extracted_text or '')[:20000],
        str(match_error or '')[:500]
    ))
    return cur.execute('''
        SELECT id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, match_error, created_at, updated_at
        FROM ready_to_ship_unmatched_labels
        WHERE id = ?
    ''', (cur.lastrowid,)).fetchone()


def _ready_to_ship_label_match_score(label_text, order_row):
    hay = _ready_to_ship_match_norm(label_text)
    hay_compact = _ready_to_ship_match_compact(label_text)
    hay_tokens = set(hay.split())
    hay_address = _ready_to_ship_address_norm(label_text)
    hay_address_compact = _ready_to_ship_match_compact(hay_address)
    hay_address_tokens = set(hay_address.split())
    if not hay:
        return 0, []

    def val(*keys):
        for key in keys:
            raw = _ready_to_ship_row_value(order_row, key, '')
            if str(raw or '').strip():
                return str(raw or '').strip()
        return ''

    name = val('shipping_name', 'buyer_name', 'name')
    street1 = val('shipping_street1', 'shipping_address1', 'street1', 'address1')
    street2 = val('shipping_street2', 'shipping_address2', 'street2', 'address2')
    city = val('shipping_city', 'city')
    state = val('shipping_state', 'state')
    postal = val('shipping_postal_code', 'shipping_zip', 'postal_code', 'zip')
    country = val('shipping_country', 'country')
    order_ref = val('order_id', 'legacy_order_id')
    store = val('store')
    is_amazon_order = _ready_to_ship_match_norm(store) == 'amazon'

    score = 0
    reasons = []

    order_ref_compact = _ready_to_ship_match_compact(order_ref)
    if order_ref_compact and len(order_ref_compact) >= 6 and order_ref_compact in hay_compact:
        score += 50 if len(order_ref_compact) >= 10 else 30
        reasons.append('order')

    postal_digits = re.sub(r'\D+', '', postal)
    postal_compact = _ready_to_ship_match_compact(postal)
    if postal_digits or postal_compact:
        variants = set()
        if postal_compact:
            variants.add(postal_compact)
            if len(postal_compact) >= 5:
                variants.add(postal_compact[:5])
        if postal_digits:
            variants.add(postal_digits)
            if len(postal_digits) >= 5:
                variants.add(postal_digits[:5])
        if len(postal_digits) >= 5:
            variants.add(postal_digits[:5])
        if any(v and len(v) >= 3 and v in hay_compact for v in variants):
            score += 34
            reasons.append('postal')

    street_norm = _ready_to_ship_address_norm(street1)
    street_compact = _ready_to_ship_match_compact(street_norm)
    if street_compact and street_compact in hay_address_compact:
        score += 38
        reasons.append('street')
    elif street_norm:
        street_tokens = street_norm.split()
        words = [
            w for w in street_tokens
            if len(w) > 2 and not w.isdigit() and w not in _READY_TO_SHIP_STREET_GENERIC_TOKENS
        ]
        street_number = next((w for w in street_tokens if re.search(r'\d', w)), '')
        word_hits = sum(1 for w in words if w in hay_address_tokens)
        if street_number and street_number in hay_address and word_hits >= 1:
            score += 32
            reasons.append('street')
        elif len(words) >= 2 and word_hits >= min(2, len(words)):
            score += 20
            reasons.append('street-ish')

    if street2:
        street2_norm = _ready_to_ship_address_norm(street2)
        if street2_norm and street2_norm in hay_address:
            score += 6
            reasons.append('street2')

    name_norm = _ready_to_ship_match_norm(name)
    name_words = [w for w in name_norm.split() if len(w) > 1]
    generic_name = name_norm in {'amazon buyer', 'ebay buyer', 'buyer', 'test customer'}
    name_is_missing_or_generic = (not name_norm) or generic_name
    if name_norm and not generic_name and _ready_to_ship_match_compact(name_norm) in hay_compact:
        score += 22
        reasons.append('name')
    elif name_words and not generic_name:
        name_hits = sum(1 for w in name_words if w in hay_tokens)
        if name_hits >= min(2, len(name_words)):
            score += 16
            reasons.append('name')
        elif len(name_words) >= 2 and name_words[-1] in hay_tokens:
            score += 8
            reasons.append('name-part')

    city_norm = _ready_to_ship_match_norm(city)
    city_tokens = [w for w in city_norm.split() if len(w) > 1]
    if city_norm and (city_norm in hay or (city_tokens and all(w in hay_tokens for w in city_tokens))):
        score += 10
        reasons.append('city')

    state_variants = _ready_to_ship_state_variants(state)
    if state_variants and any(re.search(rf'\b{re.escape(v)}\b', hay) for v in state_variants):
        score += 7
        reasons.append('state')

    country_norm = _ready_to_ship_match_norm(country)
    if country_norm and country_norm in hay:
        score += 3
        reasons.append('country')

    has_street_signal = 'street' in reasons or 'street-ish' in reasons
    if (
        is_amazon_order
        and name_is_missing_or_generic
        and 'postal' in reasons
        and ('city' in reasons or 'state' in reasons)
        and (not street_norm or has_street_signal)
    ):
        score += 8
        reasons.append('amazon-address')

    has_address_anchor = 'postal' in reasons and (
        'street' in reasons or 'street-ish' in reasons or 'name' in reasons or 'name-part' in reasons or 'city' in reasons
    )
    has_street_name = 'street' in reasons and ('name' in reasons or 'name-part' in reasons)
    has_order_anchor = 'order' in reasons and score >= 30
    has_amazon_address_anchor = 'amazon-address' in reasons
    if not (has_order_anchor or has_address_anchor or has_street_name or has_amazon_address_anchor or score >= 72):
        return 0, reasons
    return score, reasons


def _ready_to_ship_find_label_match(cur, label_text, days=2):
    try:
        days_int = max(1, min(int(days or 2), 120))
    except Exception:
        days_int = 2

    cur.execute('''
        SELECT *
        FROM orders
        WHERE paid_time >= date('now', '-' || ? || ' days')
        ORDER BY CASE WHEN COALESCE(TRIM(isHandled), '') = '1' THEN 1 ELSE 0 END,
                 paid_time DESC,
                 id DESC
    ''', (days_int,))

    best = None
    for row in cur.fetchall():
        score, reasons = _ready_to_ship_label_match_score(label_text, row)
        if score <= 0:
            continue
        if best is None or score > best['score']:
            best = {'order': row, 'score': score, 'reasons': reasons}

    return best


def _ready_to_ship_save_uploaded_label_file(file_storage):
    from werkzeug.utils import secure_filename

    original_name = secure_filename(file_storage.filename or 'label.pdf') or 'label.pdf'
    if not original_name.lower().endswith('.pdf'):
        raise ValueError('Only PDF label files are supported.')

    data = file_storage.read()
    if not data:
        raise ValueError('PDF file is empty.')
    if len(data) > 16 * 1024 * 1024:
        raise ValueError('PDF file is too large (max 16 MB).')
    if not data.lstrip().startswith(b'%PDF'):
        raise ValueError('That file does not look like a PDF.')

    filename = f"{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.pdf"
    path = _ready_to_ship_label_dir() / filename
    path.write_bytes(data)
    return filename, original_name, len(data), path


def _ready_to_ship_get_note_row(cur, order_id):
    return cur.execute('''
        SELECT order_row_id, note, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, created_at, updated_at
        FROM ready_to_ship_notes
        WHERE order_row_id = ?
    ''', (order_id,)).fetchone()


def _ready_to_ship_get_order_label_rows(cur, order_id):
    _ensure_ready_to_ship_order_labels_table(cur)
    return cur.execute('''
        SELECT id, order_row_id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, created_at, updated_at
        FROM ready_to_ship_order_labels
        WHERE order_row_id = ?
          AND COALESCE(TRIM(label_filename), '') <> ''
        ORDER BY datetime(label_uploaded_at) DESC, id DESC
    ''', (order_id,)).fetchall()


def _ready_to_ship_sync_legacy_label_columns(cur, order_id):
    _ensure_ready_to_ship_notes_table(cur)
    latest = cur.execute('''
        SELECT label_filename, label_original_filename, label_size_bytes, label_uploaded_at
        FROM ready_to_ship_order_labels
        WHERE order_row_id = ?
          AND COALESCE(TRIM(label_filename), '') <> ''
        ORDER BY datetime(label_uploaded_at) DESC, id DESC
        LIMIT 1
    ''', (order_id,)).fetchone()
    existing = cur.execute('SELECT * FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,)).fetchone()
    note_text = str(_ready_to_ship_row_value(existing, 'note', '') or '').strip() if existing else ''
    if latest:
        if existing:
            cur.execute('''
                UPDATE ready_to_ship_notes
                SET label_filename = ?,
                    label_original_filename = ?,
                    label_size_bytes = ?,
                    label_uploaded_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_row_id = ?
            ''', (
                str(_ready_to_ship_row_value(latest, 'label_filename', '') or '').strip(),
                str(_ready_to_ship_row_value(latest, 'label_original_filename', '') or '').strip(),
                int(_ready_to_ship_row_value(latest, 'label_size_bytes', 0) or 0),
                str(_ready_to_ship_row_value(latest, 'label_uploaded_at', '') or '').strip(),
                order_id
            ))
        else:
            cur.execute('''
                INSERT INTO ready_to_ship_notes
                    (order_row_id, note, label_filename, label_original_filename, label_size_bytes, label_uploaded_at)
                VALUES (?, '', ?, ?, ?, ?)
            ''', (
                order_id,
                str(_ready_to_ship_row_value(latest, 'label_filename', '') or '').strip(),
                str(_ready_to_ship_row_value(latest, 'label_original_filename', '') or '').strip(),
                int(_ready_to_ship_row_value(latest, 'label_size_bytes', 0) or 0),
                str(_ready_to_ship_row_value(latest, 'label_uploaded_at', '') or '').strip()
            ))
    elif existing:
        if note_text:
            cur.execute('''
                UPDATE ready_to_ship_notes
                SET label_filename = '',
                    label_original_filename = '',
                    label_size_bytes = 0,
                    label_uploaded_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_row_id = ?
            ''', (order_id,))
        else:
            cur.execute('DELETE FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,))


def _ready_to_ship_payload_for_order(cur, order_id):
    _ensure_ready_to_ship_notes_table(cur)
    _ensure_ready_to_ship_order_labels_table(cur)
    note_row = _ready_to_ship_get_note_row(cur, order_id)
    label_rows = _ready_to_ship_get_order_label_rows(cur, order_id)
    return _ready_to_ship_note_payload(note_row, label_rows)


def _ready_to_ship_attach_label(cur, order_id, filename, original_filename, size_bytes):
    _ensure_ready_to_ship_order_labels_table(cur)
    existing = cur.execute('SELECT * FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,)).fetchone()
    now_iso = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

    if existing:
        cur.execute('''
            UPDATE ready_to_ship_notes
            SET label_filename = ?,
                label_original_filename = ?,
                label_size_bytes = ?,
                label_uploaded_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_row_id = ?
        ''', (filename, original_filename, int(size_bytes or 0), now_iso, order_id))
    else:
        cur.execute('''
            INSERT INTO ready_to_ship_notes
                (order_row_id, note, label_filename, label_original_filename, label_size_bytes, label_uploaded_at)
            VALUES (?, '', ?, ?, ?, ?)
        ''', (order_id, filename, original_filename, int(size_bytes or 0), now_iso))

    cur.execute('''
        INSERT OR IGNORE INTO ready_to_ship_order_labels
            (order_row_id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (order_id, filename, original_filename, int(size_bytes or 0), now_iso))

    return _ready_to_ship_get_note_row(cur, order_id)


def _ensure_order_finder_matches_table(cur):
    """Persist the exact SEARCHRACK row chosen from finder.html for an order."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS order_finder_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_row_id INTEGER NOT NULL UNIQUE,
            searchrack_id INTEGER NOT NULL,
            barcode TEXT,
            location TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_order_finder_matches_order ON order_finder_matches(order_row_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_order_finder_matches_searchrack ON order_finder_matches(searchrack_id)')


def _ready_to_ship_note_payload(note_row=None, label_rows=None):
    if not note_row:
        payload = {
            'ready_to_ship_note': '',
            'ready_to_ship_note_created_at': '',
            'ready_to_ship_note_updated_at': '',
            'has_ready_to_ship_note': False
        }
        payload.update(_ready_to_ship_label_payload(None, label_rows))
        return payload

    note_text = str(_ready_to_ship_row_value(note_row, 'note', '') or '')
    payload = {
        'ready_to_ship_note': note_text,
        'ready_to_ship_note_created_at': str(_ready_to_ship_row_value(note_row, 'created_at', '') or ''),
        'ready_to_ship_note_updated_at': str(_ready_to_ship_row_value(note_row, 'updated_at', '') or ''),
        'has_ready_to_ship_note': bool(note_text.strip())
    }
    payload.update(_ready_to_ship_label_payload(note_row, label_rows))
    return payload


def _apply_ready_to_ship_note_payload(order_dict, note_row=None, label_rows=None):
    payload = _ready_to_ship_note_payload(note_row, label_rows)
    order_dict.update(payload)
    return order_dict


def _load_ready_to_ship_note_lookup(cur, order_row_ids):
    ids = []
    seen = set()
    for raw_id in (order_row_ids or []):
        try:
            order_id = int(raw_id)
        except Exception:
            continue
        if order_id in seen:
            continue
        seen.add(order_id)
        ids.append(order_id)

    if not ids:
        return {}

    _ensure_ready_to_ship_notes_table(cur)
    placeholders = ','.join('?' for _ in ids)
    rows = cur.execute(f'''
        SELECT order_row_id, note, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, created_at, updated_at
        FROM ready_to_ship_notes
        WHERE order_row_id IN ({placeholders})
    ''', ids).fetchall()

    lookup = {}
    for row in rows:
        try:
            lookup[int(row['order_row_id'])] = row
        except Exception:
            continue
    return lookup


def _load_ready_to_ship_label_lookup(cur, order_row_ids):
    ids = []
    seen = set()
    for raw_id in (order_row_ids or []):
        try:
            order_id = int(raw_id)
        except Exception:
            continue
        if order_id in seen:
            continue
        seen.add(order_id)
        ids.append(order_id)

    if not ids:
        return {}

    _ensure_ready_to_ship_order_labels_table(cur)
    placeholders = ','.join('?' for _ in ids)
    rows = cur.execute(f'''
        SELECT id, order_row_id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, created_at, updated_at
        FROM ready_to_ship_order_labels
        WHERE order_row_id IN ({placeholders})
          AND COALESCE(TRIM(label_filename), '') <> ''
        ORDER BY order_row_id, datetime(label_uploaded_at) DESC, id DESC
    ''', ids).fetchall()

    lookup = {}
    for row in rows:
        try:
            lookup.setdefault(int(row['order_row_id']), []).append(row)
        except Exception:
            continue
    return lookup


def _load_order_finder_match_lookup(cur, order_row_ids):
    ids = []
    seen = set()
    for raw_id in (order_row_ids or []):
        try:
            oid = int(raw_id)
        except Exception:
            continue
        if oid in seen:
            continue
        seen.add(oid)
        ids.append(oid)

    if not ids:
        return {}

    _ensure_order_finder_matches_table(cur)
    placeholders = ','.join('?' for _ in ids)
    rows = cur.execute(f'''
        SELECT order_row_id, searchrack_id, barcode, location, created_at, updated_at
        FROM order_finder_matches
        WHERE order_row_id IN ({placeholders})
    ''', ids).fetchall()

    lookup = {}
    for row in rows:
        try:
            lookup[int(row['order_row_id'])] = row
        except Exception:
            continue
    return lookup


def _ready_to_ship_image_value(value):
    """Return a usable image reference, or an empty string for missing values."""
    image = str(value or '').strip()
    if not image or image.lower() in {'nan', 'none', 'null', 'undefined', 'n/a', 'na'}:
        return ''
    return image


def _ready_to_ship_rawbol_image(rawbol_cur, values):
    """Find the newest Raw BOL image matching any form of an order barcode."""
    candidates = []
    seen = set()
    for value in values:
        for candidate in ss_warehouse_matching._sold_removal_barcode_variants(value):
            normalized = str(candidate or '').strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                candidates.append(normalized)
    if not candidates:
        return ''

    placeholders = ','.join('?' for _ in candidates)
    row = rawbol_cur.execute(f'''
        SELECT image_url
        FROM raw_bol_items
        WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
          AND TRIM(COALESCE(image_url, '')) != ''
        ORDER BY rowid DESC
        LIMIT 1
    ''', candidates).fetchone()
    return _ready_to_ship_image_value(row[0] if row else '')


def _is_exact_traced_suffixed_sold_order(order):
    source_upc = str(ss_warehouse_matching._sold_order_value(order, 'source_upc', '') or '').strip()
    if not source_upc or '-' not in source_upc:
        return False
    trace_source = str(ss_warehouse_matching._sold_order_value(order, 'listing_trace_source', '') or '').strip().lower()
    trace_id = str(ss_warehouse_matching._sold_order_value(order, 'listing_trace_id', '') or '').strip()
    return trace_source == 'listingagent' or bool(trace_id)


def _barcode_base_without_suffix(value):
    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(value)
    if not upc:
        return ''
    return upc.split('-', 1)[0]


def _ready_to_ship_warehouse_qty_for_barcode(barcode):
    rack_conn = None
    try:
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        matches = ss_warehouse_matching._searchrack_matches_for_barcode(rack_cur, barcode, include_zero=False)
        if not matches:
            base_barcode = _barcode_base_without_suffix(barcode)
            if base_barcode and ss_normalization._normalize_upc_preserve_suffix_for_match(base_barcode) != ss_normalization._normalize_upc_preserve_suffix_for_match(barcode):
                matches = ss_warehouse_matching._searchrack_matches_for_barcode(rack_cur, base_barcode, include_zero=False)
        return sum(max(0, ss_normalization._coerce_int(row.get('quantity'), 0)) for row in matches)
    except Exception:
        return 0
    finally:
        if rack_conn is not None:
            rack_conn.close()


def _ready_to_ship_rawbol_total_qty_for_barcode(barcode):
    base_barcode = _barcode_base_without_suffix(barcode) or str(barcode or '').strip()
    target_key = ss_warehouse_matching._sold_removal_barcode_key(base_barcode)
    variants = sorted(v.lower() for v in ss_warehouse_matching._sold_removal_barcode_variants(base_barcode))
    invalid_upc_values = {'null', 'n/a', 'does not apply'}
    if not target_key or target_key in invalid_upc_values:
        return 0

    conn = None
    total_qty = 0
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_bol_items'")
        has_raw_items = bool(cur.fetchone())
        if has_raw_items:
            cur.execute("PRAGMA table_info('raw_bol_items')")
            cols = [r[1] for r in cur.fetchall()]
            cols_lower = {c.lower(): c for c in cols}
            upc_col = cols_lower.get('upc') or cols_lower.get('barcode')
            qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
            if upc_col and qty_col:
                base_sql = f'''
                    SELECT {upc_col} AS upc, {qty_col} AS qty
                    FROM raw_bol_items
                    WHERE {upc_col} IS NOT NULL
                      AND TRIM({upc_col}) != ''
                '''
                if variants:
                    placeholders = ','.join('?' for _ in variants)
                    cur.execute(base_sql + f" AND LOWER(TRIM({upc_col})) IN ({placeholders})", tuple(variants))
                else:
                    cur.execute(base_sql)
                for row in cur.fetchall():
                    raw_upc = str(row['upc'] or '').strip()
                    if ss_warehouse_matching._sold_removal_barcode_key(raw_upc) != target_key:
                        continue
                    total_qty += max(0, ss_normalization._coerce_int(row['qty'], 0))
                if total_qty > 0:
                    return total_qty

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rawbol'")
        has_legacy = bool(cur.fetchone())
        if has_legacy:
            cur.execute("PRAGMA table_info('rawbol')")
            cols = [r[1] for r in cur.fetchall()]
            cols_lower = {c.lower(): c for c in cols}
            upc_col = cols_lower.get('upc') or cols_lower.get('barcode')
            qty_col = cols_lower.get('qty') or cols_lower.get('quantity')
            if upc_col and qty_col:
                base_sql = f'''
                    SELECT {upc_col} AS upc, {qty_col} AS qty
                    FROM rawbol
                    WHERE {upc_col} IS NOT NULL
                      AND TRIM({upc_col}) != ''
                '''
                if variants:
                    placeholders = ','.join('?' for _ in variants)
                    cur.execute(base_sql + f" AND LOWER(TRIM({upc_col})) IN ({placeholders})", tuple(variants))
                else:
                    cur.execute(base_sql)
                for row in cur.fetchall():
                    raw_upc = str(row['upc'] or '').strip()
                    if ss_warehouse_matching._sold_removal_barcode_key(raw_upc) != target_key:
                        continue
                    total_qty += max(0, ss_normalization._coerce_int(row['qty'], 0))
        return total_qty
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _ready_to_ship_sold_count_for_barcode(barcode):
    base_barcode = _barcode_base_without_suffix(barcode) or str(barcode or '').strip()
    target_key = ss_warehouse_matching._sold_removal_barcode_key(base_barcode)
    variants = sorted(v.lower() for v in ss_warehouse_matching._sold_removal_barcode_variants(base_barcode))
    invalid_upc_values = {'null', 'n/a', 'does not apply'}
    if not target_key or target_key in invalid_upc_values:
        return 0
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        if variants:
            placeholders = ','.join('?' for _ in variants)
            cur.execute(
                f"SELECT COALESCE(SUM(COALESCE(quantity,1)),0) FROM orders WHERE LOWER(TRIM(barcode)) IN ({placeholders})",
                tuple(variants)
            )
        else:
            cur.execute(
                "SELECT COALESCE(SUM(COALESCE(quantity,1)),0) FROM orders WHERE LOWER(TRIM(barcode)) = ?",
                (base_barcode.lower(),)
            )
        row = cur.fetchone()
        return max(0, ss_normalization._coerce_int(row[0], 0)) if row else 0
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _ready_to_ship_items_to_list_stats_for_barcode(barcode):
    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
    base_upc = upc.split('-', 1)[0] if upc else ''
    payload = {
        'base_barcode': base_upc,
        'prepped_qty': 0,
        'items_to_list_url': ss_warehouse_allocations._items_prep_items_to_list_url(base_upc) if base_upc else '/items-to-list'
    }
    if not base_upc:
        return payload

    like_related = ss_prep_context._ready_to_ship_prep_related_like(base_upc)

    try:
        ss_prep_schema._ensure_items_prep_tables()
        with ss_database.db_connection('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info('bol_items')")
            cols = [r[1] for r in cur.fetchall()]
            if not cols:
                return payload

            has_itemprepped = any(c.lower() == 'itemprepped' for c in cols)
            has_original_qty = any(c.lower() == 'original_qty' for c in cols)
            has_good_qty = any(c.lower() == 'good_qty' for c in cols)
            has_bad_qty = any(c.lower() == 'bad_qty' for c in cols)
            has_unchecked_qty = any(c.lower() == 'unchecked_qty' for c in cols)

            prep_status_expr = "COALESCE(s_exact.status, s_fallback.status)"
            prep_qty_expr = "COALESCE(s_exact.quantity, s_fallback.quantity)"
            prep_join = (
                " FROM bol_items b "
                "LEFT JOIN items_prep_status s_exact "
                "ON s_exact.upc = b.upc "
                "AND COALESCE(s_exact.lot_number, '') = COALESCE(b.lot_number, '') "
                "LEFT JOIN items_prep_status s_fallback "
                "ON s_fallback.upc = b.upc "
                "AND COALESCE(s_fallback.lot_number, '') = '' "
                "AND s_exact.id IS NULL "
            )

            sql = (
                'SELECT b.upc, COALESCE(b.lot_number, "") AS lot_number, COALESCE(b.quantity, 1) AS quantity, ' +
                ('COALESCE(b.original_qty, NULL) AS original_qty, ' if has_original_qty else 'NULL AS original_qty, ') +
                ('COALESCE(b.good_qty, NULL) AS good_qty, ' if has_good_qty else 'NULL AS good_qty, ') +
                ('COALESCE(b.bad_qty, NULL) AS bad_qty, ' if has_bad_qty else 'NULL AS bad_qty, ') +
                ('COALESCE(b.unchecked_qty, NULL) AS unchecked_qty, ' if has_unchecked_qty else 'NULL AS unchecked_qty, ') +
                f'{prep_status_expr} AS prep_status, {prep_qty_expr} AS prep_quantity '
                + prep_join +
                '''WHERE b.id = (
                        SELECT MAX(b2.id)
                        FROM bol_items b2
                        WHERE b2.upc = b.upc COLLATE NOCASE
                          AND COALESCE(b2.lot_number, '') = COALESCE(b.lot_number, '')
                    ) '''
            )
            if has_itemprepped:
                sql += "AND (b.itemprepped IS NULL OR b.itemprepped = 0) "
            sql += "AND (b.upc = ? COLLATE NOCASE OR b.upc LIKE ? ESCAPE '\\')"

            cur.execute(sql, (base_upc, like_related))
            total_qty = 0
            for row in cur.fetchall():
                row_dict = dict(row)
                row_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row_dict.get('upc'))
                if not row_upc:
                    continue

                good_bucket = max(0, ss_normalization._coerce_int(row_dict.get('good_qty'), 0))
                bad_bucket = max(0, ss_normalization._coerce_int(row_dict.get('bad_qty'), 0))
                status = ss_normalization._items_to_list_effective_status(
                    row_upc,
                    row_dict.get('prep_status'),
                    good_qty=good_bucket,
                    bad_qty=bad_bucket
                )
                if status not in ('good', 'bad', 'return'):
                    continue

                prep_qty = row_dict.get('prep_quantity')
                if prep_qty is not None:
                    display_qty = max(0, ss_normalization._coerce_int(prep_qty, ss_normalization._coerce_int(row_dict.get('quantity'), 1)))
                elif status == 'good':
                    display_qty = max(0, ss_normalization._coerce_int(row_dict.get('good_qty'), ss_normalization._coerce_int(row_dict.get('quantity'), 1)))
                elif status == 'bad':
                    display_qty = max(0, ss_normalization._coerce_int(row_dict.get('bad_qty'), ss_normalization._coerce_int(row_dict.get('quantity'), 1)))
                else:
                    display_qty = max(0, ss_normalization._coerce_int(row_dict.get('quantity'), 1))

                total_qty += display_qty

            payload['prepped_qty'] = max(0, total_qty)
    except Exception:
        pass

    return payload
