// Dependency-free UI smoke test: render a real graph with a minimal DOM contract.
// This complements Python lineage tests; it is not a substitute for Flask/browser E2E.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.classes = new Set();
    this.style = {}; this.clientWidth = 750; this.textContent = ''; this.events = {}; this.pointer = null;
    this.classList = {
      add: name => { this.classes.add(name); this.attributes.class = [...this.classes].join(' '); },
      remove: name => { this.classes.delete(name); this.attributes.class = [...this.classes].join(' '); },
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classes.has(name) : force;
        if (enabled) this.classList.add(name); else this.classList.remove(name);
        return enabled;
      }
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name === 'class') this.classes = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  removeEventListener(name, callback) {
    this.events[name] = (this.events[name] || []).filter(existing => existing !== callback);
  }
  dispatch(name, event = {}) { return (this.events[name] || []).map(callback => callback(event)); }
  setPointerCapture(pointer) { this.pointer = pointer; }
  hasPointerCapture(pointer) { return this.pointer === pointer; }
  releasePointerCapture() { this.pointer = null; }
  getBoundingClientRect() { return {width:Number(this.attributes.width)||773,height:Number(this.attributes.height)||649}; }
  scrollTo() { }
}
const context = {window: {}, document: {
  createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)
}};
context.localStorage = {
  values:new Map(),
  getItem(key){return this.values.get(key) ?? null;},
  setItem(key,value){this.values.set(key,String(value));}
};
context.document.exitFullscreen = async () => {
  const fullscreen = context.document.fullscreenElement;
  context.document.fullscreenElement = null;
  fullscreen?.dispatch('fullscreenchange');
};
const file = path.join(__dirname, '../src/cadeo/web/lineage-graph.js');
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
context.window.DPCVisualGraph.renderGraph(host, graph, {field:'device_id',layoutKey:'lineage-smoke'});
function descendants(element) {return [element,...element.children.flatMap(descendants)];}
const all = descendants(host);
assert(all.some(x=>x.tag==='svg'), 'SVG graph must render');
assert.equal(all.filter(x=>x.tag==='g' && x.attributes.role==='button').length,3);
assert(all.some(x=>x.textContent.includes('bronze.devices.asset_id')),
  'Selected field should display physical lineage');
assert(all.some(x=>x.textContent.includes('REQ_001')),
  'Selected field should display linked requirement ID');
const lineageNode=all.find(x=>x.tag==='g' && x.attributes.class?.includes('graph-node'));
const lineageEdge=all.find(x=>x.tag==='path' && x.attributes.class?.includes('graph-edge'));
const lineageStart=lineageNode.attributes.transform,lineageEdgeStart=lineageEdge.attributes.d;
lineageNode.dispatch('pointerdown',{button:0,isPrimary:true,pointerId:2,clientX:100,clientY:100});
lineageNode.dispatch('pointermove',{clientX:140,clientY:130});
lineageNode.dispatch('pointerup');
assert.notEqual(lineageNode.attributes.transform,lineageStart,'Lineage nodes should be draggable');
assert.notEqual(lineageEdge.attributes.d,lineageEdgeStart,'Lineage edges should follow moved nodes');
const lineagePosition=lineageNode.attributes.transform;
const restoredLineage=new Element('div');
context.window.DPCVisualGraph.renderGraph(restoredLineage,graph,{layoutKey:'lineage-smoke'});
assert.equal(descendants(restoredLineage).find(x=>x.tag==='g'&&x.attributes.class?.includes('graph-node')).attributes.transform,
  lineagePosition,'Lineage node positions should persist by graph key');
const dependencyHost=new Element('div');
context.window.DPCVisualGraph.renderGraph(dependencyHost,{...graph,mode:'dependencies'},{layoutKey:'dependencies-smoke'});
const dependencyNode=descendants(dependencyHost).find(x=>x.tag==='g'&&x.attributes.class?.includes('graph-node'));
const dependencyStart=dependencyNode.attributes.transform;
dependencyNode.dispatch('keydown',{key:'ArrowRight',shiftKey:false,preventDefault(){}});
assert.notEqual(dependencyNode.attributes.transform,dependencyStart,'Dependency nodes should move by keyboard');
const dependencyPosition=dependencyNode.attributes.transform;
const restoredDependencies=new Element('div');
context.window.DPCVisualGraph.renderGraph(restoredDependencies,{...graph,mode:'dependencies'},{layoutKey:'dependencies-smoke'});
assert.equal(descendants(restoredDependencies).find(x=>x.tag==='g'&&x.attributes.class?.includes('graph-node')).attributes.transform,
  dependencyPosition,'Dependency node positions should persist by graph key');

const erdHost = new Element('div');
const entity = (id, name, attributes) => ({id, name, product:'tutorial', target:id,
  grain:`One row per ${id}`, external:false, key_attributes:[], attributes});
const erd = {
  mode:'erd',
  entities:[
    entity('connection','Connection',[{name:'merchant_id',type:'string',nullable:true},
      {name:'service_id',type:'string',nullable:true}]),
    entity('merchant','Merchant',[{name:'merchant_id',type:'string',nullable:false}]),
    entity('service','Service',[{name:'service_id',type:'string',nullable:false}])
  ],
  relationships:[
    {from:'connection',to:'merchant',attribute:'merchant_id',related_attribute:'merchant_id',cardinality:'many_to_one',status:'resolved'},
    {from:'connection',to:'service',attribute:'service_id',related_attribute:'service_id',cardinality:'many_to_one',status:'resolved'}
  ]
};
erdHost.requestFullscreen = async () => {
  context.document.fullscreenElement = erdHost;
  erdHost.dispatch('fullscreenchange');
};
context.window.DPCVisualGraph.renderGraph(erdHost,erd,{layoutKey:'erd-smoke'});
const erdElements = descendants(erdHost);
const erdEdges = erdElements.filter(x=>x.tag==='path' && x.attributes.class?.includes('erd-edge'));
assert.equal(erdEdges.length,2,'ERD should draw both declared relationships');
const verticalEdge = erdEdges.find(x=>x.children[0]?.textContent.includes('Connection.service_id'));
assert.match(verticalEdge.attributes.d,/^M334 \d+ C389 \d+,389 \d+,334 \d+$/,
  'Same-column relationship should route outside the cards');
const firstCard = erdElements.find(x=>x.tag==='g' && x.attributes.class?.includes('erd-card'));
const dragHandle = firstCard.children.find(x=>x.attributes.class==='erd-hit');
const startingPosition = firstCard.attributes.transform;
const startingEdge = verticalEdge.attributes.d;
dragHandle.dispatch('pointerdown',{button:0,isPrimary:true,pointerId:1,clientX:100,clientY:100});
dragHandle.dispatch('pointermove',{clientX:140,clientY:130});
dragHandle.dispatch('pointerup');
assert.notEqual(firstCard.attributes.transform,startingPosition,'Dragging should reposition the entity');
assert.notEqual(verticalEdge.attributes.d,startingEdge,'Dragging should recalculate connected edges');
const draggedPosition = firstCard.attributes.transform;
dragHandle.dispatch('keydown',{key:'ArrowDown',shiftKey:false,preventDefault(){}});
assert.notEqual(firstCard.attributes.transform,draggedPosition,'Arrow keys should reposition the focused entity');
const erdPosition=firstCard.attributes.transform;
const restoredERD=new Element('div');
restoredERD.requestFullscreen=erdHost.requestFullscreen;
context.window.DPCVisualGraph.renderGraph(restoredERD,erd,{layoutKey:'erd-smoke'});
assert.equal(descendants(restoredERD).find(x=>x.tag==='g'&&x.attributes.class?.includes('erd-card')).attributes.transform,
  erdPosition,'ERD card positions should persist by graph key');

const fullscreenButton = erdElements.find(x=>x.tag==='button' && x.attributes['aria-label']==='Enter full screen');
assert(fullscreenButton,'ERD should provide a full-screen control');
(async () => {
  await Promise.all(fullscreenButton.dispatch('click'));
  assert.equal(context.document.fullscreenElement,erdHost);
  assert.equal(fullscreenButton.textContent,'Exit full screen');
  await Promise.all(fullscreenButton.dispatch('click'));
  assert.equal(context.document.fullscreenElement,null);
  assert.equal(fullscreenButton.textContent,'Full screen');
  console.log('SVG lineage, draggable diagrams, persistence and full-screen smoke tests passed');
})().catch(error=>{
  console.error(error);
  process.exitCode=1;
});
