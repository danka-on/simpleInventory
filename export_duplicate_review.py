import sqlite3
import csv

print("Generating detailed CSV report of consolidated duplicates...")

# All the duplicates we consolidated
all_duplicates = [
    ('400013532749', 'LOT 16264617', 8, [1, 4, 4, 27, 4, 4, 12, 27], 'CRC GENERIC'),
    ('400013532749', 'LOT 16448708', 8, [9, 8, 11, 2, 9, 10, 7, 23], 'CRC GENERIC'),
    ('400013532749', 'LOT 16578199', 8, [11, 10, 12, 23, 4, 7, 6, 7], 'CRC GENERIC'),
    ('48552491662', 'LOT 16578199', 5, [1, 1, 4, 1, 5], 'Tabletops Unlimited 16-Pc. Alec Dinnerware Set White'),
    ('48552491662', 'LOT 16448708', 2, [1, 1], 'Tabletops Unlimited 16-Pc. Alec Dinnerware Set White'),
    ('48552584265', 'LOT 16448708', 4, [1, 1, 1, 1], 'Tabletops Unlimited Soft Square 42-Pc. Dinnerware White'),
    ('48552584265', 'LOT 16578199', 4, [1, 1, 1, 1], 'Tabletops Unlimited Soft Square 42-Pc. Dinnerware White'),
    ('48552590518', 'LOT 16448708', 4, [4, 2, 1, 1], 'Tabletops Gallery Tabletops Gallery Round 40 Pc. White'),
    ('28199274910', 'LOT 16264617', 3, [1, 1, 1], 'Godinger Godinger Dublin Whiskey Shot G Clear'),
    ('28199274910', 'LOT 16578199', 2, [3, 1], 'Godinger Godinger Dublin Whiskey Shot G Clear'),
    ('400011639440', 'LOT 16448708', 3, [1, 15, 1], 'DIV 22/34 CRC REJECTS'),
    ('48552722612', 'LOT 16578199', 3, [6, 2, 1], 'Haven Floral Scalloped Salad Plates, White With Floral Debossed'),
    ('48552733311', 'LOT 16578199', 3, [1, 1, 1], 'Haven Soft Square 32-Pc. Dinnerware White'),
    ('86279178893', 'LOT 16448708', 3, [1, 1, 1], 'Cuisinart Cuisinart Space-Saving Onyx 8- Onyx'),
    ('13302451029', 'LOT 16264617', 2, [1, 2], 'The Cellar The Cellar Set of 3 Whiteware White'),
    ('194137133059', 'LOT 16578199', 2, [2, 1], 'The Cellar Whiteware James Collection Lid White'),
    ('194137133066', 'LOT 16264617', 2, [2, 2], 'The Cellar Whiteware James Round Dip Bowl White'),
    ('194137133080', 'LOT 16578199', 2, [4, 1], 'The Cellar Aaden Textured Dinner Bowl White'),
    ('194137133431', 'LOT 16578199', 2, [1, 1], 'Oake Clay Dinner Plates, Set of 4 Khaki'),
    ('194137198225', 'LOT 16448708', 2, [6, 5], 'Oake 4-Pc. Dip Bowls Tray Set Wood'),
    ('194590060978', 'LOT 16448708', 2, [2, 1], 'Villeroy Boch Villeroy Boch Manufacture'),
    ('194590199524', 'LOT 16578199', 2, [2, 1], 'Elrene Vietri Medallion Blue Block Pr Bl'),
    ('21864337323', 'LOT 16264617', 2, [1, 1], 'Portmeirion Blue Portofino 60 x 120 Tabl'),
    ('25398232475', 'LOT 16264617', 2, [2, 1], 'Pfaltzgraff Pfaltzgraff Carrie 12-Pc Din'),
    ('25398243808', 'LOT 16264617', 2, [1, 1], 'Pfaltzgraff Pfaltzgraff Medallion Radian'),
    ('28199256657', 'LOT 16448708', 2, [1, 1], 'Godinger Godinger Dublin Set of 4 5oz J'),
    ('28199257296', 'LOT 16448708', 2, [1, 1], 'Godinger Dublin Double Old-Fashioned'),
    ('28199260364', 'LOT 16264617', 2, [1, 2], 'Godinger Godinger West Street Shooters C'),
    ('28199291566', 'LOT 16448708', 2, [1, 1], 'Godinger Godinger Red Peppermint Wrappe'),
    ('28225897489', 'LOT 16578199', 2, [1, 1], 'International Silver 67-Pc. Carleigh Fla'),
    ('28332846226', 'LOT 16264617', 2, [1, 1], 'kate spade new york kate spade new york'),
    ('35886129972', 'LOT 16448708', 2, [1, 1], 'J.A. Henckels TWIN Brand Opus 1810 Stain'),
    ('35886214388', 'LOT 16264617', 2, [1, 1], 'J.A. Henckels Vintage 1876 1810 Stainles'),
    ('35886385323', 'LOT 16264617', 2, [1, 1], 'J.A. Henckels Madison Square 65-Pc. 1810'),
    ('35886385330', 'LOT 16578199', 2, [1, 1], 'Henckels Lani 65-Pc. 1810 Stainless St S'),
    ('35886387624', 'LOT 16578199', 2, [1, 1], 'J.A. Henckels Modernist 13-Pc. Knife Blo'),
    ('35886439255', 'LOT 16448708', 2, [1, 1], 'Zwilling Gourmet Steak Knives, Set of 4'),
    ('35886482893', 'LOT 16448708', 2, [2, 2], 'Zwilling Twin Gourmet 15-Pc. Cutlery Se'),
    ('35886645564', 'LOT 16264617', 2, [2, 1], 'J.A. Henckels HENCKELS Solution 16-Piece'),
    ('37725606613', 'LOT 16264617', 2, [1, 1], 'Noritake Colorwave Coupe Pasta Bowls, S'),
    ('37725607597', 'LOT 16264617', 2, [1, 1], 'Noritake Colorwave SidePrep Bowls, Set G'),
    ('45908144173', 'LOT 16448708', 2, [1, 1], 'Farberware Farberware Edgekeeper 15-Piec'),
    ('47596030349', 'LOT 16448708', 2, [1, 3], 'Lenox French Perle Collection Pistac Nat'),
    ('47596043677', 'LOT 16448708', 2, [1, 1], 'Lenox Lenox Laurel Leaf Placemat 19 Plat'),
    ('47596142387', 'LOT 16264617', 2, [1, 1], 'Lenox Opal Innocence Oblong Tablec White'),
    ('47596162637', 'LOT 16264617', 2, [1, 1], 'Lenox Opal Innocence Oblong Tablec Ivory'),
    ('48552397896', 'LOT 16448708', 2, [1, 1], 'Tabletops Unlimited Wildflower 16-Pc. Di'),
    ('48552488846', 'LOT 16578199', 2, [1, 1], 'Tabletops Unlimited 16-Pc. Adams Dinnerw'),
    ('48552527729', 'LOT 16264617', 2, [1, 1], 'Tabletops Unlimited Denmark Latte Mugs,'),
    ('48552604673', 'LOT 16578199', 2, [1, 1], 'Tabletops Unlimited Fiore 42pc Dinnerwar'),
    ('48552703161', 'LOT 16448708', 2, [1, 1], 'Denmark Tools for Cooks Denmark Tools fo'),
    ('48552722650', 'LOT 16578199', 2, [3, 1], 'Haven 14 Scallop Floral-Print Stone Whit'),
    ('692786820790', 'LOT 16264617', 2, [2, 1], 'French Home French Home Faux Ivory Chops'),
    ('701587159432', 'LOT 16264617', 2, [1, 1], 'Wedgwood Wedgwood Hibiscus Dinner Plate'),
    ('701587447119', 'LOT 16264617', 2, [1, 0], 'Waterford Wedding Heirloom Frame, 8 x 1'),
    ('715021869726', 'LOT 16578199', 2, [1, 1], 'Euro Ceramica White Essential 16 Piece D'),
    ('715021871606', 'LOT 16264617', 2, [1, 1], 'Euro Ceramica Highlands 3 Piece Serving'),
    ('72456117281', 'LOT 16578199', 2, [1, 1], 'Design Imports Design Imports Lemon Blis'),
    ('730384895007', 'LOT 16264617', 2, [1, 2], 'Certified International Vera Cruz 12-Pc.'),
    ('731742133144', 'LOT 16578199', 2, [1, 1], 'Infinity Instruments Infinity Instrument'),
    ('733652135973', 'LOT 16264617', 2, [1, 2], 'Hampton Forge Hampton Forge 180 Stainles'),
    ('76440141368', 'LOT 16578199', 2, [1, 1], 'Anchor Hocking Anchor Hocking 16 Piece M'),
    ('790824227911', 'LOT 16578199', 2, [1, 1], 'Michael Aram Heart Dreidel Multi'),
    ('790824320919', 'LOT 16448708', 2, [1, 1], 'Michael Aram Calla Lily 8 x 10 Frame No'),
    ('790824462220', 'LOT 16578199', 2, [1, 1], 'Michael Aram Ivy and Oak 4-Pc. Gift Set'),
    ('810071428500', 'LOT 16448708', 2, [1, 2], 'JoyJolt JoyJolt Savor Fluted Glass Cof C'),
    ('840191207679', 'LOT 16578199', 2, [1, 1], 'Elama Elama Gia 24 Pc. Dinnerware Se Whi'),
    ('88235775368', 'LOT 16448708', 2, [1, 1], 'Circleware Tipsy With Style Set of 6 - 1'),
    ('882864264909', 'LOT 16264617', 2, [1, 1], 'Lenox Flatware, Esquire 65-Piece Set'),
    ('882864277947', 'LOT 16264617', 2, [1, 1], 'Lenox Dinnerware, Set of 6 Butterfly'),
    ('882864391216', 'LOT 16264617', 2, [2, 1], 'Lenox Tuscany Buy 4 Get 6 Red Wine S No'),
    ('882864435019', 'LOT 16264617', 2, [1, 1], 'Lenox Butterfly Meadow Thermal Trave'),
    ('882864646866', 'LOT 16448708', 2, [1, 1], 'Lenox Lenox Butterfly Meadow 28 Pc. Mult'),
    ('882864841001', 'LOT 16448708', 2, [1, 1], 'Lenox Lenox Butterfly Meadow Salt White'),
    ('885991045229', 'LOT 16578199', 2, [1, 1], 'Mikasa Regent Bead 65-Piece Flatware'),
    ('885991196938', 'LOT 16264617', 2, [1, 1], 'Mikasa Mikasa Cassia 12 Crystal Vase No'),
    ('885991245780', 'LOT 16264617', 2, [1, 1], 'Year Day Year Day 20-Pc. Flatware Set Ma'),
    ('99967248372', 'LOT 16578199', 2, [1, 2], 'Picnic Time St. Tropez Blanket Tote Outd')
]

# Write to CSV
with open('consolidated_duplicates_review.csv', 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(['UPC', 'LOT', 'Duplicate_Entries', 'Individual_Quantities', 'Total_Consolidated', 'Description', 'Assessment', 'Likely_Issue'])
    
    for upc, lot, entries, qtys, desc in all_duplicates:
        total = sum(qtys)
        all_same = len(set(qtys)) == 1
        all_ones = all_same and qtys[0] == 1
        
        if all_ones:
            assessment = 'SUSPICIOUS'
            likely_issue = 'Excel had duplicate rows - same item listed multiple times'
        elif all_same:
            assessment = 'SUSPICIOUS'
            likely_issue = f'Excel had {entries} rows with identical qty={qtys[0]} each'
        else:
            assessment = 'REVIEW NEEDED'
            likely_issue = 'Multiple boxes/pallets OR Excel error - check original file'
        
        writer.writerow([
            upc,
            lot,
            entries,
            ', '.join(str(q) for q in qtys),
            total,
            desc,
            assessment,
            likely_issue
        ])

print("✅ CSV report saved to: consolidated_duplicates_review.csv")
print("\nThis file contains:")
print("  - All 78 UPC+LOT combinations that had duplicates")
print("  - Original quantity breakdown for each")
print("  - Assessment of whether it's suspicious or needs review")
print("\nReview this file to verify if consolidation was correct!")
