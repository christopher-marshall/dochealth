"""Unit tests for the text-metric functions — run with: pytest

Every case here is a real edge case we hit while building this against
kubernetes/website, written down so a future change can't silently undo the fix.
That's the point of a regression test: it isn't checking that the code works
today, it's checking that a change six weeks from now doesn't break something
you'd forgotten was hard.

Naming matters: pytest collects any file named test_*.py, and inside it any
function named test_*. Each function should assert one behaviour, and the
function name should say which — when it fails, that name is the error message.
"""
import pytest
import textstat

from dochealth import extract


# ── to_prose: frontmatter ─────────────────────────────────────────────────────

def test_frontmatter_is_stripped():
    page = "---\ntitle: Demo\n---\n\nReal body text."
    assert extract.to_prose(page).strip() == "Real body text."


def test_frontmatter_closing_delimiter_may_have_trailing_space():
    """kubernetes' ListFromCacheSnapshot.md closes its frontmatter with '--- '."""
    page = "---\ntitle: Demo\n--- \n\nReal body text."
    assert "title" not in extract.to_prose(page)


def test_horizontal_rules_in_body_are_not_frontmatter():
    """A page with no frontmatter must not lose the text between two --- rules."""
    page = "# Heading\n\nParagraph one.\n\n---\n\nParagraph two.\n"
    assert "Paragraph two." in extract.to_prose(page)


# ── to_prose: code and markup ─────────────────────────────────────────────────

def test_fenced_code_is_not_prose():
    page = "Intro words here.\n\n```yaml\napiVersion: apps/v1\nkind: Deployment\n```\n"
    assert "apiVersion" not in extract.to_prose(page)


def test_indented_fenced_code_is_not_prose():
    """206 fences in kubernetes/website are indented inside a list item, so the
    opening fence is not at column zero."""
    page = "Intro words here.\n\n- A step:\n\n    ```yaml\n    kind: Deployment\n    ```\n"
    assert "Deployment" not in extract.to_prose(page)


def test_wide_fence_is_not_ended_by_an_inner_fence():
    """Docusaurus wraps fences in ````mdx-code-block; a lazy ```-to-``` match
    pairs the outer opener with the inner one and leaks the code."""
    page = ("Intro words here.\n\n````mdx-code-block\n```bash\n"
            "npm install remark-math\n```\n````\n")
    prose = extract.to_prose(page)
    assert "npm install" not in prose and "mdx-code-block" not in prose


def test_admonition_markers_are_stripped_but_their_titles_are_kept():
    """::: is directive syntax, not reader-visible; the title after it is."""
    page = ":::warning production only\n\nThis plugin is inactive.\n\n:::\n"
    prose = extract.to_prose(page)
    assert ":::" not in prose
    assert "production only" in prose


def test_indented_admonition_markers_are_stripped():
    """Admonitions nested in a list item are indented, like fences are."""
    page = "- A step:\n\n    :::warning\n\n    Careful here.\n\n    :::\n"
    assert ":::" not in extract.to_prose(page)


def test_admonition_syntax_discussed_in_prose_is_left_alone():
    """The admonitions page writes ':::warning' mid-sentence as reader-visible
    text — only a marker at the start of its own line is directive syntax."""
    page = "This is a Docusaurus v3 :::warning admonition.\n"
    assert ":::warning" in extract.to_prose(page)


def test_link_text_survives_but_url_does_not():
    page = "See [the Pod spec](/docs/concepts/pods/) for details."
    prose = extract.to_prose(page)
    assert "the Pod spec" in prose and "/docs/concepts/" not in prose


# ── strip_tables ──────────────────────────────────────────────────────────────

def test_tables_are_stripped_but_surrounding_prose_is_kept():
    page = ("Before the table.\n"
            "| Resource | Description |\n"
            "| -------- | ----------- |\n"
            "| limits.cpu | The CPU limit. |\n"
            "After the table.\n")
    out = extract.strip_tables(page)
    assert "limits.cpu" not in out
    assert "Before the table." in out and "After the table." in out


def test_html_tables_are_stripped_like_markdown_ones():
    """pod-security-standards.md keeps its content in <table>, not pipes. Whether
    table text counts as prose must not depend on which syntax the author chose:
    unstripped, that page scored -29 Flesch; stripped, 34.6."""
    page = ("Before the table.\n"
            "<table>\n<tr><td>limits.cpu</td><td>The CPU limit.</td></tr>\n</table>\n"
            "After the table.\n")
    out = extract.strip_tables(page)
    assert "limits.cpu" not in out
    assert "Before the table." in out and "After the table." in out


def test_two_html_tables_do_not_swallow_the_prose_between_them():
    """The match must be lazy. A greedy .* runs from the first <table> to the
    LAST </table>, deleting everything in between — 596 characters of real prose
    on pod-security-standards.md, which has two tables."""
    page = ("<table><tr><td>one</td></tr></table>\n"
            "Prose between the tables.\n"
            "<table><tr><td>two</td></tr></table>\n")
    out = extract.strip_tables(page)
    assert "Prose between the tables." in out


# ── strip_html ────────────────────────────────────────────────────────────────
# Hugo permits raw HTML in .md, so strip_jsx's .mdx gate never sees it. Left
# unstripped it does not error, it just quietly wrecks two metrics: tag soup has
# no sentence-ending punctuation, so textstat reads thousands of syllables as one
# sentence, and `word_count` counts `<p>` as a word.

def test_html_tags_are_removed_but_their_text_is_kept():
    """Unlike a table, a tag is not content — only the markup goes. Removing the
    text with it would delete real words from word_count."""
    out = extract.strip_html("<li>A Pod can be <code>Running</code>.</li>")
    assert out.strip() == "A Pod can be Running."


def test_html_attributes_do_not_prevent_a_tag_being_stripped():
    """pod-security-standards.md writes <strong style="white-space: nowrap">,
    which splits into three 'words' if the attributes survive."""
    assert extract.strip_html('<strong style="white-space: nowrap">Baseline</strong>') \
        .strip() == "Baseline"


def test_capitalised_placeholders_are_not_tags():
    """<NodeIP>, <IPv4> and <CONTAINER_NAME> are placeholders in running prose on
    kubernetes pages. An earlier attempt at this fix applied strip_jsx (which
    matches any capitalised tag) to .md and deleted them."""
    page = "Set the address to <NodeIP> and the family to <IPv4>."
    assert extract.strip_html(page) == page


def test_lowercase_placeholders_that_are_not_html_elements_survive():
    """~50 distinct ones across kubernetes: <service-name>, <token-id>,
    <path-to-config>. This is why the tag list is a whitelist and not a general
    <...> pattern."""
    page = "Run it against <service-name> in <namespace-name>."
    assert extract.strip_html(page) == page


def test_html_element_names_used_as_subject_matter_survive():
    """docusaurus.config.js.mdx documents <link>, <head>, <meta> and <script> as
    its topic. A whitelist taken from the HTML spec would delete the content of
    the page that is about them — the argument for listing only the tags that
    actually cause damage."""
    page = "Docusaurus injects a <link> tag into <head>."
    assert extract.strip_html(page) == page


def test_tag_names_are_matched_whole():
    """<p> is stripped, <param> is not. The word boundary has to sit after the
    tag name, not before the closing bracket — <p\\b> is just <p> with an extra
    step, since there is always a boundary between a letter and '>'."""
    assert extract.strip_html("<p>text</p>").strip() == "text"
    assert extract.strip_html("<param>text</param>") == "<param>text</param>"


def test_html_table_words_still_count_toward_word_count():
    """The two branches diverge on purpose: table text is not prose for the
    Flesch score, but it is still words on the page. Only the tags come out of
    the word_count branch."""
    page = ("---\ntitle: Demo\n---\n\nIntro sentence here.\n"
            "<table>\n<tr><td>alpha beta gamma</td></tr>\n</table>\n")
    metrics = extract.text_metrics(page)
    assert "alpha" in extract.strip_html(extract.to_prose(page))
    assert metrics["word_count"] > len("Intro sentence here.".split())


# ── text_metrics: title ───────────────────────────────────────────────────────

def test_title_comes_from_frontmatter():
    page = "---\ntitle: Secrets\n---\n\n# Not the title\n"
    assert extract.text_metrics(page)["title"] == "Secrets"


def test_nested_frontmatter_title_is_ignored():
    """secret.md has a `title:` nested under `feature:` — must not win."""
    page = ("---\n"
            "title: Secrets\n"
            "feature:\n"
            "  title: Secret and configuration management\n"
            "---\n\nBody.\n")
    assert extract.text_metrics(page)["title"] == "Secrets"


def test_h1_is_the_fallback_when_there_is_no_frontmatter():
    page = "# Reading CSV files\n\nSome prose.\n"
    assert extract.text_metrics(page)["title"] == "Reading CSV files"


# ── text_metrics: readability guard ───────────────────────────────────────────

def test_flesch_is_none_when_there_is_no_prose():
    """textstat returns 0.0 for empty input, which reads as 'very difficult'
    rather than 'not measured' — the two must stay distinguishable."""
    page = "---\ntitle: Section index\n---\n"
    assert extract.text_metrics(page)["flesch_reading_ease"] is None


def test_flesch_is_scored_when_there_is_enough_prose():
    page = ("---\ntitle: Demo\n---\n\nThe cat sat on the mat. It was a nice day. "
            "The sun was warm. A dog walked by. The cat did not move. "
            "Later it rained.\n")
    assert extract.text_metrics(page)["flesch_reading_ease"] is not None


def test_flesch_is_none_below_the_sentence_floor():
    """Flesch averages over sentences: on two of them it is noise, not a
    measurement. The floor is about sample size, not about difficulty — a page
    with plenty of sentences keeps whatever score it earns, however bad.

    The floor of 5 is measured, not guessed: below it the 3-4 sentence bucket has
    a standard deviation of 37.9, the widest in either corpus, and at 5-9 that
    drops to 14.0 — already level with the 10-19 bucket.
    (This docstring used to cite a -29 page as the example of a genuinely dense
    one. That score was the HTML-residue bug, not difficulty; it reads 34.6 now.)"""
    page = "---\ntitle: Demo\n---\n\nThe cat sat on the mat. It was a nice day.\n"
    assert extract.text_metrics(page)["flesch_reading_ease"] is None


# ── text_metrics: headings ────────────────────────────────────────────────────

# ── strip_jsx ─────────────────────────────────────────────────────────────────

def test_mdx_comments_are_stripped():
    """836 headings across docusaurus carry a {/* #slug */} id comment, left
    behind by the 'migrate MDX heading ids to comment syntax' commit."""
    prose = extract.strip_jsx("## Content plugins {/* #content-plugins */}\n")
    assert "content-plugins" not in prose


def test_mdx_comment_strip_leaves_the_heading_itself():
    """An over-broad pattern would eat the heading line it sits on."""
    page = "## Content plugins {/* #content-plugins */}\n\nSome prose.\n"
    assert extract.text_metrics(page, ".mdx")["heading_count"] == 1


def test_import_line_strip_does_not_swallow_the_rest_of_the_page():
    """`^import\\s.+$` must stay under MULTILINE only — DOTALL would make .+
    run to the end of the file."""
    prose = extract.strip_jsx("import Tabs from '@theme/Tabs';\n\nReal body text.\n")
    assert "Real body text." in prose


def test_hash_comments_inside_code_are_not_headings():
    page = "# Real Heading\n\n```bash\n# this is a shell comment\necho hi\n```\n"
    assert extract.text_metrics(page)["heading_count"] == 1


# ── text_metrics: counts ──────────────────────────────────────────────────────

def test_heading_max_depth_is_the_deepest_level_used():
    page = "# One\n\n## Two\n\n#### Four\n\n## Two again\n"
    assert extract.text_metrics(page)["heading_max_depth"] == 4


def test_heading_max_depth_is_zero_when_there_are_no_headings():
    page = "Just a paragraph of prose with no headings at all.\n"
    assert extract.text_metrics(page)["heading_max_depth"] == 0


def test_indented_fences_are_counted():
    """kubernetes/website has 206 indented fence lines; deployment.md alone had
    72 blocks a column-zero pattern missed (23 counted vs 95 real)."""
    page = "Intro words.\n\n1. A step:\n\n    ```yaml\n    kind: Pod\n    ```\n"
    assert extract.text_metrics(page)["code_fence_count"] == 1


def test_wide_fence_wrapper_counts_as_one_block():
    """````mdx-code-block wraps a fence for authoring; the reader sees one block.
    The old [0::2] pairing counted the wrapper lines and mis-paired the rest."""
    page = "````mdx-code-block\n```bash\nnpm i\n```\n````\n"
    assert extract.text_metrics(page)["code_fence_count"] == 1


def test_exec_on_fences_are_not_counted():
    """The 'paired' exclusion, pinned: an exec="on" fence is a
    runnable example, not a code sample."""
    page = 'Intro words.\n\n```python exec="on"\nprint(1)\n```\n'
    assert extract.text_metrics(page)["code_fence_count"] == 0


def test_code_block_density_is_fences_per_thousand_words():
    """Brief's definition: code fences / words * 1000."""
    page = "one two three four five\n\n```py\nx = 1\n```\n"
    m = extract.text_metrics(page)
    # 1 fenced block, 5 prose words -> 1 / 5 * 1000
    assert m["code_block_density"] == 200


def test_code_block_density_is_zero_when_there_is_no_prose():
    page = "```py\nx = 1\n```\n"
    assert extract.text_metrics(page)["code_block_density"] == 0


def test_internal_links_are_counted():
    page = "See [Pods](/docs/concepts/pods/) and [Volumes](../storage/volumes.md).\n"
    assert extract.text_metrics(page)["internal_link_count"] == 2


def test_external_links_are_not_internal():
    page = "See [the KEP](https://github.com/kubernetes/enhancements) for detail.\n"
    assert extract.text_metrics(page)["internal_link_count"] == 0


def test_anchors_and_mailto_are_not_internal_links():
    """A same-page anchor isn't a link to another page; nor is an email."""
    page = "Jump to [the section](#configuration) or mail [us](mailto:a@b.com).\n"
    assert extract.text_metrics(page)["internal_link_count"] == 0


def test_image_embeds_are_not_links():
    """![alt](x.png) is an embed, not a link to a page — the leading ! is the
    only thing distinguishing it, so the pattern needs a negative lookbehind."""
    page = "![A flowchart](/images/flowchart.png)\n"
    assert extract.text_metrics(page)["internal_link_count"] == 0


def test_internal_links_are_counted_from_raw_text_not_prose():
    """to_prose() rewrites [text](url) down to just `text`, so counting links
    on prose finds nothing. This metric must read the raw markdown."""
    page = "---\ntitle: Demo\n---\n\nSee [Pods](/docs/concepts/pods/).\n"
    assert extract.text_metrics(page)["internal_link_count"] == 1


# ── is_non_page_by_convention ─────────────────────────────────────────────────

def test_underscore_filename_is_a_non_page_when_the_corpus_says_so():
    """Docusaurus' two partials are the whole roster on that corpus."""
    assert extract.is_non_page_by_convention("_markdown-partial-example.mdx", "_")


def test_no_prefix_configured_means_nothing_is_flagged():
    """kubernetes_config.py omits non_page_prefix on purpose: Hugo's leading
    underscore means "section index", and workloads/pods/_index.md is 3,176
    words of real content. A shared default would delete the corpus."""
    assert extract.is_non_page_by_convention("_index.md", None) is False


def test_ordinary_filename_is_not_flagged():
    assert extract.is_non_page_by_convention("configmap.md", "_") is False


def test_the_prefix_is_tested_against_the_basename_not_the_path():
    """Pages live at website/docs/api/plugins/_partial-....mdx, so a rule
    applied to the full path would never match anything."""
    assert extract.is_non_page_by_convention("_partial-tags.mdx", "_") is True


# ── strip_inline_code ─────────────────────────────────────────────────────────
# Sketched ahead of the implementation and green as of 2026-08-17.
#
# The rule, which is the table rule applied to a second case:
# inline code is prose for word_count (it is substance a reader reads) and NOT
# prose for Flesch (it is not natural language). Two composable functions, not a
# flag on to_prose.
#
# Measured across both corpora before writing any of this:
#   * 20% of kubernetes' 7,131 inline spans and 15% of docusaurus' 3,799 contain
#     a period, so this is TWO defects, pulling in opposite directions —
#     inflated syllables-per-word AND invented sentence boundaries.
#   * Median Flesch moves +2.6 (kubernetes) / +3.6 (docusaurus) if fixed.
#     Worst page: plugin-content-docs.mdx, 5.50 -> 42.61.
#   * No page in either corpus drops below MIN_SENTENCES_FOR_FLESCH as a result.

def test_dotted_inline_code_does_not_invent_sentence_boundaries():
    """THE headline defect. textstat treats every '.' as a sentence end, so a
    dotted identifier splits one sentence into three and the page reads as far
    snappier than it is. Real example from the docusaurus corpus."""
    page = ("Set the `siteConfig.themeConfig.navbar` value in "
            "`docusaurus.config.js` to enable it.")
    assert textstat.sentence_count(extract.strip_inline_code(page)) == 1


def test_a_span_occupies_exactly_one_word_slot():
    """A placeholder, not a deletion. Deleting gives 'Run first.' — which
    understates the sentence by dropping one of its referents. A reader parses a
    code span as a single item, so it keeps one slot however many tokens it held."""
    page = "Run `kubectl get pods -o wide` first."
    assert textstat.lexicon_count(extract.strip_inline_code(page)) == 3


def test_the_placeholder_is_one_syllable():
    """The whole point is to stop `PodDisruptionBudget` contributing seven
    syllables of API name to a readability score."""
    out = extract.strip_inline_code("Read the `PodDisruptionBudget` docs now.")
    assert textstat.syllable_count(out) == textstat.syllable_count("Read the x docs now.")


def test_identifier_heavy_prose_scores_like_its_plain_twin():
    """End to end through text_metrics: two pages with identical sentence
    structure should score the same, whether the nouns are API names or not."""
    frame = ("The {0} controls rollout. Set the {0} carefully. "
             "A bad {0} breaks things. Check the {0} first. Then ship the {0}.")
    coded = frame.format("`spec.template.metadata.labels`")
    plain = frame.format("field")
    assert (extract.text_metrics(coded)["flesch_reading_ease"]
            == pytest.approx(extract.text_metrics(plain)["flesch_reading_ease"], abs=1.0))


def test_it_must_run_on_raw_text_not_on_prose():
    """The ordering trap, and the same species as tables-before-tags:
    to_prose already unwraps `x` to a bare x, so by then
    there are no backticks left to find. strip_inline_code has to see the raw
    markdown, which means a second to_prose pass on the Flesch branch."""
    page = "Set `a.b.c` now."
    assert "`" not in extract.to_prose(page)              # why the order matters
    assert "a.b.c" not in extract.strip_inline_code(page)  # it works on raw text


# These two pin the ORDER, which is a property of text_metrics rather than of
# strip_inline_code — that function is given fence-free text by its caller and
# would happily chew a raw fence, which is exactly why the order exists. Both
# were first written against strip_inline_code directly and were testing the
# wrong unit.
#
# The assertion is that adding a fenced block changes NOTHING about the score.
# If inline stripping ran first it would eat the fence delimiters, FENCE_BLOCK_RE
# would then find no fence to remove, and the code body would land in the prose.

PROSE = ("Intro sentence here. Second one goes here. Third one follows. "
         "Fourth one after that. Fifth one at last.")


def test_a_fence_containing_inline_code_does_not_reach_the_flesch_score():
    """One inline span inside a fence shifts the backtick pairing by one, so `x`
    escapes into the prose while the code around it is consumed. Measured
    2026-08-17, before the ordering was settled."""
    page = PROSE + "\n\n```yaml\nkind: Pod\nname: `x`\n```\n"
    assert (extract.text_metrics(page)["flesch_reading_ease"]
            == extract.text_metrics(PROSE)["flesch_reading_ease"])


def test_a_four_backtick_fence_does_not_reach_the_flesch_score():
    """THE case with real pages behind it: docusaurus wraps fences in
    ````mdx-code-block on 6 pages / 42 fence lines. Four backticks
    pair as two empty inline spans, so the delimiters vanish and the body is left
    behind as prose."""
    page = PROSE + "\n\n````mdx-code-block\ninner: value\n````\n"
    assert (extract.text_metrics(page)["flesch_reading_ease"]
            == extract.text_metrics(PROSE)["flesch_reading_ease"])


def test_word_count_still_counts_inline_code():
    """NO xfail: this passes today and must keep passing. It is the invariant
    that keeps the two branches independent: the Flesch branch strips inline
    code, the word_count branch does not, so a
    page's length never changes because of how this fix is implemented."""
    page = "---\ntitle: Demo\n---\n\nSet `replicas` to three."
    assert extract.text_metrics(page)["word_count"] == 4


# ── parse_blame_times ─────────────────────────────────────────────────────────
# Sketched ahead of the implementation and green as of 2026-08-17.
#
# WHY the metric: `days_since_update` says when the FILE was last touched, which
# a typo fix resets. Median line age says how old the CONTENT is, and the two
# disagree usefully — measured across 20 kubernetes pages, Spearman +0.38.
# manage-resources-containers.md was edited 54 days ago and its median line is
# 1,838 days old. That is the brief's "staleness is not neglect" credibility risk
# attacked from the other side.
#
# WHY only the parser is tested here: `file_history` has no pytest coverage
# either — anything that shells out to git is checked against the real clone in
# check.py, because a fixture repo would be testing the fixture. Splitting a pure
# `parse_blame_times(porcelain) -> list[int]` out of the subprocess call is what
# makes any of it unit-testable at all.
#
# Run blame with -w so a whitespace-only reformat does not reset every line's
# age. That is the same class of noise `noise_re` handles for commits, and blame
# has no other filter for it.

PORCELAIN_ONE_LINE = (
    "7f3b633aa0332b8a1ddc61b6d4e2e34fc6dd5522 1 1 1\n"
    "author Bjørn Erik Pedersen\n"
    "author-mail <bjorn.erik.pedersen@gmail.com>\n"
    "author-time 1525536051\n"
    "author-tz +0200\n"
    "committer k8s-ci-robot\n"
    "committer-mail <k8s-ci-robot@users.noreply.github.com>\n"
    "committer-time 1600000000\n"
    "committer-tz -0700\n"
    "summary Convert site to Hugo (#8316)\n"
    "filename content/en/docs/concepts/overview/_index.md\n"
    "\t---\n"
)


def test_one_author_time_per_blamed_line():
    two = PORCELAIN_ONE_LINE + PORCELAIN_ONE_LINE.replace("1525536051", "1600000001")
    assert extract.parse_blame_times(two) == [1525536051, 1600000001]


def test_committer_time_is_not_author_time():
    """Both sit at column zero, one letter apart in shape. author-time is when
    the content was WRITTEN; committer-time is when it landed, and they diverge
    on every rebased or cherry-picked commit. The fixture above deliberately
    gives them different values so a loose match shows up as a wrong answer
    rather than a passing test."""
    assert extract.parse_blame_times(PORCELAIN_ONE_LINE) == [1525536051]


def test_a_content_line_mentioning_author_time_is_not_metadata():
    """Blame prefixes the file's own content with a TAB. A docs page explaining
    `git blame --line-porcelain` would otherwise be aged by the timestamps in its
    own example output — self-poisoning, and exactly the species of bug that
    the HTML-tag and inline-code parser bugs were."""
    page = PORCELAIN_ONE_LINE.replace("\t---\n", "\tauthor-time 999\n")
    assert extract.parse_blame_times(page) == [1525536051]


def test_no_blame_output_yields_no_times():
    """An empty file blames to nothing. The caller needs a list it can check
    before taking a median, not an exception."""
    assert extract.parse_blame_times("") == []
