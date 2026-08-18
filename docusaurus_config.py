import re

CONFIG = {
    "noise_re": re.compile(r"^chore(\(.+\))?:"),
    # A leading underscore means "partial, never rendered" in Docusaurus. Both
    # such files in this corpus are include fragments, not pages:
    # _markdown-partial-example.mdx and _partial-tags-file-api-ref-section.mdx.
    "non_page_prefix": "_",
    # No example patterns as Docusaurus2 uses ``` code fences.
}
