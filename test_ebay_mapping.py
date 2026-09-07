import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import ebay_mapping as m


class Response:
    def __init__(self, body=None, status=200, xml=''):
        self.body, self.status_code, self.content = body, status, xml.encode()
    def json(self):
        return self.body


class MappingTests(unittest.TestCase):
    def test_suffixed_upc_lookup_preserves_item_identity(self):
        for raw, expected in [('123456789012-1','123456789012'),
                              ('35886267162-2','035886267162'),
                              ('123456789012-OPEN_BOX','123456789012')]:
            data = {'upc':raw, 'title':'Mixer'}
            product, _ = m.product_input(data)
            self.assertEqual(product['externalProductIdentifierInput']['productId'], expected)
            self.assertEqual(data['upc'], raw)
        self.assertEqual(m.recommendation_gtin('Mixer-123'), '')
        self.assertNotEqual(m.request_uuid('123456789012-1','A'),m.request_uuid('123456789012-2','A'))

    def test_graphql_errors_keep_reason_and_redact_credentials(self):
        for status in [200, 400]:
            response = Response({'errors':[{'message':'Unknown type "ExampleInput".',
                'extensions':{'code':'GRAPHQL_VALIDATION_FAILED'}}]}, status)
            with self.assertRaisesRegex(m.MappingError, 'starting recommendations:.*GRAPHQL_VALIDATION_FAILED.*Unknown type'):
                m.start(lambda *a,**k:response, 'secret-test-token', {'title':'Mixer'})
        response = Response({'errors':[{'message':'Insufficient permissions', 'extensions':{'code':'FORBIDDEN'}}]})
        with self.assertRaisesRegex(m.MappingError, 'FORBIDDEN.*Insufficient permissions'):
            m.poll(lambda *a,**k:response,'secret-test-token','task')
        detail = m.error_details([{'message':'secret-test-token Bearer abc123 refresh_token=hidden client_secret=private'}], 'secret-test-token')
        for secret in ['secret-test-token','abc123','hidden','private']:
            self.assertNotIn(secret,detail)
        self.assertIn('[redacted]',detail)
        response = Response({'data':{'startListingPreviewsCreation':{'errors':[{'errorDescription':'Image URL could not be resolved'}]}}})
        with self.assertRaisesRegex(m.MappingError,'Image URL could not be resolved'):
            m.start(lambda *a,**k:response,'token',{'title':'Mixer'})

    def test_request_and_permissions(self):
        product, skipped = m.product_input({'title':'Mixer','upc':'123456789012','images':['http://example.com/a','https://127.0.0.1/a','https://example.com/a']})
        self.assertEqual(skipped,2)
        self.assertEqual(product['externalProductIdentifierInput']['productType'],'UPC')
        seen=[]
        def post(url, **kwargs):
            seen.append((url,kwargs))
            return Response({'data':{'startListingPreviewsCreation':{'listingPreviewsCreationTask':{'id':'task'}}}})
        self.assertEqual(m.start(post,'fake-token',product),'task')
        self.assertEqual(seen[0][1]['json']['variables']['input']['externalProducts'],[product])
        self.assertEqual(seen[0][1]['headers']['X-EBAY-C-MARKETPLACE-ID'],'EBAY_US')
        for status in [401,403,429,500]:
            with self.assertRaises(m.MappingError): m.start(lambda *a,**k:Response({},status),'x',product)
        with self.assertRaises(m.MappingError): m.start(lambda *a,**k:Response({'errors':[{'message':'no access'}]}),'x',product)
        with self.assertRaises(m.MappingError): m.product_input({'title':'x','marketplaceId':'EBAY_GB'})

    def test_processing_partial_result_confidence_and_html(self):
        result=None
        def post(*a,**k): return Response({'data':{'listingPreviewsCreationTaskById':{'listingPreviewsCreationTask':{'id':'task','result':result}}}})
        self.assertEqual(m.poll(post,'x','task')['status'],'processing')
        result={'completionStatus':'COMPLETED_WITH_ERROR','listingPreviews':[{'mappingReferenceId':'ref','title':'Mixer','description':'<p onclick="bad()">safe<script>bad()</script><img src=x onerror=bad()> &amp; sound</p>',
            'aspects':[{'name':'Color','values':[{'value':'Blue','confidence':'PREFILL'}]}, {'name':'Power','values':[{'value':'325W','confidence':'HINT'}]}]}]}
        p=m.poll(post,'x','task')['previews'][0]
        self.assertEqual(p['aspects'],{'Color':['Blue']})
        self.assertEqual(p['hints'],{'Power':['325W']})
        self.assertEqual(p['description'],'<p>safe &amp; sound</p>')
        result['listingPreviews'][0]['mappingReferenceId']=''
        self.assertEqual(m.poll(post,'x','task')['status'],'no_match')

    def test_xml_reference_escaping_and_validation(self):
        data={'title':'Mixer & bowl','sku':'A<1','price':10,'quantity':1}
        inventory={'condition':'NEW','product':{'imageUrls':['https://example.com/a'],'aspects':{'Color':['Blue']}}}
        offer={'marketplaceId':'EBAY_US','categoryId':'123','listingDescription':'<p>Good</p>','listingPolicies':{'fulfillmentPolicyId':'1','paymentPolicyId':'2','returnPolicyId':'3'}}
        args=(inventory,offer,{'country':'US','postalCode':'10001'},'reference','uuid')
        call,xml=m.listing_xml(data,*args)
        ns={'e':'urn:ebay:apis:eBLBaseComponents'};root=ET.fromstring(xml)
        self.assertEqual(root.findtext('e:Item/e:MappingReferenceId',namespaces=ns),'reference')
        self.assertEqual(root.findtext('e:Item/e:Title',namespaces=ns),'Mixer & bowl')
        self.assertEqual(call,'AddFixedPriceItem')
        for fields in [{'offerId':'existing'},{'quantity':1.5},{'price':0},{'price':'NaN'}]:
            with self.assertRaises(m.MappingError):m.listing_xml({**data,**fields},*args)

    def test_routes_cached_tasks_provenance_and_idempotent_publish(self):
        # Isolate Flask and use only a temporary SQLite database, never application data.
        request=types.SimpleNamespace(get_json=lambda:{'title':'Mixer','upc':'123456789012'},args={})
        fake=types.ModuleType('flask');fake.request=request;fake.jsonify=lambda **k:k
        sys.modules.pop('listing_mapping_routes',None)
        with patch.dict(sys.modules,{'flask':fake}):
            import listing_mapping_routes as routes
        handlers={}
        app=types.SimpleNamespace(route=lambda path,**k:lambda fn:handlers.setdefault(path,fn))
        with tempfile.TemporaryDirectory() as directory:
            @contextlib.contextmanager
            def db(name):
                connection=sqlite3.connect(os.path.join(directory,name))
                try:
                    yield connection;connection.commit()
                except Exception:
                    connection.rollback();raise
                finally: connection.close()
            inventory={'condition':'NEW','product':{'imageUrls':['https://example.com/a']}}
            offer={'merchantLocationKey':'US','marketplaceId':'EBAY_US','categoryId':'123','listingDescription':'Good','listingPolicies':{'fulfillmentPolicyId':'1','paymentPolicyId':'2','returnPolicyId':'3'}}
            api=lambda method,path,**kwargs:Response({'offers':[]} if path.endswith('/offer') else {'location':{'address':{'country':'US','postalCode':'10001'}}})
            publish=routes.register(app,db_connection=db,get_token=lambda:'test',post=lambda *a,**k:None,
                build_draft=lambda *a,**k:{'inventoryItem':inventory,'offer':offer},ebay_request=api,
                mark_listed=lambda *a,**k:None,mark_bol=lambda *a,**k:None,safe_error=lambda *a:'error')
            with patch.object(m,'start',return_value='task') as start:
                handlers['/api/listingagent/ebay/mapping/start']();handlers['/api/listingagent/ebay/mapping/start']()
                self.assertEqual(start.call_count,1)
            request.args={'upc':'wrong','taskId':'task'}
            self.assertEqual(handlers['/api/listingagent/ebay/mapping/task']()[1],400)
            request.args={'upc':'123456789012','taskId':'task'}
            with patch.object(m,'poll',return_value={'status':'ready','previews':[{'mappingReferenceId':'ref'}]}) as poll:
                handlers['/api/listingagent/ebay/mapping/task']();handlers['/api/listingagent/ebay/mapping/task']()
                self.assertEqual(poll.call_count,1)
            data={'upc':'123456789012','sku':'SKU1','title':'Mixer','quantity':1,'price':10,'mappingReferenceId':'ref'}
            with self.assertRaises(m.MappingError):publish({**data,'mappingReferenceId':'forged'})
            self.assertTrue(publish({**data,'dry_run':True})['dry_run'])
            with patch.dict(os.environ,{'EBAY_OLDAUTH_TOKEN':'test'}),patch.object(m,'trading',return_value={'listingId':'123','warnings':[]}) as trading:
                self.assertTrue(publish(data)['success'])
                self.assertTrue(publish(data)['alreadyPublished'])
                self.assertEqual(trading.call_count,2) # Verify and Add, exactly once each.
            with patch.dict(os.environ,{'EBAY_OLDAUTH_TOKEN':'test'}),patch.object(m,'trading',side_effect=[{},TimeoutError('uncertain')]):
                with self.assertRaises(TimeoutError):publish({**data,'sku':'SKU2'})
            with patch.dict(os.environ,{'EBAY_OLDAUTH_TOKEN':'test'}),patch.object(m,'trading',return_value={}) as trading:
                with self.assertRaisesRegex(m.MappingError,'still processing'):publish({**data,'sku':'SKU2'})
                self.assertEqual(trading.call_count,1) # Only Verify; no second Add.


if __name__=='__main__': unittest.main()
