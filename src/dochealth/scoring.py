"""Detection and ranking over a metrics DataFrame.

Everything here is a pure function of a DataFrame, so it is importable and
testable.

The split this module creates is between what the dashboard measures and draws. 
Thresholds and ranking rules live here, while bands, colours, column labels and
link formats stay in app.py, because they are presentation and changing one has
no effect on which page is judged worse than another.
"""
import pandas as pd

# The scored axes and which end of each is bad: +1 means a HIGH raw value is
# worse. TWO of them — words_per_heading is still displayed but carries no
# weight; see DECISIONS.md, *Why words per heading is not scored*.
#
# Adding an axis here is the ONLY edit required: the scored/partial split
# derives from this list rather than from a hardcoded axis count,
# so rows re-sort themselves; SCORE_INPUTS is derived rather than written out to
# keep that promise true.
DIRECTIONS = {
    "days_since_update": +1,      # older = worse
    "flesch_reading_ease": -1,    # lower = harder to read = worse
}
SCORE_INPUTS = list(DIRECTIONS)

# The thin-page cut. Named because flag_by_shape's docstring argues for it and
# the species scatter draws it; the reasoning lives in that docstring.
THIN_WORDS = 150

# No WEIGHTS constant, deliberately: there is no composite score, and the
# absence is the decision — see DECISIONS.md, *Why there is no composite score*.
CONSISTENTLY_POOR_QUANTILE = 0.75  # worst quartile of every axis at once
# staleness_to_age_ratio scales the staleness term to between FLOOR and 1.0 of
# its percentile rank. A MODIFIER, not a third input — as an input it would
# double-count staleness. Calibrated against three candidate floors on a
# motivating pair of pages; the table is in DECISIONS.md, *Calibrating the
# staleness floor*.
STALENESS_MODIFIER_FLOOR = 0.5


def flag_by_shape(df: pd.DataFrame) -> pd.Series:
    """True where a row is too thin in prose and code to be a doc page.

    WHY 150: it sits inside a gap in both corpora (110 -> 223 docusaurus,
    142 -> 161 kubernetes), so anything in 143..160 gives an identical answer.

    Two known and accepted false positives:
      deployment/vercel.mdx        110w — a complete deployment guide, short
                                          because it delegates to Vercel's docs
      security/linux-security.md   118w — real security guidance with subheadings
    Dropping to ~105 would rescue both but admit three section indexes
    (124/129/142w) into the scored set. 150 was kept because the visible
    "Not scored" list catches false EXCLUSIONS, while nothing protects the
    fix-first list from a section index sitting at the top of it.

    A short real page and a section index have the same content shape. No column
    currently separates them. Link density was tested and fails.
    """
    return (df["word_count"] < THIN_WORDS) & (df["code_fence_count"] == 0)
     


def exclusion_reason(df: pd.DataFrame) -> pd.Series:
    """One string per row saying why it isn't scored, or NA if it is scored.
    """
    if "non_page_by_convention" not in df.columns:
        raise ValueError(
            "This CSV predates the non_page_by_convention column. Regenerate it: "
            "dochealth extract <repo> <docs_dir> --config <file> --out <csv>. "
            "Falling back to 'no rows flagged' would silently misclassify every "
            "partial in the corpus, which is the failure this column exists to stop."
        )

    reason = pd.Series(pd.NA, index=df.index, dtype="object")

    # Apply shape filter to avoid scoring non-docs pages (thin on prose and no code)
    shape = flag_by_shape(df)

    # Convention is assigned second so it wins on rows both rules catch: "partial"
    # is the more specific fact about _markdown-partial-example.mdx than "thin".
    reason[shape] = "thin: low prose and low code"
    reason[df["non_page_by_convention"].fillna(False).astype(bool)] = "partial: filename convention"
    return reason


def directed_ranks(scored_df: pd.DataFrame) -> pd.DataFrame:
    """Percentile rank per axis, oriented so higher = worse.

    The staleness column is modified by `staleness_to_age_ratio`.
    """
    directed = pd.DataFrame(index=scored_df.index)
    for column, sign in DIRECTIONS.items():
        pct = scored_df[f"{column}_pct"]
        directed[column] = pct if sign > 0 else 1 - pct

    # A missing ratio means no discount rather than a zeroed term.
    ratio = scored_df["staleness_to_age_ratio"].fillna(1.0)
    directed["days_since_update"] *= (
        STALENESS_MODIFIER_FLOOR + (1 - STALENESS_MODIFIER_FLOOR) * ratio)
    return directed.add_suffix("_directed")


def consistently_poor(directed: pd.DataFrame, quantile: float) -> pd.DataFrame:
    """Pages in the worst quantile of EVERY axis, worst least-bad axis first.

    Here, a page that is bad at everything outranks one that is terrible at a 
    single thing and fine at the rest.

    `best_axis_rank` is that `min`: the rank of the axis each page does best on,
    on a scale where higher is worse.
      windows-security.md      0.94 stale / 0.96 unreadable -> 0.94, top of list
      advanced-pod-config.md   0.59 stale / 0.99 unreadable -> 0.59, bottom
    The second is the worst-reading page in the list and still ranks last,
    because being merely mid-stale is an axis it can hide behind.
    """
    complete = directed.dropna()
    if complete.empty:
        return complete.assign(best_axis_rank=[])
    cuts = complete.quantile(quantile)
    hits = complete[(complete >= cuts).all(axis=1)]
    return hits.assign(best_axis_rank=hits.min(axis=1)).sort_values(
        "best_axis_rank", ascending=False)


def add_percentile_ranks(scored: pd.DataFrame) -> pd.DataFrame:
    """Add a `<axis>_pct` column per scored axis. Returns a new frame.

    Wrapped in a function rather than left as a loop in the dashboard so that
    `directed_ranks` has a stated precondition instead of an implicit one: it
    reads `<axis>_pct`, and something has to have put them there.
    """
    # Percentile rank, computed on the scored subset only. Percentile rather than
    # min-max, because every one of these axes has outliers. Ranking
    # before the split would let the stubs compress the range real pages score in.
    # No direction is applied: whether a high rank is good or bad differs per axis
    # and words_per_heading is band-shaped rather than monotonic.
    ranked = scored.copy()
    for column in SCORE_INPUTS:
        ranked[f"{column}_pct"] = ranked[column].rank(pct=True)
    return ranked
