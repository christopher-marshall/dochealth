"""Unit tests for the detection and ranking rules.

These could not exist while the functions lived in app.py: a Streamlit script
runs top to bottom on import, so importing a detector rendered the whole
dashboard. test_app.py had to reach them through AppTest, which meant asserting
on rendered output — it could tell you the split changed, never which rule
changed or why.

Each case here pins a JUDGMENT rather than a behaviour, and DECISIONS.md gives
the evidence for every one. They are the tests most worth having and the ones
most likely to be "simplified" by someone who does not know what they cost.
"""
import pandas as pd
import pytest

from dochealth import scoring


def frame(**columns) -> pd.DataFrame:
    """A metrics frame with only the columns a test cares about."""
    return pd.DataFrame(columns)


# ── flag_by_shape ─────────────────────────────────────────────────────────────

def test_thin_prose_alone_is_not_enough():
    """The AND is the whole design. theme-mermaid.mdx (23 words) has FEWER words
    than concepts/_index.md (33) and must NOT be caught, because its content is
    code — so no word threshold alone separates them and the fence count does
    the work."""
    df = frame(word_count=[23, 33], code_fence_count=[4, 0])
    assert list(scoring.flag_by_shape(df)) == [False, True]


def test_a_long_page_with_no_fences_is_a_real_page():
    df = frame(word_count=[3176], code_fence_count=[0])
    assert not scoring.flag_by_shape(df).iat[0]


def test_the_cut_is_strict():
    """150 exactly is scored. The threshold sits inside a measured gap in both
    corpora (110->223 docusaurus, 142->161 kubernetes), so nothing real lives at
    the boundary — but which side it falls on should still be deliberate."""
    df = frame(word_count=[149, 150], code_fence_count=[0, 0])
    assert list(scoring.flag_by_shape(df)) == [True, False]


# ── exclusion_reason ──────────────────────────────────────────────────────────

def test_a_csv_without_the_convention_column_is_refused():
    """Falling back to 'no rows flagged' would silently misclassify every
    partial in the corpus, which is the failure the column exists to stop. An
    old CSV must fail loudly rather than render a plausible screen."""
    df = frame(word_count=[500], code_fence_count=[3])
    with pytest.raises(ValueError, match="non_page_by_convention"):
        scoring.exclusion_reason(df)


def test_convention_wins_over_shape_on_a_row_both_rules_catch():
    """'partial' is the more specific fact about _markdown-partial-example.mdx
    than 'thin', and the assignment order is what encodes that."""
    df = frame(word_count=[9], code_fence_count=[0], non_page_by_convention=[True])
    assert scoring.exclusion_reason(df).iat[0] == "partial: filename convention"


def test_a_scored_row_gets_no_reason():
    df = frame(word_count=[900], code_fence_count=[2], non_page_by_convention=[False])
    assert pd.isna(scoring.exclusion_reason(df).iat[0])


def test_a_missing_convention_value_is_not_a_flag():
    """fillna(False): absent is not the same as true, the same rule the
    extractor follows for unmeasured readings."""
    df = frame(word_count=[900], code_fence_count=[2], non_page_by_convention=[None])
    assert pd.isna(scoring.exclusion_reason(df).iat[0])


# ── add_percentile_ranks ──────────────────────────────────────────────────────

def test_percentile_ranks_do_not_mutate_the_input():
    df = frame(days_since_update=[10, 20], flesch_reading_ease=[50.0, 60.0])
    scoring.add_percentile_ranks(df)
    assert "days_since_update_pct" not in df.columns


def test_an_unmeasured_axis_stays_unmeasured():
    """A page with no reading gets NaN, never a rank of 0 — absent is not worst."""
    df = frame(days_since_update=[10, 20], flesch_reading_ease=[50.0, None])
    ranked = scoring.add_percentile_ranks(df)
    assert pd.isna(ranked["flesch_reading_ease_pct"].iat[1])


# ── directed_ranks ────────────────────────────────────────────────────────────

def test_readability_is_inverted_and_staleness_is_not():
    """+1 means a high raw value is worse, -1 means a low one is. After this,
    HIGHER ALWAYS MEANS WORSE on every column, which is what lets the
    consistently-poor cut compare them at all."""
    df = frame(days_since_update_pct=[0.9], flesch_reading_ease_pct=[0.9],
               staleness_to_age_ratio=[1.0])
    out = scoring.directed_ranks(df)
    assert out["days_since_update_directed"].iat[0] == pytest.approx(0.9)
    assert out["flesch_reading_ease_directed"].iat[0] == pytest.approx(0.1)


def test_the_staleness_modifier_discounts_a_page_maintained_across_its_life():
    """controlling-access.md: 1,168d stale on a 3,836d-old page = ratio 0.30.
    At the chosen floor of 0.5 its staleness term keeps 0.5 + 0.5*0.30 = 65% of
    its rank, while a page untouched since it was written keeps all of it."""
    df = frame(days_since_update_pct=[1.0, 1.0], flesch_reading_ease_pct=[0.5, 0.5],
               staleness_to_age_ratio=[0.30, 1.0])
    out = scoring.directed_ranks(df)["days_since_update_directed"]
    assert out.iat[0] == pytest.approx(0.65)
    assert out.iat[1] == pytest.approx(1.0)


def test_a_missing_ratio_means_no_discount_rather_than_a_zeroed_term():
    """Silently deleting a page's staleness because a context column was absent
    would be the not-measured-is-not-zero mistake, one layer down."""
    df = frame(days_since_update_pct=[1.0], flesch_reading_ease_pct=[0.5],
               staleness_to_age_ratio=[None])
    assert scoring.directed_ranks(df)["days_since_update_directed"].iat[0] == pytest.approx(1.0)


# ── consistently_poor ─────────────────────────────────────────────────────────

def test_membership_needs_every_axis_not_a_total():
    """This is what replaced the composite, and the point is that it needs no
    weight. A page terrible at one axis and fine at the other is NOT in the
    list, however extreme that one axis is."""
    directed = pd.DataFrame({"a_directed": [0.99, 0.80, 0.10, 0.20, 0.30],
                             "b_directed": [0.10, 0.85, 0.20, 0.30, 0.40]})
    poor = scoring.consistently_poor(directed, 0.75)
    # Row 0 is the most extreme page in the frame on axis a and is still absent:
    # it is fine on b, so it is not consistently anything.
    assert list(poor.index) == [1]


def test_ordering_is_the_axis_each_page_does_best_on():
    """windows-security (0.94/0.96) outranks advanced-pod-config (0.59/0.99):
    the second is the worst-reading page in the list and still ranks last,
    because being merely mid-stale is an axis it can hide behind."""
    directed = pd.DataFrame({"a_directed": [0.94, 0.59], "b_directed": [0.96, 0.99]},
                            index=["windows-security", "advanced-pod-config"])
    poor = scoring.consistently_poor(directed, 0.0)
    assert list(poor.index) == ["windows-security", "advanced-pod-config"]
    assert poor["best_axis_rank"].iat[0] == pytest.approx(0.94)


def test_a_page_missing_an_axis_cannot_be_consistently_poor():
    """Consistency cannot be established from one reading. This is why the
    partial-row problem dissolved when the composite went — a one-axis page
    simply is not eligible here."""
    directed = pd.DataFrame({"a_directed": [0.99, 0.99], "b_directed": [0.99, None]})
    assert list(scoring.consistently_poor(directed, 0.75).index) == [0]


def test_an_empty_frame_returns_an_empty_result_rather_than_raising():
    directed = pd.DataFrame({"a_directed": [], "b_directed": []})
    poor = scoring.consistently_poor(directed, 0.75)
    assert poor.empty and "best_axis_rank" in poor.columns
