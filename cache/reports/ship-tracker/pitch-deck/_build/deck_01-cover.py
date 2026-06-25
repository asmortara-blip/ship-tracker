"""Slide 01 — the deck cover. D.cover() draws the full cover."""
import sys
sys.path.insert(0, "/tmp/viz_ship")
import _deck as D

OUT = "/Users/aaronmortara/MC/Models/reports/final/Ship Tracker/Pitch Deck (v2)/01-cover.pdf"

fig, ax = D.cover()
D.save(fig, OUT)
print("wrote", OUT)
