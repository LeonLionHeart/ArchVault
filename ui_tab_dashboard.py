"""
ui_tab_dashboard.py  —  ArchVault v5.0.0-beta
Editable drag-and-drop dashboard with 2-D tile grid.
  • Tiles have both column span (1–4) and row span (1–4)
  • Resize from any edge: left/right = cols, top/bottom = rows
  • 2-D bin-packing reflow ensures tiles pack tightly
  • Lock/unlock, add/remove, drag-to-reorder
  • Layout persisted as [(id, cols, rows), …]
  • Fully theme-aware
"""
import os, json, subprocess
from datetime import datetime, timedelta
from functools import partial

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QSizePolicy, QScrollArea, QGridLayout, QMenu, QApplication,
)
from PyQt6.QtCore import (
    Qt, QTimer, QRectF, QMimeData, QPoint, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QFont, QColor, QBrush, QDrag, QPainter, QPen,
    QPainterPath, QPixmap, QCursor,
)
from ui_widgets import confirm_action

VERSION = "v5.0.0-beta"

_GREEN  = "#10b981"; _RED = "#ef4444"; _BLUE = "#38bdf8"
_INDIGO = "#818cf8"; _AMBER = "#f59e0b"; _TEAL = "#2dd4bf"
_VIOLET = "#a78bfa"; _ORANGE = "#f97316"

STATUS_COLORS = {
    "Completed": _GREEN, "Running": _BLUE, "Stalled": _AMBER,
    "Failed": _RED, "Error": _RED, "Cancelled": _VIOLET,
}

SETTINGS_FILE = "/etc/archvault/settings.json"
ROW_UNIT = 90        # px per grid row unit
MAX_GRID_ROWS = 40   # upper bound for packing scan

# ═════════════════════════════════════════════════════════════════════════════
#  TILE REGISTRY  (cols × rows defaults)
# ═════════════════════════════════════════════════════════════════════════════
TILE_CATALOG = [
    {"id": "stat_row",       "label": "Summary Stats",        "cols": 4, "rows": 1, "default": True},
    {"id": "quick_actions",  "label": "Quick Actions",        "cols": 4, "rows": 1, "default": True},
    {"id": "chart_donut",    "label": "Success Rate Chart",   "cols": 2, "rows": 2, "default": True},
    {"id": "chart_spark",    "label": "7-Day Activity",       "cols": 2, "rows": 2, "default": True},
    {"id": "last_job",       "label": "Last Job",             "cols": 2, "rows": 2, "default": True},
    {"id": "next_task",      "label": "Next Scheduled Task",  "cols": 2, "rows": 2, "default": True},
    {"id": "disk_usage",     "label": "Disk Usage",           "cols": 4, "rows": 2, "default": True},
    {"id": "recent_jobs",    "label": "Recent Jobs",          "cols": 4, "rows": 3, "default": True},
    {"id": "system_health",  "label": "System Health",        "cols": 2, "rows": 2, "default": False},
    {"id": "active_timers",  "label": "Active Timers",        "cols": 2, "rows": 2, "default": False},
    {"id": "backup_size",    "label": "Total Backup Size",    "cols": 1, "rows": 1, "default": False},
    {"id": "protection",     "label": "Protection Status",    "cols": 2, "rows": 2, "default": False},
    {"id": "scheduler_cal",  "label": "Schedule Calendar",    "cols": 2, "rows": 1, "default": False},
]

_DEFAULT_LAYOUT = [(t["id"], t["cols"], t["rows"])
                   for t in TILE_CATALOG if t["default"]]
_CATALOG_MAP = {t["id"]: t for t in TILE_CATALOG}


# ═════════════════════════════════════════════════════════════════════════════
#  PAINT-ONLY CHART WIDGETS
# ═════════════════════════════════════════════════════════════════════════════

class _DonutChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._completed = 0; self._total = 0
        self._bg_ring = "#1e2d45"; self._text_color = "#e2e8f0"

    def set_data(self, completed, total, ring_color="#1e2d45",
                 text_color="#e2e8f0"):
        self._completed = completed; self._total = total
        self._bg_ring = ring_color; self._text_color = text_color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        side = min(w, h) - 20
        if side < 40:
            p.end(); return
        thick = max(8, side // 9)
        cx, cy = w / 2, h / 2
        rect = QRectF(cx - side/2, cy - side/2, side, side)
        p.setPen(QPen(QColor(self._bg_ring), thick,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        if self._total > 0:
            pct = self._completed / self._total
            p.setPen(QPen(QColor("#ef4444"), thick,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, -360 * 16)
            span = int(pct * 360 * 16)
            p.setPen(QPen(QColor("#00ff87"), thick,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, -span)
        p.setPen(QColor(self._text_color))
        fsize = max(10, side // 6)
        p.setFont(QFont("Segoe UI", fsize, QFont.Weight.Bold))
        txt = (f"{round(self._completed/self._total*100)}%"
               if self._total else "—")
        p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class _BarSparkline(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._bars = []

    def set_data(self, bars):
        self._bars = bars; self.update()

    def paintEvent(self, _):
        if not self._bars:
            return
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._bars)
        pad_x, pad_bot, pad_top, gap = 8, 20, 8, 5
        chart_h = h - pad_top - pad_bot
        if chart_h < 10:
            p.end(); return
        bar_w = (w - 2*pad_x - gap*(n-1)) / n
        max_v = max((g+f for _, g, f in self._bars), default=1) or 1
        for i, (lbl, good, fail) in enumerate(self._bars):
            x = pad_x + i * (bar_w + gap)
            y_base = h - pad_bot
            total = good + fail
            if total:
                bh = (total / max_v) * chart_h
                if fail:
                    fh = (fail / max_v) * chart_h
                    path = QPainterPath()
                    path.addRoundedRect(
                        QRectF(x, y_base - fh, bar_w, fh), 3, 3)
                    p.fillPath(path, QColor("#ef4444"))
                if good:
                    gh = (good / max_v) * chart_h
                    gy = y_base - bh
                    path = QPainterPath()
                    path.addRoundedRect(
                        QRectF(x, gy, bar_w, gh), 3, 3)
                    p.fillPath(path, QColor("#00ff87"))
            else:
                path = QPainterPath()
                path.addRoundedRect(
                    QRectF(x, y_base - 3, bar_w, 3), 2, 2)
                p.fillPath(path, QColor("#1e2d45"))
            p.setPen(QColor("#4a5568"))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRectF(x, y_base + 4, bar_w, pad_bot),
                       Qt.AlignmentFlag.AlignHCenter
                       | Qt.AlignmentFlag.AlignTop, lbl)
        p.end()


# ═════════════════════════════════════════════════════════════════════════════
#  DRAGGABLE / RESIZABLE TILE WRAPPER
# ═════════════════════════════════════════════════════════════════════════════

_EDGE = 8  # px — edge resize hit zone

class DashboardTile(QFrame):
    """Tile with 4-edge resize (cols & rows) and titlebar drag."""
    tile_removed = pyqtSignal(str)
    tile_drag_started = pyqtSignal(str)
    tile_resized = pyqtSignal(str, int, int)  # id, new_cols, new_rows

    def __init__(self, tile_id, label, content_widget, cols, rows,
                 bg, border, fg, muted, locked=True):
        super().__init__()
        self.tile_id = tile_id
        self._cols = cols; self._rows = rows
        self._locked = locked
        self._bg = bg; self._border = border
        self._fg = fg; self._muted = muted
        self._drag_start = None
        self._resize_edge = None   # "l","r","t","b"
        self._resize_start_pos = 0
        self._resize_start_cols = cols
        self._resize_start_rows = rows

        self.setObjectName(f"tile_{tile_id}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        self._titlebar = QWidget()
        self._titlebar.setFixedHeight(32)
        self._titlebar.setStyleSheet("background:transparent;")
        tb = QHBoxLayout(self._titlebar)
        tb.setContentsMargins(12, 4, 6, 4); tb.setSpacing(6)

        self._drag_icon = QLabel("⠿")
        self._drag_icon.setStyleSheet(
            f"color:{muted};font-size:14px;background:transparent;"
            "border:none;")
        self._drag_icon.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._title_label = QLabel(label.upper())
        self._title_label.setStyleSheet(
            f"color:{muted};font-size:10px;font-weight:bold;"
            "letter-spacing:1.5px;background:transparent;border:none;")
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._btn_remove = QPushButton("✕")
        self._btn_remove.setFixedSize(22, 22)
        self._btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_remove.setStyleSheet(
            f"QPushButton{{color:{muted};background:transparent;"
            f"border:none;font-size:13px;font-weight:bold;"
            f"border-radius:4px;}}"
            f"QPushButton:hover{{color:#ef4444;background:{border};}}")
        self._btn_remove.clicked.connect(
            lambda: self.tile_removed.emit(self.tile_id))

        tb.addWidget(self._drag_icon)
        tb.addWidget(self._title_label)
        tb.addStretch()
        tb.addWidget(self._btn_remove)

        root.addWidget(self._titlebar)
        self._content = content_widget
        root.addWidget(content_widget, 1)

        self.setMouseTracking(True)
        self.set_locked(locked)

    def _apply_style(self):
        self.setStyleSheet(
            f"DashboardTile, QFrame#tile_{self.tile_id}{{"
            f"background-color:{self._bg};"
            f"border:1px solid {self._border};"
            f"border-radius:14px;}}"
            f"QLabel{{background:transparent;border:none;"
            f"text-decoration:none;}}")

    def set_locked(self, locked):
        self._locked = locked
        self._drag_icon.setVisible(not locked)
        self._btn_remove.setVisible(not locked)
        if locked:
            self._titlebar.setFixedHeight(28)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self._titlebar.setFixedHeight(32)

    def retheme(self, bg, border, fg, muted):
        self._bg = bg; self._border = border
        self._fg = fg; self._muted = muted
        self._apply_style()
        self._drag_icon.setStyleSheet(
            f"color:{muted};font-size:14px;background:transparent;"
            "border:none;")
        self._title_label.setStyleSheet(
            f"color:{muted};font-size:10px;font-weight:bold;"
            "letter-spacing:1.5px;background:transparent;border:none;")
        self._btn_remove.setStyleSheet(
            f"QPushButton{{color:{muted};background:transparent;"
            f"border:none;font-size:13px;font-weight:bold;"
            f"border-radius:4px;}}"
            f"QPushButton:hover{{color:#ef4444;background:{border};}}")

    # ── Edge hit test ─────────────────────────────────────────────────────
    def _edge_at(self, pos):
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        # Corners: prefer horizontal if truly in a corner
        if x <= _EDGE:
            return "l"
        if x >= w - _EDGE:
            return "r"
        if y <= _EDGE:
            return "t"
        if y >= h - _EDGE:
            return "b"
        return None

    # ── Mouse events ──────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if self._locked or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        pos = e.position().toPoint()
        edge = self._edge_at(pos)
        if edge:
            self._resize_edge = edge
            if edge in ("l", "r"):
                self._resize_start_pos = e.globalPosition().x()
            else:
                self._resize_start_pos = e.globalPosition().y()
            self._resize_start_cols = self._cols
            self._resize_start_rows = self._rows
            return
        if self._titlebar.geometry().contains(pos):
            self._drag_start = pos
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()

        # Active resize
        if self._resize_edge is not None:
            parent = self.parentWidget()
            if self._resize_edge in ("l", "r"):
                parent_w = parent.width() if parent else 800
                col_w = max(parent_w / 4, 80)
                dx = e.globalPosition().x() - self._resize_start_pos
                if self._resize_edge == "l":
                    dx = -dx
                delta = round(dx / col_w)
                new_cols = max(1, min(4,
                                      self._resize_start_cols + delta))
                if new_cols != self._cols:
                    self._cols = new_cols
                    self.tile_resized.emit(
                        self.tile_id, self._cols, self._rows)
            else:
                dy = e.globalPosition().y() - self._resize_start_pos
                if self._resize_edge == "t":
                    dy = -dy
                delta = round(dy / ROW_UNIT)
                new_rows = max(1, min(4,
                                      self._resize_start_rows + delta))
                if new_rows != self._rows:
                    self._rows = new_rows
                    self.tile_resized.emit(
                        self.tile_id, self._cols, self._rows)
            return

        # Cursor feedback
        if not self._locked:
            edge = self._edge_at(pos)
            if edge in ("l", "r"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edge in ("t", "b"):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif self._titlebar.geometry().contains(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        # Drag
        if (self._drag_start is not None
                and (pos - self._drag_start).manhattanLength() > 20):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(self.tile_id)
            drag.setMimeData(mime)
            pix = QPixmap(self.size())
            pix.fill(Qt.GlobalColor.transparent)
            self.render(pix)
            painter = QPainter(pix)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.fillRect(pix.rect(), QColor(0, 0, 0, 160))
            painter.end()
            drag.setPixmap(pix)
            drag.setHotSpot(self._drag_start)
            self.tile_drag_started.emit(self.tile_id)
            drag.exec(Qt.DropAction.MoveAction)
            self._drag_start = None
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        self._resize_edge = None
        super().mouseReleaseEvent(e)


# ═════════════════════════════════════════════════════════════════════════════
#  DROP-TARGET GRID — 2-D bin packing (4 cols, unlimited rows)
# ═════════════════════════════════════════════════════════════════════════════

class TileGrid(QWidget):
    layout_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._tiles: list[DashboardTile] = []
        self._grid = QGridLayout(self)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(8, 4, 8, 20)
        # Equal column widths
        for c in range(4):
            self._grid.setColumnStretch(c, 1)

    def add_tile(self, tile: DashboardTile):
        self._tiles.append(tile)
        tile.tile_removed.connect(self._on_tile_removed)
        tile.tile_resized.connect(self._on_tile_resized)
        self._reflow()

    def remove_tile(self, tile_id: str):
        self._tiles = [t for t in self._tiles if t.tile_id != tile_id]
        self._reflow()
        self.layout_changed.emit()

    def get_layout_order(self) -> list:
        return [(t.tile_id, t._cols, t._rows) for t in self._tiles]

    def set_locked(self, locked):
        for t in self._tiles:
            t.set_locked(locked)

    def _on_tile_removed(self, tile_id):
        for t in self._tiles:
            if t.tile_id == tile_id:
                t.setParent(None); t.deleteLater(); break
        self.remove_tile(tile_id)

    def _on_tile_resized(self, tile_id, new_cols, new_rows):
        self._reflow()
        self.layout_changed.emit()

    def _reflow(self):
        """2-D bin-packing: place tiles into first-fit grid slots."""
        # Remove everything from grid layout
        while self._grid.count():
            self._grid.takeAt(0)

        occupied = set()  # (row, col)

        for tile in self._tiles:
            c_span = min(tile._cols, 4)
            r_span = min(tile._rows, 4)
            placed = False

            for r in range(MAX_GRID_ROWS):
                for c in range(4):
                    if c + c_span > 4:
                        continue
                    # Check every cell in the proposed span
                    cells = [(r + dr, c + dc)
                             for dr in range(r_span)
                             for dc in range(c_span)]
                    if any(cell in occupied for cell in cells):
                        continue
                    # Place tile here
                    for cell in cells:
                        occupied.add(cell)
                    self._grid.addWidget(tile, r, c, r_span, c_span)
                    placed = True
                    break
                if placed:
                    break

        # Set row heights based on ROW_UNIT
        max_row = max((r for r, _ in occupied), default=0) + 1
        for r in range(max_row):
            self._grid.setRowMinimumHeight(r, ROW_UNIT)
        # Clear any leftover taller rows from previous reflow
        for r in range(max_row, max_row + 10):
            self._grid.setRowMinimumHeight(r, 0)

    # ── Drag / Drop ───────────────────────────────────────────────────────
    def dragEnterEvent(self, e):
        if e.mimeData().hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        source_id = e.mimeData().text()
        drop_pos = e.position().toPoint()
        target_idx = len(self._tiles)
        for i, tile in enumerate(self._tiles):
            if tile.tile_id == source_id:
                continue
            r = tile.geometry()
            center = r.center()
            if (drop_pos.y() < center.y()
                    or (drop_pos.y() < center.y() + r.height()//2
                        and drop_pos.x() < center.x())):
                target_idx = i; break
        src_tile = None; src_idx = -1
        for i, t in enumerate(self._tiles):
            if t.tile_id == source_id:
                src_tile = t; src_idx = i; break
        if src_tile is None:
            return
        self._tiles.pop(src_idx)
        if target_idx > src_idx:
            target_idx -= 1
        self._tiles.insert(target_idx, src_tile)
        self._reflow()
        self.layout_changed.emit()
        e.acceptProposedAction()


# ═════════════════════════════════════════════════════════════════════════════
#  TABLE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _apply_table_css(tbl, bg, inp, border, fg="#e2e8f0"):
    sel_bg = f"{border}44"
    tbl.setStyleSheet(f"""
        QTableWidget {{
            background-color: transparent; color: {fg};
            border: none; font-size: 12px;
        }}
        QHeaderView::section {{
            background-color: {inp}; color: #8892b0;
            font-weight: bold; font-size: 10px; letter-spacing: 1px;
            border: none; border-bottom: 1px solid {border};
            padding: 8px 12px;
        }}
        QTableWidget::item {{
            background-color: transparent;
            border-bottom: 1px solid {border}44; padding: 8px 12px;
        }}
        QTableWidget::item:selected {{
            background-color: {sel_bg};
            color: {fg}; font-weight: bold;
        }}
        QScrollBar:vertical {{ background: transparent; width: 6px; }}
        QScrollBar::handle:vertical {{
            background: {border}; border-radius: 3px;
        }}
    """)


def _mk_table(headers, bg, inp, border, fg="#e2e8f0"):
    tbl = QTableWidget(0, len(headers))
    tbl.setHorizontalHeaderLabels(headers)
    tbl.verticalHeader().hide()
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.setAlternatingRowColors(False)
    tbl.setSortingEnabled(False); tbl.setShowGrid(False)
    _apply_table_css(tbl, bg, inp, border, fg)
    return tbl


# ═════════════════════════════════════════════════════════════════════════════
#  STAT CARD
# ═════════════════════════════════════════════════════════════════════════════

class _StatMini(QFrame):
    def __init__(self, title, icon, accent, bg, border, fg):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        self._accent = accent; self._fg = fg
        self.setFixedHeight(90)
        self.setStyleSheet(
            f"QFrame{{background-color:{bg};border:1px solid {border};"
            f"border-radius:12px;}}"
            f"QLabel{{background:transparent;border:none;"
            f"text-decoration:none;}}")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 10, 12, 10); outer.setSpacing(8)
        left = QVBoxLayout(); left.setSpacing(1)
        self._lbl_title = QLabel(title.upper())
        self._lbl_title.setStyleSheet(
            "color:#8892b0;font-size:9px;font-weight:bold;"
            "letter-spacing:1.2px;")
        self._lbl_value = QLabel("—")
        self._lbl_value.setStyleSheet(
            f"color:{fg};font-size:24px;font-weight:bold;")
        self._lbl_sub = QLabel("")
        self._lbl_sub.setStyleSheet("color:#4a5568;font-size:10px;")
        left.addWidget(self._lbl_title)
        left.addWidget(self._lbl_value)
        left.addWidget(self._lbl_sub); left.addStretch()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            f"color:{accent};font-size:20px;font-weight:bold;"
            "background:transparent;border:none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFixedSize(32, 32)
        outer.addLayout(left, 1)
        outer.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

    def set_value(self, val, sub=""):
        self._lbl_value.setText(str(val))
        self._lbl_sub.setText(sub)

    def retheme(self, bg, border, fg):
        self._fg = fg
        self.setStyleSheet(
            f"QFrame{{background-color:{bg};border:1px solid {border};"
            f"border-radius:12px;}}"
            f"QLabel{{background:transparent;border:none;"
            f"text-decoration:none;}}")
        self._lbl_value.setStyleSheet(
            f"color:{fg};font-size:24px;font-weight:bold;")


# ═════════════════════════════════════════════════════════════════════════════
#  DASHBOARD MIXIN
# ═════════════════════════════════════════════════════════════════════════════

class DashboardMixin:

    def _make_stat_card(self, title, value, accent_color, subtitle=""):
        c = self._tc()
        card = _StatMini(title, "▪", accent_color,
                         c["panel"], c["border"], c["fg"])
        card.set_value(value, subtitle)
        return card

    def _tc(self) -> dict:
        t = getattr(self, "_current_theme", {})
        is_light = (t.get("bg", "#0b").startswith("#f")
                    or t.get("bg", "#0b").startswith("#e"))
        return {
            "bg":      t.get("bg",         "#0b0e1a"),
            "panel":   t.get("panel",      "#111827"),
            "border":  t.get("border",     "#1e2d45"),
            "fg":      t.get("fg",         "#e2e8f0"),
            "muted":   t.get("sidebar_fg", "#8892b0"),
            "inp":     t.get("input_bg",   "#141d2e"),
            "primary": t.get("primary",    _INDIGO),
            "is_light": is_light,
        }

    # ── Persist layout ────────────────────────────────────────────────────
    def _save_dashboard_layout(self):
        if not hasattr(self, '_db_grid'):
            return
        self.settings["dashboard_layout"] = self._db_grid.get_layout_order()
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
            os.chmod(SETTINGS_FILE, 0o600)
        except Exception:
            pass

    def _load_dashboard_layout(self) -> list:
        layout = self.settings.get("dashboard_layout")
        if not layout:
            return list(_DEFAULT_LAYOUT)
        result = []
        for item in layout:
            if isinstance(item, str):
                cat = _CATALOG_MAP.get(item)
                if cat:
                    result.append((item, cat["cols"], cat["rows"]))
            elif isinstance(item, (list, tuple)):
                tid = item[0]
                cat = _CATALOG_MAP.get(tid, {})
                cols = item[1] if len(item) > 1 else cat.get("cols", 2)
                rows = item[2] if len(item) > 2 else cat.get("rows", 2)
                result.append((tid, cols, rows))
        return result if result else list(_DEFAULT_LAYOUT)

    # ══════════════════════════════════════════════════════════════════════
    #  TILE CONTENT BUILDERS
    # ══════════════════════════════════════════════════════════════════════

    def _build_tile_stat_row(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w); lay.setSpacing(10)
        lay.setContentsMargins(10, 6, 10, 8)
        _FG = c["fg"]
        self._db_card_total     = _StatMini("Total Jobs",  "▲", "#3b82f6",
                                            c["panel"], c["border"], _FG)
        self._db_card_completed = _StatMini("Completed",   "✓", _GREEN,
                                            c["panel"], c["border"], _FG)
        self._db_card_failed    = _StatMini("Failed",      "✕", _RED,
                                            c["panel"], c["border"], _FG)
        self._db_card_rate      = _StatMini("Success Rate","%", _AMBER,
                                            c["panel"], c["border"], _FG)
        self._db_card_profiles  = _StatMini("Profiles",    "≣", _ORANGE,
                                            c["panel"], c["border"], _FG)
        for sc in [self._db_card_total, self._db_card_completed,
                   self._db_card_failed, self._db_card_rate,
                   self._db_card_profiles]:
            lay.addWidget(sc)
        return w

    def _build_tile_quick_actions(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 6, 14, 8); lay.setSpacing(10)
        BTN = (f"background-color:{c['inp']};color:{c['fg']};"
               f"font-weight:600;padding:6px 14px;border-radius:8px;"
               f"border:1px solid {c['border']};font-size:12px;")
        def _nav(label, idx):
            b = QPushButton(label); b.setStyleSheet(BTN)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda: self.sidebar.setCurrentRow(idx))
            return b
        lay.addWidget(_nav("▲  Backup", 2))
        lay.addWidget(_nav("▼  Restore", 3))
        lay.addWidget(_nav("▤  Jobs", 14))
        lay.addWidget(_nav("◷  Schedule", 6))
        lay.addStretch()
        self._db_lbl_scan = QLabel("Last refresh: —")
        self._db_lbl_scan.setStyleSheet(
            f"color:{c['muted']};font-size:11px;background:transparent;")
        lay.addWidget(self._db_lbl_scan)
        btn_ref = QPushButton("⟳  Refresh")
        btn_ref.setStyleSheet(
            f"background-color:{c['primary']};color:white;"
            f"font-weight:bold;padding:6px 14px;border-radius:8px;"
            f"border:none;font-size:12px;")
        btn_ref.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ref.clicked.connect(self.refresh_dashboard)
        lay.addWidget(btn_ref)
        return w

    def _build_tile_chart_donut(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(4)
        self._db_donut = _DonutChart()
        lay.addWidget(self._db_donut, 1)
        leg = QHBoxLayout(); leg.setSpacing(14)
        for col, txt in (("#00ff87", "Completed"), ("#ef4444", "Failed")):
            dot = QLabel("●"); dot.setStyleSheet(
                f"color:{col};font-size:11px;background:transparent;")
            lb = QLabel(txt); lb.setStyleSheet(
                f"color:{c['muted']};font-size:11px;"
                "background:transparent;")
            rw = QHBoxLayout(); rw.setSpacing(3)
            rw.addWidget(dot); rw.addWidget(lb); leg.addLayout(rw)
        leg.addStretch(); lay.addLayout(leg)
        return w

    def _build_tile_chart_spark(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(4)
        sh = QHBoxLayout(); sh.addStretch()
        for col, lbl_txt in (("#00ff87", "Done"), ("#ef4444", "Failed")):
            sq = QLabel("▮"); sq.setStyleSheet(
                f"color:{col};font-size:12px;background:transparent;"
                "border:none;")
            tx = QLabel(lbl_txt); tx.setStyleSheet(
                f"color:{c['muted']};font-size:10px;"
                "background:transparent;border:none;")
            sh.addWidget(sq); sh.addWidget(tx)
        lay.addLayout(sh)
        self._db_spark = _BarSparkline()
        lay.addWidget(self._db_spark, 1)
        return w

    def _build_tile_last_job(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(14, 4, 14, 8)
        lay.setSpacing(3)
        self._db_lj_type = QLabel("—")
        self._db_lj_type.setStyleSheet(
            f"color:{c['fg']};font-size:16px;font-weight:bold;")
        self._db_lj_target = QLabel("—")
        self._db_lj_target.setStyleSheet(
            f"color:{c['muted']};font-size:12px;")
        self._db_lj_target.setWordWrap(True)
        lj_bot = QHBoxLayout()
        self._db_lj_time = QLabel("—")
        self._db_lj_time.setStyleSheet("color:#4a5568;font-size:11px;")
        self._db_lj_pill = QLabel("—")
        self._db_lj_pill.setStyleSheet(
            "color:#8892b0;font-weight:bold;font-size:12px;"
            "background:transparent;border:none;padding:0;")
        lj_bot.addWidget(self._db_lj_time); lj_bot.addStretch()
        lj_bot.addWidget(self._db_lj_pill)
        lay.addWidget(self._db_lj_type)
        lay.addWidget(self._db_lj_target)
        lay.addStretch(); lay.addLayout(lj_bot)
        return w

    def _build_tile_next_task(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(14, 4, 14, 8)
        lay.setSpacing(3)
        self._db_nt_name = QLabel("—")
        self._db_nt_name.setStyleSheet(
            f"color:{c['fg']};font-size:16px;font-weight:bold;")
        self._db_nt_when = QLabel("—")
        self._db_nt_when.setStyleSheet(
            f"color:{c['primary']};font-size:12px;font-weight:bold;")
        self._db_nt_target = QLabel("—")
        self._db_nt_target.setStyleSheet(
            f"color:{c['muted']};font-size:12px;")
        self._db_nt_target.setWordWrap(True)
        self._db_nt_detail = QLabel("—")
        self._db_nt_detail.setStyleSheet("color:#4a5568;font-size:11px;")
        lay.addWidget(self._db_nt_name)
        lay.addWidget(self._db_nt_when)
        lay.addWidget(self._db_nt_target)
        lay.addStretch(); lay.addWidget(self._db_nt_detail)
        return w

    def _build_tile_disk_usage(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(8)
        self._db_tbl_disk = _mk_table(
            ["Profile", "Destination", "Used", "Free", "% Full"],
            c["panel"], c["inp"], c["border"], c["fg"])
        hh = self._db_tbl_disk.horizontalHeader()
        for i, m in enumerate([
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
        ]):
            hh.setSectionResizeMode(i, m)
        lay.addWidget(self._db_tbl_disk); return w

    def _build_tile_recent_jobs(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(8)
        rh = QHBoxLayout(); rh.addStretch()
        btn_all = QPushButton("View All →")
        btn_all.setStyleSheet(
            f"background:transparent;color:{c['primary']};"
            f"font-size:12px;font-weight:bold;border:none;padding:0;")
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.clicked.connect(lambda: self.sidebar.setCurrentRow(14))
        rh.addWidget(btn_all); lay.addLayout(rh)
        self._db_tbl_recent = _mk_table(
            ["Date & Time", "Type", "Target", "Status", "Details"],
            c["panel"], c["inp"], c["border"], c["fg"])
        rh2 = self._db_tbl_recent.horizontalHeader()
        for i, m in enumerate([
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.Stretch,
        ]):
            rh2.setSectionResizeMode(i, m)
        lay.addWidget(self._db_tbl_recent); return w

    def _build_tile_system_health(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(14, 4, 14, 8)
        lay.setSpacing(6)
        self._db_sh_cpu = QLabel("CPU: —")
        self._db_sh_cpu.setStyleSheet(
            f"color:{c['fg']};font-size:13px;font-weight:bold;")
        self._db_sh_ram = QLabel("RAM: —")
        self._db_sh_ram.setStyleSheet(
            f"color:{c['fg']};font-size:13px;font-weight:bold;")
        self._db_sh_uptime = QLabel("Uptime: —")
        self._db_sh_uptime.setStyleSheet(
            f"color:{c['muted']};font-size:12px;")
        self._db_sh_load = QLabel("Load: —")
        self._db_sh_load.setStyleSheet(
            f"color:{c['muted']};font-size:12px;")
        lay.addWidget(self._db_sh_cpu); lay.addWidget(self._db_sh_ram)
        lay.addWidget(self._db_sh_uptime)
        lay.addWidget(self._db_sh_load); lay.addStretch()
        return w

    def _build_tile_active_timers(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)
        self._db_tbl_timers = _mk_table(
            ["Timer", "Next Run", "Status"],
            c["panel"], c["inp"], c["border"], c["fg"])
        hh = self._db_tbl_timers.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._db_tbl_timers); return w

    def _build_tile_backup_size(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(4)
        self._db_bs_total = QLabel("—")
        self._db_bs_total.setStyleSheet(
            f"color:{c['fg']};font-size:28px;font-weight:bold;")
        self._db_bs_total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._db_bs_sub = QLabel("across all destinations")
        self._db_bs_sub.setStyleSheet(
            f"color:{c['muted']};font-size:11px;")
        self._db_bs_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(); lay.addWidget(self._db_bs_total)
        lay.addWidget(self._db_bs_sub); lay.addStretch()
        return w

    def _build_tile_protection(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)
        self._db_tbl_prot = _mk_table(
            ["Profile", "Last Backup", "Age", "Status"],
            c["panel"], c["inp"], c["border"], c["fg"])
        hh = self._db_tbl_prot.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._db_tbl_prot); return w

    def _build_tile_scheduler_cal(self, c):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)
        self._db_cal_grid = QWidget()
        self._db_cal_grid.setStyleSheet("background:transparent;")
        gl = QGridLayout(self._db_cal_grid)
        gl.setSpacing(4); gl.setContentsMargins(0, 0, 0, 0)
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self._db_cal_cells = {}
        for ci, d in enumerate(days):
            hdr = QLabel(d)
            hdr.setStyleSheet(
                f"color:{c['muted']};font-size:9px;font-weight:bold;"
                "background:transparent;border:none;")
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            gl.addWidget(hdr, 0, ci)
            cell = QLabel("—")
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.setFixedSize(38, 28)
            cell.setStyleSheet(
                f"background:{c['inp']};color:{c['muted']};"
                f"border-radius:6px;font-size:10px;font-weight:bold;")
            gl.addWidget(cell, 1, ci)
            self._db_cal_cells[d] = cell
        lay.addWidget(self._db_cal_grid); lay.addStretch()
        return w

    # ══════════════════════════════════════════════════════════════════════
    #  BUILD TILE BY ID
    # ══════════════════════════════════════════════════════════════════════

    def _build_tile_by_id(self, tile_id, cols, rows, c, locked):
        cat = _CATALOG_MAP.get(tile_id)
        if not cat:
            return None
        builder = {
            "stat_row":      self._build_tile_stat_row,
            "quick_actions": self._build_tile_quick_actions,
            "chart_donut":   self._build_tile_chart_donut,
            "chart_spark":   self._build_tile_chart_spark,
            "last_job":      self._build_tile_last_job,
            "next_task":     self._build_tile_next_task,
            "disk_usage":    self._build_tile_disk_usage,
            "recent_jobs":   self._build_tile_recent_jobs,
            "system_health": self._build_tile_system_health,
            "active_timers": self._build_tile_active_timers,
            "backup_size":   self._build_tile_backup_size,
            "protection":    self._build_tile_protection,
            "scheduler_cal": self._build_tile_scheduler_cal,
        }.get(tile_id)
        if not builder:
            return None
        content = builder(c)
        return DashboardTile(
            tile_id, cat["label"], content, cols, rows,
            c["panel"], c["border"], c["fg"], c["muted"], locked)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE BUILD
    # ══════════════════════════════════════════════════════════════════════

    def build_dashboard_page(self):
        page = QWidget()
        page.setObjectName("db_page")
        root_lay = QVBoxLayout(page)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        c = self._tc()
        self._db_locked = True

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QWidget(); toolbar.setFixedHeight(48)
        toolbar.setStyleSheet("background:transparent;")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(8, 6, 8, 6); tb_lay.setSpacing(10)

        self._db_lbl_hi = QLabel("Dashboard")
        self._db_lbl_hi.setStyleSheet(
            f"color:{c['fg']};font-size:20px;font-weight:bold;"
            "background:transparent;")
        tb_lay.addWidget(self._db_lbl_hi)
        self._db_lbl_date = QLabel(
            datetime.now().strftime("%A, %B %-d  %Y"))
        self._db_lbl_date.setStyleSheet(
            f"color:{c['muted']};font-size:12px;background:transparent;")
        tb_lay.addWidget(self._db_lbl_date)
        tb_lay.addStretch()

        _TB = (f"QPushButton{{background-color:{c['inp']};"
               f"color:{c['fg']};font-weight:bold;padding:5px 14px;"
               f"border-radius:8px;border:1px solid {c['border']};"
               f"font-size:12px;}}"
               f"QPushButton:hover{{background-color:{c['border']};}}")

        self._db_btn_lock = QPushButton("🔒  Locked")
        self._db_btn_lock.setStyleSheet(_TB)
        self._db_btn_lock.setCursor(Qt.CursorShape.PointingHandCursor)
        self._db_btn_lock.clicked.connect(self._toggle_dashboard_lock)
        self._db_btn_lock.setToolTip(
            "Unlock to rearrange, resize, add, or remove tiles")
        tb_lay.addWidget(self._db_btn_lock)

        self._db_btn_add = QPushButton("＋  Add Tile")
        self._db_btn_add.setStyleSheet(_TB)
        self._db_btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._db_btn_add.clicked.connect(self._show_add_tile_menu)
        self._db_btn_add.setVisible(False)
        tb_lay.addWidget(self._db_btn_add)

        self._db_btn_reset = QPushButton("↺  Reset")
        self._db_btn_reset.setStyleSheet(_TB)
        self._db_btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._db_btn_reset.clicked.connect(self._reset_dashboard_layout)
        self._db_btn_reset.setVisible(False)
        self._db_btn_reset.setToolTip("Reset dashboard to default layout")
        tb_lay.addWidget(self._db_btn_reset)

        root_lay.addWidget(toolbar)

        # ── Scroll area ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:6px;}"
            "QScrollBar::handle:vertical{background:#1e2d45;"
            "border-radius:3px;}")
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._db_grid = TileGrid()
        self._db_grid.layout_changed.connect(self._save_dashboard_layout)
        scroll.setWidget(self._db_grid)
        root_lay.addWidget(scroll, 1)

        # ── Build tiles ───────────────────────────────────────────────────
        layout_order = self._load_dashboard_layout()
        layout_order = [(tid, co, ro) for tid, co, ro in layout_order
                        if tid in _CATALOG_MAP]
        if not layout_order:
            layout_order = list(_DEFAULT_LAYOUT)

        self._db_tile_widgets = {}
        for tile_id, cols, rows in layout_order:
            tile = self._build_tile_by_id(
                tile_id, cols, rows, c, self._db_locked)
            if tile:
                self._db_grid.add_tile(tile)
                self._db_tile_widgets[tile_id] = tile

        QTimer.singleShot(150, self.refresh_dashboard)
        return page

    # ── Lock / Unlock ─────────────────────────────────────────────────────
    def _toggle_dashboard_lock(self):
        self._db_locked = not self._db_locked
        self._db_grid.set_locked(self._db_locked)
        if self._db_locked:
            self._db_btn_lock.setText("🔒  Locked")
            self._db_btn_add.setVisible(False)
            self._db_btn_reset.setVisible(False)
            self._save_dashboard_layout()
        else:
            self._db_btn_lock.setText("🔓  Unlocked")
            self._db_btn_add.setVisible(True)
            self._db_btn_reset.setVisible(True)

    # ── Add tile ──────────────────────────────────────────────────────────
    def _show_add_tile_menu(self):
        current = set(tid for tid, _, _ in
                       self._db_grid.get_layout_order())
        available = [t for t in TILE_CATALOG if t["id"] not in current]
        if not available:
            return
        c = self._tc()
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background-color:{c['panel']};color:{c['fg']};"
            f"border:1px solid {c['border']};border-radius:8px;"
            f"padding:6px;}}"
            f"QMenu::item{{padding:8px 20px;border-radius:4px;}}"
            f"QMenu::item:selected{{background-color:{c['inp']};}}")
        for t in available:
            act = menu.addAction(f"  {t['label']}")
            act.triggered.connect(
                partial(self._add_tile, t["id"],
                        t["cols"], t["rows"]))
        menu.exec(self._db_btn_add.mapToGlobal(
            QPoint(0, self._db_btn_add.height())))

    def _add_tile(self, tile_id, cols, rows):
        c = self._tc()
        tile = self._build_tile_by_id(
            tile_id, cols, rows, c, self._db_locked)
        if tile:
            self._db_grid.add_tile(tile)
            self._db_tile_widgets[tile_id] = tile
            self._save_dashboard_layout()
            self.refresh_dashboard()

    # ── Reset ─────────────────────────────────────────────────────────────
    def _reset_dashboard_layout(self):
        if not confirm_action(
                self, "Reset Dashboard",
                "Reset the dashboard to its default tile layout?",
                detail="Your current tile arrangement, sizes, and "
                       "any added/removed tiles will be lost.",
                confirm_text="Reset Layout", icon_char="↺"):
            return
        for t in list(self._db_grid._tiles):
            t.setParent(None); t.deleteLater()
        self._db_grid._tiles.clear()
        self._db_tile_widgets.clear()
        c = self._tc()
        for tile_id, cols, rows in _DEFAULT_LAYOUT:
            tile = self._build_tile_by_id(
                tile_id, cols, rows, c, self._db_locked)
            if tile:
                self._db_grid.add_tile(tile)
                self._db_tile_widgets[tile_id] = tile
        self._save_dashboard_layout()
        self.refresh_dashboard()

    # ══════════════════════════════════════════════════════════════════════
    #  RE-THEME
    # ══════════════════════════════════════════════════════════════════════

    def retheme_dashboard(self, t: dict):
        if not hasattr(self, '_db_grid'):
            return
        pan = t.get("panel",      "#111827")
        bdr = t.get("border",     "#1e2d45")
        fg  = t.get("fg",         "#e2e8f0")
        mut = t.get("sidebar_fg", "#8892b0")
        inp = t.get("input_bg",   "#141d2e")
        pri = t.get("primary",    _INDIGO)

        if hasattr(self, '_db_lbl_hi'):
            self._db_lbl_hi.setStyleSheet(
                f"color:{fg};font-size:20px;font-weight:bold;"
                "background:transparent;")
        if hasattr(self, '_db_lbl_date'):
            self._db_lbl_date.setStyleSheet(
                f"color:{mut};font-size:12px;background:transparent;")

        _TB = (f"QPushButton{{background-color:{inp};"
               f"color:{fg};font-weight:bold;padding:5px 14px;"
               f"border-radius:8px;border:1px solid {bdr};"
               f"font-size:12px;}}"
               f"QPushButton:hover{{background-color:{bdr};}}")
        for a in ('_db_btn_lock', '_db_btn_add', '_db_btn_reset'):
            b = getattr(self, a, None)
            if b:
                b.setStyleSheet(_TB)

        for tile in self._db_grid._tiles:
            tile.retheme(pan, bdr, fg, mut)

        for a in ('_db_card_total', '_db_card_completed',
                  '_db_card_failed', '_db_card_rate',
                  '_db_card_profiles'):
            card = getattr(self, a, None)
            if card and isinstance(card, _StatMini):
                card.retheme(pan, bdr, fg)

        if hasattr(self, "_db_donut"):
            self._db_donut._text_color = fg
            self._db_donut._bg_ring = bdr
            self._db_donut.update()

        for attr, sty in [
            ("_db_lbl_scan",   f"color:{mut};font-size:11px;"
                               "background:transparent;"),
            ("_db_lj_type",    f"color:{fg};font-size:16px;"
                               "font-weight:bold;"),
            ("_db_lj_target",  f"color:{mut};font-size:12px;"),
            ("_db_lj_time",    "color:#4a5568;font-size:11px;"),
            ("_db_lj_pill",    "color:#8892b0;font-weight:bold;"
                               "font-size:12px;background:transparent;"
                               "border:none;padding:0;"),
            ("_db_nt_name",    f"color:{fg};font-size:16px;"
                               "font-weight:bold;"),
            ("_db_nt_when",    f"color:{pri};font-size:12px;"
                               "font-weight:bold;"),
            ("_db_nt_target",  f"color:{mut};font-size:12px;"),
            ("_db_nt_detail",  "color:#4a5568;font-size:11px;"),
            ("_db_sh_cpu",     f"color:{fg};font-size:13px;"
                               "font-weight:bold;"),
            ("_db_sh_ram",     f"color:{fg};font-size:13px;"
                               "font-weight:bold;"),
            ("_db_sh_uptime",  f"color:{mut};font-size:12px;"),
            ("_db_sh_load",    f"color:{mut};font-size:12px;"),
            ("_db_bs_total",   f"color:{fg};font-size:28px;"
                               "font-weight:bold;"),
            ("_db_bs_sub",     f"color:{mut};font-size:11px;"),
        ]:
            if hasattr(self, attr):
                getattr(self, attr).setStyleSheet(sty)

        for ta in ("_db_tbl_disk", "_db_tbl_recent",
                   "_db_tbl_timers", "_db_tbl_prot"):
            if hasattr(self, ta):
                _apply_table_css(getattr(self, ta), pan, inp, bdr, fg)

    # ══════════════════════════════════════════════════════════════════════
    #  DATA REFRESH
    # ══════════════════════════════════════════════════════════════════════

    def refresh_dashboard(self):
        if not hasattr(self, '_db_grid'):
            return
        c = self._tc()
        jobs  = getattr(self, 'job_history', [])
        tasks = getattr(self, 'scheduled_tasks', {})
        active = set(tid for tid, _, _ in
                      self._db_grid.get_layout_order())

        if "stat_row" in active and hasattr(self, '_db_card_total'):
            total     = len(jobs)
            completed = sum(1 for j in jobs
                           if j.get("status") == "Completed")
            failed    = sum(1 for j in jobs
                           if j.get("status") in ("Failed","Error"))
            running   = sum(1 for j in jobs
                           if j.get("status") == "Running")
            stalled   = sum(1 for j in jobs
                           if j.get("status") == "Stalled")
            rate_pct  = round(completed/total*100) if total else 0
            profiles  = sum(
                len(v) for v in getattr(self,"profiles",{}).values())
            self._db_card_total.set_value(
                total or "—",
                f"{running} running" if running else "")
            self._db_card_completed.set_value(completed or "—")
            self._db_card_failed.set_value(
                failed or "—",
                f"+{stalled} stalled" if stalled else "")
            self._db_card_rate.set_value(
                f"{rate_pct}%" if total else "—")
            self._db_card_profiles.set_value(profiles or "—")

        if "chart_donut" in active and hasattr(self, '_db_donut'):
            total = len(jobs)
            comp = sum(1 for j in jobs
                      if j.get("status") == "Completed")
            self._db_donut.set_data(comp, total, c["border"], c["fg"])

        if "chart_spark" in active and hasattr(self, '_db_spark'):
            today = datetime.now().date()
            dd = {today - timedelta(days=i): [0,0]
                  for i in range(6, -1, -1)}
            for j in jobs:
                try:
                    jd = datetime.strptime(
                        j["time"][:10], "%Y-%m-%d").date()
                    if jd in dd:
                        if j.get("status") == "Completed":
                            dd[jd][0] += 1
                        elif j.get("status") in (
                                "Failed","Error","Stalled"):
                            dd[jd][1] += 1
                except Exception:
                    pass
            self._db_spark.set_data(
                [(d.strftime("%a"), g, f) for d,(g,f) in dd.items()])

        if "last_job" in active and hasattr(self, '_db_lj_type'):
            if jobs:
                last = jobs[-1]
                s = last.get("status","—")
                sc = STATUS_COLORS.get(s, "#8892b0")
                self._db_lj_type.setText(last.get("type","—"))
                self._db_lj_target.setText(last.get("target","—"))
                self._db_lj_time.setText(last.get("time","—"))
                self._db_lj_pill.setText(s)
                self._db_lj_pill.setStyleSheet(
                    f"color:{sc};font-weight:bold;font-size:12px;"
                    "background:transparent;border:none;padding:0;")
            else:
                self._db_lj_type.setText("No jobs recorded yet.")
                self._db_lj_target.setText("—")
                self._db_lj_time.setText("—")
                self._db_lj_pill.setText("")

        if "next_task" in active and hasattr(self, '_db_nt_name'):
            dm = {"Mon":0,"Tue":1,"Wed":2,"Thu":3,
                  "Fri":4,"Sat":5,"Sun":6}
            now = datetime.now()
            soonest = None; sd = None
            for tn, td in tasks.items():
                act = [d for d, on in td.get("days",{}).items() if on]
                if not act:
                    continue
                try:
                    th, tm = map(int, td.get("time","00:00").split(":"))
                except (ValueError, IndexError):
                    continue
                for day in act:
                    dn = dm.get(day, -1)
                    if dn < 0:
                        continue
                    ahead = (dn - now.weekday()) % 7
                    if ahead == 0:
                        if now.hour > th or (
                                now.hour == th and now.minute >= tm):
                            ahead = 7
                    run = (now.replace(hour=th, minute=tm, second=0,
                                       microsecond=0)
                           + timedelta(days=ahead))
                    delta = run - now
                    if sd is None or delta < sd:
                        sd = delta; soonest = (tn, td, run)
            if soonest:
                tn, td, tr = soonest
                d = sd.days; h = sd.seconds//3600
                m = (sd.seconds%3600)//60
                if d == 0:
                    w = (f"Today at {tr.strftime('%H:%M')}"
                         f"  (in {h}h {m}m)")
                elif d == 1:
                    w = f"Tomorrow at {tr.strftime('%H:%M')}"
                else:
                    w = (f"{tr.strftime('%A')} at "
                         f"{tr.strftime('%H:%M')}  (in {d}d)")
                self._db_nt_name.setText(tn)
                self._db_nt_when.setText(w)
                self._db_nt_target.setText(
                    f"Target: {td.get('target','—')}")
                self._db_nt_detail.setText(
                    f"Retention: {td.get('retention','—')} days  ·  "
                    f"{td.get('engine','—')}")
            else:
                self._db_nt_name.setText("No scheduled tasks.")
                self._db_nt_when.setText("—")
                self._db_nt_target.setText("—")
                self._db_nt_detail.setText("—")

        if "disk_usage" in active and hasattr(self, '_db_tbl_disk'):
            self._db_tbl_disk.setRowCount(0)
            seen = set()
            for cat, cps in getattr(self,"profiles",{}).items():
                for pn, pd in cps.items():
                    dst = pd.get("path","")
                    if not dst or dst in seen or cat == "cloud":
                        continue
                    seen.add(dst)
                    used = free = pct = "N/A"
                    try:
                        r = subprocess.run(
                            ["df","-h",dst], capture_output=True,
                            text=True, timeout=3)
                        if r.returncode == 0:
                            p = r.stdout.strip().splitlines()[1].split()
                            used, free, pct = p[2], p[3], p[4]
                    except Exception:
                        pass
                    rc = self._db_tbl_disk.rowCount()
                    self._db_tbl_disk.insertRow(rc)
                    pi = QTableWidgetItem(f"[{cat.upper()}] {pn}")
                    pi.setForeground(QBrush(QColor(c["primary"])))
                    self._db_tbl_disk.setItem(rc, 0, pi)
                    self._db_tbl_disk.setItem(
                        rc, 1, QTableWidgetItem(dst))
                    ui = QTableWidgetItem(used)
                    ui.setForeground(QBrush(QColor("#8892b0")))
                    self._db_tbl_disk.setItem(rc, 2, ui)
                    fi = QTableWidgetItem(free)
                    fi.setForeground(QBrush(QColor(_GREEN)))
                    self._db_tbl_disk.setItem(rc, 3, fi)
                    try:
                        pv = int(pct.rstrip("%"))
                        pc = (_RED if pv > 90 else _AMBER
                              if pv > 75 else _GREEN)
                    except (ValueError, AttributeError):
                        pc = "#8892b0"
                    p2 = QTableWidgetItem(pct)
                    p2.setForeground(QBrush(QColor(pc)))
                    self._db_tbl_disk.setItem(rc, 4, p2)

        if "recent_jobs" in active and hasattr(self, '_db_tbl_recent'):
            self._db_tbl_recent.setRowCount(0)
            for job in reversed(jobs[-10:]):
                rc = self._db_tbl_recent.rowCount()
                self._db_tbl_recent.insertRow(rc)
                s = job.get("status","")
                sc = STATUS_COLORS.get(s, "#8892b0")
                items = [
                    QTableWidgetItem(job.get("time","")),
                    QTableWidgetItem(job.get("type","")),
                    QTableWidgetItem(job.get("target","")),
                    QTableWidgetItem(f"  {s}  "),
                    QTableWidgetItem(job.get("description","")),
                ]
                items[3].setFont(
                    QFont("Segoe UI", 10, QFont.Weight.Bold))
                items[3].setForeground(QBrush(QColor(sc)))
                items[1].setForeground(QBrush(QColor("#8892b0")))
                items[4].setForeground(QBrush(QColor("#4a5568")))
                for i, item in enumerate(items):
                    self._db_tbl_recent.setItem(rc, i, item)

        if "system_health" in active and hasattr(self, '_db_sh_cpu'):
            try:
                with open("/proc/loadavg") as f:
                    p = f.read().split()
                l1, l5, l15 = p[0], p[1], p[2]
                nc = os.cpu_count() or 1
                lp = round(float(l1)/nc*100)
                cc = (_RED if lp > 90 else _AMBER
                      if lp > 70 else _GREEN)
                self._db_sh_cpu.setText(f"CPU Load: {lp}%  ({l1})")
                self._db_sh_cpu.setStyleSheet(
                    f"color:{cc};font-size:13px;font-weight:bold;")
                self._db_sh_load.setText(
                    f"Load avg: {l1} / {l5} / {l15}")
            except Exception:
                self._db_sh_cpu.setText("CPU: unavailable")
            try:
                with open("/proc/meminfo") as f:
                    mem = {}
                    for line in f:
                        p = line.split()
                        if p[0] in ("MemTotal:","MemAvailable:"):
                            mem[p[0].rstrip(":")] = int(p[1])
                tg = mem.get("MemTotal",0)/1048576
                ag = mem.get("MemAvailable",0)/1048576
                ug = tg - ag
                pp = round(ug/tg*100) if tg else 0
                rc = (_RED if pp > 90 else _AMBER if pp > 75 else _GREEN)
                self._db_sh_ram.setText(
                    f"RAM: {ug:.1f}/{tg:.1f} GB ({pp}%)")
                self._db_sh_ram.setStyleSheet(
                    f"color:{rc};font-size:13px;font-weight:bold;")
            except Exception:
                self._db_sh_ram.setText("RAM: unavailable")
            try:
                with open("/proc/uptime") as f:
                    secs = int(float(f.read().split()[0]))
                dy = secs//86400; hr = (secs%86400)//3600
                mn = (secs%3600)//60
                self._db_sh_uptime.setText(
                    f"Uptime: {dy}d {hr}h {mn}m")
            except Exception:
                self._db_sh_uptime.setText("Uptime: unavailable")

        if "active_timers" in active and hasattr(self,'_db_tbl_timers'):
            self._db_tbl_timers.setRowCount(0)
            try:
                r = subprocess.run(
                    ["systemctl","list-timers","--no-pager",
                     "archvault-task-*"],
                    capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n")[1:]:
                        line = line.strip()
                        if not line or line.startswith("--"):
                            continue
                        parts = line.split()
                        if len(parts) >= 6:
                            unit = (parts[-2]
                                    if parts[-2].endswith(".timer")
                                    else parts[-1])
                            name = unit.replace(
                                "archvault-task-","").replace(
                                    ".timer","")
                            nr = " ".join(parts[:3])
                            rc = self._db_tbl_timers.rowCount()
                            self._db_tbl_timers.insertRow(rc)
                            ni = QTableWidgetItem(name)
                            ni.setForeground(
                                QBrush(QColor(c["fg"])))
                            self._db_tbl_timers.setItem(rc,0,ni)
                            ti = QTableWidgetItem(nr)
                            ti.setForeground(
                                QBrush(QColor(c["primary"])))
                            self._db_tbl_timers.setItem(rc,1,ti)
                            si = QTableWidgetItem("Active")
                            si.setForeground(
                                QBrush(QColor(_GREEN)))
                            self._db_tbl_timers.setItem(rc,2,si)
            except Exception:
                pass

        if "backup_size" in active and hasattr(self,'_db_bs_total'):
            tb = 0; dc = 0; seen = set()
            for cat, cps in getattr(self,"profiles",{}).items():
                for pn, pd in cps.items():
                    dst = pd.get("path","")
                    if not dst or dst in seen or cat == "cloud":
                        continue
                    seen.add(dst); dc += 1
                    try:
                        r = subprocess.run(
                            ["du","-sb",dst], capture_output=True,
                            text=True, timeout=10)
                        if r.returncode == 0:
                            tb += int(r.stdout.split()[0])
                    except Exception:
                        pass
            if tb > 0:
                for u in ("B","KB","MB","GB","TB"):
                    if tb < 1024:
                        self._db_bs_total.setText(f"{tb:,.1f} {u}")
                        break
                    tb /= 1024
            else:
                self._db_bs_total.setText("—")
            self._db_bs_sub.setText(f"across {dc} destination(s)")

        if "protection" in active and hasattr(self,'_db_tbl_prot'):
            self._db_tbl_prot.setRowCount(0)
            now = datetime.now()
            for cat, cps in getattr(self,"profiles",{}).items():
                for pn, pd in cps.items():
                    ts = f"{cat.upper()}: {pn}"
                    lb = None
                    for j in reversed(jobs):
                        if (j.get("target") == ts
                                and j.get("status") == "Completed"):
                            lb = j; break
                    rc = self._db_tbl_prot.rowCount()
                    self._db_tbl_prot.insertRow(rc)
                    ni = QTableWidgetItem(f"[{cat.upper()}] {pn}")
                    ni.setForeground(QBrush(QColor(c["fg"])))
                    self._db_tbl_prot.setItem(rc, 0, ni)
                    if lb:
                        self._db_tbl_prot.setItem(
                            rc, 1, QTableWidgetItem(lb["time"]))
                        try:
                            jd = datetime.strptime(
                                lb["time"][:10], "%Y-%m-%d")
                            ad = (now - jd).days
                            ai = QTableWidgetItem(f"{ad}d ago")
                            if ad > 7:
                                ai.setForeground(
                                    QBrush(QColor(_RED)))
                                st, sc2 = "⚠ Stale", _RED
                            elif ad > 3:
                                ai.setForeground(
                                    QBrush(QColor(_AMBER)))
                                st, sc2 = "⚡ Aging", _AMBER
                            else:
                                ai.setForeground(
                                    QBrush(QColor(_GREEN)))
                                st, sc2 = "✓ Current", _GREEN
                            self._db_tbl_prot.setItem(rc, 2, ai)
                        except Exception:
                            self._db_tbl_prot.setItem(
                                rc, 2, QTableWidgetItem("—"))
                            st, sc2 = "?", "#8892b0"
                    else:
                        self._db_tbl_prot.setItem(
                            rc, 1, QTableWidgetItem("Never"))
                        self._db_tbl_prot.setItem(
                            rc, 2, QTableWidgetItem("—"))
                        st, sc2 = "✕ Unprotected", _RED
                    si = QTableWidgetItem(st)
                    si.setFont(
                        QFont("Segoe UI", 10, QFont.Weight.Bold))
                    si.setForeground(QBrush(QColor(sc2)))
                    self._db_tbl_prot.setItem(rc, 3, si)

        if "scheduler_cal" in active and hasattr(
                self, '_db_cal_cells'):
            dc = {d: 0 for d in
                  ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]}
            for tn, td in tasks.items():
                for day, on in td.get("days",{}).items():
                    if on and day in dc:
                        dc[day] += 1
            for day, cell in self._db_cal_cells.items():
                cnt = dc.get(day, 0)
                if cnt > 0:
                    cell.setText(str(cnt))
                    cell.setStyleSheet(
                        f"background:{_GREEN}33;color:{_GREEN};"
                        f"border-radius:6px;font-size:11px;"
                        f"font-weight:bold;")
                else:
                    cell.setText("—")
                    cell.setStyleSheet(
                        f"background:{c['inp']};color:{c['muted']};"
                        f"border-radius:6px;font-size:10px;"
                        f"font-weight:bold;")

        if hasattr(self, '_db_lbl_scan'):
            self._db_lbl_scan.setText(
                f"Last refresh: "
                f"{datetime.now().strftime('%H:%M:%S')}")
