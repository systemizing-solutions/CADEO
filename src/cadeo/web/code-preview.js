/* Read-only code presentation. No SQL or YAML business logic is defined here. */
'use strict';
(function(root) {
  const escape = value => String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  function sql(text) {
    const tokens = /(--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|`[^`]*`|\b[A-Za-z_][\w$]*\b|\b\d+(?:\.\d+)?\b)/g;
    const keywords = new Set(('SELECT FROM WHERE WITH AS JOIN LEFT RIGHT FULL INNER OUTER ON AND OR NOT NULL IS IN EXISTS ' +
      'CASE WHEN THEN ELSE END COALESCE CONCAT UNION ALL DISTINCT GROUP BY HAVING ORDER LIMIT OFFSET ' +
      'CREATE OR REPLACE VIEW TABLE DROP INSERT INTO VALUES UPDATE DELETE CAST OVER PARTITION ROW_NUMBER ' +
      'TRUE FALSE BETWEEN LIKE CURRENT_TIMESTAMP COUNT SUM MIN MAX PRIMARY KEY').split(' '));
    let position=0, result='', match;
    while ((match=tokens.exec(text)) !== null) {
      result+=escape(text.slice(position,match.index));
      const value=match[0], upper=value.toUpperCase();
      const kind=value.startsWith('--')||value.startsWith('/*')?'comment':
        value.startsWith("'")?'string':
        value.startsWith('"')||value.startsWith('`')?'identifier':
        /^\d/.test(value)?'number':keywords.has(upper)?'keyword':null;
      result+=kind?`<span class="code-${kind}">${escape(value)}</span>`:escape(value);
      position=tokens.lastIndex;
    }
    return result+escape(text.slice(position));
  }
  function yaml(text, badLines=[]) {
    const bad = new Set(badLines);
    return text.split('\n').map((line,index) => {
      const match=line.match(/^(\s*(?:-\s+)?)([\w.-]+)(\s*:\s*)(.*)$/);
      let markup;
      if (line.trimStart().startsWith('#'))markup=`<span class="code-comment">${escape(line)}</span>`;
      else if(match) {
        let suffix=escape(match[4]);
        const raw=match[4].trim();
        if (/^(true|false|null|~|-?\d+(?:\.\d+)?)$/i.test(raw))suffix=`<span class="code-number">${escape(match[4])}</span>`;
        else if (raw.startsWith('#'))suffix=`<span class="code-comment">${escape(match[4])}</span>`;
        else if (raw) suffix=`<span class="code-string">${escape(match[4])}</span>`;
        markup=`${escape(match[1])}<span class="code-key">${escape(match[2])}</span>${escape(match[3])}${suffix}`;
      } else markup=escape(line)||' ';
      return `<span class="code-line${bad.has(index+1)?' code-line-error':''}" data-line="${index+1}">${markup}</span>`;
    }).join('\n')+'\n';
  }
  root.DPCCode={escape,sql,yaml};
})(typeof window === 'undefined' ? globalThis : window);
