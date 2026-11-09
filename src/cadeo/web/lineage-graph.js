/* Read-only graph projection of /api/graph. The backend owns lineage and DAG semantics.
   All node labels and detail values are inserted as text, never interpreted as HTML. */
'use strict';
window.DPCVisualGraph = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs = {}, text) => {
    const result = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attrs)) result.setAttribute(name, String(value));
    if (text !== undefined) result.textContent = String(text);
    return result;
  };
  const make = (tag, cls, value) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (value !== undefined) element.textContent = String(value);
    return element;
  };
  const COLORS = {
    source: '#eaf4fd', join: '#fff4db', filter: '#fef0e9',
    transformation: '#e9f0fc', union: '#eee9ff', deduplication: '#eee9ff',
    target: '#e0f5e9', 'quality-check': '#eff6ee', publication: '#e0f5e9',
    preservation: '#eef4fa', 'product-reference': '#eaf4fd', default: '#f0f4f8'
  };
  function layout(graph) {
    // Longest-path layering. The service's graph is a DAG; depth is derived
    // from edges, not from a hard-coded source/join/target layout.
    const byId = new Map(graph.nodes.map(item => [item.id, item]));
    const parents = new Map(graph.nodes.map(item => [item.id, []]));
    for (const edge of graph.edges) if (parents.has(edge.to) && byId.has(edge.from)) {
      parents.get(edge.to).push(edge.from);
    }
    const memo = new Map();
    const visiting = new Set();
    const depth = id => {
      if (memo.has(id)) return memo.get(id);
      if (visiting.has(id)) throw new Error('Graph contains a dependency cycle');
      visiting.add(id);
      const result = Math.min(100, 1 + Math.max(-1, ...parents.get(id).map(depth)));
      visiting.delete(id);
      memo.set(id, result);
      return result;
    };
    graph.nodes.forEach(item => depth(item.id));
    const columns = new Map();
    graph.nodes.forEach(item => {
      const rank = memo.get(item.id);
      if (!columns.has(rank)) columns.set(rank, []);
      columns.get(rank).push(item);
    });
    const width = 215, height = 76, xgap = 76, ygap = 33;
    const maxNodes = Math.max(1, ...[...columns.values()].map(items => items.length));
    const positions = new Map();
    for (const [rank, values] of columns) {
      const total = values.length * (height + ygap);
      const offset = Math.max(0, (maxNodes * (height + ygap) - total) / 2);
      values.forEach((item, index) => positions.set(item.id, {
        x: 35 + rank * (width + xgap), y: 38 + offset + index * (height + ygap), w: width, h: height
      }));
    }
    return {
      positions,
      width: 90 + (Math.max(0, ...columns.keys()) + 1) * (width + xgap),
      height: 90 + maxNodes * (height + ygap)
    };
  }
  function loadPositions(key, positions, width, height) {
    if (!key) return;
    try {
      const saved = JSON.parse(localStorage.getItem(key) || '{}');
      for (const [id, position] of positions) {
        const prior = saved[id];
        if (!Number.isFinite(prior?.x) || !Number.isFinite(prior?.y)) continue;
        position.x = Math.max(0, Math.min(width - position.w, prior.x));
        position.y = Math.max(0, Math.min(height - position.h, prior.y));
      }
    } catch { /* Storage may be unavailable in private or restricted contexts. */ }
  }
  function savePositions(key, positions) {
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(Object.fromEntries(
        [...positions].map(([id, position]) => [id, {x:position.x,y:position.y}]))));
    } catch { /* A diagram remains usable when its layout cannot be persisted. */ }
  }
  function enableDrag(handle, group, position, svg, dimensions, onMove, onCommit) {
    let suppressClick = false;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.isPrimary === false) return;
      const bounds = svg.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const size = dimensions();
      const origin = {x:position.x,y:position.y};
      const startX = event.clientX, startY = event.clientY;
      let moved = false;
      const move = moveEvent => {
        const dx = (moveEvent.clientX - startX) * size.width / bounds.width;
        const dy = (moveEvent.clientY - startY) * size.height / bounds.height;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        position.x = Math.max(0, Math.min(size.width - position.w, origin.x + dx));
        position.y = Math.max(0, Math.min(size.height - position.h, origin.y + dy));
        group.setAttribute('transform', `translate(${position.x} ${position.y})`);
        group.classList.add('dragging');
        onMove();
      };
      const finish = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', finish);
        handle.removeEventListener('pointercancel', finish);
        if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
        group.classList.remove('dragging');
        if (moved) { suppressClick = true; onCommit(); }
      };
      handle.setPointerCapture(event.pointerId);
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
    });
    handle.addEventListener('keydown', event => {
      const step = event.shiftKey ? 40 : 20;
      const offset = {ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]}[event.key];
      if (!offset) return;
      const size = dimensions();
      position.x = Math.max(0, Math.min(size.width - position.w, position.x + offset[0]));
      position.y = Math.max(0, Math.min(size.height - position.h, position.y + offset[1]));
      group.setAttribute('transform', `translate(${position.x} ${position.y})`);
      onMove(); onCommit(); event.preventDefault();
    });
    return () => { const dragged = suppressClick; suppressClick = false; return dragged; };
  }
  const truncate = (text, limit) => text.length > limit ? text.slice(0, limit - 1) + '…' : text;
  function renderGraph(container, graph, { field = '', branch = 'all', onField, layoutKey = '' } = {}) {
    container.replaceChildren();
    const selectedField = (graph.fields || []).find(item => item.attribute === field);
    const selectedRules = selectedField ? selectedField.rules.filter(rule => branch === 'all' || String(rule.branch) === String(branch)) : [];
    const highlighted = new Set(selectedRules.flatMap(rule => rule.node_ids));
    const isFocus = Boolean(selectedField);
    const area = make('div', 'graph-scroll');
    const canvas = make('div', 'graph-canvas');
    const { positions, width, height } = layout(graph);
    loadPositions(layoutKey, positions, width, height);
    const svg = node('svg', { width, height, viewBox: `0 0 ${width} ${height}`,
      role: 'img', 'aria-label': graph.mode === 'lineage' ? 'Data product lineage flow' : 'Dependency execution graph' });
    const defs = node('defs');
    const arrow = node('marker', { id: 'dpc-arrow', markerWidth: 10, markerHeight: 10,
      refX: 9, refY: 5, orient: 'auto-start-reverse', markerUnits: 'strokeWidth' });
    arrow.append(node('path', { d: 'M 0 1 L 9 5 L 0 9 Z', fill: '#8da6bc' }));
    defs.append(arrow); svg.append(defs);
    const lines = node('g');
    const edgePaths = [];
    graph.edges.forEach(edge => {
      const a = positions.get(edge.from), b = positions.get(edge.to);
      if (!a || !b) return;
      const lit = !isFocus || (highlighted.has(edge.from) && highlighted.has(edge.to));
      const sx = a.x + a.w, sy = a.y + a.h / 2, tx = b.x, ty = b.y + b.h / 2;
      const delta = Math.max(30, (tx - sx) / 2);
      const curve = node('path', {
        d: `M${sx} ${sy} C${sx + delta} ${sy},${tx - delta} ${ty},${tx} ${ty}`,
        class: `graph-edge ${lit ? 'active' : 'muted'}`,
        'marker-end': 'url(#dpc-arrow)'
      });
      const tooltip = node('title', {}, `${edge.from} → ${edge.to}${edge.label ? ` · ${edge.label}` : ''}`);
      curve.append(tooltip); lines.append(curve);
      edgePaths.push({edge,path:curve});
    });
    svg.append(lines);
    const nodes = node('g');
    graph.nodes.forEach(item => {
      const pos = positions.get(item.id);
      const group = node('g', {
        class: `graph-node ${isFocus && !highlighted.has(item.id) ? 'muted' : 'active'}`,
        transform: `translate(${pos.x} ${pos.y})`, tabindex: 0, role: 'button',
        'aria-label': `${item.kind}: ${item.label}. ${item.detail || ''}`
      });
      group.append(node('rect', { width: pos.w, height: pos.h, rx: 10,
        fill: COLORS[item.kind] || COLORS.default, stroke: '#acbfce', 'stroke-width': 1 }));
      group.append(node('text', { x: 13, y: 20, class: 'graph-node-kind' }, item.kind.replace(/-/g, ' ')));
      group.append(node('text', { x: 13, y: 42, class: 'graph-node-title' }, truncate(item.label, 26)));
      group.append(node('text', { x: 13, y: 61, class: 'graph-node-detail' }, truncate(item.detail || '', 31)));
      group.append(node('title', {}, `${item.label}\n${item.detail || item.id}`));
      group.setAttribute('aria-label', `${item.kind}: ${item.label}. ${item.detail || ''}. Drag or use arrow keys to move.`);
      const dragged=enableDrag(group, group, pos, svg, () => ({width,height}), () => {
        edgePaths.forEach(({edge,path}) => {
          const a=positions.get(edge.from),b=positions.get(edge.to);
          if (!a || !b) return;
          const same=a.x===b.x,leftToRight=a.x<b.x;
          const sx=same?a.x+a.w:(leftToRight?a.x+a.w:a.x);
          const tx=same?b.x+b.w:(leftToRight?b.x:b.x+b.w);
          const sy=a.y+a.h/2,ty=b.y+b.h/2;
          const routeX=Math.max(a.x+a.w,b.x+b.w)+45;
          const delta=Math.max(30,Math.abs(tx-sx)/2);
          const direction=leftToRight?1:-1;
          const curve=same?`M${sx} ${sy} C${routeX} ${sy},${routeX} ${ty},${tx} ${ty}`:
            `M${sx} ${sy} C${sx+direction*delta} ${sy},${tx-direction*delta} ${ty},${tx} ${ty}`;
          path.setAttribute('d',curve);
        });
        resize();
      }, () => savePositions(layoutKey, positions));
      const select = () => showNodeDetail(item);
      group.addEventListener('click', () => {if(!dragged())select();});
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); }
      });
      nodes.append(group);
    });
    svg.append(nodes);
    canvas.append(svg); area.append(canvas);
    const toolbar = make('div', 'graph-zoom');
    let scale = 1;
    function resize() { svg.setAttribute('width',Math.round(width*scale));svg.setAttribute('height',Math.round(height*scale)); }
    function zoom(amount) { scale = Math.max(0.35, Math.min(1.7, scale * amount)); resize(); }
    function button(text, action, label) { const b = make('button', 'btn secondary small', text);
      b.type = 'button'; b.setAttribute('aria-label', label); b.addEventListener('click', action); return b; }
    const fullscreenButton=button('Full screen',()=>toggleFullscreen(),'Enter full screen');
    let previousFullscreenScale=null;
    function updateFullscreenButton(){
      const active=document.fullscreenElement===container;
      fullscreenButton.textContent=active?'Exit full screen':'Full screen';
      fullscreenButton.setAttribute('aria-label',active?'Exit full screen':'Enter full screen');
      fullscreenButton.title=active?'Exit full screen':'Enter full screen';
      if(!active&&previousFullscreenScale!==null){scale=previousFullscreenScale;previousFullscreenScale=null;resize();}
    }
    async function toggleFullscreen(){
      try {
        if(document.fullscreenElement===container)await document.exitFullscreen();
        else {
          previousFullscreenScale=scale;
          await container.requestFullscreen();
          if(document.fullscreenElement===container){
            scale=Math.max(.35,Math.min(1.7,(area.clientWidth-30)/width));
            resize();area.scrollTo({left:0,top:0});
          }
        }
      } catch(error) {
        if(document.fullscreenElement!==container)previousFullscreenScale=null;
        panel.append(make('p','graph-error',`Full-screen mode unavailable: ${error.message}`));
      }
    }
    fullscreenButton.disabled=typeof container.requestFullscreen!=='function'||typeof document.exitFullscreen!=='function';
    container.addEventListener('fullscreenchange',updateFullscreenButton);
    toolbar.append(button('−', () => zoom(0.8), 'Zoom out'), button('+', () => zoom(1.25), 'Zoom in'),
      button('Fit', () => { scale = Math.min(1, Math.max(0.35, (area.clientWidth - 30) / width)); resize();
        area.scrollTo({ left: 0, top: 0 }); }, 'Fit graph to width'),fullscreenButton);
    const panel = make('div', 'graph-selection');
    function showNodeDetail(item) {
      panel.replaceChildren(make('h3', '', `${item.kind}: ${item.label}`),
        make('p', 'description', item.detail || item.id));
      panel.append(make('p', 'description', `Graph node: ${item.id}`));
    }
    const summary = make('p', 'description',
      `${graph.nodes.length} nodes · ${graph.edges.length} dependencies` +
      (graph.mode === 'lineage' ? ` · ${(graph.fields || []).length} canonical attributes` : ' · execution order'));
    container.append(toolbar, summary, area, panel);
    if (selectedField) {
      panel.replaceChildren(make('h3', '', `Attribute: ${selectedField.attribute}`),
        make('p', 'description', `Type: ${selectedField.type} · Requirements: ${selectedField.requirement_ids.join(', ') || 'not linked'}`));
      selectedRules.forEach(rule => {
        const block = make('section', 'graph-rule');
        block.append(make('strong', '', `Branch ${rule.branch + 1} · ${rule.derivation}`),
          make('p', 'graph-expression', rule.summary));
        const direct = rule.direct_columns.map(col => `${col.alias_column} → ${col.physical_column} [${col.source_id}]`);
        const context = rule.context_columns.map(col => `${col.alias_column} → ${col.physical_column} [${col.source_id}]`);
        block.append(make('p', 'description', `Direct sources: ${direct.join(', ') || 'constant / no source columns'}`));
        if (context.length) block.append(make('p', 'description', `Join/filter context: ${context.join(', ')}`));
        panel.append(block);
      });
    } else if (graph.mode === 'dependencies') {
      panel.append(make('h3', '', 'Execution order'));
      const list = make('ol', 'graph-execution-order');
      for (const key of graph.execution_order || []) list.append(make('li', '', key));
      panel.append(list);
    } else panel.append(make('p', 'description', 'Select a canonical attribute above to highlight its physical source and transformation path. Select any graph node to inspect it.'));
    // Initial render adapts to viewport without requiring a graph library or CDN.
    const available = area.clientWidth || 650;
    if (width > available * 1.4) scale = Math.max(0.55, available / (width * 0.85));
    resize();
  }

  // Canonical ERD: a read-only projection of model entities and declared
  // relationships. Never infer business relationships from physical SQL joins.
  function renderERD(container, graph, { onEdit, layoutKey = '' } = {}) {
    container.replaceChildren();
    const entities = graph.entities || [];
    const relationships = graph.relationships || [];
    if (!entities.length) {
      container.append(make('p', 'description', 'Define a canonical entity in Model to see its ERD.'));
      return;
    }
    const top = make('div', 'erd-intro');
    top.append(make('h3', '', 'Canonical entity-relationship diagram'),
      make('p', 'description', 'The designed business model: entities, logical keys, attributes and declared relationships. Physical joins and transformations belong to Source-to-target lineage.'));
    container.append(top);
    const toolbar = make('div', 'graph-zoom');
    const info = make('div', 'erd-summary', `${entities.filter(e => !e.external).length} canonical entities · ${relationships.length} declared relationships`);
    const area = make('div', 'graph-scroll erd-scroll');
    const canvas = make('div', 'graph-canvas');
    const colCount = entities.length <= 1 ? 1 : entities.length <= 5 ? 2 : 3;
    const columnWidth = 300, columnGap = 105, headerHeight = 75, fieldHeight = 34;
    const rowGap = 95, pagePadding = 34;
    const cards = entities.map(entity => ({
      ...entity, w:columnWidth,
      h:entity.external ? 105 : headerHeight + entity.attributes.length * fieldHeight + 32
    }));
    const positions = new Map();
    let y = pagePadding;
    for (let i = 0; i < cards.length; i += colCount) {
      const batch = cards.slice(i, i + colCount);
      batch.forEach((card, c) => positions.set(card.id, {
        x:pagePadding + c * (columnWidth + columnGap), y, w:card.w, h:card.h
      }));
      y += Math.max(...batch.map(c => c.h)) + rowGap;
    }
    const width = pagePadding * 2 + Math.min(colCount, cards.length) * columnWidth + (Math.min(colCount, cards.length) - 1) * columnGap;
    const height = y - rowGap + pagePadding;
    loadPositions(layoutKey,positions,width,height);
    const svg = node('svg', {width, height, viewBox:`0 0 ${width} ${height}`,
      role:'img', 'aria-label':'Canonical entity relationship diagram'});
    const defs = node('defs');
    const arrow = node('marker', {id:'dpc-erd-arrow',markerWidth:9,markerHeight:9,refX:8,refY:4,orient:'auto',markerUnits:'strokeWidth'});
    arrow.append(node('path',{d:'M0 1 L8 4 L0 7',stroke:'#557a99',fill:'none','stroke-width':1.4}));
    defs.append(arrow);svg.append(defs);
    const edgeLayer=node('g');
    const linkPaths = [];
    const entityById = new Map(cards.map(card => [card.id, card]));
    const fieldY = (card, attribute) => {
      const index = card.attributes.findIndex(item => item.name === attribute);
      return index >= 0 ? headerHeight + index * fieldHeight + fieldHeight/2 : 40;
    };
    const relationshipGeometry = relationship => {
      const a=positions.get(relationship.from),b=positions.get(relationship.to);
      if (!a || !b) return null;
      const ea=entityById.get(relationship.from),eb=entityById.get(relationship.to);
      const leftToRight=a.x<b.x,same=a.x===b.x;
      const sx=same?a.x+a.w:(leftToRight?a.x+a.w:a.x);
      const tx=same?b.x+b.w:(leftToRight?b.x:b.x+b.w);
      const routeX=Math.max(a.x+a.w,b.x+b.w)+55;
      const sy=a.y+fieldY(ea,relationship.attribute);
      const ty=b.y+fieldY(eb,relationship.related_attribute);
      const curve=same?`M${sx} ${sy} C${routeX} ${sy},${routeX} ${ty},${tx} ${ty}`:
        `M${sx} ${sy} C${sx+(leftToRight?55:-55)} ${sy},${tx+(leftToRight?-55:55)} ${ty},${tx} ${ty}`;
      return {curve,centerX:same?routeX:(sx+tx)/2,centerY:(sy+ty)/2-12};
    };
    for (const relationship of relationships) {
      const geometry=relationshipGeometry(relationship);
      if (!geometry) continue;
      const ea=entityById.get(relationship.from),eb=entityById.get(relationship.to);
      const path=node('path',{d:geometry.curve,'class':`erd-edge${relationship.status !== 'resolved'?' unresolved':''}`,
        'marker-end':'url(#dpc-erd-arrow)'});
      const label={many_to_one:'many → one',one_to_many:'one → many',one_to_one:'one → one'}[relationship.cardinality] || relationship.cardinality;
      path.append(node('title',{},`${ea.name}.${relationship.attribute} → ${eb.name}.${relationship.related_attribute || '(entity)'} · ${label} · ${relationship.status}`));
      edgeLayer.append(path);
      const edgeLabel = node('g', {class:'erd-edge-label'});
      const cardinality = {many_to_one:'N : 1',one_to_many:'1 : N',one_to_one:'1 : 1'}[relationship.cardinality] || '';
      const labelRect=node('rect',{x:geometry.centerX-22,y:geometry.centerY-12,width:44,height:22,rx:10,class:'erd-edge-label-bg'});
      const labelText=node('text',{x:geometry.centerX,y:geometry.centerY+3,'text-anchor':'middle',class:'erd-cardinality'},cardinality);
      edgeLabel.append(labelRect,labelText);
      edgeLayer.append(edgeLabel);
      linkPaths.push({relationship,path,label:edgeLabel,labelRect,labelText});
    }
    function updateLinks() {
      linkPaths.forEach(({relationship,path,labelRect,labelText})=>{
        const geometry=relationshipGeometry(relationship);
        if (!geometry) return;
        path.setAttribute('d',geometry.curve);
        labelRect.setAttribute('x',geometry.centerX-22);
        labelRect.setAttribute('y',geometry.centerY-12);
        labelText.setAttribute('x',geometry.centerX);
        labelText.setAttribute('y',geometry.centerY+3);
      });
    }
    svg.append(edgeLayer);
    const cardLayer=node('g');
    const cardGroups=[];
    const fieldGroups=[];
    const detail=make('section','graph-selection erd-detail');
    let selection=null;
    function showDetail(entity, attribute) {
      selection={entity:entity.id,attribute};
      detail.replaceChildren();
      if (attribute) {
        const field=entity.attributes.find(item=>item.name===attribute);
        detail.append(make('h3','',`${entity.name}.${field.name}`),
          make('p','description',`Type: ${field.type} · ${field.nullable?'Nullable':'Required'} · ${field.key?'Unique key attribute':'Non-key attribute'}`),
          make('p','description',`Requirements: ${field.requirement_ids.join(', ') || 'Not linked'}`));
      } else {
        detail.append(make('h3','',entity.name),
          make('p','description',entity.external?'External or unresolved entity. Define it in another data product and use project scope to resolve it.':
            `Product: ${entity.product} · Published target: ${entity.target}`),
          make('p','description',`Record grain: ${entity.grain || 'Not available'}`),
          make('p','description',`Logical key: ${entity.key_attributes.join(', ') || 'Not defined'}`));
      }
      const related=relationships.filter(r =>
        (r.from===entity.id && (!attribute || r.attribute===attribute)) ||
        (r.to===entity.id && (!attribute || r.related_attribute===attribute)));
      if (related.length) {
        detail.append(make('h4','', 'Declared relationships'));
        related.forEach(rel=>{
          const a=entityById.get(rel.from),b=entityById.get(rel.to);
          detail.append(make('p','description',`${a.name}.${rel.attribute} → ${b.name}.${rel.related_attribute || '(entity)'} · ${rel.cardinality.replaceAll('_',' ')} · ${rel.status}`));
        });
      }
      cardGroups.forEach(({entity:record,group})=>group.classList.toggle('erd-muted',record.id!==entity.id && !related.some(r=>r.from===record.id||r.to===record.id)));
      fieldGroups.forEach(({entity:record,field,group})=>{
        group.classList.toggle('erd-selected-field',record.id===entity.id && field===attribute);
      });
      linkPaths.forEach(({relationship,path,label})=>{const muted=!related.includes(relationship);path.classList.toggle('erd-muted',muted);label.classList.toggle('erd-muted',muted);});
    }
    for (const entity of cards) {
      const pos=positions.get(entity.id);
      const group=node('g',{class:`erd-card${entity.external?' external':''}`,transform:`translate(${pos.x} ${pos.y})`,role:'group','aria-label':`Entity ${entity.name}`});
      group.append(node('rect',{width:pos.w,height:pos.h,rx:10,class:'erd-card-bg'}));
      group.append(node('rect',{width:pos.w,height:headerHeight-12,rx:10,class:'erd-card-header'}));
      group.append(node('text',{x:16,y:24,class:'erd-card-title'},entity.name));
      group.append(node('text',{x:16,y:44,class:'erd-card-subtitle'},entity.external?'External / unresolved entity':
        truncate(entity.product+' · '+entity.target,40)));
      const title=node('title',{},`${entity.name}\n${entity.grain || 'External entity'}`);group.append(title);
      const hit=node('rect',{x:0,y:0,width:pos.w,height:headerHeight-12,fill:'transparent',class:'erd-hit',tabindex:0,role:'button',
        'aria-label':`Inspect ${entity.name}; drag or use arrow keys to move`,'title':'Drag to move; use arrow keys to reposition'});
      const dragged=enableDrag(hit,group,pos,svg,()=>({width,height}),()=>{updateLinks();resize();},
        ()=>savePositions(layoutKey,positions));
      hit.addEventListener('click',()=>{if(!dragged())showDetail(entity,null);});
      hit.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();showDetail(entity,null);}});
      group.append(hit);
      entity.attributes.forEach((field,i)=>{
        const gy=headerHeight+i*fieldHeight;
        const row=node('g',{class:'erd-field',role:'button',tabindex:0,'aria-label':`${entity.name}.${field.name}: ${field.type}`});
        row.append(node('rect',{x:5,y:gy,width:pos.w-10,height:fieldHeight,rx:3,class:'erd-field-bg'}));
        if(field.key)row.append(node('text',{x:13,y:gy+22,class:'erd-key-icon'},'PK'));
        row.append(node('text',{x:field.key?48:15,y:gy+22,class:'erd-attribute-name'},truncate(field.name,24)));
        row.append(node('text',{x:pos.w-13,y:gy+22,'text-anchor':'end',class:'erd-attribute-type'},field.type+(field.nullable?'?':'')));
        row.append(node('title',{},`${field.name} · ${field.type}${field.nullable?' (nullable)':' (required)'}${field.key?' · logical key':''}`));
        row.addEventListener('click',()=>showDetail(entity,field.name));
        row.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showDetail(entity,field.name);}});
        group.append(row);fieldGroups.push({entity,field:field.name,group:row});
      });
      if(!entity.external)group.append(node('text',{x:14,y:pos.h-9,class:'erd-card-footer'},
        truncate('Grain: '+entity.grain,47)));
      cardLayer.append(group);cardGroups.push({entity,group});
    }
    svg.append(cardLayer);canvas.append(svg);area.append(canvas);
    let scale=1;
    function resize(){svg.setAttribute('width',Math.round(width*scale));svg.setAttribute('height',Math.round(height*scale));}
    function zoom(amount){scale=Math.max(.3,Math.min(1.75,scale*amount));resize();}
    function button(text,action,label){const b=make('button','btn secondary small',text);b.type='button';b.setAttribute('aria-label',label);b.addEventListener('click',action);return b;}
    const fullscreenButton=button('Full screen',()=>toggleFullscreen(),'Enter full screen');
    let previousFullscreenScale=null;
    function updateFullscreenButton(){
      const active=document.fullscreenElement===container;
      fullscreenButton.textContent=active?'Exit full screen':'Full screen';
      fullscreenButton.setAttribute('aria-label',active?'Exit full screen':'Enter full screen');
      fullscreenButton.title=active?'Exit full screen':'Enter full screen';
      if(!active&&previousFullscreenScale!==null){
        scale=previousFullscreenScale;previousFullscreenScale=null;resize();
      }
    }
    async function toggleFullscreen(){
      try {
        if(document.fullscreenElement===container)await document.exitFullscreen();
        else {
          previousFullscreenScale=scale;
          await container.requestFullscreen();
          if(document.fullscreenElement===container){
            scale=Math.max(.3,Math.min(1.75,(area.clientWidth-25)/width));
            resize();area.scrollTo({left:0,top:0});
          }
        }
      } catch(error) {
        if(document.fullscreenElement!==container)previousFullscreenScale=null;
        detail.append(make('p','erd-warning',`Full-screen mode unavailable: ${error.message}`));
      }
    }
    fullscreenButton.disabled=typeof container.requestFullscreen!=='function'||typeof document.exitFullscreen!=='function';
    container.addEventListener('fullscreenchange',updateFullscreenButton);
    toolbar.append(button('−',()=>zoom(.8),'Zoom out'),button('+',()=>zoom(1.25),'Zoom in'),
      button('Fit',()=>{scale=Math.min(1,Math.max(.3,(area.clientWidth-25)/width));resize();area.scrollTo({left:0,top:0});},'Fit ERD to width'),fullscreenButton);
    if(onEdit)toolbar.append(button('Edit canonical model',onEdit,'Edit model attributes and relationships'));
    container.append(toolbar,info,area,detail);
    detail.append(make('p','description','Select an entity or attribute to inspect its key, grain, requirements and declared relationships. Edit relationships in the Model form.'));
    if(!relationships.length)detail.append(make('p','erd-hint','No conceptual relationships declared. Add model.relationships to connect entities; physical SQL joins do not define an ERD relationship.'));
    (graph.warnings||[]).forEach(w=>detail.append(make('p','erd-warning',w)));
    const available=area.clientWidth||700;
    if(width>available*1.3)scale=Math.max(.45,available/width);
    resize();
  }
  return { renderGraph, renderERD };
})();
