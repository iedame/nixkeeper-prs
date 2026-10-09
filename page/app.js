// The proof-of-concept page: nixpkgs' open PRs from the digest (the data
// branch's data/prs.json), in views (what to act on) and filters, a plain
// table, more rows as you scroll. Nothing is sent anywhere.

const BASE = 'https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/';
const DATA = `${BASE}prs.json`;
const ISSUE_URL = 'https://github.com/NixOS/nixpkgs/issues/';
const PR_URL = 'https://github.com/NixOS/nixpkgs/pull/';
// The same PR on ghdiff.com, a fast review site (github.com's path on its
// host; it asks for your own GitHub token, kept in your browser). Not ours:
// a link, not a dependency.
const GHDIFF_URL = 'https://ghdiff.com/NixOS/nixpkgs/pull/';
const STEP = 200;

// nixpkgs' CI labels: what it says about a PR, more reliably than the
// API's fields (GitHub's merge state is UNKNOWN for most PRs).
const has = (p, label) => p.labels.includes(label);
// nixpkgs' CI's verdict, or the digest's own for r-ryantm's PRs.
const mergeBotEligible = (p) => has(p, '2.status: merge-bot eligible') || Boolean(p.mergeBot);
const conflicts = (p) => has(p, '2.status: merge conflict') || p.mergeable === 'CONFLICTING';
const DAY_MS = 86400e3;
// How many of xs have each key(x).
function countBy(xs, key) {
  const counts = {};
  for (const x of xs) counts[key(x)] = (counts[key(x)] || 0) + 1;
  return counts;
}
const daysSince = (iso) => (Date.now() - new Date(iso)) / DAY_MS;
// How many packages a PR rebuilds, the most of Linux's and Darwin's labels
// ("10.rebuild-linux: 11-100" → 11): null when CI hasn't said.
function rebuilds(p) {
  let most = null;
  for (const l of p.labels) {
    const m = /^10\.rebuild-(?:linux|darwin): (\d+)/.exec(l);
    if (m) most = Math.max(most ?? 0, Number(m[1]));
  }
  return most;
}
// Approvals, by nixpkgs' label ("12.approvals: 3+") or the reviews read.
function approvals(p) {
  const m = p.labels.map((l) => /^12\.approvals: (\d+)/.exec(l)).find(Boolean);
  return Math.max(m ? Number(m[1]) : 0, p.approvedBy.length);
}

// What its diff says (the digest's diffs.py), in words.
const TAGS = {
  strictDeps: 'adds strictDeps',
  structuredAttrs: 'adds __structuredAttrs',
  pyproject: 'pyproject = true',
  finalAttrs: 'finalAttrs',
  updateScript: 'adds an updateScript',
  pythonImportsCheck: 'adds pythonImportsCheck',
  revToTag: 'rev → tag',
  sriHash: 'SRI hash',
  dropWithLib: 'drops with lib',
  byName: 'moves to by-name',
};
const HINTS = {
  rec: 'uses rec',
  withLib: 'uses with lib',
  revNotTag: 'rev, not tag',
  oldHash: 'old-style hash',
  noPythonImportsCheck: 'no pythonImportsCheck',
};

// An update to an older version (the digest's "downgrade"; older digests
// said "backwards" for these and the snapshot ones alike).
const isDowngrade = (p) => p.state === 'downgrade' || p.state === 'backwards';
const STATES = {
  superseded: 'superseded',
  overtaken: 'overtaken',
  downgrade: 'downgrade',
  backwards: 'backwards',
  snapshotToRelease: 'snapshot → release',
  preRelease: 'pre-release suffix',
};

// What to act on, each a test on a PR (groups: the digest's duplicates).
const VIEWS = {
  all: { label: 'All open', test: () => true },
  mergeBot: {
    label: 'Merge it yourself',
    title:
      "Merge-bot eligible (nixpkgs CI's label, or r-ryantm's by-name PRs) with CI green: a maintainer of every package it touches can merge it with the merge bot",
    test: (p) => mergeBotEligible(p) && p.ci === 'SUCCESS' && !conflicts(p),
  },
  needsCommitter: {
    label: 'Needs a committer',
    title:
      "Approved by a package maintainer, CI green, no conflict, no changes requested, and not merge-bot eligible: one committer's merge",
    test: (p) =>
      has(p, '12.approved-by: package-maintainer') &&
      p.ci === 'SUCCESS' &&
      !conflicts(p) &&
      p.review !== 'CHANGES_REQUESTED' &&
      !mergeBotEligible(p),
  },
  security: {
    label: 'Security',
    title:
      "nixpkgs' security severity label, or a CVE named in what it adds (a patch named after one)",
    test: (p) => has(p, '1.severity: security') || p.buckets.includes('cve'),
  },
  firstTime: {
    label: 'First-time, unreviewed',
    title: 'First-time contributions with no review at all, opened over 30 days ago',
    test: (p) =>
      has(p, '12.first-time contribution') &&
      !p.approvedBy.length &&
      !p.review &&
      daysSince(p.created) > 30,
  },
  noReviewers: {
    label: 'No reviewers',
    title: 'No default reviewers: no maintainer was asked to review it',
    test: (p) => has(p, '7.no default reviewers'),
  },
  noRebuild: {
    label: 'No rebuilds',
    title: "Rebuilds nothing on Linux or Darwin (CI says 0): can't break builds",
    test: (p) => rebuilds(p) === 0,
  },
  abandoned: {
    label: 'Likely abandoned',
    title: 'Changes requested and untouched for 90 days, or stale and conflicted: close or adopt',
    test: (p) =>
      (p.review === 'CHANGES_REQUESTED' && daysSince(p.updated) > 90) ||
      (has(p, '2.status: stale') && conflicts(p)),
  },
  superseded: {
    label: 'Superseded',
    title: 'Updates nixpkgs already has (master or the channel moved past them): to close',
    test: (p) => p.state === 'superseded',
  },
  overtaken: {
    label: 'Overtaken',
    title: 'Updates another update overtook (nixpkgs moved, not up to their version): to rebase',
    test: (p) => p.state === 'overtaken',
  },
  alreadyIn: {
    label: 'Already in nixpkgs',
    title:
      'Init PRs for an attribute nixpkgs has now (added some other way while they waited): to close',
    test: (p) => p.alreadyIn,
  },
  hydraFailing: {
    label: 'Touches a Hydra failure',
    title:
      "PRs touching a package whose build fails on Hydra now (with the log's reason): maybe its fix",
    test: (p) => p.hydraFailing,
  },
  blocksBot: {
    label: 'Blocking the bot',
    title: 'PRs with the very title the update bot would use next: it skips that update',
    test: (p) => p.blocksBot,
  },
  duplicates: {
    label: 'Duplicates',
    title: 'PRs with the same diff as another, or several open for one package',
    test: (p) => duplicateOf.has(p.n),
  },
  downgrade: {
    label: 'Downgrades',
    title: "Updates to a version older than the one they start from, by Nix's own order",
    test: (p) => isDowngrade(p),
  },
  botDowngrade: {
    label: 'Bot downgrades',
    title:
      "r-ryantm's downgrades (an updateScript picking an older release): to close, and to tell nixpkgs-update",
    test: (p) => isDowngrade(p) && p.author === 'r-ryantm',
  },
  snapshotToRelease: {
    label: 'Snapshot → release',
    title:
      'From an unstable snapshot to a tagged release, older by Nix: usually a deliberate switch back',
    test: (p) => p.state === 'snapshotToRelease',
  },
  preRelease: {
    label: 'Pre-release suffix',
    title:
      'Newer for Nix, a pre-release for libversion (1.1.0 -> 1.1.0.dev0, 3.1 -> 3.1_p1): worth a look',
    test: (p) => p.state === 'preRelease',
  },
  versionOnly: {
    label: 'Version bump only',
    title:
      'Every changed line is a version, hash or revision (read from its diff): the safest review there is',
    test: (p) => p.buckets.includes('version-only'),
  },
  tiny: {
    label: 'Tiny',
    title: 'At most 10 lines in at most 2 files',
    test: (p) => p.buckets.includes('tiny'),
  },
};

let prs = [];
const duplicateOf = new Map(); // PR number -> its groups
let view = 'all';
let shown = 0;
let list = []; // PRs, and in the duplicates view {group} headings among them
let groups = [];
const byNumber = new Map();

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const shortDay = (day) =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const ago = (iso) => {
  const days = Math.floor((Date.now() - new Date(iso)) / 86400e3);
  return days < 1 ? 'today' : days < 60 ? `${days} d` : `${Math.floor(days / 30)} mo`;
};

function readFilters() {
  const params = new URLSearchParams(location.search);
  view = VIEWS[params.get('view')] ? params.get('view') : 'all';
  $('q').value = params.get('q') || '';
  $('maintainer').value = params.get('maintainer') || '';
  $('bucket').value = params.get('bucket') || '';
  $('sort').value = params.get('sort') || '';
  $('base').value = params.get('base') || '';
  $('rebuilds').value = params.get('rebuilds') || '';
  $('label').value = params.get('label') || '';
  $('hideDrafts').checked = params.get('drafts') !== 'shown';
}

function writeFilters() {
  if (tab !== 'prs') return;
  const params = new URLSearchParams();
  if (view !== 'all') params.set('view', view);
  for (const id of ['q', 'maintainer', 'bucket', 'base', 'rebuilds', 'label', 'sort'])
    if ($(id).value) params.set(id, $(id).value);
  if (!$('hideDrafts').checked) params.set('drafts', 'shown');
  const query = params.toString();
  history.replaceState(null, '', location.pathname + (query ? `?${query}` : ''));
}

function matches(p) {
  return VIEWS[view].test(p) && filtered(p);
}

// The filters but the view's own test (a duplicate group's members).
function filtered(p) {
  if ($('hideDrafts').checked && p.draft) return false;
  const bucket = $('bucket').value;
  if (bucket && !p.buckets.includes(bucket)) return false;
  const base = $('base').value;
  if (base && p.base !== base) return false;
  const size = $('rebuilds').value;
  if (size) {
    const r = rebuilds(p);
    if (r === null || r > Number(size)) return false;
  }
  const label = $('label').value.trim().toLowerCase();
  if (label && !p.labels.some((l) => l.toLowerCase().includes(label))) return false;
  const handle = $('maintainer').value.trim().replace(/^@/, '').toLowerCase();
  if (handle) {
    const theirs = (p.mergeBot?.maintainers || p.maintainers || []).map((m) => m.toLowerCase());
    if (!theirs.includes(handle)) return false;
  }
  const q = $('q').value.trim().toLowerCase();
  if (q) {
    if (!`#${p.n} ${p.title} ${p.author}`.toLowerCase().includes(q)) return false;
  }
  return true;
}

function facts(p) {
  const out = [];
  if (p.mergeBot)
    out.push(
      `<span class="fact ${p.mergeBot.ready ? 'good' : ''}" title="Maintainers who could merge it with the merge bot: ${esc(p.mergeBot.maintainers.join(', '))}">merge bot${p.mergeBot.ready ? ' ready' : ''}</span>`,
    );
  if (p.state) {
    const u = p.update;
    out.push(
      `<span class="fact warn" title="${esc(`${u.attr}: ${u.from} -> ${u.to}; nixpkgs has ${u.now}`)}">${esc(STATES[p.state] || p.state)}${['superseded', 'overtaken'].includes(p.state) ? ` (has ${esc(u.now)})` : ''}</span>`,
    );
  }
  if (p.alreadyIn)
    out.push(
      `<span class="fact warn" title="nixpkgs has ${esc(p.alreadyIn.attr)} already">already in nixpkgs${p.alreadyIn.version ? ` (${esc(p.alreadyIn.version)})` : ''}</span>`,
    );
  for (const [pkg, systems] of Object.entries(p.hydraFailing || {}))
    out.push(
      `<span class="fact bad" title="${esc(
        Object.entries(systems)
          .map(([system, reason]) => `${system}: ${reason || 'failed'}`)
          .join('; '),
      )}">fails on Hydra: <a href="${HYDRA_JOB}${encodeURIComponent(`${pkg}.${Object.keys(systems)[0]}`)}">${esc(pkg)}</a></span>`,
    );
  if (p.blocksBot) {
    const by = p.blocksBot.by;
    out.push(
      `<span class="fact warn" title="The update bot skips this update while it's open${by ? `; its next try is expected around ${esc(by)}` : ''}">blocks the bot${by ? ` (bot ~${esc(shortDay(by))})` : ''}</span>`,
    );
  }
  for (const g of duplicateOf.get(p.n) || []) {
    const others = g.prs.filter((n) => n !== p.n);
    out.push(
      `<span class="fact" title="${g.kind === 'samePackage' ? `Also for ${esc(g.key)}${g.base ? ` into ${esc(g.base)}` : ''}:` : 'The same change as'} ${others.map((n) => `#${n}`).join(', ')}">${{ sameDiff: 'same diff', sameChange: 'same change', samePackage: 'same package' }[g.kind]}: ${others
        .map((n) => `<a href="${PR_URL}${n}">#${n}</a>`)
        .join(' ')}</span>`,
    );
  }
  const seen = p.diffFacts || {};
  if (seen.versionOnly)
    out.push(
      '<span class="fact good" title="Only versions, hashes and revisions change">version bump only</span>',
    );
  for (const cve of seen.cves || [])
    out.push(`<span class="fact bad" title="Named in what it adds">${esc(cve)}</span>`);
  for (const tag of seen.tags || [])
    out.push(
      `<span class="fact" title="A migration it makes (from its diff)">${esc(TAGS[tag] || tag)}</span>`,
    );
  for (const hint of seen.hints || [])
    out.push(
      `<span class="fact warn" title="In a file it adds: worth asking">${esc(HINTS[hint] || hint)}</span>`,
    );
  if (conflicts(p)) out.push('<span class="fact bad">conflicts</span>');
  if (has(p, '1.severity: security')) out.push('<span class="fact bad">security</span>');
  const r = rebuilds(p);
  if (r !== null && r >= 501)
    out.push(
      `<span class="fact" title="Packages rebuilt (CI's label)">rebuilds ${r.toLocaleString()}+</span>`,
    );
  if (p.review === 'CHANGES_REQUESTED') out.push('<span class="fact bad">changes requested</span>');
  else if (p.approvedBy.length)
    out.push(
      `<span class="fact good" title="Approved by ${esc(p.approvedBy.join(', '))}">approved</span>`,
    );
  const kinds = p.buckets.filter((b) => b !== 'tiny');
  if (kinds.length) out.push(`<span class="buckets">${esc(kinds.join(' · '))}</span>`);
  return out.join(' ');
}

const CI = { SUCCESS: '✓', FAILURE: '✗', ERROR: '✗', PENDING: '…', EXPECTED: '…' };

// The orders of the sort switch; '' is the view's usual: the bot's next
// try first for "Blocking the bot", else the oldest update first (what's
// been waiting longest).
const SORTS = {
  updated: (a, b) => a.updated.localeCompare(b.updated),
  recent: (a, b) => b.updated.localeCompare(a.updated),
  created: (a, b) => a.created.localeCompare(b.created),
  small: (a, b) => a.additions + a.deletions - (b.additions + b.deletions),
  // Fewest rebuilds first (the safest), unknown last.
  rebuilds: (a, b) =>
    (rebuilds(a) ?? 1e9) - (rebuilds(b) ?? 1e9) || a.updated.localeCompare(b.updated),
  // Most approvals first (the closest to done).
  approvals: (a, b) => approvals(b) - approvals(a) || a.updated.localeCompare(b.updated),
  bot: (a, b) =>
    (a.blocksBot?.by || '9999').localeCompare(b.blocksBot?.by || '9999') ||
    a.updated.localeCompare(b.updated),
};
const order = () => SORTS[$('sort').value] || (view === 'blocksBot' ? SORTS.bot : SORTS.updated);

// A duplicate group's heading row: what its PRs share, and how many.
function groupRow(g) {
  const what =
    g.kind === 'sameDiff'
      ? 'with the same diff'
      : g.kind === 'sameChange'
        ? 'making the same change (other context)'
        : `open for the same package: <span class="mono">${esc(g.key)}</span>${g.base ? ` into <span class="mono">${esc(g.base)}</span>` : ''}`;
  return `<tr class="group"><td colspan="7"><b>${g.members.length} PRs</b> ${what}</td></tr>`;
}

function row(p) {
  return `<tr${p.draft ? ' class="draft"' : ''}>
    <td class="num"><a href="${PR_URL}${p.n}">#${p.n}</a><br><a class="alt" href="${GHDIFF_URL}${p.n}" title="Review it on ghdiff.com">ghdiff</a></td>
    <td class="title">${esc(p.title)}${p.draft ? ' <span class="fact">draft</span>' : ''}</td>
    <td>${esc(p.author)}</td>
    <td class="num" title="${p.changedFiles} files">+${p.additions} −${p.deletions}</td>
    <td class="ci ${(p.ci || '').toLowerCase()}" title="${p.ci || 'no checks'}">${CI[p.ci] || '–'}</td>
    <td>${facts(p)}</td>
    <td class="num" title="${esc(p.updated)}">${ago(p.updated)}</td>
  </tr>`;
}

function drawMore() {
  const next = list.slice(shown, shown + STEP);
  $('rows').insertAdjacentHTML(
    'beforeend',
    next.map((item) => (item.group ? groupRow(item.group) : row(item))).join(''),
  );
  shown += next.length;
  $('more').textContent = shown < list.length ? `Showing ${shown} of ${list.length}…` : '';
}

function render() {
  writeFilters();
  for (const b of $('views').querySelectorAll('button'))
    b.setAttribute('aria-pressed', b.dataset.view === view);
  if (view === 'duplicates') {
    // Each group together, under its heading: the biggest first, then the
    // one waiting longest; its members in the sort's order. A group needs
    // two members left by the filters.
    const sort = order();
    const found = groups
      .map((g) => ({
        ...g,
        members: g.prs.map((n) => byNumber.get(n)).filter((p) => p && filtered(p)),
      }))
      .filter((g) => g.members.length > 1)
      .map((g) => ({ ...g, members: g.members.sort(sort) }))
      .sort((a, b) => b.members.length - a.members.length || sort(a.members[0], b.members[0]));
    list = found.flatMap((g) => [{ group: g }, ...g.members]);
    const prCount = new Set(found.flatMap((g) => g.members.map((p) => p.n))).size;
    $('count').textContent =
      `${found.length.toLocaleString()} groups, ${prCount.toLocaleString()} PRs`;
  } else {
    list = prs.filter(matches).sort(order());
    $('count').textContent = `${list.length.toLocaleString()} PRs`;
  }
  $('rows').innerHTML = '';
  shown = 0;
  drawMore();
}

// The tabs: open PRs (above), open issues, and the PRs merged into master
// that the channel doesn't have yet; the last two loaded when first shown.
let tab = 'prs';
const loaded = {};

async function loadJson(name) {
  const res = await fetch(`${BASE}${name}`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${name}: ${res.status}`);
  return res.json();
}

// A list drawn STEP rows at a time as you scroll (tbody, the "more" line).
function pager(rowsId, moreId, draw) {
  const pg = { list: [], shown: 0 };
  pg.more = () => {
    const next = pg.list.slice(pg.shown, pg.shown + STEP);
    $(rowsId).insertAdjacentHTML('beforeend', next.map(draw).join(''));
    pg.shown += next.length;
    $(moreId).textContent =
      pg.shown < pg.list.length ? `Showing ${pg.shown} of ${pg.list.length}…` : '';
  };
  pg.set = (list) => {
    pg.list = list;
    pg.shown = 0;
    $(rowsId).innerHTML = '';
    pg.more();
  };
  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && pg.shown < pg.list.length) pg.more();
  }).observe($(moreId));
  return pg;
}

// Issues, by their title's convention (nixpkgs' issue templates).
const ISSUE_KINDS = [
  ['build', /^\s*\[?build failure\b/i, 'Build failure'],
  ['update', /^\s*\[?update request\b/i, 'Update request'],
  ['package', /^\s*\[?package request\b/i, 'Package request'],
  ['module', /^\s*\[?module request\b/i, 'Module request'],
  ['unreproducible', /^\s*\[?unreproducible package\b/i, 'Unreproducible'],
  ['docs', /^\s*\[?(?:missing )?(?:documentation|doc|nixpkgs manual)\b/i, 'Documentation'],
  ['tracking', /^\s*\[?tracking(?: issue)?\b/i, 'Tracking'],
  ['feature', /^\s*\[?feature request\b/i, 'Feature request'],
];
// "foo: …", or the package after a template's prefix ("Build failure: foo").
const ATTR = /^[\s`'"]*([A-Za-z_][\w.+-]*)/;
const KIND_WORDS = new Set([
  'build',
  'update',
  'package',
  'module',
  'unreproducible',
  'documentation',
  'doc',
  'tracking',
  'feature',
  'missing',
  'nixpkgs',
  'rfc',
  'bug',
  'error',
  'request',
]);

function issueFacts(i) {
  const found = ISSUE_KINDS.find(([, re]) => re.test(i.title));
  const kind = found ? found[0] : 'other';
  let pkg = null;
  if (found && ['build', 'update', 'package', 'unreproducible'].includes(kind)) {
    const rest = i.title.slice(i.title.indexOf(':') + 1);
    pkg = i.title.includes(':') ? ATTR.exec(rest)?.[1] : null;
  } else if (!found) {
    const m = /^\s*([A-Za-z_][\w.+-]*)\s*:/.exec(i.title);
    if (m && !KIND_WORDS.has(m[1].toLowerCase())) pkg = m[1];
  }
  return { ...i, kind, pkg: pkg?.replace(/[.:,]+$/, '') || null };
}

// Open PRs by package: their title's "attr:" and the by-name directories.
function prsByPackage() {
  const map = new Map();
  const add = (name, n) => {
    if (!name) return;
    const key = name.toLowerCase();
    map.set(key, [...new Set([...(map.get(key) || []), n])]);
  };
  for (const p of prs) {
    add(/^([\w.+-]+):/.exec(p.title)?.[1], p.n);
    for (const pkg of p.packages || []) add(pkg, p.n);
  }
  return map;
}

const HYDRA_JOB = 'https://hydra.nixos.org/job/nixpkgs/unstable/';
// What Hydra says of a build-failure issue (the digest's issues.py).
function hydraCell(i) {
  const h = i.hydra;
  if (!h) return '';
  const job = (system) =>
    `<a href="${HYDRA_JOB}${encodeURIComponent(`${h.package}.${system}`)}">${esc(system)}</a>`;
  switch (h.verdict) {
    case 'builds':
      return `<span class="fact ${h.condition ? 'warn' : 'good'}" title="${h.condition ? 'Builds on Hydra, but the title adds a condition to check' : 'Every Hydra job it concerns builds: a candidate to close'}">builds</span> ${(h.systems || []).map(job).join(' ')}`;
    case 'failing':
      return `<span class="fact bad">failing</span> ${Object.entries(h.reasons || {})
        .map(
          ([system, reason]) =>
            `${job(system)}${reason ? ` <span class="buckets">${esc(reason)}</span>` : ''}`,
        )
        .join(' ')}`;
    default:
      return `<span class="buckets">${esc({ waiting: 'dependency failed or not finished', variant: "a variant Hydra doesn't build", noJob: 'no Hydra job by that name', platformNotBuilt: 'platform not built by Hydra' }[h.verdict] || h.verdict)}</span>`;
  }
}
// What nixpkgs has of an update request (the digest's issues.py).
function updateCell(i) {
  const u = i.update;
  const prs = (u.prs || []).map((n) => `<a href="${PR_URL}${n}">#${n}</a>`).join(' ');
  const has = u.now ? ` <span class="buckets">has ${esc(u.now)}</span>` : '';
  const said = {
    done: '<span class="fact good" title="nixpkgs has the version asked, or newer: a candidate to close">done</span>',
    partly:
      '<span class="fact warn" title="nixpkgs moved on, but not to the version asked">partly</span>',
    open: '<span class="fact">open</span>',
    notFound: '<span class="buckets">no package by that name</span>',
    notVersion: '<span class="buckets">not a version asked</span>',
  }[u.verdict];
  const bot = u.bot
    ? ` · <span class="fact" title="The update bot's queue has ${esc(u.bot.to)}: it updates this on its next try${u.bot.by ? `, expected around ${esc(u.bot.by)}` : ''}">bot: ${esc(u.bot.to)}${u.bot.by ? ` ~${esc(shortDay(u.bot.by))}` : ''}</span>`
    : '';
  return `${said || esc(u.verdict)}${has}${prs ? ` · PR ${prs}` : ''}${bot}`;
}
const HYDRA_FILTERS = {
  updDone: (_, u) => u?.verdict === 'done',
  updPr: (_, u) => u?.verdict !== 'done' && (u?.prs || []).length > 0,
  updOpen: (_, u) => u?.verdict === 'open' && !(u.prs || []).length,
  updBot: (_, u) => u?.verdict !== 'done' && !!u?.bot,
  updPartly: (_, u) => u?.verdict === 'partly',
  close: (h) => h?.verdict === 'builds' && !h.condition,
  condition: (h) => h?.verdict === 'builds' && h.condition,
  failing: (h) => h?.verdict === 'failing',
  other: (h) => h && !['builds', 'failing'].includes(h.verdict),
};

let issues = [];
let openFor = new Map();
let issuePager;
function issueRow(i) {
  const label = ISSUE_KINDS.find(([k]) => k === i.kind)?.[2] || '';
  const prsFor = (i.pkg && openFor.get(i.pkg.toLowerCase())) || [];
  return `<tr>
    <td class="num"><a href="${ISSUE_URL}${i.n}">#${i.n}</a></td>
    <td class="title">${esc(i.title)}</td>
    <td>${esc(label)}</td>
    <td class="mono">${esc(i.pkg || '')}</td>
    <td>${i.update ? updateCell(i) : hydraCell(i)}</td>
    <td>${prsFor.map((n) => `<a href="${PR_URL}${n}">#${n}</a>`).join(' ')}</td>
  </tr>`;
}
function renderIssues() {
  const q = $('iq').value.trim().toLowerCase();
  const kind = $('ikind').value;
  const withPr = $('ihasPr').checked;
  const hydra = $('ihydra').value;
  const list = issues.filter(
    (i) =>
      (!kind || i.kind === kind) &&
      (!hydra || HYDRA_FILTERS[hydra](i.hydra, i.update)) &&
      (!withPr || (i.pkg && openFor.has(i.pkg.toLowerCase()))) &&
      (!q || `#${i.n} ${i.title} ${i.pkg || ''}`.toLowerCase().includes(q)),
  );
  $('icount').textContent = `${list.length.toLocaleString()} issues`;
  issuePager.set(list);
  writeTab({ q: $('iq').value, kind, hydra, pr: withPr ? '1' : '' });
}

let merged = { prs: [] };
let mergedPager;
const UPDATE_TITLE = /^[\w.+-]+: \S*\d\S* (?:->|→) \S*\d\S*\s*$/;
function mergedRow(p) {
  return `<tr>
    <td class="num"><a href="${PR_URL}${p.n}">#${p.n}</a></td>
    <td class="title">${esc(p.title)}</td>
    <td class="num" title="${esc(p.merged)}">${ago(p.merged)}</td>
    <td>${esc(p.base)}</td>
  </tr>`;
}
function renderMerged() {
  const q = $('mq').value.trim().toLowerCase();
  const updates = $('mupdates').checked;
  const list = merged.prs
    .filter(
      (p) =>
        (!updates || UPDATE_TITLE.test(p.title)) &&
        (!q || `#${p.n} ${p.title}`.toLowerCase().includes(q)),
    )
    .sort((a, b) => b.merged.localeCompare(a.merged));
  $('mcount').textContent = `${list.length.toLocaleString()} PRs`;
  mergedPager.set(list);
  writeTab({ q: $('mq').value, updates: updates ? '1' : '' });
}

// The address of an issues or merged tab, with its filters.
function writeTab(values) {
  const params = new URLSearchParams({ tab });
  for (const [k, v] of Object.entries(values)) if (v) params.set(k, v);
  history.replaceState(null, '', `${location.pathname}?${params}`);
}

async function showTab(name) {
  tab = name;
  for (const b of $('tabs').querySelectorAll('button'))
    b.setAttribute('aria-pressed', b.dataset.tab === tab);
  for (const t of ['prs', 'issues', 'merged']) $(`tab-${t}`).hidden = t !== tab;
  const params = new URLSearchParams(location.search);
  if (tab === 'prs') return render();
  try {
    if (tab === 'issues' && !loaded.issues) {
      $('icount').textContent = 'Loading the issues…';
      const data = await loadJson('issues.json');
      issues = data.issues.map(issueFacts);
      openFor = prsByPackage();
      const counts = countBy(issues, (i) => i.kind);
      $('ikind').insertAdjacentHTML(
        'beforeend',
        [...ISSUE_KINDS, ['other', null, 'Other']]
          .map(
            ([k, , label]) =>
              `<option value="${k}">${label} (${(counts[k] || 0).toLocaleString()})</option>`,
          )
          .join(''),
      );
      issuePager = pager('irows', 'imore', issueRow);
      $('iq').value = params.get('q') || '';
      $('ikind').value = params.get('kind') || '';
      $('ihasPr').checked = params.get('pr') === '1';
      $('ihydra').value = HYDRA_FILTERS[params.get('hydra')] ? params.get('hydra') : '';
      $('issueFilters').addEventListener('input', renderIssues);
      $('issueFilters').addEventListener('submit', (e) => e.preventDefault());
      loaded.issues = true;
    }
    if (tab === 'merged' && !loaded.merged) {
      $('mcount').textContent = 'Loading the merged PRs…';
      merged = await loadJson('merged.json');
      const hours = daysSince(merged.since) * 24;
      const age = hours < 48 ? `${Math.floor(hours)} h ago` : `${Math.floor(hours / 24)} d ago`;
      $('mhead').innerHTML =
        `The nixos-unstable channel is at <a href="https://github.com/NixOS/nixpkgs/commit/${esc(merged.revision)}" class="mono">${esc(merged.revision.slice(0, 10))}</a>, committed ${new Date(merged.since).toLocaleString()} (${age}): ${merged.prs.length.toLocaleString()} PRs merged into master since, waiting for the channel. As of ${new Date(merged.generatedAt).toLocaleString()}.`;
      mergedPager = pager('mrows', 'mmore', mergedRow);
      $('mq').value = params.get('q') || '';
      $('mupdates').checked = params.get('updates') === '1';
      $('mergedFilters').addEventListener('input', renderMerged);
      $('mergedFilters').addEventListener('submit', (e) => e.preventDefault());
      loaded.merged = true;
    }
  } catch (e) {
    $(tab === 'issues' ? 'icount' : 'mcount').textContent = `Couldn't read it (${e.message}).`;
    return;
  }
  if (tab === 'issues') renderIssues();
  else renderMerged();
}

async function main() {
  readFilters();
  let data;
  try {
    const res = await fetch(DATA, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`${res.status}`);
    data = await res.json();
  } catch (e) {
    $('status').textContent = `Couldn't read the digest (${e.message}).`;
    return;
  }
  prs = data.prs;
  groups = data.groups;
  for (const p of prs) byNumber.set(p.n, p);
  // Digests from before 2026-10-09 said only the bot's title.
  for (const p of prs) if (typeof p.blocksBot === 'string') p.blocksBot = { title: p.blocksBot };
  for (const g of data.groups)
    for (const n of g.prs) duplicateOf.set(n, [...(duplicateOf.get(n) || []), g]);
  $('status').textContent =
    `${prs.length.toLocaleString()} open PRs, as of ${new Date(data.generatedAt).toLocaleString()}.`;
  $('views').innerHTML = Object.entries(VIEWS)
    .map(
      ([key, v]) =>
        `<button type="button" data-view="${key}" title="${esc(v.title || '')}">${esc(v.label)} <b>${prs.filter(v.test).length.toLocaleString()}</b></button>`,
    )
    .join('');
  const bases = Object.entries(countBy(prs, (p) => p.base)).sort((a, b) => b[1] - a[1]);
  $('base').insertAdjacentHTML(
    'beforeend',
    bases
      .map(([b, n]) => `<option value="${esc(b)}">${esc(b)} (${n.toLocaleString()})</option>`)
      .join(''),
  );
  const labels = [...new Set(prs.flatMap((p) => p.labels))].sort();
  $('labels').innerHTML = labels.map((l) => `<option value="${esc(l)}"></option>`).join('');
  const buckets = [...new Set(prs.flatMap((p) => p.buckets))].sort();
  $('bucket').insertAdjacentHTML(
    'beforeend',
    buckets.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join(''),
  );
  readFilters(); // the bucket's options exist now
  $('views').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    view = b.dataset.view;
    render();
  });
  $('filters').addEventListener('input', render);
  $('filters').addEventListener('submit', (e) => e.preventDefault());
  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && shown < list.length) drawMore();
  }).observe($('more'));
  $('tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.dataset.tab === tab) return;
    history.replaceState(null, '', location.pathname); // each tab starts unfiltered
    showTab(b.dataset.tab);
  });
  const start = new URLSearchParams(location.search).get('tab');
  showTab(['issues', 'merged'].includes(start) ? start : 'prs');
}

main();
