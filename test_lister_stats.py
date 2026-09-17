"""Lister Stats: per-day and per-person counts built from what the side panel already records."""
from contextlib import closing
import datetime
import sqlite3
import unittest
from unittest.mock import patch

import lister_stats
import test_lister

UPC = test_lister.UPC


class ListerStatsTest(unittest.TestCase):
    # Same temporary app and databases as test_lister, without inheriting (and re-running) its tests.
    setUp = test_lister.ListerTestCase.setUp
    seed = test_lister.ListerTestCase.seed
    sql = test_lister.ListerTestCase.sql

    def link(self, upc, platform, created_at, actor='dan@example.com', price=10.0, quantity=1, kind='listed', **keys):
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            self.lister.init_tables(conn.cursor())
            conn.execute('''INSERT INTO listing_links (upc, platform, listing_id, sku, title, price, quantity, created_by, source, kind, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'extension', ?, ?)''',
                         (upc, platform, keys.get('listing_id'), keys.get('sku'), 'Item ' + upc, price, quantity, actor, kind, created_at))
            conn.commit()

    def test_a_listing_the_store_already_had_does_not_count_as_lister_output(self):
        now = datetime.datetime.now().replace(microsecond=0).isoformat()
        self.link(UPC + '-1', 'ebay', now, listing_id='111')
        self.link(UPC + '-2', 'ebay', now, listing_id='222', kind='existing')
        data = self.client.get('/api/lister/stats?days=7').get_json()
        self.assertEqual(data['totals']['today']['listed'], 1)
        self.assertEqual([l['upc'] for l in data['listings']], [UPC + '-1'])

    def test_stats_count_listings_per_day_store_and_person(self):
        today = datetime.date.today()
        now = datetime.datetime.now().replace(microsecond=0)
        yesterday = (today - datetime.timedelta(days=1)).isoformat()
        self.link(UPC + '-1', 'ebay', (now - datetime.timedelta(minutes=20)).isoformat(), listing_id='111', price=20.0, quantity=2)
        self.link(UPC + '-2', 'ebay', (now - datetime.timedelta(minutes=10)).isoformat(), listing_id='222')
        self.link(UPC + '-3', 'amazon', now.isoformat(), actor='ona@example.com', sku='AMZ-3', price=5.0)
        self.link('012345678905', 'amazon', yesterday + 'T09:00:00', sku='AMZ-4')
        self.link('012345678906', 'ebay', (today - datetime.timedelta(days=40)).isoformat() + 'T09:00:00', listing_id='333')
        self.client.post('/api/lister/queue/' + UPC + '-9/skip', json={'platform': 'amazon', 'actor': 'dan@example.com'})
        self.client.post('/api/lister/learn', json={'upc': UPC + '-1', 'platform': 'ebay', 'step': 'category', 'chosen': 'A > B',
                                                    'suggested': 'A > B', 'actor': 'dan@example.com'})
        with closing(sqlite3.connect(self.root / 'sold.db')) as conn:
            conn.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, item_id TEXT, sku TEXT, paid_time TEXT, title TEXT, quantity INTEGER, price REAL)')
            conn.execute("INSERT INTO orders (order_id, item_id, sku, paid_time, title, quantity, price) VALUES ('O-1', '111', 'x', '2026-09-17', 'Item', 1, 19.5)")
            conn.commit()
        ai_db = self.root / 'ai_usage_test.db'
        with closing(sqlite3.connect(ai_db)) as conn:
            conn.execute('CREATE TABLE ai_usage (id INTEGER PRIMARY KEY, created_at TEXT, provider TEXT, model TEXT, feature TEXT, cost_usd REAL)')
            utc_now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            conn.executemany('INSERT INTO ai_usage (created_at, provider, model, feature, cost_usd) VALUES (?, ?, ?, ?, ?)',
                             [(utc_now, 'anthropic', 'haiku', 'Lister title', 0.001), (utc_now, 'openai', 'img', 'Lister AI photoshop', 0.04),
                              (utc_now, 'openai', 'whisper', 'Voice note transcription', 0.01)])
            conn.commit()

        with patch.object(lister_stats.ListerStats, '_ai_path', lambda s: ai_db):
            data = self.client.get('/api/lister/stats?days=7').get_json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['totals']['today']['listed'], 3)
        self.assertEqual((data['totals']['today']['ebay'], data['totals']['today']['amazon']), (2, 1))
        self.assertEqual(data['totals']['today']['value'], 55.0)
        self.assertEqual(data['totals']['week']['listed'], 4)
        self.assertEqual(data['totals']['all']['listed'], 5)
        self.assertEqual(data['totals']['today']['skipped'], 1)
        self.assertEqual(data['pace']['avg_minutes'], 10.0)
        self.assertEqual(len(data['series']), 7)
        self.assertEqual(data['series'][-1]['date'], today.isoformat())
        self.assertEqual(data['series'][-2]['amazon'], 1)
        self.assertEqual([u['actor'] for u in data['users']], ['dan@example.com', 'ona@example.com'])
        self.assertEqual(data['users'][0]['listed'], 3)
        self.assertEqual(data['people'], ['dan@example.com', 'ona@example.com'])
        self.assertEqual(data['sold'], {'listings': 1, 'orders': 1, 'revenue': 19.5})
        self.assertEqual((data['ai']['title'], data['ai']['photo'], data['ai']['description']), (1, 1, 0))
        self.assertEqual(data['suggestions'], {'asked': 1, 'agreed': 1})
        self.assertEqual(len(data['listings']), 4)
        self.assertEqual(data['listings'][0]['platform'], 'amazon')

        mine = self.client.get('/api/lister/stats?days=30&actor=ona@example.com').get_json()
        self.assertEqual(mine['totals']['range']['listed'], 1)
        self.assertEqual(mine['users'][0]['actor'], 'ona@example.com')
        self.assertEqual(mine['ai']['title'], 0)

        page = self.client.get('/lister-stats')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Lister Stats', page.data)

    def test_stats_empty_and_bad_range(self):
        data = self.client.get('/api/lister/stats?days=abc').get_json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['days'], 30)
        self.assertEqual(data['totals']['all']['listed'], 0)
        self.assertEqual(data['users'], [])


if __name__ == '__main__':
    unittest.main()
