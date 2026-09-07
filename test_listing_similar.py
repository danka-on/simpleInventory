"""Exercise similar-listing routes with real feature imports and isolated dependencies."""
from test_feature_support import isolated_functions
from pathlib import Path
from types import SimpleNamespace
import re
import unittest
import ebay_mapping

NAMES={'api_listingagent_ebay_catalog_search','_listingagent_score_ebay_candidate','_listingagent_ebay_title_tokens','_listingagent_detect_color_tokens','_listingagent_merge_ebay_candidates','_listingagent_ebay_candidate_key','_listingagent_browse_items_to_candidates'}
class UserError(Exception):
    def __init__(self,message,status_code=400): super().__init__(message);self.status_code=status_code;self.extra={}

def context(args, responder):
    env={'ebay_mapping':ebay_mapping,'re':re,'request':SimpleNamespace(args=args),'jsonify':lambda x:x,'_ListingAgentUserError':UserError,
         '_listingagent_parse_int':lambda x,d:int(x or d),'_listingagent_get_settings':lambda:{},
         '_safe_error':lambda e,c:str(e),'_ebay_buy_api_request':responder,
         '_listingagent_normalize_ebay_catalog_summary':lambda x:x,
         '_listingagent_ebay_browse_category_fields':lambda x:{'categoryId':'1'},
         '_listingagent_ebay_browse_aspects_to_dict':lambda x:x.get('aspects',{})}
    isolated_functions(NAMES, env)
    return env

def response(items=None,status=200,catalog=False):
    return SimpleNamespace(status_code=status,text='yes',json=lambda:{'productSummaries' if catalog else 'itemSummaries':items or []})

class SimilarListingsTests(unittest.TestCase):
    def test_only_base_upc_reaches_ebay(self):
        for args in [{'upc':'035886267162-1'}, {'q':'035886267162-2'}, {'gtin':'35886267162-3'}]:
            calls=[]
            def request(method,path,**kwargs):
                calls.append(kwargs['params'])
                return response([])
            context(args,request)['api_listingagent_ebay_catalog_search']()
            self.assertEqual(calls[0]['gtin'],'035886267162')
            for params in calls:
                for field in ('gtin','q'):
                    if field in params:
                        self.assertEqual(params[field],'035886267162')

    def test_live_search_precedes_catalog_and_title_fallback(self):
        calls=[]
        def request(method,path,**kwargs):
            calls.append((path,kwargs['params']))
            if kwargs['params'].get('q')=='Sony WH1000XM5':
                return response([{'itemId':'live','title':'Sony WH1000XM5','aspects':{'Brand':'Sony'}}])
            return response([])
        env=context({'upc':'123456789012','title':'Sony WH1000XM5'},request)
        result=env['api_listingagent_ebay_catalog_search']()
        self.assertEqual(calls[0][1]['gtin'],'123456789012')
        self.assertEqual(calls[1][1]['q'],'Sony WH1000XM5')
        self.assertEqual(result['results'][0]['itemId'],'live')
        self.assertIn('/buy/browse/',calls[0][0])

    def test_model_relevance_beats_attribute_count(self):
        env=context({},lambda *a,**k:None)
        score=env['_listingagent_score_ebay_candidate']
        right={'title':'Sony WH1000XM5 Wireless Headphones','aspects':{}}
        wrong={'title':'Sony WH1000XM4 Wireless Headphones','aspects':{str(n):'x' for n in range(80)}}
        self.assertGreater(score(right,preferred_title=right['title']),score(wrong,preferred_title=right['title']))

    def test_variants_keep_separate_specifics(self):
        env=context({},lambda *a,**k:None)
        items=[{'itemId':'red','epid':'same','title':'Mixer','aspects':{'Color':'Red'}},{'itemId':'blue','epid':'same','title':'Mixer','aspects':{'Color':'Blue'}}]
        rows=env['_listingagent_browse_items_to_candidates'](items)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['aspects']['Color'] for r in rows},{'Red','Blue'})

    def test_manual_query_and_partial_failure(self):
        calls=[]
        def request(method,path,**kwargs):
            calls.append(kwargs['params'])
            if '/buy/browse/' in path: raise RuntimeError('offline')
            return response([{'epid':'catalog','title':'Mixer','source':'catalog'}],catalog=True)
        result=context({'q':'Mixer'},request)['api_listingagent_ebay_catalog_search']()
        self.assertEqual(calls[0]['q'],'Mixer')
        self.assertEqual(result['results'][0]['epid'],'catalog')
        self.assertTrue(result['warnings'])

if __name__=='__main__': unittest.main()
