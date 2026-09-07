"""Isolated UI preview. Serves only the listing template/assets; never loads Flask or databases."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json, re
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = '''<script>
state.activeUpc = '123456789012';
state.item = {upc:state.activeUpc,title:'KitchenAid Artisan 5 Quart Stand Mixer · Blue',quantity:1};
state.queue = [state.item,{upc:'223456789012',title:'Sony WH-1000XM5 Wireless Headphones'},{upc:'323456789012',title:'LEGO Botanical Collection Orchid'},{upc:'423456789012',title:'OXO Good Grips Coffee Grinder'}];
state.ebayPoliciesLoaded = true;
for(const [id,value] of Object.entries({upc:state.activeUpc,sku:'SS-1042',title:state.item.title,price:'149.99',quantity:'1',conditionDescription:'Tested and working. Light wear on the bowl. Includes whisk and dough hook.',amazonUpc:state.activeUpc,amazonSku:'SS-1042',amazonPrice:'149.99'})) $(id).value=value;
for(const [id,value,text] of [['categoryId','20677','Home & Garden > Kitchen > Stand Mixers'],['fulfillmentPolicyId','ship','Standard shipping · 2 business days'],['paymentPolicyId','pay','Immediate payment'],['returnPolicyId','return','30-day returns']]) { $(id).append(new Option(text,value)); $(id).value=value; }
$('listingDescEditor').textContent='KitchenAid Artisan mixer in blue. Tested and working, with light cosmetic wear. Includes the stainless steel bowl, whisk, and dough hook.';
renderQueue(); renderPreview(); _updateNextFieldBtn(); ebayCatalogSearch({automatic:true});
window.addEventListener('error', e=>{const out=document.createElement('pre');out.textContent='UI ERROR: '+e.message;document.body.prepend(out)});
</script>'''
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=self.path.split('?')[0]
        if path=='/':
            source=(ROOT/'templates/listingagent.html').read_text(encoding='utf-8')
            source=source.replace("{{ url_for('static', filename='favicon.ico') }}",'/static/favicon.ico')
            source=source.replace('    loadSettings();','')
            a=source.index('    loadQueue().then(() => {')
            b=source.index('    showRecents();',a)
            source=source[:a]+source[b:]
            fixture = '' if 'empty=1' in self.path else FIXTURE
            source=source.replace('<script src="/static/listing-workspace.js',fixture+'<script src="/static/listing-workspace.js')
            if 'save_error=1' in self.path:
                source=source.replace('<body>', '''<body><script>const realFetch=window.fetch;window.fetch=(url,opts)=>String(url).endsWith('/working_draft')&&opts?.method==='POST'?Promise.resolve(new Response(JSON.stringify({success:false,error:'Preview: simulated connection failure'}),{status:503})):realFetch(url,opts);</script>''')
            source=source.replace('<div class="wrap">', '<div class="wrap"><p class="hint">Preview / Sample data / Publishing disabled</p>')
            self.send(source.encode(),'text/html; charset=utf-8');return
        if path=='/api/listingagent/ebay/catalog_search':
            self.send(json.dumps({'success':True,'results':[
                {'itemId':'demo-blue','title':'KitchenAid KSM150 Artisan 5 Quart Stand Mixer Blue','source':'browse','matchReason':'Similar title','price_min':159.99,'price_max':159.99,'categoryId':'20677','categoryPath':'Kitchen > Stand Mixers','aspects':{'Brand':'KitchenAid','Model':'KSM150','Color':'Blue'},'itemWebUrl':'https://www.ebay.com/itm/demo'},
                {'itemId':'demo-red','title':'KitchenAid Artisan KSM150 Stand Mixer Red','source':'browse','matchReason':'Similar title','price_min':149.99,'aspects':{'Brand':'KitchenAid','Color':'Red'}}
            ]}).encode(),'application/json');return
        if path=='/api/listingagent/ebay/catalog_item':
            self.send(json.dumps({'success':True,'result':{'itemId':'demo-blue','title':'KitchenAid KSM150 Artisan 5 Quart Stand Mixer Blue','price_min':159.99,'categoryId':'20677','aspects':{'Brand':'KitchenAid','Model':'KSM150','Color':'Blue','Capacity':'5 qt','Power':'325 W','Type':'Stand Mixer'}}}).encode(),'application/json');return
        if path=='/api/listingagent/ebay/mapping/task':
            self.send(json.dumps({'success':True,'status':'ready','previews':[{'mappingReferenceId':'demo-native','title':'KitchenAid Artisan KSM150 5 Quart Stand Mixer Blue','categoryId':'20677','categoryName':'Stand Mixers','epid':'','description':'<p>KitchenAid Artisan stand mixer with a 5-quart bowl.</p>','aspects':{'Brand':['KitchenAid'],'Color':['Blue'],'Capacity':['5 qt']},'hints':{'Power':['325 W']}}]}).encode(),'application/json');return
        if path.startswith('/api/'):

            self.send(json.dumps({'success':True,'items':[],'data':{},'policies':{}}).encode(),'application/json');return
        if path.startswith('/static/'):
            target=(ROOT/path.lstrip('/')).resolve()
            if target.is_relative_to(ROOT/'static') and target.is_file():
                kind='text/css' if target.suffix=='.css' else 'application/javascript' if target.suffix=='.js' else 'image/png'
                self.send(target.read_bytes(),kind);return
        self.send_response(404);self.end_headers()
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'));data=json.loads(self.rfile.read(size) or b'{}')
        if self.path=='/api/listingagent/ebay/mapping/start':
            self.send(json.dumps({'success':True,'taskId':'demo-task','skippedImages':0,'publishingConnected':True}).encode(),'application/json');return
        if self.path=='/api/listingagent/working_draft':
            self.send(json.dumps({'success':True,'draft':data.get('draft')}).encode(),'application/json');return
        self.send_response(403);self.end_headers()
    def send(self,data,kind):
        self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(data)
if __name__=='__main__':
    print('Isolated listing preview at http://127.0.0.1:8765',flush=True)
    HTTPServer(('127.0.0.1',8765),Handler).serve_forever()
