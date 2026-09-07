// Parse local scripts without running application code or contacting services.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
let checked = 0, failed = 0;
function check(source, filename) {
  try { new vm.Script(source, {filename}); checked++; }
  catch (error) { console.error(error.stack); failed++; }
}
for (const name of fs.readdirSync(path.join(root, 'static'))) {
  if (name.endsWith('.js')) check(fs.readFileSync(path.join(root, 'static', name), 'utf8'), name);
}
for (const name of fs.readdirSync(path.join(root, 'templates'))) {
  if (!name.endsWith('.html')) continue;
  const html = fs.readFileSync(path.join(root, 'templates', name), 'utf8');
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    if (/\bsrc\s*=/.test(match[1]) || /type\s*=\s*["']application\/(?:ld\+)?json/.test(match[1])) continue;
    // Values and control tags are rendered by Jinja at runtime. Keep all JS
    // branches here so syntax errors in rarely visited pages remain visible.
    const source = match[2].replace(/\{#[\s\S]*?#\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, 'null').replace(/\{%[\s\S]*?%\}/g, '');
    check(source, name + ':' + (html.slice(0, match.index).split('\n').length));
  }
}
console.log(`Parsed ${checked} scripts; ${failed} failed.`);
process.exitCode = failed ? 1 : 0;
