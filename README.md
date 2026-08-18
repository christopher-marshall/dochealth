# dochealth

Per-page documentation health metrics for docs-as-code repos.

Point it at a git repository and a docs directory. It walks every `.md` and
`.mdx` page, reads the git history and the prose, and writes one CSV row per
page — how stale the page is, how old its content is as opposed to its file, how
readable it is, how much of it is code, and who has touched it. A Streamlit
dashboard reads those CSVs back.

It is deliberately a measuring instrument rather than a grader. **There is no
single health score**, because the weights turned out to be undecidable and
decisive at the same time, and a mean of percentile ranks says the same thing
about every corpus. The reasoning, with the numbers behind it, is in
[DECISIONS.md](DECISIONS.md).

## Install

    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dashboard]"

Editable (`-e`) so the `dochealth` command always runs the working tree.
`[dashboard]` pulls Streamlit, which `dochealth extract` does not import — a
CSV-only install is legitimate and does not need a web framework.

## Usage

    dochealth extract <repo> <docs_dir> (--config FILE | --no-config) --out FILE
    dochealth dashboard

For example, against a clone of kubernetes/website:

    dochealth extract website content/en/docs/concepts \
        --config kubernetes_config.py --out metrics-kubernetes.csv
    dochealth dashboard

One of `--config` or `--no-config` is **required**, and that is not an
oversight — an optional config degrades silently into wrong-but-plausible
numbers, which is the failure mode this whole project is organised against.
`--out` is likewise required: extraction takes minutes, and forgetting to
redirect costs a full rerun.

`dashboard` takes no arguments. It opens every `metrics-*.csv` in the **working
directory**, so run it from wherever `--out` put them.

## Configuration

A config is a Python file defining a `CONFIG` dict. Two example configs ship in
this repo, and between them they use two keys:

```python
import re

CONFIG = {
    # Commits whose subject matches are ignored when dating a page.
    # Only for repos with a reliable convention — see DECISIONS.md.
    "noise_re": re.compile(r"^chore(\(.+\))?:"),
    # Filename prefix marking a file as a fragment rather than a page.
    "non_page_prefix": "_",
}
```

Both keys are optional and both are deliberately without defaults. A default
`noise_re` is *wrong* on a repo with free-form commit messages, and a default
`non_page_prefix` deletes real content on a Hugo site, where a leading
underscore means "section index" rather than "partial".

## What it measures

One row per page. Columns worth knowing:

| column | meaning |
|---|---|
| `days_since_update` | days since the last commit that actually changed lines |
| `median_line_age_days` | age of the page's median line, from `git blame -w`. A typo fix resets `days_since_update` but not this |
| `age_days` | days since the page was created |
| `staleness_to_age_ratio` | 1.0 means untouched since it was written |
| `flesch_reading_ease` | higher is easier. `None` below a five-sentence floor, because the score is an average over sentences |
| `word_count` | reader-visible prose only: frontmatter, code fences, HTML and link URLs are stripped |
| `code_fence_count`, `code_block_density` | fences, and fences per 1,000 words |
| `heading_count`, `heading_max_depth`, `words_per_heading` | structure |
| `internal_link_count`, `todo_flag` | context; neither carries weight |
| `commit_count`, `author_count` | lifetime history counts |
| `non_page_by_convention` | set by the extractor from the config's `non_page_prefix` |
| `extracted_at` | so a stalled refresh cannot quietly serve stale numbers as current |

Two of these are scored: `days_since_update`, modified by
`staleness_to_age_ratio`, and `flesch_reading_ease`. Everything else is context
or feeds the not-a-doc-page detector. Pages too thin in **both** prose and code
are not scored at all and are listed separately, because a navigation stub and
an abandoned page are not the same finding.

## The dashboard

`dochealth dashboard` opens five tabs: an overview with per-axis worst lists and
readability against the published Flesch bands; staleness and authorship, with
every page plotted against its own lifetime; a per-page detail panel that places
each metric against the corpus; the full table; and the pages that were not
scored, with a reason for each.

Filters narrow what you look at, never what a page is measured against —
percentiles are always computed over the whole corpus.

## Testing

    python -m pytest        # ~7s, the parsers, the rules and the dashboard
    python check.py         # ~3min, end-to-end against a real clone

`test_extract.py` holds one case per parsing edge case found while building
this. `test_scoring.py` pins the detection and ranking rules — the thin-page
cut, the staleness modifier, what makes a page consistently poor — each of which
is a judgment with evidence behind it in DECISIONS.md. `test_cli.py` does the
same for the command line, where several cases pin a *decision* rather than a
bug. `test_app.py` executes the dashboard with `streamlit.testing.v1.AppTest`
and asserts on what it rendered, because a dashboard that boots is not a
dashboard that works.

`check.py` needs a real clone with real history, which pytest fixtures cannot
stand in for — it verifies rename-following, one `git blame` timestamp per line
of a file, and the noise-filter invariants against live data.

## Why the design is the way it is

[DECISIONS.md](DECISIONS.md) records what was measured, what was rejected, and
what was built and then deleted. Some entries worth reading before changing a
metric:

* [Why there is no composite score](DECISIONS.md#why-there-is-no-composite-score)
* [Calibrating the staleness floor](DECISIONS.md#calibrating-the-staleness-floor)
* [Why words per heading is not scored](DECISIONS.md#why-words-per-heading-is-not-scored)
* [What `--follow` trades away](DECISIONS.md#what---follow-trades-away)
* [What earns a config key](DECISIONS.md#what-earns-a-config-key)

## Reproducing the corpora

The two test corpora are not committed. Clone them into the repo root:

    git clone https://github.com/kubernetes/website.git
    git clone https://github.com/facebook/docusaurus.git

Then extract with the matching config — `website` with
`content/en/docs/concepts`, `docusaurus` with `website/docs`.
