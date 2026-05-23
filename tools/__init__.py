"""Operator-facing CLI tools that live outside the Streamlit runtime.

The modules in this package are invoked via ``python -m tools.<name>``
from a shell, a Docker container, or a scheduler. They are NOT imported
by the Streamlit UI — keep heavyweight dependencies and side-effects
confined to their ``_main`` entry points so importing the package stays
cheap.
"""
