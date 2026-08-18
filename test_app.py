"""Smoke tests for the dashboard — run with: pytest

These exist because of a real escape. Moving app.py into src/dochealth/
silently broke `CORPORA`: it globbed metrics-*.csv
relative to `Path(__file__).parent`, so after the move it matched nothing and the
app died on `KeyError: None`. It shipped anyway, because the only check was that
`curl` got HTTP 200 from the server — and Streamlit serves the page shell over
HTTP before the script has run at all. **A dashboard that boots is not a
dashboard that works.**

streamlit.testing.v1.AppTest executes the script and collects what it rendered,
which is the only way to test a Streamlit page: the script runs top to bottom on
import, so the module cannot be imported without rendering it.

These are deliberately shallow — that the app runs, finds its data, wires its
filters correctly, and splits each corpus the way DECISIONS.md says. The
judgments underneath are unit-tested directly in test_scoring.py and
test_extract.py; asserting on rendered output can tell you that a split changed
but not which rule changed, so it is the wrong place to pin a threshold.
"""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = "src/dochealth/app.py"
# Resolved at import, before any test chdirs: AppTest needs an absolute path once
# a test moves the working directory to give the app a doctored corpus.
APP_ABS = str(Path(APP).resolve())

# Verified by hand against both corpora, and unchanged when the filename rule
# moved from the dashboard into the extractor.
EXPECTED_SPLIT = {
    "docusaurus": ("94", "89", "5"),
    "kubernetes": ("176", "167", "9"),
}


@pytest.fixture(scope="module")
def app():
    """Pinned to a known corpus rather than whichever CORPORA sorts first —
    the glob picks up every metrics-*.csv in the working directory, so the
    default depends on what you happen to have extracted lately."""
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.selectbox[0].set_value("kubernetes").run()
    return at


def test_the_app_runs_without_raising(app):
    assert not app.exception
    assert not app.error


def test_the_known_corpora_are_discovered(app):
    """The regression that prompted this file: an empty CORPORA rendered a
    selectbox with no options, and the KeyError that followed named nothing.

    A SUPERSET check, not equality. CORPORA is a glob over the working
    directory, so generating a metrics-*.csv is a normal thing to do and must
    not fail the suite — an earlier version of this test asserted the exact
    list and broke the first time a second docusaurus extract appeared."""
    assert {"docusaurus", "kubernetes"} <= set(app.sidebar.selectbox[0].options)


@pytest.mark.parametrize("corpus", ["docusaurus", "kubernetes"])
def test_each_corpus_splits_as_documented(corpus):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.selectbox[0].set_value(corpus).run()
    assert not at.exception
    found, scored, not_scored = EXPECTED_SPLIT[corpus]
    # By label, not by position — the KPI row has grown once already (medians
    # were added after this test was written) and will again.
    kpis = {m.label: m.value for m in at.metric}
    assert kpis["Pages found"] == found
    assert kpis["Scored"] == scored
    assert kpis["Not scored"] == not_scored


def test_the_sections_are_present(app):
    """The composite score was replaced by per-axis lists plus the
    consistently-poor list, and the not-scored list must stay visible rather
    than becoming a footnote.

    Presence, not order or wording, and across BOTH heading levels — section
    order, heading text and header-vs-subheader are all live design choices.
    Pinning them has broken this test twice for changes that removed nothing:
    once on a rename, once when "Worst scoring pages" was demoted to its two
    subheadings. What must not silently vanish is the not-scored list."""
    headings = [h.value for h in app.header] + [s.value for s in app.subheader]
    assert "Not scored" in headings         # never a footnote
    assert "Consistently poor" in headings  # what replaced the composite score
    assert "Most stale" in headings         # the per-axis lists
    assert "Pages" in headings


def test_filtering_narrows_the_view_without_changing_the_ranks(app):
    """The design decision behind where filters are applied: a percentile is a
    comparison against the corpus, so filtering must not re-rank. Filter to one
    directory and the pages still carry the ranks they had unfiltered."""
    before = AppTest.from_file(APP, default_timeout=120).run()
    before.sidebar.selectbox[0].set_value("kubernetes").run()
    unfiltered_rows = before.metric[1].value

    before.sidebar.multiselect[0].set_value(["security"]).run()
    assert not before.exception
    assert before.metric[1].value != unfiltered_rows  # the view really narrowed
    assert before.metric[1].delta == f"of {unfiltered_rows}"


def _page_detail_percentiles(at, path):
    """The 'Where it sits' column of the Page detail panel, keyed by metric.

    Found by shape rather than by index: the panel renders one dataframe per
    metric group, and which of the app's many dataframes those are is not
    something a test should have to know positionally.
    """
    picker = next(w for w in at.selectbox if w.key == "page_picker")
    picker.set_value(path).run()
    assert not at.exception
    out = {}
    for frame in at.dataframe:
        value = frame.value
        if getattr(value, "columns", None) is not None and "in this corpus" in value.columns:
            out.update(dict(zip(value["metric"], value["in this corpus"])))
    assert out, "no Page detail panel rendered"
    return out


def test_page_detail_percentiles_are_not_re_based_by_a_filter():
    """The invariant the whole filter-ordering design exists to protect.

    Percentiles are a comparison against the CORPUS, so narrowing the view must
    not change them. This shipped broken: the Page detail tab was handed the
    filtered frame instead of `scored_all`, so filtering to one directory
    silently re-ranked the page against its own neighbours — the least-stale
    page in a 16-page folder reading healthy at 1,173 days. Counting rows, which
    is all the test above does, cannot see that.
    """
    page = "content/en/docs/concepts/security/controlling-access.md"
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.selectbox[0].set_value("kubernetes").run()

    unfiltered = _page_detail_percentiles(at, page)

    at.sidebar.multiselect[0].set_value(["security"]).run()
    assert not at.exception
    assert at.metric[1].delta, "the view did not actually narrow"
    filtered = _page_detail_percentiles(at, page)

    assert filtered == unfiltered


def test_the_page_picker_respects_the_filter():
    """Its help text promises this; it read the unfiltered frame until it didn't.

    A picker offering pages the filter has excluded is not just cosmetic — it is
    the one route into Page detail, so the tab silently disagreed with every
    other view about what "in view" meant.
    """
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.selectbox[0].set_value("kubernetes").run()
    picker = next(w for w in at.selectbox if w.key == "page_picker")
    everything = list(picker.options)

    at.sidebar.multiselect[0].set_value(["security"]).run()
    picker = next(w for w in at.selectbox if w.key == "page_picker")
    narrowed = list(picker.options)

    assert narrowed, "the filter matched pages, so the picker cannot be empty"
    assert len(narrowed) < len(everything)
    assert all("/security/" in path for path in narrowed)


def test_a_reading_outside_the_axis_is_named_not_dropped(tmp_path, monkeypatch):
    """The chart's axis is fixed at 0-100; Flesch is not bounded by it.

    `pd.cut` drops out-of-range values without a word, so a page reading -57
    would vanish from the one view most likely to expose it — and readings of
    -29 and -57 are exactly how two parser bugs were found. Neither corpus has one today, so the case is built rather than
    waited for: a real CSV with one reading pushed off the scale.
    """
    off_scale_page = "content/en/docs/concepts/architecture/nodes.md"
    frame = pd.read_csv("metrics-kubernetes.csv")
    target = frame["path"] == off_scale_page
    assert target.sum() == 1, "fixture page is missing from the corpus"
    assert frame.loc[target, "word_count"].iat[0] >= 150, "fixture page must be scored"
    frame.loc[target, "flesch_reading_ease"] = -57.02

    # Named metrics-kubernetes.csv because REPO_URLS is keyed by corpus name and
    # an unknown corpus is a hard stop by design.
    monkeypatch.chdir(tmp_path)
    frame.to_csv(tmp_path / "metrics-kubernetes.csv", index=False)

    at = AppTest.from_file(APP_ABS, default_timeout=120).run()
    assert not at.exception

    captions = [c.value for c in at.caption]
    assert any("outside the 0\u2013100 axis" in c for c in captions), captions

    listed = any(
        off_scale_page in frame_el.value["url"].astype(str).str.cat(sep=" ")
        for frame_el in at.dataframe
        if getattr(frame_el.value, "columns", None) is not None
        and "url" in frame_el.value.columns
    )
    assert listed, "the off-scale page was counted but never named"


def test_a_filter_matching_nothing_says_so(app):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.text_input[0].set_value("zzz-no-such-page").run()
    assert not at.exception
    assert any("No pages match" in w.value for w in at.warning)
