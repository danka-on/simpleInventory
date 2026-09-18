"""Sweet Shelves Lister: the server side of the Chrome listing side panel.

The extension opens beside an eBay or Amazon Seller Central listing page, shows the listing
proposals that were selected for the browser, fills what it can on the store page, and, once
the listing is live, confirms it here. Confirming writes the store listing <-> warehouse link
(listing_links, the listing queue, the store-listing fallback match and a Finder alias), so
Ready to Ship and the warehouse pages resolve the order even when the store shows a different
UPC than the one on our rack.

This module is shared verbatim by the modular app (sweetshelves/routing.py) and the Desktop
debby app.py, so every dependency is injected through register(app, deps).
"""

import base64
import datetime
import html as html_module
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from urllib.parse import quote

from flask import jsonify, render_template, request, send_from_directory

VERSION = '0.2.34'
PLATFORMS = ('ebay', 'amazon')
OPEN_STATUSES = ('proposed', 'held', 'needs_photos', 'blocked')
MUTATION_HEADER = 'X-Sweet-Shelves-Lister'
FEED_REQUIRED = ('manifest.json', 'background.js', 'sidepanel.html')

# Prep notes that mean the unit is not new (same list the Listing Agent gate uses).
DAMAGE_WORDS = (
    'damage', 'damaged', 'scratch', 'scratched', 'dent', 'dented', 'broken', 'crack', 'cracked', 'torn',
    'tear', 'missing', 'stain', 'stained', 'chip', 'chipped', 'worn', 'defect', 'open box', 'opened',
)

# "AI photoshop": one OpenAI image edit per photo. The prompt keeps the product untouched.
OPENAI_IMAGE_MODEL = 'gpt-image-1'
DEFAULT_AI_PHOTO_PROMPT = (
    'Clean up this product photo for an online marketplace listing. Replace the background with a plain, '
    'evenly lit white studio background and fix the exposure and white balance. Keep the product itself exactly '
    'as it is: same shape, colors, labels, printed text, wear and any damage. Do not add, remove or retouch '
    'anything on the product. Keep the whole product visible and centered.'
)
AI_PHOTO_MAX_BYTES = 20_000_000
AI_PHOTO_TIMEOUT = 100  # gunicorn gives a request 120 s

# A proposal build (eBay catalog, comps, category, Claude title/description) can take a minute.
PREPARE_STALE_SECONDS = 600

# eBay inventory condition enum -> Amazon condition_type used by the Listings Items API.
AMAZON_CONDITIONS = {
    'NEW': 'new_new',
    'NEW_OTHER': 'new_open_box',
    'NEW_WITH_DEFECTS': 'new_open_box',
    'USED_EXCELLENT': 'used_like_new',
    'USED_VERY_GOOD': 'used_very_good',
    'USED_GOOD': 'used_good',
    'USED_ACCEPTABLE': 'used_acceptable',
}

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'[ \t]+')
_EBAY_ITEM_RE = re.compile(r'^\d{9,15}$')
_ASIN_RE = re.compile(r'^[A-Z0-9]{10}$')


class ListerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _loads(text, default):
    if text in (None, ''):
        return default
    try:
        value = json.loads(text)
    except Exception:
        return default
    return value if value is not None else default


def _order_key(stamp):
    """ISO timestamps sort as text; turn one into a number so newest-first sorting needs no parsing."""
    digits = re.sub(r'\D', '', str(stamp or ''))[:14]
    return int(digits) if digits else 0


def _row(row):
    if row is None:
        return None
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return dict(row)


def _text(value, limit=None):
    text = str(value if value is not None else '').strip()
    return text[:limit] if limit else text


def html_to_text(value):
    """Plain text for stores that reject HTML (Amazon condition notes, eBay title fields)."""
    text = str(value or '')
    text = re.sub(r'(?i)<\s*br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|h[1-6]|tr|ul|ol|table)\s*>', '\n\n', text)
    text = re.sub(r'(?i)</\s*li\s*>', '\n', text)
    text = _TAG_RE.sub('', text)
    text = html_module.unescape(text)
    text = _WS_RE.sub(' ', text)
    return re.sub(r'\n\s*\n+', '\n\n', text).strip()


def _absolute(url, base_url):
    value = _text(url)
    if not value:
        return ''
    if value.startswith('/'):
        return base_url.rstrip('/') + value
    return value


def _thumb_url(url, base_url, width=240):
    """A list thumbnail the side panel can load. Our own photos (a + NEW item's picture is a
    /static/custom_items phone photo) are site-relative, which an extension page cannot resolve,
    and full size; they go through the server's resizer instead."""
    value = _text(url)
    if value.startswith('/static/'):
        value = '/static-thumb/' + value[len('/static/'):] + '?w=' + str(width)
    return _absolute(value, base_url)


def mobile_photos_url(upc, base_url, *, camera=True, back=True, token=''):
    """The phone camera page for one unit. camera=1 opens the camera by itself; the QR code keeps
    a Back link, the Telegram message does not, because there the URL is the whole message."""
    url = (base_url or '').rstrip('/') + '/items-to-list/mobile-photos?upc=' + quote(_text(upc), safe='')
    if back:
        url += '&return=%2Fitems-to-list'
    if camera:
        url += '&camera=1'
    if token:
        url += '&t=' + quote(_text(token), safe='')
    return url


def _base_upc(upc):
    """'035886267162-1' -> '035886267162': the catalog code a store search understands."""
    return _text(upc).split('-', 1)[0].strip()


def secure_image_url(url):
    """The BOL carries its catalog pictures as http:// links (Macy's and Bloomingdale's Scene7).
    A page served over https - the side panel included - will not load a plain http picture, so the
    link is asked for over https instead. Both hosts answer there; anything else is left alone."""
    value = _text(url)
    return 'https://' + value[len('http://'):] if value[:7].lower() == 'http://' else value


def is_suffixed(upc):
    return '-' in _text(upc)


def condition_from_notes(notes, *, suffixed=False, default='NEW_OTHER', prep_status=''):
    """Condition + condition note from prep evidence. A flaw word means used; the note text becomes
    the store's condition description. English voice-note text is expected to be in `notes` already.
    Item Prep passing a plain unit (status good, nothing written or said about it) is evidence the
    other way: that unit is new, so both stores get NEW instead of the assumed default."""
    cleaned = []
    for note in notes or []:
        text = _text(note)
        if text and text not in cleaned:
            cleaned.append(text)
    joined = '\n'.join(cleaned)
    lowered = joined.lower()
    damaged = any(word in lowered for word in DAMAGE_WORDS)
    if damaged:
        return {'condition': 'USED_GOOD', 'conditionDescription': joined[:1000],
                'reason': 'prep notes mention a flaw', 'assumed': False}
    # A suffixed unit was split off for a reason, so it never auto-upgrades to NEW.
    if not joined and not suffixed and _text(prep_status).lower() == 'good':
        return {'condition': 'NEW', 'conditionDescription': '',
                'reason': 'Item Prep passed it good with no notes', 'assumed': False}
    if suffixed:
        return {'condition': default, 'conditionDescription': joined[:1000],
                'reason': 'suffixed unit without a flaw note; confirm the condition', 'assumed': True}
    return {'condition': default, 'conditionDescription': joined[:1000],
            'reason': 'no flaw recorded; default condition assumed', 'assumed': True}


def note_text_variants(note):
    """Item Prep stores 'LT: ... | EN: ...'. The English half is what a listing should use."""
    text = _text(note)
    if not text:
        return ''
    match = re.search(r'(?:^|\|)\s*EN:\s*(.+?)\s*$', text, flags=re.S)
    if match:
        return _text(match.group(1))
    if text.upper().startswith('LT:') and '|' not in text:
        return ''  # Lithuanian only; the caller may translate it
    return text


# The user's ilona-corner eBay description (2026-09-17): an ITEM DESCRIPTION card (intro, an important
# condition note when there is a flaw, a facts list, "review all photos"), an ITEM CONDITION card, then the
# shop's fixed welcome / condition & photos / authenticity / shipping / returns / help / footer blocks.
# Claude writes only the item parts; everything from SHOP_FOOTER on is the user's text, kept as given.
DESCRIPTION_OUTER = '<div style="max-width: 900px; margin: 0px auto; line-height: 1.7;">'
DESCRIPTION_BOX = ('<div style="color: rgb(77, 74, 67); font-family: Arial, Helvetica, sans-serif; background: rgb(250, 247, 240); '
                   'border: 1px solid rgb(216, 207, 189); padding: 30px; margin-bottom: 18px;">')
DESCRIPTION_TITLE = ('<div style="font-family:Georgia,\'Times New Roman\',serif; font-size:25px; color:#596456; text-align:center; '
                     'letter-spacing:1px; margin-bottom:20px;">ITEM DESCRIPTION</div>')
CONDITION_BOX = ('<div style="background: rgb(250, 247, 240); border: 1px solid rgb(216, 207, 189); padding: 30px; margin-bottom: 35px;">\n'
                 '  <div style="color: rgb(89, 100, 86); font-family: Georgia, &quot;Times New Roman&quot;, serif; font-size: 21px; '
                 'text-align: center; letter-spacing: 1px; margin-bottom: 15px;">ITEM CONDITION</div>\n'
                 '  <div style="background: rgb(241, 243, 237); border-left: 4px solid rgb(137, 149, 127); padding: 20px 24px;">{condition}</div>\n'
                 '</div>')
PHOTOS_LINE = '<p><strong>Please review all photos carefully, as they are part of the description.</strong></p>'
CONDITION_WORDS = {
    'NEW': 'New', 'NEW_OTHER': 'New (other)', 'NEW_WITH_DEFECTS': 'New with defects', 'USED_EXCELLENT': 'Used - excellent',
    'USED_VERY_GOOD': 'Used - very good', 'USED_GOOD': 'Used - good', 'USED_ACCEPTABLE': 'Used - acceptable',
    'FOR_PARTS_OR_NOT_WORKING': 'For parts or not working',
}
# What the eBay condition codes mean, so the AI does not upgrade "New (other)" to "new in original packaging".
CONDITION_MEANING = {
    'NEW': 'brand new, unused, in the original packaging',
    'NEW_OTHER': 'new and unused, but possibly without the original packaging, open box or a store return; do not claim original packaging',
    'NEW_WITH_DEFECTS': 'new and unused but with cosmetic defects',
    'USED_EXCELLENT': 'used, excellent condition', 'USED_VERY_GOOD': 'used, very good condition', 'USED_GOOD': 'used, good condition',
    'USED_ACCEPTABLE': 'used, acceptable condition with visible wear', 'FOR_PARTS_OR_NOT_WORKING': 'for parts or not working',
}
SHOP_FOOTER = """<div style="color: rgb(77, 74, 67); font-family: Arial, Helvetica, sans-serif; background: rgb(250, 247, 240); border: 1px solid rgb(216, 207, 189);">

  <!-- HEADER -->

  <div style="text-align:center; padding:36px 20px 28px 20px;
  background:#f4efe4; border-bottom:1px solid #d8cfbd;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:32px; letter-spacing:4px; color:#596456;">
      ILONA-CORNER
    </div>

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:15px; font-style:italic; color:#8b806d; margin-top:8px;">
      Beautiful Finds for Your Home &amp; Everyday Life
    </div>

    <div style="width:90px; height:1px; background:#b9a47b;
    margin:20px auto 0 auto;"></div>

  </div>


  <!-- WELCOME -->

  <div style="padding:30px 40px 20px 40px; text-align:center;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:21px; color:#596456; letter-spacing:1px;">
      WELCOME TO OUR SHOP
    </div>

    <p style="max-width:720px; margin:15px auto;">
      Thank you for visiting <strong>ilona-corner</strong>!
      We are a small business offering carefully selected brand-name
      home goods, d&eacute;cor, gifts, and everyday finds at great prices.
    </p>

    <p style="max-width:720px; margin:15px auto;">
      Our merchandise is sourced through established U.S. retail
      overstock, closeout, shelf-pull, surplus, and liquidation channels,
      including merchandise originating from major department stores.
    </p>

  </div>


  <!-- CONDITION & PHOTOS -->

  <div style="margin:15px 40px; background:#ffffff;
  border:1px solid #e3ddd1; padding:23px;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:18px; color:#596456; margin-bottom:10px;">
      CONDITION &amp; PHOTOS
    </div>

    Each item's condition is clearly stated in the individual listing.
    Our inventory may include new retail surplus, shelf-pull,
    store display, sample, and other merchandise.

    <br><br>

    Because some items have been displayed, handled, transported,
    or stored in a retail environment, minor cosmetic signs of handling
    or packaging wear may occasionally be present.

    <br><br>

    Any known noticeable imperfections, damage, or missing components
    are disclosed to the best of our ability. Retail packaging may show
    shelf wear, stickers, adhesive residue, dents, tears, or other
    cosmetic wear.

    <br><br>

    <strong>Please review the complete description and all photos
    carefully before purchasing.</strong> Photos are an important
    part of the item's condition description.

  </div>


  <!-- AUTHENTICITY -->

  <div style="margin:15px 40px; background:#ffffff;
  border:1px solid #e3ddd1; padding:23px;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:18px; color:#596456; margin-bottom:10px;">
      AUTHENTICITY
    </div>

    We stand behind the authenticity of the branded merchandise we sell.
    Our merchandise is sourced through established U.S. retail surplus,
    overstock, closeout, shelf-pull, and liquidation channels.

  </div>


  <!-- SHIPPING -->

  <div style="margin:15px 40px; background:#ffffff;
  border:1px solid #e3ddd1; padding:23px;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:18px; color:#596456; margin-bottom:10px;">
      PACKED WITH CARE
    </div>

    Every purchase is carefully inspected and securely packed
    before shipment.

    <br><br>

    Orders ship from the <strong>USA</strong> to the delivery address
    provided with your eBay order. Tracking information will be uploaded
    once your package ships.

  </div>


  <!-- RETURNS -->

  <div style="margin:15px 40px; background:#ffffff;
  border:1px solid #e3ddd1; padding:23px;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:18px; color:#596456; margin-bottom:10px;">
      RETURNS
    </div>

    We want you to feel confident about your purchase.
    Returns are handled according to the return terms shown in the
    individual listing and applicable eBay policies.

    <br><br>

    Returned merchandise should be returned in the same condition
    received and include all original components, accessories,
    tags, inserts, and packaging included with the order.

    <br><br>

    If your item arrives damaged, defective, or materially different
    from the listing description, please contact us through eBay
    messages so we can help.

  </div>


  <!-- CUSTOMER SERVICE -->

  <div style="margin:15px 40px 35px 40px; background:#ffffff;
  border:1px solid #e3ddd1; padding:23px;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:18px; color:#596456; margin-bottom:10px;">
      WE'RE HERE TO HELP
    </div>

    Questions are always welcome. We are a small business and genuinely
    appreciate every customer and every order.

    <br><br>

    If you have any questions or concerns, please contact us through
    eBay messages and we will be happy to assist.

    <br><br>

    Our shop is closed on weekends. Messages received during the weekend
    will be answered as soon as possible during regular weekday
    business hours.

  </div>


  <!-- FOOTER -->

  <div style="background:#596456; padding:35px 25px;
  text-align:center; color:#ffffff;">

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:20px; letter-spacing:2px;">
      THANK YOU FOR SHOPPING
    </div>

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-size:27px; letter-spacing:3px; margin-top:5px;">
      ILONA-CORNER
    </div>

    <div style="width:80px; height:1px; background:#d8c8a6;
    margin:18px auto;"></div>

    <div style="font-size:14px; color:#eee9df;">
      Carefully Selected &nbsp;&bull;&nbsp;
      Honestly Described &nbsp;&bull;&nbsp;
      Packed With Care
    </div>

    <div style="font-family:Georgia,'Times New Roman',serif;
    font-style:italic; margin-top:15px; color:#f4efe4;">
      We hope you find something you love.
    </div>

  </div>

</div>"""


def _esc_html(value):
    return html_module.escape(_text(value), quote=False)


def _rich(value, limit=None):
    """Escaped text where **words** become <strong>words</strong> (the only markup Claude may use)."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _esc_html(_text(value, limit)))


def parse_description_json(text):
    """The JSON object Claude was asked for; tolerant of code fences and stray prose around it."""
    raw = re.sub(r'^```[^\n]*\n?', '', _text(text)).rstrip('`').strip()
    start, end = raw.find('{'), raw.rfind('}')
    candidate = raw[start:end + 1] if start >= 0 and end > start else raw
    try:
        data = json.loads(candidate)
    except Exception:
        return {'intro': html_to_text(raw)}
    return data if isinstance(data, dict) else {'intro': html_to_text(raw)}


def render_description_item(title, data, *, upc='', condition=''):
    """The two per-item cards of the template (ITEM DESCRIPTION, ITEM CONDITION)."""
    data = data or {}
    intro = _text(data.get('intro'), 1200) or f'**{_text(title, 200)}**'
    note = _text(data.get('conditionNote'), 600)
    details = []
    for entry in data.get('details') or []:
        if isinstance(entry, dict) and _text(entry.get('value')):
            details.append((_text(entry.get('label'), 60), _text(entry.get('value'), 200)))
        elif isinstance(entry, str) and _text(entry):
            label, _, value = entry.partition(':') if ':' in entry else ('', '', entry)
            details.append((_text(label, 60), _text(value, 200)))
    labels = {label.lower() for label, _ in details}
    if is_suffixed(upc) and 'quantity' not in labels:
        details.append(('Quantity', '1'))  # a -suffix is one physical unit
    condition_word = CONDITION_WORDS.get(_text(condition).upper(), '')
    if condition_word and 'condition' not in labels:
        details.append(('Condition', condition_word))
    condition_text = _text(data.get('condition'), 800) or note or condition_word
    lines = [f'<p>{_rich(intro)}</p>']
    if note:
        lines.append(f'<p><strong>IMPORTANT CONDITION NOTE:</strong> {_rich(note)}</p>')
    if details:
        lines.append('<ul>\n' + '\n'.join(f'<li>{_esc_html(label) + ": " if label else ""}{_rich(value)}</li>' for label, value in details) + '\n</ul>')
    lines.append(PHOTOS_LINE)
    parts = [DESCRIPTION_BOX, '  ' + DESCRIPTION_TITLE, '  <div style="text-align: left;">\n' + '\n'.join(lines) + '\n  </div>', '</div>']
    if condition_text:
        parts.append(CONDITION_BOX.format(condition=_rich(condition_text)))
    return '\n\n'.join(parts)


def render_description(title, data, *, upc='', condition=''):
    """Fill the user's template: the item cards, then the shop's fixed blocks, inside the 900px wrapper."""
    return '\n\n'.join([DESCRIPTION_OUTER, render_description_item(title, data, upc=upc, condition=condition), SHOP_FOOTER, '</div>'])


def qr_svg(text):
    """A QR code as an SVG document, or None when the qrcode package is missing."""
    try:
        import qrcode
        import qrcode.image.svg
    except Exception:
        return None
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return image.to_string(encoding='unicode') if hasattr(image, 'to_string') else None


def fill_fields(proposal, eligibility, *, upc, base_url):
    """The values the side panel fills into a store page, derived from one proposal."""
    proposal = proposal or {}
    eligibility = eligibility or {}
    images = []
    for image in proposal.get('images') or []:
        if isinstance(image, dict):
            if not image.get('enabled', True):
                continue
            url = _absolute(image.get('url'), base_url)
        else:
            url = _absolute(image, base_url)
        if url and url not in images:
            images.append(url)
    aspects = {}
    for name, value in (proposal.get('aspects') or {}).items():
        if isinstance(value, (list, tuple)):
            cleaned = [_text(v) for v in value if _text(v)]
            if cleaned:
                aspects[_text(name)] = cleaned
        elif _text(value):
            aspects[_text(name)] = [_text(value)]
    brand = ''
    for name, values in aspects.items():
        if name.lower() == 'brand' and values:
            brand = values[0]
    condition = _text(proposal.get('condition')).upper()
    description_html = _text(proposal.get('listingDescription'))
    quantity = proposal.get('quantity')
    listable = eligibility.get('listable_quantity')
    try:
        quantity = int(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        quantity = None
    if listable not in (None, '') and quantity is not None:
        try:
            quantity = min(quantity, int(listable))
        except (TypeError, ValueError):
            pass
    return {
        # Stores only know the catalog UPC; the -suffix stays on the SKU so the sale traces to that unit.
        'upc': _base_upc(upc),
        'sku': _text(proposal.get('sku')) or upc,
        'title': _text(proposal.get('title'), 80),
        'price': proposal.get('price'),
        'currency': _text(proposal.get('currency')) or 'USD',
        'quantity': quantity,
        'condition': condition,
        'amazonCondition': AMAZON_CONDITIONS.get(condition, ''),
        'conditionDescription': _text(proposal.get('conditionDescription')),
        'descriptionHtml': description_html,
        'descriptionText': html_to_text(description_html),
        'categoryId': _text(proposal.get('categoryId')),
        'categoryPath': _text(proposal.get('categoryPath')),
        'brand': brand,
        'aspects': aspects,
        'images': images[:12],
        'lots': list((eligibility.get('prep') or {}).get('lots') or []),
        'racks': list((eligibility.get('rack') or {}).get('locations') or []),
        'listableQuantity': listable,
    }


def validate_link(data):
    """Normalise and check a confirm-link body. Returns the cleaned dict or raises ListerError."""
    platform = _text(data.get('platform')).lower()
    if platform not in PLATFORMS:
        raise ListerError('platform must be ebay or amazon')
    listing_id = _text(data.get('listing_id') or data.get('listingId'), 64)
    sku = _text(data.get('sku'), 120)
    asin = _text(data.get('asin'), 20).upper()
    if platform == 'ebay':
        if not _EBAY_ITEM_RE.match(listing_id):
            raise ListerError('eBay needs the numeric item number from the live listing')
        asin = ''
    else:
        if asin and not _ASIN_RE.match(asin):
            raise ListerError('ASIN must be 10 letters or digits')
        if not sku and not asin:
            raise ListerError('Amazon needs the seller SKU or the ASIN')
        listing_id = listing_id or asin
    price = data.get('price')
    try:
        price = round(float(price), 2) if price not in (None, '') else None
    except (TypeError, ValueError):
        raise ListerError('price must be a number')
    quantity = data.get('quantity')
    try:
        quantity = int(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        raise ListerError('quantity must be a whole number')
    url = _text(data.get('url'), 500)
    if url and not url.lower().startswith('https://'):
        raise ListerError('url must be an https link')
    return {
        'platform': platform,
        'listing_id': listing_id,
        'offer_id': _text(data.get('offer_id') or data.get('offerId'), 64),
        'sku': sku,
        'asin': asin,
        'store_upc': _text(data.get('store_upc') or data.get('storeUpc'), 40),
        'url': url,
        'title': _text(data.get('title'), 200),
        'price': price,
        'quantity': quantity,
        'note': _text(data.get('note'), 500),
    }


class Lister:
    def __init__(self, deps, static_folder, app=None):
        self.app = app
        self.db = deps['db_connection']
        self.safe_error = deps['_safe_error']
        self.mark_listed = deps['_listagent_mark_listed']
        self.upc_variants = deps['_listagent_upc_variants']
        self.format_upc12 = deps['_listagent_format_upc12']
        self.init_listagent = deps['_listagent_init_tables']
        self.clear_scan_cache = deps.get('_listing_helper_scan_cache_clear')
        # Queue-driven lister (0.2): everything below is optional so older hosts keep the 0.1 routes.
        self.upc_detail_view = deps.get('api_listingagent_upc_detail')
        self.build_proposal = deps.get('_agent_build_proposal')
        self.remove_from_queue = deps.get('_listagent_remove_from_queue')
        self.mark_bol_listed = deps.get('_listing_center_mark_bol_listed')
        self.add_listing_photo = deps.get('_listagent_add_photo')
        self.amazon_catalog_view = deps.get('api_listingagent_amazon_catalog_search')
        self.amazon_restriction_view = deps.get('api_listingagent_amazon_restriction_check')
        self.bump_data_version = deps.get('update_data_version')
        # Telegram (optional): "+ Photo link" sends the phone camera page to the configured chats.
        self.telegram_send = deps.get('_telegram_send_message')
        self.telegram_recipients = deps.get('_telegram_collect_recipient_rows')
        self.telegram_token = deps.get('_telegram_get_bot_token')
        # Whose phone: the chat a Cloudflare login linked on the Telegram page (self-serve pairing).
        self.telegram_chat_for = deps.get('_telegram_chat_for_email')
        self.base_dir = deps.get('BASE_DIR')
        self.static_folder = Path(static_folder)
        self.feed_dir = self.static_folder / 'lister'
        self._jobs = {}
        self._jobs_lock = threading.Lock()
        self._preloads = {}
        self._preload_all = {'running': False, 'upcs': [], 'done': 0, 'startedAt': '', 'finishedAt': ''}

    # -- storage -----------------------------------------------------------------------------

    @staticmethod
    def init_tables(cur):
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_helper_selections (
                proposal_id INTEGER PRIMARY KEY,
                upc TEXT NOT NULL,
                platform_hint TEXT,
                selected_by TEXT,
                selected_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                proposal_id INTEGER,
                platform TEXT NOT NULL,
                listing_id TEXT,
                offer_id TEXT,
                sku TEXT,
                asin TEXT,
                store_upc TEXT,
                url TEXT,
                title TEXT,
                price REAL,
                quantity INTEGER,
                note TEXT,
                effects_json TEXT,
                created_by TEXT,
                source TEXT NOT NULL DEFAULT 'extension',
                kind TEXT NOT NULL DEFAULT 'listed',
                created_at TEXT NOT NULL
            )
        ''')
        # 'listed' = the panel took this listing from start to end on the store; 'existing' = the store
        # already carried the UPC and the panel only linked that live listing ("Use that listing").
        # Only 'listed' rows count as the Lister's own output.
        cur.execute('PRAGMA table_info(listing_links)')
        if 'kind' not in {r[1] for r in cur.fetchall()}:
            cur.execute("ALTER TABLE listing_links ADD COLUMN kind TEXT NOT NULL DEFAULT 'listed'")
            cur.execute("UPDATE listing_links SET kind = 'existing' WHERE note LIKE 'already on the store%'")
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_upc ON listing_links(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_platform_listing ON listing_links(platform, listing_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_platform_sku ON listing_links(platform, sku)')
        # What the user picked on the store's intermediate steps (category, catalog match) versus what
        # the panel suggested: training data for taking those steps over later.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_choices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                base_upc TEXT NOT NULL,
                platform TEXT NOT NULL,
                step TEXT NOT NULL,
                chosen TEXT NOT NULL,
                suggested TEXT,
                url TEXT,
                actor TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_lister_choices_base ON lister_choices(base_upc, platform, step)')
        # Can this UPC be listed on Amazon by us (catalog ASIN + listing restrictions)? Cached per catalog UPC.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_amazon_checks (
                base_upc TEXT PRIMARY KEY,
                asin TEXT,
                title TEXT,
                brand TEXT,
                status TEXT NOT NULL,
                reasons TEXT,
                error TEXT,
                condition_type TEXT,
                checked_at TEXT NOT NULL
            )
        ''')
        cur.execute('PRAGMA table_info(lister_amazon_checks)')
        have = {r[1] for r in cur.fetchall()}
        if 'detail' not in have:
            cur.execute('ALTER TABLE lister_amazon_checks ADD COLUMN detail TEXT')
            # Rows written before the per-condition parse counted collectible_* / refurbished
            # gating as a block, so their verdicts cannot be trusted: re-check on next sight.
            cur.execute("DELETE FROM lister_amazon_checks WHERE status IN ('listable', 'restricted')")
        # AI title / description written for a unit (preload or a click), reused by the fill instead of regenerating.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_generated (
                upc TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                html TEXT,
                auto INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (upc, kind)
            )
        ''')
        # "+ Photo link": the message the bot sent, so opening the link can delete it again.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_photo_links (
                token TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id INTEGER,
                upc TEXT NOT NULL,
                created_at TEXT NOT NULL,
                opened_at TEXT,
                PRIMARY KEY (token, chat_id)
            )
        ''')
        # Per-store "X" on the side panel: the item leaves that store's list but stays queued for the other.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_queue_state (
                upc TEXT NOT NULL,
                platform TEXT NOT NULL,
                state TEXT NOT NULL,
                actor TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (upc, platform)
            )
        ''')
        # Which store list the side panel has open, per signed-in person, so Items to List can
        # make its "+" add to that store only. A heartbeat: an old row means the panel is closed.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_panel_state (
                email TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                follow INTEGER NOT NULL DEFAULT 1,
                open INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            )
        ''')

    @staticmethod
    def _has_table(cur, name):
        cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
        return cur.fetchone() is not None

    def _variants(self, upc):
        variants = [v for v in (self.upc_variants(upc) or []) if v]
        return variants or [upc]

    # -- proposals -------------------------------------------------------------------------

    def items(self, *, scope='selected', platform='', base_url='http://localhost/'):
        scope = (scope or 'selected').strip().lower()
        if scope not in ('selected', 'open', 'all'):
            raise ListerError('scope must be selected, open or all')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT * FROM listing_helper_selections')
            selections = {int(r['proposal_id']): _row(r) for r in cur.fetchall()}
            rows = []
            if self._has_table(cur, 'listing_proposals'):
                if scope == 'selected':
                    if selections:
                        placeholders = ','.join('?' for _ in selections)
                        cur.execute(f'SELECT * FROM listing_proposals WHERE id IN ({placeholders}) ORDER BY updated_at DESC, id DESC',
                                    tuple(selections))
                        rows = cur.fetchall()
                elif scope == 'open':
                    placeholders = ','.join('?' for _ in OPEN_STATUSES)
                    cur.execute(f'SELECT * FROM listing_proposals WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 300',
                                OPEN_STATUSES)
                    rows = cur.fetchall()
                else:
                    cur.execute('SELECT * FROM listing_proposals ORDER BY updated_at DESC, id DESC LIMIT 300')
                    rows = cur.fetchall()
            proposals = [_row(r) for r in rows]
            upcs = [p['upc'] for p in proposals]
            links = self._links_for_upcs(cur, upcs)
        items = []
        # Store lookups cost two database opens per item; keep them for the short selected list.
        with_stores = scope == 'selected' or len(proposals) <= 60
        for proposal in proposals:
            item = self._item(proposal, selections.get(int(proposal['id'])), links.get(proposal['upc'], []), base_url,
                              with_stores=with_stores)
            if platform and platform in PLATFORMS and any(l['platform'] == platform for l in item['links']):
                item['listedOnPlatform'] = True
            items.append(item)
        # Selected items first (most recently selected on top), then the rest by update time.
        items.sort(key=lambda it: (0 if it['selected'] else 1, it['selectedAt'] and -_order_key(it['selectedAt']) or 0,
                                   -_order_key(it['updatedAt'])))
        counts = {'selected': len(selections), 'returned': len(items)}
        return items, counts

    def _links_for_upcs(self, cur, upcs):
        out = {}
        if not upcs:
            return out
        placeholders = ','.join('?' for _ in upcs)
        cur.execute(f'SELECT * FROM listing_links WHERE upc IN ({placeholders}) ORDER BY id DESC', tuple(upcs))
        for row in cur.fetchall():
            link = self._link_public(_row(row))
            out.setdefault(link['upc'], []).append(link)
        return out

    def _item(self, proposal, selection, links, base_url, *, with_stores=True):
        data = _loads(proposal.get('proposal_json'), {})
        eligibility = _loads(proposal.get('eligibility_json'), {})
        flags = _loads(proposal.get('flags_json'), [])
        upc = proposal['upc']
        fields = fill_fields(data, eligibility, upc=upc, base_url=base_url)
        stores = self._store_listings(upc) if with_stores else {'ebay': [], 'amazon': []}
        return {
            'id': int(proposal['id']),
            'upc': upc,
            'status': proposal.get('status'),
            'selected': selection is not None,
            'selectedAt': (selection or {}).get('selected_at') or '',
            'selectedBy': (selection or {}).get('selected_by') or '',
            'platformHint': (selection or {}).get('platform_hint') or '',
            'updatedAt': proposal.get('updated_at') or '',
            'reviewerNote': proposal.get('reviewer_note') or '',
            'flags': [f for f in flags if isinstance(f, dict)],
            'fields': fields,
            'thumb': fields['images'][0] if fields['images'] else '',
            'existing': stores,
            'links': links,
            'listedListingId': proposal.get('listed_listing_id') or '',
        }

    def _store_listings(self, upc):
        """Listings the stores already carry for this UPC, so the panel can warn before a duplicate."""
        variants = self._variants(upc)
        base = variants[0].split('-', 1)[0]
        keys = {v for v in variants} | {base, base.lstrip('0')}
        keys = tuple(k for k in keys if k)
        placeholders = ','.join('?' for _ in keys)
        out = {'ebay': [], 'amazon': []}
        try:
            with self.db('ebayStore.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'INVENTORY'):
                    cur.execute(f"SELECT ItemID, SKU, UPC, Title, List_State FROM INVENTORY WHERE TRIM(COALESCE(UPC, '')) IN ({placeholders}) ORDER BY ID DESC LIMIT 10", keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        out['ebay'].append({'listingId': _text(r.get('ItemID')), 'sku': _text(r.get('SKU')),
                                            'upc': _text(r.get('UPC')), 'title': _text(r.get('Title')),
                                            'state': _text(r.get('List_State'))})
        except sqlite3.Error:
            pass
        try:
            with self.db('amazonStore.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'ITEMS'):
                    cur.execute(f"SELECT ASIN, SKU, UPC, TITLE, STATUS, FULFILLMENT_CHANNEL FROM ITEMS WHERE TRIM(COALESCE(UPC, '')) IN ({placeholders}) ORDER BY rowid DESC LIMIT 10", keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        out['amazon'].append({'asin': _text(r.get('ASIN')), 'sku': _text(r.get('SKU')),
                                              'upc': _text(r.get('UPC')), 'title': _text(r.get('TITLE')),
                                              'state': _text(r.get('STATUS')),
                                              'fulfillment': _text(r.get('FULFILLMENT_CHANNEL'))})
        except sqlite3.Error:
            pass
        return out

    def select(self, proposal_ids, *, selected=True, actor='', platform_hint=''):
        ids = []
        for value in proposal_ids or []:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                raise ListerError('proposal_ids must be integers')
        if not ids:
            raise ListerError('proposal_ids is required')
        platform_hint = platform_hint if platform_hint in PLATFORMS else ''
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            if not selected:
                cur.execute(f"DELETE FROM listing_helper_selections WHERE proposal_id IN ({','.join('?' for _ in ids)})", tuple(ids))
            else:
                if not self._has_table(cur, 'listing_proposals'):
                    raise ListerError('No listing proposals exist yet', 404)
                cur.execute(f"SELECT id, upc, status FROM listing_proposals WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids))
                found = {int(r['id']): _row(r) for r in cur.fetchall()}
                missing = [i for i in ids if i not in found]
                if missing:
                    raise ListerError(f'proposal not found: {missing[0]}', 404)
                now = _now()
                for pid in ids:
                    cur.execute('''
                        INSERT INTO listing_helper_selections (proposal_id, upc, platform_hint, selected_by, selected_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(proposal_id) DO UPDATE SET platform_hint = excluded.platform_hint,
                            selected_by = excluded.selected_by, selected_at = excluded.selected_at
                    ''', (pid, found[pid]['upc'], platform_hint, actor, now))
                    self._event(cur, pid, found[pid]['upc'], 'helper_selected', actor, f'sent to browser lister{" for " + platform_hint if platform_hint else ""}')
            conn.commit()
            cur.execute('SELECT COUNT(*) AS n FROM listing_helper_selections')
            total = int(cur.fetchone()['n'])
        return {'selected': len(ids) if selected else 0, 'total_selected': total}

    def _event(self, cur, proposal_id, upc, event, actor, note='', payload=None):
        if not self._has_table(cur, 'listing_proposal_events'):
            return
        cur.execute('''
            INSERT INTO listing_proposal_events (proposal_id, upc, event, actor, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (int(proposal_id), upc, event, actor or 'lister', note or '',
              json.dumps(payload, default=str) if payload is not None else None, _now()))

    # -- links -------------------------------------------------------------------------------

    def _link_public(self, row):
        if row is None:
            return None
        row = dict(row)
        row['effects'] = _loads(row.pop('effects_json', None), {})
        return row

    def record_link(self, data, *, actor='', base_url='http://localhost/'):
        clean = validate_link(data)
        kind = 'existing' if _text(data.get('kind')) == 'existing' else 'listed'
        proposal_id = data.get('proposal_id') or data.get('proposalId')
        upc = _text(data.get('upc'))
        effects = {'steps': []}
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            proposal = None
            if proposal_id not in (None, ''):
                try:
                    proposal_id = int(proposal_id)
                except (TypeError, ValueError):
                    raise ListerError('proposal_id must be an integer')
                if self._has_table(cur, 'listing_proposals'):
                    cur.execute('SELECT * FROM listing_proposals WHERE id = ?', (proposal_id,))
                    proposal = _row(cur.fetchone())
                if not proposal:
                    raise ListerError('proposal not found', 404)
                upc = upc or proposal['upc']
            upc = self.format_upc12(upc)
            if not upc:
                raise ListerError('upc is required')
            if not clean['title'] and proposal:
                clean['title'] = _text(_loads(proposal.get('proposal_json'), {}).get('title'), 200)
            if not clean['url']:
                if clean['platform'] == 'ebay':
                    clean['url'] = f"https://www.ebay.com/itm/{clean['listing_id']}"
                elif clean['asin']:
                    clean['url'] = f"https://www.amazon.com/dp/{clean['asin']}"

            # Duplicate guard: the same live listing should not be linked twice.
            key_col, key_val = ('listing_id', clean['listing_id']) if clean['platform'] == 'ebay' else ('sku', clean['sku'] or None)
            if key_val:
                cur.execute(f'SELECT * FROM listing_links WHERE platform = ? AND {key_col} = ? ORDER BY id DESC LIMIT 1', (clean['platform'], key_val))
                existing = _row(cur.fetchone())
                if existing:
                    if existing['upc'] != upc:
                        raise ListerError(f"That {clean['platform']} listing is already linked to UPC {existing['upc']}", 409)
                    return {'link': self._link_public(existing), 'duplicate': True}

            # 1. Queue: this is what Ready to Ship reads to map a store SKU / item number / ASIN to our UPC.
            cur.execute("SELECT id FROM listing_queue WHERE upc IN (%s) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1"
                        % ','.join('?' for _ in self._variants(upc)), tuple(self._variants(upc)))
            if cur.fetchone() is None:
                cur.execute('''INSERT INTO listing_queue (upc, title, source, added_mode, status, added_at)
                               VALUES (?, ?, 'lister', 'my', 'queued', ?)''', (upc, clean['title'], _now()))
                effects['steps'].append('queue row created')
            conn.commit()
        try:
            queue_item = self.mark_listed(
                upc, platform=clean['platform'], listing_id=clean['listing_id'] or None,
                offer_id=clean['offer_id'] or None, sku=clean['sku'] or None, asin=clean['asin'] or None,
                url=clean['url'] or None, title=clean['title'] or None, price=clean['price'],
                quantity=clean['quantity'], source='lister')
            effects['queue'] = {'id': (queue_item or {}).get('id'), 'status': (queue_item or {}).get('status')}
            effects['steps'].append(f"listing queue marked listed on {clean['platform']}")
        except Exception as e:  # the queue is best effort; the link row below is the durable record
            effects['queue_error'] = str(e)[:300]
        # /items-to-list Marketplaces box for this UPC (source shows as "Listing Agent").
        if self.mark_bol_listed:
            try:
                marked = self.mark_bol_listed(clean['platform'], upc=upc)
                if isinstance(marked, dict) and marked.get('success') is False:
                    effects['bol_error'] = _text(marked.get('error'), 200)
                else:
                    effects['steps'].append(f"Items to List marked listed on {clean['platform']}")
            except Exception as e:
                effects['bol_error'] = str(e)[:300]
        try:
            finalized = self.finalize_queue(upc)
            if finalized:
                effects['steps'].append(f"queue item {finalized}")
        except Exception as e:
            effects['queue_error'] = (effects.get('queue_error') or '') + ' finalize: ' + str(e)[:200]

        # 2. Warehouse rows for this UPC (suffixed units included) -> store-listing fallback + Finder alias.
        rack_rows = self.rack_rows(upc)
        if rack_rows:
            match = self._write_inventory_match(clean, upc, rack_rows[0])
            effects['inventory_match'] = match
            effects['steps'].append(f"warehouse match -> {match.get('inventory_location') or 'rack'} ({match.get('inventory_barcode')})")
        else:
            effects['steps'].append('no warehouse rows with stock for this UPC; link kept on the queue only')
        effects['rack_ids'] = [r['id'] for r in rack_rows]
        if clean['store_upc'] and self.format_upc12(clean['store_upc']) != upc:
            effects['steps'].append(f"store shows UPC {clean['store_upc']}; kept our UPC {upc} as the warehouse key")

        # 3. Durable link row + proposal bookkeeping.
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            if proposal:
                effects['proposal_previous_status'] = proposal.get('status')
                if proposal.get('status') not in ('listed', 'rejected', 'superseded'):
                    cur.execute('''UPDATE listing_proposals SET status = 'listed', listed_listing_id = ?, listed_offer_id = ?,
                                   reviewed_by = ?, reviewed_at = ?, updated_at = ?, error = NULL WHERE id = ?''',
                                (clean['listing_id'] or clean['asin'] or clean['sku'], clean['offer_id'] or None,
                                 actor or 'lister', _now(), _now(), proposal['id']))
                    effects['steps'].append('proposal marked listed')
                self._event(cur, proposal['id'], upc, 'published_via_browser', actor,
                            f"{clean['platform']} {clean['listing_id'] or clean['sku'] or clean['asin']}", clean)
                cur.execute('DELETE FROM listing_helper_selections WHERE proposal_id = ?', (proposal['id'],))
            cur.execute('''
                INSERT INTO listing_links (upc, proposal_id, platform, listing_id, offer_id, sku, asin, store_upc, url,
                                           title, price, quantity, note, effects_json, created_by, source, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'extension', ?, ?)
            ''', (upc, proposal['id'] if proposal else None, clean['platform'], clean['listing_id'] or None,
                  clean['offer_id'] or None, clean['sku'] or None, clean['asin'] or None, clean['store_upc'] or None,
                  clean['url'] or None, clean['title'] or None, clean['price'], clean['quantity'], clean['note'] or None,
                  json.dumps(effects, default=str), actor or None, kind, _now()))
            link_id = cur.lastrowid
            conn.commit()
            cur.execute('SELECT * FROM listing_links WHERE id = ?', (link_id,))
            link = self._link_public(_row(cur.fetchone()))
        if self.clear_scan_cache:
            try:
                self.clear_scan_cache()
            except Exception:
                pass
        return {'link': link, 'duplicate': False}

    def rack_rows(self, upc):
        variants = self._variants(upc)
        base = variants[0].split('-', 1)[0]
        rows = []
        try:
            with self.db('searchRack.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'SEARCHRACK'):
                    return rows
                placeholders = ','.join('?' for _ in variants)
                cur.execute(f'''
                    SELECT ID, BARCODE, TITLE, ITEM_POSITION, QUANTITY, IMAGE
                    FROM SEARCHRACK
                    WHERE COALESCE(QUANTITY, 0) > 0 AND (TRIM(COALESCE(BARCODE, '')) IN ({placeholders}) OR BARCODE LIKE ?)
                    ORDER BY CASE WHEN BARCODE LIKE '%-%' THEN 1 ELSE 0 END, ID ASC
                ''', (*variants, f'{base}-%'))
                for r in cur.fetchall():
                    r = _row(r)
                    rows.append({'id': int(r['ID']), 'barcode': _text(r.get('BARCODE')), 'title': _text(r.get('TITLE')),
                                 'location': _text(r.get('ITEM_POSITION')), 'quantity': r.get('QUANTITY'),
                                 'image': _text(r.get('IMAGE'))})
        except sqlite3.Error:
            return rows
        return rows

    def _write_inventory_match(self, clean, upc, rack):
        """Same rows the Store Listing Helper's manual 'Match inventory' writes, so Ready to Ship's
        store-listing fallback and the Finder learn the link."""
        store = clean['platform']
        listing_key = clean['listing_id'] if store == 'ebay' else (clean['sku'] or clean['asin'])
        listing_id = f"{store}:{listing_key}"
        from finder_aliases import update_alias
        with self.db('listing_alerts.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS listing_inventory_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store TEXT NOT NULL,
                    listing_key TEXT NOT NULL COLLATE NOCASE,
                    listing_id TEXT,
                    marketplace_barcode TEXT,
                    listing_title TEXT,
                    searchrack_id INTEGER NOT NULL,
                    inventory_barcode TEXT,
                    inventory_location TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(store, listing_key)
                )
            ''')
            cur.execute('''
                INSERT INTO listing_inventory_matches (store, listing_key, listing_id, marketplace_barcode, listing_title,
                                                       searchrack_id, inventory_barcode, inventory_location, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(store, listing_key) DO UPDATE SET
                    listing_id = excluded.listing_id, marketplace_barcode = excluded.marketplace_barcode,
                    listing_title = excluded.listing_title, searchrack_id = excluded.searchrack_id,
                    inventory_barcode = excluded.inventory_barcode, inventory_location = excluded.inventory_location,
                    updated_at = CURRENT_TIMESTAMP
            ''', (store, listing_key, listing_id, clean['store_upc'] or upc, clean['title'], rack['id'],
                  rack['barcode'], rack['location']))
            alias_undo = None
            try:
                alias_undo = update_alias(conn, f'{store}:{listing_key.lower()}', rack['barcode'], clean['title'] or '')
            except Exception:
                pass
            conn.commit()
        return {'store': store, 'listing_key': listing_key, 'searchrack_id': rack['id'],
                'inventory_barcode': rack['barcode'], 'inventory_location': rack['location'],
                'finder_alias_undo': alias_undo}

    def links(self, *, upc='', platform='', listing_id='', sku='', asin='', kind='', limit=100):
        clauses, params = [], []
        if kind in ('listed', 'existing'):
            clauses.append("COALESCE(NULLIF(TRIM(kind), ''), 'listed') = ?")
            params.append(kind)
        if upc:
            variants = self._variants(self.format_upc12(upc) or upc)
            clauses.append(f"upc IN ({','.join('?' for _ in variants)})")
            params.extend(variants)
        if platform in PLATFORMS:
            clauses.append('platform = ?')
            params.append(platform)
        if listing_id:
            clauses.append('listing_id = ?')
            params.append(_text(listing_id))
        if sku:
            clauses.append('sku = ? COLLATE NOCASE')
            params.append(_text(sku))
        if asin:
            clauses.append('asin = ? COLLATE NOCASE')
            params.append(_text(asin).upper())
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute(f'SELECT * FROM listing_links {where} ORDER BY id DESC LIMIT ?', (*params, max(1, min(int(limit or 100), 500))))
            return [self._link_public(_row(r)) for r in cur.fetchall()]

    def delete_link(self, link_id, *, actor=''):
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT * FROM listing_links WHERE id = ?', (int(link_id),))
            link = self._link_public(_row(cur.fetchone()))
            if not link:
                raise ListerError('link not found', 404)
            effects = link.get('effects') or {}
            if link.get('proposal_id') and self._has_table(cur, 'listing_proposals'):
                previous = effects.get('proposal_previous_status')
                if previous and previous not in ('listed', 'rejected', 'superseded'):
                    cur.execute('''UPDATE listing_proposals SET status = ?, listed_listing_id = NULL, listed_offer_id = NULL,
                                   updated_at = ? WHERE id = ? AND status = 'listed' ''', (previous, _now(), link['proposal_id']))
                self._event(cur, link['proposal_id'], link['upc'], 'browser_link_removed', actor,
                            f"{link['platform']} {link.get('listing_id') or link.get('sku') or ''}")
            cur.execute('DELETE FROM listing_links WHERE id = ?', (int(link_id),))
            # Undo what record_link did to the queue: this store is no longer listed, the row is queued again.
            self.init_listagent(cur)
            variants = self._variants(link['upc'])
            platform = link['platform']
            cur.execute(f"SELECT * FROM listing_queue WHERE upc IN ({','.join('?' for _ in variants)}) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1", tuple(variants))
            queue_row = _row(cur.fetchone())
            if queue_row and queue_row.get(f'listed_{platform}_at'):
                same = any(_text(queue_row.get(col)) and _text(queue_row.get(col)) == _text(link.get(key))
                           for col, key in (('listed_listing_id', 'listing_id'), ('listed_sku', 'sku'), ('listed_asin', 'asin')))
                if same or not (queue_row.get('listed_listing_id') or queue_row.get('listed_sku') or queue_row.get('listed_asin')):
                    cur.execute(f'''UPDATE listing_queue SET listed_{platform}_at = NULL, status = 'queued',
                                    listed_listing_id = NULL, listed_offer_id = NULL, listed_sku = NULL, listed_asin = NULL, listed_url = NULL,
                                    listed_platform = NULL, listed_at = NULL WHERE id = ?''', (queue_row['id'],))
                    link['queue_reverted'] = True
            conn.commit()
        if link.get('queue_reverted'):
            # The listing log row this link wrote would otherwise still trace sales to this unit.
            try:
                with self.db('listinglog.db') as conn:
                    cur = conn.cursor()
                    if self._has_table(cur, 'listing_log'):
                        cur.execute('''DELETE FROM listing_log WHERE LOWER(COALESCE(source, '')) = 'lister' AND platform = ? AND upc = ?
                                       AND (COALESCE(listing_id, '') = ? OR COALESCE(sku, '') = ? OR COALESCE(asin, '') = ?)''',
                                    (link['platform'], link['upc'], _text(link.get('listing_id')), _text(link.get('sku')) or '-', _text(link.get('asin')) or '-'))
                        conn.commit()
            except sqlite3.Error:
                pass
            if any('Items to List marked listed' in step for step in (effects.get('steps') or [])):
                try:
                    with self.db('bol.db') as conn:
                        cur = conn.cursor()
                        if self._has_table(cur, 'bol_items'):
                            keys = tuple({link['upc'], link['upc'].lstrip('0') or link['upc'], *self._variants(link['upc'])})
                            cur.execute(f'''UPDATE bol_items SET listed_{platform} = 0, listed_{platform}_date = NULL, listed_{platform}_source = NULL
                                            WHERE TRIM(COALESCE(upc, '')) IN ({','.join('?' for _ in keys)}) AND listed_{platform}_source = 'listing_center' ''', keys)
                            conn.commit()
                            link['items_to_list_reverted'] = bool(cur.rowcount)
                except sqlite3.Error:
                    pass
        match = effects.get('inventory_match') or {}
        if match.get('store') and match.get('listing_key'):
            try:
                with self.db('listing_alerts.db') as conn:
                    cur = conn.cursor()
                    if self._has_table(cur, 'listing_inventory_matches'):
                        cur.execute('DELETE FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?) AND searchrack_id = ?',
                                    (match['store'], match['listing_key'], match.get('searchrack_id')))
                        conn.commit()
            except sqlite3.Error:
                pass
        if self.clear_scan_cache:
            try:
                self.clear_scan_cache()
            except Exception:
                pass
        return link

    def resolve(self, *, platform='', sku='', listing_id='', asin='', upc=''):
        """Which of our UPCs (and rack rows) does a store identifier point at?"""
        if not any([sku, listing_id, asin, upc]):
            raise ListerError('give a sku, listing_id, asin or upc')
        links = self.links(platform=platform, sku=sku, listing_id=listing_id, asin=asin, upc=upc, limit=5)
        resolved_upc = links[0]['upc'] if links else ''
        if not resolved_upc and (sku or listing_id or asin):
            with self.db('listagent.db') as conn:
                cur = conn.cursor()
                self.init_listagent(cur)
                clauses, params = [], []
                if sku:
                    clauses.append("TRIM(COALESCE(listed_sku, '')) = ? COLLATE NOCASE")
                    params.append(_text(sku))
                if listing_id:
                    clauses.append("(TRIM(COALESCE(listed_listing_id, '')) = ? OR TRIM(COALESCE(listed_offer_id, '')) = ?)")
                    params.extend([_text(listing_id), _text(listing_id)])
                if asin:
                    clauses.append("TRIM(COALESCE(listed_asin, '')) = ? COLLATE NOCASE")
                    params.append(_text(asin))
                cur.execute(f"SELECT upc FROM listing_queue WHERE {' OR '.join(clauses)} ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", tuple(params))
                row = cur.fetchone()
                resolved_upc = row['upc'] if row else ''
        if not resolved_upc and upc:
            resolved_upc = self.format_upc12(upc)
        return {'upc': resolved_upc, 'links': links, 'rack': self.rack_rows(resolved_upc) if resolved_upc else []}

    # -- queue-driven lister (0.2) --------------------------------------------------------------

    def _queue_rows(self, cur):
        """Active Listing Agent queue rows, oldest first (the first one is what the panel works on)."""
        self.init_listagent(cur)
        self.init_tables(cur)
        cur.execute('''
            SELECT * FROM listing_queue WHERE status IN ('queued', 'done')
            ORDER BY added_at ASC, id ASC LIMIT 400
        ''')
        rows = [_row(r) for r in cur.fetchall()]
        cur.execute("SELECT upc, platform, created_at FROM lister_queue_state WHERE state = 'skipped'")
        skips = {(r['upc'], r['platform']): r['created_at'] for r in cur.fetchall()}
        return rows, skips

    def _skip_key(self, upc, platform):
        return (self.format_upc12(upc) or _text(upc), platform)

    def _thumbs(self, upcs):
        """Catalog thumbnail per UPC from the BOL tables (one query per database, not per item)."""
        out = {}
        bases = {}
        for upc in upcs:
            base = _base_upc(upc)
            for key in {base, base.lstrip('0') or base, base.zfill(12) if base.isdigit() else base}:
                if key:
                    bases.setdefault(key, set()).add(upc)
        if not bases:
            return out
        keys = tuple(bases)
        placeholders = ','.join('?' for _ in keys)
        for db_name, query in (
            ('rawbol.db', f"SELECT upc, image_url FROM raw_bol_items WHERE TRIM(COALESCE(upc, '')) IN ({placeholders}) AND COALESCE(image_url, '') != '' ORDER BY rowid DESC"),
            ('bol.db', f"SELECT upc, image_url FROM bol_items WHERE TRIM(COALESCE(upc, '')) IN ({placeholders}) AND COALESCE(image_url, '') != '' ORDER BY id DESC"),
        ):
            try:
                with self.db(db_name) as conn:
                    cur = conn.cursor()
                    cur.execute(query, keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        image = _text(r.get('image_url'))
                        if image.lower() in ('nan', 'none', 'null'):
                            continue
                        image = secure_image_url(image)
                        for upc in bases.get(_text(r.get('upc')), ()):
                            out.setdefault(upc, image)
            except sqlite3.Error:
                continue
        return out

    def _defects(self, upcs):
        """The BOL's defect note per catalog UPC (raw_bol_items.prep_reason), e.g. "Missing pieces"."""
        out = {}
        bases = {}
        for upc in upcs:
            base = _base_upc(upc)
            for key in {base, base.lstrip('0') or base, base.zfill(12) if base.isdigit() else base}:
                if key:
                    bases.setdefault(key, set()).add(upc)
        if not bases:
            return out
        keys = tuple(bases)
        try:
            with self.db('rawbol.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'raw_bol_items'):
                    return out
                cur.execute(f"SELECT upc, prep_reason FROM raw_bol_items WHERE TRIM(COALESCE(upc, '')) IN ({','.join('?' for _ in keys)}) AND TRIM(COALESCE(prep_reason, '')) != '' ORDER BY rowid DESC", keys)
                for row in cur.fetchall():
                    row = _row(row)
                    reason = _text(row.get('prep_reason'), 120)
                    if reason.lower() in ('nan', 'none', 'null'):
                        continue
                    for upc in bases.get(_text(row.get('upc')), ()):
                        out.setdefault(upc, reason)
        except sqlite3.Error:
            return out
        return out

    def _latest_proposals(self, cur, upcs):
        """Newest non-superseded proposal per UPC: id, status and whether it carries listing fields."""
        out = {}
        if not upcs or not self._has_table(cur, 'listing_proposals'):
            return out
        placeholders = ','.join('?' for _ in upcs)
        cur.execute(f'''SELECT id, upc, status, proposal_json, flags_json, eligibility_json, updated_at FROM listing_proposals
                        WHERE upc IN ({placeholders}) AND status != 'superseded' ORDER BY id DESC''', tuple(upcs))
        for r in cur.fetchall():
            r = _row(r)
            if r['upc'] in out:
                continue
            data = _loads(r.get('proposal_json'), {})
            out[r['upc']] = {'id': int(r['id']), 'status': r.get('status'), 'ready': bool(_text(data.get('title'))),
                             'updatedAt': r.get('updated_at') or '', 'proposal': data,
                             'flags': [f for f in _loads(r.get('flags_json'), []) if isinstance(f, dict)],
                             'eligibility': _loads(r.get('eligibility_json'), {})}
        return out

    def _job_state(self, upc):
        with self._jobs_lock:
            job = self._jobs.get(upc)
        if not job:
            return {}
        if job.get('running') and time.time() - job.get('started', 0) > PREPARE_STALE_SECONDS:
            job = {**job, 'running': False, 'error': 'timed out'}
        return {'running': bool(job.get('running')), 'error': job.get('error') or '', 'startedAt': job.get('started_iso') or ''}

    @staticmethod
    def store_url(platform, entry):
        if platform == 'ebay' and entry.get('listingId'):
            return f"https://www.ebay.com/itm/{entry['listingId']}"
        if platform == 'amazon' and entry.get('asin'):
            return f"https://www.amazon.com/dp/{entry['asin']}"
        return ''

    def queue(self, *, platform, base_url='http://localhost/'):
        """The side panel's list for one store: queued items (oldest first) then the ones listed there."""
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            rows, skips = self._queue_rows(cur)
            upcs = [self.format_upc12(r['upc']) or _text(r['upc']) for r in rows]
            links = self._links_for_upcs(cur, list({*upcs, *[r['upc'] for r in rows]}))
            proposals = self._latest_proposals(cur, upcs)
            amazon_checks = self._amazon_checks(cur, upcs)
        thumbs = self._thumbs(upcs)
        prep_statuses = self._prep_statuses(upcs)
        note_counts = self._note_counts(upcs)
        defects = self._defects(upcs)
        active, done, hidden = [], [], 0
        other = 'amazon' if platform == 'ebay' else 'ebay'
        for row, upc in zip(rows, upcs):
            listed_at = row.get(f'listed_{platform}_at') or ''
            skipped_at = skips.get((upc, platform)) or skips.get((row['upc'], platform)) or ''
            if skipped_at and not listed_at:
                hidden += 1
                continue
            stores = self._store_listings(upc)
            existing = stores.get(platform) or []
            live = [e for e in existing if not e.get('state') or e['state'].lower() in ('active', 'live')]
            own_links = [l for l in links.get(upc, []) + links.get(row['upc'], []) if l['platform'] == platform]
            proposal = proposals.get(upc) or {}
            item = {
                'id': int(row['id']),
                'upc': upc,
                'baseUpc': _base_upc(upc),
                'suffixed': is_suffixed(upc),
                'title': _text(row.get('title'), 200),
                'thumb': _thumb_url(thumbs.get(upc, ''), base_url),
                'addedAt': row.get('added_at') or '',
                'source': row.get('source') or '',
                'platform': platform,
                'status': 'listed' if listed_at else 'queued',
                'listedAt': listed_at,
                'listed': {'ebay': bool(row.get('listed_ebay_at')), 'amazon': bool(row.get('listed_amazon_at'))},
                'skipped': {'ebay': bool(skips.get((upc, 'ebay'))), 'amazon': bool(skips.get((upc, 'amazon')))},
                'otherStatus': 'listed' if row.get(f'listed_{other}_at') else ('skipped' if skips.get((upc, other)) else 'queued'),
                'existing': existing,
                'storeUrl': (self.store_url(platform, live[0]) if live else '') or (own_links[0].get('url') if own_links else ''),
                'alreadyOnStore': bool(live) and not listed_at,
                'links': own_links,
                'proposal': {k: proposal[k] for k in ('id', 'status', 'ready', 'updatedAt') if k in proposal},
                'preparing': self._job_state(upc).get('running', False),
                'checkRequest': bool(row.get('has_check_request')),
                'amazonCheck': amazon_checks.get(_base_upc(upc)),
                'prepStatus': prep_statuses.get(upc),
                'notes': note_counts.get(upc),
                'preload': self.preload_status(upc),
                'defect': defects.get(upc, ''),
            }
            (done if listed_at else active).append(item)
        done.sort(key=lambda it: -_order_key(it['listedAt']))
        return {'items': active + done, 'counts': {'queued': len(active), 'listed': len(done), 'hidden': hidden}}

    def _upc_detail(self, upc, base_url):
        """The Listing Agent's aggregate view of one UPC (inventory, BOL, stores, prep notes/photos/voice)."""
        if not self.upc_detail_view or not self.app:
            return {}
        path = f'/api/listingagent/upc/{upc}'
        with self.app.test_request_context(path, base_url=base_url):
            rv = self.upc_detail_view(upc)
        if isinstance(rv, tuple):
            rv = rv[0]
        data = rv.get_json(silent=True) if hasattr(rv, 'get_json') else (rv or {})
        return (data or {}).get('item') or {} if (data or {}).get('success') else {}

    def _prep_statuses(self, upcs):
        """Item Prep's verdict per unit for the queue rows (one query): upc -> {'status', 'reason'}."""
        keys = tuple({v for u in upcs for v in self._variants(u)})
        if not keys:
            return {}
        out = {}
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'items_prep_status'):
                    return out
                cur.execute(f"SELECT upc, status, reason, updated_at FROM items_prep_status WHERE upc IN ({','.join('?' for _ in keys)}) ORDER BY COALESCE(updated_at, '') ASC, id ASC", keys)
                by_key = {}
                for r in cur.fetchall():
                    r = _row(r)
                    by_key[_text(r.get('upc'))] = {'status': _text(r.get('status')).lower(), 'reason': _text(r.get('reason'), 120)}
        except sqlite3.Error:
            return out
        for upc in upcs:
            for v in self._variants(upc):
                if v in by_key:
                    out[upc] = by_key[v]
                    break
        return out

    def _note_counts(self, upcs):
        """How many written notes and voice notes Item Prep holds per unit (for the queue rows)."""
        keys = tuple({v for u in upcs for v in self._variants(u)})
        out = {}
        if not keys:
            return out
        counts = {}
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                placeholders = ','.join('?' for _ in keys)
                if self._has_table(cur, 'items_prep_notes'):
                    cur.execute(f"SELECT upc, COUNT(*) AS n FROM items_prep_notes WHERE upc IN ({placeholders}) GROUP BY upc", keys)
                    for r in cur.fetchall():
                        counts.setdefault(_text(r['upc']), {})['written'] = int(r['n'] or 0)
                if self._has_table(cur, 'items_prep_media'):
                    cur.execute(f"SELECT upc, COUNT(*) AS n FROM items_prep_media WHERE upc IN ({placeholders}) AND COALESCE(media_type, '') = 'audio' GROUP BY upc", keys)
                    for r in cur.fetchall():
                        counts.setdefault(_text(r['upc']), {})['voice'] = int(r['n'] or 0)
        except sqlite3.Error:
            return out
        for upc in upcs:
            total = {'written': 0, 'voice': 0}
            for v in self._variants(upc):
                for k in total:
                    total[k] += counts.get(v, {}).get(k, 0)
            if total['written'] or total['voice']:
                out[upc] = total
        return out

    def _prep_status(self, upc):
        """Item Prep's verdict for this unit: {'status': 'good'|'bad'|..., 'reason', 'updatedAt'} or {}."""
        variants = list(self._variants(upc))
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'items_prep_status'):
                    return {}
                cur.execute(f"SELECT status, reason, updated_at, quantity FROM items_prep_status WHERE upc IN ({','.join('?' for _ in variants)}) ORDER BY COALESCE(updated_at, '') DESC, id DESC LIMIT 1", tuple(variants))
                row = _row(cur.fetchone())
        except sqlite3.Error:
            return {}
        if not row:
            return {}
        return {'status': _text(row.get('status')).lower(), 'reason': _text(row.get('reason'), 300), 'updatedAt': row.get('updated_at') or '', 'quantity': row.get('quantity')}

    def _voice_analysis(self, media_ids):
        """English/Lithuanian text already produced for prep voice notes (voice_note_analysis in bol.db)."""
        out = {}
        ids = [int(i) for i in media_ids if str(i).isdigit()]
        if not ids:
            return out
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'voice_note_analysis'):
                    return out
                cur.execute(f"SELECT * FROM voice_note_analysis WHERE media_id IN ({','.join('?' for _ in ids)})", tuple(ids))
                for r in cur.fetchall():
                    r = _row(r)
                    running = float(r.get('lease_until') or 0) > time.time()
                    out[int(r['media_id'])] = {
                        'lithuanian': r.get('lithuanian') or '', 'english': r.get('english') or '',
                        'status': 'processing' if running else ('complete' if r.get('english') else ('error' if r.get('error') else 'pending')),
                        'error': r.get('error') or '',
                    }
        except sqlite3.Error:
            pass
        return out

    def detail(self, upc, *, base_url='http://localhost/'):
        """Everything the panel needs to list one unit: fields to fill, notes, voice notes, photos, stock."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        detail = self._upc_detail(upc, base_url)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_listagent(cur)
            self.init_tables(cur)
            proposal = (self._latest_proposals(cur, [upc]) or {}).get(upc) or {}
            links = self._links_for_upcs(cur, [upc]).get(upc, [])
            cur.execute("SELECT * FROM listing_queue WHERE upc IN (%s) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1"
                        % ','.join('?' for _ in self._variants(upc)), tuple(self._variants(upc)))
            queue_row = _row(cur.fetchone()) or {}
            cur.execute("SELECT platform FROM lister_queue_state WHERE upc = ? AND state = 'skipped'", (upc,))
            skipped = [r['platform'] for r in cur.fetchall()]
            own_photos = []
            if self._has_table(cur, 'listing_photos'):
                cur.execute(f"SELECT id, image_path, created_at, original_filename FROM listing_photos WHERE upc IN ({','.join('?' for _ in self._variants(upc))}) ORDER BY id DESC LIMIT 60",
                            tuple(self._variants(upc)))
                own_photos = [_row(r) for r in cur.fetchall()]
            learned = self._learned(cur, upc)
        prep = detail.get('prep') or {}
        voice_rows = prep.get('voice_notes') or []
        analysis = self._voice_analysis([v.get('id') for v in voice_rows])
        voice_notes = []
        english_notes = []
        for v in voice_rows:
            a = analysis.get(int(v['id'])) if str(v.get('id', '')).isdigit() else None
            entry = {'id': v.get('id'), 'url': v.get('url'), 'createdAt': v.get('created_at') or '',
                     'english': (a or {}).get('english', ''), 'lithuanian': (a or {}).get('lithuanian', ''),
                     'status': (a or {}).get('status', 'pending'), 'error': (a or {}).get('error', '')}
            voice_notes.append(entry)
            if entry['english']:
                english_notes.append(entry['english'])
        written_notes = []
        for n in prep.get('notes') or []:
            text = n.get('note') if isinstance(n, dict) else n
            written_notes.append({'id': (n.get('id') if isinstance(n, dict) else None), 'text': _text(text),
                                  'english': note_text_variants(text), 'createdAt': (n.get('created_at') if isinstance(n, dict) else '') or ''})
        defect = _text(detail.get('defect'))
        note_texts = ([defect] if defect else []) + [n['english'] or n['text'] for n in written_notes if (n['english'] or n['text'])] + english_notes
        prep_status = self._prep_status(upc)
        condition = condition_from_notes(note_texts, suffixed=is_suffixed(upc),
                                         prep_status=prep_status.get('status') or '')

        photos = []
        seen = set()

        def add_photo(url, source, photo_id=None, origin=''):
            url = _absolute(url, base_url)
            if not url.lower().startswith(('http://', 'https://')):
                return  # catalog rows carry placeholders like "No image"
            # our own static file can come back from the catalog under another scheme/host: one tile per file
            key = url.split('/static/', 1)[1].split('?', 1)[0] if '/static/' in url else url
            if key in seen:
                return
            seen.add(key)
            photos.append({'url': url, 'source': source, 'id': photo_id, 'name': url.rsplit('/', 1)[-1].split('?')[0],
                           'from': origin})  # for AI photos: the source photo's file name

        for p in own_photos:
            rel = _text(p.get('image_path'))
            if rel:
                origin = _text(p.get('original_filename'))
                add_photo(f'/static/{rel}', 'ai' if '_ai_' in rel else 'listing', p.get('id'),
                          origin[3:] if origin.startswith('ai:') else '')
        for url in prep.get('images') or []:
            add_photo(url, 'prep')
        for url in detail.get('images') or []:
            add_photo(url, 'catalog')

        inventory = detail.get('inventory') or {}
        total_qty = int(inventory.get('total_quantity') or 0)
        proposal_data = proposal.get('proposal') or {}
        eligibility = proposal.get('eligibility') or {}
        if proposal.get('ready'):
            fields = fill_fields(proposal_data, eligibility, upc=upc, base_url=base_url)
            fields['source'] = 'proposal'
        else:
            fields = fill_fields({}, {}, upc=upc, base_url=base_url)
            fields['title'] = _text(detail.get('title') or queue_row.get('title'), 80)
            fields['quantity'] = 1 if is_suffixed(upc) else (total_qty or None)
            fields['source'] = 'inventory'
        if is_suffixed(upc):
            fields['quantity'] = 1
        generated = self.generated(upc)
        fields['generated'] = {k: ('auto' if v.get('auto') else 'manual') for k, v in generated.items()}
        if generated.get('title', {}).get('text'):
            fields['title'] = generated['title']['text'][:80]
        if generated.get('description', {}).get('html'):
            fields['descriptionHtml'] = generated['description']['html']
            fields['descriptionText'] = generated['description']['text']
        # Evidence beats a proposal's assumed default both ways: a flaw note makes it used, a clean
        # pass in Item Prep makes it NEW. A condition note already on the proposal says something
        # about this unit that prep did not see, so it keeps the proposal's condition.
        evidence = not condition['assumed'] and not (condition['condition'] == 'NEW' and _text(fields.get('conditionDescription')))
        if not fields.get('condition') or evidence:
            fields['condition'] = condition['condition']
        fields['amazonCondition'] = AMAZON_CONDITIONS.get(fields['condition'], '')
        if not fields.get('conditionDescription') and condition['conditionDescription']:
            fields['conditionDescription'] = condition['conditionDescription']
            fields['conditionDescriptionSource'] = 'notes'  # the panel flags it: read once before listing
        # One-line stock picture for the panel: what prep counted, what the rack holds, what is live.
        def live_units(entries):
            units = 0
            for e in entries or []:
                state = _text(e.get('state') or e.get('status')).lower()
                if state and state not in ('active', 'live', ''):
                    continue
                try:
                    units += max(int(e.get('quantity') or 0), 1)
                except (TypeError, ValueError):
                    units += 1
            return units
        ebay_live = (detail.get('ebay_store') or {}).get('listings') or []
        amazon_live = (detail.get('amazon_store') or {}).get('listings') or []
        prep_qty = prep_status.get('quantity')
        try:
            prep_qty = int(prep_qty) if prep_qty not in (None, '') else None
        except (TypeError, ValueError):
            prep_qty = None
        if is_suffixed(upc):
            listable = 1 if total_qty or prep_qty else 0
        elif eligibility.get('listable_quantity') not in (None, ''):
            listable = int(eligibility.get('listable_quantity') or 0)
        elif prep_qty and total_qty:
            listable = min(prep_qty, total_qty)
        else:
            listable = total_qty or prep_qty or 0
        gate = {'prepQty': prep_qty, 'rackQty': total_qty, 'liveEbay': live_units(ebay_live), 'liveAmazon': live_units(amazon_live),
                'listable': listable, 'mismatch': bool(prep_qty and total_qty and prep_qty != total_qty)}
        if not fields.get('images'):
            fields['images'] = [p['url'] for p in photos if p['source'] != 'catalog'][:12] or [p['url'] for p in photos][:12]
        if not fields.get('descriptionText'):
            parts = [fields['title']]
            if fields.get('conditionDescription'):
                parts.append('Condition: ' + fields['conditionDescription'])
            fields['descriptionText'] = '\n\n'.join(p for p in parts if p)
        # Steps the store makes the user take before the form: suggest what the proposal or a past choice says.
        for platform in PLATFORMS:
            picks = learned.get(platform) or {}
            if not fields.get('categoryPath') and picks.get('category'):
                fields['categoryPath'] = picks['category']['chosen']
                fields['categoryPathSource'] = 'learned'
        bol = detail.get('bol') or {}
        cost = None
        for key in ('avg_cost', 'cost', 'unit_cost'):
            if bol.get(key) not in (None, ''):
                try:
                    cost = round(float(bol[key]), 2)
                    break
                except (TypeError, ValueError):
                    pass
        return {
            'upc': upc,
            'baseUpc': _base_upc(upc),
            'suffixed': is_suffixed(upc),
            'title': fields['title'],
            'fields': fields,
            'condition': condition,
            'notes': written_notes,
            'defect': defect,
            'voiceNotes': voice_notes,
            'videos': prep.get('videos') or [],
            'photos': photos,
            'inventory': {'quantity': total_qty, 'positions': inventory.get('positions') or [],
                          'rows': (inventory.get('rows') or [])[:10]},
            'cost': cost,
            'bol': {k: bol.get(k) for k in ('description', 'lot_number', 'original_retail', 'avg_cost') if isinstance(bol, dict) and k in bol},
            'existing': {'ebay': (detail.get('ebay_store') or {}).get('listings') or [],
                         'amazon': (detail.get('amazon_store') or {}).get('listings') or []},
            'links': links,
            'queue': {'id': queue_row.get('id'), 'status': queue_row.get('status'),
                      'listed': {'ebay': bool(queue_row.get('listed_ebay_at')), 'amazon': bool(queue_row.get('listed_amazon_at'))},
                      'skipped': skipped},
            'proposal': {k: proposal[k] for k in ('id', 'status', 'ready', 'updatedAt', 'flags') if k in proposal},
            'learned': learned,
            'prepStatus': prep_status,
            'gate': gate,
            'preload': self.preload_status(upc),
            'preparing': self._job_state(upc),
            'mobilePhotosUrl': mobile_photos_url(upc, base_url),
            'aiPhotoPrompt': DEFAULT_AI_PHOTO_PROMPT,
        }

    def own_chat(self, email):
        """The chat this login linked for itself, or None (not linked, not signed in, no pairing)."""
        if not email or not callable(self.telegram_chat_for):
            return None
        try:
            found = self.telegram_chat_for(email)
        except Exception:
            return None
        return found if found and _text(found.get('chat_id')) else None

    def send_link(self, *, key, token, url, text, to_email=''):
        """Telegram one link and remember the messages it made, so that opening the link - or
        replacing it with a different one - can take those messages back down again. It goes to the
        phone of whoever asked (to_email, once they linked their Telegram); a person who has not
        linked one yet still gets it the old way, on every enabled chat."""
        if not callable(self.telegram_send):
            raise ListerError('Telegram is not wired up on this server.', 501)
        own = self.own_chat(to_email)
        if own:
            rows = [{'chat_id': own['chat_id'], 'display_name': own.get('name'), 'enabled': 1}]
        else:
            if not callable(self.telegram_recipients):
                raise ListerError('Telegram is not wired up on this server.', 501)
            try:
                rows = self.telegram_recipients() or []
            except Exception as e:
                raise ListerError('Could not read the Telegram recipients: ' + str(e), 500)

        targets, seen = [], set()
        for row in rows:
            try:
                chat_id = _text((row or {}).get('chat_id'))
                enabled = int((row or {}).get('enabled') or 0) == 1
            except Exception:
                continue
            if not chat_id or not enabled or chat_id in seen:
                continue
            seen.add(chat_id)
            targets.append({'chatId': chat_id, 'name': _text((row or {}).get('display_name')) or chat_id})
        if not targets:
            raise ListerError('No enabled Telegram recipients. Add one on the Telegram page first.', 400)

        sent, errors, delivered = [], [], []
        for target in targets:
            try:
                # disable_notification=False: the phone should buzz, that is the point of the button.
                ok, result = self.telegram_send(target['chatId'], text, disable_notification=False)
            except Exception as e:
                ok, result = False, str(e)
            if ok:
                sent.append(target['name'])
                try:
                    message_id = int((result or {}).get('message_id') or 0)
                except Exception:
                    message_id = 0
                delivered.append((target['chatId'], message_id))
            else:
                errors.append({'chatId': target['chatId'], 'error': _text(result, 200)})

        if delivered:
            try:
                with self.db('listagent.db') as conn:
                    cur = conn.cursor()
                    self.init_tables(cur)
                    cur.executemany(
                        'INSERT OR REPLACE INTO lister_photo_links (token, chat_id, message_id, upc, created_at, opened_at) '
                        'VALUES (?, ?, ?, ?, ?, NULL)',
                        [(token, chat_id, message_id, _text(key), _now()) for chat_id, message_id in delivered])
                    conn.commit()
            except Exception:
                # A link that cannot be cleaned up later still has to reach the phone.
                pass
        if not sent:
            raise ListerError('Telegram refused the message: ' + (errors[0]['error'] if errors else 'unknown error'), 502)
        return {'token': token, 'url': url, 'sent': sent, 'errors': errors, 'routed': 'you' if own else 'everyone'}

    def photo_link(self, upc, *, base_url='http://localhost/', to_email=''):
        """Telegram the phone camera page for this unit — the QR code without the scanning."""
        upc = _text(upc)
        if not upc:
            raise ListerError('upc is required')
        token = secrets.token_urlsafe(9)
        url = mobile_photos_url(upc, base_url, back=False, token=token)
        title = self._photo_link_title(upc)
        # Nothing but the icon, the name and the link: the message is read on a lock screen.
        text = '\U0001F4F7 ' + (title or ('UPC ' + upc)) + '\n' + url
        result = self.send_link(key=upc, token=token, url=url, text=text, to_email=to_email)
        return {'url': url, 'sent': result['sent'], 'errors': result['errors'], 'routed': result['routed']}

    def _photo_link_title(self, upc):
        """What to call this unit in a chat message: the proposal title, else the BOL line."""
        try:
            with self.db('listagent.db') as conn:
                cur = conn.cursor()
                # A proposal is usually filed under the base code, the queue row under the
                # suffixed one, so ask for both.
                keys = list(dict.fromkeys(self.upc_variants(upc) + self.upc_variants(_base_upc(upc))))
                proposals = self._latest_proposals(cur, keys)
            for entry in proposals.values():
                found = _text((entry.get('proposal') or {}).get('title'), 120)
                if found:
                    return found
        except Exception:
            pass
        try:
            with self.db('bol.db') as conn:
                row = conn.execute('SELECT item_description FROM bol_items WHERE upc = ? COLLATE NOCASE '
                                   'ORDER BY import_date DESC, id DESC LIMIT 1', (_base_upc(upc),)).fetchone()
            return _text((_row(row) or {}).get('item_description'), 120)
        except Exception:
            return ''

    def photo_link_opened(self, token):
        """The phone opened the link, so the bot takes its own message back down."""
        token = _text(token, 64)
        if not token:
            raise ListerError('token is required')
        try:
            with self.db('listagent.db') as conn:
                cur = conn.cursor()
                self.init_tables(cur)
                rows = [_row(r) for r in cur.execute(
                    'SELECT chat_id, message_id FROM lister_photo_links WHERE token = ? AND opened_at IS NULL',
                    (token,)).fetchall()]
                cur.execute('UPDATE lister_photo_links SET opened_at = ? WHERE token = ?', (_now(), token))
                conn.commit()
        except Exception as e:
            raise ListerError('Could not look the link up: ' + str(e), 500)
        if not rows:
            return {'deleted': 0}

        token_value = _text(self.telegram_token() if callable(self.telegram_token) else '')
        if not token_value:
            return {'deleted': 0}
        import requests
        deleted = 0
        for row in rows:
            message_id = int(row.get('message_id') or 0)
            if not message_id:
                continue
            try:
                response = requests.post(f'https://api.telegram.org/bot{token_value}/deleteMessage',
                                         json={'chat_id': str(row.get('chat_id') or ''), 'message_id': message_id},
                                         timeout=(4, 15))
                if response.ok and (response.json() or {}).get('ok'):
                    deleted += 1
            except Exception:
                # Telegram refusing a delete must never break the page that is about to take photos.
                pass
        return {'deleted': deleted}

    def prepare(self, upc, *, base_url='http://localhost/', force=False, actor='lister'):
        """Build the Listing Agent proposal (title, price, category, specifics, description) in the background."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        if not self.build_proposal or not self.app:
            raise ListerError('The proposal builder is not available on this server.', 501)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_listagent(cur)
            proposal = (self._latest_proposals(cur, [upc]) or {}).get(upc) or {}
        if proposal.get('ready') and not force:
            return {'status': 'ready', 'proposalId': proposal['id']}
        if proposal and proposal.get('status') == 'blocked' and not force:
            # The gate refused to build values (no rack stock, no category...): tell the panel instead of spinning.
            reasons = [_text(f.get('message'), 200) for f in (proposal.get('flags') or []) if f.get('level') == 'block']
            return {'status': 'blocked', 'proposalId': proposal['id'], 'reasons': reasons}
        with self._jobs_lock:
            job = self._jobs.get(upc)
            if job and job.get('running') and time.time() - job.get('started', 0) <= PREPARE_STALE_SECONDS:
                return {'status': 'preparing'}
            self._jobs[upc] = {'running': True, 'started': time.time(), 'started_iso': _now(), 'error': ''}
        app, build = self.app, self.build_proposal

        def run():
            error = ''
            try:
                with app.app_context():
                    build(upc, actor=actor, base_url=base_url)
            except Exception as e:  # reported through the detail endpoint, never raised in the thread
                error = str(e)[:300] or type(e).__name__
            with self._jobs_lock:
                self._jobs[upc] = {'running': False, 'started': time.time(), 'started_iso': _now(), 'error': error}

        threading.Thread(target=run, name=f'lister-prepare-{upc}', daemon=True).start()
        return {'status': 'preparing'}

    def skip(self, upc, *, platform, actor=''):
        """X on the panel: hide the item from this store's list. Gone from both lists means out of the queue."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('''INSERT INTO lister_queue_state (upc, platform, state, actor, created_at) VALUES (?, ?, 'skipped', ?, ?)
                           ON CONFLICT(upc, platform) DO UPDATE SET state = 'skipped', actor = excluded.actor, created_at = excluded.created_at''',
                        (upc, platform, actor or None, _now()))
            conn.commit()
        return {'upc': upc, 'platform': platform, 'queue': self.finalize_queue(upc) or 'queued'}

    def unskip(self, upc, *, platform):
        upc = self.format_upc12(upc)
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            cur.execute('DELETE FROM lister_queue_state WHERE upc = ? AND platform = ?', (upc, platform))
            variants = self._variants(upc)
            cur.execute(f"SELECT id, status FROM listing_queue WHERE upc IN ({','.join('?' for _ in variants)}) ORDER BY id DESC LIMIT 1", tuple(variants))
            row = _row(cur.fetchone())
            if row and row['status'] in ('removed', 'done'):
                cur.execute("UPDATE listing_queue SET status = 'queued', removed_at = NULL WHERE id = ?", (row['id'],))
            conn.commit()
        return {'upc': upc, 'platform': platform, 'queue': 'queued'}

    def finalize_queue(self, upc):
        """Once both stores are listed or skipped, the queue row is done (something listed) or removed."""
        upc = self.format_upc12(upc)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            variants = self._variants(upc)
            cur.execute(f"SELECT * FROM listing_queue WHERE upc IN ({','.join('?' for _ in variants)}) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1", tuple(variants))
            row = _row(cur.fetchone())
            if not row:
                return ''
            cur.execute("SELECT platform FROM lister_queue_state WHERE upc = ? AND state = 'skipped'", (upc,))
            skipped = {r['platform'] for r in cur.fetchall()}
            resolved = all(row.get(f'listed_{p}_at') or p in skipped for p in PLATFORMS)
            if not resolved:
                return ''
            listed_any = any(row.get(f'listed_{p}_at') for p in PLATFORMS)
            if listed_any:
                cur.execute("UPDATE listing_queue SET status = 'done' WHERE id = ?", (row['id'],))
                conn.commit()
                return 'done'
            cur.execute("UPDATE listing_queue SET status = 'removed', removed_at = ? WHERE id = ?", (_now(), row['id']))
            conn.commit()
            return 'removed'

    # -- Items to List "+" that follows the side panel's open store ---------------------------

    PANEL_FRESH_SECONDS = 45

    def panel_report(self, email, *, platform, follow=True, open_=True):
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('''INSERT INTO lister_panel_state (email, platform, follow, open, updated_at) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(email) DO UPDATE SET platform = excluded.platform, follow = excluded.follow,
                           open = excluded.open, updated_at = excluded.updated_at''',
                        (email or '', platform, 1 if follow else 0, 1 if open_ else 0, time.time()))
            conn.commit()
        return self.panel_state(email)

    def panel_state(self, email):
        """The store the side panel shows for this person, when it is open and the setting is on."""
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT * FROM lister_panel_state WHERE email = ?', (email or '',))
            row = _row(cur.fetchone())
        if not row:
            return {'active': False, 'platform': ''}
        age = max(0.0, time.time() - float(row['updated_at'] or 0))
        active = bool(row['open']) and bool(row['follow']) and age <= self.PANEL_FRESH_SECONDS
        return {'active': active, 'platform': row['platform'] if active else '', 'follow': bool(row['follow']),
                'open': bool(row['open']) and age <= self.PANEL_FRESH_SECONDS}

    def store_states(self, upcs):
        """Per requested UPC, where it stands on each store's list: listed, on the list, or off it.

        Listed is read from every queue row, whatever its status, and from the Lister's links:
        clearing a queue row does not unlist anything on a store.
        """
        out = {}
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            cur.execute("SELECT upc, platform FROM lister_queue_state WHERE state = 'skipped'")
            skipped = {(r['upc'], r['platform']) for r in cur.fetchall()}
            for raw in list(dict.fromkeys(_text(u) for u in (upcs or []) if _text(u)))[:250]:
                variants = self._variants(raw)
                marks = ','.join('?' for _ in variants)
                cur.execute(f'SELECT * FROM listing_queue WHERE upc IN ({marks}) ORDER BY id DESC', tuple(variants))
                rows = [_row(r) for r in cur.fetchall()]
                cur.execute(f'SELECT DISTINCT platform FROM listing_links WHERE upc IN ({marks})', tuple(variants))
                linked = {r['platform'] for r in cur.fetchall()}
                latest = rows[0] if rows else {}
                key = self.format_upc12(raw) or raw
                entry = {'queue': latest.get('status') or ''}
                for p in PLATFORMS:
                    listed = p in linked or any(r.get(f'listed_{p}_at') for r in rows)
                    off = any((v, p) in skipped for v in {key, raw, *variants})
                    if listed:
                        entry[p] = 'listed'
                    elif latest.get('status') == 'queued' and not off:
                        entry[p] = 'on'
                    else:
                        entry[p] = 'off'
                out[raw] = entry
        return out

    def set_store(self, upc, *, platform, on, only=False, fresh=False, actor=''):
        """Put an item on one store's list (and, with only, keep it off the other) or take it off."""
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        raw = _text(upc)
        if not raw:
            raise ListerError('upc is required')
        before = self.store_states([raw])[raw]
        other = 'amazon' if platform == 'ebay' else 'ebay'
        if on:
            self.unskip(raw, platform=platform)
            # A new entry, or one coming back from done/removed, is for this store only. One that is
            # already waiting on the other store stays there: that was asked for on its own.
            if only and (fresh or before['queue'] != 'queued') and before[other] != 'listed':
                self.skip(raw, platform=other, actor=actor)
        else:
            self.skip(raw, platform=platform, actor=actor)
        return self.store_states([raw])[raw]

    def mark_existing(self, upc, *, platform, entry, actor='', base_url='http://localhost/'):
        """The store already carries this UPC: record that listing as the link without touching the page."""
        entry = entry or {}
        body = {'platform': platform, 'upc': upc, 'listing_id': entry.get('listingId') or '', 'sku': entry.get('sku') or '',
                'asin': entry.get('asin') or '', 'store_upc': entry.get('upc') or '', 'title': entry.get('title') or '',
                'note': 'already on the store; linked from the side panel', 'kind': 'existing'}
        return self.record_link(body, actor=actor, base_url=base_url)

    # -- learning the store's intermediate steps ------------------------------------------------

    def _learned(self, cur, upc):
        """Latest choice per store and step for this catalog UPC (any unit of it), plus how often each was picked."""
        self.init_tables(cur)
        base = _base_upc(upc)
        keys = tuple({base, base.lstrip('0') or base, base.zfill(12) if base.isdigit() else base})
        cur.execute(f"SELECT platform, step, chosen, suggested, url, created_at FROM lister_choices WHERE base_upc IN ({','.join('?' for _ in keys)}) ORDER BY id DESC LIMIT 100", keys)
        out = {}
        for r in cur.fetchall():
            r = _row(r)
            steps = out.setdefault(r['platform'], {})
            entry = steps.setdefault(r['step'], {'chosen': r['chosen'], 'suggested': r['suggested'] or '', 'createdAt': r['created_at'], 'count': 0, 'history': []})
            entry['count'] += 1
            if r['chosen'] not in entry['history']:
                entry['history'].append(r['chosen'])
        return out

    def learn(self, data, *, actor=''):
        """Record what the user picked on a store step (category, catalog match, condition) versus the suggestion."""
        upc = self.format_upc12(_text(data.get('upc')))
        platform = _text(data.get('platform')).lower()
        step = _text(data.get('step'), 40).lower()
        chosen = _text(data.get('chosen'), 500)
        if not upc:
            raise ListerError('upc is required')
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        if step not in ('category', 'match', 'condition', 'field'):
            raise ListerError('step must be category, match, condition or field')
        if not chosen:
            raise ListerError('chosen is required')
        suggested = _text(data.get('suggested'), 500)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('''INSERT INTO lister_choices (upc, base_upc, platform, step, chosen, suggested, url, actor, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (upc, _base_upc(upc), platform, step, chosen, suggested or None, _text(data.get('url'), 500) or None, actor or None, _now()))
            choice_id = cur.lastrowid
            conn.commit()
            learned = self._learned(cur, upc)
        return {'id': choice_id, 'upc': upc, 'platform': platform, 'step': step, 'chosen': chosen, 'suggested': suggested,
                'agreed': bool(suggested) and suggested.strip().lower() == chosen.strip().lower(), 'learned': learned}

    # -- title / description from the notes (Claude Haiku, same key the Listing Agent uses) -------

    def generate(self, upc, *, kind, values, base_url='http://localhost/', auto=False, instructions=''):
        """A listing title (<= 80 chars) or an HTML description built from the item's values and prep notes."""
        upc = self.format_upc12(upc)
        if kind not in ('title', 'description'):
            raise ListerError('kind must be title or description')
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not api_key:
            raise ListerError('Text generation needs server setup: add ANTHROPIC_API_KEY to the Pi .env and restart the service.', 503)
        values = values if isinstance(values, dict) else {}
        title = _text(values.get('title'), 200)
        if not title:
            raise ListerError('a title is required')
        notes = [_text(n, 400) for n in (values.get('notes') or []) if _text(n)]
        aspects = values.get('aspects') if isinstance(values.get('aspects'), dict) else {}
        aspect_lines = '\n'.join(f'- {k}: {", ".join(v) if isinstance(v, list) else v}' for k, v in aspects.items() if k and v)
        facts = [
            f'Current title: {title}',
            f"Inventory description: {_text(values.get('systemTitle'), 200)}" if _text(values.get('systemTitle')) else '',
            f"Brand: {_text(values.get('brand'), 80)}" if _text(values.get('brand')) else '',
            f"Category: {_text(values.get('categoryPath'), 200)}" if _text(values.get('categoryPath')) else '',
            (f"Condition: {_text(values.get('condition'), 40)}"
             + (f" ({CONDITION_MEANING[_text(values.get('condition')).upper()]})" if _text(values.get('condition')).upper() in CONDITION_MEANING else '')
             if _text(values.get('condition')) else ''),
            f"Condition notes from the warehouse: {_text(values.get('conditionDescription'), 800)}" if _text(values.get('conditionDescription')) else '',
            ('Prep notes:\n' + '\n'.join('- ' + n for n in notes)) if notes else '',
            f'UPC: {_base_upc(upc)}' if upc else '',
            f'Item specifics:\n{aspect_lines}' if aspect_lines else '',
        ]
        facts_text = '\n'.join(f for f in facts if f)
        if kind == 'title':
            prompt = ('Write one eBay listing title of at most 80 characters for this product: brand first, then the product, '
                      'key attributes (size, color, count, material) and nothing else. No quotes, no emojis, no ALL CAPS, '
                      'no words like "wow" or "look". Reply with the title only.\n\n' + facts_text)
        else:
            prompt = ('Write the item-specific copy for an eBay listing description of this product and reply with ONE JSON object '
                      'only, no markdown fences, with these keys:\n'
                      '"intro": one or two sentences presenting the product in a warm, factual tone, using only the facts below (no invented '
                      'features, design names or materials), e.g. "Beautiful **MacKenzie-Childs '
                      'Tutti Frutti Pineapple Figurine** featuring vibrant tropical colors and the signature **Courtly Check** pattern." '
                      'Wrap the full product name and at most one signature feature in **double asterisks** (the only formatting allowed).\n'
                      '"conditionNote": when the warehouse condition notes or prep notes mention any flaw, damage, wear, stain, chip, '
                      'missing part or open/damaged packaging, one or two plain sentences stating it and pointing to the close-up photos, '
                      'e.g. "This item is new and unused, but a few leaf tips have small chips/nicks. Please see the close-up photos for '
                      'the exact condition." Otherwise an empty string.\n'
                      '"details": a list of {"label", "value"} facts in this order when known: Brand, Collection, Item (what it is), '
                      'Approx. Size, Material, Color, Pieces, Care, Imported (label "" and value "Imported" only if stated), Quantity, '
                      'Condition (short, e.g. "New with minor cosmetic defects"). Wrap the size and material values in **double '
                      'asterisks**. Only facts given below, never guesses; leave out what is unknown.\n'
                      '"condition": two or three sentences for the ITEM CONDITION box: the condition in plain words and every flaw '
                      'from the notes stated honestly; mention packaging only if the notes or condition say something about it, e.g. "New and unused. A few of the colorful leaf tips have very small chips/nicks. '
                      'The imperfections are minor and are shown in the close-up photos."\n'
                      'Do not include a price, shipping, returns, the shop name, or the word eBay. English only.\n\n' + facts_text)
        # Extra house rules typed into the panel's prompt box, last so they win over the defaults.
        extra = _text(instructions, 1000)
        if extra:
            prompt += ('\n\nExtra instructions from the seller (follow these over the rules above where they conflict):\n' + extra)
        import requests
        try:
            response = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 1200 if kind == 'description' else 120,
                      'messages': [{'role': 'user', 'content': prompt}]},
                timeout=45,
            )
        except requests.RequestException:
            raise ListerError('The text service could not be reached. Try again in a moment.', 504)
        if response.status_code >= 400:
            try:
                message = ((response.json().get('error') or {}).get('message') or '')[:200]
            except Exception:
                message = response.text[:200]
            raise ListerError(f'Text generation failed (HTTP {response.status_code}). {message}'.strip(), 502)
        result = response.json()
        text = _text(((result.get('content') or [{}])[0].get('text') or ''))
        text = re.sub(r'^```[^\n]*\n?', '', text).rstrip('`').strip()
        if not text:
            raise ListerError('The text service returned nothing.', 502)
        try:
            from ai_usage import record as record_ai_usage
            record_ai_usage('anthropic', 'claude-haiku-4-5-20251001', f'Lister {kind}', result)
        except Exception:
            pass
        if kind == 'title':
            out = {'kind': kind, 'title': text.strip('"\'').splitlines()[0][:80]}
            self.store_generated(upc, 'title', out['title'], '', auto=auto)
            return out
        data = parse_description_json(text)
        condition = _text(values.get('condition'), 40)
        html = render_description(title, data, upc=upc, condition=condition)
        # Plain-text stores get the item part only, not the shop's eBay boilerplate.
        text_only = html_to_text(render_description_item(title, data, upc=upc, condition=condition))
        self.store_generated(upc, 'description', text_only, html, auto=auto)
        return {'kind': kind, 'descriptionHtml': html, 'descriptionText': text_only}

    # -- can Amazon take this UPC from us? (catalog ASIN + listing restrictions, cached per UPC) ---

    AMAZON_CHECK_TTL_DAYS = 7

    def _call_view(self, view, path, query):
        """Run one of the app's own JSON views in-process."""
        if not view or not self.app:
            return {}
        with self.app.test_request_context(path, query_string={k: str(v) for k, v in query.items() if v not in (None, '')}):
            rv = view()
        if isinstance(rv, tuple):
            rv = rv[0]
        data = rv.get_json(silent=True) if hasattr(rv, 'get_json') else rv
        return data or {}

    def _amazon_checks(self, cur, upcs):
        bases = tuple({_base_upc(u) for u in upcs if _base_upc(u)})
        if not bases:
            return {}
        self.init_tables(cur)
        cur.execute(f"SELECT * FROM lister_amazon_checks WHERE base_upc IN ({','.join('?' for _ in bases)})", bases)
        out = {}
        for r in cur.fetchall():
            r = _row(r)
            detail = _loads(r.get('detail'), {}) or {}
            out[r['base_upc']] = {'status': r['status'], 'asin': r.get('asin') or '', 'title': r.get('title') or '', 'brand': r.get('brand') or '',
                                  'reasons': _loads(r.get('reasons'), []), 'error': r.get('error') or '', 'conditionType': r.get('condition_type') or '',
                                  'blockedConditions': detail.get('blockedConditions') or [], 'openConditions': detail.get('openConditions') or [],
                                  'approvalOnly': bool(detail.get('approvalOnly')), 'links': detail.get('links') or [],
                                  'checkedAt': r.get('checked_at') or ''}
        return out

    def amazon_check(self, upc, *, force=False, condition_type=''):
        """status: listable | partial | approval | restricted | no_asin | unavailable | error.

        Only the conditions Sweet Shelves sells in count: Amazon answers a condition-less check with a
        row per condition it knows (collectible_*, refurbished, club), and those used to read as gating.
        Cached for a week per catalog UPC.
        """
        upc = self.format_upc12(upc)
        base = _base_upc(upc)
        if not base:
            raise ListerError('upc is required')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            cached = self._amazon_checks(cur, [base]).get(base)
        if cached and not force and cached['status'] in ('listable', 'partial', 'approval', 'restricted', 'no_asin'):
            stamp = _order_key(cached['checkedAt'])
            fresh = _order_key((datetime.datetime.now() - datetime.timedelta(days=self.AMAZON_CHECK_TTL_DAYS)).isoformat(timespec='seconds'))
            if stamp >= fresh and (not condition_type or cached['conditionType'] == condition_type):
                return {**cached, 'cached': True}
        if not self.amazon_catalog_view or not self.amazon_restriction_view:
            raise ListerError('The Amazon catalog check is not available on this server.', 501)
        result = {'asin': '', 'title': '', 'brand': '', 'status': 'error', 'reasons': [], 'error': '', 'conditionType': condition_type,
                  'blockedConditions': [], 'openConditions': [], 'approvalOnly': False, 'links': []}
        search = self._call_view(self.amazon_catalog_view, '/api/listingagent/amazon/catalog_search', {'upc': base, 'mode': 'upc', 'limit': 3})
        if not search.get('success'):
            error = _text(search.get('error'), 300)
            result['status'] = 'unavailable' if 'not available' in error.lower() else 'error'
            result['error'] = error or 'catalog search failed'
        else:
            hits = [h for h in (search.get('results') or []) if _text(h.get('asin'))]
            if not hits:
                result['status'] = 'no_asin'
            else:
                best = hits[0]
                result.update({'asin': _text(best.get('asin')).upper(), 'title': _text(best.get('title'), 200), 'brand': _text(best.get('brand'), 80)})
                check = self._call_view(self.amazon_restriction_view, '/api/listingagent/amazon/restriction_check',
                                        {'asin': result['asin'], 'conditionType': condition_type, 'refresh': '1' if force else ''})
                if not check.get('success'):
                    error = _text(check.get('error'), 300)
                    result['status'] = 'unavailable' if 'not available' in error.lower() else 'error'
                    result['error'] = error or 'restriction check failed'
                else:
                    restriction = check.get('restriction') or {}
                    reasons = [_text(r if isinstance(r, str) else (r.get('message') or r.get('reasonCode') or json.dumps(r)), 300) for r in (restriction.get('reasons') or [])]
                    result['reasons'] = [r for r in reasons if r]
                    blocked = [_text(c, 40) for c in (restriction.get('blockedConditions') or []) if _text(c)]
                    open_conditions = [_text(c, 40) for c in (restriction.get('openConditions') or []) if _text(c)]
                    result['blockedConditions'] = blocked
                    result['openConditions'] = open_conditions
                    result['approvalOnly'] = bool(restriction.get('approvalOnly'))
                    result['links'] = [_text(u, 300) for u in (restriction.get('links') or [])][:3]
                    if not restriction.get('checked'):
                        result['status'] = 'error'
                        result['error'] = _text(check.get('warning'), 300) or 'restriction check did not complete'
                    elif restriction.get('restricted'):
                        # Blocked in every condition we sell in. Approval-only is a door we can knock on.
                        result['status'] = 'approval' if result['approvalOnly'] else 'restricted'
                    elif blocked:
                        # Some conditions are gated but others are open: listable, with a caveat.
                        result['status'] = 'partial'
                    else:
                        result['status'] = 'listable'
        result['checkedAt'] = _now()
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            detail = json.dumps({'blockedConditions': result['blockedConditions'], 'openConditions': result['openConditions'],
                                 'approvalOnly': result['approvalOnly'], 'links': result['links']})
            cur.execute('''INSERT INTO lister_amazon_checks (base_upc, asin, title, brand, status, reasons, error, condition_type, detail, checked_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(base_upc) DO UPDATE SET asin = excluded.asin, title = excluded.title, brand = excluded.brand,
                               status = excluded.status, reasons = excluded.reasons, error = excluded.error,
                               condition_type = excluded.condition_type, detail = excluded.detail, checked_at = excluded.checked_at''',
                        (base, result['asin'] or None, result['title'] or None, result['brand'] or None, result['status'],
                         json.dumps(result['reasons']), result['error'] or None, condition_type or None, detail, result['checkedAt']))
            conn.commit()
        return {**result, 'cached': False}

    # -- AI text kept per unit ---------------------------------------------------------------------

    def store_generated(self, upc, kind, text, html, *, auto=False):
        upc = self.format_upc12(upc)
        if not upc or not _text(text):
            return
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('''INSERT INTO lister_generated (upc, kind, text, html, auto, created_at) VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(upc, kind) DO UPDATE SET text = excluded.text, html = excluded.html, auto = excluded.auto,
                               created_at = excluded.created_at''', (upc, kind, _text(text, 20000), html or None, 1 if auto else 0, _now()))
            conn.commit()

    def generated(self, upc):
        upc = self.format_upc12(upc)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT kind, text, html, auto, created_at FROM lister_generated WHERE upc = ?', (upc,))
            return {r['kind']: {'text': r['text'], 'html': r['html'] or '', 'auto': bool(r['auto']), 'createdAt': r['created_at']} for r in cur.fetchall()}

    # -- preload: everything the automatic switches would do, in the background, per item or for the list ---

    PRELOAD_STEPS = ('prepare', 'title', 'description', 'photos')

    def preload_status(self, upc):
        upc = self.format_upc12(upc)
        with self._jobs_lock:
            job = self._preloads.get(upc)
        return dict(job) if job else None

    def preload_all_status(self):
        with self._jobs_lock:
            overall = dict(self._preload_all)
            items = {u: dict(self._preloads[u]) for u in overall.get('upcs', []) if u in self._preloads}
        total = len(overall.get('upcs', []))
        done = sum(1 for u in overall.get('upcs', []) if (items.get(u) or {}).get('finishedAt'))
        overall['total'] = total
        overall['done'] = done
        overall['percent'] = int(round(done * 100 / total)) if total else 100
        overall['items'] = items
        return overall

    def _preload_values(self, upc, base_url):
        """What the panel would send to generate(): the item's values plus every note we hold."""
        info = self.detail(upc, base_url=base_url)
        fields = info.get('fields') or {}
        notes = [n.get('english') or n.get('text') for n in info.get('notes') or []] + \
                [v.get('english') for v in info.get('voiceNotes') or []] + ([info.get('defect')] if info.get('defect') else [])
        values = {'title': fields.get('title') or info.get('title'), 'systemTitle': info.get('title'), 'brand': fields.get('brand'),
                  'categoryPath': fields.get('categoryPath'), 'condition': fields.get('condition'),
                  'conditionDescription': fields.get('conditionDescription'), 'notes': [n for n in notes if n],
                  'aspects': fields.get('aspects') or {}}
        return info, values

    def _preload_run(self, upc, steps, base_url):
        def mark(step, value):
            with self._jobs_lock:
                job = self._preloads.get(upc)
                if job:
                    job['steps'][step] = value
        try:
            if 'prepare' in steps:
                mark('prepare', 'running')
                try:
                    with self.db('listagent.db') as conn:
                        cur = conn.cursor()
                        self.init_listagent(cur)
                        proposal = (self._latest_proposals(cur, [upc]) or {}).get(upc) or {}
                    if proposal.get('ready') or proposal.get('status') in ('held', 'approved'):
                        mark('prepare', 'done')  # values exist; a held/approved proposal cannot be rebuilt anyway
                    elif proposal.get('status') == 'blocked':
                        mark('prepare', 'blocked')
                    elif self.build_proposal:
                        self.build_proposal(upc, actor='lister', base_url=base_url)
                        mark('prepare', 'done')
                    else:
                        mark('prepare', 'skipped')
                except Exception as e:
                    if getattr(e, 'status_code', None) == 409:
                        mark('prepare', 'done')  # held proposal or approval under way: the values are already there
                    else:
                        mark('prepare', 'error: ' + str(e)[:120])
            info = values = None
            for kind in ('title', 'description'):
                if kind not in steps:
                    continue
                mark(kind, 'running')
                try:
                    if self.generated(upc).get(kind):
                        mark(kind, 'done')
                        continue
                    if values is None:
                        info, values = self._preload_values(upc, base_url)
                    self.generate(upc, kind=kind, values=values, base_url=base_url, auto=True)
                    mark(kind, 'done')
                except Exception as e:
                    mark(kind, 'error: ' + str(e)[:120])
            if 'photos' in steps:
                mark('photos', 'running')
                try:
                    if info is None:
                        info = self.detail(upc, base_url=base_url)
                    photos = info.get('photos') or []
                    have = {p.get('from') for p in photos if p.get('source') == 'ai' and p.get('from')}
                    own = [p for p in photos if p.get('source') in ('listing', 'prep') and p.get('name') not in have]
                    if not own and not any(p.get('source') in ('listing', 'prep') for p in photos):
                        own = [p for p in photos if p.get('source') == 'catalog' and p.get('name') not in have][:4]
                    with self._jobs_lock:
                        self._preloads[upc]['photos'] = {'done': 0, 'total': len(own)}
                    errors = []
                    for p in own:
                        try:
                            self.ai_photo(upc, url=p['url'], base_url=base_url)
                        except Exception as e:  # one bad source photo must not sink the rest
                            errors.append(str(e))
                            continue
                        with self._jobs_lock:
                            self._preloads[upc]['photos']['done'] += 1
                    if errors and len(errors) == len(own):
                        raise ListerError(errors[0])
                    mark('photos', 'done')
                except Exception as e:
                    mark('photos', 'error: ' + str(e)[:120])
        finally:
            with self._jobs_lock:
                job = self._preloads.get(upc)
                if job:
                    job['running'] = False
                    job['finishedAt'] = _now()

    def preload(self, upc, *, steps=None, base_url='http://localhost/'):
        """Start (or report) the background preload of one unit. Returns the job state."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        steps = [s for s in (steps or list(self.PRELOAD_STEPS)) if s in self.PRELOAD_STEPS]
        if not steps:
            raise ListerError('nothing to preload: no steps')
        if not self.app:
            raise ListerError('preload is not available on this server', 501)
        with self._jobs_lock:
            job = self._preloads.get(upc)
            if job and job.get('running') and time.time() - job.get('started', 0) <= PREPARE_STALE_SECONDS * 2:
                return dict(job)
            self._preloads[upc] = {'running': True, 'started': time.time(), 'startedAt': _now(), 'finishedAt': '',
                                   'steps': {s: 'pending' for s in steps}, 'photos': None}
        app = self.app

        def run():
            with app.app_context():
                self._preload_run(upc, steps, base_url)

        threading.Thread(target=run, name=f'lister-preload-{upc}', daemon=True).start()
        return self.preload_status(upc)

    def preload_all(self, upcs, *, steps=None, base_url='http://localhost/'):
        """Preload a list of units one after another (the whole queue, typically)."""
        upcs = [self.format_upc12(u) for u in upcs or [] if self.format_upc12(u)]
        steps = [s for s in (steps or list(self.PRELOAD_STEPS)) if s in self.PRELOAD_STEPS]
        if not upcs:
            raise ListerError('upcs is required')
        if not steps:
            raise ListerError('nothing to preload: no steps')
        if not self.app:
            raise ListerError('preload is not available on this server', 501)
        with self._jobs_lock:
            if self._preload_all.get('running'):
                return self.preload_all_status()
            self._preload_all = {'running': True, 'upcs': upcs, 'done': 0, 'startedAt': _now(), 'finishedAt': ''}
            for u in upcs:
                job = self._preloads.get(u)
                if not (job and job.get('running')):
                    self._preloads[u] = {'running': True, 'started': time.time(), 'startedAt': _now(), 'finishedAt': '',
                                         'steps': {s: 'pending' for s in steps}, 'photos': None, 'queued': True}
        app = self.app

        def run():
            with app.app_context():
                for u in upcs:
                    with self._jobs_lock:
                        job = self._preloads.get(u)
                        if job:
                            job['started'] = time.time()
                            job.pop('queued', None)
                    self._preload_run(u, steps, base_url)
                with self._jobs_lock:
                    self._preload_all['running'] = False
                    self._preload_all['finishedAt'] = _now()

        threading.Thread(target=run, name='lister-preload-all', daemon=True).start()
        return self.preload_all_status()

    # -- voice notes ---------------------------------------------------------------------------

    def analyze_voice(self, media_id, *, reanalyze=False):
        """Transcribe (Whisper) and translate (Claude) one prep voice note through the shared service."""
        from voice_note_routes import VoiceNotes, VoiceError
        base_dir = self.base_dir or self.static_folder.parent
        service = VoiceNotes(base_dir, str(self.static_folder))
        try:
            return service.analyze(int(media_id), reanalyze=bool(reanalyze))
        except VoiceError as e:
            raise ListerError(str(e), getattr(e, 'status', 502))

    # -- photos ---------------------------------------------------------------------------------

    def local_photo(self, url, base_url):
        """Absolute path of one of our own static images, or None for anything outside static/."""
        value = _text(url)
        if not value:
            return None
        for prefix in (base_url.rstrip('/'), ''):
            candidate = value[len(prefix):] if prefix and value.startswith(prefix) else (value if not prefix else '')
            if candidate.startswith('/static/'):
                rel = candidate[len('/static/'):].split('?', 1)[0]
                path = (self.static_folder / rel).resolve()
                try:
                    inside = path.is_relative_to(self.static_folder.resolve())
                except AttributeError:
                    inside = str(path).startswith(str(self.static_folder.resolve()))
                if inside and path.is_file():
                    return path
        return None

    def photo_bytes(self, url, base_url):
        """(bytes, mime, name) for a photo: our static file directly, anything else fetched over HTTPS."""
        path = self.local_photo(url, base_url)
        if path is not None:
            mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
            data = path.read_bytes()
            if len(data) > AI_PHOTO_MAX_BYTES:
                raise ListerError('That photo is larger than 20 MB.', 413)
            return data, mime, path.name
        value = _text(url)
        if value.startswith(base_url.rstrip('/') + '/') or value.startswith('/'):
            raise ListerError('That photo is not one of our static files.')
        value = secure_image_url(value)
        if not value.lower().startswith('https://'):
            raise ListerError('Photos must be Sweet Shelves static files or https images.')
        import requests
        try:
            response = requests.get(value, timeout=20, stream=True)
        except requests.RequestException:
            raise ListerError('Could not download that photo.', 502)
        if response.status_code != 200:
            raise ListerError(f'Could not download that photo (HTTP {response.status_code}).', 502)
        data = response.raw.read(AI_PHOTO_MAX_BYTES + 1)
        if len(data) > AI_PHOTO_MAX_BYTES:
            raise ListerError('That photo is larger than 20 MB.', 413)
        mime = (response.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip()
        name = value.rsplit('/', 1)[-1].split('?')[0] or 'photo.jpg'
        return data, mime, name

    def ai_photo(self, upc, *, url, prompt='', base_url='http://localhost/', quality='medium'):
        """Send one photo through the OpenAI image edit and store the result as a listing photo."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if not api_key:
            raise ListerError('AI photoshop needs server setup: add OPENAI_API_KEY to the Pi .env and restart the service.', 503)
        prompt = _text(prompt, 2000) or DEFAULT_AI_PHOTO_PROMPT
        quality = quality if quality in ('low', 'medium', 'high', 'auto') else 'medium'
        data, mime, name = self.photo_bytes(url, base_url)
        import requests
        try:
            response = requests.post(
                'https://api.openai.com/v1/images/edits',
                headers={'Authorization': f'Bearer {api_key}'},
                files=[('image[]', (name, data, mime))],
                data={'model': OPENAI_IMAGE_MODEL, 'prompt': prompt, 'n': '1', 'quality': quality, 'size': 'auto',
                      'output_format': 'jpeg'},
                timeout=AI_PHOTO_TIMEOUT,
            )
        except requests.RequestException:
            raise ListerError('The image service could not be reached. Try again in a moment.', 504)
        if response.status_code != 200:
            try:
                message = ((response.json().get('error') or {}).get('message') or '')[:300]
            except Exception:
                message = response.text[:300]
            raise ListerError(f'Image edit failed (HTTP {response.status_code}). {message}'.strip(), 502)
        try:
            result = response.json()
            image_b64 = result['data'][0]['b64_json']
            output = base64.b64decode(image_b64)
        except Exception:
            raise ListerError('The image service returned no picture.', 502)
        try:
            from ai_usage import record as record_ai_usage
            record_ai_usage('openai', OPENAI_IMAGE_MODEL, 'Lister AI photoshop', result)
        except Exception:
            pass
        folder = self.static_folder / 'listingagent_uploads'
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{upc}_ai_{int(time.time() * 1000)}.jpg"
        (folder / filename).write_bytes(output)
        rel = f'listingagent_uploads/{filename}'
        photo_id = None
        if self.add_listing_photo:
            try:
                saved = self.add_listing_photo(upc, image_path=rel, original_filename=f'ai:{name}', size_bytes=len(output))
                photo_id = (saved or {}).get('id')
            except Exception:
                photo_id = None
        if self.bump_data_version:
            try:
                self.bump_data_version()
            except Exception:
                pass
        photo_url = base_url.rstrip('/') + '/static/' + rel
        return {'photo': {'url': photo_url, 'id': photo_id, 'source': 'ai', 'name': filename, 'from': name},
                'prompt': prompt}

    # -- ledger ---------------------------------------------------------------------------------

    def ledger(self, *, upc='', platform='', limit=200):
        """Every listing this panel linked, with the sales that traced back to it (sold.db orders)."""
        links = self.links(upc=upc, platform=platform, limit=limit)
        if not links:
            return []
        sales = {}
        try:
            with self.db('sold.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'orders'):
                    cur.execute('PRAGMA table_info(orders)')
                    columns = {r[1] for r in cur.fetchall()}
                    wanted = [c for c in ('id', 'order_id', 'store', 'item_id', 'sku', 'barcode', 'source_upc', 'listing_sku',
                                          'listing_listing_id', 'listing_asin', 'listing_trace_id', 'paid_time', 'shipped_time',
                                          'title', 'quantity', 'price') if c in columns]
                    if 'order_id' in columns:
                        keys = set()
                        for link in links:
                            for value in (link.get('listing_id'), link.get('sku'), link.get('asin')):
                                if _text(value):
                                    keys.add(_text(value))
                        if keys:
                            placeholders = ','.join('?' for _ in keys)
                            clauses = []
                            for col in ('item_id', 'sku', 'listing_sku', 'listing_listing_id', 'listing_asin'):
                                if col in columns:
                                    clauses.append(f"TRIM(COALESCE({col}, '')) IN ({placeholders})")
                            cur.execute(f"SELECT {', '.join(wanted)} FROM orders WHERE {' OR '.join(clauses)} ORDER BY paid_time DESC LIMIT 500",
                                        tuple(keys) * len(clauses))
                            for r in cur.fetchall():
                                r = _row(r)
                                for col in ('item_id', 'sku', 'listing_sku', 'listing_listing_id', 'listing_asin'):
                                    key = _text(r.get(col))
                                    if key in keys:
                                        sales.setdefault(key, []).append(r)
        except sqlite3.Error:
            pass
        out = []
        for link in links:
            matched = []
            seen = set()
            for value in (link.get('listing_id'), link.get('sku'), link.get('asin')):
                for order in sales.get(_text(value), []):
                    marker = (order.get('order_id'), order.get('item_id'), order.get('sku'))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    matched.append(order)
            entry = dict(link)
            entry['sales'] = matched
            entry['traced'] = [o for o in matched if _text(o.get('source_upc')) == link['upc'] or _text(o.get('listing_trace_id'))]
            out.append(entry)
        return out

    # -- extension update feed -------------------------------------------------------------

    def feed_file(self, filename):
        if not self.feed_dir.is_dir():
            return None
        return send_from_directory(str(self.feed_dir), filename)


FALLBACK_PAGE = '''<!doctype html><meta charset="utf-8"><title>Sweet Shelves Lister</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 24px;line-height:1.6;color:#1f2937}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}</style>
<h1>Sweet Shelves Lister</h1>
<p>No extension release has been published to this server yet.</p>
<p>From the development PC run <code>powershell -File tools/publish-lister.ps1</code>. It packages
<code>lister-extension/</code>, uploads the checksummed release to <code>static/lister/</code> and this page becomes the update page.</p>
'''


def register(app, deps):
    """Wire the Lister routes. deps is a mapping (module globals or a dict) that provides
    db_connection, _safe_error, _listagent_mark_listed, _listagent_upc_variants, _listagent_format_upc12,
    _listagent_init_tables and optionally _listing_helper_scan_cache_clear. The queue-driven panel (0.2) also
    uses api_listingagent_upc_detail, _agent_build_proposal, _listagent_remove_from_queue,
    _listing_center_mark_bol_listed, _listagent_add_photo, update_data_version and BASE_DIR when present."""
    lister = Lister(deps, app.static_folder, app)

    def base_url():
        try:
            root = (request.url_root or '').strip() or 'http://localhost/'
            # Behind Cloudflare gunicorn is spoken to over plain HTTP, so url_root says http://.
            # Links we hand to a phone (QR code, Telegram) are opened cold and must not need a redirect.
            proto = _text(request.headers.get('X-Forwarded-Proto')).split(',')[0].strip().lower()
            if proto == 'https' and root.startswith('http://'):
                root = 'https://' + root[len('http://'):]
            return root
        except Exception:
            return 'http://localhost/'

    def actor():
        data = request.get_json(silent=True) if request.method in ('POST', 'DELETE') else None
        value = _text((data or {}).get('actor') or (data or {}).get('reviewed_by') or request.args.get('actor'), 80)
        return value or _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120) or 'lister'

    def signed_in_email():
        return _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120).lower()

    def guard_mutation():
        if request.headers.get('Sec-Fetch-Site') == 'cross-site' and not request.headers.get(MUTATION_HEADER):
            raise ListerError('Use the Sweet Shelves Lister extension or a Sweet Shelves page for this action.', 403)

    def failure(e, context):
        if isinstance(e, ListerError):
            return jsonify({'success': False, 'error': str(e)}), e.status
        return jsonify({'success': False, 'error': lister.safe_error(e, context)}), 500

    def lister_feed_index():
        response = lister.feed_file('index.html') if (lister.feed_dir / 'index.html').is_file() else None
        if response is None:
            return FALLBACK_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}
        return response

    def lister_feed_file(filename):
        response = lister.feed_file(filename)
        if response is None:
            return jsonify({'success': False, 'error': 'No release published'}), 404
        if filename.startswith('releases/'):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            response.headers['Cache-Control'] = 'no-store'
        return response

    def api_lister_ping():
        return jsonify({'success': True, 'version': VERSION, 'server_time': _now(),
                        'user': _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120),
                        'mutation_header': MUTATION_HEADER})

    def api_lister_items():
        try:
            items, counts = lister.items(scope=request.args.get('scope') or 'selected',
                                         platform=_text(request.args.get('platform')).lower(), base_url=base_url())
            return jsonify({'success': True, 'items': items, 'counts': counts, 'version': VERSION})
        except Exception as e:
            return failure(e, 'lister:items')

    def api_lister_select():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            ids = data.get('proposal_ids')
            if ids is None and data.get('proposal_id') is not None:
                ids = [data.get('proposal_id')]
            result = lister.select(ids, selected=bool(data.get('selected', True)), actor=actor(),
                                   platform_hint=_text(data.get('platform')).lower())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:select')

    def api_lister_links_get():
        try:
            links = lister.links(upc=_text(request.args.get('upc')), platform=_text(request.args.get('platform')).lower(),
                                 listing_id=_text(request.args.get('listing_id')), sku=_text(request.args.get('sku')),
                                 asin=_text(request.args.get('asin')), kind=_text(request.args.get('kind')).lower(),
                                 limit=request.args.get('limit') or 100)
            return jsonify({'success': True, 'links': links})
        except Exception as e:
            return failure(e, 'lister:links')

    def api_lister_links_post():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.record_link(data, actor=actor(), base_url=base_url())
            return jsonify({'success': True, **result}), (200 if result.get('duplicate') else 201)
        except Exception as e:
            return failure(e, 'lister:link')

    def api_lister_link_delete(link_id):
        try:
            guard_mutation()
            link = lister.delete_link(link_id, actor=actor())
            return jsonify({'success': True, 'removed': link})
        except Exception as e:
            return failure(e, 'lister:unlink')

    def api_lister_resolve():
        try:
            result = lister.resolve(platform=_text(request.args.get('platform')).lower(), sku=_text(request.args.get('sku')),
                                    listing_id=_text(request.args.get('listing_id')), asin=_text(request.args.get('asin')),
                                    upc=_text(request.args.get('upc')))
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:resolve')

    def api_lister_events():
        """The panel reports fills so the proposal timeline shows what reached the store page."""
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            try:
                proposal_id = int(data.get('proposal_id'))
            except (TypeError, ValueError):
                raise ListerError('proposal_id is required')
            event = _text(data.get('event'), 40)
            if event not in ('helper_filled', 'helper_opened', 'helper_fill_failed'):
                raise ListerError('unknown event')
            with lister.db('listagent.db') as conn:
                cur = conn.cursor()
                lister.init_tables(cur)
                if not lister._has_table(cur, 'listing_proposals'):
                    raise ListerError('proposal not found', 404)
                cur.execute('SELECT upc FROM listing_proposals WHERE id = ?', (proposal_id,))
                row = cur.fetchone()
                if not row:
                    raise ListerError('proposal not found', 404)
                payload = data.get('payload') if isinstance(data.get('payload'), dict) else None
                lister._event(cur, proposal_id, row['upc'], event, actor(), _text(data.get('note'), 300), payload)
                conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            return failure(e, 'lister:event')

    # -- queue-driven panel (0.2) ------------------------------------------------------------------

    def api_lister_queue():
        try:
            result = lister.queue(platform=_text(request.args.get('platform') or 'ebay').lower(), base_url=base_url())
            return jsonify({'success': True, 'version': VERSION, **result})
        except Exception as e:
            return failure(e, 'lister:queue')

    def api_lister_queue_detail(upc):
        try:
            return jsonify({'success': True, 'item': lister.detail(upc, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:detail')

    def api_lister_queue_prepare(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.prepare(upc, base_url=base_url(), force=bool(data.get('force')), actor=actor())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:prepare')

    def api_lister_queue_skip(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            platform = _text(data.get('platform')).lower()
            if data.get('undo'):
                result = lister.unskip(upc, platform=platform)
            else:
                result = lister.skip(upc, platform=platform, actor=actor())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:skip')

    def api_lister_panel():
        try:
            email = signed_in_email()
            if request.method == 'POST':
                guard_mutation()
                data = request.get_json(silent=True) or {}
                return jsonify({'success': True, **lister.panel_report(
                    email, platform=_text(data.get('platform')).lower(), follow=data.get('follow', True) is not False,
                    open_=data.get('open', True) is not False)})
            response = jsonify({'success': True, **lister.panel_state(email)})
            response.headers['Cache-Control'] = 'no-store'
            return response
        except Exception as e:
            return failure(e, 'lister:panel')

    def api_lister_queue_stores():
        try:
            data = request.get_json(silent=True) or {}
            upcs = data.get('upcs') or []
            if not isinstance(upcs, list):
                raise ListerError('upcs must be a list')
            return jsonify({'success': True, 'items': lister.store_states(upcs)})
        except Exception as e:
            return failure(e, 'lister:stores')

    def api_lister_queue_store(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            state = lister.set_store(upc, platform=_text(data.get('platform')).lower(), on=bool(data.get('on')),
                                     only=bool(data.get('only')), fresh=bool(data.get('fresh')), actor=actor())
            return jsonify({'success': True, 'upc': _text(upc), 'state': state})
        except Exception as e:
            return failure(e, 'lister:store')

    def api_lister_queue_mark_existing(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.mark_existing(upc, platform=_text(data.get('platform')).lower(), entry=data.get('entry') or {},
                                          actor=actor(), base_url=base_url())
            return jsonify({'success': True, **result}), (200 if result.get('duplicate') else 201)
        except Exception as e:
            return failure(e, 'lister:mark-existing')

    def api_lister_queue_amazon_check(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.amazon_check(upc, force=bool(data.get('force')), condition_type=_text(data.get('conditionType'), 40))
            return jsonify({'success': True, 'check': result})
        except Exception as e:
            return failure(e, 'lister:amazon-check')

    def api_lister_queue_preload(upc):
        try:
            if request.method == 'GET':
                return jsonify({'success': True, 'preload': lister.preload_status(upc)})
            guard_mutation()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, 'preload': lister.preload(upc, steps=data.get('steps'), base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:preload')

    def api_lister_preload_all():
        try:
            if request.method == 'GET':
                return jsonify({'success': True, **lister.preload_all_status()})
            guard_mutation()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **lister.preload_all(data.get('upcs') or [], steps=data.get('steps'), base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:preload-all')

    def api_lister_queue_generate(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.generate(upc, kind=_text(data.get('kind')).lower(), values=data.get('values') or {}, base_url=base_url(), auto=bool(data.get('auto')),
                                      instructions=data.get('instructions') or '')
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:generate')

    def api_lister_learn():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **lister.learn(data, actor=actor())}), 201
        except Exception as e:
            return failure(e, 'lister:learn')

    def api_lister_voice_analyze(media_id):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, 'analysis': lister.analyze_voice(media_id, reanalyze=bool(data.get('reanalyze')))})
        except Exception as e:
            return failure(e, 'lister:voice')

    def api_lister_photo_ai():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.ai_photo(_text(data.get('upc')), url=_text(data.get('url'), 1000), prompt=data.get('prompt') or '',
                                     base_url=base_url(), quality=_text(data.get('quality')).lower() or 'medium')
            return jsonify({'success': True, **result}), 201
        except Exception as e:
            return failure(e, 'lister:photo-ai')

    def api_lister_photo_fetch():
        """The panel drags our photos onto store pages; the bytes come from here (same-origin safe)."""
        try:
            data, mime, name = lister.photo_bytes(_text(request.args.get('url'), 1000), base_url())
            return jsonify({'success': True, 'name': name, 'mime': mime, 'base64': base64.b64encode(data).decode('ascii')})
        except Exception as e:
            return failure(e, 'lister:photo-fetch')

    def api_lister_photo_link():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.photo_link(_text(data.get('upc')), base_url=base_url(), to_email=signed_in_email())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:photo-link')

    def api_lister_photo_link_opened():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **lister.photo_link_opened(_text(data.get('token')))})
        except Exception as e:
            return failure(e, 'lister:photo-link-opened')

    def api_lister_qr():
        text = _text(request.args.get('text'), 1000)
        if not text:
            return jsonify({'success': False, 'error': 'text is required'}), 400
        svg = qr_svg(text)
        if not svg:
            return jsonify({'success': False, 'error': 'QR codes need the qrcode package on the server.'}), 501
        return svg, 200, {'Content-Type': 'image/svg+xml; charset=utf-8', 'Cache-Control': 'no-store'}

    def api_lister_ledger():
        try:
            rows = lister.ledger(upc=_text(request.args.get('upc')), platform=_text(request.args.get('platform')).lower(),
                                 limit=request.args.get('limit') or 200)
            return jsonify({'success': True, 'links': rows})
        except Exception as e:
            return failure(e, 'lister:ledger')

    def lister_ledger_page():
        return render_template('lister_ledger.html')

    app.add_url_rule('/api/lister/queue', 'api_lister_queue', api_lister_queue)
    app.add_url_rule('/api/lister/queue/<upc>', 'api_lister_queue_detail', api_lister_queue_detail)
    app.add_url_rule('/api/lister/queue/<upc>/prepare', 'api_lister_queue_prepare', api_lister_queue_prepare, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/skip', 'api_lister_queue_skip', api_lister_queue_skip, methods=['POST'])
    app.add_url_rule('/api/lister/panel', 'api_lister_panel', api_lister_panel, methods=['GET', 'POST'])
    app.add_url_rule('/api/lister/queue-stores', 'api_lister_queue_stores', api_lister_queue_stores, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/store', 'api_lister_queue_store', api_lister_queue_store, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/mark-existing', 'api_lister_queue_mark_existing', api_lister_queue_mark_existing, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/generate', 'api_lister_queue_generate', api_lister_queue_generate, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/preload', 'api_lister_queue_preload', api_lister_queue_preload, methods=['GET', 'POST'])
    app.add_url_rule('/api/lister/preload', 'api_lister_preload_all', api_lister_preload_all, methods=['GET', 'POST'])
    app.add_url_rule('/api/lister/queue/<upc>/amazon-check', 'api_lister_queue_amazon_check', api_lister_queue_amazon_check, methods=['POST'])
    app.add_url_rule('/api/lister/learn', 'api_lister_learn', api_lister_learn, methods=['POST'])
    app.add_url_rule('/api/lister/voice/<int:media_id>/analyze', 'api_lister_voice_analyze', api_lister_voice_analyze, methods=['POST'])
    app.add_url_rule('/api/lister/photos/ai', 'api_lister_photo_ai', api_lister_photo_ai, methods=['POST'])
    app.add_url_rule('/api/lister/photos/fetch', 'api_lister_photo_fetch', api_lister_photo_fetch)
    app.add_url_rule('/api/lister/qr', 'api_lister_qr', api_lister_qr)
    app.add_url_rule('/api/lister/photo-link', 'api_lister_photo_link', api_lister_photo_link, methods=['POST'])
    app.add_url_rule('/api/lister/photo-link/opened', 'api_lister_photo_link_opened', api_lister_photo_link_opened, methods=['POST'])
    app.add_url_rule('/api/lister/ledger', 'api_lister_ledger', api_lister_ledger)
    app.add_url_rule('/lister-ledger', 'lister_ledger_page', lister_ledger_page)

    app.add_url_rule('/lister/', 'lister_feed_index', lister_feed_index)
    app.add_url_rule('/lister/<path:filename>', 'lister_feed_file', lister_feed_file)
    app.add_url_rule('/api/lister/ping', 'api_lister_ping', api_lister_ping)
    app.add_url_rule('/api/lister/items', 'api_lister_items', api_lister_items)
    app.add_url_rule('/api/lister/select', 'api_lister_select', api_lister_select, methods=['POST'])
    app.add_url_rule('/api/lister/links', 'api_lister_links_get', api_lister_links_get)
    app.add_url_rule('/api/lister/links', 'api_lister_links_post', api_lister_links_post, methods=['POST'])
    app.add_url_rule('/api/lister/links/<int:link_id>', 'api_lister_link_delete', api_lister_link_delete, methods=['DELETE'])
    app.add_url_rule('/api/lister/resolve', 'api_lister_resolve', api_lister_resolve)
    app.add_url_rule('/api/lister/events', 'api_lister_events', api_lister_events, methods=['POST'])
    import lister_new_item
    lister_new_item.register(app, lister, deps)
    import lister_stats
    lister_stats.register(app, lister)
    return lister
