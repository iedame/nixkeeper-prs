// The proof-of-concept page: nixpkgs' open PRs from the digest (the data
// branch's data/prs.json), in views (what to act on) and filters, a plain
// table, more rows as you scroll. Nothing is sent anywhere.

const DATA = 'https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/prs.json';
const PR_URL = 'https://github.com/NixOS/nixpkgs/pull/';
const STEP = 200;

// What to act on, each a test on a PR (groups: the digest's duplicates).
const VIEWS = {
  all: { label: 'All open', test: () => true },
  mergeBot: {
    label: 'Merge it yourself',
    title:
      "r-ryantm's by-name PRs with CI green: a maintainer of every package can merge with the merge bot",
    test: (p) => p.mergeBot?.ready,
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
  backwards: {
    label: 'Backwards',
    title: 'Updates whose new version sorts below the old: a downgrade, or a version scheme change',
    test: (p) => p.state === 'backwards',
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
let list = [];

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
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
  $('hideDrafts').checked = params.get('drafts') !== 'shown';
}

function writeFilters() {
  const params = new URLSearchParams();
  if (view !== 'all') params.set('view', view);
  for (const id of ['q', 'maintainer', 'bucket']) if ($(id).value) params.set(id, $(id).value);
  if (!$('hideDrafts').checked) params.set('drafts', 'shown');
  const query = params.toString();
  history.replaceState(null, '', location.pathname + (query ? `?${query}` : ''));
}

function matches(p) {
  if (!VIEWS[view].test(p)) return false;
  if ($('hideDrafts').checked && p.draft) return false;
  const bucket = $('bucket').value;
  if (bucket && !p.buckets.includes(bucket)) return false;
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
      `<span class="fact warn" title="${esc(`${u.attr}: ${u.from} -> ${u.to}; nixpkgs has ${u.now}`)}">${p.state} (has ${esc(u.now)})</span>`,
    );
  }
  if (p.blocksBot)
    out.push(
      '<span class="fact warn" title="The update bot skips this update while it\'s open">blocks the bot</span>',
    );
  for (const g of duplicateOf.get(p.n) || []) {
    const others = g.prs.filter((n) => n !== p.n);
    out.push(
      `<span class="fact" title="${g.kind === 'sameDiff' ? 'The same diff as' : `Also for ${esc(g.key)}:`} ${others.map((n) => `#${n}`).join(', ')}">${g.kind === 'sameDiff' ? 'same diff' : 'same package'}: ${others
        .map((n) => `<a href="${PR_URL}${n}">#${n}</a>`)
        .join(' ')}</span>`,
    );
  }
  if (p.mergeable === 'CONFLICTING') out.push('<span class="fact bad">conflicts</span>');
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

function row(p) {
  return `<tr${p.draft ? ' class="draft"' : ''}>
    <td><a href="${PR_URL}${p.n}">#${p.n}</a></td>
    <td class="title">${esc(p.title)}${p.draft ? ' <span class="fact">draft</span>' : ''}</td>
    <td>${esc(p.author)}</td>
    <td class="num" title="${p.fileCount} files">+${p.additions} −${p.deletions}</td>
    <td class="ci ${(p.ci || '').toLowerCase()}" title="${p.ci || 'no checks'}">${CI[p.ci] || '–'}</td>
    <td>${facts(p)}</td>
    <td class="num" title="${esc(p.updated)}">${ago(p.updated)}</td>
  </tr>`;
}

function drawMore() {
  const next = list.slice(shown, shown + STEP);
  $('rows').insertAdjacentHTML('beforeend', next.map(row).join(''));
  shown += next.length;
  $('more').textContent = shown < list.length ? `Showing ${shown} of ${list.length}…` : '';
}

function render() {
  writeFilters();
  for (const b of $('views').querySelectorAll('button'))
    b.setAttribute('aria-pressed', b.dataset.view === view);
  list = prs.filter(matches);
  // Oldest update first: what's been waiting longest.
  list.sort((a, b) => a.updated.localeCompare(b.updated));
  $('count').textContent = `${list.length.toLocaleString()} PRs`;
  $('rows').innerHTML = '';
  shown = 0;
  drawMore();
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
  render();
}

main();
