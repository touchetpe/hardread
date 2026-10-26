// Build a compact display-name/dex table from Showdown's compiled data.
const path = require('path');
const root = process.argv[2];
const d = (f) => require(path.join(root, 'dist/data', f));
const {Pokedex} = d('pokedex.js');
const {Moves} = d('moves.js');
const {Items} = d('items.js');
const {Abilities} = d('abilities.js');
const out = {species: {}, moves: {}, items: {}, abilities: {}, types: {}};
for (const [id, s] of Object.entries(Pokedex)) {
  const b = s.baseStats; if (!b || !s.types) continue;
  out.species[id] = {n: s.name, t: s.types, a: Object.values(s.abilities || {}), b: [b.hp, b.atk, b.def, b.spa, b.spd, b.spe]};
}
for (const [id, m] of Object.entries(Moves)) {
  out.moves[id] = {n: m.name, t: m.type, c: (m.category === 'Status' ? 'X' : m.category[0]), p: m.basePower || 0, r: m.priority || 0};
}
for (const [id, it] of Object.entries(Items)) out.items[id] = it.name;
for (const [id, a] of Object.entries(Abilities)) out.abilities[id] = a.name;
for (const t of ['Normal','Fire','Water','Electric','Grass','Ice','Fighting','Poison','Ground','Flying','Psychic','Bug','Rock','Ghost','Dragon','Dark','Steel','Fairy','Stellar']) out.types[t.toLowerCase()] = t;
process.stdout.write(JSON.stringify(out));
