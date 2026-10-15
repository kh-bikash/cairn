// Generates docs/architecture.svg: a hand-drawn style overview of how Cairn works.
// Usage: npm i --no-save roughjs@4.6.6 && node docs/diagram.mjs
import { createRequire } from 'node:module'
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const rough = require('roughjs/bundled/rough.cjs.js')
const gen = rough.generator()
const here = dirname(fileURLToPath(import.meta.url))

const W = 1280
const H = 960
const INK = '#2b2b2b'
const FONT = "'Virgil', 'Segoe Print', 'Comic Sans MS', 'Chalkboard SE', cursive"
const parts = []
let seed = 7

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function draw(drawable) {
  for (const p of gen.toPaths(drawable)) {
    parts.push(`<path d="${p.d}" stroke="${p.stroke}" stroke-width="${p.strokeWidth}" fill="${p.fill ?? 'none'}" stroke-linecap="round" stroke-linejoin="round"/>`)
  }
}

function text(x, y, s, { size = 16, weight = 400, anchor = 'middle', color = INK } = {}) {
  parts.push(`<text x="${x}" y="${y}" font-family="${FONT}" font-size="${size}" font-weight="${weight}" fill="${color}" text-anchor="${anchor}">${esc(s)}</text>`)
}

function box(x, y, w, h, title, lines = [], fill = '#fff7d6', opts = {}) {
  draw(gen.rectangle(x, y, w, h, { seed: seed++, roughness: 1.4, bowing: 1.2, stroke: INK, strokeWidth: 1.6, fill, fillStyle: 'hachure', hachureGap: 7, fillWeight: 0.9, ...opts }))
  text(x + w / 2, y + 28, title, { size: 18, weight: 700 })
  lines.forEach((l, i) => text(x + w / 2, y + 52 + i * 21, l, { size: 14 }))
}

function arrow(x1, y1, x2, y2, label, { dx = 0, dy = -8 } = {}) {
  draw(gen.line(x1, y1, x2, y2, { seed: seed++, roughness: 1.2, stroke: INK, strokeWidth: 1.6 }))
  const a = Math.atan2(y2 - y1, x2 - x1)
  const len = 12
  for (const side of [-1, 1]) {
    const ang = a + Math.PI + side * 0.45
    draw(gen.line(x2, y2, x2 + len * Math.cos(ang), y2 + len * Math.sin(ang), { seed: seed++, roughness: 0.8, stroke: INK, strokeWidth: 1.6 }))
  }
  if (label) text((x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy, label, { size: 13, color: '#555' })
}

function node(cx, cy, r, label, fill) {
  draw(gen.circle(cx, cy, r * 2, { seed: seed++, roughness: 1.3, stroke: INK, strokeWidth: 1.4, fill, fillStyle: 'solid' }))
  text(cx, cy + r + 18, label, { size: 14, weight: 700 })
}

function edge(x1, y1, x2, y2, label, dy = -5, dx = 0, anchor = 'middle') {
  draw(gen.line(x1, y1, x2, y2, { seed: seed++, roughness: 1.0, stroke: '#777', strokeWidth: 1.2 }))
  if (label) text((x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy, label, { size: 11.5, color: '#666', anchor })
}

// ---------------------------------------------------------------- title
text(W / 2, 44, 'Cairn: the company brain that remembers why', { size: 28, weight: 700 })
text(W / 2, 72, 'every answer is a path through a FalkorDB graph, and every reason links to its source', { size: 15, color: '#555' })

// ---------------------------------------------------------------- sources
const srcY = 100
const sources = [
  ['GitHub', ['PRs, issues, reviews', 'manifests, CODEOWNERS']],
  ['Slack export', ['threads and replies']],
  ['Jira / Linear CSV', ['tickets, comments']],
  ['Docs (OKF)', ['ADRs, RFCs, postmortems', 'runbooks, meeting notes']],
  ['Org chart CSV', ['teams, roles, leavers']],
]
const sw = 222
sources.forEach(([t, l], i) => box(30 + i * (sw + 22), srcY, sw, 104, t, l, '#d8ecff'))

// ---------------------------------------------------------------- importers
box(250, 262, 780, 74, 'Importers: structure first, no AI', ['people resolved across tools · systems · dependencies · who did what, when'], '#e7f7e1')
sources.forEach((_, i) => arrow(30 + i * (sw + 22) + sw / 2, srcY + 106, 640 + (i - 2) * 120, 260))

// ---------------------------------------------------------------- graph
const gx = 250
const gy = 384
const gw = 780
const gh = 300
box(gx, gy, gw, gh, 'FalkorDB: one graph per company', [], '#fff1e6', { hachureGap: 9 })
const P = { person: [330, 480], team: [330, 600], system: [560, 600], decision: [640, 470], premise: [830, 470], source: [930, 600], answer: [790, 610] }
edge(...P.person, ...P.team, 'MEMBER_OF', 6, 8, 'start')
edge(...P.person, ...P.system, 'KNOWS_ABOUT', -10)
edge(...P.person, ...P.decision, 'DECIDED', -8)
edge(...P.decision, ...P.premise, 'BECAUSE')
edge(...P.premise, ...P.source, 'EVIDENCED_BY', 0, 12, 'start')
text(893, 550, '+ quote', { size: 11.5, color: '#666', anchor: 'start' })
edge(...P.decision, ...P.system, 'AFFECTS', -4)
edge(...P.source, ...P.system, 'TOUCHES / MENTIONS', -8, -60)
edge(...P.answer, ...P.decision, 'USED', 10)
node(...P.person, 22, 'Person', '#ffb38a')
node(...P.team, 18, 'Team', '#c9b6ff')
node(...P.system, 22, 'System', '#8fe0d4')
node(...P.decision, 24, 'Decision', '#9be29b')
node(...P.premise, 17, 'Reason', '#d7f28a')
node(...P.source, 18, 'Source', '#a9b4ff')
node(...P.answer, 15, 'Answer', '#e6e6e6')
text(960, 452, 'CHALLENGES', { size: 12, color: '#c0392b' })
draw(gen.curve([[946, 592], [1008, 545], [985, 478], [848, 466]], { seed: seed++, roughness: 1.2, stroke: '#c0392b', strokeWidth: 1.4, strokeLineDash: [6, 5] }))
arrow(640, 338, 640, 382, 'MERGE (idempotent)', { dx: 90, dy: 4 })

// ---------------------------------------------------------------- AI extraction
box(30, 400, 196, 270, 'AI extraction', ['triage (fast model)', 'decisions + reasons', 'quotes verified', 'against the source', 'merged across sources', 'drift: 2 models agree', 'or it is dropped'], '#ffe0ec')
arrow(228, 520, 248, 520)

// ---------------------------------------------------------------- tools
box(1054, 384, 196, 300, 'Graph tools', ['why', 'who_knows', 'impact (DEPENDS_ON*)', 'bus_factor (betweenness)', 'teams (label propagation)', 'connect (shortest path)', 'change_impact', 'incident_memory', 'handoff_plan'], '#e9f2ff')
arrow(1032, 534, 1052, 534)

// ---------------------------------------------------------------- consumers
const cy = 760
box(70, cy, 330, 140, 'Agent', ['retrieval first, then the model', 'citations or it stays silent', 'Groq / Gemini, auto failover', 'graph-only answers without AI'], '#f2f2f2')
box(475, cy, 330, 140, 'Interfaces', ['web app: ask, decisions, people,', 'systems, workflows, approvals', 'MCP server: Claude Desktop, Cursor', 'CLI: fetch, import, extract, eval'], '#f2f2f2')
box(880, cy, 330, 140, 'Actions (human approves)', ['issue routing comments', 'change heads-up issues', 'handoff docs, OKF wiki PRs', 'sandbox repo only, no @-pings'], '#fff7d6')
arrow(1150, 686, 1045, 758, 'propose', { dx: 30 })
arrow(640, 686, 640, 758, 'read', { dx: 24 })
arrow(330, 686, 235, 758, 'traverse', { dx: -36 })
arrow(402, 830, 473, 830)
arrow(807, 830, 878, 830)

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
<rect width="${W}" height="${H}" fill="#fffdf8"/>
${parts.join('\n')}
</svg>
`
const out = process.argv[2] ?? join(here, 'architecture.svg')
writeFileSync(out, svg)
console.log(`wrote ${out} (${parts.length} shapes)`)
