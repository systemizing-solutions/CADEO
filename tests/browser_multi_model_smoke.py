from playwright.sync_api import expect, sync_playwright
from pathlib import Path
from urllib.parse import urlsplit
import yaml, json, hashlib, shutil
from cadeo.services.specification_service import UniqueKeysLoader

root=Path(__file__).resolve().parents[1]
assets=root/'src/cadeo/web'
spec=root/'examples/multi_model/product.yaml'
schema=json.loads((root/'src/cadeo/schemas/product.schema.json').read_text())
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
    if path=='/api/bootstrap': j({'token':'token','workspace':'/workspace','version':'0.9.0'});return
    if path=='/api/schemas': j({'schemas':['schemas/product-v0.9.0.schema.json']});return
    if path=='/api/schema': j(schema);return
    if path=='/api/files': j({'files':['product.yaml']});return
    if path=='/api/document': j({'path':'product.yaml','document':document,'yaml':yamltext,'schema':'schemas/product-v0.9.0.schema.json','etag':hashlib.sha256(yamltext.encode()).hexdigest()});return
    if path=='/api/dialects': j({'dialects':['sqlite']});return
    if path=='/api/engines': j({'engines':[]});return
    if path=='/api/engines/installation': j({'job':None,'interpreter':'python','virtual_environment':True});return
    if path=='/api/check-registry': j({'product':'merchant_operations','checks':[{'id':'merchants/canonical_key_unique','type':'generated','model':'merchants'}, {'id':'devices/canonical_key_unique','type':'generated','model':'devices'}], 'unknown_references':[]});return
    if path=='/api/yaml/diagnostics': j({'valid':True,'issues':[]});return
    if path=='/api/yaml/export': j({'document':route.request.post_data_json['document'],'yaml':yamltext});return
    print('Missing route:',path)
    j({'error':'No mock'},404)

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':900})
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    markup=(assets/'index.html').read_text()
    import re
    markup=re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', markup)
    markup=re.sub(r'<link[^>]+href="/assets/[^"]+"[^>]*>', '', markup)
    page.set_content(markup)
    page.add_style_tag(content=(assets/'tom-select.css').read_text())
    page.add_style_tag(content=(assets/'editor.css').read_text())
    responses={
      '/api/bootstrap':{'token':'token','workspace':'/workspace','version':'0.9.0'},
      '/api/schemas':{'schemas':['schemas/product-v0.9.0.schema.json']},
      '/api/schema':schema,
      '/api/files':{'files':['product.yaml']},
      '/api/document':{'path':'product.yaml','document':document,'yaml':yamltext,
        'schema':'schemas/product-v0.9.0.schema.json','etag':hashlib.sha256(yamltext.encode()).hexdigest()},
      '/api/dialects':{'dialects':['sqlite']},
      '/api/engines':{'engines':[
        {'id':'sqlite','name':'SQLite','driver_loaded':True,'build_ready':True},
        {'id':'postgresql','name':'PostgreSQL','driver_loaded':False,'install_extra':'postgresql',
         'install_command':'pip install cadeo[postgresql]'},
        {'id':'mariadb','name':'MariaDB','driver_loaded':False,'install_extra':'mariadb'},
        {'id':'actian','name':'Actian Data Platform / Vector / Actian X / Ingres','driver_loaded':False,
         'install_extra':'actian'}]},
      '/api/engines/installation':{'job':None,'interpreter':'python','virtual_environment':True},
      '/api/check-registry':{'product':'merchant_operations','checks':[
        {'id':'merchants/canonical_key_unique','type':'generated','model':'merchants',
         'sql':'SELECT COUNT(*) AS failures FROM (SELECT merchant_id FROM target GROUP BY merchant_id HAVING COUNT(*) > 1) AS duplicate_keys;'},
        {'id':'devices/canonical_key_unique','type':'generated','model':'devices',
         'sql':'SELECT COUNT(*) AS failures FROM (SELECT device_id FROM target GROUP BY device_id HAVING COUNT(*) > 1) AS duplicate_keys;'}],
        'unknown_references':[]},
      '/api/yaml/diagnostics':{'valid':True,'issues':[]},
      '/api/artifact':{'content':'SELECT 1;\n'}
    }
    page.evaluate("""(responses) => { window.checkRegistryCalls=0; window.savedDrafts={}; window.fetch = async (url, options) => {
      const name = String(url).split('?')[0];
      if (name === '/api/document' && options?.method === 'PUT') {
        const draft=JSON.parse(options.body);
        const result={path:draft.path,document:draft.document,yaml:JSON.stringify(draft.document),
          etag:'saved-draft',schema:draft.schema};
        window.savedDrafts[draft.path]=result;
        return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
      }
      if (name === '/api/document') {
        const path=new URL(String(url),'http://localhost').searchParams.get('path');
        if (window.savedDrafts[path]) return new Response(JSON.stringify(window.savedDrafts[path]),
          {status:200,headers:{'Content-Type':'application/json'}});
      }
      if (name === '/api/files') return new Response(JSON.stringify({files:['product.yaml',...Object.keys(window.savedDrafts)]}),
        {status:200,headers:{'Content-Type':'application/json'}});
      if (name === '/api/check-registry') {
        window.checkRegistryCalls++;
        if (window.rejectCheckRegistry) return new Response(JSON.stringify({
          error:'Complete or correct the specification before selecting checks',
          issues:[{path:'sources.datasets',message:"unused source definitions ['raw_devices']"}]
        }), {status:422,headers:{'Content-Type':'application/json'}});
      }
      const obj = responses[name] ?? {error:'not implemented: '+name};
      return new Response(JSON.stringify(obj), {status:responses[name]?200:404,headers:{'Content-Type':'application/json'}});
    }; }""",responses)
    page.add_script_tag(content=(assets/'tom-select.js').read_text())
    page.add_script_tag(content=(assets/'code-preview.js').read_text())
    page.add_script_tag(content=(assets/'lineage-graph.js').read_text())
    page.add_script_tag(content=(assets/'editor.js').read_text())
    page.locator('#documentTitle').filter(has_text='merchant_operations').wait_for(timeout=10000)
    page.locator('#openWorkspaceTop').click()
    page.locator('.engine-picker .ts-control').click()
    driver_search=page.locator('.engine-picker-dropdown .dropdown-input')
    driver_search.fill('postg')
    page.locator('.engine-picker-dropdown .option[data-value="postgresql"]').wait_for()
    expect(page.locator('.engine-picker-dropdown .option[data-selectable]')).to_have_count(1)
    driver_search.press('Enter')
    assert page.locator('#engineChoice').input_value()=='postgresql'
    assert page.locator('#installEngine').is_enabled()
    assert 'pip install cadeo[postgresql]' in page.locator('#engineInfo').inner_text()
    page.locator('.engine-picker .ts-control').click()
    driver_search.fill('no-such-database')
    page.locator('.engine-picker-dropdown .no-results').wait_for()
    driver_search.press('Escape')
    assert page.locator('#engineChoice').input_value()=='postgresql'
    page.evaluate('refreshEngineCatalog()')
    assert page.locator('#engineChoice').input_value()=='postgresql'
    assert page.locator('.engine-picker').count()==1
    page.locator('.engine-picker .ts-control').click()
    driver_search.fill('sqlite')
    page.locator('.engine-picker-dropdown .option[data-value="sqlite"]').click()
    assert page.locator('#engineChoice').input_value()=='sqlite'
    assert page.locator('#installEngine').is_disabled()
    page.locator('#closeWorkspace').click()
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
    page.evaluate("navigateStage('mapping')")
    page.wait_for_function("state.checkRegistryStatus === 'ready'")
    assert 'merchants/canonical_key_unique' in page.locator('#generatedChecks').inner_text()
    assert 'No duplicate groups for merchant_id' in page.locator('#generatedChecks').inner_text()
    criteria_path=page.evaluate("acceptanceCriteria()[0].path")
    page.evaluate("path => writePath(path, [])", criteria_path)
    page.evaluate('renderGeneratedChecks()')
    check_row=page.locator('#generatedChecks .generated-check').first
    check_row.get_by_role('combobox', name='Acceptance criterion for merchants/canonical_key_unique', exact=True).select_option('0')
    check_row.get_by_role('button', name='Link criterion', exact=True).click()
    assert page.evaluate('path => readPath(path)', criteria_path)==['merchants/canonical_key_unique']
    page.locator('#generatedChecks .generated-check').first.get_by_role('button', name='Unlink', exact=True).click()
    assert page.evaluate('path => readPath(path)', criteria_path)==[]
    page.get_by_role('button', name='Edit rule settings', exact=True).click()
    assert page.evaluate('state.activeStage')=='model'
    assert page.locator('[data-path="model.entities.merchants.unique_key_attributes"]').is_visible()
    page.evaluate("navigateStage('mapping')")
    page.locator('#generatedChecks summary').click()
    assert 'HAVING COUNT(*) > 1' in page.locator('#generatedChecks pre').inner_text()
    page.locator('#activeModel').select_option('devices')
    assert 'devices/canonical_key_unique' in page.locator('#generatedChecks').inner_text()
    assert 'merchants/canonical_key_unique' not in page.locator('#generatedChecks').inner_text()
    page.get_by_role('button', name='Manage custom checks', exact=True).click()
    assert page.evaluate('state.activeStage')=='assurance'
    assert '--check <check-id-from-manifest>' in page.locator('#generatedChecks').inner_text()
    assert page.get_by_role('textbox', name='New check ID', exact=True).evaluate('(node) => node === document.activeElement')
    assert 'devices/canonical_key_unique' in page.locator('#generatedChecks').inner_text()
    assert page.get_by_role('button', name='Add custom check', exact=True).is_visible()
    page.get_by_role('textbox', name='New check ID', exact=True).fill('device_status_allowed')
    page.get_by_role('button', name='Add custom check', exact=True).click()
    assert page.evaluate('state.document.assurance.entities.devices.tests.device_status_allowed.type')=='accepted_values'
    page.evaluate("path => writePath(path, ['devices/device_status_allowed'])", criteria_path)
    check=page.locator('[data-path="assurance.entities.devices.tests.device_status_allowed"]')
    check.get_by_role('textbox', name='Check ID device_status_allowed', exact=True).fill('status_allowed')
    check.get_by_role('textbox', name='Check ID device_status_allowed', exact=True).press('Tab')
    assert page.evaluate("Object.hasOwn(state.document.assurance.entities.devices.tests,'status_allowed')")
    assert page.evaluate('path => readPath(path)', criteria_path)==['devices/status_allowed']
    check=page.locator('[data-path="assurance.entities.devices.tests.status_allowed"]')
    page.once('dialog', lambda dialog: dialog.accept())
    check.get_by_role('button', name='Remove', exact=True).first.click()
    assert not page.evaluate("Object.hasOwn(state.document.assurance.entities.devices.tests,'status_allowed')")
    assert page.evaluate('path => readPath(path)', criteria_path)==[]
    page.evaluate("navigateStage('model')")
    page.locator('#activeModel').select_option('merchants')
    assert 'Minimum path:' in page.locator('.first-run-path').inner_text()
    glossary=page.locator('.term-glossary')
    assert glossary.count()==1 and glossary.locator('summary').inner_text()=='Business and data terms'
    assert glossary.locator('dt').all_text_contents()==['Canonical model','Record grain','Reconciliation']
    assert glossary.evaluate('(node) => !node.open')
    optional_settings=page.locator('details.optional-settings').first
    assert page.locator('details.optional-settings').count()>0
    assert page.locator('details.optional-settings[open]').count()==0
    optional_settings.locator(':scope > summary').click()
    assert optional_settings.evaluate('(node) => node.open')
    optional_settings.locator(':scope > summary').click()
    assert optional_settings.evaluate('(node) => !node.open')
    assert page.locator('.field-label .required').count()>0
    page.locator('#activeModel').select_option('devices')
    assert page.locator('.destination-settings select').input_value()=='warehouse'
    destination_table=page.locator('[data-path="delivery/targets/devices/table"]')
    assert destination_table.input_value()=='canonical_devices'
    destination_table.fill('published_devices')
    assert page.evaluate('state.document.delivery.targets.devices.table')=='published_devices'
    page.get_by_text('Connect',exact=True).first.click()
    assert page.locator('[data-path="sources.connections"]').is_visible()
    assert page.locator('.destination-settings').is_visible()
    assert 'DPC_EXECUTION_DATABASE_URL' in page.locator('.destination-settings').inner_text()
    page.evaluate("writePath(['sources','connections','destination'], {url_env:'DESTINATION_DATABASE_URL'}); drawForm();")
    page.locator('[data-path="delivery/execution/connection"]').select_option('destination')
    assert page.evaluate('state.document.delivery.execution.connection')=='destination'
    assert 'DESTINATION_DATABASE_URL' in page.locator('.destination-settings').inner_text()
    page.get_by_text('Deliver',exact=True).first.click()
    assert page.get_by_role('button',name='Compile SQL (offline)',exact=True).is_visible()
    assert page.get_by_role('button',name='Build, run checks & publish',exact=True).is_visible()
    assert 'does not execute SQL' in page.locator('#actionHelp').inner_text()
    assert '--check <check-id-from-manifest>' in page.locator('#actionHelp').inner_text()
    assert page.evaluate("evidenceBadge('not_run').textContent")=='Not Run'
    failed_build_message=page.evaluate("""() => actionOutcomeMessage('build', {
      manifest:{error:'quality gate reconciliation failed (1 failures); publication blocked'},
      manifest_path:'build/run-123/run-manifest.json'}, false)""")
    assert 'reconciliation failed (1 failures)' in failed_build_message
    assert 'build/run-123/run-manifest.json' in failed_build_message
    assert page.locator('[data-path="delivery/execution/connection"]').is_visible()
    assert page.locator('[data-path="delivery/execution/connection"]').input_value()=='destination'
    assert page.locator('[data-path="delivery/targets/devices/table"]').input_value()=='published_devices'
    original_artifact_paths=page.evaluate('JSON.stringify(state.document.delivery.artifact_paths ?? null)')
    assert page.locator('[data-path="delivery/artifact_paths/sql"]').input_value()=='sql'
    assert page.locator('[data-path="delivery/artifact_paths/project_plans"]').input_value()=='.'
    assert page.locator('[data-path="delivery/artifact_paths/transformations"]').is_visible()
    assert page.evaluate('JSON.stringify(state.document.delivery.artifact_paths ?? null)')==original_artifact_paths
    page.locator('[data-path="delivery/artifact_paths/sql"]').fill('generated_sql')
    assert page.evaluate('state.document.delivery.artifact_paths.sql')=='generated_sql'
    page.get_by_text('Map',exact=True).first.click()
    original_fields=page.evaluate('JSON.stringify(state.document.mapping.entities.devices.fields)')
    page.evaluate('window.rejectCheckRegistry=true')
    page.locator('[data-path="mapping/entities/devices/source"]').select_option('raw_merchants')
    assert page.evaluate('state.document.mapping.entities.devices.source')=='raw_merchants'
    assert page.evaluate('JSON.stringify(state.document.mapping.entities.devices.fields)')==original_fields
    assert 'Source changed to raw_merchants' in page.locator('#status').inner_text()
    page.wait_for_function("state.checkRegistryStatus==='unavailable' && !!state.checkRegistryError")
    assert 'Generated checks unavailable' in page.locator('#checkRegistryStatus').inner_text()
    assert page.locator('#checkRegistryStatus .status.error').count()==0
    page.locator('#checkRegistryStatus summary').click()
    assert 'You can still save this draft and edit any stage.' in page.locator('#checkRegistryStatus').inner_text()
    assert "unused source definitions ['raw_devices']" in page.locator('#checkRegistryStatus').inner_text()
    assert 'Source changed to raw_merchants' in page.locator('#status').inner_text()
    page.evaluate('window.rejectCheckRegistry=false')
    page.locator('[data-path="mapping/entities/devices/source"]').select_option('raw_devices')
    page.wait_for_function("state.checkRegistryStatus==='ready'")
    assert page.locator('#checkRegistryStatus').inner_text()==''
    print('SOURCE AND DESTINATION CONTROLS PASS')
    page.get_by_text('Model',exact=True).first.click()
    page.once('dialog',lambda dialog: dialog.accept('agreements'))
    page.get_by_role('button',name='+ Add model').click()
    choices=page.locator('#activeModel option').all_text_contents()
    print('after add:',choices)
    assert 'agreements' in choices
    model_state=page.evaluate('({model:Object.keys(state.document.model.entities),mapping:Object.keys(state.document.mapping.entities),assurance:Object.keys(state.document.assurance.entities),delivery:Object.keys(state.document.delivery.targets)})')
    print('registries:',model_state)
    assert all('agreements' in ids for ids in model_state.values())
    assert all(set(ids)==set(model_state['model']) for ids in model_state.values())
    page.evaluate("""async () => { state.dirty=false; window.checkRegistryCalls=0; await newFile(); }""")
    assert page.locator('#quickStartSaveRow').is_visible()
    assert page.locator('#quickStartPath').get_attribute('placeholder')=='new_product.yaml'
    page.evaluate("navigateStage('sources')")
    assert page.locator('#quickStartSaveRow').is_visible()
    page.evaluate("navigateStage('definition')")
    page.wait_for_timeout(50)
    assert page.evaluate('window.checkRegistryCalls')==0
    page.locator('[data-path="definition/name"]').fill('draft_steps')
    page.locator('[data-path="definition/outcome"]').fill('Save progress before sources are known')
    page.locator('#saveFile').click()
    page.wait_for_function("state.path === 'draft_steps.yaml' && !state.dirty && document.getElementById('status').textContent === 'Saved draft_steps.yaml.'")
    assert 'Saved draft_steps.yaml.' in page.locator('#status').inner_text()
    assert page.evaluate("Object.keys(window.savedDrafts['draft_steps.yaml'].document.sources.datasets).length")==0
    page.evaluate("async () => { await navigateStage('mapping'); await saveFile(); }")
    page.evaluate("navigateStage('definition')")
    page.locator('[data-path="definition/owner/team"]').fill('Draft owners')
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#saveFile').click()
    page.wait_for_function("window.savedDrafts['draft_steps.yaml'].document.definition.owner.team === 'Draft owners'")
    page.evaluate("loadFile('draft_steps.yaml')")
    page.wait_for_function("!state.dirty && state.document.definition.owner.team === 'Draft owners'")
    assert page.locator('[data-path="definition/outcome"]').input_value()=='Save progress before sources are known'
    assert not errors,errors
    print('BROWSER SMOKE PASS')
    browser.close()
