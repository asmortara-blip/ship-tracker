"""worker package — out-of-process schedulers and background jobs.

These modules run *outside* the Streamlit process. They must not import
`streamlit` (no `st.*` calls, no cached_resource decorators), and they
must be safely invocable from a unix cron job or a Docker CMD.
"""
