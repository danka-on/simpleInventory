import sqlite3
import csv
from datetime import datetime

# BOL pricing data - map UPC to CLIENT COST (your actual cost) and retail values
bol_pricing = {
    '790824588616': {'cost': 69.44, 'retail': 475.00, 'description': 'Michael Aram Butterfly Ginkgo Cake Stand wi Bronze'},
    '4003683330750': {'cost': 43.30, 'retail': 540.00, 'description': 'Villeroy Boch Victor 46-Piece Flatware Set Silver'},
    '35886540487': {'cost': 37.20, 'retail': 334.00, 'description': 'J.A. Henckels HENCKELS Solution Self-Sharpen Black, Silver-Tone'},
    '44228073101': {'cost': 35.28, 'retail': 334.00, 'description': 'Mikasa Mikasa Marseille 65-Piece Flat Silver No Size'},
    '882864329578': {'cost': 33.68, 'retail': 275.00, 'description': 'Lenox Lenox Stratton 65-PC Flatware Stainless Steel'},
    '701587450744': {'cost': 33.45, 'retail': 220.00, 'description': 'Waterford Waterford Gin Journeys Hiball Clear'},
    '35886239299': {'cost': 26.94, 'retail': 265.00, 'description': 'J.A. Henckels Vintage 1876 1810 Stainless S'},
    '719978848155': {'cost': 25.66, 'retail': 240.00, 'description': 'Cambridge Silversmiths Cambridge Silversmiths Beacon Gold'},
    '715021877868': {'cost': 22.45, 'retail': 235.00, 'description': 'Euro Ceramica Euro Ceramica Turkey Platter White Platter'},
    '35886666866': {'cost': 22.45, 'retail': 458.00, 'description': 'J.A. Henckels HENCKELS Stainless steel 13-Pc Silver, Black and Walnut 13 Pieces'},
    '882864545565': {'cost': 18.44, 'retail': 65.00, 'description': 'Kate Spade kate spade new york Rosy Glow'},
    '885991207733': {'cost': 17.96, 'retail': 187.00, 'description': 'Fitz and Floyd Fitz and Floyd Gold Serif 32-P White And Gold'},
    '749151219618': {'cost': 17.96, 'retail': 185.00, 'description': 'Spode Spode Delamere Dinner Plates, Brown Plate'},
    '790824451217': {'cost': 17.56, 'retail': 120.00, 'description': 'Michael Aram Michael Aram Butterfly Ginkgo No Color No Size'},
    '813924011997': {'cost': 17.43, 'retail': 167.00, 'description': 'RiverRidge Home Riverridge Bouquet 46 Piece Mo Silver'},
    '790824510952': {'cost': 16.84, 'retail': 110.00, 'description': 'Michael Aram Palm Gold Collection 5-Pc. Pla Gold'},
    '85081656483': {'cost': 15.72, 'retail': 164.00, 'description': 'Gibson Elite Gibson Elite Berea 16 Pc. Dinn Green 16 Piece Set'},
    '846823014103': {'cost': 14.91, 'retail': 99.95, 'description': 'MacKenzie-Childs Courtly Check Cork-Backed Plac'},
    '840115619434': {'cost': 14.91, 'retail': 99.95, 'description': 'MacKenzie-Childs Sterling Check Enameled Steel'},
    '25398244058': {'cost': 14.37, 'retail': 128.00, 'description': 'Pfaltzgraff Pfaltzgraff Farmyard Rooster 1 Brown No Size'},
    '28199976074': {'cost': 13.79, 'retail': 136.00, 'description': 'Godinger Revere Footed Cake Plate with Silver No Size'},
    '194372039147': {'cost': 12.83, 'retail': 115.00, 'description': 'Lenox Butterfly Meadow Large Bowl wi Multi'},
    '33805578535': {'cost': 12.51, 'retail': 93.00, 'description': 'BIA Cordon Bleu BIA Cordon Bleu Petal Round Se White No Size'},
    '35886110116': {'cost': 12.09, 'retail': 109.00, 'description': 'Zwilling Gourmet Steak Knives, Set of 4'},
    '71160107854': {'cost': 12.03, 'retail': 84.99, 'description': 'Corelle Tranquil Reflections 12pc Set White 12 Piece Set'},
    '37725614458': {'cost': 11.23, 'retail': 100.00, 'description': 'Noritake Noritake Serene Garden 4 Piece Light Green Plate'},
    '882864825810': {'cost': 10.42, 'retail': 92.00, 'description': 'Lenox Lenox Butterfly Meadow Enamele White Body Wpastel Floral And'},
    '882864840967': {'cost': 9.62, 'retail': 85.00, 'description': 'Lenox Lenox Butterfly Meadow Kitchen White Body Wpastel Floral And'},
    '28225832893': {'cost': 9.52, 'retail': 84.00, 'description': 'International Silver 180 Stainless Steel 51-Pc. Ad Grey Group'},
    '885991237204': {'cost': 8.98, 'retail': 93.00, 'description': 'Fitz and Floyd Fitz and Floyd Everyday Deep S White 2 piece set'},
    '48552584265': {'cost': 8.82, 'retail': 130.00, 'description': 'Tabletops Unlimited Soft Square 42-Pc. Dinnerware White'},
    '608084003797': {'cost': 8.34, 'retail': 87.00, 'description': 'Tableau Margo Bowls, Set of 4 Blue'},
    '810133073877': {'cost': 7.92, 'retail': 84.00, 'description': 'Cambridge Silversmiths Cambridge Silversmiths Stainle Metallic and Stainless No Size'},
    '85081567369': {'cost': 7.62, 'retail': 60.00, 'description': 'Gibson Gibson Canyon Crest Stackable Sage Green 12 Piece Set'},
    '749151756700': {'cost': 7.54, 'retail': 68.00, 'description': 'Portmeirion Botanic Garden Bouquet Tea for Whitemulti'},
    '20911078721': {'cost': 7.52, 'retail': 78.00, 'description': 'Mikasa French Countryside 5-Piece P'},
    '719663173395': {'cost': 7.23, 'retail': 72.00, 'description': 'Lenox Meadow 5-Piece Kitchen Linens White'},
    '885991092520': {'cost': 7.18, 'retail': 73.00, 'description': 'Mikasa Mikasa Palazzo Crystal Vase no color'},
    '21864425075': {'cost': 7.10, 'retail': 75.00, 'description': 'Spode Spode Woodland Polyester Table Ivory 60X84 Rectangle'},
    '6941327134297': {'cost': 7.06, 'retail': 54.99, 'description': 'Glitzhome Glitzhome Stars and Stripes Wi Red, Blue, White'},
    '28199917190': {'cost': 6.41, 'retail': 70.00, 'description': 'Godinger 5-Pc. Copper Storage Bowl Set Copper'},
    '28199917237': {'cost': 6.41, 'retail': 66.00, 'description': 'Godinger Hammered Silver-Tone Bowls and Silver'},
    '194372039130': {'cost': 6.41, 'retail': 58.00, 'description': 'Lenox Butterfly Meadow Bowl with Sta Multi'},
    '20911572830': {'cost': 6.29, 'retail': 65.00, 'description': 'Mikasa Italian Countryside Vegetabl'},
    '795785120653': {'cost': 6.17, 'retail': 75.00, 'description': 'Thirstystone Natural Natural No Size'},
    '720201255923': {'cost': 6.09, 'retail': 70.00, 'description': 'Lorren Home Trends RCR Laurus Crystal Oval Bowl Clear'},
    '28199274910': {'cost': 5.77, 'retail': 30.00, 'description': 'Godinger Godinger Dublin Whiskey Shot G Clear'},
    '28199259757': {'cost': 5.77, 'retail': 64.00, 'description': 'Godinger Dublin 5-Piece Beverage Set'},
    '32622018897': {'cost': 5.65, 'retail': 42.99, 'description': 'Luigi Bormioli Luigi Bormioli Glassware, Set No Color'},
    '48552491662': {'cost': 5.53, 'retail': 75.00, 'description': 'Tabletops Unlimited 16-Pc. Alec Dinnerware Set White'},
    '3550190501131': {'cost': 5.13, 'retail': 67.00, 'description': 'Duralex Duralex Clear Tumbler - 11 18 CLEAR No Size'},
    '692786815857': {'cost': 5.13, 'retail': 60.00, 'description': 'French Home Faux Ivory Coffee Spoons, Set Ivory'},
    '194137303643': {'cost': 5.01, 'retail': 50.00, 'description': 'Oake Clear Textured Highball Glasse Amber'},
    '28199258132': {'cost': 4.81, 'retail': 60.00, 'description': 'Godinger Dublin 7-Pc. Spirits Set No Color No Size'},
    '28199210581': {'cost': 4.81, 'retail': 50.00, 'description': 'Godinger Godinger Sullivan Street Flute Clear No Size'},
    '28199210611': {'cost': 4.81, 'retail': 50.00, 'description': 'Godinger Godinger Sullivan Street Flute Clear No Size'},
    '615715835603': {'cost': 4.33, 'retail': 45.00, 'description': 'Lorren Home Trends Lorren Home Trends Floral Desi Pink ONE SIZE'},
    '3550190504545': {'cost': 4.17, 'retail': 55.00, 'description': 'Duralex Duralex Picardie Tumbler - 6 CLEAR No Size'},
    '35886326470': {'cost': 4.06, 'retail': 38.00, 'description': 'J.A. Henckels Zwilling J.A. Henckels Bellase Silver'},
    '194372032070': {'cost': 4.01, 'retail': 36.00, 'description': 'Lenox Lenox Wildflowers Large Vase White Large'},
    '194372025751': {'cost': 4.01, 'retail': 42.00, 'description': 'Lenox Lenox Cantera Teaspoons, Set o Metallic and Stainless'},
    '48552527729': {'cost': 3.88, 'retail': 45.00, 'description': 'Tabletops Unlimited Denmark Latte Mugs, Set of 4 White'},
    '194145426754': {'cost': 3.88, 'retail': 65.00, 'description': 'The Cellar Lia Collection Fluted Double O'},
    '885991237136': {'cost': 3.59, 'retail': 37.00, 'description': 'Fitz and Floyd Fitz and Floyd Everyday Whitew White 4 Piece'},
    '704572920260': {'cost': 3.19, 'retail': 40.00, 'description': 'Circle Glass Circle Glass Mini Yorkshire Di Clear NO SIZE'},
    '194590045791': {'cost': 2.91, 'retail': 40.00, 'description': 'Elrene Autumn Pumpkin Grove Fall Rect Multi 60 x 84'},
    '28199286494': {'cost': 2.81, 'retail': 20.00, 'description': 'Godinger Crystal 2-Pc. Votive Candle-Ho'},
    '882864038746': {'cost': 2.57, 'retail': 33.00, 'description': 'Lenox British Colonial Fruit Bowl Bamboo'},
    '91709352583': {'cost': 1.92, 'retail': 17.00, 'description': 'Lenox Butterfly Meadow Scalloped Tea'},
    '400013532749': {'cost': 6.31, 'retail': 31.00, 'description': 'CRC GENERIC'},
    '4003686448926': {'cost': 3.05, 'retail': 38.00, 'description': 'Villeroy Boch Boston Goblet Iridescent No Size'},
}

def get_base_upc(upc):
    """Strip suffix from UPC (e.g., '28199210611-1' -> '28199210611')"""
    if '-' in upc:
        return upc.split('-')[0]
    return upc

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print("="*80)
print("GENERATING MISSING ITEMS REPORT WITH PRICING FOR LOT# 16423315")
print("="*80)

# Get all unchecked items from LOT 16423315, excluding BergHOFF knife set
cur.execute('''
    SELECT upc, item_description, image_url, original_qty, good_qty, bad_qty, unchecked_qty, 
           import_date, bol_number
    FROM bol_items
    WHERE lot_number = '16423315'
    AND unchecked_qty > 0
    AND LOWER(item_description) NOT LIKE '%berghoff%knife%'
    ORDER BY item_description
''')

items = cur.fetchall()

if not items:
    print("\n❌ No unchecked items found in LOT# 16423315 (excluding BergHOFF knife set)")
    conn.close()
    exit(0)

print(f"\nFound {len(items)} unchecked items (excluding BergHOFF knife set)\n")

# Calculate totals
total_cost = 0.0
total_retail = 0.0
items_with_pricing = 0
items_without_pricing = 0

for item in items:
    upc, desc, image_url, orig_qty, good_qty, bad_qty, unch_qty, imp_date, bol_num = item
    base_upc = get_base_upc(upc)
    
    if base_upc in bol_pricing:
        pricing = bol_pricing[base_upc]
        total_cost += pricing['cost'] * unch_qty
        total_retail += pricing['retail'] * unch_qty
        items_with_pricing += 1
    else:
        items_without_pricing += 1

# Prepare CSV data
csv_filename = f'LOT_16423315_Missing_Items_Report_WITH_PRICING_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    
    # Write header section
    writer.writerow(['MISSING ITEMS REPORT WITH FINANCIAL VALUATION'])
    writer.writerow([f'LOT Number: 16423315'])
    writer.writerow([f'Report Date: {datetime.now().strftime("%B %d, %Y at %I:%M %p")}'])
    writer.writerow([f'Total Missing Items: {len(items)}'])
    writer.writerow([f'Items with Pricing Data: {items_with_pricing}'])
    writer.writerow([f'Items without Pricing Data: {items_without_pricing}'])
    writer.writerow([])
    
    # Financial Summary
    writer.writerow(['FINANCIAL SUMMARY'])
    writer.writerow([])
    writer.writerow([f'Total Client Cost (Missing Items): ${total_cost:,.2f}'])
    writer.writerow([f'Total Original Retail Value (Missing Items): ${total_retail:,.2f}'])
    writer.writerow([f'Your Potential Loss at Client Cost: ${total_cost:,.2f}'])
    writer.writerow([f'Potential Loss at Retail Value: ${total_retail:,.2f}'])
    writer.writerow([])
    writer.writerow(['NOTE: Client Cost reflects the actual amount paid to acquire this inventory. These figures represent the documented merchandise that remains unaccounted for. The actual financial impact will depend on whether items are recovered, whether insurance claims are filed, and the terms of any supplier agreements regarding missing or damaged shipments.'])
    writer.writerow([])
    
    # Executive Summary
    writer.writerow(['EXECUTIVE SUMMARY'])
    writer.writerow([])
    writer.writerow(['This report identifies merchandise from LOT# 16423315 that remains unaccounted for during the physical inventory verification process. The items listed below were received and documented in the Bill of Lading but have not been physically located or verified in the warehouse. This discrepancy requires immediate investigation to determine whether the items are misplaced, improperly stored, or were not actually delivered despite being documented on the shipping manifest.'])
    writer.writerow([])
    writer.writerow(['INVESTIGATION REQUIRED'])
    writer.writerow([])
    writer.writerow(['The following actions are recommended:'])
    writer.writerow(['1. Conduct a comprehensive physical search of all warehouse locations, including overflow areas and mislabeled storage positions.'])
    writer.writerow(['2. Review security footage and receiving documentation to verify actual delivery of these items.'])
    writer.writerow(['3. Cross-reference with packing slips and supplier documentation to confirm items were included in the shipment.'])
    writer.writerow(['4. Contact the supplier/shipper to report the discrepancy and initiate a claim for missing merchandise.'])
    writer.writerow(['5. Document all findings and update inventory records accordingly.'])
    writer.writerow(['6. If items are not located within 30 days, file insurance claim and/or supplier claim for the documented financial loss.'])
    writer.writerow([])
    
    # Detailed Item List
    writer.writerow(['DETAILED ITEM LIST WITH PRICING'])
    writer.writerow([])
    
    # Column headers
    writer.writerow(['Item #', 'UPC/Barcode', 'Description', 'Qty Missing', 'Client Cost', 'Unit Retail', 
                     'Total Client Cost', 'Total Retail', 'Original Qty', 'Good Qty', 'Bad Qty', 
                     'Import Date', 'BOL Number', 'Status'])
    
    # Write each item
    for idx, item in enumerate(items, 1):
        upc, desc, image_url, orig_qty, good_qty, bad_qty, unch_qty, imp_date, bol_num = item
        desc_clean = (desc or 'No description available').strip()
        base_upc = get_base_upc(upc)
        
        # Get pricing if available
        if base_upc in bol_pricing:
            pricing = bol_pricing[base_upc]
            unit_cost = pricing['cost']
            unit_retail = pricing['retail']
            total_cost_item = unit_cost * unch_qty
            total_retail_item = unit_retail * unch_qty
            unit_cost_str = f'${unit_cost:,.2f}'
            unit_retail_str = f'${unit_retail:,.2f}'
            total_cost_str = f'${total_cost_item:,.2f}'
            total_retail_str = f'${total_retail_item:,.2f}'
        else:
            unit_cost_str = 'N/A'
            unit_retail_str = 'N/A'
            total_cost_str = 'N/A'
            total_retail_str = 'N/A'
        
        # Status explanation
        status = 'NOT LOCATED - Physical verification incomplete'
        
        writer.writerow([
            idx,
            upc or 'N/A',
            desc_clean,
            unch_qty,
            unit_cost_str,
            unit_retail_str,
            total_cost_str,
            total_retail_str,
            orig_qty,
            good_qty,
            bad_qty,
            imp_date or 'Unknown',
            bol_num or 'N/A',
            status
        ])
    
    writer.writerow([])
    writer.writerow(['REPORT FOOTER'])
    writer.writerow([])
    writer.writerow([f'TOTAL FINANCIAL EXPOSURE: ${total_cost:,.2f} (Your Client Cost) / ${total_retail:,.2f} (Retail)'])
    writer.writerow([])
    writer.writerow(['This report was generated automatically from the inventory management system. All data reflects the current state of LOT# 16423315 as of the report generation date. Items marked as "unchecked" indicate merchandise that was documented on the receiving manifest but has not been physically verified or located in the warehouse. Pricing data is sourced from the original Bill of Lading documentation with Client Cost reflecting your actual acquisition cost.'])
    writer.writerow([])
    writer.writerow(['RECOMMENDED ACTIONS:'])
    writer.writerow(['1. Immediate physical search of all warehouse locations'])
    writer.writerow(['2. Review with receiving staff to confirm delivery status'])
    writer.writerow(['3. Contact supplier within 48 hours to report discrepancy'])
    writer.writerow(['4. File formal claim if items not located within 7 days'])
    writer.writerow(['5. Update insurance provider if loss exceeds policy threshold'])
    writer.writerow([])
    writer.writerow(['For questions or to report updates on the status of these items, please contact the warehouse manager or inventory control supervisor.'])
    writer.writerow([])
    writer.writerow([f'Report Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'])
    writer.writerow(['Report Generated By: Inventory Management System'])
    writer.writerow(['Classification: Internal Use Only - Contains Proprietary Financial Information'])

conn.close()

print(f"✅ Report generated: {csv_filename}\n")
print("="*80)
print("FINANCIAL SUMMARY")
print("="*80)
print(f"Total Client Cost (Missing):     ${total_cost:>12,.2f}")
print(f"Total Original Retail (Missing): ${total_retail:>12,.2f}")
print(f"\nYour Actual Financial Exposure:  ${total_cost:>12,.2f}")
print(f"Potential Loss at Retail:        ${total_retail:>12,.2f}")
print(f"\nItems with pricing data:    {items_with_pricing:>3}")
print(f"Items without pricing data: {items_without_pricing:>3}")
print("="*80)
print("\nReport includes:")
print("  • Executive summary with financial impact")
print("  • Investigation recommendations")
print("  • Detailed item list with unit and total pricing")
print("  • Cost and retail value calculations")
print("  • Recommended actions for claim filing")
print("\nTop 5 Most Valuable Missing Items:")
print("-"*80)

# Sort items by value and show top 5
valued_items = []
for item in items:
    upc, desc, image_url, orig_qty, good_qty, bad_qty, unch_qty, imp_date, bol_num = item
    base_upc = get_base_upc(upc)
    if base_upc in bol_pricing:
        pricing = bol_pricing[base_upc]
        total_item_retail = pricing['retail'] * unch_qty
        valued_items.append((desc, unch_qty, pricing['retail'], total_item_retail))

valued_items.sort(key=lambda x: x[3], reverse=True)

for idx, (desc, qty, unit_retail, total_retail_item) in enumerate(valued_items[:5], 1):
    desc_short = (desc[:55] + '...') if len(desc) > 55 else desc
    print(f"{idx}. {desc_short}")
    print(f"   Qty: {qty} × ${unit_retail:,.2f} = ${total_retail_item:,.2f}")

print("\n" + "="*80)
