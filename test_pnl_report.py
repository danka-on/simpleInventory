import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pnl_report import build_pnl, upc_key


class PnlReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def run_script(self, name, script):
        conn = sqlite3.connect(self.root / name)
        try:
            conn.executescript(script)
            conn.commit()
        finally:
            conn.close()

    def seed(self):
        self.run_script('rawbol.db', '''
            CREATE TABLE upload_logs (id INTEGER PRIMARY KEY, filename TEXT, lot_number TEXT, bol_location TEXT,
                import_date TEXT, rows_imported INTEGER, uploaded_at TEXT, total_client_cost REAL, shipping_cost REAL);
            INSERT INTO upload_logs VALUES (1, 'feb.xls', 'LOT-A', 'NJ', '2026-02-18', 2, '', 1000.0, 200.0);
            INSERT INTO upload_logs VALUES (2, 'mar.xls', 'LOT-B', 'NJ', '2026-03-10', 1, '', 500.0, NULL);
            INSERT INTO upload_logs VALUES (3, 'junk.xls', '111', 'NJ', '2025-11-01', NULL, '', NULL, NULL);
            CREATE TABLE raw_bol_items (id INTEGER PRIMARY KEY, upc TEXT, item_description TEXT, avg_cost REAL, image_url TEXT,
                quantity INTEGER, lot_number TEXT, bol_location TEXT, import_date TEXT, created_at TEXT,
                aftersale_quantity INTEGER, original_retail REAL);
            INSERT INTO raw_bol_items VALUES (1, '028199270349', 'Frame', 10.0, '', 4, 'LOT-A', '', '2026-02-18', '', NULL, 100.0);
            INSERT INTO raw_bol_items VALUES (2, '035886415860', 'Knife block', 40.0, '', 1, 'LOT-A', '', '2026-02-18', '', NULL, 400.0);
            INSERT INTO raw_bol_items VALUES (3, '028199270349', 'Frame', 12.0, '', 2, 'LOT-B', '', '2026-03-10', '', NULL, 100.0);
        ''')
        self.run_script('amazonStore.db', '''
            CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, UPC TEXT);
            INSERT INTO ITEMS VALUES (1, 'B0KNIFE123', 'SKU-K', '035886415860');
        ''')
        self.run_script('searchRack.db', '''
            CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, QUANTITY INTEGER);
            INSERT INTO SEARCHRACK VALUES (1, 'Frame', '028199270349-2', 3);
            INSERT INTO SEARCHRACK VALUES (2, 'Mystery', '999999999999', 1);
        ''')
        self.run_script('sold.db', '''
            CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, item_id TEXT, title TEXT, quantity INTEGER, price REAL,
                paid_time TEXT, seller_fee REAL, shipping_cost REAL, store TEXT, barcode TEXT, lot_number TEXT,
                source_base_upc TEXT, source_upc TEXT, sku TEXT);
            -- explicit lot, on manifest, qty 2
            INSERT INTO orders VALUES (1, 'A-1', '', 'Frame', 2, 30.0, '2026-03-05T10:00:00Z', 4.0, 6.0, 'amazon', '028199270349', 'LOT-A', NULL, NULL, NULL);
            -- no lot, UPC on both manifests, sold after LOT-B arrived -> inferred LOT-B
            INSERT INTO orders VALUES (2, 'A-2', '', 'Frame', 1, 25.0, '2026-04-01T10:00:00Z', 3.0, 5.0, 'ebay', '28199270349.0', '', NULL, NULL, NULL);
            -- explicit LOT-B but UPC not on LOT-B manifest -> extra item of LOT-B, cost borrowed from LOT-A row
            INSERT INTO orders VALUES (3, 'A-3', 'B0KNIFE123', 'Knife block', 1, 200.0, '2026-04-02T10:00:00Z', 30.0, 10.0, 'amazon', 'B0KNIFE123', 'LOT-B', NULL, NULL, NULL);
            -- UPC on no manifest, no lot -> not on BOL
            INSERT INTO orders VALUES (4, 'A-4', '', 'Mystery mug', 1, 15.0, '2026-04-03T10:00:00Z', 2.0, 4.0, 'ebay', '999999999999', '', NULL, NULL, NULL);
            -- Facebook cash sale, no fees
            INSERT INTO orders VALUES (5, 'FB-1', '', 'Cash sale', 1, 40.0, '2026-04-04 10:00:00', NULL, NULL, 'marketplace', '028199270349', 'LOT-A', NULL, NULL, NULL);
            -- barcode typed as a price -> excluded
            INSERT INTO orders VALUES (6, 'FB-2', '', '', 1, 88235731340.0, '2026-01-17 12:52:57', NULL, NULL, 'marketplace', '1', '', NULL, NULL, NULL);
            -- test store -> excluded
            INSERT INTO orders VALUES (7, 'T-1', '', 'Test', 1, 10.0, '2026-03-17T00:00:00Z', NULL, NULL, 'test', '1', 'LOT-A', NULL, NULL, NULL);
            -- lot label that exists only on orders -> pseudo lot bucket
            INSERT INTO orders VALUES (9, 'A-9', '', 'Loose item', 1, 50.0, '2026-05-01T10:00:00Z', 5.0, 5.0, 'amazon', '777777777777', 'lostlot', NULL, NULL, NULL);
            -- unpaid order -> ignored
            INSERT INTO orders VALUES (8, 'A-8', '', 'Unpaid', 1, 99.0, NULL, NULL, NULL, 'amazon', '028199270349', 'LOT-A', NULL, NULL, NULL);
            CREATE TABLE returns (id INTEGER PRIMARY KEY, original_order_id INTEGER, order_id TEXT, item_id TEXT, barcode TEXT, title TEXT,
                quantity INTEGER, original_price REAL, refund_amount REAL, original_shipping_cost REAL, return_shipping_cost REAL,
                original_seller_fee REAL, seller_fee_refund REAL, return_date TEXT, store TEXT, return_reason TEXT);
            INSERT INTO returns VALUES (1, 1, 'A-1', '', '028199270349', 'Frame', 1, 30.0, 30.0, 6.0, 5.0, 4.0, 4.0, '2026-04-10', 'amazon', '');
            -- duplicate re-sync of the same return is ignored
            INSERT INTO returns VALUES (2, 1, 'A-1', '', '028199270349', 'Frame', 1, 30.0, 30.0, 6.0, 5.0, 4.0, 4.0, '2026-04-10', 'amazon', '');
            -- return with no order at all
            INSERT INTO returns VALUES (3, NULL, 'ZZZ', '', '', 'Orphan', 1, 20.0, 20.0, 3.0, 0.0, 0, 0, '2026-04-12', 'ebay', 'Refund');
            CREATE TABLE payouts (id INTEGER PRIMARY KEY, store TEXT, settlement_id TEXT, start_date TEXT, end_date TEXT,
                payout_date TEXT, amount REAL, currency TEXT, status TEXT, transaction_count INTEGER, synced_at TEXT);
            INSERT INTO payouts VALUES (1, 'amazon', 'S1', '2026-03-01', '2026-03-14', '2026-03-14T20:00:00Z', 150.0, 'USD', 'Closed', 0, '2026-09-01 08:00:00');
            INSERT INTO payouts VALUES (2, 'ebay', 'S2', '2026-04-01', '2026-04-07', '2026-04-07T08:00:00Z', 80.0, 'USD', 'Closed', 0, '2026-09-02 08:00:00');
            INSERT INTO payouts VALUES (3, 'amazon', 'S3', '2026-04-15', NULL, NULL, -9.0, 'USD', 'Open', 0, '2026-09-02 08:00:00');
            INSERT INTO payouts VALUES (4, 'amazon', 'S4', '2026-02-01', '2026-02-14', '2026-02-14T20:00:00Z', 0.0, 'USD', 'Closed', 0, '2026-09-02 08:00:00');
            CREATE TABLE pnl_expenses (id INTEGER PRIMARY KEY, expense_date TEXT, category TEXT, amount REAL, note TEXT, created_at TEXT);
            INSERT INTO pnl_expenses VALUES (1, '2026-04-20', 'Shipping supplies', 25.5, 'boxes', '');
        ''')

    def build(self):
        return build_pnl(self.root, now=dt.datetime(2026, 9, 13, 12, 0))

    def lot(self, report, number):
        return next(lot for lot in report['lots'] if lot['lot_number'] == number)

    def test_upc_key_normalises_suffixes_floats_and_leading_zeros(self):
        self.assertEqual(upc_key('028199270349-2'), '28199270349')
        self.assertEqual(upc_key('28199270349.0'), '28199270349')
        self.assertEqual(upc_key(' B0KNIFE123 '), 'b0knife123')
        self.assertEqual(upc_key('nan'), '')

    def test_missing_databases_still_produce_an_empty_report(self):
        report = self.build()
        self.assertTrue(report['success'])
        self.assertEqual(report['lots'], [])
        self.assertEqual(report['months'], [])
        self.assertEqual(report['totals']['cash_in'], 0.0)

    def test_data_errors_are_excluded_and_listed(self):
        self.seed()
        report = self.build()
        reasons = {row['id']: row['reason'] for row in report['data_quality']['excluded_orders']}
        self.assertIn(6, reasons)
        self.assertIn('barcode', reasons[6])
        self.assertEqual(reasons[7], 'test store')
        self.assertEqual(report['data_quality']['orders_counted'], 6)
        self.assertLess(report['totals']['revenue'], 1000)

    def test_orders_are_attributed_to_lots_with_extras_and_inference(self):
        self.seed()
        report = self.build()
        lot_a = self.lot(report, 'LOT-A')
        lot_b = self.lot(report, 'LOT-B')

        # LOT-A: order 1 (2 units, $60) + cash sale 5 ($40); refund of $35 on order 1.
        self.assertEqual(lot_a['sold']['orders'], 2)
        self.assertEqual(lot_a['sold']['units'], 3)
        self.assertEqual(lot_a['sold']['revenue'], 100.0)
        self.assertEqual(lot_a['sold']['refunds'], 35.0)
        self.assertEqual(lot_a['sold']['net_sales'], 100.0 - 4.0 - 6.0 - 35.0)
        self.assertEqual(lot_a['landed_cost'], 1200.0)
        self.assertEqual(lot_a['manifested_units'], 5)
        self.assertEqual(lot_a['sell_through_pct'], 60.0)
        self.assertEqual(lot_a['remaining_units'], 2)
        self.assertEqual(lot_a['extra']['orders'], 0)
        self.assertFalse(lot_a['freight_missing'])
        # cogs = 3 units x $10, freight = 3 x (200 / 5)
        self.assertEqual(lot_a['cogs'], 30.0)
        self.assertEqual(lot_a['freight_alloc'], 120.0)
        self.assertEqual(lot_a['margin'], round(55.0 - 30.0 - 120.0, 2))

        # LOT-B: order 2 inferred by UPC (sold after LOT-B arrived), order 3 extra via ASIN map.
        self.assertEqual(lot_b['sold']['orders'], 2)
        self.assertEqual(lot_b['inferred_orders'], 1)
        self.assertEqual(lot_b['extra']['orders'], 1)
        self.assertEqual(lot_b['extra']['revenue'], 200.0)
        self.assertEqual(lot_b['manifest_units_sold'], 1)
        self.assertTrue(lot_b['freight_missing'])
        self.assertEqual(lot_b['landed_cost'], 500.0)
        self.assertEqual(lot_b['sold']['net_sales'], (25.0 - 3.0 - 5.0) + (200.0 - 30.0 - 10.0))
        self.assertEqual(lot_b['recovery_pct'], round(177.0 / 500.0 * 100, 1))
        # knife cost is borrowed from the LOT-A manifest row, frame from LOT-B
        self.assertEqual(lot_b['cogs'], 12.0 + 40.0)
        self.assertEqual(lot_b['no_cost_orders'], 0)
        # on hand: suffixed frame units go to the latest lot that lists the UPC
        self.assertEqual(lot_b['on_hand_units'], 3)
        self.assertEqual(lot_a['on_hand_units'], 0)

        not_on_bol = report['buckets']['not_on_bol']
        self.assertEqual(not_on_bol['orders'], 1)
        self.assertEqual(not_on_bol['revenue'], 15.0)
        self.assertEqual(report['data_quality']['orders_not_on_bol'], 1)
        self.assertEqual(report['data_quality']['on_hand_units_not_on_any_manifest'], 1)

    def test_lot_label_without_manifest_is_a_bucket_not_a_pallet(self):
        self.seed()
        report = self.build()
        self.assertNotIn('lostlot', [lot['lot_number'] for lot in report['lots']])
        pseudo = report['buckets']['pseudo_lots']
        self.assertEqual(len(pseudo), 1)
        self.assertEqual(pseudo[0]['lot_number'], 'lostlot')
        self.assertEqual(pseudo[0]['orders'], 1)
        self.assertEqual(pseudo[0]['net_sales'], 40.0)
        self.assertEqual(report['data_quality']['lots_not_in_upload_logs'], [])
        self.assertEqual(report['data_quality']['payouts_first_month'], '2026-03')
        self.assertEqual(report['data_quality']['orders_first_month'], '2026-03')

    def test_junk_lot_is_flagged_not_hidden(self):
        self.seed()
        report = self.build()
        junk = self.lot(report, '111')
        self.assertEqual(junk['manifested_units'], 0)
        self.assertIsNone(junk['goods_cost'])
        self.assertIn('111', report['data_quality']['lots_missing_goods_cost'])
        self.assertIn('111', report['data_quality']['lots_without_manifest_rows'])
        self.assertEqual(report['data_quality']['lots_missing_freight'], ['111', 'LOT-B'])

    def test_cash_view_uses_closed_payouts_lot_spend_and_expenses(self):
        self.seed()
        report = self.build()
        months = {row['month']: row for row in report['months']}
        self.assertEqual(months['2026-02']['lot_goods'], 1000.0)
        self.assertEqual(months['2026-02']['lot_freight'], 200.0)
        self.assertEqual(months['2026-02']['payouts_amazon'], 0.0)  # $0 closed settlement is not cash
        self.assertEqual(months['2026-03']['payouts_amazon'], 150.0)
        self.assertEqual(months['2026-03']['lot_goods'], 500.0)
        self.assertEqual(months['2026-04']['payouts_ebay'], 80.0)
        self.assertEqual(months['2026-04']['cash_sales'], 40.0)
        self.assertEqual(months['2026-04']['expenses'], 25.5)
        self.assertEqual(months['2026-04']['cash_in'], 120.0)
        self.assertEqual(months['2026-04']['cash_out'], 25.5)
        self.assertEqual(months['2026-04']['refunds'], 35.0 + 23.0)  # matched + orphan return
        self.assertEqual(months['2026-04']['net'], 94.5)
        self.assertEqual(months['2026-04']['cumulative'], round(-1200.0 + (150.0 - 500.0) + 94.5, 2))
        totals = report['totals']
        self.assertEqual(totals['cash_in'], 270.0)
        self.assertEqual(totals['cash_out'], 1725.5)
        self.assertEqual(totals['net'], -1455.5)
        quality = report['data_quality']
        self.assertEqual(len(quality['open_settlements']), 1)
        self.assertEqual(len(quality['zero_closed_settlements']), 1)
        self.assertEqual(quality['payouts_last_synced'], '2026-09-02 08:00:00')
        self.assertEqual(quality['returns_last_date'], '2026-04-12')
        self.assertEqual(quality['returns_days_stale'], 154)
        self.assertEqual(quality['returns_unmatched'], 1)
        self.assertEqual(quality['returns_unmatched_amount'], 23.0)

    def test_store_summary_charges_orphan_returns_to_the_store(self):
        self.seed()
        report = self.build()
        stores = {row['store']: row for row in report['stores']}
        self.assertEqual(stores['ebay']['orders'], 2)
        self.assertEqual(stores['ebay']['refunds'], 23.0)
        self.assertEqual(stores['ebay']['net_sales'], (25.0 - 3.0 - 5.0) + (15.0 - 2.0 - 4.0) - 23.0)
        self.assertEqual(stores['marketplace']['fees'], 0.0)
        self.assertEqual(stores['amazon']['refunds'], 35.0)
        self.assertEqual(stores['amazon']['orders'], 3)


if __name__ == '__main__':
    unittest.main()
