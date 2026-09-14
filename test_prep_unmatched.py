import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from PIL import Image
from prep_unmatched import register, gtin_key

UPC = '035886415884'


def photo():
    stream = io.BytesIO(); Image.new('RGB',(24,24),'red').save(stream,'JPEG')
    return stream.getvalue()


class PrepUnmatchedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.app=Flask(__name__,static_folder=str(self.root/'static'))
        @contextmanager
        def db(name):
            conn=sqlite3.connect(self.root/name); conn.row_factory=sqlite3.Row
            try:
                yield conn; conn.commit()
            except Exception:
                conn.rollback(); raise
            finally: conn.close()
        self.db=db
        def registry(cur):
            cur.execute('CREATE TABLE IF NOT EXISTS custom_item_registry(upc TEXT PRIMARY KEY,item_description TEXT,image_url TEXT,reserved_at TEXT,updated_at TEXT)')
        def ensure_prep():
            with db('bol.db') as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS items_prep_images(id INTEGER PRIMARY KEY,upc TEXT,row_status TEXT,image_path TEXT,created_at TEXT)")
        with db('bol.db') as conn:
            conn.execute('CREATE TABLE bol_items(upc TEXT,item_description TEXT,image_url TEXT,lot_number TEXT,bol_number TEXT,import_date TEXT)')
        self.service=register(self.app,db_connection=db,normalize_upc=lambda x:str(x or '').strip().lstrip('0'),
            reject_title=lambda x:'Enter a real item name.' if x.lower() in ('unknown','item') else None,
            ensure_registry=registry,ensure_prep=ensure_prep,amazon_context=lambda: ({},'seller','US','market'),
            ebay_request=lambda *a,**k:None, clear_cache=lambda:None)
        self.client=self.app.test_client()

    def rows(self,sql):
        with self.db('bol.db') as conn: return [tuple(row) for row in conn.execute(sql)]

    def lookup(self, amazon=None, ebay=None, upcitemdb=None):
        with patch.object(self.service,'amazon_matches',return_value=amazon or []),patch.object(self.service,'ebay_matches',return_value=ebay or []),\
             patch.object(self.service,'upcitemdb_matches',return_value=upcitemdb or []):
            return self.client.get('/api/items-prep/unmatched/lookup?upc='+UPC).get_json()

    def upcitemdb_web(self, items, status=200):
        calls=[]
        def get(url, params=None, timeout=None, stream=False):
            calls.append(url)
            if url.startswith('https://api.upcitemdb.com/'):
                return SimpleNamespace(status_code=status,json=lambda:{'items':items},raise_for_status=lambda:None)
            return SimpleNamespace(status_code=200,headers={'Content-Type':'image/jpeg'},close=lambda:None)
        return calls, patch('prep_unmatched.requests.get',side_effect=get)

    def manual(self,**overrides):
        data=dict(upc=UPC,title='KitchenAid stand mixer',request_id='a'*32,approved='true',lot_number='LOT1',
                  name_photo=(io.BytesIO(photo()),'label.jpg'),item_photo=(io.BytesIO(photo()),'item.jpg'))
        data.update(overrides)
        return self.client.post('/api/items-prep/unmatched/manual',data=data,content_type='multipart/form-data')

    def test_gtin_equivalence_and_invalid_values(self):
        self.assertEqual(gtin_key(UPC),gtin_key('0'+UPC))
        self.assertFalse(gtin_key('035886415885'))
        self.assertFalse(gtin_key('not a upc'))

    def test_both_sources_and_no_adoption_before_approval(self):
        a=self.service.candidate('amazon','Knife block','https://img/a','ASIN',UPC)
        e=self.service.candidate('ebay','Knife block set','https://img/e','ITEM',UPC)
        data=self.lookup([a],[e])
        self.assertEqual([x['source'] for x in data['candidates']],['amazon','ebay'])
        self.assertEqual(self.rows('SELECT * FROM bol_items'),[])
        candidate=data['candidates'][0]
        body=dict(upc=UPC,match_id=candidate['match_id'],title='Wrong title',image_url='https://wrong',lot_number='L1')
        self.assertEqual(self.client.post('/api/items-prep/unmatched/adopt',json=body).status_code,400)
        body['approved']=True
        self.assertEqual(self.client.post('/api/items-prep/unmatched/adopt',json=body).status_code,200)
        self.assertEqual(self.rows('SELECT item_description,image_url,lot_number FROM bol_items'),[('Knife block','https://img/a','L1')])
        self.assertEqual(self.client.post('/api/items-prep/unmatched/adopt',json=body).status_code,200)
        self.assertEqual(len(self.rows('SELECT * FROM bol_items')),1)

    def test_other_upc_cannot_adopt_match(self):
        data=self.lookup([self.service.candidate('amazon','Knife set','https://img/a','A',UPC)])
        r=self.client.post('/api/items-prep/unmatched/adopt',json=dict(upc='99999',match_id=data['candidates'][0]['match_id'],approved=True))
        self.assertEqual(r.status_code,400)

    def test_unavailable_is_not_a_no_match(self):
        with patch.object(self.service,'amazon_matches',side_effect=RuntimeError()),patch.object(self.service,'ebay_matches',return_value=[]),\
             patch.object(self.service,'upcitemdb_matches',side_effect=RuntimeError()):
            data=self.client.get('/api/items-prep/unmatched/lookup?upc='+UPC).get_json()
        self.assertEqual([t['result'] for t in data['tried']],['unavailable','no exact match','unavailable'])

    def test_bad_barcode_skips_external_calls(self):
        with patch.object(self.service,'amazon_matches') as amazon,patch.object(self.service,'ebay_matches') as ebay,\
             patch.object(self.service,'upcitemdb_matches') as upcitemdb:
            data=self.client.get('/api/items-prep/unmatched/lookup?upc=STORE123').get_json()
        amazon.assert_not_called(); ebay.assert_not_called(); upcitemdb.assert_not_called(); self.assertEqual(data['candidates'],[])

    def test_ebay_requires_exact_identifier_not_title_keyword(self):
        calls=[]
        def call(method,path,**kw):
            calls.append((path,kw))
            if path.endswith('/search'): data={'itemSummaries':[{'itemId':'wrong'},{'itemId':'right'}]}
            elif path.endswith('wrong'): data={'title':UPC+' knife set','gtin':'035886415885'}
            else: data={'title':'Correct set','localizedAspects':[{'name':'UPC','value':UPC}],'image':{'imageUrl':'https://img/a'}}
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:data)
        with patch.object(self.service,'ebay_request',side_effect=call): results=self.service.ebay_matches(UPC)
        self.assertEqual([r['catalog_id'] for r in results],['right'])
        self.assertEqual(calls[0][1]['params']['gtin'],UPC); self.assertNotIn('q',calls[0][1]['params'])

    def test_amazon_requires_returned_identifiers(self):
        items=[dict(asin='wrong',summaries=[dict(marketplaceId='US',itemName=UPC+' wrong item')]),
               dict(asin='right',identifiers=[dict(marketplaceId='US',identifiers=[dict(identifierType='UPC',identifier=UPC)])],summaries=[dict(marketplaceId='US',itemName='Correct set')])]
        with patch('sp_api.api.CatalogItems') as catalog:
            catalog.return_value.search_catalog_items.return_value=SimpleNamespace(payload={'items':items})
            result=self.service.amazon_matches(UPC)
        self.assertEqual([r['catalog_id'] for r in result],['right'])

    def test_upcitemdb_is_asked_only_when_marketplaces_find_nothing(self):
        amazon=self.service.candidate('amazon','Knife block','https://img/a','ASIN',UPC)
        with patch.object(self.service,'amazon_matches',return_value=[amazon]),patch.object(self.service,'ebay_matches',return_value=[]),\
             patch.object(self.service,'upcitemdb_matches') as upcitemdb:
            data=self.client.get('/api/items-prep/unmatched/lookup?upc='+UPC).get_json()
        upcitemdb.assert_not_called()
        self.assertEqual([t['source'] for t in data['tried']],['Amazon','eBay'])
        found=self.service.candidate('upcitemdb','Tie up shade','https://img/u','0'+UPC,UPC)
        data=self.lookup(upcitemdb=[found])
        self.assertEqual([(c['source_label'],c['title']) for c in data['candidates']],[('UPCitemdb','Tie up shade')])
        self.assertEqual(data['tried'][-1],{'source':'UPCitemdb','result':'found'})

    def test_upcitemdb_needs_the_exact_code_and_an_https_photo(self):
        items=[dict(upc='049000028911',title='Other item',images=['https://img.example/other.jpg']),
               dict(ean='0'+UPC,title='Tie up shade',images=['http://10.0.0.5/inside.jpg','http://img.example/shade.jpg'])]
        calls,web=self.upcitemdb_web(items)
        with web: result=self.service.upcitemdb_matches(UPC)
        self.assertEqual([(r['title'],r['image_url'],r['source_label']) for r in result],[('Tie up shade','https://img.example/shade.jpg','UPCitemdb')])
        self.assertEqual(calls[1:],['https://img.example/shade.jpg'])

    def test_upcitemdb_answers_are_kept_and_misses_retried_after_a_day(self):
        calls,web=self.upcitemdb_web([dict(upc=UPC,title='Tie up shade',images=[])])
        with web:
            self.service.upcitemdb_matches(UPC); again=self.service.upcitemdb_matches('0'+UPC)
        self.assertEqual(len(calls),1); self.assertEqual(again[0]['title'],'Tie up shade')
        other='049000028911'
        calls,web=self.upcitemdb_web([])
        with web:
            self.assertEqual(self.service.upcitemdb_matches(other),[]); self.service.upcitemdb_matches(other)
            self.assertEqual(len(calls),1)
            with self.db('bol.db') as conn:
                conn.execute("UPDATE prep_upcitemdb_lookups SET looked_up_at='2000-01-01T00:00:00+00:00' WHERE gtin=?",(gtin_key(other),))
            self.service.upcitemdb_matches(other)
        self.assertEqual(len(calls),2)

    def test_upcitemdb_daily_limit_is_unavailable_and_not_kept(self):
        calls,web=self.upcitemdb_web([],status=429)
        with web,patch.object(self.service,'amazon_matches',return_value=[]),patch.object(self.service,'ebay_matches',return_value=[]):
            first=self.client.get('/api/items-prep/unmatched/lookup?upc='+UPC).get_json()
            self.client.get('/api/items-prep/unmatched/lookup?upc='+UPC)
        self.assertEqual(first['tried'][-1],{'source':'UPCitemdb','result':'unavailable'})
        self.assertEqual(len(calls),2)
        self.assertEqual(self.rows('SELECT * FROM prep_upcitemdb_lookups'),[])

    def test_manual_saves_both_originals_and_retries_once(self):
        self.assertEqual(self.manual().status_code,200)
        self.assertEqual(self.manual().status_code,200)
        self.assertEqual(len(self.rows('SELECT * FROM bol_items')),1)
        self.assertEqual(len(self.rows('SELECT * FROM items_prep_images')),2)
        files=list((self.root/'static/items_prep/unmatched').iterdir())
        self.assertEqual(len(files),2)
        self.assertTrue(all(f.read_bytes()==photo() for f in files))
        evidence=json.loads(self.rows('SELECT evidence_json FROM prep_unmatched_intake')[0][0])
        self.assertEqual(set(evidence['photos']),{'name','item'})

    def test_missing_photo_or_invalid_name_never_creates_item(self):
        for fields in [dict(name_photo=None),dict(item_photo=None),dict(title=''),dict(title='unknown'),dict(approved='false')]:
            self.assertEqual(self.manual(**fields).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM bol_items'),[])

    def test_invalid_image_rejected(self):
        self.assertEqual(self.manual(name_photo=(io.BytesIO(b'fake jpeg'),'x.jpg')).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM bol_items'),[])

    def test_existing_manifest_not_overwritten(self):
        with self.db('bol.db') as conn: conn.execute('INSERT INTO bol_items(upc,item_description) VALUES (?,?)',(UPC,'Manifest title'))
        self.assertEqual(self.manual().status_code,400)
        self.assertEqual(self.rows('SELECT item_description FROM bol_items'),[('Manifest title',)])

    def test_label_extraction_is_only_a_suggestion_and_can_fail(self):
        for name in ['KitchenAid mixer',None]:
            with patch.object(self.service,'extract_name',return_value=name):
                result=self.client.post('/api/items-prep/unmatched/read-name',data={'name_photo':(io.BytesIO(photo()),'label.jpg')}).get_json()
            self.assertEqual(result['name'],name)
            self.assertEqual(self.rows('SELECT * FROM bol_items'),[])

    def test_extraction_failure_allows_typing(self):
        with patch.object(self.service,'extract_name',side_effect=RuntimeError('unavailable')):
            result=self.client.post('/api/items-prep/unmatched/read-name',data={'name_photo':(io.BytesIO(photo()),'label.jpg')}).get_json()
        self.assertTrue(result['success']); self.assertIsNone(result['name'])


if __name__=='__main__': unittest.main()
