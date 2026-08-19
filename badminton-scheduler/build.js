/**
 * Bundles the app into dist/badminton-scheduler.html — one self-contained file
 * that runs from a file:// path, a USB stick or any static host, no server needed.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

/** Strip module syntax so the files can be concatenated into one script. */
function flatten(source) {
  return source
    .replace(/^import\s[\s\S]*?from\s+'[^']*';\s*$/gm, '')
    .replace(/^import\s+'[^']*';\s*$/gm, '')
    .replace(/^export\s+\*\s+from\s+'[^']*';\s*$/gm, '')
    .replace(/^export\s+(?=(async\s+)?function|const|class)/gm, '');
}

const exampleConfig = read('examples/monash-open-2025.example.json');

const app = flatten(read('web/app.js'))
  .replace(
    /async function loadExample\(\) \{[\s\S]*?\n\}/,
    'async function loadExample() {\n  return JSON.parse(JSON.stringify(EXAMPLE_CONFIG));\n}',
  );

const bundle = [
  `const EXAMPLE_CONFIG = ${exampleConfig.trim()};`,
  flatten(read('src/config.js')),
  flatten(read('src/draw.js')),
  flatten(read('src/scheduler.js')),
  flatten(read('src/report.js')),
  flatten(read('src/import.js')),
  flatten(read('src/index.js')),
  app,
].join('\n\n');

const html = read('web/index.html')
  .replace('<link rel="stylesheet" href="./styles.css">', `<style>\n${read('web/styles.css')}\n</style>`)
  .replace('<script type="module" src="./app.js"></script>', `<script type="module">\n${bundle}\n</script>`);

fs.mkdirSync(path.join(root, 'dist'), { recursive: true });
const out = path.join(root, 'dist', 'badminton-scheduler.html');
fs.writeFileSync(out, html);
console.log(`Wrote ${out} (${(html.length / 1024).toFixed(0)} KB)`);

// Same page without the document shell, for hosts that supply their own
// <!doctype>/<head>/<body> wrapper.
const embedded = html
  .replace(/<!doctype html>\s*/i, '')
  .replace(/<\/?html[^>]*>\s*/gi, '')
  .replace(/<\/?head>\s*/gi, '')
  .replace(/<\/?body>\s*/gi, '')
  .replace(/<meta[^>]*>\s*/gi, '');
const embedOut = path.join(root, 'dist', 'badminton-scheduler.embed.html');
fs.writeFileSync(embedOut, embedded);
console.log(`Wrote ${embedOut} (${(embedded.length / 1024).toFixed(0)} KB)`);
