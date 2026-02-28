"""
Soft UI shared components — imported by every tab mixin.
Provides button styles, page headers, stat mini-cards, and card wrappers.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QFrame, QSizePolicy)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

# ── Gradient button styles ───────────────────────────────────────────────────
BTN_PRIMARY   = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #3b82f6,stop:1 #60a5fa); color: #ffffff; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")
BTN_SUCCESS   = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #10b981,stop:1 #34d399); color: #ffffff; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")
BTN_DANGER    = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #ef4444,stop:1 #f87171); color: #ffffff; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")
BTN_WARNING   = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #f59e0b,stop:1 #fbbf24); color: #1c1917; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")
BTN_SECONDARY = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #334155,stop:1 #475569); color: #e2e8f0; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")
BTN_INFO      = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #0ea5e9,stop:1 #38bdf8); color: #ffffff; "
                 "font-weight: 700; font-size: 12px; padding: 9px 20px; "
                 "border-radius: 8px; border: none; letter-spacing: 0.3px;")

# ── Page title builder ───────────────────────────────────────────────────────
def mk_page_title(title: str, subtitle: str = "") -> QWidget:
    """Returns a widget with a large bold title + optional muted subtitle."""
    w = QWidget()
    w.setStyleSheet("background:transparent;")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 8)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setFont(QFont("Inter", 22, QFont.Weight.Bold))
    t.setStyleSheet("background:transparent; border:none;")
    lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setStyleSheet("color:#64748b; font-size:12px; background:transparent; border:none;")
        lay.addWidget(s)
    return w

# ── Inline stat badge ────────────────────────────────────────────────────────
def mk_stat_badge(label: str, value: str, accent: str = "#60a5fa") -> QFrame:
    """Small horizontal info badge used in card footers."""
    f = QFrame()
    f.setStyleSheet(f"background:{accent}18; border:1px solid {accent}44; border-radius:8px;")
    lay = QHBoxLayout(f)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(6)
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color:#64748b; font-size:11px; font-weight:600; background:transparent;")
    val = QLabel(value)
    val.setStyleSheet(f"color:{accent}; font-size:11px; font-weight:700; background:transparent;")
    lay.addWidget(lbl)
    lay.addWidget(val)
    return f

# ── Section divider label ────────────────────────────────────────────────────
def mk_section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "color:#475569; font-size:10px; font-weight:700; "
        "letter-spacing:1.5px; background:transparent; border:none; "
        "padding: 8px 0 4px 0;")
    return lbl

# ── Card frame ───────────────────────────────────────────────────────────────
def mk_card(bg: str = "#111827", border: str = "#1e2d45",
            radius: int = 16) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame{{background:{bg}; border:1px solid {border}; "
        f"border-radius:{radius}px;}} "
        f"QLabel{{background:transparent; border:none;}}")
    return f
