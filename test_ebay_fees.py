"""
Test script to see what fee data eBay actually returns in GetOrders API
"""
import os
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

def test_ebay_order_fees():
    print("Testing eBay GetOrders API for fee data...")
    url = "https://api.ebay.com/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetOrders",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
    }
    
    # Get just 1 order from last 7 days
    xml_payload = f'''
    <?xml version="1.0" encoding="utf-8"?>
    <GetOrdersRequest xmlns="urn:ebay:apis:eBLBaseComponents">
      <RequesterCredentials>
        <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
      </RequesterCredentials>
      <OrderRole>Seller</OrderRole>
      <OrderStatus>All</OrderStatus>
      <NumberOfDays>7</NumberOfDays>
      <Pagination>
        <EntriesPerPage>1</EntriesPerPage>
        <PageNumber>1</PageNumber>
      </Pagination>
    </GetOrdersRequest>
    '''
    
    response = requests.post(url, headers=headers, data=xml_payload, timeout=30)
    root = ET.fromstring(response.text)
    
    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
    
    # Find first order
    order = root.find('.//ebay:Order', ns)
    if order:
        order_id = order.find('ebay:OrderID', ns)
        print(f"\nOrder ID: {order_id.text if order_id is not None else 'N/A'}")
        
        # Look for ALL transaction-level fee fields
        for transaction in order.findall('.//ebay:Transaction', ns):
            print("\n--- Transaction Details ---")
            
            item_id = transaction.find('.//ebay:Item/ebay:ItemID', ns)
            title = transaction.find('.//ebay:Item/ebay:Title', ns)
            price = transaction.find('.//ebay:TransactionPrice', ns)
            
            print(f"Item ID: {item_id.text if item_id is not None else 'N/A'}")
            print(f"Title: {title.text if title is not None else 'N/A'}")
            print(f"Price: ${price.text if price is not None else 'N/A'}")
            
            # Check ALL possible fee fields
            print("\n--- Fee Fields ---")
            
            # FinalValueFee
            final_value_fee = transaction.find('.//ebay:FinalValueFee', ns)
            print(f"FinalValueFee: ${final_value_fee.text if final_value_fee is not None else 'NOT FOUND'}")
            
            # Check parent elements
            monetary_details = transaction.find('.//ebay:MonetaryDetails', ns)
            if monetary_details is not None:
                print("MonetaryDetails found!")
                for child in monetary_details:
                    print(f"  - {child.tag.replace('{urn:ebay:apis:eBLBaseComponents}', '')}: {child.text}")
            
            # Check Taxes
            taxes = transaction.find('.//ebay:Taxes', ns)
            if taxes is not None:
                print("Taxes element found:")
                for child in taxes:
                    print(f"  - {child.tag.replace('{urn:ebay:apis:eBLBaseComponents}', '')}: {child.text}")
            
            total_tax = transaction.find('.//ebay:Taxes/ebay:TotalTaxAmount', ns)
            print(f"TotalTaxAmount: ${total_tax.text if total_tax is not None else 'NOT FOUND'}")
            
            # Shipping
            actual_shipping = transaction.find('.//ebay:ActualShippingCost', ns)
            print(f"ActualShippingCost: ${actual_shipping.text if actual_shipping is not None else 'NOT FOUND'}")
            
            # Print ALL subelements to see what's available
            print("\n--- All Transaction Subelements ---")
            for elem in transaction.iter():
                tag_name = elem.tag.replace('{urn:ebay:apis:eBLBaseComponents}', '')
                if elem.text and elem.text.strip():
                    print(f"{tag_name}: {elem.text.strip()}")
    else:
        print("No orders found in response")

if __name__ == "__main__":
    test_ebay_order_fees()
