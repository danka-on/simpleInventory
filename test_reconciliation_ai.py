import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

import reconciliation_ai as ai


class WarehouseClaudeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        env = patch.dict('os.environ', {'ANTHROPIC_API_KEY':'test-only'})
        env.start()
        self.addCleanup(env.stop)
        self.rows = [dict(id=i, barcode=f'CUSTOM-{i}', title=f'Item {i}',
                         alternate_titles=['Dream Forever custom mug'] if i == 100 else [],
                         note='blue ceramic' if i == 100 else '') for i in range(1, 101)]
        self.catalog = ai.Catalog(self.rows)
        self.listing = dict(store='ebay', listing_key='a', title='Yinka Ilori DREAM FOREVER Mug',
                            suggestions=[{'searchrack_id':1}], image='https://no-images.invalid/a.jpg')
        self.match = dict(searchrack_id=100, verdict='possible', reason='Custom name agrees; confirm brand.')

    def responses(self, matches=None, stop='end_turn'):
        return [Mock(status_code=200, json=lambda:{'input_tokens':1234}),
                Mock(status_code=200, json=lambda:{'stop_reason':stop, 'content':[{'type':'text',
                    'text':json.dumps({'matches':[self.match] if matches is None else matches})}],
                    'usage':{'input_tokens':1234, 'output_tokens':100}})]

    def test_entire_catalog_custom_names_and_text_only_then_cached(self):
        with patch.object(ai.requests, 'post', side_effect=self.responses()) as post:
            result, hit = ai.search(self.conn, self.listing, self.catalog)
            self.assertFalse(hit)
            self.assertEqual(result['matches'][0]['searchrack_id'],100)
            self.assertEqual(result['catalog_rows'],100)
            body = post.call_args.kwargs['json']
            content = body['messages'][0]['content']
            self.assertTrue(all(b['type']=='text' for b in content))
            sent = json.loads(content[0]['text'].split('\n',1)[1])
            self.assertEqual(len(sent),100)
            self.assertIn('Dream Forever custom mug', sent[-1]['names'])
            self.assertNotIn('https://no-images', json.dumps(body))
            self.assertEqual(result['estimated_cost_usd'],.001734)
            self.assertEqual(ai.search(self.conn, self.listing, self.catalog),(result,True))
            self.assertEqual(post.call_count,2)

    def test_catalog_and_listing_changes_invalidate_but_locations_do_not(self):
        with patch.object(ai.requests, 'post', side_effect=self.responses()):
            ai.search(self.conn, self.listing, self.catalog)
        self.assertIsNotNone(ai.cached(self.conn,self.listing,self.catalog))
        self.rows[-1]['location']='B2'
        self.assertEqual(ai.Catalog(self.rows).digest,self.catalog.digest)
        self.rows[-1]['alternate_titles'].append('new custom name')
        self.assertIsNone(ai.cached(self.conn,self.listing,ai.Catalog(self.rows)))
        self.assertIsNone(ai.cached(self.conn,dict(self.listing,title='Other item'),self.catalog))
        self.assertIsNone(ai.cached(self.conn,self.listing,ai.Catalog(self.rows[:-1])))
        self.conn.execute('UPDATE reconciliation_text_cache SET checked_at=0')
        self.assertIsNone(ai.cached(self.conn,self.listing,self.catalog))

    def test_no_match_is_cached(self):
        with patch.object(ai.requests, 'post', side_effect=self.responses([])) as post:
            result, hit=ai.search(self.conn,self.listing,self.catalog)
            self.assertEqual(result['matches'],[])
            self.assertTrue(ai.search(self.conn,self.listing,self.catalog)[1])
            self.assertEqual(post.call_count,2)

    def test_invalid_or_truncated_answers_never_cached(self):
        for matches,stop in [([dict(self.match,searchrack_id=999)],'end_turn'),
                             ([self.match,self.match],'end_turn'),
                             ([dict(self.match,verdict='confirmed')],'end_turn'),
                             ([dict(self.match,reason='')],'end_turn'),
                             ([self.match],'max_tokens')]:
            with self.subTest(matches=matches,stop=stop):
                with patch.object(ai.requests,'post',side_effect=self.responses(matches,stop)):
                    with self.assertRaisesRegex(ValueError,'invalid answer'):
                        ai.search(self.conn,self.listing,self.catalog)
                self.assertIsNone(ai.cached(self.conn,self.listing,self.catalog))

    def test_count_limit_stops_paid_call_instead_of_searching_subset(self):
        with patch.object(ai.requests,'post',return_value=Mock(status_code=200,json=lambda:{'input_tokens':200000})) as post:
            with self.assertRaisesRegex(ValueError,'No paid search'):
                ai.search(self.conn,self.listing,self.catalog)
            self.assertEqual(post.call_count,1)
            self.assertTrue(post.call_args.args[0].endswith('/count_tokens'))

    def test_service_failure_leaves_no_result_and_releases_lock(self):
        with patch.object(ai.requests,'post',side_effect=ai.requests.Timeout):
            with self.assertRaisesRegex(ValueError,'unchanged'):
                ai.search(self.conn,self.listing,self.catalog)
        self.assertIsNone(ai.cached(self.conn,self.listing,self.catalog))
        self.assertTrue(ai._lock.acquire(blocking=False))
        ai._lock.release()

    def test_missing_key_empty_catalog_and_concurrency_do_not_call(self):
        with patch.object(ai.requests,'post') as post:
            with patch.dict('os.environ',{'ANTHROPIC_API_KEY':''}):
                with self.assertRaisesRegex(ValueError,'not configured'):
                    ai.search(self.conn,self.listing,self.catalog)
            with self.assertRaisesRegex(ValueError,'no stocked'):
                ai.search(self.conn,self.listing,ai.Catalog([]))
            ai._lock.acquire()
            try:
                with self.assertRaisesRegex(ValueError,'Another'):
                    ai.search(self.conn,self.listing,self.catalog)
            finally:
                ai._lock.release()
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
