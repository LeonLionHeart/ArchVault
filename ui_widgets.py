"""
ui_widgets.py  —  ArchVault v5.0.3-beta
Shared widgets: ToggleSwitch, confirm_action dialog.
"""
from PyQt6.QtWidgets import (
    QWidget, QSizePolicy, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, pyqtProperty,
    QRectF, pyqtSignal, QSize,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

VERSION = "v5.0.3-beta"


# ═════════════════════════════════════════════════════════════════════════════
#  TOGGLE SWITCH
# ═════════════════════════════════════════════════════════════════════════════

class ToggleSwitch(QWidget):
    """
    Animated iOS-style toggle switch.
    Inherits QWidget (NOT QCheckBox) so it is 100% immune to
    the application-level QCheckBox / QCheckBox::indicator stylesheet rules.
    Emits toggled(bool) on state change, just like QCheckBox.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._checked = False
        self._circle_pos = 3.0

        self._anim = QPropertyAnimation(self, b"_circle_pos_prop", self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setDuration(180)

    def isChecked(self):
        return self._checked

    def setChecked(self, value: bool):
        if self._checked == value:
            return
        self._checked = value
        self._animate_to(29 if value else 3)
        self.update()

    @pyqtProperty(float)
    def _circle_pos_prop(self):
        return self._circle_pos

    @_circle_pos_prop.setter
    def _circle_pos_prop(self, pos):
        self._circle_pos = pos
        self.update()

    def _animate_to(self, end_x: float):
        self._anim.stop()
        self._anim.setStartValue(self._circle_pos)
        self._anim.setEndValue(end_x)
        self._anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self._animate_to(29 if self._checked else 3)
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track_rect = QRectF(0, 0, 52, 28)
        track_color = QColor("#0ea5e9") if self._checked else QColor("#374151")
        painter.setBrush(track_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(track_rect, 14, 14)

        # Thumb
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d1d5db"), 1))
        painter.drawEllipse(QRectF(self._circle_pos, 4, 20, 20))

        painter.end()

    def sizeHint(self):
        return QSize(52, 28)


# ═════════════════════════════════════════════════════════════════════════════
#  THEMED CONFIRMATION DIALOG
# ═════════════════════════════════════════════════════════════════════════════

class ConfirmDialog(QDialog):
    """
    Themed confirmation dialog that reads the app's _current_theme.

    Usage:
        from ui_widgets import confirm_action
        if confirm_action(self, title, message, detail, destructive=True):
            ...proceed...
    """

    def __init__(self, parent, title, message, detail="",
                 confirm_text="Confirm", cancel_text="Cancel",
                 destructive=False, icon_char="⚠"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # ── Resolve theme ─────────────────────────────────────────────────
        t = {}
        p = parent
        while p:
            if hasattr(p, '_current_theme'):
                t = p._current_theme
                break
            p = getattr(p, 'parent', lambda: None)()

        bg    = t.get("panel",      "#111827")
        bg2   = t.get("bg",         "#0b0e1a")
        bdr   = t.get("border",     "#1e2d45")
        fg    = t.get("fg",         "#e2e8f0")
        muted = t.get("sidebar_fg", "#8892b0")
        inp   = t.get("input_bg",   "#141d2e")
        pri   = t.get("primary",    "#818cf8")

        # ── Card container ────────────────────────────────────────────────
        card = QWidget(self)
        card.setStyleSheet(
            f"QWidget#_cfd_card{{"
            f"  background-color:{bg};"
            f"  border:1px solid {bdr};"
            f"  border-radius:16px;"
            f"}}"
            f"QLabel{{background:transparent;border:none;"
            f"  text-decoration:none;color:{fg};}}")
        card.setObjectName("_cfd_card")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 100))
        card.setGraphicsEffect(shadow)

        dlg_lay = QVBoxLayout(self)
        dlg_lay.setContentsMargins(20, 20, 20, 20)
        dlg_lay.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(0)

        # ── Icon + Title row ──────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(14)

        icon_bg = "#ef444433" if destructive else f"{pri}22"
        icon_fg = "#ef4444" if destructive else pri
        icon_lbl = QLabel(icon_char)
        icon_lbl.setFixedSize(44, 44)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"background:{icon_bg};color:{icon_fg};"
            f"font-size:22px;font-weight:bold;"
            f"border-radius:12px;")

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color:{fg};font-size:17px;font-weight:bold;")
        title_lbl.setWordWrap(True)

        top.addWidget(icon_lbl)
        top.addWidget(title_lbl, 1)
        root.addLayout(top)

        root.addSpacing(14)

        # ── Message ───────────────────────────────────────────────────────
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(
            f"color:{fg};font-size:13px;")
        root.addWidget(msg_lbl)

        # ── Detail (optional smaller text in a box) ───────────────────────
        if detail:
            root.addSpacing(8)
            det_lbl = QLabel(detail)
            det_lbl.setWordWrap(True)
            det_lbl.setStyleSheet(
                f"color:{muted};font-size:12px;"
                f"background:{inp};border:1px solid {bdr};"
                f"border-radius:8px;padding:10px 14px;")
            root.addWidget(det_lbl)

        root.addSpacing(20)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        btn_cancel = QPushButton(cancel_text)
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setMinimumWidth(100)
        btn_cancel.setStyleSheet(
            f"QPushButton{{"
            f"  background-color:{inp};"
            f"  color:{fg};font-weight:600;padding:9px 22px;"
            f"  border-radius:8px;border:1px solid {bdr};"
            f"  font-size:13px;}}"
            f"QPushButton:hover{{background-color:{bdr};}}")
        btn_cancel.clicked.connect(self.reject)

        if destructive:
            conf_bg = "#ef4444"
            conf_bg_hover = "#dc2626"
            conf_fg = "#ffffff"
        else:
            conf_bg = pri
            conf_bg_hover = f"{pri}cc"
            conf_fg = t.get("primary_fg", "#ffffff")

        btn_confirm = QPushButton(confirm_text)
        btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_confirm.setMinimumWidth(100)
        btn_confirm.setStyleSheet(
            f"QPushButton{{"
            f"  background-color:{conf_bg};"
            f"  color:{conf_fg};font-weight:bold;padding:9px 22px;"
            f"  border-radius:8px;border:none;"
            f"  font-size:13px;}}"
            f"QPushButton:hover{{background-color:{conf_bg_hover};}}")
        btn_confirm.clicked.connect(self.accept)

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_confirm)
        root.addLayout(btn_row)

        # Default focus on cancel for destructive, confirm for normal
        if destructive:
            btn_cancel.setFocus()
        else:
            btn_confirm.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
        super().keyPressEvent(e)


def confirm_action(parent, title, message, detail="",
                   confirm_text="Confirm", cancel_text="Cancel",
                   destructive=False, icon_char="⚠") -> bool:
    """
    Convenience — shows themed confirmation dialog.
    Returns True if user confirmed, False otherwise.
    """
    dlg = ConfirmDialog(
        parent, title, message, detail,
        confirm_text, cancel_text, destructive, icon_char)
    return dlg.exec() == QDialog.DialogCode.Accepted
