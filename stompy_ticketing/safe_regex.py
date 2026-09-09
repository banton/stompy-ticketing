r"""Shared, non-backtracking regex policy for user-supplied search filters.

RE2 syntax, not Python re: no look-around/backreferences; character classes
such as \w are ASCII. Unsupported syntax is refused, never run by a fallback
engine. Hosts may reuse this compiler so every search door has one policy.
"""

import re2

MAX_PATTERN_LENGTH = 500


def compile_search_regex(pattern: str):
    """Compile a case-insensitive, linear-time search with bounded engine memory.

    Raises ValueError on invalid/unsupported syntax or excessive pattern size.
    The re2 wrapper caches at most 128 compiled patterns; each gets 1 MiB of RE2
    memory instead of the default 8 MiB. This is not a wall-clock timeout or
    a bound on database retrieval/input volume.
    """
    if not isinstance(pattern, str):
        raise ValueError("Regex pattern must be a string")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Regex pattern too long (max {MAX_PATTERN_LENGTH} chars)")
    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False  # User patterns must not be echoed to native stderr.
    options.max_mem = 1024 * 1024
    try:
        return re2.compile(pattern, options=options)
    except (re2.error, UnicodeError) as exc:
        raise ValueError(
            "Invalid or unsupported RE2 regex pattern "
            "(look-around and backreferences are not supported)"
        ) from exc
