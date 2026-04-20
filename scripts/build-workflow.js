#!/usr/bin/env node
// Build script: bundle router/*.js into a single jsCode string and inject it
// into v3-events.template.json, emitting v3-events.json.
//
// Usage:
//   node scripts/build-workflow.js
//
// The bundling is deliberately primitive (no esbuild): strip ES module syntax,
// concatenate files, append an adapter that calls routeEvent() with n8n's
// $input. Keeps router.js authoring normal (testable via `node --test`).

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');

const SOURCES = [
  'router/ci-diagnose.js',
  'router/router.js',
];

const TEMPLATE = 'charts/n8n-workflows/v3.1/v3-events.template.json';
const OUTPUT   = 'charts/n8n-workflows/v3.1/v3-events.json';
const ROUTER_PLACEHOLDER = '{{ROUTER_JS}}';
const ROUTER_NODE_NAME = 'Router';

const ENTRY_ADAPTER = `
// ─── n8n entry adapter (appended by build-workflow.js) ─────────────────────
const input = $input.first().json;
const body = input.body && typeof input.body === 'object' ? input.body : input;
const meta = input.metadata || body.metadata || {};
if (!body.metadata && meta) body.metadata = meta;
try {
  const result = routeEvent(body);
  return [{ json: { ...result, _routedAt: new Date().toISOString(), _input: body } }];
} catch (err) {
  return [{ json: { action: 'escalate', reason: 'router_exception', error: String(err && err.message || err), _input: body } }];
}
`;

function stripModuleSyntax(src) {
  return src
    .replace(/^\s*import[^;]*?;?\s*$/gm, '')              // drop import lines
    .replace(/^\s*export\s+(const|let|var|function)\s+/gm, '$1 ') // strip `export ` keyword
    .replace(/^\s*export\s+\{[^}]*\}\s*;?\s*$/gm, '');    // drop re-export blocks
}

function bundleRouter() {
  const chunks = SOURCES.map((rel) => {
    const full = resolve(repoRoot, rel);
    const raw = readFileSync(full, 'utf8');
    return `// ── ${rel} ─────────────────────────────────────────────\n${stripModuleSyntax(raw)}`;
  });
  return [...chunks, ENTRY_ADAPTER].join('\n');
}

function loadTemplate() {
  const tpl = JSON.parse(readFileSync(resolve(repoRoot, TEMPLATE), 'utf8'));
  const node = tpl.nodes.find(n => n.name === ROUTER_NODE_NAME);
  if (!node) throw new Error(`template has no node named "${ROUTER_NODE_NAME}"`);
  if (node.parameters.jsCode !== ROUTER_PLACEHOLDER) {
    throw new Error(`Router node jsCode must be exactly "${ROUTER_PLACEHOLDER}", got: ${node.parameters.jsCode?.slice(0, 40)}...`);
  }
  return { tpl, node };
}

function main() {
  const { tpl, node } = loadTemplate();
  const bundle = bundleRouter();
  node.parameters.jsCode = bundle; // set on the parsed object — JSON.stringify handles escaping
  const out = JSON.stringify(tpl, null, 2);
  JSON.parse(out); // sanity
  writeFileSync(resolve(repoRoot, OUTPUT), out + '\n');
  const lines = bundle.split('\n').length;
  console.log(`✓ built ${OUTPUT}`);
  console.log(`  nodes: ${tpl.nodes.length}, router bundle: ${bundle.length} bytes / ${lines} lines`);
}

main();
