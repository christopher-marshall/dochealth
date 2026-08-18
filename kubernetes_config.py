CONFIG = {
    # No noise_re as Kubernetes commit messages are not standardized.
    # No non_page_prefix: Hugo's leading underscore means "section index", not
    # "partial", and 15 of the 21 underscore files here carry real content —
    # workloads/pods/_index.md is 3,176 words. Setting "_" would delete them.
    # Empty stubs like concepts/storage/_index.md are caught by content shape.
    # No example patterns as Kubernetes uses ``` code fences.
}
