# nixkeeper-prs

A digest of [nixpkgs](https://github.com/NixOS/nixpkgs)' open pull requests,
sorted by what they change, for [nixkeeper](https://github.com/iedame/nixkeeper):
which ones a maintainer can merge themselves, which are already done in
nixpkgs, which stop the update bot, which duplicate another. nixpkgs gets
about 2,000 PRs a week, and a few percent are never merged nor closed: the
point is surfacing the small, mergeable ones before they're buried.

**A proof of concept**: run hourly, with a plain page at
<https://iedame.github.io/nixkeeper-prs/> (not linked from nixkeeper.com yet).

## The digest

On the `data` branch:

- [`data/prs.json`](https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/prs.json):
  every open PR (`prs`): its number, title, author, draft, dates, base
  branch, head commit, lines added and removed, `changedFiles` and
  `files` (the first 100), labels, `mergeable` (`CONFLICTING`...), `review` (GitHub's
  review decision), `approvedBy`, `ci` (its checks' combined state); and
  what the digest makes of it:
  - `buckets`: `update`, `init`, `drop` (by its title, nixpkgs'
    conventions), `by-name` (touches only `pkgs/by-name`), `tiny` (at most
    10 lines in 2 files), `bot` (r-ryantm's), `ci-bot` (nixpkgs-ci's), `python`, `haskell`, `node`,
    `nixos`, `lib`, `ci`, `docs` (by path), `treewide` (50 files or more)
  - `packages` (the `pkgs/by-name` ones it touches) and their
    `maintainers` (the channel's index)
  - `update` (`attr`, `from`, `to`, and `now`: what nixpkgs has, master's
    version or else the channel's; a title is an update only with a digit
    on both sides) and `state`. By Nix's own order (`builtins.compareVersions`,
    ported in `nixversions.py` and checked against Nix on every open
    update's versions): `downgrade` (`to` is older), `snapshotToRelease`
    (from an `-unstable-` snapshot to a release, older for Nix: usually a
    deliberate switch back), `preRelease` (newer for Nix, a pre-release for
    libversion: `1.1.0 -> 1.1.0.dev0`). Against nixpkgs now, by nixkeeper's
    order: `superseded` (nixpkgs moved past `from` and has `to` or newer:
    close it), `overtaken` (moved past `from`, not up to `to`: rebase it)
  - `blocksBot`: the title the update bot would use for its next update of
    that package (nixkeeper-updates' queue), when this PR, not the bot's,
    has it: the bot finds it and skips the update
  - `mergeBot`: when a maintainer can merge it with the
    [merge bot](https://github.com/NixOS/nixpkgs/blob/master/ci/README.md#nixpkgs-merge-bot):
    nixpkgs' CI labels it `2.status: merge-bot eligible` (it knows who's a
    committer, so it also counts committers' PRs and approvals; `label`:
    true), or, without the label, r-ryantm's PRs touching only
    `pkgs/by-name`, into a development branch, no changes requested;
    `ready` (CI green) and the `maintainers` of every package it touches
  - `diff`: the fingerprint of its diff (blob ids and hunk positions left
    out: the same change made on another base is the same)
  - `diffFacts`, read from its diff (`nixkeeper_prs/diffs.py`; a reading
    of the text, not an evaluation): `change` (a fingerprint of the
    changed lines only, no context), `versionOnly` (every changed line is
    a version, hash or revision: a plain bump; also the `version-only`
    bucket), `tags` (migrations in files that existed: `strictDeps`,
    `structuredAttrs`, `revToTag`, `sriHash`, `dropWithLib`, `pyproject`,
    `finalAttrs`, `updateScript`, `pythonImportsCheck`, `byName`), `cves`
    (CVE ids in added lines; also the `cve` bucket), `hints` (in Nix files
    it adds: `rec`, `withLib`, `revNotTag`, `oldHash`,
    `noPythonImportsCheck`)

  and `groups` of duplicates: `sameDiff` (the same fingerprint),
  `sameChange` (the same changed lines with other context, when that's not
  already a same-diff group) and `samePackage` (several open updates or
  inits of one attribute).
- [`data/meta.json`](https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/meta.json):
  when, how many PRs, how many in each bucket and state, blocking the bot,
  merge-bot eligible and ready, the groups, the diffs read, and the issues
  and merged PRs listed (`issues`, `merged`: how many and when; a listing
  that failed keeps the last one, its time saying so).
- [`data/issues.json`](https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/issues.json):
  every open issue's number and title (`issues`: `[{"n", "title"}]`), for
  counting a package's issues by the words in their titles, as nixkeeper
  does.
- [`data/merged.json`](https://raw.githubusercontent.com/iedame/nixkeeper-prs/data/data/merged.json):
  the PRs merged into master since the nixos-unstable channel's commit
  (`revision`, committed at `since`): what master has that the channel
  doesn't yet (`prs`: `[{"n", "title", "draft", "base", "merged"}]`).
  Listed with GitHub's search in 12-hour windows (it gives at most 1,000
  results).
- `data/diffs.json`: each PR's fingerprint and diff facts by the head
  commit it was read at, so a diff is read again only when the PR changes.

## How it's made

- Every open PR's details through GitHub's GraphQL API, with the
  `NIXKEEPER_PRS_TOKEN` secret: a fine-grained token with read-only access
  to public repositories (5,000 points an hour; without it, the
  workflow's own token, 1,000). A full sweep (25 PRs a request, ~500
  requests, ~460 points) takes about an hour, as GitHub answers each page
  in seconds: it's done the first time and once a day. Other runs list
  every open PR's number and last update (100 a request, minutes) and read
  the details of those new or updated since again, 25 a request; the
  rest come from the last digest. What changes without updating a PR (its
  checks finishing, its merge state: GitHub works it out lazily, so most
  PRs' `mergeable` is `UNKNOWN`) is caught up by the daily full sweep. The log says how long GitHub takes a request.
- What nixpkgs has: the nixos-unstable channel's package index (versions,
  maintainers), master's versions from
  [nixkeeper-hydra](https://github.com/iedame/nixkeeper-hydra)'s digest,
  the update bot's queue from
  [nixkeeper-updates](https://github.com/iedame/nixkeeper-updates)'.
  Versions are ordered as nixkeeper orders them (its code, a flake input).
- Diffs through GitHub's REST API (`pulls/N` as a diff), with the same
  token (its 5,000 requests an hour, apart from GraphQL's points; github.com's
  `pull/N.diff` answers 429 to GitHub Actions after a few dozen), one a
  second, at most 1,500 and 40 minutes a run, counted from when the PRs are
  known; the most recently updated first, small PRs only (at most 50 files
  and 2,000 lines): the first runs read the backlog. Told it's too many,
  a run stops asking and leaves the rest for the next.

Every request is one at a time, at most one a second, with a User-Agent
naming this repository.

## Running it

```bash
GITHUB_TOKEN=... nix run . -- data
```

writes the digest to `data/` (GitHub's GraphQL API needs a token: any
token reading public data). `nix flake check` runs the tests and lint,
`nix fmt` formats.

## License

MIT
