"""ui/plots — pure plotly figure builders shared across tabs.

Each module here exposes plain functions that take data + return a
``plotly.graph_objects.Figure``. No streamlit imports — that belongs
in the tab layer so the builders stay unit-testable without a UI
runtime.
"""
