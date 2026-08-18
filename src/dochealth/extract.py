"""Extraction script. Provide a repo path and a docs_dir, and it returns a DataFrame with one row per doc page, 
containing the path, git metrics, and text metrics.
"""
from datetime import datetime, timezone
from pathlib import Path
import statistics
import subprocess
import re
import textstat
import pandas as pd

# Fenced code block definition shared by to_prose() and
# code_fence_count. The fence may be indented (inside a list item) 
# and may use more than three backticks to wrap fences of its own.
# The backreference makes the closer match the opener's width, so an 
# inner ``` can't end a ```` block early. Group 2 is the opening 
# fence's info string (e.g. `yaml`, `python exec="on"`).
FENCE_BLOCK_RE = re.compile(r"^[ \t]*(`{3,})([^\n]*)\n.*?^[ \t]*\1`*[ \t]*$",
                            re.DOTALL | re.MULTILINE)

# Flesch averages over sentences. Below this floor the score reads None.
MIN_SENTENCES_FOR_FLESCH = 5


def find_doc_files(repo_path: Path, docs_dir: str) -> list[Path]:
    """Returns every .md or .mdx file under repo_path/docs_dir, at any depth.
    """
    patterns = ["*.md", "*.mdx"]
    docs = sorted(f for pattern in patterns for f in (repo_path / docs_dir).rglob(pattern))
    if not docs:
        raise ValueError(f"No .md or .mdx files found in {repo_path / docs_dir}")
    return docs

def to_prose(text: str) -> str:
    """Strip non-reader-visible markup, for word_count and flesch_reading_ease."""
    text = re.sub(r"\A---\n.*?^---[ \t]*$", "", text, flags=re.DOTALL | re.MULTILINE, count=1)    # frontmatter
    text = FENCE_BLOCK_RE.sub("", text)                       # fenced code blocks
    # Docusaurus admonitions: strip the ::: marker and its directive name.
    # Anchored to the start of a line (indent allowed): prose *about*
    # the syntax remains.
    text = re.sub(r"^[ \t]*:{3,}[a-zA-Z][\w-]*(?:\{[^}]*\})?(?:\[([^\]]*)\])?", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*:{3,}[ \t]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\{\{.*?\}\}", "", text, flags=re.DOTALL)  # {{ macro(...) }} templates
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.DOTALL)    # {% ... %} jinja statements
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)   # HTML comments
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # markdown links: keep visible text, drop the URL
    text = re.sub(r"https?://\S+", "", text)                  # bare URLs
    text = re.sub(r"`([^`]*)`", r"\1", text)                  # inline code: drop backticks, keep the word
    return text

def strip_jsx(prose) -> str:
    """Remove MDX-only syntax: JSX tags (e.g., <Component ...> or </Component>),
    import/export lines, and {/* ... */} comments.
    """
    text = re.sub(r"\{/\*.*?\*/\}", "", prose, flags=re.DOTALL)
    pattern = r"^import\s.+$|^export\s.+$|</?[A-Z][\w.]*[^>]*/?>"
    text = re.sub(pattern, "", text, flags=re.MULTILINE)
    return text

def strip_tables(text: str) -> str:
    """Remove markdown and HTML tables (rows starting and ending with | for markdown
    <table>, </table> for HTML).
    """
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"<table\b.*?</table>", "", text, flags=re.DOTALL)
    return text

def strip_html(text: str) -> str:
    """Remove most common HTML tags."""
    text = re.sub(r"</?(code|li|td|p|ul|tr|strong|a|th|em|caption|tbody|thead|table)\b[^>]*>", "", text)
    return text

def strip_inline_code(text: str) -> str:
    """Remove inline code and replace each instance with a placeholder for Flesch score."""
    text = re.sub(r"`([^`]*)`", r"this", text)
    return text

def file_history(repo_path: Path, rel_path: str) -> list[tuple[datetime, str, str, int, int]]:
    """Return a file's commits, newest first:
    (date, author, subject, lines_added, lines_deleted).
    rel_path is relative to the repo root.
      --follow   keeps following the file across renames. Without it, git stops
                 at the commit that moved the file and everything older is
                 invisible: 6 commits instead of 147 on the HPA page.

      --numstat  prints "added<TAB>deleted<TAB>filename" on its own line(s)
                 after each commit. A commit that only moved the file shows
                 0 and 0 — which is how you tell a real edit from a reshuffle
                 without guessing from the commit message.
    """
    # Run the git log command and capture its output.
    # The --format option prints a line starting with "COMMIT|" for each commit, 
    # followed by the date, author, and subject.
    # The --numstat option prints the number of lines added and deleted for each file in the commit.
    # The output is parsed to extract information for each commit.
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--follow", "--numstat",
         "--format=COMMIT|%aI|%an|%s", "--", rel_path],
        capture_output=True, text=True, check=True)

    # Parse the output to extract commit information.
    out = result.stdout
    commits = []
    for chunk in out.split("COMMIT|")[1:]:
        lines = chunk.splitlines()
        numstat = lines[2].split("\t")
        date, author, subject = lines[0].split("|", 2)
        date = datetime.fromisoformat(date)
        lines_added = int(numstat[0]) if numstat[0].isdigit() else 1
        lines_deleted = int(numstat[1]) if numstat[1].isdigit() else 1
        git_log = (date, author, subject, lines_added, lines_deleted)
        commits.append(git_log)
    return commits

def text_metrics(text: str, suffix: str = ".md") -> dict:
    """Calculate the metrics for a page's raw markdown text.
    """
    # Parse the raw text into prose for word count.
    prose = to_prose(text)
    # Parse the raw text for Flesch score.
    flesch_fence = FENCE_BLOCK_RE.sub("", text)   
    flesch_inline_code = strip_inline_code(flesch_fence)
    flesch_prose = to_prose(flesch_inline_code)
    # Apply additional parsing for MDX files.
    if suffix == ".mdx":
        prose = strip_jsx(prose)
        flesch_prose = strip_jsx(flesch_prose)
    # Strip tables from prose for the Flesch score.
    flesch_prose_no_tables = strip_tables(flesch_prose)
    flesch_prose_no_tables_html = strip_html(flesch_prose_no_tables)
    if textstat.sentence_count(flesch_prose_no_tables_html) < MIN_SENTENCES_FOR_FLESCH:
        # Too few sentences to produce meaningful measure.
        flesch_reading_ease = None
    else:
        flesch_reading_ease = round(textstat.flesch_reading_ease(flesch_prose_no_tables_html), 2)
    # Remove HTML tags from prose
    prose_no_html = strip_html(prose)
    # Extract the title from the frontmatter or the first heading.
    title_match = re.search(r"^title: (.+)$", text, re.MULTILINE)
    if not title_match:
        title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    # If neither frontmatter nor heading is found, title will be None.
    title = title_match.group(1).strip() if title_match else None
    word_count = len(prose_no_html.split())
    heading_count = len(re.findall(r"^#{1,6} ", prose, re.MULTILINE))
    # Find the maximum heading depth by checking the length of the '#' characters in each heading
    heading_max_depth = max((len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", prose, re.MULTILINE)), default=0)
    # Count fenced blocks in the text, ignoring those with exec="on".
    code_fence_count = sum(1 for m in FENCE_BLOCK_RE.finditer(text) if 'exec="on"' not in m.group(2))
    # Calculate code block density as the number of code fences per 1000 words.
    code_block_density = round(code_fence_count / word_count * 1000, 2) if word_count > 0 else 0
    # Count internal links (not starting with http://, https://, mailto:, or #).
    internal_link_count = len(re.findall(r"(?<!!)\[[^\]]*\]\((?!https?://|mailto:|#)[^)]+\)", text))
    # Check for TODO or WIP flags in the text.
    todo_flag = bool(re.search(r"\b(TODO|WIP)\b", text, re.IGNORECASE))

    return {
        "title": title,
        "word_count": word_count,
        "heading_count": heading_count,
        "heading_max_depth": heading_max_depth,
        "code_fence_count": code_fence_count,
        "code_block_density": code_block_density,
        "internal_link_count": internal_link_count,
        "todo_flag": todo_flag,
        "flesch_reading_ease": flesch_reading_ease
    }


def is_non_page_by_convention(filename: str, prefix: str | None) -> bool:
    """True where a filename marks the file as not a rendered page.

    Takes a bare filename, not a path.
    """
    return bool(prefix) and filename.startswith(prefix)

def parse_blame_times(porcelain: str) -> list[int]:
    """Epoch seconds for every blamed line, in file order.

    A flat scan, not a block parse: --line-porcelain repeats the full commit
    header for each line, so one field per line needs no correlation. Contrast
    file_history, which splits on COMMIT| because it has to keep date, author,
    subject and numstat together.

    `startswith("author-time ")` covers all three hazards at once:
      * anchored at column 0, so the TAB-prefixed content line cannot match —
        a page documenting `git blame` would otherwise age itself from the
        timestamps in its own example output
      * exact prefix, so `committer-time` (also at column 0) cannot match.
        author-time is when the content was written, committer-time when it
        landed; they diverge on every rebase and cherry-pick
      * the trailing space stops a hypothetical `author-timezone` matching
    """
    return [int(line.split()[1]) for line in porcelain.splitlines()
            if line.startswith("author-time ")]


def median_line_age(repo_path: Path, rel_path: str, now: datetime) -> int | None:
    """Days since the median line of a file was last written.

    `days_since_update` says when the file itself was last edited, which a typo fix
    resets; this says how old the content itself is.

    -w ignores whitespace-only changes, so a reformatting pass does not reset
    every line's age. That is the same noise problem noise_re handles for
    commits, and blame has no other filter for it.

    Callers must skip untracked files first: git log returns on a path
    it does not know, but blame exits `fatal: no such path ... in HEAD`, which
    check=True turns into an exception.
    """
    result = subprocess.run(["git", "-C", str(repo_path), "blame", "-w", "--line-porcelain", "--", rel_path],
                            capture_output=True, text=True, check=True)
    times = parse_blame_times(result.stdout)
    if not times:
        return None  # an empty file blames to nothing; None, not a crash
    # fromtimestamp is given timezone.utc explicitly: without it the result is
    # naive local time, and subtracting that from an aware `now` raises.
    median_written = datetime.fromtimestamp(statistics.median(times), timezone.utc)
    return (now - median_written).days


def extract_docs(repo_path: Path, docs_dir: str, config: dict | None = None) -> pd.DataFrame:
    """Create a dataframe with one row per doc page: path + git metrics + text metrics.
    """
    # Config file required for each doc project.
    config = config or {}
    # Noisy commit exclusion from config, if present.
    noise_re = config.get("noise_re")
    # Filename convention marking non-pages, if this corpus has one. Recorded
    # here rather than at analysis time so the CSV carries the verdict: a
    # dashboard handed a config-less CSV would otherwise misclassify these rows
    # silently — a metric that fails by producing a plausible wrong number
    # rather than an error.
    non_page_prefix = config.get("non_page_prefix")
    # UTC time now for calculating days since last update, age, and extraction time.
    datetime_now = datetime.now(timezone.utc)
    # Empty list to store metrics for each document.
    metrics_list = []

    for f in find_doc_files(repo_path, docs_dir):
        path = str(f.relative_to(repo_path))
        commits = file_history(repo_path, path)
        if not commits:
            continue  # skip untracked files
        content_commits = [
            (d, a, s) for d, a, s, added, deleted in commits if (added + deleted) > 0 and not (noise_re and noise_re.search(s))
        ]
        if not content_commits:
            # If every commit was noise,fall back to raw log.
            content_commits = [(d, a, s) for d, a, s, _, _ in commits]
        # Safe here and not above: blame raises on a path git does not know,
        # where file_history returns quietly. The guard above has already run.
        median_line_age_days = median_line_age(repo_path, path, datetime_now)
        metrics = text_metrics(f.read_text(encoding="utf-8"), f.suffix)
        # Two metrics below calculated outside the dictionary as they are used
        # within the dictionary to calculate other metrics.
        days_since_update = (datetime_now - content_commits[0][0]).days
        age_days = (datetime_now - content_commits[-1][0]).days
        metrics = {
            "path": path,
            "title": metrics["title"],
            "extracted_at": datetime_now.isoformat(),
            "non_page_by_convention": is_non_page_by_convention(f.name, non_page_prefix),
            "word_count": metrics["word_count"],
            "heading_count": metrics["heading_count"],
            "heading_max_depth": metrics["heading_max_depth"],
            "code_fence_count": metrics["code_fence_count"],
            "code_block_density": metrics["code_block_density"],
            "internal_link_count": metrics["internal_link_count"],
            "todo_flag": metrics["todo_flag"],
            "days_since_update": days_since_update,
            "days_since_update_raw": (datetime_now - commits[0][0]).days,
            "age_days": age_days,
            "median_line_age_days": median_line_age_days,
            "last_update_commit_msg": content_commits[0][2],
            "commit_count": len(content_commits),
            "author_count": len(set(commit[1] for commit in content_commits)),
            "flesch_reading_ease": metrics["flesch_reading_ease"],
            "words_per_heading": round(metrics["word_count"] / metrics["heading_count"], 2) if metrics["heading_count"] > 0 else float("nan"),
            "staleness_to_age_ratio": round(days_since_update / age_days, 2) if age_days > 0 else float("nan")
        }
        metrics_list.append(metrics)
    return pd.DataFrame(metrics_list)