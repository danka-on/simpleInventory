"""Ebay orders for Sweet Shelves."""

import datetime
import os
import requests
import sqlite3
import xml.etree.ElementTree as ET
from DBmanager import connect_db, ebayStoreDB, store_ebay_order
from token_manager import get_access_token
from . import warehouse_receiving as ss_warehouse_receiving


_EBAY_CONDITION_ID_LABELS = {
    '1000': 'New',
    '1500': 'New other',
    '1750': 'New with defects',
    '2000': 'Certified refurbished',
    '2010': 'Excellent refurbished',
    '2020': 'Very good refurbished',
    '2030': 'Good refurbished',
    '2500': 'Seller refurbished',
    '2750': 'Like new',
    '3000': 'Used',
    '4000': 'Used - Very Good',
    '5000': 'Used - Good',
    '6000': 'Used - Acceptable',
    '7000': 'For parts or not working',
}


def _ebay_item_text_details(item_node, namespace):
    """Extract listing condition and descriptions from a full eBay Item payload."""
    def _text(path):
        node = item_node.find(path, namespace)
        return str(node.text or '').strip() if node is not None else ''

    condition_id = _text('.//ebay:ConditionID')
    condition_display = _text('.//ebay:ConditionDisplayName')
    return {
        'condition': condition_display or _EBAY_CONDITION_ID_LABELS.get(condition_id, condition_id),
        'condition_description': _text('.//ebay:ConditionDescription'),
        'description': _text('.//ebay:Description'),
    }


def orders():
    print("✅ Now running orders()...")
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
    }
    entries = 100
    page_number = 1
    total_pages = 20
    count = 0
    active_item_ids_seen = set()
    pages_processed = 0
    pages_successful = 0
    # GetMyeBaySelling no longer returns condition fields, so enrichment is
    # completed through one-time GetItem lookups. Keep explicit processed flags
    # so categories without a UPC or condition do not trigger calls forever.
    conn = None
    try:
        conn = sqlite3.connect('ebayStore.db')
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(INVENTORY)")
        columns = {str(row[1]).lower() for row in cur.fetchall()}
        enrichment_columns = {
            'upc': 'UPC TEXT',
            'upc_processed': 'UPC_Processed INTEGER DEFAULT 0',
            'condition': 'Condition TEXT',
            'conditiondescription': 'ConditionDescription TEXT',
            'description': 'Description TEXT',
            'condition_processed': 'Condition_Processed INTEGER DEFAULT 0',
        }
        for normalized_name, definition in enrichment_columns.items():
            if normalized_name not in columns:
                cur.execute(f'ALTER TABLE INVENTORY ADD COLUMN {definition}')
        conn.commit()
    except Exception as alter_e:
        print(f"Failed to ensure eBay enrichment columns exist: {alter_e}")
    finally:
        if conn is not None:
            conn.close()
    processed_detail_ids = set()
    while page_number <= total_pages:

        xml_payload = f'''
        <?xml version="1.0" encoding="utf-8"?>
            <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
              <RequesterCredentials>
                <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
              </RequesterCredentials>
              <DetailLevel>ReturnAll</DetailLevel>
              <ErrorLanguage>en_US</ErrorLanguage>
              <WarningLevel>High</WarningLevel>
              <ActiveList>
                <Sort>TimeLeft</Sort>
                <Pagination>
                  <EntriesPerPage>{entries}</EntriesPerPage>
                  <PageNumber>{page_number}</PageNumber>
                </Pagination>
              </ActiveList>
            <SoldList>
                <Include>true</Include>
                <DurationInDays>60</DurationInDays>
                    <Pagination>
                  <EntriesPerPage>{entries}</EntriesPerPage>
                  <PageNumber>{page_number}</PageNumber>
                </Pagination>
            </SoldList>
            <UnsoldList>
                <Include>true</Include>
                <Pagination>
                      <EntriesPerPage>{entries}</EntriesPerPage>
                      <PageNumber>{page_number}</PageNumber>
                    </Pagination>
                    
                </UnsoldList>
            
            </GetMyeBaySellingRequest>'''

        ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
        response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=20)
        root = ET.fromstring(response.text)

        '''
        rough_string = ET.tostring(root, encoding="utf-8")
        # Parse that into a minidom object
        dom = minidom.parseString(rough_string)
        # Pretty print
        pretty_xml = dom.toprettyxml(indent="  ")
        print(pretty_xml)
        '''



        ack = root.find('ebay:Ack', ns)
        ack_text = (ack.text or '').strip() if ack is not None else ''
        if ack_text not in ("Success", "Warning"):
            print(f"❌ API Error on page {page_number}: {ack_text if ack_text else 'No Ack'}")
        else:
            pages_successful += 1
        pages_processed += 1






        def _process_ebay_items(items, list_state):
            """Parse eBay XML items, store in DB, and print debug info."""
            nonlocal count

            def _parse_int_text(node):
                if node is None or node.text is None:
                    return None
                txt = str(node.text).strip()
                if not txt:
                    return None
                try:
                    return int(float(txt))
                except Exception:
                    return None

            for item in items:
                title = item.find('ebay:Title', ns)
                item_id = item.find('ebay:ItemID', ns)
                item_id_text = item_id.text if item_id is not None else "N/A"
                sku = item.find('ebay:SKU', ns)
                price = item.find('.//ebay:CurrentPrice', ns)
                quantity = item.find('ebay:Quantity', ns)
                quantity_available = item.find('ebay:QuantityAvailable', ns)
                quantity_sold = item.find('.//ebay:SellingStatus/ebay:QuantitySold', ns)
                list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
                sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
                URL = f"https://www.ebay.com/itm/{item_id_text}"
                picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
                high_res_url = ss_warehouse_receiving.get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"
                item_text_details = _ebay_item_text_details(item, ns)
                condition_text = item_text_details['condition']
                condition_description_text = item_text_details['condition_description']
                description_text = item_text_details['description']

                qty_total = _parse_int_text(quantity)
                qty_available = _parse_int_text(quantity_available)
                qty_sold = _parse_int_text(quantity_sold)
                qty_to_store = None

                if qty_available is not None:
                    qty_to_store = max(0, qty_available)
                elif list_state == "Active" and qty_total is not None and qty_sold is not None:
                    qty_to_store = max(0, qty_total - qty_sold)
                elif qty_total is not None:
                    qty_to_store = max(0, qty_total)

                qty_text = str(qty_to_store) if qty_to_store is not None else "N/A"

                ebayStoreDB(title=title.text if title is not None else "N/A",
                           item_id=item_id_text,
                           sku=sku.text if sku is not None else "None",
                           price=price.text if price is not None else "N/A",
                           quantity=qty_text,
                           image=high_res_url,
                           List_State=list_state,
                           Sold_Date=sold_date.text if sold_date is not None else "None",
                           List_Date=list_date.text if list_date is not None else "None",
                           URL=URL if URL is not None else "None",
                           condition=condition_text,
                           description=description_text,
                           condition_description=condition_description_text)

                if list_state == "Active" and item_id_text not in [None, "", "N/A", "None"]:
                    active_item_ids_seen.add(item_id_text)

                print("Item", count)
                print("📦 Title:", title.text if title is not None else "N/A")
                print("🆔 ItemID:", item_id.text if item_id is not None else "N/A")
                print("🔖 SKU:", sku.text if sku is not None else "None")
                print("💲 Price:", price.text if price is not None else "N/A")
                print("🔢 Quantity:", qty_text)
                print("🖼️ Image:", high_res_url)
                print("🛣️ URL:", f"https://www.ebay.com/itm/{item_id.text}")
                print("—" * 40)
                count += 1

        #ACTIVE LIST ITEMS
        ActiveItems = root.findall('.//ebay:ActiveList/ebay:ItemArray/ebay:Item', ns)
        _process_ebay_items(ActiveItems, "Active")

        # finding unsold item list
        UnsoldItems = root.findall('.//ebay:UnsoldList/ebay:ItemArray/ebay:Item', ns)
        _process_ebay_items(UnsoldItems, "Unsold")

        # finding sold list items
        SoldItems = root.findall('.//ebay:SoldList/ebay:OrderTransactionArray/ebay:OrderTransaction/ebay:Transaction/ebay:Item', ns)
        _process_ebay_items(SoldItems, "Sold")

        # Collect all item IDs from Active, Unsold, and Sold lists
        all_item_ids = set()
        for item in ActiveItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)
        for item in UnsoldItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)
        for item in SoldItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)

        # GetItem still returns the listing condition. Use it only for rows that
        # have not already completed their UPC/condition enrichment.
        to_lookup = []
        try:
            with connect_db('ebayStore.db') as lookup_conn:
                lookup_cur = lookup_conn.cursor()
                for eid in all_item_ids:
                    if eid in processed_detail_ids:
                        continue
                    lookup_cur.execute('''
                        SELECT UPC, UPC_Processed, Condition, Condition_Processed
                        FROM INVENTORY
                        WHERE ItemID = ?
                        LIMIT 1
                    ''', (eid,))
                    detail_row = lookup_cur.fetchone()
                    if not detail_row:
                        continue
                    stored_upc, upc_processed, stored_condition, condition_processed = detail_row
                    missing_upc = not str(stored_upc or '').strip() or str(stored_upc or '').strip().lower() == 'null'
                    missing_condition = not str(stored_condition or '').strip()
                    if ((missing_upc and not int(upc_processed or 0))
                            or (missing_condition and not int(condition_processed or 0))):
                        to_lookup.append(eid)
        except Exception as lookup_e:
            print(f"Failed to identify eBay listings needing details: {lookup_e}")

        if to_lookup:
            with connect_db('ebayStore.db') as upc_conn:
                upc_cur = upc_conn.cursor()
                for eid in to_lookup:
                    getitem_xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{eid}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>'''
                    getitem_headers = headers.copy()
                    getitem_headers["X-EBAY-API-CALL-NAME"] = "GetItem"
                    try:
                        getitem_resp = requests.post("https://api.ebay.com/ws/api.dll", headers=getitem_headers, data=getitem_xml, timeout=20)
                        getitem_root = ET.fromstring(getitem_resp.text)
                        getitem_ack = getitem_root.find('ebay:Ack', ns)
                        getitem_ack_text = str(getitem_ack.text or '').strip() if getitem_ack is not None else ''
                        if getitem_ack_text not in ('Success', 'Warning'):
                            error_node = getitem_root.find('.//ebay:LongMessage', ns)
                            error_text = str(error_node.text or '').strip() if error_node is not None else 'Unknown eBay error'
                            raise RuntimeError(f'GetItem returned {getitem_ack_text or "no acknowledgement"}: {error_text}')

                        product_details = getitem_root.find('.//{urn:ebay:apis:eBLBaseComponents}ProductListingDetails')
                        upc = None
                        if product_details is not None:
                            upc_elem = product_details.find('{urn:ebay:apis:eBLBaseComponents}UPC')
                            upc = upc_elem.text if upc_elem is not None else None

                        item_text_details = _ebay_item_text_details(getitem_root, ns)
                        condition_text = item_text_details['condition']
                        condition_description = item_text_details['condition_description']
                        description_text = item_text_details['description']

                        upc_cur.execute('''
                            UPDATE INVENTORY
                            SET UPC = COALESCE(NULLIF(?, ''), UPC),
                                UPC_Processed = 1,
                                Condition = COALESCE(NULLIF(?, ''), Condition),
                                ConditionDescription = COALESCE(NULLIF(?, ''), ConditionDescription),
                                Description = COALESCE(NULLIF(?, ''), Description),
                                Condition_Processed = 1
                            WHERE ItemID = ?
                        ''', (upc, condition_text, condition_description, description_text, eid))
                        print(f"Enriched eBay ItemID {eid}: UPC={upc or '-'}, Condition={condition_text or '-'}")
                        processed_detail_ids.add(eid)
                    except Exception as e:
                        print(f"Failed to enrich eBay ItemID {eid}: {e}")

        # Get total pages if first time
        if page_number == 1:
            page_info = root.find('.//ebay:PaginationResult', ns)
            if page_info is not None:
                total_pages = int(page_info.find('ebay:TotalNumberOfPages', ns).text)
            else:
                return
                #break  # no pagination info, likely no results

        page_number += 1

    # Reconcile stale active rows only after a fully successful pass.
    # If an item is not in the latest ActiveList snapshot, it should not remain Active.
    try:
        if total_pages > 0 and pages_processed >= total_pages and pages_successful >= total_pages:
            with sqlite3.connect('ebayStore.db') as _conn:
                _cur = _conn.cursor()
                _cur.execute('''
                    UPDATE INVENTORY
                    SET List_State = 'Unsold'
                    WHERE TRIM(COALESCE(List_State, '')) = 'Active'
                ''')

                if active_item_ids_seen:
                    ids = sorted(active_item_ids_seen)
                    chunk = 800
                    for i in range(0, len(ids), chunk):
                        part = ids[i:i + chunk]
                        placeholders = ','.join('?' for _ in part)
                        _cur.execute(f'''
                            UPDATE INVENTORY
                            SET List_State = 'Active'
                            WHERE ItemID IN ({placeholders})
                        ''', part)
                _conn.commit()
            print(f"✅ Reconciled eBay active states from latest sync snapshot ({len(active_item_ids_seen)} active IDs)")
        else:
            print(f"⚠️ Skipped eBay active-state reconciliation (pages_processed={pages_processed}, pages_successful={pages_successful}, total_pages={total_pages})")
    except Exception as e:
        print(f"⚠️ Failed eBay active-state reconciliation: {e}")


def get_ebay_orders(days=90):
    print(f"Getting eBay orders for the last {days} days via Fulfillment API...")

    def _amount_value(node, default=None):
        if isinstance(node, dict):
            node = node.get('value')
        try:
            return float(node)
        except (TypeError, ValueError):
            return default

    def _pick_contact(order_payload):
        instructions = order_payload.get('fulfillmentStartInstructions') or []
        shipping_step = instructions[0].get('shippingStep') if instructions and isinstance(instructions[0], dict) else {}
        ship_to = shipping_step.get('shipTo') if isinstance(shipping_step, dict) else {}
        if ship_to:
            return ship_to
        buyer = order_payload.get('buyer') or {}
        return buyer.get('buyerRegistrationAddress') or {}

    def _pick_contact_address(contact):
        if not isinstance(contact, dict):
            return {}
        return contact.get('contactAddress') or {}

    def _first_payment_date(order_payload):
        payments = ((order_payload.get('paymentSummary') or {}).get('payments') or [])
        for payment in payments:
            payment_date = str((payment or {}).get('paymentDate') or '').strip()
            if payment_date:
                return payment_date
        return str(order_payload.get('creationDate') or order_payload.get('lastModifiedDate') or '').strip()

    def _estimate_line_fee(line_total, subtotal, total_fee, line_count):
        if total_fee is None:
            return None
        if subtotal and line_total is not None:
            try:
                return round(total_fee * (line_total / subtotal), 4)
            except Exception:
                return None
        if line_count == 1:
            return round(total_fee, 4)
        return None

    days = max(1, min(int(days or 90), 730))
    token = get_access_token()
    if not token:
        raise Exception('Missing eBay OAuth token (run ebay_oauth_setup.py)')

    created_from = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    url = "https://api.ebay.com/sell/fulfillment/v1/order"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    total_orders = 0
    total_lines = 0
    offset = 0
    limit = 200

    while True:
        params = {
            "filter": f"creationdate:[{created_from}..]",
            "limit": limit,
            "offset": offset
        }
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code >= 400:
            body = response.text[:500]
            raise Exception(f"eBay Fulfillment getOrders failed ({response.status_code}): {body}")

        payload = response.json() if response.text else {}
        orders = payload.get('orders') or []
        if not orders:
            break

        total_orders += len(orders)
        print(f"Retrieved {len(orders)} eBay order(s) at offset {offset}")

        for order in orders:
            order_id = str(order.get('orderId') or order.get('legacyOrderId') or '').strip()
            if not order_id:
                continue

            contact = _pick_contact(order)
            address = _pick_contact_address(contact)
            payment_date = _first_payment_date(order)
            order_status = str(order.get('orderPaymentStatus') or '').strip()
            fulfillment_status = str(order.get('orderFulfillmentStatus') or '').strip()
            shipped_time = str(order.get('lastModifiedDate') or '').strip() if fulfillment_status == 'FULFILLED' else None

            order_total_fee = _amount_value(order.get('totalMarketplaceFee'))
            order_subtotal = _amount_value((order.get('pricingSummary') or {}).get('priceSubtotal'))
            order_tax = _amount_value((order.get('pricingSummary') or {}).get('tax'))
            line_items = order.get('lineItems') or []

            for line_item in line_items:
                total_lines += 1
                quantity = line_item.get('quantity')
                try:
                    quantity = int(quantity)
                except (TypeError, ValueError):
                    quantity = 1

                line_total = _amount_value(line_item.get('lineItemCost'))
                if line_total is None:
                    line_total = _amount_value(line_item.get('total'))

                unit_price = None
                if line_total is not None and quantity > 0:
                    unit_price = round(line_total / quantity, 4)

                line_shipping_cost = _amount_value(((line_item.get('deliveryCost') or {}).get('shippingCost')))
                if line_shipping_cost is None and len(line_items) == 1:
                    line_shipping_cost = _amount_value((order.get('pricingSummary') or {}).get('deliveryCost'))

                line_tax = None
                if order_tax is not None:
                    if len(line_items) == 1:
                        line_tax = order_tax
                    else:
                        line_total_for_tax = _amount_value(line_item.get('total'))
                        order_total_for_tax = _amount_value((order.get('pricingSummary') or {}).get('total'))
                        if line_total_for_tax is not None and order_total_for_tax:
                            try:
                                line_tax = round(order_tax * (line_total_for_tax / order_total_for_tax), 4)
                            except Exception:
                                line_tax = None

                legacy_item_id = str(line_item.get('legacyItemId') or '').strip()
                line_item_id = str(line_item.get('lineItemId') or '').strip()
                # lineItemId uniquely identifies a line within an eBay order.
                # legacyItemId identifies the listing and can be shared by
                # multiple lines (for example, different variations).
                item_id = line_item_id or legacy_item_id or None
                sku_val = (
                    str(line_item.get('sku') or '').strip()
                    or str(line_item.get('sellerInventoryReference') or '').strip()
                    or None
                )

                store_ebay_order({
                    'order_id': order_id,
                    'item_id': item_id,
                    'listing_item_id': legacy_item_id or item_id,
                    'sku': sku_val,
                    'title': line_item.get('title'),
                    'item_condition': line_item.get('condition') or line_item.get('conditionDisplayName'),
                    'item_condition_description': line_item.get('conditionDescription') or line_item.get('conditionNote'),
                    'item_description': line_item.get('description') or line_item.get('itemDescription'),
                    'quantity': quantity,
                    'price': unit_price,
                    'checkout_status': order_status,
                    'shipping_name': str(contact.get('fullName') or '').strip() or None,
                    'shipping_street1': str(address.get('addressLine1') or '').strip() or None,
                    'shipping_street2': str(address.get('addressLine2') or '').strip() or None,
                    'shipping_city': str(address.get('city') or '').strip() or None,
                    'shipping_state': str(address.get('stateOrProvince') or '').strip() or None,
                    'shipping_postal_code': str(address.get('postalCode') or '').strip() or None,
                    'shipping_country': str(address.get('countryCode') or '').strip() or None,
                    'paid_time': payment_date or None,
                    'shipped_time': shipped_time,
                    'seller_fee': _estimate_line_fee(line_total, order_subtotal, order_total_fee, len(line_items)),
                    'taxes': line_tax,
                    'fees': None,
                    'shipping_cost': line_shipping_cost,
                    'image': None,
                    'barcode': None,
                    'isHandled': '',
                    'isHandledDate': ''
                })

        total = payload.get('total')
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0

        offset += len(orders)
        if len(orders) < limit or (total and offset >= total):
            break

    print(f"Stored/updated {total_lines} eBay sold line item(s) from {total_orders} order(s)")
