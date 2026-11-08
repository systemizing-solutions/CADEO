/* Generic JSON Schema Draft 2020-12 form renderer and thin local API client.
   Domain names, choices, array definitions, and section fields come from /api/schema.
   The server owns YAML parsing, validation, code generation, and database execution. */
'use strict';

const state = { schema: null, schemaId: 'schemas/product-v0.8.5.schema.json', token: '', document: {}, path: '', etag: null,
                dirty: false, revision: 0, rawChanged: false, tab: 'form', files: [], yamlDraft: null, yamlBaseline: null, activeStage: 'definition', validation: null, stageScroll: {}, openGroups: {}, graphMode: 'erd', graphScope: 'file', graphField: '', graphBranch: 'all', yamlScroll: 0, sqlArtifacts: [], sqlGenerated: [], sqlContent: {}, sqlStale: false, yamlIssues: [], yamlCursor: 0, evidence: null, evidenceStale: false, checkRegistry: [], checkRegistryStatus: 'unavailable', checkRegistryError: '' };
const el = id => document.getElementById(id);
const create = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const titleCase = text => String(text).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const isObject = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const STAGES = ['definition','model','sources','mapping','assurance','delivery'];
const STAGE_GUIDES = [
  { title: 'Define the need', text: 'Name the product and owner. Add at least one consumer, requirement, feature, and acceptance criterion that describes how you will know it worked.' },
  { title: 'Describe the output', text: 'Choose what one row represents, a unique key, and the fields this product should publish. Record grain means “what does one row represent?”' },
  { title: 'Describe the input', text: 'Add the source table and its columns. You can complete this contract without connecting to a live database.' },
  { title: 'Map input to output', text: 'For each output field, choose a source column or expression. Every canonical attribute needs a mapping.' },
  { title: 'Decide what proves quality', text: 'Review the generated checks, link them to acceptance criteria, and record which source-row exceptions are acceptable.' },
  { title: 'Choose where to publish', text: 'Set the output table and SQL dialect. Add the required refresh schedule, timezone, recovery policy, and runbook. A database connection is optional for validation and SQL preview.' }
];
const stageLabel = stage => resolved(state.schema?.properties?.[stage])?.title || titleCase(stage);
const pathId = path => path.map(x => String(x).replace(/~/g,'~0').replace(/\//g,'~1')).join('/');
const sourceIds = () => Object.keys(state.document?.sources?.datasets || {});
const modelIds = () => Object.keys(state.document?.model?.entities || {});
const activeModelId = () => modelIds().includes(state.activeModel) ? state.activeModel : modelIds()[0] || null;
const modelForPath = path => {
  const index = path.indexOf('entities');
  if (index >= 0 && ['model','mapping','assurance'].includes(path[0]) && modelIds().includes(path[index+1]))
    return path[index+1];
  const target = path.indexOf('targets');
  if (target >= 0 && path[0] === 'delivery' && modelIds().includes(path[target+1]))
    return path[target+1];
  return activeModelId();
};
const attributeIds = (path = []) => Object.keys(state.document?.model?.entities?.[modelForPath(path)]?.attributes || {});
const allAttributePaths = () => modelIds().flatMap(id =>
  Object.keys(state.document?.model?.entities?.[id]?.attributes || {}).map(name => [id,name]));
const requirementIds = () => (state.document?.definition?.requirements || []).map(x => x.id).filter(Boolean);
const featureIds = () => (state.document?.definition?.features || []).map(x => x.id).filter(Boolean);
// The server's SqlTemplateService.check_statements() is the only source of check IDs.
const checkIds = () => state.checkRegistryStatus === 'ready' ? state.checkRegistry.map(item => item.id) : [];
const refsAt = (path, schema) => {
  const last = path.at(-1), parent = path.at(-2);
  const kind = schema?.['x-reference'] || (last === 'source' || last === 'connection' || last === 'model_ref' ? last :
    last === 'column' && path.includes('assurance') ? 'attribute' :
    parent === 'requirement_ids' ? 'requirement' : parent === 'check_ids' ? 'check' :
    parent === 'unique_key_attributes' && path.includes('model') ? 'attribute' : null);
  if (kind === 'source') return sourceIds();
  if (kind === 'attribute') return attributeIds(path);
  if (kind === 'model' || last === 'model_ref') return modelIds();
  if (kind === 'source_column') {
    const index = path.indexOf('datasets');
    const dataset = index >= 0 ? path[index + 1] : null;
    return Object.keys(state.document?.sources?.datasets?.[dataset]?.columns || {});
  }
  if (kind === 'requirement') return requirementIds();
  if (kind === 'feature') return featureIds();
  if (kind === 'check') return checkIds();
  if (kind === 'connection') return Object.keys(state.document?.sources?.connections || {});
  return null;
};
function setValidationStale() {
  if (state.validation) {
    state.validation.stale = true;
    el('status').textContent = 'Specification changed — validation results are stale. Validate again.';
    el('status').className = 'status muted';
  }
}


async function request(url, method = 'GET', data) {
  const options = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
  if (method !== 'GET') {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-Editor-Token'] = state.token;
    options.body = JSON.stringify(data || {});
  }
  const response = await fetch(url, options);
  let payload;
  try { payload = await response.json(); }
  catch { throw new Error(`Unexpected response (${response.status})`); }
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.issues = Array.isArray(payload.issues) ? payload.issues : [];
    throw error;
  }
  return payload;
}
// The currently selected *unsaved* document determines available checks.
// A failed or stale response must never leave an old list marked authoritative.
let checkRegistryTimer = null;
let checkRegistrySequence = 0;
function fillCheckSelector(select, value) {
  const current = String(value ?? '');
  const available = checkIds();
  const ready = state.checkRegistryStatus === 'ready';
  select.replaceChildren();
  const placeholder = create('option', '', ready ? 'Choose a generated check…' :
    state.checkRegistryStatus === 'loading' ? 'Loading generated checks…' :
    'Correct the specification to load checks');
  placeholder.value = '';
  select.append(placeholder);
  // Preserve an existing invalid ID in the form: show it as invalid, do not
  // turn it into a valid option or delete it from the YAML.
  if (current && !available.includes(current)) {
    const unknown = create('option', '', `${current} (${ready ? 'unknown check ID' : 'not verified'})`);
    unknown.value = current;
    unknown.disabled = ready;
    select.append(unknown);
  }
  for (const id of available) {
    const option = create('option', '', id); option.value = id; select.append(option);
  }
  select.value = current;
  select.disabled = !ready;
  select.title = ready ? 'Generated by the compiler for this specification' :
    (state.checkRegistryError || 'The check registry is not available for this draft');
}
function refreshCheckSelectors() {
  document.querySelectorAll('select[data-reference-kind="check"]').forEach(select => {
    let path;
    try { path = JSON.parse(select.dataset.referencePath); } catch { return; }
    fillCheckSelector(select, readPath(path));
  });
}
function scheduleCheckRegistry(delay = 300) {
  const sequence = ++checkRegistrySequence;
  clearTimeout(checkRegistryTimer);
  state.checkRegistry = [];
  state.checkRegistryStatus = state.rawChanged ? 'unavailable' : 'loading';
  state.checkRegistryError = state.rawChanged ? 'Apply YAML edits to refresh generated checks' : '';
  refreshCheckSelectors();
  if (state.rawChanged || !state.schema || !isObject(state.document)) return;
  const documentSnapshot = clone(state.document);
  const schemaSnapshot = state.schemaId;
  const pathSnapshot = el('filePath').value.trim() || state.path || 'product.yaml';
  checkRegistryTimer = setTimeout(async () => {
    try {
      const result = await request('/api/check-registry', 'POST', {
        document: documentSnapshot, schema: schemaSnapshot, path: pathSnapshot });
      if (sequence !== checkRegistrySequence || state.rawChanged) return;
      state.checkRegistry = result.checks;
      state.checkRegistryStatus = 'ready';
      state.checkRegistryError = '';
    } catch (error) {
      if (sequence !== checkRegistrySequence || state.rawChanged) return;
      state.checkRegistry = [];
      state.checkRegistryStatus = 'unavailable';
      const detail = error.issues?.map(issue => `${issue.path}: ${issue.message}`).join('; ');
      state.checkRegistryError = detail ? `${error.message}: ${detail}` : error.message;
      status(`Check selection unavailable for ${pathSnapshot}: ${state.checkRegistryError}`, 'error');
    }
    refreshCheckSelectors();
  }, delay);
}

function status(message, kind = 'muted', result = null) {
  el('status').textContent = message;
  el('status').className = `status ${kind}`;
  if (result !== null) el('results').textContent = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
}
function busy(button, yes) { button.disabled = yes; button.dataset.oldLabel ||= button.textContent; button.textContent = yes ? 'Working…' : button.dataset.oldLabel; }
function markDirty(affectsChecks = true) {
  state.revision += 1;
  state.dirty = true;
  el('dirtyState').className = 'save-state dirty';
  el('dirtyState').textContent = 'Unsaved changes';
  el('dirtyState').removeAttribute('title');
  updateDocumentTitle();
  setValidationStale();
  if (state.sqlArtifacts.length) { state.sqlStale=true; updateSqlState(); }
  if (state.evidence) { state.evidenceStale=true; updateEvidenceState(); }
  renderHealth();
  if (affectsChecks) scheduleCheckRegistry();
}
function markSaved() { state.dirty = false; el('dirtyState').className = 'save-state'; el('dirtyState').textContent = 'Saved'; el('dirtyState').removeAttribute('title'); }
function updateDocumentTitle() {
  const product = state.document && state.document.definition;
  el('documentTitle').textContent = product && product.name ? product.name : 'New specification';
  el('documentSubtitle').textContent = state.path || 'Choose a YAML path to save this product';
}
function readPath(path) {
  let value = state.document;
  for (const step of path) value = value?.[step];
  return value;
}
function writePath(path, value, refresh = false) {
  if (!path.length) state.document = value;
  else {
    let ref = state.document;
    for (let i = 0; i < path.length - 1; i++) {
      const key = path[i];
      if (ref[key] === undefined || ref[key] === null) ref[key] = typeof path[i + 1] === 'number' ? [] : {};
      ref = ref[key];
    }
    ref[path.at(-1)] = value;
  }
  markDirty(!(path[0] === 'definition' && path.includes('check_ids')));
  if (refresh) drawForm();
}
function removePath(path) {
  if (!window.confirm(`Remove ${path.join('.')}? This will delete its value from the unsaved specification.`)) return;
  const parent = readPath(path.slice(0, -1));
  if (Array.isArray(parent)) parent.splice(path.at(-1), 1);
  else delete parent[path.at(-1)];
  markDirty(); drawForm();
}
function pointer(ref) {
  if (!ref.startsWith('#/')) throw new Error(`Unsupported schema reference: ${ref}`);
  return ref.slice(2).split('/').reduce((value, segment) => value[segment.replace(/~1/g, '/').replace(/~0/g, '~')], state.schema);
}
function resolved(original) {
  if (original === true) return {};
  if (original === false) return { type: 'null' };
  if (!original || typeof original !== 'object') return {};
  if (original.$ref) {
    const { $ref, ...extras } = original;
    return { ...resolved(pointer($ref)), ...extras };
  }
  if (original.allOf) {
    const merged = original.allOf.reduce((acc, section) => ({ ...acc, ...resolved(section),
      properties: { ...(acc.properties || {}), ...(resolved(section).properties || {}) },
      required: [...new Set([...(acc.required || []), ...(resolved(section).required || [])])] }), {});
    return { ...merged, ...Object.fromEntries(Object.entries(original).filter(([key]) => key !== 'allOf')) };
  }
  return original;
}
function variantChoices(schema) {
  const s = resolved(schema);
  if (s.oneOf || s.anyOf) return (s.oneOf || s.anyOf).map(resolved);
  if (s.if && s.then && s.else) return [resolved(s.then), resolved(s.else)];
  return null;
}
function variantIndex(schema, value, variants) {
  const s = resolved(schema);
  if (s.if && s.then && s.else) return isObject(value) &&
    (s.if.required || []).every(k => Object.hasOwn(value, k)) ? 0 : 1;
  if (value === undefined || value === null) return 0;
  const score = part => {
    const props = part.properties || {};
    let count = 0;
    for (const [name, prop] of Object.entries(props)) {
      const p = resolved(prop);
      if (p.const !== undefined && value?.[name] === p.const) count += 100;
      else if (p.const !== undefined && value?.[name] !== undefined) count -= 100;
      else if (Object.hasOwn(value || {}, name)) count += 2;
    }
    for (const name of part.required || []) if (value?.[name] === undefined) count -= 3;
    return count;
  };
  return variants.reduce((best, part, index) => score(part) > score(variants[best]) ? index : best, 0);
}
function variantLabel(part, index) {
  const props = part.properties || {};
  const fixed = Object.entries(props).find(([, v]) => resolved(v).const !== undefined);
  if (fixed) return String(resolved(fixed[1]).const);
  if (part.title) return part.title;
  const required = part.required || [];
  if (required.length === 1) return titleCase(required[0]);
  if (part.description) return part.description.slice(0, 46);
  return `Option ${index + 1}`;
}
function typeOfSchema(s) {
  if (s.type) return Array.isArray(s.type) ? s.type[0] : s.type;
  if (s.properties || s.additionalProperties !== undefined) return 'object';
  if (s.items) return 'array';
  return 'string';
}
function defaultFor(schema, depth = 0) {
  if (depth > 18) return null;
  const s = resolved(schema);
  if (s.const !== undefined) return clone(s.const);
  if (s.default !== undefined) return clone(s.default);
  const choices = variantChoices(s);
  if (choices) return defaultFor(choices[0], depth + 1);
  if (s.enum) return s.enum[0];
  const t = typeOfSchema(s);
  if (t === 'object') {
    const value = {};
    for (const name of s.required || []) if (s.properties?.[name]) value[name] = defaultFor(s.properties[name], depth + 1);
    return value;
  }
  if (t === 'array') return [];
  if (t === 'boolean') return false;
  if (t === 'integer' || t === 'number') return s.minimum ?? 0;
  if (t === 'null') return null;
  return '';
}
function button(text, onClick, css = 'text-button') { const b = create('button', css, text); b.type = 'button'; b.addEventListener('click', onClick); return b; }
function textInput(value, onChange, placeholder = '', type = 'text') {
  const n = create('input'); n.type = type; n.value = value ?? ''; n.placeholder = placeholder;
  n.addEventListener('input', () => onChange(type === 'number' ? (n.value ? Number(n.value) : null) : n.value));
  return n;
}
function labeledField(schema, value, path, name, required, depth) {
  const field = create('div', 'field');
  field.dataset.path = path.join('.');
  const problems = (state.validation?.stale ? [] : state.validation?.issues || [])
    .filter(issue => issue.path === path.join('.') || issue.path.startsWith(path.join('.')+'.'));
  if (problems.length) field.classList.add('invalid');
  const head = create('div', 'field-heading');
  const label = create('label', 'field-label', resolved(schema).title || titleCase(name));
  if (required) label.append(create('span', 'required', ' *'));
  head.append(label);
  if (!required && value !== undefined) head.append(button('Remove', () => removePath(path), 'text-button danger'));
  field.append(head);
  const s = resolved(schema);
  if (s.description) field.append(create('p', 'description', s.description));
  // Examples and guidance come from the same JSON Schema as YAML completion.
  // Show focused modelling examples rather than adding a hardcoded form model.
  if (path[0] === 'model' && path.length === 2 && Array.isArray(s.examples) && s.examples.length) {
    const sample = s.examples[0];
    const example = typeof sample === 'string' ? sample : JSON.stringify(sample);
    field.append(create('p', 'field-example', `Example: ${example}`));
  }
  field.append(renderValue(s, value, path, depth));
  if (problems.length && path.length > 1) field.append(create('p','field-error',problems[0].message));
  return field;
}
function renderValue(schema, value, path, depth = 0) {
  const s = resolved(schema);
  if (depth > 25) return create('p', 'description', 'Nested structure is too deep to display.');
  if (s.readOnly) {
    const preview = create(isObject(value) || Array.isArray(value) ? 'textarea' : 'input');
    if (preview.tagName === 'INPUT') preview.type = 'text';
    preview.value = isObject(value) || Array.isArray(value)
      ? JSON.stringify(value, null, 2) : String(value ?? '');
    preview.readOnly = true;
    preview.setAttribute('aria-label', 'Read-only schema-defined value');
    return preview;
  }
  const alternatives = variantChoices(s);
  if (alternatives) {
    const container = create('div', 'nested');
    const row = create('div', 'variant-row'); row.append(create('label', '', 'Type'));
    const select = create('select');
    const selected = variantIndex(s, value, alternatives);
    alternatives.forEach((part, index) => {
      const option = create('option', '', titleCase(variantLabel(part, index)));
      option.value = index; option.selected = selected === index; select.append(option);
    });
    select.addEventListener('change', () => {
      if (value !== undefined && !window.confirm('Change expression type? Existing expression values will be replaced.')) { select.value = String(selected); return; }
      writePath(path, defaultFor(alternatives[Number(select.value)]), true);
    });
    row.append(select); container.append(row);
    container.append(renderValue(alternatives[selected], value, path, depth + 1));
    return container;
  }
  const references = refsAt(path, s);
  // Check selectors must never fall back to a free-text field or locally
  // generated suggestions when the backend registry is loading or invalid.
  const checkReference = path.at(-2) === 'check_ids' || s['x-reference'] === 'check';
  if ((checkReference || references?.length) && typeOfSchema(s) === 'string' && s.const === undefined && !s.enum) {
    const select = create('select'); select.dataset.path = pathId(path);
    if (checkReference) {
      select.dataset.referenceKind = 'check';
      select.dataset.referencePath = JSON.stringify(path);
      fillCheckSelector(select, value);
    } else {
      const current = String(value ?? '');
      const empty = create('option', '', 'Choose a defined reference…'); empty.value = ''; select.append(empty);
      if (current && !references.includes(current)) {
        const missing = create('option', '', current + ' (unknown reference)'); missing.value = current; select.append(missing);
      }
      references.forEach(item => { const option=create('option','',item); option.value=item; select.append(option); });
      select.value = current;
    }
    select.addEventListener('change', () => writePath(path, select.value));
    return select;
  }
  if (s.const !== undefined) {
    const wrapper = create('div');
    const readOnly = create('input'); readOnly.type = 'text';
    readOnly.value = value === undefined ? '' : String(value);
    readOnly.placeholder = String(s.const);
    readOnly.readOnly = true; readOnly.setAttribute('aria-label', 'Fixed schema value');
    wrapper.append(readOnly);
    if (value !== s.const) wrapper.append(button(`Use required value: ${s.const}`,
      () => writePath(path, clone(s.const), true)));
    return wrapper;
  }
  if (s.enum) {
    const select = create('select');
    if (value === undefined) { const opt = create('option', '', 'Select…'); opt.value = ''; select.append(opt); }
    s.enum.forEach((item, index) => { const opt = create('option', '', String(item)); opt.value = String(index); opt.selected = item === value; select.append(opt); });
    select.addEventListener('change', () => writePath(path, s.enum[Number(select.value)]));
    return select;
  }
  if (s.type && Array.isArray(s.type)) {
    const wrapper = create('div'); const row = create('div', 'variant-row');
    const selector = create('select'); const current = value === null ? 'null' :
      typeof value === 'number' ? (Number.isInteger(value) && s.type.includes('integer') ? 'integer' : 'number') :
      typeof value === 'boolean' ? 'boolean' : 'string';
    const chosen = s.type.includes(current) ? current : s.type[0];
    s.type.forEach(type => { const option = create('option', '', type); option.value = type; option.selected = type === chosen; selector.append(option); });
    selector.addEventListener('change', () => writePath(path, defaultFor({ type: selector.value }), true));
    row.append(create('label', '', 'Value type'), selector); wrapper.append(row);
    wrapper.append(renderValue({ type: chosen }, value, path, depth + 1)); return wrapper;
  }
  if (!Object.keys(s).length) {
    const kind = Array.isArray(value) ? 'array' : value === null ? 'null' :
      isObject(value) ? 'object' : value === undefined ? 'string' : typeof value;
    const choices = ['string', 'number', 'boolean', 'null', 'object', 'array'];
    const wrapper = create('div'); const row = create('div', 'variant-row');
    row.append(create('label', '', 'Value type'));
    const select = create('select');
    choices.forEach(item => { const opt = create('option', '', titleCase(item)); opt.value = item; opt.selected = kind === item; select.append(opt); });
    select.addEventListener('change', () => {
      const type = select.value;
      const replacement = type === 'object' ? {} : type === 'array' ? [] : defaultFor({ type });
      writePath(path, replacement, true);
    });
    row.append(select); wrapper.append(row);
    const child = kind === 'object' ? { type: 'object', additionalProperties: true } :
                  kind === 'array' ? { type: 'array', items: true } : { type: kind };
    wrapper.append(renderValue(child, value, path, depth + 1));
    return wrapper;
  }
  const type = typeOfSchema(s);
  if (type === 'object') {
    const container = create('div', 'nested');
    const obj = isObject(value) ? value : {};
    const properties = s.properties || {};
    for (const [key, property] of Object.entries(properties)) {
      const required = (s.required || []).includes(key);
      if (!required && obj[key] === undefined) {
        const add = create('div', 'add-row');
        add.append(button(`+ ${titleCase(key)}`, () => writePath([...path, key], defaultFor(property), true)));
        const desc = resolved(property).description;
        if (desc) add.append(create('span', 'description', desc.slice(0, 80)));
        container.append(add);
      } else container.append(labeledField(property, obj[key], [...path, key], key, required, depth + 1));
    }
    if (s.additionalProperties === false) {
      for (const key of Object.keys(obj).filter(k => !Object.hasOwn(properties, k))) {
        const warning = create('div', 'group-card');
        warning.append(create('span', 'description', `Unknown field: ${key} (not allowed by the schema)`));
        warning.append(button('Remove', () => removePath([...path, key]), 'text-button danger'));
        container.append(warning);
      }
    }
    if (s.additionalProperties && s.additionalProperties !== false) {
      const extra = s.additionalProperties === true ? {} : s.additionalProperties;
      for (const key of Object.keys(obj).filter(k => !Object.hasOwn(properties, k))) {
        const card = collapsible([...path,key],key); const heading = create('div', 'entry-actions');
        const newInput = create('input'); newInput.type = 'text'; newInput.value = key;
        newInput.addEventListener('change', () => {
          const proposed = newInput.value.trim();
          if (proposed && proposed !== key && !Object.hasOwn(obj, proposed)) {
            obj[proposed] = obj[key]; delete obj[key]; markDirty(); drawForm();
          }
        });
        heading.append(newInput, button('Remove', () => removePath([...path, key]), 'text-button danger'));
        card.append(heading);
        card.append(renderValue(extra, obj[key], [...path, key], depth + 1)); container.append(card);
      }
      const add = create('div', 'add-row');
      const isMapping = path.length === 4 && path[0] === 'mapping' && path[1] === 'entities' && path[3] === 'fields';
      const input = isMapping ? create('select') : create('input');
      if (isMapping) {
        const empty = create('option', '', 'Choose unmapped canonical attribute'); empty.value = ''; input.append(empty);
        attributeIds(path).filter(name => !Object.hasOwn(obj,name)).forEach(name => {
          const opt = create('option', '', name); opt.value = name; input.append(opt);
        });
      } else input.placeholder = 'New key name';
      input.setAttribute('aria-label', 'New entry key');
      add.append(input, button('+ Add entry', () => {
        const key = input.value.trim();
        if (!key || Object.hasOwn(obj, key) || Object.hasOwn(properties, key)) { status('Enter a unique key name.', 'error'); return; }
        writePath([...path, key], defaultFor(extra), true);
      })); container.append(add);
    }
    return container;
  }
  if (type === 'array') {
    const container = create('div', 'nested');
    const values = Array.isArray(value) ? value : [];
    values.forEach((item, index) => {
      const card = collapsible([...path,index], `Item ${index + 1}`); const head = create('div', 'entry-actions');
      const actions = create('div', 'item-actions');
      if (index > 0) actions.append(button('↑', () => { [values[index - 1], values[index]] = [values[index], values[index - 1]]; markDirty(); drawForm(); }));
      if (index < values.length - 1) actions.append(button('↓', () => { [values[index + 1], values[index]] = [values[index], values[index + 1]]; markDirty(); drawForm(); }));
      actions.append(button('Remove', () => removePath([...path, index]), 'text-button danger'));
      head.append(actions); card.append(head, renderValue(s.items || {}, item, [...path, index], depth + 1)); container.append(card);
    });
    container.append(button('+ Add item', () => writePath(path, [...values, defaultFor(s.items || {})], true)));
    return container;
  }
  if (type === 'boolean') {
    const wrapper = create('label'); const checkbox = create('input'); checkbox.type = 'checkbox'; checkbox.checked = value === true;
    checkbox.addEventListener('change', () => writePath(path, checkbox.checked));
    wrapper.append(checkbox, create('span', 'description', checkbox.checked ? ' True' : ' False')); return wrapper;
  }
  if (type === 'null') return create('span', 'description', 'null');
  if (type === 'integer' || type === 'number') {
    const input = textInput(value, v => writePath(path, v), '', 'number'); input.dataset.path = pathId(path);
    if (type === 'integer') input.step = '1';
    if (s.minimum !== undefined) input.min = s.minimum;
    if (s.maximum !== undefined) input.max = s.maximum;
    return input;
  }
  const input = textInput(value, v => writePath(path, v), s.examples?.[0] || ''); input.dataset.path = pathId(path);
  if (s.pattern) input.pattern = s.pattern;
  if (s.minLength !== undefined) input.minLength = s.minLength;
  return input;
}
function rememberStagePosition() {
  if (state.tab === 'form') state.stageScroll[state.activeStage] = el('formPanel').scrollTop;
}
function collapsible(path, title) {
  const id = pathId(path);
  const details = create('details','group-card');
  details.open = state.openGroups[id] !== false;
  const summary = create('summary','group-heading', title);
  details.append(summary);
  details.addEventListener('toggle',()=>{
    if(details.isConnected) state.openGroups[id]=details.open;
  });
  return details;
}
function stageSchema() { return resolved(state.schema)?.properties?.[state.activeStage] || {}; }
function addModel() {
  const id = (window.prompt('New model ID (letters, numbers and underscores)', '') || '').trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(id) || modelIds().includes(id)) {
    if (id) status('Enter a unique model ID containing letters, numbers or underscores.', 'error');
    return;
  }
  const model = state.document.model ||= { entities: {} };
  const mapping = state.document.mapping ||= { entities: {} };
  const assurance = state.document.assurance ||= { quality: { on_failure: 'block' }, entities: {} };
  const delivery = state.document.delivery ||= { targets: {} };
  model.entities ||= {}; mapping.entities ||= {}; assurance.entities ||= {}; delivery.targets ||= {};
  model.entities[id] = {
    entity_name: titleCase(id.replace(/s$/, '')), record_grain: '',
    unique_key_attributes: [], attributes: {}
  };
  mapping.entities[id] = { source: '', fields: {} };
  assurance.entities[id] = { reconciliation: { allow: [] }, preservation: { mode: 'external', reference: '' } };
  delivery.targets[id] = { table: '' };
  state.activeModel = id;
  markDirty(); drawForm();
}
function removeModel() {
  const id = activeModelId();
  if (!id || modelIds().length <= 1) { status('A data product needs at least one model.', 'error'); return; }
  if (!window.confirm(`Delete model ${id} and its mappings, assurance and publication settings? This cannot be undone.`)) return;
  delete state.document.model.entities[id];
  delete state.document.mapping?.entities?.[id];
  delete state.document.assurance?.entities?.[id];
  delete state.document.delivery?.targets?.[id];
  state.activeModel = modelIds()[0] || null;
  markDirty(); drawForm();
}
function renderModelSelector() {
  const host = el('modelSelector'); host.replaceChildren();
  if (!['model','mapping','assurance','delivery'].includes(state.activeStage)) return;
  const row = create('div','model-selector');
  row.append(create('label','field-label','Canonical model'));
  const select = create('select'); select.id = 'activeModel'; select.setAttribute('aria-label','Select canonical model');
  modelIds().forEach(id => { const option = create('option','',id); option.value=id;
    option.selected = id === activeModelId(); select.append(option); });
  select.addEventListener('change', () => { state.activeModel=select.value; drawForm(); });
  row.append(select, button('+ Add model', addModel, 'btn secondary small'));
  if (modelIds().length > 1) row.append(button('Remove model', removeModel, 'btn secondary small'));
  row.append(create('span','description', `${modelIds().length} published model(s) in this data product`));
  host.append(row);
}
function drawForm() {
  const root = el('formRoot');
  const pane = el('formPanel');
  const active = document.activeElement;
  const focused = active?.dataset?.path;
  let selection = null;
  try { selection = active?.selectionStart; } catch { /* non-text input */ }
  const oldScroll = pane.scrollTop;
  root.replaceChildren();
  const property = stageSchema();
  renderModelSelector();
  const modelId = activeModelId();
  const stage = state.activeStage;
  if (modelId && ['model','mapping','assurance'].includes(stage)) {
    if (stage === 'assurance') {
      root.append(labeledField(property.properties.quality,
        state.document.assurance?.quality, ['assurance','quality'], 'quality', true, 0));
    }
    const itemSchema = resolved(property.properties.entities).additionalProperties;
    root.append(labeledField(itemSchema,
      state.document?.[stage]?.entities?.[modelId], [stage,'entities',modelId], modelId, true, 0));
  } else if (modelId && stage === 'delivery') {
    const targetSchema = resolved(property.properties.targets).additionalProperties;
    root.append(labeledField(targetSchema, state.document.delivery?.targets?.[modelId],
      ['delivery','targets',modelId], `${modelId} publication target`, true, 0));
    const global = Object.fromEntries(Object.entries(property.properties)
      .filter(([key]) => key !== 'targets'));
    root.append(renderValue({ type:'object', properties:global,
      required:(property.required || []).filter(key=>key!=='targets'), additionalProperties:false },
      Object.fromEntries(Object.entries(state.document.delivery || {}).filter(([key])=>key !== 'targets')), ['delivery'], 0));
  } else root.append(renderValue(property, state.document[stage], [stage], 0));
  el('mappingOverview').replaceChildren();
  if (state.activeStage === 'mapping') drawMappingOverview();
  pane.scrollTop = oldScroll;
  if (focused) {
    const field = [...pane.querySelectorAll('[data-path]')].find(n=>n.dataset.path===focused);
    if (field && typeof field.focus === 'function') {
      field.focus({preventScroll:true});
      if (selection != null && typeof field.setSelectionRange === 'function')
        try { field.setSelectionRange(selection,selection); } catch { /* not supported on inputs like select */ }
    }
  }
  renderHealth();
}
function drawMappingOverview() {
  const host=el('mappingOverview');
  const all=allAttributePaths();
  if (!all.length) return;
  host.append(create('h3','',`Source-to-target coverage · ${modelIds().length} models`));
  const table=create('table','mapping-table');
  const heading=create('tr'); ['Model','Canonical attribute','STTM expression','Requirements','Coverage']
    .forEach(v=>heading.append(create('th','',v)));
  table.append(heading);
  for (const [modelId,attr] of all) {
    const tr=create('tr');
    tr.append(create('td','',modelId),create('td','',attr));
    const rule=state.document?.mapping?.entities?.[modelId]?.fields?.[attr];
    tr.append(create('td','',rule===undefined?'No mapping defined':JSON.stringify(rule)));
    const required=state.document?.model?.entities?.[modelId]?.attributes?.[attr]?.requirement_ids || [];
    tr.append(create('td','',required.join(', ') || '—'));
    tr.append(create('td',rule===undefined?'unmapped':'mapped',rule===undefined?'Missing mapping':'Mapped'));
    table.append(tr);
  }
  host.append(table);
}
function stageCompleteness(stage) {
  const value=state.document?.[stage];const schema=resolved(state.schema?.properties?.[stage]);
  if (!isObject(value))return {label:'Missing stage',state:'error'};
  const missing=(schema.required || []).filter(k => value[k] === undefined || value[k] === '' || value[k] === null);
  if (missing.length)return {label:'Missing '+missing.join(', '),state:'error'};
  const errors=state.validation?.stale? [] : (state.validation?.issues || []).filter(x=>x.path===stage || x.path.startsWith(stage+'.'));
  if (errors.length)return {label:errors.length+' validation finding(s)',state:'error'};
  if (stage==='mapping') {
    const absent=allAttributePaths().filter(([id,name])=>
      !Object.hasOwn(value.entities?.[id]?.fields || {}, name));
    if (absent.length)return {label:absent.length+' unmapped field(s)',state:'error'};
  }
  return {label:state.validation?.valid && !state.validation?.stale && state.validation.scope==='file' ? 'Validated' : 'Needs validation',state:state.validation?.valid && !state.validation?.stale && state.validation.scope==='file' ? 'complete':'pending'};
}
function renderHealth() {
  const i=STAGES.indexOf(state.activeStage);
  if (!el('workflow')) return;
  const workflow=el('workflow'); workflow.replaceChildren();
  STAGES.forEach((stage,index)=>{
    const result=stageCompleteness(stage);
    const item=button('',()=>navigateStage(stage),`stage-link ${stage===state.activeStage?'active':''} ${result.state === 'error'?'error':result.state==='complete'?'complete':''}`);
    const dot=create('span','stage-dot',result.state==='complete'?'✓':result.state==='error'?'!':'');
    item.append(dot,create('span','',stageLabel(stage)),create('small','stage-number',String(index+1)));
    item.title=`${stageLabel(stage)}: ${result.label}`;workflow.append(item);
  });
  el('stageTitle').textContent=stageLabel(state.activeStage);
  el('stageDescription').textContent=resolved(state.schema?.properties?.[state.activeStage])?.description || '';
  el('stageProgress').textContent=`Step ${i+1} of 6`;
  el('contextGuide').textContent=STAGE_GUIDES[i]?.text || 'Complete this stage and validate.';
  renderQuickStart();
  const h=el('healthSummary');h.replaceChildren();
  const current=stageCompleteness(state.activeStage);
  const checks=[['Stage structure',current.state==='error'?'failed':'passed',current.label],
    ['Schema version',state.document?.schema?.version?'passed':'failed',state.document?.schema?.version || 'Not selected'],
    ['Mappings',allAttributePaths().every(([id,key])=>Object.hasOwn(state.document?.mapping?.entities?.[id]?.fields || {},key))?'passed':'failed',
      `${allAttributePaths().filter(([id,key])=>!Object.hasOwn(state.document?.mapping?.entities?.[id]?.fields || {},key)).length} unmapped`]];
  checks.forEach(([name,kind,detail])=>{
    const row=create('div',`health-row ${kind}`); row.append(create('span','health-icon',kind==='passed'?'✓':'!'),create('span','',`${name}: ${detail}`));h.append(row);
  });
  renderContextActions();
}
function renderQuickStart() {
  const index=STAGES.indexOf(state.activeStage);
  const guide=STAGE_GUIDES[index];
  const finalStage=index===STAGES.length-1;
  const validated=state.validation?.valid===true && !state.validation.stale && state.validation.scope==='file';
  const hasPreview=state.sqlArtifacts.length>0 && !state.sqlStale;
  el('quickStartTitle').textContent=`Step ${index+1} of ${STAGES.length}: ${guide.title}`;
  el('quickStartText').textContent=guide.text;
  if(finalStage && !el('filePath').value.trim()) {
    const slug=String(state.document?.definition?.name || 'new_product').toLowerCase().replace(/[^a-z0-9_-]+/g,'_');
    el('filePath').value=`${slug || 'new_product'}.yaml`;
  }
  const saveRow=el('quickStartSaveRow');
  const needsSave=finalStage && validated && !hasPreview && (!state.path || state.dirty);
  saveRow.classList.toggle('hidden',!needsSave);
  if(document.activeElement!==el('quickStartPath'))el('quickStartPath').value=el('filePath').value;
  const action=el('quickStartAction');
  if(hasPreview) {
    action.dataset.action='view-sql';
    action.textContent='Review SQL preview';
  } else if(!finalStage) {
    action.dataset.action='advance';
    action.textContent=`Continue to ${stageLabel(STAGES[index+1])}`;
  } else if(!validated) {
    action.dataset.action='validate';
    action.textContent='Validate product';
    if(state.validation && !state.validation.stale && !state.validation.valid)
      el('quickStartText').textContent=`Validation found ${state.validation.issues?.length || 0} issue(s). Use the findings in the right panel to jump to fields, then validate again.`;
  } else if(state.path && !state.dirty) {
    action.dataset.action='preview';
    action.textContent='Preview SQL';
  } else {
    action.dataset.action='save-preview';
    action.textContent='Save & preview SQL';
  }
}
async function handleQuickStartAction() {
  const action=el('quickStartAction').dataset.action;
  try {
    if(action==='advance')return navigateStage(STAGES[STAGES.indexOf(state.activeStage)+1]);
    if(action==='validate') {
      el('actionScope').value='file';
      await runAction('validate');
      return;
    }
    if(action==='save-preview') {
      el('actionScope').value='file';
      el('filePath').value=el('quickStartPath').value.trim();
      await saveFile();
      await runAction('compile');
      renderQuickStart();
      return;
    }
    if(action==='preview') {
      el('actionScope').value='file';
      await runAction('compile');
      renderQuickStart();
      return;
    }
    if(action==='view-sql')await showSqlTab();
  } catch(error) {
    status(error.message,'error');
  }
}
function renderContextActions() {
  const actions=[
    [['validate','Validate']],
    [['erd','View canonical ERD'],['validate','Validate'],['trace','Lineage']],
    [['discover','Discover sources'],['validate','Validate']],
    [['compile','Preview SQL'],['trace','Lineage']],
    [['evidence','Review evidence'],['checks','Review checks'],['validate','Validate']],
    [['evidence','Review evidence'],['compile','Compile'],['plan','Review build'],['build','Build & publish']],
  ][STAGES.indexOf(state.activeStage)] || [];
  const container=el('contextActions');container.replaceChildren();
  actions.forEach(([id,label])=>container.append(button(label,()=> id==='evidence' ? openEvidence() : id==='trace' ? openVisualGraph('lineage') : id==='erd' ? openVisualGraph('erd') : id==='plan' ? openVisualGraph('dependencies') : runAction(id),id==='build'?'btn primary':'btn secondary')));
}
async function navigateStage(stage) {
  if (!STAGES.includes(stage))return;
  rememberStagePosition();
  try {
    if (state.rawChanged) await applyYaml();
    if (state.tab==='yaml')await showForm();
    state.activeStage=stage;
    drawForm();
    el('formPanel').scrollTop=0;
    el('stageTitle').scrollIntoView({block:'start',behavior:'smooth'});
  }catch(error){status('YAML edits are preserved: '+error.message,'error');}
}
function showIssues(findings) {
  const host=el('issues');host.replaceChildren();
  for (const issue of findings || []) {
    const div=create('div','issue'); const path=issue.path || '$';
    div.append(create('strong','',path),create('span','',issue.message));
    if (issue.suggestion) div.append(create('small','', ' — '+issue.suggestion));
    div.tabIndex=0;
    const jump=()=>{
      if (state.rawChanged && issue.line) {
        showYaml().then(()=>jumpToYamlLine(issue.line,issue.column||1));return;
      }
      const stage=path.split('.')[0];
      if (STAGES.includes(stage)) {
        navigateStage(stage).then(()=>{
          const matched=[...el('formPanel').querySelectorAll('[data-path]')].find(node=>node.dataset.path===path);
          if (matched)matched.scrollIntoView({block:'center',behavior:'smooth'});
        });
      } else showYaml().then(()=>issue.line?jumpToYamlLine(issue.line,issue.column||1):jumpToYaml(path));
    };
    div.addEventListener('click',jump);div.addEventListener('keydown',e=>{if(e.key==='Enter')jump();});
    host.append(div);
  }
}
function jumpToYaml(path) {
  const text=el('yamlText');const key=path.split('.').at(-1);
  const index=text.value.search(new RegExp('(^|\\n)\\s*'+key.replace(/[^a-zA-Z0-9_]/g, '')+'\\s*:','m'));
  if(index>=0){text.focus();text.setSelectionRange(index,index);}
}
async function refreshSchemas() {
  const choices = (await request('/api/schemas')).schemas;
  const select = el('schemaSelect'); select.replaceChildren();
  for (const choice of choices) {
    const option = create('option', '', choice === 'schemas/product-v0.8.5.schema.json' ? 'Bundled product schema (v0.8.5)' : choice);
    option.value = choice;
    select.append(option);
  }
  if (!choices.includes(state.schemaId)) throw new Error(`Selected schema no longer exists: ${state.schemaId}`);
  select.value = state.schemaId;
}
function schemaReference(path = el('filePath').value.trim() || state.path || 'product.yaml') {
  // Calculate a relative schema path from the selected workspace paths.
  // The server independently verifies that the resolved file is in-workspace.
  const from = path.split('/').slice(0, -1).filter(Boolean);
  const to = state.schemaId.split('/').filter(Boolean);
  while (from.length && to.length && from[0] === to[0]) { from.shift(); to.shift(); }
  return { path: [...from.map(() => '..'), ...to].join('/'),
           version: state.schema?.['x-schema-version'] };
}
function syncSchemaReference() {
  if (isObject(state.document) && state.schema)
    state.document.schema = schemaReference();
}
async function activateSchema(choice, modified = true) {
  const selected = await request('/api/schema?path=' + encodeURIComponent(choice));
  state.schemaId = choice;
  state.schema = selected;
  if (modified && selected.properties?.spec_version?.const && isObject(state.document))
    state.document.spec_version = selected.properties.spec_version.const;
  el('schemaSelect').value = choice;
  el('schemaVersion').textContent = selected.title || selected['x-schema-version'] || 'Custom schema';
  syncSchemaReference();
  drawForm();
  if (modified) markDirty();
  else scheduleCheckRegistry(0);
}
async function refreshFiles() {
  state.files = (await request('/api/files')).files;
  const list = el('files'); list.replaceChildren();
  for (const path of state.files) {
    const item = button(path, () => loadFile(path), 'file-item' + (path === state.path ? ' active' : ''));
    list.append(item);
  }
  if (!state.files.length) list.append(create('div', 'sidebar-note', 'No specifications yet. Create one or import YAML.'));
}
async function showForm() {
  if (state.rawChanged) await applyYaml();
  state.tab = 'form'; el('formPanel').classList.remove('hidden'); el('yamlPanel').classList.add('hidden');
  el('diagramPanel').classList.add('hidden'); el('sqlPanel').classList.add('hidden'); el('evidencePanel').classList.add('hidden');
  el('formTab').classList.add('selected'); el('yamlTab').classList.remove('selected'); el('sqlTab').classList.remove('selected');
  el('diagramPanel').classList.add('hidden');
  el('diagramTab').classList.remove('selected'); el('evidenceTab').classList.remove('selected');
  drawForm();
  el('formPanel').scrollTop=state.stageScroll[state.activeStage] || 0;
}
async function showYaml() {
  if (state.tab === 'yaml') return;
  rememberStagePosition();
  // Retain the user's original YAML text (including comments and whitespace)
  // unless form edits have changed the underlying specification values.
  const unchanged = state.yamlDraft !== null && state.yamlBaseline === JSON.stringify(state.document);
  let content;
  if (unchanged) content=state.yamlDraft;
  else {
    if (state.yamlDraft !== null && !window.confirm('Form changes require regenerating YAML, which may reformat comments and whitespace. Continue?')) return;
    const out = await request('/api/yaml/export', 'POST', { document: state.document,
      path: el('filePath').value.trim() || 'product.yaml', schema: state.schemaId });
    state.document = out.document;
    content=out.yaml;
  }
  el('yamlText').value=content;
  const cursor=Math.min(state.yamlCursor||0,content.length);
  el('yamlText').setSelectionRange(cursor,cursor);el('yamlText').scrollTop=state.yamlScroll||0;
  state.yamlDraft=content;state.yamlBaseline=JSON.stringify(state.document);
  state.rawChanged = state.rawChanged && state.yamlDraft === content; state.tab = 'yaml';
  el('formPanel').classList.add('hidden'); el('diagramPanel').classList.add('hidden'); el('sqlPanel').classList.add('hidden'); el('evidencePanel').classList.add('hidden'); el('evidencePanel').classList.add('hidden');
  el('yamlPanel').classList.remove('hidden');
  el('formTab').classList.remove('selected'); el('diagramTab').classList.remove('selected'); el('evidenceTab').classList.remove('selected'); el('yamlTab').classList.add('selected'); el('sqlTab').classList.remove('selected');
  updateHighlight(); scheduleYamlDiagnostics();
}
async function applyYaml() {
  if (!state.rawChanged) return;
  const result = await request('/api/yaml/import', 'POST', { yaml: el('yamlText').value,
    path: el('filePath').value.trim() || 'product.yaml' });
  if (result.schema && result.schema !== state.schemaId) await activateSchema(result.schema, false);
  state.document = result.document; syncSchemaReference(); state.rawChanged = false;
  scheduleCheckRegistry(0);
  state.yamlDraft=el('yamlText').value; state.yamlBaseline=JSON.stringify(state.document);
  markDirty(); updateDocumentTitle();
  drawForm(); status('YAML applied to the form. Save to persist changes.', 'success');
}
async function loadFile(path) {
  if (state.dirty && !window.confirm('Discard unsaved changes and open another specification?')) return;
  try {
    const result = await request('/api/document?path=' + encodeURIComponent(path));
    resetSqlPreview(); state.evidence=null; state.evidenceStale=false; el('evidenceManifestPath').value='';
    state.yamlCursor=0;state.yamlScroll=0;
    state.document = result.document; state.path = result.path; state.etag = result.etag;
    state.yamlDraft=result.yaml; state.yamlBaseline=JSON.stringify(state.document);
    el('filePath').value = state.path;
    await activateSchema(result.schema, false);
    markSaved(); state.rawChanged = false; scheduleCheckRegistry(0);
    updateDocumentTitle(); await refreshFiles(); await showForm(); setWorkspaceSidebar(false);
    status('Loaded ' + state.path + '. Active schema: ' + state.schemaId + '.', 'muted', '');
  } catch (error) { status(error.message, 'error'); }
}
function blankDocument() {
  return {
    spec_version: state.schema?.['x-schema-version'] || '0.8.5',
    definition: {
      name: 'new_product', description: '', owner: { team: '', contact: '' },
      consumers: [], outcome: '', requirements: [], features: [],
    },
    model: { entities: { entity: {
      entity_name: 'Entity', record_grain: '', unique_key_attributes: [], attributes: {},
    } } },
    sources: { connections: {}, datasets: {} },
    mapping: { entities: { entity: { source: '', fields: {} } } },
    assurance: {
      quality: { on_failure: 'block' },
      entities: { entity: {
        reconciliation: { allow: [] },
        preservation: { mode: 'external', reference: '' },
        tests: {},
      } },
    },
    delivery: {
      output: './generated', dialect: 'sqlite',
      refresh: { schedule: '', timezone: '' },
      recovery: { strategy: 'rerun', max_attempts: 3, runbook: '' },
      targets: { entity: { table: '' } },
    },
  };
}
async function newFile() {
  if (state.dirty && !window.confirm('Discard unsaved changes?')) return;
  try {
    resetSqlPreview(); state.evidence=null; state.evidenceStale=false; el('evidenceManifestPath').value='';
    state.yamlCursor=0;state.yamlScroll=0;
    state.document = blankDocument(); syncSchemaReference();
    state.path = ''; state.etag = null; state.rawChanged = false; state.yamlDraft=null;state.yamlBaseline=null;
    state.activeStage = 'definition'; state.activeModel = 'entity';
    scheduleCheckRegistry(0);
    el('filePath').value = ''; syncSchemaReference(); markDirty(); updateDocumentTitle(); await showForm();
    setWorkspaceSidebar(false);
    status('New specification created. Start with Define and continue through each stage.', 'muted', '');
  } catch (error) { status(error.message, 'error'); }
}
async function saveFile() {
  if (state.rawChanged) await applyYaml();
  const path = el('filePath').value.trim();
  if (!path) throw new Error('Enter a workspace-relative YAML path before saving.');
  // If the path changes, the existing ETag must not authorize overwriting another file.
  let etag = path === state.path ? state.etag : null;
  if (path !== state.path && state.files.includes(path)) {
    const existing = await request('/api/document?path=' + encodeURIComponent(path));
    if (!window.confirm(`Replace the existing ${path} with this draft?`))
      throw new Error('Save cancelled; edits remain in the editor.');
    etag = existing.etag;
  }
  syncSchemaReference();
  const unchanged=state.yamlDraft!==null && state.yamlBaseline===JSON.stringify(state.document) && path===state.path;
  if (!unchanged && state.yamlDraft!==null && !window.confirm('Saving form changes will regenerate the YAML text and may reformat comments. Continue?'))
    throw new Error('Save cancelled; edits remain in the editor.');
  const result = await request('/api/document', 'PUT', { path, document: state.document, etag, schema: state.schemaId,
    raw_yaml:unchanged?state.yamlDraft:null });
  state.path = result.path; state.document = result.document; state.etag = result.etag;
  state.yamlDraft=result.yaml;state.yamlBaseline=JSON.stringify(state.document);
  markSaved(); updateDocumentTitle(); drawForm();
  await refreshFiles(); status('Saved ' + state.path + '.', 'success');
  return result;
}
async function exportFile() {
  if (state.rawChanged) await applyYaml();
  const unchanged=state.yamlDraft!==null && state.yamlBaseline===JSON.stringify(state.document);
  if (!unchanged && state.yamlDraft!==null && !window.confirm('Export will regenerate YAML and may reformat comments. Continue?'))return;
  const result = await request('/api/yaml/export', 'POST', { document: state.document,
    path: el('filePath').value.trim() || 'product.yaml', schema: state.schemaId,
    raw_yaml:unchanged?state.yamlDraft:null });
  state.document = result.document;state.yamlDraft=result.yaml;state.yamlBaseline=JSON.stringify(state.document);
  const blob = new Blob([result.yaml], { type: 'application/yaml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url;
  link.download = state.path.split('/').at(-1) || 'data-product.yaml';
  document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
  status('YAML exported from the current editor document.', 'success');
}
async function validateDraft() {
  const scope=el('actionScope').value;
  if (!state.rawChanged) syncSchemaReference();
  if (scope==='project' && state.dirty && !window.confirm('Project validation uses saved YAML files, not this unsaved draft. Continue without saving?'))return;
  const payload=scope==='project' ? {scope:'project'} :
    {scope:'file',document:state.document,schema:state.schemaId,path:el('filePath').value.trim() || state.path || 'product.yaml'};
  const result=state.rawChanged && scope==='file' ?
    await request('/api/yaml/diagnostics','POST',{yaml:el('yamlText').value,path:payload.path,schema:state.schemaId}) :
    await request('/api/validate','POST',payload);
  const issues=scope==='project'?(result.results||[]).flatMap(item=>(item.issues||[]).map(issue=>({...issue,path:item.path+' · '+issue.path}))) : result.issues;
  state.validation={valid:result.valid,issues,stale:false,scope};
  if (state.rawChanged && scope==='file') { state.yamlIssues=issues; renderYamlDiagnostics(); updateHighlight(); }
  showIssues(issues);
  renderHealth();
  status(`${scope==='project'?'Project':'Specification'} validation ${result.valid?'passed':'failed'}. ${issues.length} finding(s).`,result.valid?'success':'error',result);
  return result;
}
async function runAction(explicitAction) {
  const action=explicitAction || el('actionSelect').value;
  const control=el('runAction');
  const currentRun=Symbol(action);state.currentRun=currentRun;
  state.validation=null;showIssues([]);el('results').textContent='';
  if(action==='compile' && state.sqlArtifacts.length){state.sqlStale=true;updateSqlState();}
  status(`${titleCase(action)} running…`,'running','');
  if (control && !control.classList.contains('hidden'))busy(control,true);
  try {
    if (action==='validate') { await validateDraft(); return; }
    if (state.rawChanged) await applyYaml();
    if (state.dirty) {
      if (!window.confirm(`${titleCase(action)} works from saved YAML. Save your current changes before continuing?`)) return;
      await saveFile();
    }
    if (!state.path) throw new Error('Select or save a YAML specification.');
    const path=el('actionScope').value === 'project' ? '.' : state.path;
    const requestBody={
      action,path,schema:state.schemaId,dialect:el('dialectOverride').value.trim()||null,
      out:el('outputOverride').value.trim()||null,
      targets:el('targetInput').value.split(',').map(s=>s.trim()).filter(Boolean),
      profile:el('profileFlag').checked,database:el('databasePath').value.trim()||null
    };
    if (action === 'compile') requestBody.test_pack = el('testPackExport').value;
    if (action === 'build') {
      const plan=await request('/api/action','POST',{...requestBody,action:'plan'});
      const steps=plan.plan?.steps||[];
      status(`Build review: ${steps.length} planned execution step(s). Publication occurs after quality checks.`,'muted',plan);
      const connectionId=state.document.delivery?.execution?.connection;
      const connection=state.document.sources?.connections?.[connectionId];
      const connectionSummary=connection ? `${connectionId} (URL from ${connection.url_env})` : 'No execution connection is configured in this specification';
      const targets=Object.entries(state.document.delivery?.targets || {})
        .map(([modelId,target])=>`${modelId}: ${target.table}`).join('\n') || 'No publication targets are configured';
      const projectNote=el('actionScope').value === 'project' ? '\nProject builds may also use connections and targets from upstream products.' : '';
      if (!window.confirm(`Review build: ${steps.length} execution step(s) for ${path}.\n\nConnection: ${connectionSummary}\nTargets configured in this specification:\n${targets}\n\nQuality checks must pass before publication. This can modify the connected database. CADE cannot tell whether it is development, staging or production; confirm the configured environment before continuing.${projectNote}`))return;
      requestBody.confirm=true;
    }
    const revisionAtRequest=state.revision;
    const result=await request('/api/action','POST',requestBody);
    if (state.currentRun !== currentRun)return;
    const passed=result.passed!==false && result.valid!==false && result.status!=='failed';
    status(`${titleCase(action)} ${passed?'completed':'reported failures'}.`,passed?'success':'error', action==='compile'?null:result);
    if (action==='compile' && result.generated){
      await showSqlPreview(result.artifacts||[],result.generated);
      if (result.test_packs?.length)
        status(`Compile completed. SQL test packs: ${result.test_packs.join(', ')}`, 'success');
      if (state.revision!==revisionAtRequest){state.sqlStale=true;updateSqlState();}
    }
    if (action==='trace') showEngineeringResult(result);
    if (action==='checks')showEngineeringResult(result);
    if (action==='plan')showEngineeringResult(result);
    if (action==='build' && result.manifest_path) {
      el('evidenceManifestPath').value=result.manifest_path; await openEvidence();
    }
  }catch(error){if(state.currentRun===currentRun){
    status(`${titleCase(action)} failed: ${error.message}`,'error','');
    if(action==='compile'){
      state.sqlStale=true;
      await showSqlTab();
      el('sqlPreviewState').textContent='Compilation failed: '+error.message+' Previous generated SQL, if listed, is stale.';
      el('sqlPreviewState').className='sql-preview-state error';
    }
  }}
  finally{if(control && !control.classList.contains('hidden'))busy(control,false);}
}
function updateEvidenceState() {
  const banner=el('evidenceState');if(!banner)return;
  if(state.evidenceStale){banner.className='evidence-state stale';
    banner.textContent='Stale evidence — the product definition changed. Refresh to check the current specification. Previously displayed results are not current proof.';return;}
  if(!state.evidence){banner.className='evidence-state';banner.textContent='Review the traceability matrix. Run a build to generate execution evidence.';return;}
  const evidence=state.evidence;
  banner.className=`evidence-state ${evidence.evidence_status==='published'?'success':evidence.evidence_status==='blocked'?'error':evidence.evidence_status==='stale'?'stale':''}`;
  banner.textContent=evidence.evidence_status==='not_run'?'No run evidence: checks are defined but have not been executed. A local CADE build using the configured connection is highly recommended; if skipped, ensure a data run is completed in development before release.':
    evidence.evidence_status==='stale'?'Selected run does not match the current specification; results are not treated as passed.':
    `Run ${evidence.run_id}: ${evidence.evidence_status}. ${evidence.summary.failed} failed check(s), ${evidence.summary.not_run} not run.`;
}
const evidenceBadge=(value)=>create('span',`evidence-badge ${value||'not_run'}`,value||'not_run');
function drawEvidence(evidence){
  state.evidence=evidence;state.evidenceStale=false;
  if(evidence.manifest_path)el('evidenceManifestPath').value=evidence.manifest_path;
  updateEvidenceState();
  const summary=el('evidenceSummary');summary.replaceChildren();
  [['Requirements',evidence.summary.requirements],['Acceptance criteria',evidence.summary.acceptance_criteria],
   ['Passed',evidence.summary.passed],['Failed',evidence.summary.failed],['Not run',evidence.summary.not_run],
   ['Publication',evidence.publication.status]].forEach(([label,value])=>{
     const box=create('div','evidence-stat');box.append(create('span','evidence-stat-name',label),create('strong','',String(value)));summary.append(box);
   });
  const body=el('evidenceRows');body.replaceChildren();
  evidence.matrix.forEach(item=>{
    const tr=create('tr');
    for(const [key,label] of [['requirement_id','requirement'],['feature_id','feature'],['acceptance_id','acceptance'],['check_id','check']]){
      const td=create('td');const value=item[key];
      if(value){td.append(create('strong','',value));
        const detail=item[label];if(detail)td.append(create('small','evidence-detail',detail));}
      else td.append(create('span','evidence-empty','Not linked'));
      tr.append(td);
    }
    const result=create('td');result.append(evidenceBadge(item.status));tr.append(result);
    tr.append(create('td','',item.failures===null||item.failures===undefined?'—':String(item.failures)));
    body.append(tr);
  });
  const reconciliation=el('evidenceReconciliation');reconciliation.replaceChildren();
  if(!evidence.reconciliation_exceptions.length)reconciliation.append(create('p','description',
    evidence.reconciliation_status==='reviewed'?'No reconciliation exceptions were recorded.':'Reconciliation not yet verified for this run.'));
  evidence.reconciliation_exceptions.forEach(item=>{
    const row=create('div','evidence-item');row.append(evidenceBadge(item.status),
      create('span','',`Branch ${item.branch}: ${item.metric} — ${item.count} record(s)`));reconciliation.append(row);
  });
  const blockers=el('evidenceBlockers');blockers.replaceChildren();
  if(!evidence.publication_blockers.length)blockers.append(create('p','description',
    evidence.evidence_status==='published'?'No blockers: publication completed.':'No blocking execution result has been recorded. Publication is not yet proven.'));
  evidence.publication_blockers.forEach(item=>{
    const row=create('div','evidence-item');row.append(evidenceBadge('blocked'),create('span','',item.message));blockers.append(row);
  });
  const notes=el('evidenceNotes');notes.replaceChildren();
  [...evidence.traceability_warnings,...evidence.evidence_warnings].forEach(message=>notes.append(create('p','evidence-note',message)));
  if(evidence.unlinked_checks.length)notes.append(create('p','evidence-note',
    `Checks without acceptance links: ${evidence.unlinked_checks.map(x=>x.id).join(', ')}`));
  if(!notes.childNodes.length)notes.append(create('p','description','All declared checks have traceable acceptance links.'));
}
async function showEvidenceTab() {
  state.tab='evidence';
  for(const tab of ['form','diagram','yaml','sql','evidence']){
    el(tab+'Panel').classList.toggle('hidden',tab!=='evidence');
    el(tab+'Tab').classList.toggle('selected',tab==='evidence');
    el(tab+'Tab').setAttribute('aria-selected',String(tab==='evidence'));
  }
  updateEvidenceState();
}
let evidenceToken=0;
async function openEvidence() {
  rememberStagePosition();
  if(state.rawChanged)await applyYaml();
  await showEvidenceTab();
  const token=++evidenceToken;
  el('evidenceState').textContent='Loading traceability and available run evidence…';
  const revision=state.revision;
  try {
    const report=await request('/api/evidence','POST',{
      document:state.document,schema:state.schemaId,
      path:el('filePath').value.trim()||state.path||'product.yaml',
      manifest_path:el('evidenceManifestPath').value.trim()||null
    });
    if(token!==evidenceToken||state.tab!=='evidence')return;
    drawEvidence(report);
    if(state.revision!==revision){state.evidenceStale=true;updateEvidenceState();}
  }catch(error){if(token===evidenceToken){state.evidence=null;updateEvidenceState();
    el('evidenceState').textContent='Unable to load evidence: '+error.message;
    el('evidenceState').className='evidence-state error';}}
}

function resetSqlPreview() {
  state.sqlArtifacts=[];state.sqlGenerated=[];state.sqlContent={};state.sqlStale=false;
  el('sqlArtifactSelect').replaceChildren();el('sqlCode').textContent='';el('sqlLineNumbers').textContent='';
  el('sqlGeneratedFiles').replaceChildren();el('sqlFilePath').textContent='—';updateSqlState();
}
function updateSqlState() {
  const banner=el('sqlPreviewState');
  if (state.sqlStale) {
    banner.textContent='Stale preview — the specification has changed. Compile again to refresh the SQL.';
    banner.className='sql-preview-state stale';
  } else if (state.sqlArtifacts.length) {
    banner.textContent=`${state.sqlArtifacts.length} generated SQL artifact(s). SQLGlot translated and SQLFluff checked executable statements during compilation; this does not prove database execution.`;
    banner.className='sql-preview-state success';
  } else {
    banner.textContent='Compile a specification to preview its SQL artifacts and output file paths.';
    banner.className='sql-preview-state';
  }
}
async function copyToClipboard(value) {
  if (!value) return;
  try { await navigator.clipboard.writeText(value); status('Copied to clipboard.', 'success'); }
  catch (error) { status(`Copy unavailable: ${error.message}. Select and copy the path instead.`, 'error'); }
}
async function showSqlTab() {
  state.tab='sql';
  el('formPanel').classList.add('hidden');el('yamlPanel').classList.add('hidden');el('evidencePanel').classList.add('hidden');
  el('diagramPanel').classList.add('hidden');el('sqlPanel').classList.remove('hidden');
  ['form','diagram','yaml','sql','evidence'].forEach(tab=>{
    el(tab+'Tab').classList.toggle('selected',tab==='sql');
    el(tab+'Tab').setAttribute('aria-selected',String(tab==='sql'));
  });
  updateSqlState();
  const codeViewport=el('sqlCode').parentElement;
  const horizontalScroll=codeViewport.scrollLeft;
  el('sqlCode').focus({preventScroll:true});
  el('sqlCode').scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
  codeViewport.scrollLeft=horizontalScroll;
}
let sqlSelectionToken=0;
async function renderSqlArtifact() {
  const token=++sqlSelectionToken;
  const path=el('sqlArtifactSelect').value;
  const artifact=state.sqlArtifacts.find(item=>item.path===path);
  if (!artifact)return;
  if (!Object.hasOwn(state.sqlContent,path)) {
    const result=await request('/api/artifact?path='+encodeURIComponent(path));
    if (token!==sqlSelectionToken)return;
    state.sqlContent[path]=result.content;
  }
  const text=state.sqlContent[path];
  el('sqlArtifactMeta').textContent=`${artifact.product||'Data product'} · ${artifact.kind||'SQL'}${artifact.check_id ? ' · '+artifact.check_id : ''} · ${text.split('\n').length} lines`;
  el('sqlDialect').textContent=(artifact.dialect||'Unknown dialect').toUpperCase();
  el('sqlFilePath').textContent=path;
  el('sqlCode').innerHTML=window.DPCCode.sql(text);
  el('sqlLineNumbers').textContent=Array.from({length:text.split('\n').length},(_,i)=>i+1).join('\n');
}
async function showSqlPreview(artifacts, generated) {
  resetSqlPreview();
  state.sqlGenerated=generated;
  state.sqlArtifacts=artifacts;
  const selector=el('sqlArtifactSelect');
  selector.replaceChildren();
  for (const artifact of artifacts) {
    const option=create('option');option.value=artifact.path;
    option.textContent=`${artifact.product||'Product'} / ${artifact.kind}${artifact.check_id ? ': '+artifact.check_id : ''} [${artifact.dialect||'?'}]`;
    selector.append(option);
  }
  // Load only the selected artifact, keeping project-wide previews lightweight.
  try {
    if (artifacts.length)await renderSqlArtifact();
  } catch(error) {
    status('SQL files were generated, but preview could not read an artifact: '+error.message,'error');
  }
  const host=el('sqlGeneratedFiles');host.replaceChildren();
  for(const path of generated){
    const item=create('div','sql-file-item');item.append(create('code','',path),button('Copy',()=>copyToClipboard(path),'text-button'));
    host.append(item);
  }
  await showSqlTab();
}
function showEngineeringResult(data) {
  const host=el('artifactPreview');host.replaceChildren();
  const reports=data.reports || data.results || (data.plan?[data.plan]:[]);
  for (const item of reports.slice(0,5)) {
    const card=create('div','design-card');card.append(create('h3','',item.path || item.product || 'Product evidence'));
    if (item.lineage)card.append(create('pre','',JSON.stringify(item.lineage,null,2)));
    else if (item.execution_order)card.append(create('pre','',item.execution_order.join('\n↓\n')));
    else card.append(create('pre','',JSON.stringify(item,null,2)));host.append(card);
  }
}
async function openVisualGraph(mode) {
  state.graphMode=mode;
  if (mode==='lineage') state.graphScope='file';
  await showDiagram();
}
async function showDiagram() {
  rememberStagePosition();
  if (state.rawChanged) await applyYaml();
  state.tab='diagram';
  el('formPanel').classList.add('hidden');el('yamlPanel').classList.add('hidden');el('evidencePanel').classList.add('hidden');el('sqlPanel').classList.add('hidden');
  el('diagramPanel').classList.remove('hidden');
  el('formTab').classList.remove('selected');el('yamlTab').classList.remove('selected');el('sqlTab').classList.remove('selected');
  el('diagramTab').classList.add('selected');
  await renderVisualGraph();
}
async function renderVisualGraph() {
  const root=el('designView');root.replaceChildren();
  const controls=create('div','graph-controls');
  const mode=create('select');mode.setAttribute('aria-label','Graph view');
  [['erd','Canonical ERD (design)'],['lineage','Source-to-target flow'],['dependencies','Build dependencies']].forEach(([id,label])=>{
    const option=create('option','',label);option.value=id;mode.append(option);
  });
  mode.value=state.graphMode;
  mode.addEventListener('change',()=>{
    state.graphMode=mode.value;
    if(state.graphMode==='lineage')state.graphScope='file';
    renderVisualGraph().catch(error=>status(error.message,'error'));
  });
  controls.append(create('label','graph-label','View'),mode);
  if(state.graphMode==='dependencies'||state.graphMode==='erd'){
    const scope=create('select');scope.setAttribute('aria-label','Dependency scope');
    [['file','Current specification (draft)'],['project','Entire project (saved YAML)']].forEach(([id,label])=>{
      const option=create('option','',label);option.value=id;scope.append(option);
    });
    scope.value=state.graphScope;
    scope.addEventListener('change',()=>{
      if(scope.value==='project' && state.dirty && !window.confirm('The project diagram uses saved YAML files, not unsaved changes. Continue?')){scope.value='file';return;}
      state.graphScope=scope.value;renderVisualGraph().catch(error=>status(error.message,'error'));
    });
    controls.append(create('label','graph-label','Scope'),scope);
  }
  const graphHost=create('div','graph-host');
  const loading=create('p','description','Loading the designed model from the validated specification…');
  graphHost.append(loading);root.append(controls,graphHost);
  try {
    const response=await request('/api/graph','POST',{
      mode:state.graphMode,scope:state.graphScope,
      document:state.document,schema:state.schemaId,
      path:el('filePath').value.trim() || state.path || 'product.yaml'
    });
    // Ignore outdated requests after the user navigates away or changes the graph view.
    if(state.tab!=='diagram'||mode.value!==state.graphMode||!root.contains(graphHost))return;
    if(state.graphMode==='erd'){
      window.DPCVisualGraph.renderERD(graphHost,response,{onEdit:async()=>{await showForm();await navigateStage('model');}});
    } else if(state.graphMode==='lineage'){
      const selector=create('select');selector.setAttribute('aria-label','Canonical attribute for field lineage');
      const all=create('option','','All canonical attributes');all.value='';selector.append(all);
      (response.fields||[]).forEach(field=>{
        const option=create('option','',field.attribute);option.value=field.attribute;selector.append(option);
      });
      if(!(response.fields||[]).some(item=>item.attribute===state.graphField))state.graphField='';
      selector.value=state.graphField;
      const branch=create('select');branch.setAttribute('aria-label','Union branch');
      const chooseBranch=()=>{
        branch.replaceChildren();const all=create('option','','All branches');all.value='all';branch.append(all);
        const field=response.fields.find(item=>item.attribute===state.graphField);
        (field?.rules||[]).forEach(rule=>{
          const option=create('option','',`Branch ${rule.branch+1}`);option.value=String(rule.branch);branch.append(option);
        });
        if(!(field?.rules||[]).some(rule=>String(rule.branch)===state.graphBranch))state.graphBranch='all';
        branch.value=state.graphBranch;branch.disabled=!field;
      };
      const draw=()=>window.DPCVisualGraph.renderGraph(graphHost,response,{field:state.graphField,branch:state.graphBranch});
      selector.addEventListener('change',()=>{state.graphField=selector.value;state.graphBranch='all';chooseBranch();draw();});
      branch.addEventListener('change',()=>{state.graphBranch=branch.value;draw();});
      chooseBranch();controls.append(create('label','graph-label','Attribute'),selector,
        create('label','graph-label','Union branch'),branch);
      draw();
    } else window.DPCVisualGraph.renderGraph(graphHost,response);
  }catch(error){
    graphHost.replaceChildren(create('p','graph-error',`Cannot display graph: ${error.message}`),
      create('p','description','Correct the specification using Validate, or select a valid saved project. No changes have been discarded.'));
    status(`Graph unavailable: ${error.message}`,'error');
  }
}
function updateHighlight() {
  const text=el('yamlText');
  el('yamlHighlight').innerHTML=window.DPCCode.yaml(text.value,state.yamlIssues.map(issue=>issue.line));
  el('yamlHighlight').scrollTop=text.scrollTop;
  el('yamlHighlight').scrollLeft=text.scrollLeft;
}
function yamlContext() {
  const textarea=el('yamlText');
  const prior=textarea.value.slice(0,textarea.selectionStart).split('\n');
  const current=prior.pop()||'';
  const indent=current.match(/^\s*/)[0].length;
  const stack=[];
  for(const line of prior){
    if (!line.trim()||line.trim().startsWith('#'))continue;
    const match=line.match(/^(\s*)(-\s*)?([\w.-]+)\s*:\s*(.*)$/);
    if(!match)continue;
    const level=match[1].length;
    while(stack.length && stack.at(-1).indent>=level)stack.pop();
    // A list item starts a new object under array.items.
    if (match[2]) {
      stack.push({indent:level,key:'__item__'});
      if(!match[4].trim())stack.push({indent:level+1,key:match[3]});
    } else stack.push({indent:level,key:match[3]});
  }
  while(stack.length && stack.at(-1).indent>=indent)stack.pop();
  let schema=state.schema;
  const descend=(area,key)=>{
    area=resolved(area);
    const variants=variantChoices(area);
    if(variants){
      const found=variants.find(v=>Object.hasOwn(resolved(v).properties||{},key));
      if(found)area=resolved(found);
    }
    let child=area.properties?.[key] || area.patternProperties?.[Object.keys(area.patternProperties||{})[0]] ||
      (isObject(area.additionalProperties)?area.additionalProperties:null);
    return child?resolved(child):{};
  };
  for(const level of stack){
    schema=level.key==='__item__' ? resolved(schema).items||{} : descend(schema,level.key);
  }
  // Keys versus values are both generated from the actual selected schema.
  const valueMatch=current.match(/^\s*(?:-\s+)?([\w.-]+)\s*:\s*([^#]*)$/);
  if(valueMatch){
    const field=descend(schema,valueMatch[1]);
    return {kind:'value',schema:field,prefix:valueMatch[2].trim(),start:textarea.selectionStart-valueMatch[2].length};
  }
  const keyMatch=current.match(/^\s*(?:-\s+)?([\w.-]*)$/);
  return {kind:'key',schema:resolved(schema),prefix:keyMatch?.[1]||'',start:textarea.selectionStart-(keyMatch?.[1]||'').length};
}
function showYamlSuggestions() {
  const textarea=el('yamlText');const ctx=yamlContext();const area=resolved(ctx.schema);
  let choices=[];
  if(ctx.kind==='value'){
    const values=area.enum||(area.const!==undefined?[area.const]:null);
    if(values)choices=values.map(value=>({label:JSON.stringify(value),insert:typeof value==='string'?String(value):JSON.stringify(value),detail:area.description||'Allowed value'}));
  }else{
    const variants=variantChoices(area)||[area];
    const props=Object.assign({},...variants.map(v=>resolved(v).properties||{}));
    choices=Object.entries(props).map(([key,field])=>({label:key,insert:key+': ',detail:resolved(field).description||'Schema field'}));
  }
  choices=choices.filter(c=>c.label.toLowerCase().startsWith(ctx.prefix.toLowerCase())).slice(0,50);
  const host=el('yamlSuggestions');host.replaceChildren();
  for(const item of choices) {
    const suggestion=button(item.label+' — '+item.detail.slice(0,110),()=>{
      const pos=textarea.selectionStart;
      textarea.value=textarea.value.slice(0,ctx.start)+item.insert+textarea.value.slice(pos);
      textarea.focus();const cursor=ctx.start+item.insert.length;textarea.setSelectionRange(cursor,cursor);
      host.classList.add('hidden');state.rawChanged=true;state.yamlDraft=textarea.value;markDirty();updateHighlight();scheduleYamlDiagnostics();
    },'yaml-suggestion');
    host.append(suggestion);
  }
  host.classList.toggle('hidden',!choices.length);
}
let yamlDiagnosticsTimer=null,yamlDiagnosticsSequence=0;
function scheduleYamlDiagnostics(){
  if(state.tab!=='yaml')return;
  const id=++yamlDiagnosticsSequence;
  el('yamlDiagnosticSummary').textContent='Checking unsaved YAML…';
  clearTimeout(yamlDiagnosticsTimer);
  yamlDiagnosticsTimer=setTimeout(async()=>{
    try{
      const data=await request('/api/yaml/diagnostics','POST',{
        yaml:el('yamlText').value,path:el('filePath').value.trim()||state.path||'product.yaml',schema:state.schemaId});
      if(id!==yamlDiagnosticsSequence)return;
      state.yamlIssues=data.issues||[];renderYamlDiagnostics();updateHighlight();
    }catch(error){
      if(id!==yamlDiagnosticsSequence)return;
      el('yamlDiagnosticSummary').textContent='Diagnostics unavailable: '+error.message;
    }
  },450);
}
function jumpToYamlLine(line,column=1){
  const textarea=el('yamlText');const lines=textarea.value.split('\n');
  const pos=lines.slice(0,Math.max(0,line-1)).reduce((sum,value)=>sum+value.length+1,0)+Math.max(0,column-1);
  textarea.focus();textarea.setSelectionRange(pos,pos);
  textarea.scrollTop=Math.max(0,(line-3)*21);
  updateHighlight();
}
function renderYamlDiagnostics(){
  const issues=state.yamlIssues;const host=el('yamlDiagnostics');host.replaceChildren();
  el('yamlDiagnosticSummary').textContent=issues.length?`${issues.length} issue(s)`:'No problems found';
  for(const issue of issues.slice(0,60)){
    const link=button(`Line ${issue.line||1}:${issue.column||1} · ${issue.path} — ${issue.message}${issue.suggestion?' · '+issue.suggestion:''}`,
      ()=>jumpToYamlLine(issue.line||1,issue.column||1),'yaml-diagnostic');
    host.append(link);
  }
  if(issues.length>60)host.append(create('p','description',`${issues.length-60} more issues. Correct the first ones and validate again.`));
}
let engineEntries = [];
let installationPending = false;
let installationEnvironment = null;
function updateEngineSelection() {
  const selected = engineEntries.find(item => item.id === el('engineChoice').value);
  const button = el('installEngine');
  button.disabled = installationPending || !selected || !selected.install_extra || selected.driver_loaded;
  if (!selected) return;
  const capability = selected.build_ready ? 'Driver ready for certified native builds' :
    (selected.driver_loaded ? 'Driver loads; safe native build not certified' : 'Driver not ready');
  el('engineInfo').textContent = `${capability}. ${selected.install_command || 'No package is required.'} ` +
    'The YAML connection URL still determines the database. Installing a driver does not enable publication.';
}
async function refreshEngineCatalog() {
  engineEntries = (await request('/api/engines')).engines;
  const picker = el('engineChoice');
  const previous = picker.value;
  picker.replaceChildren();
  for (const entry of engineEntries) {
    const option = create('option');
    option.value = entry.id;
    option.textContent = `${entry.name} — ${entry.driver_loaded ? 'Driver ready' :
      (entry.package_installed ? 'Installed; driver unavailable' : 'Not installed')}`;
    picker.append(option);
  }
  if (engineEntries.some(entry => entry.id === previous)) picker.value = previous;
  updateEngineSelection();
}
async function pollDriverInstallation() {
  while (true) {
    const state = await request('/api/engines/installation');
    const job = state.job;
    if (!job || job.status !== 'installing') {
      installationPending = false;
      await refreshEngineCatalog();
      const ok = job && (job.status === 'succeeded' || job.status === 'already_installed');
      el('installStatus').textContent = job ? `${job.message} (${job.status}).` : 'No install in progress.';
      status(job ? `${job.engine_id}: ${job.message}` : 'No installation in progress.', ok ? 'success' : 'error');
      return;
    }
    installationPending = true;
    updateEngineSelection();
    el('installStatus').textContent = `Installing ${job.package} into ${state.interpreter}. ` +
      'Do not close the editor until installation completes.';
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}
async function installSelectedEngine() {
  const selected = engineEntries.find(item => item.id === el('engineChoice').value);
  if (!selected || !selected.install_extra || selected.driver_loaded || installationPending) return;
  try {
    const env = installationEnvironment || await request('/api/engines/installation');
    installationEnvironment = env;
    const warning = env.virtual_environment ? '' : '\nWARNING: This interpreter is not in a virtual environment.';
    if (!window.confirm(`Install ${selected.name} (${selected.package}) using pip into:\n` +
        `${env.interpreter}?${warning}\n\nThis installs third-party code with your current Python permissions. Continue?`)) return;
    installationPending = true; updateEngineSelection();
    const job = await request('/api/engines/install', 'POST', {engine_id: selected.id, confirm: true});
    el('installStatus').textContent = job.status === 'installing' ? 'Installation started…' : job.status;
    if (job.status === 'installing') await pollDriverInstallation();
    else { installationPending = false; await refreshEngineCatalog(); } 
  } catch (error) {
    installationPending = false; updateEngineSelection();
    el('installStatus').textContent = error.message;
    status(error.message, 'error');
  }
}

function setWorkspaceSidebar(open, opener = null) {
  const sidebar=el('workspaceSidebar');
  const expanded=String(open);
  sidebar.classList.toggle('hidden',!open);
  sidebar.setAttribute('aria-hidden',String(!open));
  el('workspaceScrim').classList.toggle('hidden',!open);
  el('openWorkspace').setAttribute('aria-expanded',expanded);
  el('openWorkspaceTop').setAttribute('aria-expanded',expanded);
  if(open) {
    state.workspaceOpener=opener || document.activeElement;
    el('closeWorkspace').focus();
  } else if(state.workspaceOpener?.isConnected) {
    state.workspaceOpener.focus();
  }
}

async function boot() {
  try {
    const init = await request('/api/bootstrap'); state.token = init.token;
    el('workspaceLabel').textContent = init.workspace;
    await refreshSchemas();
    await activateSchema('schemas/product-v0.8.5.schema.json', false);
    try {
      const dialects = (await request('/api/dialects')).dialects;
      for (const dialect of dialects) { const option = create('option'); option.value = dialect; el('dialectList').append(option); }
    } catch { /* editing must work even when optional SQL dialect packages are unavailable */ }
    try {
      installationEnvironment = await request('/api/engines/installation');
      await refreshEngineCatalog();
      if (installationEnvironment.job?.status === 'installing') {
        installationPending = true; updateEngineSelection();
        pollDriverInstallation().catch(error => {
          installationPending = false; updateEngineSelection(); status(error.message, 'error');
        });
      }
    } catch { el('engineInfo').textContent = 'Driver inventory unavailable. Run cade engines in your terminal.'; }
    await refreshFiles();
    const tutorialSpec = new URLSearchParams(location.search).get('spec');
    if (tutorialSpec) {
      await loadFile(tutorialSpec);
      const requestedStage = new URLSearchParams(location.search).get('stage');
      if (STAGES.includes(requestedStage)) { state.activeStage = requestedStage; drawForm(); }
    }
    else if (state.files.length) await loadFile(state.files[0]);
    else await newFile();
  } catch (error) { status(`Editor initialization failed: ${error.message}`, 'error'); }
}
el('engineChoice').addEventListener('change', updateEngineSelection);
el('installEngine').addEventListener('click', installSelectedEngine);
el('openWorkspace').addEventListener('click', event => setWorkspaceSidebar(true,event.currentTarget));
el('openWorkspaceTop').addEventListener('click', event => setWorkspaceSidebar(true,event.currentTarget));
el('closeWorkspace').addEventListener('click', () => setWorkspaceSidebar(false));
el('workspaceScrim').addEventListener('click', () => setWorkspaceSidebar(false));
document.addEventListener('keydown', event => {
  if(event.key==='Escape' && !el('workspaceSidebar').classList.contains('hidden'))
    setWorkspaceSidebar(false);
});
el('schemaSelect').addEventListener('change', async event => {
  const selected = event.target.value;
  try {
    if (state.rawChanged) await applyYaml();
    await activateSchema(selected);
    status('YAML schema.path and schema.version updated to ' + selected + '. Save to pin this contract.', 'muted');
  } catch (error) {
    event.target.value = state.schemaId;
    status(error.message, 'error');
  }
});
el('importSchemaFile').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const document = JSON.parse(await file.text());
    const result = await request('/api/schema-file', 'PUT', {
      path: 'schemas/' + file.name, document,
    });
    await refreshSchemas();
    await activateSchema(result.path);
    status('Imported and selected JSON Schema: ' + result.path, 'success');
  } catch (error) { status(error.message, 'error'); }
  event.target.value = '';
});
el('newFile').addEventListener('click', newFile);
el('refreshFiles').addEventListener('click', () => refreshFiles().catch(e => status(e.message, 'error')));
el('saveFile').addEventListener('click', async () => {
  try { await saveFile(); }
  catch (e) {
    el('dirtyState').textContent = 'Save failed';
    el('dirtyState').title = e.message;
    status(e.message, 'error');
  }
});
el('downloadYaml').addEventListener('click', async () => { try { await exportFile(); } catch (e) { status(e.message, 'error'); } });
el('formTab').addEventListener('click', async () => { try { await showForm(); } catch (e) { status(e.message, 'error'); } });
el('yamlTab').addEventListener('click', async () => { try { await showYaml(); } catch (e) { status(e.message, 'error'); } });
el('sqlTab').addEventListener('click',()=>showSqlTab().catch(error=>status(error.message,'error')));
el('evidenceTab').addEventListener('click',()=>openEvidence().catch(error=>status(error.message,'error')));
el('reloadEvidence').addEventListener('click',()=>openEvidence().catch(error=>status(error.message,'error')));
el('copyEvidencePath').addEventListener('click',()=>copyToClipboard(el('evidenceManifestPath').value.trim()));
el('sqlArtifactSelect').addEventListener('change',()=>renderSqlArtifact().catch(error=>status(error.message,'error')));
el('copySql').addEventListener('click',()=>copyToClipboard(state.sqlContent[el('sqlArtifactSelect').value]));
el('copySqlPath').addEventListener('click',()=>copyToClipboard(el('sqlArtifactSelect').value));
el('applyYaml').addEventListener('click', async () => { try { await applyYaml(); await showForm(); } catch (e) { status(e.message, 'error'); } });
el('yamlText').addEventListener('input', event => { state.yamlCursor=el('yamlText').selectionStart;state.rawChanged=true;state.yamlDraft=el('yamlText').value;markDirty();updateHighlight();scheduleYamlDiagnostics();
  if (/^[a-z_\-]$/i.test(event.data||'') && /[a-z_]{2,}$/i.test(el('yamlText').value.slice(0,el('yamlText').selectionStart).split('\n').at(-1).trim()))showYamlSuggestions(); });
el('yamlText').addEventListener('scroll', () => {state.yamlScroll=el('yamlText').scrollTop;updateHighlight();});
el('yamlText').addEventListener('click',()=>{state.yamlCursor=el('yamlText').selectionStart;});
el('yamlText').addEventListener('keyup',()=>{state.yamlCursor=el('yamlText').selectionStart;});
el('yamlText').addEventListener('keydown', event => {if((event.ctrlKey||event.metaKey) && event.code==='Space'){event.preventDefault();showYamlSuggestions();}
  if(event.key==='Escape')el('yamlSuggestions').classList.add('hidden');});
el('diagramTab').addEventListener('click', () => showDiagram().catch(e=>status(e.message,'error')));
el('validateTop').addEventListener('click', () => runAction('validate'));
el('filePath').addEventListener('change', () => { syncSchemaReference(); markDirty(); });
el('runAction').addEventListener('click', runAction);
el('clearResults').addEventListener('click', () => { state.validation=null;showIssues([]);el('results').textContent='';status('Results cleared.', 'muted'); });
el('quickStartAction').addEventListener('click', handleQuickStartAction);
el('quickStartPath').addEventListener('input', () => {
  el('filePath').value=el('quickStartPath').value;
  markDirty(false);
});
el('dismissQuickStart').addEventListener('click', () => el('quickStart').classList.add('hidden'));
el('importFile').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (state.dirty && !window.confirm('Discard unsaved changes and import YAML?')) return;
  try {
    const result = await request('/api/yaml/import', 'POST', { yaml: await file.text(), path: file.name });
    if (result.schema && result.schema !== state.schemaId) await activateSchema(result.schema, false);
    resetSqlPreview();state.evidence=null;state.evidenceStale=false;el('evidenceManifestPath').value='';state.document = result.document; state.path = ''; state.etag = null;
    el('filePath').value = file.name; syncSchemaReference(); state.rawChanged = false;
    state.yamlDraft=await file.text();state.yamlBaseline=JSON.stringify(state.document); markDirty(); updateDocumentTitle();
    await showForm(); status('YAML imported. Choose a path and save it to the workspace.', 'success');
  } catch (error) { status(error.message, 'error'); }
  event.target.value = '';
});
boot();
