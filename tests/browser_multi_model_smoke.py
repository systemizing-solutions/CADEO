from playwright.sync_api import sync_playwright
from pathlib import Path
from urllib.parse import urlsplit
import yaml, json, hashlib
from cade.services.specification_service import UniqueKeysLoader

root=Path(__file__).resolve().parents[1]
assets=root/'src/cade/web'
spec=root/'examples/multi_model/product.yaml'
schema=json.loads((root/'src/cade/schemas/product.schema.json').read_text())
yamltext=spec.read_text()
document=yaml.load(yamltext,Loader=UniqueKeysLoader)
errors=[]

def serve(route):
    path=urlsplit(route.request.url).path
    def j(payload,status=200): route.fulfill(status=status,body=json.dumps(payload),content_type='application/json')
    if path=='/': route.fulfill(body=(assets/'index.html').read_text(),content_type='text/html');return
    if path.startswith('/assets/'):
        filename=path.removeprefix('/assets/')
        suffix=Path(filename).suffix
        route.fulfill(body=(assets/filename).read_text(),content_type=('text/css' if suffix=='.css' else 'text/javascript'));return
    if path=='/api/bootstrap': j({'token':'token','workspace':'/workspace','version':'0.8.4'});return
    if path=='/api/schemas': j({'schemas':['schemas/product-v0.8.4.schema.json']});return
    if path=='/api/schema': j(schema);return
    if path=='/api/files': j({'files':['product.yaml']});return
    if path=='/api/document': j({'path':'product.yaml','document':document,'yaml':yamltext,'schema':'schemas/product-v0.8.4.schema.json','etag':hashlib.sha256(yamltext.encode()).hexdigest()});return
    if path=='/api/dialects': j({'dialects':['sqlite']});return
    if path=='/api/engines': j({'engines':[]});return
    if path=='/api/engines/installation': j({'job':None,'interpreter':'python','virtual_environment':True});return
    if path=='/api/check-registry': j({'product':'merchant_operations','checks':[{'id':'merchants/canonical_key_unique','type':'generated','model':'merchants'}, {'id':'devices/canonical_key_unique','type':'generated','model':'devices'}], 'unknown_references':[]});return
    if path=='/api/yaml/diagnostics': j({'valid':True,'issues':[]});return
    if path=='/api/yaml/export': j({'document':route.request.post_data_json['document'],'yaml':yamltext});return
    print('Missing route:',path)
    j({'error':'No mock'},404)

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':900})
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    markup=(assets/'index.html').read_text()
    import re
    markup=re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', markup)
    markup=re.sub(r'<link[^>]+href="/assets/[^"]+"[^>]*>', '', markup)
    page.set_content(markup)
    responses={
      '/api/bootstrap':{'token':'token','workspace':'/workspace','version':'0.8.4'},
      '/api/schemas':{'schemas':['schemas/product-v0.8.4.schema.json']},
      '/api/schema':schema,
      '/api/files':{'files':['product.yaml']},
      '/api/document':{'path':'product.yaml','document':document,'yaml':yamltext,
        'schema':'schemas/product-v0.8.4.schema.json','etag':hashlib.sha256(yamltext.encode()).hexdigest()},
      '/api/dialects':{'dialects':['sqlite']},
      '/api/engines':{'engines':[]},
      '/api/engines/installation':{'job':None,'interpreter':'python','virtual_environment':True},
      '/api/check-registry':{'product':'merchant_operations','checks':[
        {'id':'merchants/canonical_key_unique','type':'generated','model':'merchants'},
        {'id':'devices/canonical_key_unique','type':'generated','model':'devices'}],
        'unknown_references':[]},
      '/api/yaml/diagnostics':{'valid':True,'issues':[]},
      '/api/artifact':{'content':'SELECT 1;\n'}
    }
    page.evaluate("""(responses) => { window.fetch = async (url, options) => {
      const name = String(url).split('?')[0];
      const obj = responses[name] ?? {error:'not implemented: '+name};
      return new Response(JSON.stringify(obj), {status:responses[name]?200:404,headers:{'Content-Type':'application/json'}});
    }; }""",responses)
    page.add_script_tag(content=(assets/'code-preview.js').read_text())
    page.add_script_tag(content=(assets/'lineage-graph.js').read_text())
    page.add_script_tag(content=(assets/'editor.js').read_text())
    page.locator('#documentTitle').filter(has_text='merchant_operations').wait_for(timeout=10000)
    print('title:',page.locator('#documentTitle').text_content())
    print('workflow:',page.locator('#workflow').inner_text()[:150])
    page.evaluate("""async () => {
      await showSqlPreview([{path:'generated/preview.sql',product:'merchant_operations.devices',
        kind:'transformation',dialect:'sqlite'}],['generated/preview.sql']);
    }""")
    page.locator('#formTab').click()
    page.get_by_role('button',name='Review SQL preview',exact=True).click()
    assert page.locator('#sqlPanel').is_visible()
    assert page.locator('#formPanel').is_hidden()
    assert page.locator('#sqlTab').get_attribute('aria-selected')=='true'
    assert page.locator('#sqlCode').text_content()=='SELECT 1;\n'
    assert page.evaluate("document.activeElement.id")=='sqlCode'
    assert page.evaluate("document.getElementById('sqlCode').getBoundingClientRect().top < innerHeight")
    assert page.evaluate("document.getElementById('sqlCode').parentElement.scrollLeft")==0
    page.get_by_role('button',name='Review SQL preview',exact=True).click()
    assert page.evaluate("document.activeElement.id")=='sqlCode'
    print('SQL PREVIEW BUTTON PASS')
    page.locator('#formTab').click()
    page.get_by_text('Model',exact=True).first.click()
    page.locator('#activeModel').wait_for(timeout=10000)
    print('model picker:',page.locator('#activeModel option').all_text_contents())
    assert page.locator('#activeModel option').count()==2
    page.locator('#activeModel').select_option('devices')
    page.once('dialog',lambda dialog: dialog.accept('agreements'))
    page.get_by_role('button',name='+ Add model').click()
    choices=page.locator('#activeModel option').all_text_contents()
    print('after add:',choices)
    assert 'agreements' in choices
    model_state=page.evaluate('({model:Object.keys(state.document.model.entities),mapping:Object.keys(state.document.mapping.entities),assurance:Object.keys(state.document.assurance.entities),delivery:Object.keys(state.document.delivery.targets)})')
    print('registries:',model_state)
    assert all('agreements' in ids for ids in model_state.values())
    assert all(set(ids)==set(model_state['model']) for ids in model_state.values())
    assert not errors,errors
    print('BROWSER SMOKE PASS')
    browser.close()
