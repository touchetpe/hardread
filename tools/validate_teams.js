#!/usr/bin/env node
// Validate Showdown export-format team files against a format, using the pinned
// Showdown simulator's own TeamValidator. Writes one JSON line per team.
// Usage: node validate_teams.js <showdown_runtime_dir> <format> <out.jsonl> <team files or dirs...>
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const [runtime, format, outPath, ...inputs] = process.argv.slice(2);
if (!runtime || !format || !outPath || !inputs.length) {
  console.error('usage: validate_teams.js <showdown_runtime_dir> <format> <out.jsonl> <files/dirs...>');
  process.exit(2);
}
const {Teams, TeamValidator} = require(path.join(path.resolve(runtime), 'dist', 'sim'));
const validator = TeamValidator.get(format);

const files = [];
for (const inp of inputs) {
  const st = fs.statSync(inp);
  if (st.isDirectory()) {
    for (const f of fs.readdirSync(inp)) {
      if (f.endsWith('_team') || f.endsWith('.txt')) files.push(path.join(inp, f));
    }
  } else {
    files.push(inp);
  }
}
files.sort();

const out = fs.openSync(outPath, 'w');
let valid = 0;
const reasons = new Map();
for (const file of files) {
  const text = fs.readFileSync(file, 'utf8');
  let sets = null;
  let problems = null;
  try {
    sets = Teams.import(text);
    problems = sets && sets.length ? validator.validateTeam(sets) : ['empty or unparseable team'];
  } catch (e) {
    problems = ['exception: ' + e.message];
  }
  const ok = !problems || problems.length === 0;
  if (ok) valid++;
  for (const p of problems || []) {
    // Collapse Pokemon-specific text so reasons can be counted.
    const key = p.replace(/^[^'’]*'s /, '').slice(0, 80);
    reasons.set(key, (reasons.get(key) || 0) + 1);
  }
  const packed = sets ? Teams.pack(sets) : null;
  fs.writeSync(out, JSON.stringify({
    file, valid: ok,
    problems: problems || [],
    species: sets ? sets.map(s => s.species) : [],
    packed_sha256: packed ? crypto.createHash('sha256').update(packed).digest('hex') : null,
  }) + '\n');
}
fs.closeSync(out);
const top = [...reasons.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15);
console.log(JSON.stringify({format, total: files.length, valid, invalid: files.length - valid, top_reasons: top}, null, 1));
