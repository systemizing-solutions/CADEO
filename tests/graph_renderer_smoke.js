// Dependency-free UI smoke test: render a real graph with a minimal DOM contract.
// This complements Python lineage tests; it is not a substitute for Flask/browser E2E.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {};
    this.style = {}; this.clientWidth = 750; this.textContent = ''; this.events = {}; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.events[name] = callback; }
  scrollTo() { }
}
const context = {window: {}, document: {
  createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)
}};
const file = path.join(__dirname, '../src/cade/web/lineage-graph.js');
vm.runInNewContext(fs.readFileSync(file, 'utf8'), context, {filename: file});
const graph = {
  mode: 'lineage',
  nodes: [{id:'source:devices',kind:'source',label:'devices',detail:'bronze.devices'},
    {id:'transform:0',kind:'transformation',label:'Transform · branch 1',detail:'1 field'},
    {id:'target:canonical',kind:'target',label:'Device',detail:'gold.devices'}],
  edges:[{from:'source:devices',to:'transform:0',kind:'pipeline',label:''},
    {from:'transform:0',to:'target:canonical',kind:'pipeline',label:''}],
  fields:[{attribute:'device_id',type:'string',requirement_ids:['REQ_001'],rules:[
    {branch:0,summary:'d.asset_id',derivation:'source',direct_columns:[
      {alias_column:'d.asset_id',physical_column:'bronze.devices.asset_id',source_id:'devices'}],
    context_columns:[],node_ids:['source:devices','transform:0','target:canonical']}]}]
};
const host = new Element('div');
context.window.DPCVisualGraph.renderGraph(host, graph, {field:'device_id'});
function descendants(element) {return [element,...element.children.flatMap(descendants)];}
const all = descendants(host);
assert(all.some(x=>x.tag==='svg'), 'SVG graph must render');
assert.equal(all.filter(x=>x.tag==='g' && x.attributes.role==='button').length,3);
assert(all.some(x=>x.textContent.includes('bronze.devices.asset_id')),
  'Selected field should display physical lineage');
assert(all.some(x=>x.textContent.includes('REQ_001')),
  'Selected field should display linked requirement ID');
console.log('SVG lineage renderer smoke test passed');
