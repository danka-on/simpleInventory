"""Live text search smoke check; caches suggestions only, never links inventory."""
import json
import time
import urllib.request

base = 'http://127.0.0.1:5000'


def request(path, data=None):
    req = urllib.request.Request(base+path, data=json.dumps(data).encode() if data else None,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=110) as response:
        return json.load(response)


data = request('/api/listing-reconciliation?refresh=1')
assert data['claude_search_available']
assert 'image_comparison_available' not in data
listing = next((l for l in data['listings'] if l['listing_key']=='L5-B2XL-5N2L'
                and l['state'] not in ('accounted','handled')), None)
if not listing:
    listing = next(l for l in data['listings'] if l['state'] not in ('accounted','handled'))
print(json.dumps({'checking':listing['title'],'catalog':data['claude_catalog']}),flush=True)
body = {'action':'search_warehouse','store':listing['store'],'listing_key':listing['listing_key']}
for attempt in (1,2):
    start = time.monotonic()
    result = request('/api/listing-reconciliation',body)
    assert result['success'],result
    print(json.dumps({'attempt':attempt,'seconds':round(time.monotonic()-start,2),
                      'cached':result['cached'],'search':result['ai_search'],
                      'suggestions':[{'id':s['searchrack_id'],'title':s['title'],'verdict':s.get('ai_verdict'),
                                      'reason':s['reason']} for s in result['suggestions']]}),flush=True)
    if attempt==2:
        assert result['cached'],'Repeated unchanged search must reuse its cached answer'
refreshed = request('/api/listing-reconciliation?refresh=1')
after = next(l for l in refreshed['listings'] if l['store']==listing['store'] and l['listing_key']==listing['listing_key'])
assert after['state']==listing['state']
assert after['ai_search']['checked_at']==result['ai_search']['checked_at']
with urllib.request.urlopen(base+'/listing-reconciliation',timeout=15) as response:
    html = response.read().decode()
assert 'Search whole warehouse with Claude' in html
assert 'compare_photos' not in html and 'imagePreview' in html
print(json.dumps({'verified':True,'state_after':after['state'],'cache_survives_refresh':True}),flush=True)
