# Design decisions

Every decision below was settled by running a check over both test corpora
rather than by reasoning about it, and several confident conclusions were
overturned that way. Where a decision was reversed, the original reasoning is
kept alongside the reversal — the wrong turns are the useful part.

Two corpora are used throughout, and nothing is ever tuned against one alone:

* **kubernetes/website**, scoped to `content/en/docs/concepts` — 176 pages, Hugo,
  free-form commit messages, raw HTML permitted in `.md`.
* **docusaurus**, scoped to `website/docs` — 94 pages, MDX, conventional commits.

---

## Measuring against a reference implementation

The first version of the extractor was deliberately naive, then diffed column by
column against an existing packaged extractor over the same 176 pages.

**The reference is a baseline, not ground truth.** The first run proved it: the
packaged extractor's `title` is garbage on this repo too (3 of 176 pages, all of
them HTML or shell comments). Where two implementations disagree, ask which is
right — sometimes neither is.

### Title

Frontmatter `^title: ` with an h1 fallback reads 176/176 pages, against the
reference's 3. Beat the reference rather than matching it.

Known assumption: the pattern is not scoped to the frontmatter block, so a
column-zero `title:` in a page body would win. Zero such pages in
kubernetes/website today (checked all 1,674 in `content/en/docs`).

### Word count

Handled by `to_prose()`. The residual gap against the reference is fully
explained: on 150 pages this tool reads lower because it strips frontmatter and
the reference does not (corr 0.96 with frontmatter size); on 25 it reads higher
because the reference strips markdown table rows and this tool does not (corr
0.99 with table size). 28 concept pages contain tables.

**Table rows count toward `word_count` but are stripped for readability.** They
are substance, but they are not sentences. Two composable functions,
`to_prose()` and `strip_tables()`, rather than a boolean flag.

**Readability is `None`, not 0.0, when there is no prose to score.** textstat
returns 0.0 for an empty string, which on the Flesch scale reads as "very
difficult" rather than "not measured".

### Heading count

18/176 disagreements dropped to 2/176 by counting headings in `to_prose(text)`
rather than in raw text. The two residual disagreements are *reference* bugs: its
`^(#{1,6})\s+.+` lets `\s+` cross newlines, so a bare `## ` — left behind when
`{{% heading "whatsnext" %}}` is stripped, and 128 pages use that shortcode —
glues onto the next line of body text, inventing a heading and sometimes
swallowing a real one after it.

### Staleness and commit count

`--follow` plus `--numstat`, with the git metrics computed from content commits
only (`added + deleted > 0`), keeping `days_since_update_raw` alongside for
comparison. The worst `age_days` gap against the reference went 3,560 days → 1
day; `commit_count` disagreements went 121/176 in both directions → 36/176 in
one.

Why the zero-line filter matters, live: the newest commit on
`configuration/_index.md` and `storage/_index.md` is "Remove exec permission on
markdown files" — a repo-wide chmod, 0 lines changed, making both pages look 358
days fresher than they are.

### Why kubernetes gets no keyword noise filter

The reference's kubernetes regex is
`\b(gofmt|prettier|lint(?:ing)?|formatting|chore)\b`, and its author flagged it
UNVERIFIED. It is demonstrably wrong: it fires on `configmap.md`'s newest commit,
"Fix formatting of kubectl logs command" — one line changed, a real editorial fix
— pushing that page's `days_since_update` from 264 to 698. Repo-wide it matches
262 of 63,564 commits, including "Fix formatting and improve clarity in
container-runtimes.md" and merge commits that matched only via a branch name
containing "chore".

Tested two ways before deciding:

* **Breadth.** The widest commit in concepts history touches 34 files. A real
  mechanical pass (prettier, gofmt) would hit hundreds.
* **Concentration.** 143 distinct commits set the staleness dates of 176 pages,
  130 of them setting exactly one page each. A repo with a noise problem shows
  one commit dominating dozens; this distribution is flat.

Kubernetes edits pages individually — there is no noise class to catch, and the
reference's regex demonstrably damages real data. Ship with the zero-line filter
alone and report this as a finding, not an omission.

Borderline cases are named rather than filtered (16 pages, ~9%): "fix(links):
update kubernetes/community links from master to main" (9 pages) and "update
api-reference and ref shortcode callers for new URL structure" (7) are mechanical
find-and-replace passes but real fixes. Whether a URL update counts as
"maintained" is a reader's judgment — state it, do not encode it.

### Dates are computed at extraction time

Open, and known. Date metrics are computed against `datetime.now()`, so they are
a function of *when the extractor ran*. Two CSVs produced hours apart disagree by
exactly 1 day on whichever pages crossed a 24h boundary in between — 9 pages on
`days_since_update`, 12 on `age_days`, in the first comparison. The published
tool has the same design.

Options, neither taken yet: store the raw last and first commit dates and derive
day counts at analysis time, or stamp the extraction time into the output.
`extracted_at` currently does the latter, and the dashboard surfaces it.

---

## What earns a config key

The test is **not** "is this repo-specific?" It is: **would doing this
unconditionally produce a wrong answer on some repo?** If a strategy is harmless
everywhere — frontmatter-then-h1 title lookup — it stays in core and needs no
knob. Config is a cost: every option is something a user must discover, get
right, and maintain.

| decision | needs config? | evidence |
|---|---|---|
| file extension | **no** | `["*.md","*.mdx"]` unconditionally: k8s 176, docusaurus 94, no overlap |
| frontmatter `---` | no | both repos use YAML `---` |
| title frontmatter→h1 | no | docusaurus pages often have no `title:`; the h1 fallback covers them |
| ``` fences, `\|` tables | no | shared markdown syntax |
| JSX / `import` stripping | no, but **gate on file extension** | see below |
| **noise_re** | **YES** | see below |

### noise_re is the one that earns config

Conventional-commit prefixes in the last 4,000 commits: docusaurus 3,873 (97%),
kubernetes 127 (3%). Docusaurus declares `chore:` as a structured field (921
commits); kubernetes writes free-form English with no reliable signal. Neither
answer is right unconditionally — kubernetes wants no filter, docusaurus wants
`^chore:`.

Config should be a *compiled regex* supplied by the caller, not a keyword list
core assembles: the anchoring differs too (`^chore:` as a prefix, against the
reference's `\bchore\b` anywhere, which matched a branch name in a merge).

Proven from both sides on the same code:

* kubernetes, filter ON: damages real data. `configmap.md` ages 264 → 698 days.
* docusaurus, filter OFF: hides real staleness. 7 of 8 pages in `guides/docs`
  report 158 days when the true last content edit is up to 1,033 days ago — all
  reset by one commit, `chore(website): migrate MDX heading ids to comment syntax
  (#11779)`. That commit CHANGED lines, so the zero-line filter cannot catch it;
  only the `^chore:` declaration can.

### example_patterns — config for a different reason

No default can be *complete*. Snippet-include syntax is open-ended: polars
`{{code_block}}`, mkdocs `--8<--`, Sphinx `.. literalinclude::`, VitePress
`<<< @/file.js`. Agreed as config and deliberately never wired up — until it is,
`code_block_density` reads 0 on any repo whose examples live in macros.

### fence_mode — NOT config

The "paired" rule (drop fences whose info string has `exec="on"`) is harmless
where the syntax is unused. Measured: polars paired 222 / plain 512 (differ 290),
kubernetes 722/722, docusaurus 1000/1000 — both differ by zero. Run the exclusion
unconditionally.

### JSX stripping is gated on .mdx, not applied unconditionally

Running the JSX pattern over kubernetes/website matches 19 times across 5 pages —
and none are JSX. They are angle-bracket placeholders in prose:
`<CONTAINER_NAME>`, `<IPv4 CIDR>`, `<NodeIP>`, `<SUBMOUNT>`, `<KEY>`.

Shape cannot separate them: `<NodeIP>` is lexically identical to a component like
`<Tabs>`. The reliable discriminator is the file extension — JSX is only valid in
`.mdx` — so this is a per-file decision core can make itself, not a config key.

---

## Choosing what to score

Seventeen extracted columns collapse to a small number of defensible score
inputs. The four questions asked of the distributions were: which metrics
discriminate, which are secretly the same metric, how skewed is each, and do the
outliers survive reading the actual page.

| metric | role | why |
|---|---|---|
| `days_since_update` | **score** | behaves correctly across the whole range, including single-commit pages |
| `flesch_reading_ease` | **score** | `None` below a 5-sentence floor |
| `word_count` | detector | feeds the not-a-doc-page rule, carries no weight |
| `code_block_density` | detector + context | same rule; direction is not "more is better" |
| `staleness_to_age_ratio` | modifier + context | blind on single-commit pages as an input |
| `age_days` | context | makes staleness interpretable, is not itself health |
| `commit_count`, `author_count` | context | churn has no health direction |
| `words_per_heading` | context | scored briefly, then demoted |
| `internal_link_count` | dropped | counts the wrong direction of the link graph |
| `todo_flag` | tripwire at most | near-constant on both corpora |
| `heading_count`, `heading_max_depth`, `code_fence_count` | dropped | size cluster |

**Normalisation is percentile rank, not min-max.** Every shortlist metric has
outliers — one 2,241-day page, one −65 Flesch page — and min-max lets a single
extreme compress everyone else into a narrow band.

### Two correlation clusters, confirmed on both corpora

Spearman:

* *Page size* — `heading_count`~`word_count` 0.89 k8s / 0.83 docusaurus,
  `code_fence_count`~`word_count` 0.53 / 0.77, plus `heading_max_depth`.
  Weighting these separately triple-counts length. The fence correlation is much
  weaker on kubernetes, so "size" is the shared axis, not an identity.
* *Time exposure* — `author_count`~`commit_count` 0.98 / 0.96,
  `commit_count`~`age_days` 0.81 / 0.79. Older pages accumulate commits, and more
  commits mean more distinct authors. None of this measures maintenance.

The escape route from a cluster is a **ratio**: composition rather than quantity.
`code_block_density` does this for fences.

### todo_flag carries no weight

0/176 on kubernetes, 2/94 on docusaurus. Checked that this was not a pattern
miss: a wider net (`TODO|WIP|FIXME|XXX|TBD`) over all 176 concept pages finds one
hit, `ip-XXX-XXX-XX-XX` in example output — a redacted hostname, not a marker.
Both docusaurus hits are on live pages (`plugin-content-docs.mdx`: 80 commits,
updated 33 days ago), which suggests a TODO marks work in progress rather than
neglect.

### internal_link_count — dropped entirely

The reason is definitional and worth remembering: **the column counts outbound
links, but "stranded" is an inbound property.** Zero outbound is a dead end; zero
inbound is an orphan. They are opposite directions in the link graph.

* The top end is ordinary pages that happen to link a lot — more links is not
  healthier, so that end carries no signal.
* The bottom end (0 links) is leaf reference pages. Grepping the docs tree for
  inbound references: `create-docusaurus` 9, `plugin-ideal-image` 2,
  `github-pages` 2, `plugin-rsdoctor` 1, `browser-support` 1, `logger` 0. They
  are the destinations of `plugins/overview.mdx` (13 outbound links).
  Hub-and-leaf is healthy architecture, not neglect.
* It is half-inside the size cluster anyway (0.61 / 0.64).

**Considered and not built:** a real orphan metric needs the whole link graph
*plus* `sidebars.ts`, since Docusaurus routes pages through sidebar config as
well as markdown links — so `logger`'s zero inbound greps does not prove it is
orphaned. That is a different tool, not a column.

### commit_count and author_count carry no weight

As raw counts they mostly measure age. A rate-shaped version would decorrelate
them, but that was not the blocker — **churn is not a health direction.** A
high-churn page may be lovingly maintained or chronically unstable, and the
metric cannot tell those apart, so neither tail can be scored as good or bad.

The conclusion held on re-examination, but two of its original reasons were
wrong:

* The rate-shaped version was tested rather than predicted. It decorrelates from
  age as expected (0.66 → 0.51 docusaurus, 0.64 → 0.37 kubernetes) but **picks up
  page size instead**: `commits_per_year`~`word_count` is +0.75 / +0.74. Long
  pages accumulate more edits per year, so the rate version ranks big pages as
  well-maintained. The first analysis asserted decorrelation and never checked
  what replaced it.
* The churn-has-no-direction argument only ever covered the *high* tail. The low
  tail needs its own reason, and it is **redundancy**: for a single-commit page
  `days_since_update == age_days` by definition, so "written once, never
  revisited, and old" is already fully expressed by two existing columns.
* A low count also cannot separate "never revisited" from "recently created" —
  `deployment/github-pages.mdx` is 1 commit at 61 days old,
  `topology-aware-scheduling.md` is 2 commits at 41 days stale.

### code_block_density carries no weight

Checked the extremes on kubernetes by reading them: nothing unusual at either
end. High-density pages are genuinely code-heavy concept pages doing their job;
low-density pages are ordinary prose. Neither tail marks a page as unhealthy.

Its two real jobs stand: half of the not-a-doc-page detector, and the context
that makes thinness interpretable — thin plus fences is a reference page, thin
with no fences is not a doc page.

---

## Three species of page

Reading the outlier pages produced the finding that shapes everything
downstream: there are three kinds of page in a docs corpus, not one.

1. **Prose pages** — the metrics mean what they say.
2. **Code-reference pages** — thin in words by construction, because their
   content is code. The docusaurus thin tail is entirely these (theme and plugin
   install/config pages). **Thinness is not neglect** — the exact twin of the
   staleness caveat. This is why `word_count` gets no weight: a naive thin-page
   penalty would push healthy reference pages to the top of the fix-first list.
3. **Not-doc-pages** — navigation stubs, section indexes and include fragments,
   where the metrics produce garbage rather than a verdict.

### The not-a-doc-page detector: low prose AND low code

Derived from the reading, not assumed. Code-reference pages are thin in words but
dense in fences, so they pass; navigation stubs and fragments are thin in both,
so they trip.

`word_count < 150 & code_fence_count == 0`. The AND is the whole design:
`theme-mermaid.mdx` (23 words) has *fewer words* than `concepts/_index.md` (33
words), which must be caught — so no word threshold alone separates them, and the
fence count does the work. Every must-catch page has zero fences.

**150 sits inside a gap in both corpora** — 110→223 docusaurus, 142→161
kubernetes — so anything in 143–160 gives the same answer. Kubernetes is the
binding constraint: 69 of 176 pages have zero fences, 9 of them under 150 words.
Result: docusaurus 89 scored / 5 not, kubernetes 167 / 9.

**Two false positives, known and accepted.** Every catch was read, not judged by
filename. Correctly caught: `plugins/overview.mdx` and `themes/overview.mdx`
(bulleted lists of links), `scheduling-eviction/_index.md` (one intro paragraph
then a link list). Wrongly caught: `deployment/vercel.mdx` (110w — a complete
deployment guide, short because it delegates to Vercel's own docs) and
`security/linux-security.md` (118w — real guidance with subheadings).

Dropping the cut to ~105 rescues both but admits three section indexes
(124/129/142w) into the scored set. **150 is kept**, because the safeguard only
runs one way: the visible not-scored list catches false *exclusions*, whereas
nothing protects the fix-first list from a section index sitting at the top of
it.

**A short real page and a section index have the same content shape** — thin,
prose-only, no fences. Nothing in the current columns separates them. Link
density was the obvious candidate and **fails on measurement**:
`api-extension/_index.md` (a section index) scores 1.4 links per 100 words,
sitting directly beside `linux-security.md` (a real page) at 2.5. Separating them
would need something not currently extracted — what share of the body is list
items, or a link-text-to-prose ratio. Not worth a column until something depends
on it.

**The extractor was checked, not blamed.** The thin counts looked low enough to
suspect undercounting on link-heavy pages. `wc -w` against `word_count`:
138→118, 123→110, 85→68. The gaps are frontmatter, HTML comments and link syntax
being stripped — correct behaviour. The pages really are that short.

### Filename conventions are per-corpus, and the same character means opposite things

`_` means "partial, never rendered" in Docusaurus and "section index" in Hugo,
and **15 of kubernetes' 21 underscore files carry real content** —
`workloads/pods/_index.md` is 3,176 words. A shared default would have deleted
most of the corpus.

So `non_page_prefix` is a config key, applied by the extractor, with kubernetes
deliberately omitting it. Verified after re-extraction: kubernetes has 21
underscore files and flags **0**; docusaurus flags **2 of 2**, exactly its two
include fragments.

The row is **marked, never dropped**. A human reading the excluded list is the
check that nothing real got filed as navigation.

---

## Parser correctness

Every parser bug in this project was caught by an outlier that looked wrong when
read — never by an error, and never by a test that did not already exist. That is
the argument for exploring distributions before scoring anything.

### The markdown that survived the first pass

* **`{/* ... */}` MDX comments survived `to_prose`** — 85 of 94 docusaurus pages,
  836 occurrences, ~4% of corpus prose words. Fixed in `strip_jsx`, so it stays
  gated on `.mdx`. Measured: 0 occurrences in `.md` files, all 836 in `.mdx`.
  *Known assumption:* Docusaurus 3 parses `.md` as MDX too, so a `.md` file could
  legally carry this syntax; this corpus does not. It must be its own `re.sub` —
  the import/export pattern relies on MULTILINE `.+$`, which under DOTALL would
  swallow the rest of the file.
* **`:::` admonition markers survived** — 76 of 94 pages. Now stripped in
  `to_prose` (core: kubernetes has zero occurrences, so it is harmless there).
  Two subtleties, both pinned by tests: the *title* after a marker
  (`:::warning production only`) is reader-visible and is kept, and a marker must
  start its own line, because the admonitions page discusses `:::warning`
  mid-sentence as ordinary prose.
* **Four-backtick fences defeated the code-block regex** — 42 fence lines of 4+
  backticks across 6 pages. Docusaurus wraps fences in ` ````mdx-code-block `,
  and a lazy match pairs the outer opener with the *inner* opener, leaking the
  code body into prose. Fixed with a backreference so the closer must match the
  opener's width.
* **Fences may be indented.** Kubernetes has 206 indented fence lines inside list
  items. The old pattern caught them only by accident.
* **`code_fence_count` was counting lines, not blocks.** `^```(.*)$` with `[0::2]`
  pairing missed every indented fence and mis-paired everything after a wide
  fence: `deployment.md` counted 23 fences against a real 95. Totals moved
  kubernetes 619 → 722 (13 pages) and docusaurus 1013 → 1000 (5 pages) — both
  directions are corrections. The fence definition now lives in one place,
  `FENCE_BLOCK_RE`, shared by every consumer so they cannot disagree about what a
  fence is.

Net effect on docusaurus: `word_count` 67,776 → 65,925 (−2.7%); negative-Flesch
pages 4 → 2; `markdown-features-math-equations.mdx` −3.2 → **51.2**;
`plugin-content-docs.mdx` −10.2 → **+5.5**.

**Not fixed:** LaTeX `$…$` math still counts as words. Rendered math is arguably
reader-visible, and the one affected page now scores fine.

### Raw HTML in .md was corrupting readability and word count

`pod-security-standards.md` scored **−29 off 117 sentences**, so it was not a
small-sample artifact. Cause: Hugo permits raw HTML in `.md`, and `strip_jsx` is
gated to `.mdx` — an assumption that held for docusaurus and does not for
kubernetes. 1,067 surviving tags formed a single 5,761-character "sentence" of
`<p><ul><li><code>` with no terminating punctuation. **The page now reads 34.61**
— its prose was always unremarkable; the score was measuring markup.

**Applying `strip_jsx` to `.md` is the wrong fix, twice over.** It matches only
*capitalised* tags, so it leaves the lowercase HTML untouched — the score stays
at −29.00 exactly. And it would delete `<IPv4>` ×5, `<IPv6>` ×5, `<KEY>` ×4,
`<NodeIP>` ×2, `<SUBMOUNT>` ×2, `<CONTAINER_NAME>` ×1, which are placeholders in
running prose.

**The fix is a whitelist, and a short one on purpose.** `strip_html` removes only
named lowercase tags, keeping their text — unlike a table, a tag is not content.
A whitelist taken from the HTML spec would be *worse*: `docusaurus.config.js.mdx`
documents `<link>`, `<head>`, `<script>`, `<meta>` and `<html>` as its subject
matter, and a spec-complete list would delete the content of the page about them.

**Ordering matters:** table blocks must be removed before tag stripping, or the
`<table>` markers are gone before the block matcher looks for them. The two
branches then diverge — `word_count` keeps table text and loses tags, readability
loses both.

Scope, measured against the pre-fix CSVs:

| | pages with Flesch changed | max change |
|---|---|---|
| docusaurus | 17 | 1.28 |
| kubernetes | 8 | **63.61** |

Effectively one page. `word_count` moved on 20 pages (max 219, same page), and
**no page crossed the 150-word detector boundary**, so the species split is
untouched. Pages with no reading stayed at 11 and 5 — the fix corrects scores, it
does not change who gets one.

### Inline code is not prose

It was **two defects pulling in opposite directions**, which is why the aggregate
looked mild:

* *Syllables inflated.* `PodDisruptionBudget` scored as ordinary vocabulary —
  seven syllables of API name, pushing the score down.
* *Sentences fragmented.* **20% of kubernetes' 7,131 inline spans and 15% of
  docusaurus' 3,799 contain a period**, and textstat treats every `.` as a
  sentence end. A real docusaurus sentence — "Set the `siteConfig.themeConfig.navbar`
  value in `docusaurus.config.js` to enable it." — counted as **three sentences**,
  making the page look far snappier than it is and pushing the score up.

The two partly cancelled, so corpus medians moved only a few points while
individual pages were wrong by up to 41.

**A placeholder, not a deletion — decided by decomposing Flesch into its two
terms rather than by comparing scores.** Both variants raise the score, so the
score alone cannot choose between them:

| (docusaurus medians) | Flesch | words/sentence | syllables/word |
|---|---|---|---|
| as scored before | 48.79 | 16.33 | 1.667 |
| inline deleted | 51.53 | 17.06 | 1.610 |
| **one-syllable placeholder** | **53.44** | **18.12** | **1.588** |

Deletion turns "Set the `x` value in `y` to enable it" into "Set the value in to
enable it" — seven words, ungrammatical, and understating the sentence by
dropping two of its referents. The placeholder keeps the slot, which is why its
words-per-sentence figure is highest: **that figure is the true sentence
length**, previously hidden by the false boundaries. A reader parses a code span
as one item, so a multi-token span collapsing to one token is the intended model.

**The ordering rule is fences BEFORE inline spans, and it has teeth.** A fence's
own backticks are matched by the inline pattern, so running inline stripping on
raw text corrupts them. Two measured failure modes: inline code inside a fence
shifts the backtick pairing by one, so the inline content **escapes into the
prose**; and four-backtick fences pair as two empty spans, the delimiters vanish,
`FENCE_BLOCK_RE` then finds no fence to remove, and **the code body lands in the
readability text as prose**. Verified: swapping the two steps moves a test page
from 95.08 to 80.06.

Results, and the two invariants that had to hold:

| | kubernetes | docusaurus |
|---|---|---|
| median Flesch change | **+3.60** | **+5.02** |
| largest single move | `volume-snapshots.md` 28.42 → 45.96 | `plugin-content-docs.mdx` **5.50 → 46.75** |
| `word_count` changed on | **0 pages** | **0 pages** |
| pages with no reading | 5 → 5 | 11 → 11 |

`word_count` unchanged is the invariant holding: the readability branch strips,
the length branch does not. No page crossing the sentence floor means the fix
corrects scores without changing who gets one.

### The family these belong to

Fence pairing, raw HTML and inline code all reduce to one thing: **textstat
trusts punctuation that is not prose punctuation.** HTML tags gave it a
5,761-character "sentence" with no terminator; dotted identifiers give it three
sentences where there is one. Every one was found by reading a page whose number
looked wrong, and **none of them errored**.

The general rule, earned repeatedly: **a metric with a denominator, and a parser
that does not error, both fail by producing a plausible wrong number.** Whatever
the whitelist holds will be incomplete for the next corpus, so the failure needs
to be made loud — counting surviving `<…>` occurrences after stripping is the
cheap version.

*Caveat:* the sentence-count buckets that validated `MIN_SENTENCES_FOR_FLESCH = 5`
were measured on fragmented sentence counts. The floor's *effect* is unchanged —
the same 5 and 11 pages go unscored — but the knee table would need redoing
before it is quoted again.

---

## Scoring

### Why words per heading is not scored

It was picked on a correlation argument: it escapes the size cluster, 0.40 / 0.55
against `heading_count`'s 0.89 / 0.83. **That argument shows it is *independent*,
never that it measures health.** Reading the pages says it does not, at either
end:

| tail | what is actually there |
|---|---|
| high, unguarded | short one-heading pages. `architecture.mdx` is 261 words / 1 heading = 261; `netlify.mdx` 232/1; `whats-next.mdx` 223/1. For these, `words_per_heading` **is** `word_count` — the ratio degenerates when the denominator is 1 |
| high, ≥3 headings | read by hand: only `taint-and-toleration.md` (2,437w / 7h) is genuinely a wall of text, maybe one other in six |
| low | code-dense reference pages — `plugin-svgr` 43.5% density, `markdown-features-diagrams` 53.2% — plus heavily-headed pages like `markdown-features-toc.mdx` (915w / 45h, because it *demonstrates* headings) |

Not simply page length in disguise: `corr(words_per_heading, word_count)` is 0.20
/ 0.25. It is specifically the small-denominator instability.

**The band-shape problem is therefore dissolved, not solved** — there are not two
bad tails, there are none. A `MIN_HEADINGS_FOR_WPH` floor was designed and then
dropped. Both `words_per_heading` lists were *removed* from the dashboard rather
than unweighted: a list headed "worst pages" that points at healthy ones is worse
than no list. The column survives as context in the Pages table, where it
describes without accusing.

**Candidate replacement, not built:** *longest single section in words*. Not a
ratio, so no denominator instability; it would catch `taint-and-toleration.md`
and would not fire on a 232-word one-heading page or a 45-heading demo page.

### The maintenance-rate metric that failed the same way

Definition tested: commits per 1,000 words per year of life, restricted to pages
older than 365 days. **It decorrelates better than anything else tried** —
`word_count` −0.27 / −0.25, `age_days` +0.13 / +0.24, `days_since_update`
+0.06 / −0.08, `commit_count` +0.01 / +0.19. Genuinely independent information.

Both tails are artifacts anyway:

* **The high tail is the thinnest pages.** `theme-live-codeblock.mdx` (25 words,
  9 commits) scores 63.1, top of the "well maintained" list; `theme-mermaid.mdx`
  (23 words) scores 23.6. `word_count` in the denominator floats tiny pages.
* **The low tail flags recently-updated pages.** `node-autoscaling.md` (1,831w,
  updated **59 days ago**), `resource-managers.md` (3,658w, **127 days**),
  `migration/v3.mdx` (3,861w, **128 days**), all ranked "neglected" because they
  are long. The ~0.0 correlation with `days_since_update` reads as independence
  but means the metric is **blind to whether a page was touched last month** — a
  defect in a maintenance signal, not a virtue.

A `word_count` floor would fix the high tail. Nothing fixes the low tail without
reintroducing recency, at which point it is `days_since_update`. The conceptual
error: *per 1,000 words* assumes maintenance effort scales linearly with length.
**A commit is a unit of attention, not of coverage** — a typo fix and a rewrite
are both one commit.

**The rule this earned, now twice over: any metric with a content-size
denominator must have BOTH tails read before it is trusted.** Correlation tables
cannot detect this — both metrics looked excellent on the correlations and were
artifacts of their denominators.

### Calibrating the staleness floor

`staleness_to_age_ratio` is the answer to "staleness alone is not neglect", and
it carries no weight of its own. A naive score ranked `owners-dependents.md`
(1,677d stale / 1,861d old = **0.90**, untouched for 90% of its life) adjacent to
`controlling-access.md` (1,168d / 3,836d = **0.30**, maintained across its life
and quiet lately). Same score, opposite situations, and the discriminator was
already a column.

**It is a modifier at half strength, not a third input.** Nobody wants a page
fixed *because its ratio is high*, and as an input it would double-count
staleness. The staleness term is scaled to between `STALENESS_MODIFIER_FLOOR` and
1.0 of its percentile rank.

| floor | owners-dependents (0.90) | controlling-access (0.30) | best single-commit page |
|---|---|---|---|
| 1.0 (no modifier) | rank 13 | rank 14 | 23rd |
| **0.5 (chosen)** | **rank 4** | **rank 21** | **13th** |
| 0.0 (pure ratio) | rank 3 | rank 30 | **6th** |

Full strength is rejected on the ratio's own known defect: it is exactly 1.0 for
every single-commit page regardless of age, and those 8 kubernetes pages have a
**median staleness of 166 days** — they are new, not neglected. At floor 0.0 one
reaches 6th; at 0.5 raw staleness still carries half the term and the best
reaches 13th.

**The ratio failed as a score input for precisely the reason it works as a
multiplier.** It was rejected as an input because it erases the difference
between a 61-day-old and an 1,842-day-old single-commit page — both read 1.0. As
a multiplier that is harmless: 1.0 means "no discount", and the age information
comes from the staleness term it is scaling.

### Why there is no composite score

The brief asked for a weighted composite. It was built, read, and dropped.

**The weights turned out to be undecidable *and* decisive** — the worst
combination. The two axes are uncorrelated (−0.08 docusaurus, +0.16 kubernetes),
so the weight does not fine-tune the ranking, it *is* the ranking:

| | docusaurus | kubernetes |
|---|---|---|
| pages in the top 10 at *some* weight | 24 | 21 |
| pages in the top 10 at **every** weight | **0** | 2 |

Nothing in the data can choose between them. Every other decision here was
settled by reading pages, because a metric makes a factual claim you can check.
**A weight makes no factual claim** — it encodes how much being out-of-date costs
relative to being hard to read, and no correlation contains that. The only
empirical route would be hand-labelling a ground truth set and fitting to it;
that is a different project.

**And the payoff was never there.** The inputs are percentile ranks *within a
corpus*, so the corpus-level mean is pinned near 0.5 by construction:
**docusaurus 0.420, kubernetes 0.416** — two unrelated documentation sets,
effectively the same number. It cannot say "these docs are healthy", cannot
compare corpora, and cannot trend over time — fix your worst 20 pages and the
percentiles redistribute back. The one appealing thing about a single score was
mathematically unavailable from the start.

**What replaced it, and the one real loss.** Separate per-axis lists surface only
extremes, so a page moderately bad at everything was invisible in both — 6
kubernetes and 2 docusaurus pages sat in that gap, including
`application-security-checklist.md` (0.71 stale / 0.90 read), worse on *both*
counts than pages that did appear. The **consistently poor** list recovers them
with no weight: membership is "in the worst quartile of every axis", ordering is
`min` across axes, so bad-at-everything outranks terrible-at-one-thing. The cut
is each axis' own quantile, not a literal 0.75, because the staleness modifier
means that column no longer spans 0..1.

**Membership is boundary-sensitive, and the count must be recomputed rather than
quoted.** It has moved twice for known causes — once when the raw-HTML fix
reshuffled readability percentiles, once when the inline-code fix raised the
docusaurus median 5 points. Two pages currently sit within 0.015 of a cut.

A threshold is a far weaker commitment than a weight: it decides who is on a
short list you can read in full, not the order of 167 pages.

### A partial row cannot outrank anything, once there is no composite

With two inputs, a page missing readability would be scored on staleness *alone*
under a composite — renormalising hands its one remaining axis 100% of the
weight, so the stalest unmeasurable page tops the list automatically. **The top
four docusaurus pages were all NaN-readability rows**, at 0.97/0.97/0.93/0.92.

9 of 89 scored docusaurus pages (10%) have no reading; kubernetes has 0, because
its five such pages were all excluded as non-pages. **So the defect was invisible
in one corpus and dominated the other** — the argument for never tuning against a
single corpus, again.

A dual-list scheme was designed and built for this, then deleted when the
composite went: a partial row now simply appears in whichever per-axis lists it
can be measured on, and is excluded from the consistently-poor list because
consistency cannot be established from one reading. Recorded because the
reasoning stands if a composite ever returns — and because this is the *second*
problem here to dissolve rather than be solved. Both dissolved when the thing
that created them was removed.

### The readability floor is validated at 5 sentences

Chosen as a sensible-looking number and never checked, so it was checked.
Recomputing every page with the floor removed shows the spread by sample size:

| sentences | pages | std dev |
|---|---|---|
| 1 | 5 | 19.8 |
| 2 | 3 | 14.6 |
| **3–4** | 8 | **37.9** |
| **5–9** | 18 | **14.0** |
| 10–19 | 35 | 13.0 |
| 20–49 | 96 | 9.9 |
| 50+ | 105 | 10.8 |

The 3–4 bucket is the widest in either corpus and contains a −56.9 reading. At 5
the spread halves to 14.0, already level with 10–19. **The knee is exactly where
the threshold was put.** Raising it to 10 would cost 18 more pages their reading
to buy 14.0 → 13.0. Caveat: the low buckets hold 3–8 pages, so those figures are
themselves noisy — the direction is solid, the precision is not.

**The guard is about sample size, not about filtering out non-prose**, which is
the opposite of what was expected. Kubernetes' worst page,
`security/pod-security-standards.md`, scored −29 off 1,779 words and 117
sentences — about 15 words per sentence, entirely normal, and driven by genuinely
dense policy vocabulary. That is a real measurement and must be kept.

### A defect can be invisible from the top of the list

The `words_per_heading` band problem was predicted to misrank skeleton pages, but
under a linear direction a low value scores *well* — so it was a false negative,
not a mis-rank. Reading a top-N is a real check but not a complete one: it cannot
find what the score fails to surface.

---

## Median line age

`days_since_update` says when the FILE was last touched, and a typo fix resets
it. `median_line_age_days`, from `git blame -w`, says **how old the CONTENT is**.
That is the "staleness is not neglect" risk attacked from the opposite side, and
it is the metric `days_since_update` had been standing in for all along.

**It measures something the corpus did not already contain.** Spearman against
existing columns, on scored pages:

| | kubernetes | docusaurus |
|---|---|---|
| `days_since_update` | **+0.30** | **+0.11** |
| `word_count` | **+0.10** | **+0.25** |
| `age_days` | +0.66 | +0.60 |
| `commit_count` | +0.56 | +0.46 |

The `word_count` row is the one that matters: it **escapes the size cluster**,
which is where `words_per_heading` and the commits-per-1,000-words rate both
died. The pages it catches are the point — `replicationcontroller.md` was edited
**95 days ago** and its median line is **3,422 days** old; `plugin-content-docs.mdx`
38 days against 1,824. Pages that look maintained and are not.

**Cost, measured before building: ~50s for 176 pages.** One `git blame` per file
on top of the existing `git log` per file — extraction went 2:47 to 3:33.

**`-w`, and why it is not enough.** `git blame -w` ignores whitespace-only
changes, so a reformatting pass does not reset every line's age. But a *genuine*
mechanical pass — a link-scheme migration, a shortcode rename — still rewrites
line ages wholesale, and blame's only answer is `--ignore-rev`, which needs the
offending SHAs identified per repo. Know this before trusting the number on a
corpus nobody has read.

**A free invariant worth keeping: the gap is never negative.** The median line
cannot be newer than the newest commit, so `median_line_age_days >=
days_since_update` on every row — measured 0..3,327 on kubernetes and 0..1,969 on
docusaurus, with zero violations. That is a cheap check on a sign error or a
timezone slip. `datetime.fromtimestamp` needs `timezone.utc` passed explicitly or
it returns naive local time and the subtraction raises.

**It is deliberately not a score axis.** It has the best correlations of any
candidate so far, and that is exactly the evidence not to trust:
`words_per_heading` and the maintenance rate *both* looked excellent on a
correlation table and were artefacts of their denominators. **Both tails must be
read first.** Until then it is a column in the Pages table and a labelled list on
the dashboard — visible, unweighted, and honest about its status.

---

## What `--follow` trades away

`--follow` is load-bearing: without it `git log` stops at the commit that moved a
file, and everything older is invisible — 6 commits instead of 147 on the
horizontal-pod-autoscale page. What it costs is merge commits.

Measured across all 176 kubernetes pages:

| | |
|---|---|
| pages whose plain `git log` shows merge commits | 83 of 176 |
| merge commits dropped by `--follow` | 640 |
| of those, holding lines present in **no** parent | **6**, across 4 pages |
| of those 6, newest commit on their page | **0** |

Git's default history simplification only *shows* a merge when the file differs
from every parent, so "differs from every parent" is not evidence of anything —
it is the selection rule. The question that matters is whether a merge holds
lines found in no parent, which is content typed during conflict resolution and
reachable nowhere else. Six do, 1–5 lines each.

**No metric is wrong today.** `days_since_update` is unaffected because no
dropped merge is the newest commit on its page — the closest is 2026-02-24
against a visible 2026-06-03. `age_days` is unaffected because creation is the
oldest commit and a merge is never that. `commit_count` and `author_count` are
undercounted by 1–3 on 4 of 176 pages.

Note that `git blame` **does** attribute those lines, so blame and log genuinely
disagree about roughly 10 lines in this corpus. The guarantee here is a property
of how kubernetes merges, not of git — a corpus that resolves conflicts by
writing new prose in the merge would lose more.

---

## CLI and packaging

### What went into the package, and what deliberately did not

`src/dochealth/` holds `extract`, `app`, `cli`. Configs stayed out for a strong
reason: **a config is an input the user supplies, which is the entire meaning of
`--config <path>`.** Shipping `kubernetes_config.py` inside the package would
contradict the finding that `noise_re` has no correct default. They are example
configs that happen to live in this repo.

`extract.py`'s `if __name__ == "__main__":` block — which hardcoded both corpora
in a list of (name, repo_dir, docs_dir, config) tuples — was deleted, because
those four fields are exactly the CLI's arguments. The module is now
import-side-effect-free and the CLI owns the entry point.

### --config and --no-config are a required mutually exclusive group

An optional config degrades silently into wrong-but-plausible numbers: the
docusaurus frame was first built configless, returned a full healthy-looking
DataFrame, and was wrong on most pages' `days_since_update` — caught only because
`describe()` happened to show a suspicious median.

The fix is three lines and **no `if` statement**:

    group = extract_parser.add_mutually_exclusive_group(required=True)

`required=True` rejects neither flag; the group rejects both. Passing neither
exits 2 with `one of the arguments --config --no-config is required`, and the
constraint is documented in the tool's own usage string. Configlessness is still
permitted — it has to be, kubernetes genuinely wants no filter — but it can no
longer happen by accident, and it prints a warning to stderr when chosen.

Four tests pin this, because it is a *decision* rather than a behaviour and is
exactly the kind of constraint someone simplifies away later without realising
what it was load-bearing for.

### --out is required with no default

Considered and rejected: default to stdout (the Unix-composable choice), and
default to a filename. The deciding fact is that extraction takes minutes, so
forgetting to redirect costs a full rerun, and the CSV is an artifact the
dashboard reads back rather than an intermediate value in a pipeline. `--out -`
remains available later if piping ever earns its place.

Everything that is not the data goes to **stderr** — the row count, the no-config
warning — so that redirect stays clean either way.

### Verified by re-extracting, not by reading the diff

| columns | result |
|---|---|
| all 15 content metrics | identical |
| `extracted_at`, `days_since_update`, `days_since_update_raw`, `age_days` | differ |
| `staleness_to_age_ratio` | 32 pages, ±0.01 |

The CLI is a new front door onto the same extractor, not a reimplementation. The
date columns differ by 2 *or* 3 days, three days after the CSV was generated —
which is the extraction-time date question showing up live rather than in the
abstract.

Runtime correction while there: **2m47s for 176 pages**, not the ~68s recorded
before `--follow` and `--numstat` were added.

### Tunable judgments at analysis time, fixed conventions at extraction time

This is the line that decides where any new rule goes. Changing `non_page_prefix`
costs a full re-extract, which is a real objection — but `_` is a fixed property
of Hugo and Docusaurus, not a threshold anyone will tune. `flag_by_shape`'s
150-word cut stays in the dashboard on exactly this test. `REPO_URLS` also stays:
it is presentation, not measurement.

The deciding argument for putting the filename rule in the extractor was that **a
dashboard reading an optional config reintroduces the silent-degradation failure
one layer up** — a dashboard opened without one would render a complete,
plausible screen that silently misclassified every partial. Writing the verdict
at extraction time makes the CSV self-describing instead, and `exclusion_reason`
raises rather than defaulting when the column is absent.

### dochealth dashboard

`sys.executable -m streamlit` rather than a bare `streamlit`, so PATH cannot
substitute another environment's Streamlit for this one's, and Streamlit's exit
code is returned rather than a bare 0.

It takes no arguments: the dashboard reads every `metrics-*.csv` in the working
directory and switches between them in its own sidebar. A consequence worth
knowing — argparse rejects unknown arguments, so Streamlit's own options are not
forwarded; use the `STREAMLIT_*` environment variables, or add passthrough with
`parse_known_args` if it ever earns it.

`streamlit` stays an optional extra, and the subcommand checks for it with
`find_spec` to give an install hint rather than a traceback: `dochealth extract`
never imports it, so a CSV-only install is legitimate.

---

## The dashboard

### Filters are applied after ranking, never before

A percentile is a comparison and the comparison set has to be the corpus. Filter
first and the least-stale page in a twelve-page folder ranks 0.00 and reads as
healthy at 800 days — the composite score's defect in miniature, a number that
silently redefines itself when the view changes. Filtered views say so, and the
counts show "16 of 176".

### Distributions were built first and were the wrong form

Read back, the histograms answered "is this corpus plausible" but not "what do I
fix". Replaced by ranked lists, with the distributions moved into an expander
where calibrating whether a tail is long and thin or a cliff is a job they
actually do.

### Four views, each drawing a claim the tables only stated

* **By section** — the same metrics re-cut by directory. No new measurement, but
  it answers "where does a week of effort go" rather than "which page do I edit",
  and a median over 14 pages cannot be dragged by one outlier. Kubernetes
  `security` is stalest **and** hardest to read (492d median, 34.97);
  `hardening-guide` is the opposite shape — worst readability in the corpus at
  30.65 but only 179 days stale. No minimum-pages floor: there is no measured gap
  to put one at, so every section shows with its page count first.
* **Readability against the published scale** — equal-width 10-point bins with
  the published bands as shaded background. **The first thing in this dashboard
  that can compare corpora**, because the bands are external and fixed. Result:
  neither corpus contains a page easier than "standard", and kubernetes reads a
  full band harder than docusaurus.
* **Staleness against age** — every page against its own lifetime with the y=x
  diagonal drawn. The diagonal **is** ratio 1.0, and distance below it is how
  much of its life a page was maintained. It also shows why the ratio is a
  modifier and not an input: kubernetes' 8 diagonal pages have a median age of
  170 days (new, not neglected) while docusaurus' 9 sit at 625 (genuinely
  forgotten).
* **Bus factor** — single-author pages, most content first. **Not a quality
  signal**, and captioned as such. It asks a different question: how much of the
  corpus has no second reader. `author_count` is a lifetime count, so the table
  shows Age and Stale beside it — docusaurus' top hit is 1,580 words and one
  commit at **65 days old**, which is new, not abandoned.

### Page detail, after row selection failed as an interface

Every metric for one page, each shown *against the corpus* — "73% of pages score
lower" — because a bare 46 Flesch is the uninterpretable number this project
keeps refusing to ship. Excluded pages get the same panel: their measurements are
real, they just have no population to rank against.

**The instructive part is the first attempt.** Row selection was wired to both
tables, fully tested, `selection_mode=[0]` confirmed on the rendered protos — and
unusable, because Streamlit selects rows via a checkbox in the leftmost column
and that column is a `LinkColumn`. The obvious click went to GitHub; the working
affordance was invisible. **Every check that could be automated passed, and the
feature did not work.**

### A cache that served stale numbers

`@st.cache_data` keys on the ARGUMENTS, so `load_metrics(path)` served the
DataFrame it read at startup forever; re-extracting under a live app raised
`KeyError: median_line_age_days`. Fixed by passing the file's mtime as a second
argument, unused in the body.

`extracted_at` exists precisely so a stalled refresh cannot quietly serve stale
numbers — an unkeyed cache is a second route to exactly that, **and a worse one**:
it only errored because the schema happened to change too. With the same columns
it would have shown a screenful of plausible wrong numbers.

### Four things were built and then deleted, all for one reason

* *A narrative summary*, ported from elsewhere: every line restated a KPI
  directly above or a table directly below. It works in its original home because
  it lives in an Overview **tab**, never adjacent to what it summarises. **Port
  the layout or do not port the component.**
* *`best_axis_rank` as a column* — three names and a tooltip and it still needed
  explaining. The list's *membership* is the message; the ordering is a
  refinement nobody acts on, and on docusaurus it is one row.
* *Row selection* (above).
* *`PATH_COLUMNS`* — a two-element constant used once, whose `path` half was
  selected into the frame and silently discarded.

The pattern: **the thing that needed explaining was the thing that should not
have been there.**

### Streamlit facts worth not rediscovering

The script re-runs top to bottom on every interaction; there is no callback
model. Passing a pandas `Styler` to `st.dataframe` does **not** cost column
sorting (checked by hand, streamlit 1.61) and `column_config` survives alongside
it — so `na_rep="not measured"` makes an unmeasured reading read differently from
a bad score, at no cost.

`SCORE_INPUTS` is derived from `DIRECTIONS` rather than maintained twice. That
matters beyond tidiness: the scored/partial split derives from `SCORE_INPUTS` so
that "add a fourth metric and rows re-sort themselves" stays true, and a second
hand-kept copy quietly breaks that promise, since forgetting `DIRECTIONS` would
give a new column a rank with no direction applied.

---

## Testing

### A dashboard that boots is not a dashboard that works

Moving `app.py` into `src/dochealth/` broke `CORPORA`, which globbed
`metrics-*.csv` relative to `Path(__file__).parent`. The glob matched nothing and
the app died on `KeyError: None` — an empty dict indexed with the `None` an
optionless selectbox returns.

**The check that missed it was `curl` returning HTTP 200.** Streamlit serves the
page shell over HTTP and does not execute the script until a websocket client
connects, so a server that boots proves nothing about a script that runs. Same
species as "a surviving tag does not error": a failure that produces a plausible
non-failure.

Three fixes, and only the first is the bug:

* The glob is anchored to `Path.cwd()`. `__file__` was always wrong here — the
  CSVs are **user data**, written wherever `--out` pointed. It only worked while
  `app.py` happened to sit beside them. `app.py` itself *is* resolved from
  `__file__` by `dochealth dashboard`, because the app ships in the package and
  the data does not.
* An empty `CORPORA` now names the directory it searched and the command that
  would produce a CSV.
* `test_app.py` executes the script and asserts on what it rendered.

### Pure is not the same as importable

The dashboard's detectors were free of `st.` calls from the start, which was
supposed to make them testable. It did not: they could not be *imported*,
because a Streamlit script runs top to bottom on import, so
`from dochealth.app import exclusion_reason` rendered the whole dashboard and
died on a sidebar widget returning `None`. **Purity is not enough for
testability when the module has import-time side effects.**

Worked around first, with `streamlit.testing.v1.AppTest` — which runs the script
properly and exposes what it rendered, and is still how the dashboard itself is
tested. But asserting on rendered output can only tell you *that* a split
changed, never which rule changed or why.

Fixed properly by moving them to `scoring.py`: `flag_by_shape`,
`exclusion_reason`, `add_percentile_ranks`, `directed_ranks` and
`consistently_poor`, along with the thresholds they read. Nothing about the
rules changed — the dashboard's output is identical — but each judgment now has
a direct unit test in `test_scoring.py` rather than a rendering assertion.

The line the split draws is **what the dashboard measures against how it draws**.
Thresholds and ranking rules moved; bands, colours, link formats and column
labels stayed, because changing one of those cannot alter which page is judged
worse than another.

### Assert what must be true, not what happens to be true

All three of the original dashboard tests broke at least once on a change that
removed nothing: an exact corpora list (broken by generating a second docusaurus
CSV, which is normal), an exact section order (broken by a rename), and a heading
level (broken by demoting a header to two subheaders). Each is now a presence
check — the same error as the `curl` check, three more times.

---

## Shelved

Recorded so the reasoning does not have to be rediscovered. None of these block
a usable tool.

### Diátaxis type labels

**Near-constant on the primary corpus, whichever source you use.** The scope is
the cause: `content/en/docs/concepts` is one Diátaxis type almost by definition.
By a path-segment heuristic, 176/176 `explanation`. By kubernetes' own
`content_type` frontmatter: 160 `concept`, 1 `tutorial`, 15 with no value —
still one label on 91% of pages. Docusaurus does vary (reference 35, how-to 23,
36 unmatched by path).

Two ways to make it worth having: demonstrate it on docusaurus, or widen the
kubernetes scope to the whole of `content/en/docs` — 1,674 pages with a genuinely
mixed distribution, but roughly a 20-minute extraction.

**It is not an extractor column.** A generated label is a first draft requiring
manual review, and this project's line is that measurements are computed and
judgments are supplied. It belongs in a separate two-column CSV merged on `path`
at analysis time.

**It is not a score input.** It is a *segmentation*: does the corpus have the
right mix, and is staleness concentrated in one type? A stale reference page and
a stale tutorial are different problems.

**It is config-shaped, exactly like `noise_re`, and inverted.** Kubernetes tags
pages with Hugo's `content_type:` (161 of 176 scoped pages; across the full tree:
`feature_gate` 465, `concept` 270, `task` 184, `api_reference` 156,
`tool-reference` 142, `tutorial` 29, `reference` 28, `custom` 1, and 399 with
none). Docusaurus has no content-type field at all. There, docusaurus had the
structured field and kubernetes wrote free-form English; here it is the other way
round.

**The kubernetes vocabulary is not Diátaxis and mapping it is a judgment.**
`concept → explanation` and `task → how-to` are clear enough, but `feature_gate`
— the single largest category at 465 pages — has no obvious Diátaxis home, and
`api_reference` / `tool-reference` / `reference` are three spellings of one type.
Data-quality note: the values are inconsistently quoted in the source
(`"api_reference"` and `"reference"` alongside bare `reference`).

### Recorded elsewhere in this document

* **Inbound-link / orphan metric** — needs the full link graph *plus*
  `sidebars.ts`. A different tool, not a column.
* **`example_patterns` config key** — agreed as config, never wired up.
* **Distinct authors in the last N years** — the one maintenance question that
  survived. "Has anyone with judgment looked at this recently?" is genuinely
  different from recency, and nothing in the current columns answers it:
  `author_count` is `commit_count` wearing a different name (0.98 / 0.96). The
  commit dates are already parsed, so it is cheap — and it must be validated by
  *reading pages*, not by a correlation table, which is exactly what failed to
  catch the last two candidates.
* **Longest single section in words** — the metric that would actually catch a
  wall of text, without `words_per_heading`'s denominator problem.
