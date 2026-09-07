"""Native eBay listing previews. Network transport is injected for offline tests."""
import hashlib
import ipaddress
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

SCOPE = 'https://api.ebay.com/oauth/api_scope/sell.inventory.mapping'
ENDPOINT = 'https://graphqlapi.ebay.com/graphql'
START = '''mutation Start($input: StartListingPreviewsCreationInput!) {
 startListingPreviewsCreation(input: $input) {
  errors { errorDescription } listingPreviewsCreationTask { id }
 }
}'''
POLL = '''query Poll($input: ListingPreviewsCreationTaskByIdInput!) {
 listingPreviewsCreationTaskById(input: $input) {
  listingPreviewsCreationTask { id result {
   completionStatus
   listingPreviews { mappingReferenceId title description
    category { categoryId name } product { epid }
    aspects { name values { value confidence } }
   }
   invalidProducts { description }
   unmappedProducts { title } unprocessedProducts { title }
  } }
 }
}'''


class MappingError(ValueError):
    pass


def public_https(url):
    try:
        p = urlparse(url)
        host = (p.hostname or '').lower()
        if p.scheme != 'https' or not host or p.username or p.password or p.port not in (None, 443):
            return False
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except (ValueError, TypeError):
        return False


def recommendation_gtin(value):
    """Strip warehouse unit suffixes for product lookup only, never item identity."""
    match = re.fullmatch(r'(\d{8,14})(?:-[A-Za-z0-9][A-Za-z0-9_-]*)?', str(value or '').strip())
    if not match:
        return ''
    base = match.group(1)
    return base.zfill(12) if len(base) == 11 else base


def product_input(data):
    if data.get('marketplaceId', 'EBAY_US') != 'EBAY_US':
        raise MappingError('eBay recommendations currently support the US marketplace only.')
    title = str(data.get('title') or '').strip()[:250]
    images = list(dict.fromkeys(u for u in (data.get('images') or []) if isinstance(u, str) and public_https(u)))[:12]
    if not title and not images:
        raise MappingError('Add a product title or a publicly accessible HTTPS photo first.')
    product = {'title': title, 'images': images}
    if not title:
        product.pop('title')
    upc = recommendation_gtin(data.get('upc'))
    if re.fullmatch(r'\d{12}', upc):
        product['externalProductIdentifierInput'] = {'productId': upc, 'productType': 'UPC'}
    aspects = data.get('aspects') or {}
    if isinstance(aspects, dict):
        product['aspects'] = [{'name': str(k)[:65], 'values': [str(v)[:100] for v in (vs if isinstance(vs, list) else [vs]) if str(v).strip()][:20]}
                              for k, vs in list(aspects.items())[:50] if k and vs]
    return product, len(data.get('images') or []) - len(images)


class SafeDescription(HTMLParser):
    tags = {'p', 'br', 'ul', 'ol', 'li', 'b', 'strong', 'i', 'em', 'h2', 'h3', 'h4'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.suppressed = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.suppressed += 1
        elif not self.suppressed and tag in self.tags:
            self.output.append('<' + tag + '>')
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in self.tags and tag != 'br':
            self.output.append('</' + tag + '>')
    def handle_data(self, text):
        if not self.suppressed:
            self.output.append(escape(text))


def clean_description(value):
    parser = SafeDescription()
    parser.feed(str(value or '')[:50000])
    return ''.join(parser.output)


def error_details(errors, token=''):
    """Expose useful API diagnostics, never headers, tokens or whole responses."""
    details = []
    for error in (errors if isinstance(errors, list) else [errors])[:3]:
        if not isinstance(error, dict):
            continue
        extensions = error.get('extensions') or {}
        if not isinstance(extensions, dict):
            extensions = {}
        code = next((str(value) for value in (
            extensions.get('code'), extensions.get('errorId'), extensions.get('errorCode'),
            error.get('errorId'), error.get('code')) if value is not None), '')
        message = str(error.get('message') or error.get('errorDescription') or '')
        text = (('[' + code + '] ') if code else '') + message
        if token:
            text = text.replace(token, '[redacted]')
        text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [redacted]', text)
        text = re.sub(r'''(?i)((?:access_token|refresh_token|client_secret|authorization)["']?\s*[:=]\s*["']?)[^\s,"'}]+''', r'\1[redacted]', text)
        text = ' '.join(text.split())[:500]
        if text:
            details.append(text)
    return '; '.join(details)


def graphql(post, token, query, variables):
    response = post(ENDPOINT, headers={'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json', 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US'},
                    json={'query': query, 'variables': variables}, timeout=30)
    if response.status_code in (401, 403):
        raise MappingError('Reconnect eBay with the Inventory Mapping permission. If access is still denied, enable API access in your eBay developer account. Similar-listing search is still available.')
    if response.status_code == 429:
        raise MappingError('eBay is limiting recommendation requests. Try again later or use similar listings.')
    if response.status_code >= 500:
        raise MappingError('eBay recommendations are temporarily unavailable. Try again later or use similar listings.')
    try:
        body = response.json()
    except (ValueError, TypeError):
        raise MappingError('eBay returned an unreadable recommendation response. Try again later.')
    if not isinstance(body, dict):
        raise MappingError('eBay returned an unexpected recommendation response. Try again later.')
    if body.get('errors'):
        detail = error_details(body['errors'], token)
        stage = 'starting recommendations' if query == START else 'loading recommendations'
        raise MappingError(f'eBay rejected the request while {stage}: ' + (detail or 'No error details were provided.'))
    if response.status_code >= 400:
        raise MappingError(f'eBay rejected the recommendation request (HTTP {response.status_code}).')
    return body.get('data') or {}


def start(post, token, product):
    result = graphql(post, token, START, {'input': {'externalProducts': [product]}}).get('startListingPreviewsCreation') or {}
    if result.get('errors'):
        raise MappingError('eBay rejected the product details: ' + (error_details(result['errors'], token) or 'Check the title, photos and barcode, then try again.'))
    task_id = (result.get('listingPreviewsCreationTask') or {}).get('id')
    if not task_id:
        raise MappingError('eBay did not return a recommendation task. Please try again.')
    return str(task_id)


def poll(post, token, task_id):
    output = graphql(post, token, POLL, {'input': {'id': task_id}}).get('listingPreviewsCreationTaskById') or {}
    task = output.get('listingPreviewsCreationTask')
    if not task:
        raise MappingError('eBay could not find this recommendation task. Request fresh recommendations.')
    result = task.get('result')
    if not result:
        return {'status': 'processing', 'previews': []}
    previews = []
    for p in result.get('listingPreviews') or []:
        reference = str(p.get('mappingReferenceId') or '')
        if not reference or len(reference) > 25:
            continue  # Cannot publish a mapped preview without its source reference.
        aspects, hints = {}, {}
        for aspect in p.get('aspects') or []:
            name = aspect.get('name')
            if not name:
                continue
            for v in aspect.get('values') or []:
                target = aspects if v.get('confidence') == 'PREFILL' else hints
                if v.get('value'):
                    target.setdefault(name, []).append(str(v['value']))
        previews.append({'mappingReferenceId': reference, 'title': str(p.get('title') or '')[:80],
                         'description': clean_description(p.get('description')),
                         'categoryId': str((p.get('category') or {}).get('categoryId') or ''),
                         'categoryName': str((p.get('category') or {}).get('name') or ''),
                         'epid': str((p.get('product') or {}).get('epid') or ''),
                         'aspects': aspects, 'hints': hints})
    return {'status': 'ready' if previews else 'no_match', 'previews': previews,
            'completionStatus': result.get('completionStatus'),
            'retryable': bool(result.get('unprocessedProducts')),
            'message': '' if previews else 'eBay could not create a usable preview. Refine the title or photos, or choose a similar listing.'}


CONDITIONS = dict(zip(['NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'CERTIFIED_REFURBISHED',
                      'EXCELLENT_REFURBISHED', 'VERY_GOOD_REFURBISHED', 'GOOD_REFURBISHED',
                      'SELLER_REFURBISHED', 'LIKE_NEW', 'USED_EXCELLENT', 'USED_VERY_GOOD',
                      'USED_GOOD', 'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING'],
                     [1000,1500,1750,2000,2010,2020,2030,2500,2750,3000,4000,5000,6000,7000]))


def listing_xml(data, inventory, offer, address, reference, request_id, *, verify=False):
    """Only new US fixed-price listings; existing Inventory API offers stay on their original path."""
    if data.get('offerId') or offer.get('marketplaceId') != 'EBAY_US':
        raise MappingError('Native eBay recommendations are supported for new US listings. Existing offers must be edited through their original listing flow.')
    try:
        price = Decimal(str(data.get('price')))
        quantity = Decimal(str(data.get('quantity')))
        if not price.is_finite() or price <= 0 or not quantity.is_finite() or quantity < 1 or quantity != int(quantity):
            raise ValueError()
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise MappingError('Enter a positive price and a whole-number quantity.')
    if not data.get('title') or len(data['title']) > 80 or not offer.get('categoryId') or not offer.get('listingDescription'):
        raise MappingError('Title, category and description are required.')
    if inventory.get('condition') not in CONDITIONS:
        raise MappingError('Choose a supported item condition.')
    if address.get('country') != 'US' or not (address.get('postalCode') or address.get('city')):
        raise MappingError('Set a US item location with a postal code or city in eBay settings.')
    images = (inventory.get('product') or {}).get('imageUrls') or []
    if not images:
        raise MappingError('Add an item photo before publishing.')
    call = 'VerifyAddFixedPriceItem' if verify else 'AddFixedPriceItem'
    root = ET.Element(call + 'Request', xmlns='urn:ebay:apis:eBLBaseComponents')
    def add(parent, name, value):
        node = ET.SubElement(parent, name)
        node.text = str(value)
        return node
    add(root, 'ErrorLanguage', 'en_US')
    add(root, 'WarningLevel', 'High')
    item = ET.SubElement(root, 'Item')
    for name, value in {'Title':data['title'], 'Description':offer['listingDescription'],
                        'SKU':data['sku'], 'ListingType':'FixedPriceItem', 'ListingDuration':'GTC',
                        'Currency':'USD', 'Country':'US', 'Quantity':int(quantity),
                        'StartPrice':format(price, '.2f'), 'ConditionID':CONDITIONS[inventory['condition']],
                        'MappingReferenceId':reference, 'InventoryTrackingMethod':'SKU',
                        'UUID':request_id, 'Site':'US'}.items():
        add(item, name, value)
    add(ET.SubElement(item, 'PrimaryCategory'), 'CategoryID', offer['categoryId'])
    if address.get('postalCode'):
        add(item, 'PostalCode', address['postalCode'])
    if address.get('city'):
        add(item, 'Location', address['city'])
    if inventory.get('conditionDescription'):
        add(item, 'ConditionDescription', inventory['conditionDescription'])
    pictures = ET.SubElement(item, 'PictureDetails')
    for url in images[:12]:
        add(pictures, 'PictureURL', url)
    product = inventory.get('product') or {}
    identifiers = product.get('upc') or []
    if identifiers and re.fullmatch(r'\d{12}', str(identifiers[0])):
        add(ET.SubElement(item, 'ProductListingDetails'), 'UPC', identifiers[0])
    specifics = ET.SubElement(item, 'ItemSpecifics')
    for name, values in (inventory.get('product', {}).get('aspects') or {}).items():
        pair = ET.SubElement(specifics, 'NameValueList')
        add(pair, 'Name', name)
        for value in values:
            add(pair, 'Value', value)
    profiles = ET.SubElement(item, 'SellerProfiles')
    for kind, field in [('Shipping', 'fulfillmentPolicyId'), ('Payment', 'paymentPolicyId'), ('Return', 'returnPolicyId')]:
        value = offer.get('listingPolicies', {}).get(field)
        if not value:
            raise MappingError('Select shipping, payment and return policies before publishing.')
        add(ET.SubElement(profiles, 'Seller' + kind + 'Profile'), kind + 'ProfileID', value)
    return call, ET.tostring(root, encoding='unicode')


def trading(post, auth_token, call, xml):
    # Existing application Trading authorization is separate from Inventory OAuth.
    if not auth_token:
        raise MappingError('The eBay Trading connection is required to publish native recommendations. Configure EBAY_OLDAUTH_TOKEN on the server.')
    root = ET.fromstring(xml)
    ns = '{urn:ebay:apis:eBLBaseComponents}'
    credentials = ET.SubElement(root, ns + 'RequesterCredentials')
    ET.SubElement(credentials, ns + 'eBayAuthToken').text = auth_token
    response = post('https://api.ebay.com/ws/api.dll', headers={
        'X-EBAY-API-CALL-NAME':call, 'X-EBAY-API-SITEID':'0',
        'X-EBAY-API-COMPATIBILITY-LEVEL':'1423', 'Content-Type':'text/xml'},
        data=ET.tostring(root, encoding='utf-8'), timeout=45)
    if response.status_code >= 400:
        raise MappingError('eBay could not confirm the listing request. Retry with this same draft; its request identifier is retained.')
    result = ET.fromstring(response.content)
    if result.findtext(ns + 'Ack') not in ('Success', 'Warning'):
        errors = [e.findtext(ns + 'LongMessage') or e.findtext(ns + 'ShortMessage') or 'Listing validation failed'
                  for e in result.findall(ns + 'Errors') if e.findtext(ns + 'SeverityCode') != 'Warning']
        raise MappingError('; '.join(errors)[:1200] or 'eBay listing validation failed.')
    return {'listingId': result.findtext(ns + 'ItemID'), 'warnings':[
        e.findtext(ns + 'LongMessage') for e in result.findall(ns + 'Errors')]}


def fingerprint(product):
    return hashlib.sha256(json.dumps(product, sort_keys=True).encode()).hexdigest()


def request_uuid(upc, sku):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'sweetshelves:mapped:' + upc + ':' + sku).hex.upper()
