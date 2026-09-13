"""Live local-match pagination smoke check. Never invokes Claude or links stock."""
import json
import time
import urllib.error
import urllib.request

base = 'http://127.0.0.1:5000'


def request(data=None):
    req = urllib.request.Request(base+'/api/listing-reconciliation'+('' if data else '?refresh=1'),
                                 data=json.dumps(data).encode() if data else None,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as response:
        return json.load(response)


for attempt in range(15):
    try:
        with urllib.request.urlopen(base+'/listing-reconciliation',timeout=5) as response:
            html=response.read().decode()
        break
    except urllib.error.URLError:
        if attempt == 14:
            raise
        time.sleep(1)
assert 'Show 3 more matches' in html
payload=request()
listing=next(l for l in payload['listings'] if l.get('has_more_suggestions') and l['state'] not in ('accounted','handled'))
shown=list(listing['suggestions'])
start=time.monotonic()
result=request({'action':'more_matches','store':listing['store'],'listing_key':listing['listing_key'],
                'seen_ids':[s['searchrack_id'] for s in shown],'seen_barcodes':[s['barcode'] for s in shown]})
assert result['success'] and 0 < len(result['suggestions']) <= 3
assert not {s['searchrack_id'] for s in shown}&{s['searchrack_id'] for s in result['suggestions']}
print(json.dumps({'verified':True,'listing':listing['title'],'original_matches':len(shown),
                  'new_matches':len(result['suggestions']),'more_available':result['has_more_suggestions'],
                  'seconds':round(time.monotonic()-start,2),'action':'more_matches'}),flush=True)
