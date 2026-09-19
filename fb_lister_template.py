"""Facebook Marketplace listing template: categories, category suggestions, and the bulk-upload
workbook the Lister fills for the Facebook queue.

Facebook has no listing API for individual sellers; its bulk upload takes the workbook Facebook
publishes (data/fb_marketplace_bulk_template.xlsx: TITLE, PRICE, CONDITION, DESCRIPTION, CATEGORY,
up to 50 rows from row 5, and a VALIDATION sheet with the allowed conditions and 1,870 categories).
We fill that exact file, so its dropdowns and layout stay what Facebook's uploader expects.

Category suggestions are free (no AI call): words from the title and the eBay category path are
scored against each Facebook category path, rarer words and the last path segment counting most.
"""

import io
import json
import math
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / 'data'
TEMPLATE_PATH = DATA_DIR / 'fb_marketplace_bulk_template.xlsx'
CATEGORIES_PATH = DATA_DIR / 'fb_marketplace_categories.json'
SHEET = 'Bulk Upload Template'
FIRST_ROW = 5          # row 4 holds the headers, row 5 Facebook's example (overwritten)
MAX_ROWS = 50          # Facebook's limit per upload
TITLE_MAX = 150
DESCRIPTION_MAX = 5000
CONDITIONS = ('New', 'Used - Like New', 'Used - Good', 'Used - Fair')

# Our condition enums (eBay inventory names, as the panel uses them) -> Facebook's four.
CONDITION_MAP = {
    'NEW': 'New', 'NEW_OTHER': 'New', 'NEW_WITH_DEFECTS': 'Used - Like New',
    'LIKE_NEW': 'Used - Like New', 'CERTIFIED_REFURBISHED': 'Used - Like New',
    'EXCELLENT_REFURBISHED': 'Used - Like New', 'VERY_GOOD_REFURBISHED': 'Used - Good',
    'GOOD_REFURBISHED': 'Used - Good', 'SELLER_REFURBISHED': 'Used - Good',
    'USED_EXCELLENT': 'Used - Like New', 'USED_VERY_GOOD': 'Used - Good', 'USED_GOOD': 'Used - Good',
    'USED_ACCEPTABLE': 'Used - Fair', 'FOR_PARTS_OR_NOT_WORKING': 'Used - Fair',
}

STOP = {'and', 'or', 'the', 'a', 'an', 'of', 'for', 'with', 'in', 'on', 'to', 'by', 'from', 'new', 'other',
        'set', 'pack', 'pcs', 'pc', 'piece', 'pieces', 'inch', 'in', 'x', 'size', 'color', 'black', 'white',
        'blue', 'red', 'green', 'gray', 'grey', 'pink', 'silver', 'gold', 'accessories', 'supplies', 'products',
        'items', 'misc', 'general', 'equipment', 'parts', 'more', 'all', 'men', 'women', 'unisex'}


def _stem(word):
    word = word.lower()
    for suffix, keep in (('ies', 'y'), ('ches', 'ch'), ('shes', 'sh'), ('sses', 'ss'), ('xes', 'x'), ('s', '')):
        if len(word) > 3 and word.endswith(suffix) and not word.endswith('ss'):
            return word[: -len(suffix)] + keep
    return word


def _words(text):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or '').lower().replace("'", ''))
            if len(w) > 1 and w not in STOP and not w.isdigit()]


@lru_cache(maxsize=1)
def categories():
    return tuple(json.loads(CATEGORIES_PATH.read_text(encoding='utf-8'))['categories'])


@lru_cache(maxsize=1)
def _index():
    cats = categories()
    parsed, df = [], {}
    for path in cats:
        parts = path.split('//')
        weights = {}
        for depth, part in enumerate(parts):
            weight = 3.0 if depth == len(parts) - 1 else (1.5 if depth else 0.8)
            for word in _words(part):
                weights[word] = max(weights.get(word, 0), weight)
        parsed.append(weights)
        for word in weights:
            df[word] = df.get(word, 0) + 1
    idf = {word: math.log(1 + len(cats) / count) for word, count in df.items()}
    return parsed, idf


def suggest_categories(title, ebay_category='', limit=5):
    """Best Facebook categories for an item: [{'category', 'score'}], best first."""
    parsed, idf = _index()
    wanted = {}
    for word in _words(title):
        wanted[word] = max(wanted.get(word, 0), 1.0)
    # The eBay path is a strong hint, its last segment the strongest.
    parts = [p for p in re.split(r'\s*(?:>|//|/|\|)\s*', str(ebay_category or '')) if p.strip()]
    for depth, part in enumerate(parts):
        weight = 1.6 if depth == len(parts) - 1 else 0.9
        for word in _words(part):
            wanted[word] = max(wanted.get(word, 0), weight)
    if not wanted:
        return []
    cats = categories()
    scored = []
    for index, weights in enumerate(parsed):
        score = sum(weights[w] * idf.get(w, 0) * wanted[w] for w in wanted if w in weights)
        if score > 0:
            scored.append((score, -len(cats[index]), cats[index]))
    scored.sort(reverse=True)
    return [{'category': cat, 'score': round(score, 2)} for score, _, cat in scored[:limit]]


def fb_condition(condition):
    value = str(condition or '').strip()
    if value in CONDITIONS:
        return value
    return CONDITION_MAP.get(value.upper().replace(' ', '_'), 'New' if not value else 'Used - Good')


def whole_dollars(price):
    try:
        value = float(str(price).replace('$', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return max(1, int(round(value))) if value > 0 else None


def plain_text(value, limit):
    text = re.sub(r'<\s*(br|/p|/li|/div|/h\d)\s*/?>', '\n', str(value or ''), flags=re.I)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            .replace('&quot;', '"').replace('&#39;', "'"))
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()
    return text[:limit]


def validate_row(row):
    """Problems that stop a row from going into the workbook ([] = ready)."""
    problems = []
    if not str(row.get('title') or '').strip():
        problems.append('title')
    if whole_dollars(row.get('price')) is None:
        problems.append('price')
    if row.get('condition') not in CONDITIONS:
        problems.append('condition')
    category = str(row.get('category') or '')
    if category and category not in set(categories()):
        problems.append('category')
    return problems


CSV_HEADERS = ('TITLE', 'PRICE', 'CONDITION', 'DESCRIPTION', 'CATEGORY')


def _check_rows(rows):
    if not rows:
        raise ValueError('No listings to put in the template.')
    if len(rows) > MAX_ROWS:
        raise ValueError(f'Facebook takes at most {MAX_ROWS} listings per upload.')
    for row in rows:
        problems = validate_row(row)
        if problems:
            raise ValueError(f'{row.get("upc") or row.get("title")}: fix {", ".join(problems)} first.')


def build_csv(rows):
    """The same rows as build_workbook, as the CSV Facebook's bulk page actually accepts (UTF-8, CRLF)."""
    import csv

    _check_rows(rows)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow(CSV_HEADERS)
    for row in rows:
        writer.writerow([str(row['title']).strip()[:TITLE_MAX], whole_dollars(row['price']), row['condition'],
                         plain_text(row.get('description'), DESCRIPTION_MAX), row.get('category') or ''])
    return out.getvalue().encode('utf-8-sig')


def build_workbook(rows):
    """Facebook's own template filled with up to 50 rows -> xlsx bytes."""
    import openpyxl  # only needed when a workbook is built

    _check_rows(rows)
    book = openpyxl.load_workbook(TEMPLATE_PATH)
    sheet = book[SHEET]
    for column in range(1, 6):  # clear Facebook's example row
        sheet.cell(FIRST_ROW, column).value = None
    for offset, row in enumerate(rows):
        line = FIRST_ROW + offset
        sheet.cell(line, 1).value = str(row['title']).strip()[:TITLE_MAX]
        sheet.cell(line, 2).value = whole_dollars(row['price'])
        sheet.cell(line, 3).value = row['condition']
        sheet.cell(line, 4).value = plain_text(row.get('description'), DESCRIPTION_MAX) or None
        sheet.cell(line, 5).value = row.get('category') or None
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
