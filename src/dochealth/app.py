"""Documentation health dashboard

Run with:   streamlit run src/dochealth/app.py

...from a directory containing metrics-*.csv, which is what it globs for. Every
such file becomes a corpus in the sidebar.
"""
import altair as alt
import pandas as pd
import streamlit as st
from pathlib import Path

# The one thing this CSV-only page needs from the extractor: the sentence floor,
# so the summary can explain a missing readability rather than restating "5" and
# letting the two drift. Same reason SCORE_INPUTS is derived from DIRECTIONS.
from dochealth.extract import MIN_SENTENCES_FOR_FLESCH

st.set_page_config(page_title="Documentation health", layout="wide")

# Detection and ranking live in scoring.py — pure DataFrame functions, so they
# can be imported and unit-tested without rendering a dashboard. What stays here
# is presentation: bands, colours, link formats, column labels.
from dochealth.scoring import (
    CONSISTENTLY_POOR_QUANTILE,
    SCORE_INPUTS,
    THIN_WORDS,
    add_percentile_ranks,
    consistently_poor,
    directed_ranks,
    exclusion_reason,
)

# Working directory, not __file__: the CSVs are user data, written wherever
# `--out` pointed.
CORPORA = {p.stem.removeprefix("metrics-"): p
           for p in sorted(Path.cwd().glob("metrics-*.csv"))}

# Docs GitHub repo. Allows page names to a link to the file.
# The `path` column is already repo-relative, so this is just a prefix. Uses `main`.
# NOT derived from `git remote` at runtime as the dashboard is CSV-only.
REPO_URLS = {
    "docusaurus": "https://github.com/facebook/docusaurus/blob/main/",
    "kubernetes": "https://github.com/kubernetes/website/blob/main/"
}
# Regexes for what a link displays. LinkColumn applies these to the URL and displays
# the first capture group. The alternative is a column full of github.com/….
LINK_SHORT = r".*/([^/]+/[^/]+)$"        # last two segments, for narrow columns
LINK_FULL = r"/blob/[^/]+/(.+)$"         # the repo-relative path, for wide ones

# Histogram bin width. 10 divides every published band boundary (30/50/60/70),
# so the bars never straddle one and the two layers cannot disagree.
FLESCH_BIN_STEP = 10

# Flesch reading ease is a bare number that means nothing without its scale, so
# these are the standard published bands, NOT thresholds this project chose —
# nothing here is tuned and nothing here is a judgement about these corpora.
# Reading ages are the US grade level plus ~5.
# The sub-zero band exists for corpora we do not have: both of ours now sit
# inside 15..70, but pages scored -29 and -57 before the raw-HTML and
# inline-code parser fixes, and a band that silently swallowed them would hide
# exactly the outlier that found those bugs.
FLESCH_BANDS = [
    (-1e9, 0,   "below 0 · off the scale"),
    (0,    30,  "0–30 · very difficult · graduate"),
    (30,   50,  "30–50 · difficult · college, age 18+"),
    (50,   60,  "50–60 · fairly difficult · age 15–17"),
    (60,   70,  "60–70 · standard · age 13–14"),
    (70,   80,  "70–80 · fairly easy · age 12"),
    (80,   90,  "80–90 · easy · age 11"),
    (90,   1e9, "90+ · very easy · age 10"),
]
# Band shading, hardest to easiest. An ordered ramp rather than categorical
# colours, because the bands ARE ordered — a scheme that gave "very difficult"
# and "easy" unrelated hues would hide the one thing the axis is about.
# The keys must match `label.split(" · ")[1].title()` from FLESCH_BANDS; an
# unmatched key silently falls back to Vega's default palette, so if a band ever
# comes out grey, that derivation is where to look.
# Red/green is the classic colour-blind pair, so it is deliberately NOT the only
# channel here: every band is also named on the chart and in the legend, and the
# x-axis carries the numbers.
BAND_COLOURS = {
    "Very Difficult":   "#d73027",
    "Difficult":        "#f46d43",
    "Fairly Difficult": "#fdae61",
    "Standard":         "#fee08b",
    "Fairly Easy":      "#a6d96a",
    "Easy":             "#66bd63",
    "Very Easy":        "#1a9850",
}


# Context columns sit next to the metric they explain.
PAGE_COLUMNS = [
    "url", "title",
    # The three read as a progression: when the file was last touched, how old
    # its CONTENT is, when it was created. median_line_age_days is deliberately
    # next to days_since_update, because it is the counterpoint to it.
    "days_since_update", "median_line_age_days", "age_days", "staleness_to_age_ratio",
    "last_update_commit_msg",
    "word_count", "code_block_density",
    "flesch_reading_ease", "words_per_heading",
    "commit_count", "author_count",
]


def histogram_by_step(series: pd.Series, step: int) -> pd.DataFrame:
    """Equal-width bins over the full 0..100 Flesch scale, as pre-binned rows.

    Binned here rather than by alt.Bin so the bin edges are ours: the chart is
    layered over fixed band shading, and a chart-chosen bin boundary that did
    not line up with 30/50/60/70 would make the two layers disagree.

    The 0..100 domain is FIXED so the axis means the same thing when you switch
    corpus in the sidebar. Flesch is not bounded by it, though, and pd.cut drops
    what falls outside without a word — so the caller counts and names those
    pages instead. Do not "fix" that by widening the edges here: a rescaling
    axis costs the comparison, and a clipped page would be drawn as merely
    difficult rather than off the scale.
    Empty bins are kept — both corpora currently stop at ~70, and the run of
    zeroes above it is the finding, not padding.
    """
    edges = list(range(0, 101, step))
    counts = (pd.cut(series.dropna(), bins=edges, right=False)
              .value_counts().sort_index())
    return pd.DataFrame({"bin_start": edges[:-1], "bin_end": edges[1:],
                         "pages": counts.values})


@st.cache_data
def load_metrics(csv_path: str, mtime: float) -> pd.DataFrame:
    """Read a metrics CSV. Cached so edits to the UI don't re-read the file.

    `mtime` is unused in the body and is the whole point: st.cache_data keys on
    the ARGUMENTS, so with the path alone a running dashboard serves the copy it
    read at startup forever. Re-extracting under a live app then produced
    `KeyError: median_line_age_days` — the CSV had the new column and the cache
    did not. Passing the file's modification time makes a re-extraction a cache
    miss automatically.

    Same principle as surfacing `extracted_at` rather than hiding it: this
    project has already decided that silently serving stale numbers is not
    acceptable, and an unkeyed cache is a second way to do it.
    """
    return pd.read_csv(csv_path)


def render_page_detail(row: pd.Series, scored_all: pd.DataFrame) -> None:
    """Every metric for one page, each shown against the corpus it sits in.

    A raw number on its own is the thing this whole project keeps refusing to
    ship — 46 Flesch and 200 days mean nothing without a distribution behind
    them. So each metric is given with its percentile among SCORED pages, and
    the axes that carry weight are marked. Excluded pages get the same panel:
    their numbers are real measurements, they are just not rankable.
    """
    st.markdown(f"### {row.get('title') or row['path'].split('/')[-1]}")
    st.markdown(f"[{row['path']}]({row['url']})")

    reason = row.get("exclusion_reason")
    if pd.notna(reason):
        st.warning(f"Not scored — {reason}. The measurements below still hold; "
                   "what is missing is a corpus to rank them against.")

    # Percentile is computed against the SCORED subset only, matching how every
    # rank in this dashboard is computed. A page's own value is included in that
    # population when it is scored, and compared against it when it is not.
    def pct_of(column: str, value) -> str:
        series = scored_all[column].dropna()
        if pd.isna(value) or series.empty:
            return "—"
        return f"{(series < value).mean():.0%} of pages score lower"

    groups = {
        "Freshness": ["days_since_update", "median_line_age_days", "age_days",
                      "staleness_to_age_ratio", "commit_count", "author_count"],
        "Readability": ["flesch_reading_ease"],
        "Size and shape": ["word_count", "code_fence_count", "code_block_density",
                           "heading_count", "heading_max_depth", "words_per_heading",
                           "internal_link_count"],
    }
    for title, columns in groups.items():
        st.markdown(f"**{title}**")
        rows_out = []
        for c in columns:
            if c not in row.index:
                continue
            v = row[c]
            rows_out.append({
                "metric": c + ("  ⚖️" if c in SCORE_INPUTS else ""),
                "value": "not measured" if pd.isna(v) else f"{v:,.2f}".rstrip("0").rstrip("."),
                "in this corpus": pct_of(c, v),
            })
        st.dataframe(pd.DataFrame(rows_out), hide_index=True, width="stretch",
                     column_config={
                         "metric": st.column_config.TextColumn("Metric", width="medium"),
                         "value": st.column_config.TextColumn("Value", width="small"),
                         "in this corpus": st.column_config.TextColumn("Where it sits", width="medium"),
                     })
    if pd.notna(row.get("last_update_commit_msg")):
        st.caption(f"Last real commit: *{row['last_update_commit_msg']}*")


# ── The species split ─────────────────────────────────────────────────────────
# Two independent signals: one reads the CONTENT, one reads the FILENAME CONVENTION.
# Functions of a DataFrame, without st.* calls. Later lift them
# into a scoring module and into extract.py, unchanged.

def histogram(series: pd.Series, bins: int = 20) -> pd.DataFrame | None:
    """Bin a numeric Series for st.bar_chart. NaNs are dropped, not zero-filled.

    Returns None when nothing is measurable, so caller can say so rather
    than draw an empty axis.
    """
    values = series.dropna()
    if values.empty:
        return None
    counts = values.value_counts(bins=bins, sort=False)
    return pd.DataFrame({"pages": counts.to_numpy()},
                        index=pd.Index([round(iv.left, 1) for iv in counts.index],
                                       name=series.name))


# ── Sidebar: which corpus ─────────────────────────────────────────────────────
st.sidebar.header("Data")

# An empty CORPORA used to fall through to `CORPORA[corpus]` with corpus=None and
# die on a KeyError that named nothing useful. Say what was looked for and where:
# this exact failure shipped undetected because the only check was that the
# server returned HTTP 200, which it does before the script has ever run.
if not CORPORA:
    st.error(
        f"No `metrics-*.csv` found in {Path.cwd()}.\n\n"
        "Generate one, then run streamlit from the same directory:\n\n"
        "```\ndochealth extract <repo> <docs_dir> --config <file> "
        "--out metrics-<name>.csv\n```"
    )
    st.stop()

corpus = st.sidebar.selectbox("Corpus", list(CORPORA))
df = load_metrics(str(CORPORA[corpus]), CORPORA[corpus].stat().st_mtime)
top_n = st.sidebar.slider("Rows per list", min_value=5, max_value=25, value=10)

# ── Derived columns ───────────────────────────────────────────────────────────
# Fail loudly rather than render a table of broken links: CORPORA is glob-derived,
# so a new metrics-*.csv can appear without anyone remembering to map its repo.
if corpus not in REPO_URLS:
    st.error(f"No repo URL configured for '{corpus}' — add it to REPO_URLS.")
    st.stop()

df = df.assign(exclusion_reason=exclusion_reason(df),
               url=REPO_URLS[corpus] + df["path"],
               section=df["path"].map(lambda p: Path(p).parent.name or "(root)"))
scored = df[df["exclusion_reason"].isna()].copy()
excluded = df[df["exclusion_reason"].notna()].copy()

scored = add_percentile_ranks(scored)

scored = scored.join(directed_ranks(scored))
poor = consistently_poor(
    scored[[f"{c}_directed" for c in SCORE_INPUTS]], CONSISTENTLY_POOR_QUANTILE)

# ── Sidebar: filters ──────────────────────────────────────────────────────────
# APPLIED AFTER RANKING, and that is the whole design. A percentile is a
# comparison, and the comparison set has to be the corpus: filter first and the
# least-stale page in a 12-page folder ranks 0.00 and reads as healthy at 800
# days stale. That is the composite score's defect in miniature — a number that
# silently redefines itself when the view changes. Filtering narrows what you
# LOOK AT, never what a page is measured against.
st.sidebar.divider()
st.sidebar.subheader("Filter the view")
picked_sections = st.sidebar.multiselect("Directory", sorted(df["section"].unique()))
path_query = st.sidebar.text_input("Path contains", "")


def apply_filters(frame: pd.DataFrame) -> pd.DataFrame:
    """Narrow a frame to the current view. Never called before ranking."""
    mask = pd.Series(True, index=frame.index)
    if picked_sections:
        mask &= frame["section"].isin(picked_sections)
    if path_query:
        mask &= frame["path"].str.contains(path_query, case=False, na=False, regex=False)
    return frame[mask]


scored_all, excluded_all = scored, excluded
scored, excluded = apply_filters(scored), apply_filters(excluded)
poor = poor.loc[poor.index.intersection(scored.index)]
filtered = len(scored) + len(excluded) < len(df)

if scored.empty and excluded.empty:
    st.warning("No pages match the current filters.")
    st.stop()

# ── Header + tabs ──────────────────────────────────────────────────────────────
st.title(f"Documentation health: {corpus}")

tab_overview, tab_staleness, tab_page, tab_raw, tab_excluded = st.tabs(
    ["Overview", "Staleness & authorship", "Page detail", "Raw data", "Excluded pages"])

# ── Page detail ───────────────────────────────────────────────────────────────
# A named tab with a searchable picker, because row-selection alone was not
# discoverable: Streamlit selects rows via a checkbox in the leftmost column,
# and this table's leftmost column is a LinkColumn — so the obvious click
# navigates to GitHub and the affordance that actually works is invisible.
# The tables keep their row selection as a shortcut; this is the way in you can
# find without being told. Covers EXCLUDED pages too — their measurements are
# real, they just have no population to be ranked against.
with tab_page:
    st.subheader("Every metric for one page")
    # The picker lists the pages IN VIEW, which is what its help text has always
    # claimed; it read the unfiltered `df` until 2026-08-18. `apply_filters` is
    # reused rather than concatenating the already-filtered scored/excluded
    # frames, so there is one definition of "in view" and it cannot drift.
    in_view = apply_filters(df)
    labels = in_view.sort_values("path")["path"].tolist()
    if not labels:
        st.info("No pages in the current filter.")
    else:
        chosen_path = st.selectbox(
            "Page", labels, key="page_picker",
            help="Type to search. Respects the sidebar filter.")
        # scored_all, NOT scored: the percentiles in this panel are the same
        # kind of number as every other rank in the dashboard, so they are
        # computed against the whole corpus. Passing the filtered frame here
        # re-based them on the current view — filter to one directory and its
        # least-stale page read as healthy at 800 days, which is exactly the
        # defect the "APPLIED AFTER RANKING" block below exists to prevent.
        render_page_detail(in_view[in_view["path"] == chosen_path].iloc[0], scored_all)

# ── Overview ──────────────────────────────────────────────────────────────

with tab_overview:
# Medians describe the pages IN VIEW, which is why they may move when a filter
# is applied while the ranks beside them do not. The two are different kinds of
# number: a median summarises the set you are looking at, a percentile places one
# page against the corpus. Only the second would be corrupted by re-basing.
    median_staleness = scored["days_since_update"].median()
    median_flesch = scored["flesch_reading_ease"].median()
    median_content_age = scored["median_line_age_days"].median()

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Pages found", len(scored) + len(excluded),
            delta=f"of {len(df)}" if filtered else None, delta_color="off")
    k2.metric("Scored", len(scored),
            delta=f"of {len(scored_all)}" if filtered else None, delta_color="off")
    k3.metric("Not scored", len(excluded),
            delta=f"of {len(excluded_all)}" if filtered else None, delta_color="off")
    k4.metric("Median staleness", f"{median_staleness:,.0f} d" if pd.notna(median_staleness) else "—",
            help="Days since the last content commit.")
    k5.metric("Median content age", f"{median_content_age:,.0f} d" if pd.notna(median_content_age) else "-",
            help="The median amount of time a line of content has gone unedited on a page.")
    k6.metric("Median readability", f"{median_flesch:.0f}" if pd.notna(median_flesch) else "—",
            help="Flesch reading ease. Higher is easier. Pages below the 5-sentence "
                "floor carry no reading and are skipped.")

    if filtered:
        st.caption(":orange[Filtered view. Ranks and percentiles are still computed "
                "against the whole corpus, so a page's score means the same thing "
                "here as it does unfiltered.]")

    st.header("Review these first")

    # ── Fresh file, old content ───────────────────────────────────────────────
    if "median_line_age_days" in scored.columns:
        st.subheader("Recent updates, old content")
        st.caption(
            "Pages where the *median line* of content is far older than the last edit. These "
            "pages appear maintained by the `days_since_update` metric but their content is stale."
        )
        gap_view = scored.dropna(subset=["median_line_age_days"]).assign(
            content_gap=lambda d: d["median_line_age_days"] - d["days_since_update"])
        if gap_view.empty:
            st.info("No pages with a blame reading in this view.")
        else:
            st.dataframe(
                gap_view.nlargest(top_n, "content_gap")[
                    ["url", "days_since_update", "median_line_age_days",
                     "content_gap", "age_days", "word_count"]],
                hide_index=True, width="stretch",
                column_config={
                    "url": st.column_config.LinkColumn("Page", display_text=LINK_SHORT, width="medium"),
                    "days_since_update": st.column_config.NumberColumn("Stale (d)"),
                    "median_line_age_days": st.column_config.NumberColumn("Content (d)"),
                    "content_gap": st.column_config.NumberColumn(
                        "Gap (d)", help="Content age minus staleness. Large means the "
                                        "recent edits touched very little of the page."),
                    "age_days": st.column_config.NumberColumn("Age (d)"),
                    "word_count": st.column_config.NumberColumn("Words"),
                },
            )

    AXIS_LISTS = [
        ("Most stale", "days_since_update", False, "Days since a real edit"),
        ("Hardest to read", "flesch_reading_ease", True, "Lowest Flesch reading ease"),
    ]

    for (label, column, ascending, blurb), axis in zip(AXIS_LISTS, st.columns(len(AXIS_LISTS))):
        axis.subheader(label, divider="gray")
        axis.caption(blurb)
        measured = scored[["url", column]].dropna(subset=[column])
        if measured.empty:
            axis.info("Not measurable in this corpus.")
            continue
        top = measured.sort_values(column, ascending=ascending).head(top_n)
        axis.dataframe(
            top[["url", column]],
            hide_index=True, width="stretch",
            column_config={
                "url": st.column_config.LinkColumn("Page", display_text=LINK_SHORT),
                column: st.column_config.NumberColumn(" ", format="%.0f", width="small"),
            },
        )

    with st.expander("Distributions"):
        for column, axis in zip(SCORE_INPUTS, st.columns(len(SCORE_INPUTS))):
            axis.caption(f"**{column}**")
            chart_data = histogram(scored[column])
            if chart_data is None:
                axis.info("No measurable values in this corpus.")
            else:
                axis.bar_chart(chart_data, y="pages", height=200)

            measured = scored[column].dropna()
            if not measured.empty:
                p10, p50, p90 = measured.quantile([0.1, 0.5, 0.9])
                axis.caption(f"p10 **{p10:,.1f}**  ·  median **{p50:,.1f}**  ·  p90 **{p90:,.1f}**")
            unmeasured = int(scored[column].isna().sum())
            if unmeasured:
                axis.caption(f":orange[{unmeasured} page(s) not measured]")


    # ── Consistently poor ─────────────────────────────────────────────────────────
    # The per-axis lists above only ever surface extremes, so a page that is 
    # moderately bad at everything is invisible in both. This score addresses that issue.
    st.header("Consistently poor")
    st.caption(
        f"Pages in the worst {1 - CONSISTENTLY_POOR_QUANTILE:.0%} of both staleness "
        "and reading difficulty."
    )

    if poor.empty:
        st.info("No page is in the worst quartile of every axis in this corpus.")
    else:
        # best_axis_rank orders this table, but is not displayed in it.
        # The metric is useful to oder by but confusing to present.
        poor_view = scored.loc[poor.index]
        st.dataframe(
            poor_view.head(top_n)[
                ["url", "days_since_update", "staleness_to_age_ratio",
                "flesch_reading_ease", "age_days", "word_count"]],
            hide_index=True, width="stretch",
            column_config={
                "url": st.column_config.LinkColumn("Page", display_text=LINK_SHORT, width="medium"),
                "days_since_update": st.column_config.NumberColumn("Stale (d)"),
                "staleness_to_age_ratio": st.column_config.NumberColumn("Stale/age"),
                "flesch_reading_ease": st.column_config.NumberColumn("Flesch"),
                "age_days": st.column_config.NumberColumn("Age (d)"),
                "word_count": st.column_config.NumberColumn("Words"),
            },
        )
        st.caption(f"{len(poor)} pages qualify; showing up to {top_n}.")

    # ── By section ────────────────────────────────────────────────────────────
    # No new measurement — the same per-page columns, re-cut with the DIRECTORY
    # as the unit. It answers a different question from the per-page lists:
    # those say "which page do I edit", this says "where does a week of effort
    # go", and a median over 14 pages cannot be dragged by one outlier the way a
    # top-10 list can be *made* of them. It also surfaces clusters the top-10s
    # hide: a page at 400 days never makes a list topped by 1,681, but fourteen
    # of them in one directory is the more important fact.
    #
    # `section` is the same column the sidebar filter uses, deliberately — spot
    # the worst section here, then filter to it and read the pages. If the two
    # disagreed about what a section is, that path would break.
    #
    # EVERY section is shown, including two-page ones, with the count first.
    # A minimum-pages floor was considered and dropped: there is no measured gap
    # to put it at (unlike THIN_WORDS), and this project's rule is to label
    # rather than hide — the same call made for the not-scored list.
    st.subheader("Health by section")
    st.caption(
        "Scored pages grouped by parent directory, stalest first."
    )
    by_section = (scored.groupby("section")
                  .agg(pages=("path", "size"),
                       median_staleness=("days_since_update", "median"),
                       most_stale=("days_since_update", "max"),
                       median_flesch=("flesch_reading_ease", "median"))
                  .sort_values("median_staleness", ascending=False)
                  .reset_index())
    st.dataframe(
        by_section.style.format(na_rep="not measured", precision=1),
        hide_index=True, width="stretch",
        column_config={
            "section": st.column_config.TextColumn("Section"),
            "pages": st.column_config.NumberColumn("Pages", help="How much to trust the medians beside it."),
            "median_staleness": st.column_config.NumberColumn("Median staleness (d)"),
            "most_stale": st.column_config.NumberColumn("Most stale (d)", help="The extreme to see whether the median hides outliers."),
            "median_flesch": st.column_config.NumberColumn("Median Flesch", help="Higher is easier to read."),
        },
    )

    # ── Readability against the published scale ───────────────────────────────
    # A Flesch number on its own is uninterpretable — 46 means nothing until you
    # know 60–70 is "standard". This puts the corpus on the published scale
    # rather than on a scale of its own, which is the one thing the composite
    # score could never do — a mean of percentile ranks is ~0.42
    # for ANY corpus, so it grades against itself). These bands are external and
    # fixed, so "no page here is easier than college level" is a real statement
    # about the docs and not an artefact of who else is in the corpus.
    st.subheader("Readability against the published scale")

    # EQUAL-WIDTH bins, with the unequal published bands as background shading.
    # The first version binned BY band and was not a histogram: the bands are
    # 30/20/10/10/10/10/10 wide, so bar length was not density. Measured on
    # kubernetes — 30..50 held 101 pages and 50..60 held 50, which reads as a
    # dominant mode and is nothing of the sort: 5.05 and 5.00 pages per Flesch
    # point. The apparent peak was the bin being twice as wide.
    hist = histogram_by_step(scored["flesch_reading_ease"], FLESCH_BIN_STEP)
    band_rects = pd.DataFrame(
        [{"lo": max(lo, 0), "hi": min(hi, 100), "band": label.split(" · ")[1].title()}
         for lo, hi, label in FLESCH_BANDS if hi > 0 and lo < 100])

    shading = (alt.Chart(band_rects).mark_rect(opacity=0.18)
               .encode(x=alt.X("lo:Q", title="Flesch reading ease  (higher = easier)"),
                       x2="hi:Q",
                       color=alt.Color("band:N",
                                       scale=alt.Scale(domain=list(BAND_COLOURS),
                                                       range=list(BAND_COLOURS.values())),
                                       legend=alt.Legend(title="Published band",
                                                         orient="bottom", columns=4)),
                       tooltip=["band:N", "lo:Q", "hi:Q"]))
    labels = (alt.Chart(band_rects).mark_text(dy=-6, fontSize=10, opacity=0.75)
              .encode(x=alt.X("mid:Q"), y=alt.value(0), text="band:N")
              .transform_calculate(mid="(datum.lo + datum.hi) / 2"))
    # Headroom above the tallest bar. Altair's default domain stops exactly at the
    # max, so the modal bin runs into the band labels sitting along the top edge
    # and reads as clipped. 15% is set from the data rather than by growing the
    # chart, so the band shading still spans the full plot height. max(1, ...)
    # keeps the domain valid on an empty or all-zero corpus.
    y_headroom = max(1, int(hist["pages"].max() * 1.15) + 1)
    bars = (alt.Chart(hist).mark_bar(stroke="white", strokeWidth=1)
            .encode(x=alt.X("bin_start:Q", bin=alt.Bin(binned=True, step=FLESCH_BIN_STEP)),
                    x2="bin_end:Q",
                    y=alt.Y("pages:Q", title="Pages",
                            scale=alt.Scale(domain=[0, y_headroom], nice=False)),
                    tooltip=["bin_start:Q", "bin_end:Q", "pages:Q"])
            .add_params(alt.selection_point(name="picked", fields=["bin_start"], empty=False)))

    chart = (shading + labels + bars).properties(height=320)
    event = st.altair_chart(chart, width="stretch", on_select="rerun", key="flesch_hist")

    picked = event.get("selection", {}).get("picked", []) if event else []
    if picked:
        lo = picked[0]["bin_start"]
        hi = lo + FLESCH_BIN_STEP
        chosen = scored[scored["flesch_reading_ease"].between(lo, hi, inclusive="left")]
        st.markdown(f"**{len(chosen)} page(s) scoring {lo:.0f}–{hi:.0f}**, hardest first")
        st.dataframe(
            chosen.sort_values("flesch_reading_ease")[
                ["url", "flesch_reading_ease", "word_count", "days_since_update", "section"]],
            hide_index=True, width="stretch",
            column_config={
                "url": st.column_config.LinkColumn("Page", display_text=LINK_FULL, width="large"),
                "flesch_reading_ease": st.column_config.NumberColumn("Flesch"),
                "word_count": st.column_config.NumberColumn("Words"),
                "days_since_update": st.column_config.NumberColumn("Stale (d)"),
                "section": st.column_config.TextColumn("Directory"),
            },
        )

    unscored = int(scored["flesch_reading_ease"].isna().sum())
    if unscored:
        st.caption(
            f":orange[{unscored} scored pages do not appear in the chart. "
            f"They have fewer than {MIN_SENTENCES_FOR_FLESCH} sentences, the minimum "
            f"for a meaningful Flesch score.]"
        )

    # Flesch has no floor and no ceiling; the chart's axis does. `pd.cut` drops
    # anything outside its edges silently, so a page reading -57 would vanish
    # from the one view most likely to expose it — and pages at -29 and -57 are
    # precisely how two parser bugs were caught. Named, not just
    # counted: a count says outliers exist, a path lets you go read one.
    # Bounds match the bins exactly, [0, 100), so this cannot disagree with them.
    reading = scored["flesch_reading_ease"]
    off_scale = scored[reading.notna() & ~reading.between(0, 100, inclusive="left")]
    if not off_scale.empty:
        st.caption(
            f":orange[{len(off_scale)} scored page(s) fall outside the 0–100 axis and are "
            "not drawn above. A reading this far out is usually a parser fault rather "
            "than a page fault — read one before treating it as a finding.]"
        )
        st.dataframe(
            off_scale.sort_values("flesch_reading_ease")[
                ["url", "flesch_reading_ease", "word_count", "section"]],
            hide_index=True, width="stretch",
            column_config={
                "url": st.column_config.LinkColumn("Page", display_text=LINK_FULL, width="large"),
                "flesch_reading_ease": st.column_config.NumberColumn("Flesch"),
                "word_count": st.column_config.NumberColumn("Words"),
                "section": st.column_config.TextColumn("Directory"),
            },
        )

# extracted_at is surfaced, not hidden, so a stalled refresh can't quietly serve
# stale numbers as if they were current.
extracted = pd.to_datetime(df["extracted_at"], format="ISO8601").max()
st.caption(f"Extracted {extracted:%Y-%m-%d %H:%M} UTC  ·  {CORPORA[corpus].name}")

with tab_staleness:
    # ── The three axes ────────────────────────────────────────────────────────────
    # Built as histograms first, and the distributions turned out to answer "is this
    # corpus plausible" but not "what do I fix". The extremes are the useful thing at
    # a glance, so the ranked lists lead and the distributions moved into an expander
    # where they still serve their real job — calibrating whether a tail is a long
    # thin one or a cliff.
    # st.header("Worst scoring pages")

    # Both words_per_heading lists were REMOVED here, not just unweighted. Reading
    # them found reference pages and well-structured long pages, so a list headed
    # "worst pages" was pointing at healthy ones — worse than useless. The column
    # survives as context in the Pages table, where it describes without accusing.

    # ── Bus factor ────────────────────────────────────────────────────────────
    # Not a health score, but "how much of this corpus rests on one person": continuity 
    # risk.
    st.header("Bus factor")
    st.caption(
        "Scored pages only one person has ever updated, most content first."
    )

    solo = scored[scored["author_count"] == 1]
    if solo.empty:
        st.success("Every scored page in view has had more than one author.")
    else:
        st.dataframe(
            solo.nlargest(top_n, "word_count")[
                ["url", "word_count", "days_since_update", "age_days", "commit_count"]],
            hide_index=True, width="stretch",
            column_config={
                "url": st.column_config.LinkColumn("Page", display_text=LINK_SHORT, width="medium"),
                "word_count": st.column_config.NumberColumn("Words", help="Most content resting on one person, first."),
                "days_since_update": st.column_config.NumberColumn("Stale (d)"),
                "age_days": st.column_config.NumberColumn("Age (d)"),
                "commit_count": st.column_config.NumberColumn("Commits"),
            },
        )
        st.caption(
            f"{len(solo)} of {len(scored)} scored pages in view ({len(solo)/len(scored):.0%})."
        )

    # ── Staleness against age ─────────────────────────────────────────────────
    # The chart that draws staleness_to_age_ratio, which is the hardest idea in
    # this dashboard to state in words and the easiest to show.
    #
    # A page cannot be staler than it is old, so EVERY point lies on or below the
    # diagonal, and the diagonal IS ratio 1.0 — written once, never revisited.
    # Vertical distance below it is how much of its life the page was maintained.
    # That is the ratio's whole argument: owners-dependents (1,677d stale /
    # 1,861d old = 0.90) sits on the line, controlling-access (1,168d / 3,836d =
    # 0.30) sits far below it, and a raw staleness list ranked them adjacently.
    #
    # It also shows why the ratio is a MODIFIER and not a score input: a young
    # page sitting on the diagonal is new, not neglected — near the origin the
    # line is full of pages nobody should touch.
    st.subheader("Staleness against age")
    st.caption(
        "Each page against its own lifetime. The diagonal represents pages that have not been "
        "edited since they were written. The further below the diagonal a page sits, the more of "
        "its life was spent being edited."
    )

    ratio_plot = scored[["url", "title", "age_days", "days_since_update",
                         "staleness_to_age_ratio", "section"]].dropna(
                             subset=["age_days", "days_since_update"])
    if ratio_plot.empty:
        st.info("No pages with git history in this view.")
    else:
        lim = float(max(ratio_plot["age_days"].max(), ratio_plot["days_since_update"].max()))
        diagonal = (alt.Chart(pd.DataFrame({"x": [0, lim], "y": [0, lim]}))
                    .mark_line(strokeDash=[6, 4], color="grey", opacity=0.8)
                    .encode(x="x:Q", y="y:Q"))
        points = (alt.Chart(ratio_plot).mark_circle(size=70, opacity=0.75)
                  .encode(
                      x=alt.X("age_days:Q", title="Age: days since the page was created"),
                      y=alt.Y("days_since_update:Q", title="Staleness: days since a meaningful edit"),
                      color=alt.Color("staleness_to_age_ratio:Q",
                                      scale=alt.Scale(scheme="orangered"),
                                      legend=alt.Legend(title="Stale / age")),
                      tooltip=["title:N", "section:N", "age_days:Q",
                               "days_since_update:Q", "staleness_to_age_ratio:Q"]))
        st.altair_chart((diagonal + points).properties(height=360), width="stretch")
        on_line = int((ratio_plot["staleness_to_age_ratio"] >= 0.99).sum())
        if on_line:
            median_age = ratio_plot.loc[
                ratio_plot["staleness_to_age_ratio"] >= 0.99, "age_days"].median()
            st.caption(
                f"{on_line} pages sit on the line and have not been edited since they were published. "
                f"Their median age is **{median_age:,.0f} days old**."
            )

# ── Pages ─────────────────────────────────────────────────────────────────────

with tab_raw:

    st.header("Pages")
    st.caption("All metrics for all scored pages.")

    page_view = scored[PAGE_COLUMNS + [f"{c}_pct" for c in SCORE_INPUTS]]

    st.dataframe(
        # na_rep is what makes an unmeasured reading say so, rather than rendering as
        # a blank cell that reads like a missing value or, worse, like a zero.
        # Passing a Styler does NOT cost column sorting — checked by hand on
        # streamlit 1.61, because the usual reason to avoid one is the fear that it
        # does. column_config survives alongside it too; both were verified.
        page_view.style.format(na_rep="not measured", precision=2),
        width="stretch",
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Path", display_text=LINK_FULL, width="medium"),
            "title": st.column_config.TextColumn("Title", width="small"),
            "days_since_update": st.column_config.NumberColumn("Stale (d)", help="Days since the last non-noise commit"),
            "median_line_age_days": st.column_config.NumberColumn(
                "Content (d)",
                help="Age of this page's MEDIAN line, from git blame -w. Stale (d) says "
                     "when the file was last touched — a typo fix resets it. This says "
                     "how old the writing is. A low Stale with a high Content means a "
                     "page that looks maintained and is not."),
            "age_days": st.column_config.NumberColumn("Age (d)", help="Days since the page was created"),
            "staleness_to_age_ratio": st.column_config.NumberColumn("Stale/age", help="1.0 means untouched since creation"),
            "last_update_commit_msg": st.column_config.TextColumn("Last real commit", width="medium"),
            "word_count": st.column_config.NumberColumn("Words"),
            # NOT a percentage — it is fences per 1,000 words and is unbounded
            # above. Headed "Code %" until 2026-08-18, which invited the reader
            # to read 214 as a proportion.
            "code_block_density": st.column_config.NumberColumn(
                "Fences/1k words", help="Code fences per 1,000 words. Thin + dense = reference page, not a stub"),
            "flesch_reading_ease": st.column_config.NumberColumn("Flesch"),
            "words_per_heading": st.column_config.NumberColumn("Words/heading"),
            "commit_count": st.column_config.NumberColumn("Commits"),
            "author_count": st.column_config.NumberColumn("Authors"),
            # One per SCORE_INPUTS. Streamlit ignores config for absent columns, so
            # a stale entry here does nothing visible — check the column exists.
            "days_since_update_pct": st.column_config.ProgressColumn("Stale pct", min_value=0, max_value=1, format="%.2f"),
            "flesch_reading_ease_pct": st.column_config.ProgressColumn("Flesch pct", min_value=0, max_value=1, format="%.2f"),
        },
    )
    st.caption("One row per scored page. For a single page broken down against "
               "the corpus, use the **Page detail** tab.")

with tab_excluded:
    # ── Excluded pages ────────────────────────────────────────────────────────────
    st.header("Not scored")
    st.caption(
        "All pages that did not receive a score. Verify detection is working by reviewing these pages."
    )

    if excluded.empty:
        st.info("Nothing excluded in this corpus.")
    else:
        excluded_view = excluded[["url", "exclusion_reason", "word_count",
                                  "code_fence_count", "code_block_density", "heading_count"]]
        st.dataframe(
            excluded_view,
            width="stretch",
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Path", display_text=LINK_FULL, width="large"),
                "exclusion_reason": st.column_config.TextColumn("Why", width="medium"),
                "word_count": st.column_config.NumberColumn("Words"),
                "code_fence_count": st.column_config.NumberColumn("Fences"),
                "code_block_density": st.column_config.NumberColumn(
                    "Fences/1k words", help="Code fences per 1,000 words"),
                "heading_count": st.column_config.NumberColumn("Headings"),
            },
        )
        # Excluded pages are inspectable in the Page detail tab like any other:
        # their measurements are real, they just have no population to be ranked
        # against. The rule is that this list stays inspectable, not merely
        # visible.
        st.caption("These pages appear in the **Page detail** tab too.")

    # ── The species split as a picture ───────────────────────────────────────────
    # Pages thin in words AND thin in code is a non-page; thin in words but DENSE in 
    # code is a reference page doing its job. Stated as a threshold in flag_by_shape 
    # and as tables everywhere else.
    st.header("Why a page isn't scored")
    st.caption(
        f"The graph below plots every page in the documentation set. Pages with fewer than **{THIN_WORDS} words and zero "
        "fences** (the bottom-left corner) are not scored. Pages that are equally thin but appear " 
        "higher up the y-axis are reference pages whose content is code, and they stay scored."
    )

    species = pd.concat([
        scored.assign(species="scored"),
        excluded.assign(species=excluded["exclusion_reason"]),
    ])
    st.scatter_chart(
        species, x="word_count", y="code_block_density", color="species",
        x_label="Words", y_label="Code blocks per 1,000 words", height=340,
    )