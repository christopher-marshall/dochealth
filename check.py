"""End-to-end verification against a real clone — run `python check.py`.

pytest covers the pure parsers with fixtures. This covers what a fixture cannot:
the subprocess boundary against real git history, where the answers are only
knowable from a repository that has actually been renamed, merged and reformatted
over ten years. Every assertion below is pinned to a known page in
kubernetes/website, and each failure message names the likely cause rather than
just the mismatch.

Requires the kubernetes/website clone at ./website — see the README.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dochealth import extract

REPO = Path(__file__).parent / "website"
DOCS_DIR = "content/en/docs/concepts"

SAMPLE_MD = """\
# Reading CSV files

Some intro prose here.

## Basic usage

```python
import polars as pl
df = pl.read_csv("file.csv")
```

TODO: cover the streaming case.
"""


def step(n, name):
    print(f"\n{'─' * 60}\nStep {n}: {name}\n{'─' * 60}")


def bail(msg):
    print(f"  ✗ {msg}")
    sys.exit(1)


# ── Step 1: find_doc_files ────────────────────────────────────────────────────
step(1, "find_doc_files")
try:
    files = extract.find_doc_files(REPO, DOCS_DIR)
except NotImplementedError:
    bail("not implemented")

print(f"  found {len(files)} .md files; first three:")
for f in files[:3]:
    print(f"    {f}")
if len(files) < 50:
    bail(f"only {len(files)} files — the kubernetes concepts folder has ~176 "
         "doc pages, so the search probably isn't recursing into subdirectories")
if not all(str(f).endswith(".md") for f in files):
    bail("some results aren't .md files")
print("  ✓ looks right")

# ── Step 2: file_history ──────────────────────────────────────────────────────
# Contract: 5-tuples (date, author, subject, added, deleted), newest first,
# --follow on. Pinned to a page with known values: it was moved twice in its
# life, so a plain `git log` sees 6 commits while `--follow` recovers 147 going
# back to 2016, two of which are pure renames that changed zero lines.
step(2, "file_history")
rel = "content/en/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale.md"
try:
    commits = extract.file_history(REPO, rel)
except NotImplementedError:
    bail("not implemented")

print(f"  history of {rel.split('concepts/')[1]}: {len(commits)} commits; newest two:")
for c in commits[:2]:
    print(f"    {c}")
if not commits:
    bail("no commits for a tracked file — is the git command right? Try "
         "running it by hand in a terminal inside the website/ clone")
first = commits[0]
if not (isinstance(first, tuple) and len(first) == 5):
    bail(f"each commit should be a 5-tuple (date, author, subject, added, deleted), "
         f"got {len(first)} fields: {first!r}")
if not isinstance(first[0], datetime):
    bail(f"first element should be a datetime (parsed at the boundary, not "
         f"left as a string for extract_docs to convert), got: {first[0]!r}")
if first[0].tzinfo is None:
    bail("dates are timezone-naive — git's %aI carries a UTC offset, so "
         "fromisoformat() should give you an aware datetime. Subtracting a "
         "naive one from datetime.now(timezone.utc) raises TypeError.")
if not all(isinstance(c[3], int) and isinstance(c[4], int) for c in commits):
    bail("added/deleted must be ints — git prints them as strings, so they need "
         "converting (and binary files print '-' instead of a number)")
if len(commits) > 1 and commits[0][0] < commits[-1][0]:
    bail("commits look oldest-first — the contract is newest first")
if len(commits) < 100:
    bail(f"only {len(commits)} commits — a plain `git log` sees 6 here and "
         "--follow should recover 147. Is --follow on the command?")
renames = [c for c in commits if c[3] == 0 and c[4] == 0]
print(f"  zero-line commits found (pure renames): {len(renames)}")
for c in renames:
    print(f"    {c[0].date()}  {c[2][:60]}")
if len(renames) != 2:
    bail(f"expected exactly 2 pure renames on this page, got {len(renames)} — "
         "check how the numstat lines are being summed")
print("  ✓ looks right")

# ── Step 3: text_metrics ──────────────────────────────────────────────────────
step(3, "text_metrics")
try:
    m = extract.text_metrics(SAMPLE_MD)
except NotImplementedError:
    bail("not implemented")

print(f"  metrics for a known sample page: {m}")
expected = {"title": "Reading CSV files", "heading_count": 2,
            "code_fence_count": 1, "todo_flag": True}
ok = True
for key, want in expected.items():
    got = m.get(key, "<missing>")
    if got != want:
        print(f"  ✗ {key}: expected {want!r}, got {got!r}"); ok = False
if not 15 <= m.get("word_count", 0) <= 40:
    print(f"  ✗ word_count {m.get('word_count')} is outside the plausible "
          "range for the sample (15–40)"); ok = False
if not ok:
    print("\n  The sample text is SAMPLE_MD at the top of this file.")
    sys.exit(1)
print("  ✓ looks right")

# ── Step 4: extract_docs ──────────────────────────────────────────────────────
step(4, "extract_docs")
try:
    df = extract.extract_docs(REPO, DOCS_DIR)
except NotImplementedError:
    bail("not implemented")

print(f"  {len(df)} rows × {len(df.columns)} cols")
print(df[["path", "word_count", "days_since_update", "commit_count"]]
      .head(5).to_string(index=False))
want_cols = {"path", "title", "word_count", "heading_count", "code_fence_count",
             "todo_flag", "days_since_update", "age_days", "commit_count",
             "author_count", "flesch_reading_ease", "days_since_update_raw",
             "last_update_commit_msg", "code_block_density", "heading_max_depth",
             "internal_link_count", "extracted_at", "non_page_by_convention",
             "median_line_age_days"}
missing = want_cols - set(df.columns)
if missing:
    bail(f"missing columns: {sorted(missing)}")
if (df["age_days"] < df["days_since_update"]).any():
    bail("some rows have age_days < days_since_update — oldest/newest "
         "commit dates may be swapped")

# ── Part B: is the noise filter actually doing anything? ──────────────────────
# Filtering can only ever move the "last real edit" further into the past, and
# can only ever remove commits — so these two invariants must hold on every row.
if (df["days_since_update"] < df["days_since_update_raw"]).any():
    bad = df[df["days_since_update"] < df["days_since_update_raw"]]
    bail(f"{len(bad)} rows have days_since_update < days_since_update_raw. "
         "Dropping commits can only make the last real edit OLDER, so the "
         "filtered value should never be smaller. Are the two swapped?")
changed = df[df["days_since_update"] != df["days_since_update_raw"]]
print(f"  pages where the filter changed days_since_update: {len(changed)}")
if len(changed):
    print(changed.nlargest(3, "days_since_update")[
        ["path", "days_since_update_raw", "days_since_update"]].to_string(index=False))
print("  ✓ looks right")

# ── Step 5: median line age ───────────────────────────────────────────────────
# How old the CONTENT is, as against when the file was last touched. Only
# reachable through the real clone, same as file_history — a fixture repo would
# be testing the fixture. The pure parser is covered in test_extract.py.
step(5, "median line age (git blame)")
blame_rel = "content/en/docs/concepts/workloads/pods/pod-lifecycle.md"
if not hasattr(extract, "parse_blame_times"):
    bail("not implemented")

raw = subprocess.run(
    ["git", "-C", str(REPO), "blame", "-w", "--line-porcelain", "--", blame_rel],
    capture_output=True, text=True, check=True).stdout
times = extract.parse_blame_times(raw)
lines = len(Path(REPO / blame_rel).read_text(encoding="utf-8").splitlines())
print(f"  {blame_rel.split('concepts/')[1]}: {len(times)} blamed lines, file has {lines}")
if not times:
    bail("no author-times parsed from a tracked file — run the blame by hand "
         "and check you are matching 'author-time ' at the START of a line")
if len(times) != lines:
    bail(f"got {len(times)} times for {lines} lines. One author-time per line is "
         "the contract; a mismatch usually means committer-time is being matched "
         "too, or a tab-prefixed content line is being read as metadata")
if not all(isinstance(t, int) for t in times):
    bail("author-times should be ints (epoch seconds), parsed at the boundary")
oldest, newest = min(times), max(times)
print(f"  oldest line {datetime.fromtimestamp(oldest):%Y-%m-%d}, "
      f"newest {datetime.fromtimestamp(newest):%Y-%m-%d}")
if oldest < 1_100_000_000:
    bail(f"oldest line dates to {datetime.fromtimestamp(oldest):%Y} — before git "
         "existed. Are author-tz or the sha line being parsed as a time?")
print("  ✓ looks right")

print("\n★ All checks passed.\n"
      "\n"
      "  Read DECISIONS.md before changing a metric — several choices are\n"
      "  settled with evidence and should not be reopened from first\n"
      "  principles. In particular there is NO composite health score, and\n"
      "  that is the finding rather than an omission: the weight was both\n"
      "  undecidable and decisive, and a corpus mean of percentile ranks is\n"
      "  ~0.42 for any corpus.\n"
      "\n"
      "  Two scored axes only — days_since_update, modified by\n"
      "  staleness_to_age_ratio, and flesch_reading_ease. commit_count,\n"
      "  author_count, internal_link_count, words_per_heading and todo_flag\n"
      "  all carry no weight, each for a reason DECISIONS.md records.\n"
      "\n"
      "  Extraction:  dochealth extract <repo> <docs_dir> \\\n"
      "                   --config <file> --out metrics-<name>.csv\n"
      "  Dashboard:   dochealth dashboard   (reads every metrics-*.csv in CWD)\n"
      "\n"
      "  The rule that keeps being re-earned: a metric with a denominator, and\n"
      "  a parser that does not error, both fail by producing a PLAUSIBLE wrong\n"
      "  number rather than an exception.")
