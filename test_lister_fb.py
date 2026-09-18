"""Lister FB list: opt-in per item, review -> ready -> Facebook's workbook -> listed, and the
template itself (categories, conditions, whole-dollar prices, 50-row limit)."""

import io
import unittest

import openpyxl

import fb_lister_template as tpl
from test_lister import UPC, ListerTestCase

HEADERS = {'X-Sweet-Shelves-Lister': '1'}


class FbTemplateTests(unittest.TestCase):
    def test_category_suggestions_use_title_and_ebay_path(self):
        found = [s['category'] for s in tpl.suggest_categories('Ninja Air Fryer 4 Quart', '')]
        self.assertEqual(found[0], 'Home & Kitchen//Kitchen & Dining//Small Kitchen Appliances//Deep Fryers & Air Fryers')
        found = [s['category'] for s in tpl.suggest_categories('Dewalt 20V Drill', 'Home & Garden > Power Tools > Drills')]
        self.assertEqual(found[0], 'Tools & Home Improvement//Tools//Power Tools//Drills')
        self.assertEqual(len(tpl.categories()), 1870)

    def test_conditions_prices_and_text(self):
        self.assertEqual(tpl.fb_condition('NEW_OTHER'), 'New')
        self.assertEqual(tpl.fb_condition('USED_ACCEPTABLE'), 'Used - Fair')
        self.assertEqual(tpl.fb_condition('Used - Good'), 'Used - Good')
        self.assertEqual((tpl.whole_dollars('24.50'), tpl.whole_dollars('$1,299.6'), tpl.whole_dollars('0')), (24, 1300, None))
        self.assertEqual(tpl.plain_text('<p>A <b>plate</b></p><ul><li>Safe</li></ul>', 100), 'A plate\n• Safe')

    def test_workbook_is_facebooks_template_filled_from_row_5(self):
        rows = [{'upc': str(i), 'title': f'Item {i}', 'price': 10 + i, 'condition': 'New', 'description': 'd',
                 'category': tpl.categories()[0]} for i in range(3)]
        book = openpyxl.load_workbook(io.BytesIO(tpl.build_workbook(rows)))
        sheet = book[tpl.SHEET]
        self.assertEqual([c.value for c in sheet[4]][:5], ['TITLE', 'PRICE', 'CONDITION', 'DESCRIPTION', 'CATEGORY'])
        self.assertEqual([c.value for c in sheet[5]][:3], ['Item 0', 10, 'New'])
        self.assertIsNone(sheet.cell(8, 1).value, "Facebook's example row is gone")
        self.assertIn('VALIDATION', book.sheetnames)
        with self.assertRaises(ValueError):
            tpl.build_workbook(rows * 17)  # 51 rows
        with self.assertRaises(ValueError):
            tpl.build_workbook([dict(rows[0], condition='Broken')])


class ListerFbTests(ListerTestCase):
    def fb(self, path, body=None):
        if body is None:
            return self.client.get('/api/lister/fb' + path)
        return self.client.post('/api/lister/fb' + path, json=body, headers=HEADERS)

    def plus_fb(self, upc=UPC, *, on=True, fresh=False):
        return self.client.post(f'/api/lister/queue/{upc}/store', json={'platform': 'fb', 'on': on, 'only': True, 'fresh': fresh},
                                headers=HEADERS)

    def test_fb_is_opt_in_and_a_fresh_add_stays_off_ebay_and_amazon(self):
        # Before "+ FB": no fb key, eBay/Amazon exactly as before.
        states = self.client.post('/api/lister/queue-stores', json={'upcs': [UPC]}).get_json()['items'][UPC]
        self.assertNotIn('fb', states)
        self.assertEqual(self.fb('/queue').get_json()['items'], [])

        res = self.plus_fb(fresh=True)
        self.assertEqual(res.status_code, 200, res.get_json())
        state = res.get_json()['state']
        self.assertEqual((state['fb'], state['ebay'], state['amazon']), ('on', 'off', 'off'))
        self.assertEqual([i['upc'] for i in self.fb('/queue').get_json()['items']], [UPC])
        self.assertEqual(self.client.get('/api/lister/queue?platform=ebay').get_json()['items'], [])

        # Taking it off again.
        self.assertNotIn('fb', self.plus_fb(on=False).get_json()['state'])
        self.assertEqual(self.fb('/queue').get_json()['items'], [])

    def test_adding_fb_to_an_item_already_queued_keeps_ebay_and_amazon(self):
        state = self.plus_fb(fresh=False).get_json()['state']
        self.assertEqual((state['fb'], state['ebay'], state['amazon']), ('on', 'on', 'on'))

    def test_panel_may_report_fb(self):
        dan = dict(HEADERS, **{'Cf-Access-Authenticated-User-Email': 'dan@example.com'})
        res = self.client.post('/api/lister/panel', json={'platform': 'fb'}, headers=dan)
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self.client.get('/api/lister/panel', headers=dan).get_json()['platform'], 'fb')

    def test_review_ready_build_list(self):
        self.plus_fb(fresh=True)
        item = self.fb(f'/item/{UPC}').get_json()
        draft = item['draft']
        self.assertEqual(draft['title'], 'Lenox Butterfly Meadow Dinner Plate')
        self.assertEqual((draft['price'], draft['condition']), (24, 'New'), 'whole dollars; NEW_OTHER -> New')
        self.assertIn('Butterfly Meadow', draft['description'])
        self.assertNotIn('<', draft['description'])
        self.assertIn(draft['category'], tpl.categories())
        self.assertEqual(item['conditions'], list(tpl.CONDITIONS))

        # Ready refuses a bad row, accepts a good one.
        bad = self.fb(f'/item/{UPC}', dict(draft, price='', ready=True))
        self.assertEqual(bad.status_code, 400)
        ok = self.fb(f'/item/{UPC}', dict(draft, price=25, ready=True))
        self.assertEqual(ok.get_json()['status'], 'ready', ok.get_json())

        built = self.fb('/build', {})
        self.assertEqual(built.status_code, 200, built.get_json())
        batch = built.get_json()['batchId']
        self.assertEqual(self.fb('/queue').get_json()['items'][0]['status'], 'in_template')
        self.assertEqual(self.fb(f'/item/{UPC}', dict(draft, ready=True)).status_code, 409, 'locked while in a workbook')

        workbook = self.client.get(f'/api/lister/fb/batch/{batch}.xlsx')
        self.assertEqual(workbook.status_code, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(workbook.data))[tpl.SHEET]
        self.assertEqual([c.value for c in sheet[5]][:3], ['Lenox Butterfly Meadow Dinner Plate', 25, 'New'])

        tracked = []
        self.lister.fb_list.track_listing = lambda upc, **kw: tracked.append((upc, kw))
        listed = self.fb(f'/batch/{batch}/listed', {})
        self.assertEqual(listed.get_json()['listed'], 1, listed.get_json())
        self.assertEqual(tracked[0][0], UPC)
        self.assertIn(('facebook', UPC), self.bol_marks)
        self.assertEqual(self.fb('/queue').get_json()['items'], [])
        states = self.client.post('/api/lister/queue-stores', json={'upcs': [UPC]}).get_json()['items'][UPC]
        self.assertEqual(states['fb'], 'listed')
        self.assertEqual(self.fb(f'/batch/{batch}/listed', {}).status_code, 409)

    def test_cancelled_workbook_puts_items_back_to_ready(self):
        self.plus_fb(fresh=True)
        draft = self.fb(f'/item/{UPC}').get_json()['draft']
        self.fb(f'/item/{UPC}', dict(draft, ready=True))
        batch = self.fb('/build', {}).get_json()['batchId']
        self.assertEqual(self.fb(f'/batch/{batch}/cancel', {}).status_code, 200)
        self.assertEqual(self.fb('/queue').get_json()['items'][0]['status'], 'ready')
        self.assertEqual(self.fb('/build', {'upcs': ['000000000000']}).status_code, 400, 'nothing ready among those')

    def test_category_search_and_guard(self):
        found = self.fb('/categories?q=air fryer').get_json()['categories']
        self.assertIn('Home & Kitchen//Kitchen & Dining//Small Kitchen Appliances//Deep Fryers & Air Fryers', found)
        refused = self.client.post('/api/lister/fb/build', json={}, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(refused.status_code, 403)


def load_tests(loader, tests, pattern):
    """Only this file's tests: ListerFbTests inherits the whole test_lister fixture class."""
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(FbTemplateTests))
    for name in sorted(n for n in vars(ListerFbTests) if n.startswith('test_')):
        suite.addTest(ListerFbTests(name))
    return suite


if __name__ == '__main__':
    unittest.main()
