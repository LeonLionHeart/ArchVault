from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
                              QListWidget, QListWidgetItem, QStackedWidget, QFrame,
                              QPushButton, QSizePolicy, QGraphicsOpacityEffect)
from PyQt6.QtGui import QFont, QColor, QPixmap, QIcon
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QTimer, QSize, QByteArray
import os
import sys

# The chosen 'A' gradient logo as scalable SVG data
_LOGO_SVG_DATA = QByteArray(b"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="g" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#3b82f6"/>
      <stop offset="100%" stop-color="#10b981"/>
    </linearGradient>
  </defs>
  <path d="M 20 85 L 50 15 L 80 85" fill="none" stroke="url(#g)" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="38" y="55" width="24" height="7" fill="#10b981" rx="3.5"/>
  <rect x="28" y="70" width="44" height="7" fill="#3b82f6" rx="3.5"/>
</svg>
""")

# ── Sidebar nav map: (emoji, label, stack_index or None for headers) ────────
_NAV = [
    ("nav",  "⬡", "Dashboard",         0),
    ("hdr",  "",   "EXECUTE",           None),
    ("nav",  "▲", "Backup",            1),
    ("nav",  "▼", "Restore",           2),
    ("nav",  "◈", "Snapshot Browser",  3),
    ("hdr",  "",   "SCHEDULE",          None),
    ("nav",  "◷", "Tasks",             4),
    ("hdr",  "",   "PROFILES",          None),
    ("nav",  "◎", "Network Locations", 5),
    ("nav",  "◈", "Cloud Providers",   6),
    ("nav",  "▣", "Local Storage",     7),
    ("nav",  "◉", "USB / Removable",   8),
    ("nav",  "◆", "SFTP / SSH",        9),
    ("hdr",  "",   "MONITOR",           None),
    ("nav",  "▤", "Jobs",              10),
    ("nav",  "◎", "Settings",         11),
]

class UIShellMixin:
    def apply_theme(self, theme_name):
        themes = {
            "Dark": {
                "bg": "#0b0e1a", "fg": "#e2e8f0",
                "panel": "#111827", "border": "#1e2d45",
                "primary": "#818cf8", "primary_fg": "#ffffff",
                "sidebar_bg": "#070910", "sidebar_fg": "#8892b0",
                "sidebar_sel": "#111827", "sidebar_hover": "#0d1020",
                "input_bg": "#0d1120",
                "topbar_bg": "#090c18", "status_bg": "#060810",
            },
            "Light": {
                "bg": "#f8fafc", "fg": "#0f172a",
                "panel": "#ffffff", "border": "#e2e8f0",
                "primary": "#4f46e5", "primary_fg": "#ffffff",
                "sidebar_bg": "#f1f5f9", "sidebar_fg": "#475569",
                "sidebar_sel": "#ffffff", "sidebar_hover": "#e2e8f0",
                "input_bg": "#f8fafc",
                "topbar_bg": "#e9ecf0", "status_bg": "#dde1e7",
            },
            "Midnight Blue": {
                "bg": "#020617", "fg": "#e2e8f0",
                "panel": "#0f172a", "border": "#1e293b",
                "primary": "#38bdf8", "primary_fg": "#020617",
                "sidebar_bg": "#020617", "sidebar_fg": "#94a3b8",
                "sidebar_sel": "#0f172a", "sidebar_hover": "#0b1120",
                "input_bg": "#020617",
                "topbar_bg": "#010411", "status_bg": "#010311",
            },
            "Deep Purple": {
                "bg": "#2e1065", "fg": "#ede9fe",
                "panel": "#4c1d95", "border": "#5b21b6",
                "primary": "#c084fc", "primary_fg": "#2e1065",
                "sidebar_bg": "#1e1b4b", "sidebar_fg": "#c4b5fd",
                "sidebar_sel": "#4c1d95", "sidebar_hover": "#3b0764",
                "input_bg": "#3b0764",
                "topbar_bg": "#1e1b4b", "status_bg": "#17143a",
            },
            "USA": {
                "bg": "#0a1628", "fg": "#e8edf5",
                "panel": "#1a2d4d", "border": "#2e4a73",
                "primary": "#dc2626", "primary_fg": "#ffffff",
                "sidebar_bg": "#0a1628", "sidebar_fg": "#cbd5e1",
                "sidebar_sel": "#1a2d4d", "sidebar_hover": "#152038",
                "input_bg": "#152038",
                "topbar_bg": "#071020", "status_bg": "#06101e",
            },
            "ArchVault": {
                "bg": "#1a1b26", "fg": "#cdd6f4",
                "panel": "#24283b", "border": "#414868",
                "primary": "#1793d1", "primary_fg": "#ffffff",
                "sidebar_bg": "#0d0e17", "sidebar_fg": "#7aa2f7",
                "sidebar_sel": "#24283b", "sidebar_hover": "#1a1b26",
                "input_bg": "#16171f",
                "topbar_bg": "#13141e", "status_bg": "#0a0b12",
            },
        }
        t = themes.get(theme_name, themes["ArchVault"])
        self._current_theme = t

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {t['bg']}; color: {t['fg']};
                font-family: 'Segoe UI', system-ui, sans-serif; font-size: 13px;
            }}
            QStackedWidget {{ background-color: {t['bg']}; }}
            QLabel {{ color: {t['fg']}; background: transparent; background-color: transparent; border: none; text-decoration: none; }}
            QCheckBox, QRadioButton {{ background-color: transparent; }}
            QGroupBox {{
                font-weight: bold; font-size: 14px;
                background-color: {t['panel']};
                border: 1px solid {t['border']}; border-radius: 8px;
                margin-top: 28px; padding: 20px 15px 15px 15px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; subcontrol-position: top left;
                left: 2px; top: 0px;
                background-color: transparent; color: {t['primary']}; padding: 0px;
            }}
            QLineEdit, QComboBox, QSpinBox, QTimeEdit {{
                background-color: {t['input_bg']}; color: {t['fg']};
                border: 1px solid {t['border']}; padding: 8px 12px; border-radius: 6px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t['primary']}; }}
            QComboBox::drop-down {{
                border: none; background: transparent; width: 28px;
                subcontrol-position: center right; subcontrol-origin: padding;
            }}
            QComboBox::down-arrow {{
                image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'><polyline points='1,1 6,6 11,1' fill='none' stroke='{t['fg']}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>");
                width: 12px; height: 8px;
            }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 6px; background: transparent; }}
            QTabBar::tab {{
                background: {t['bg']}; color: {t['sidebar_fg']};
                padding: 10px 20px; border: 1px solid {t['border']};
                border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {t['bg']}; color: {t['primary']};
                font-weight: bold; border-top: 3px solid {t['primary']};
            }}
            QTabWidget > QStackedWidget > QWidget {{ background-color: {t['bg']}; }}
            QTableWidget {{
                background-color: {t['panel']}; color: {t['fg']};
                border: 1px solid {t['border']}; border-radius: 6px;
                gridline-color: transparent;
                selection-background-color: {t['sidebar_sel']};
                alternate-background-color: {t['input_bg']};
            }}
            QHeaderView::section {{
                background-color: {t['bg']}; color: {t['sidebar_fg']};
                font-weight: bold; border: none;
                border-bottom: 1px solid {t['border']}; padding: 8px;
            }}
            QTableWidget::item {{
                background-color: {t['panel']}; color: {t['fg']};
                padding: 5px; border-bottom: 1px solid {t['border']};
            }}
            QTableWidget::item:alternate {{ background-color: {t['input_bg']}; }}
            QTableWidget::item:selected {{ background-color: {t['sidebar_sel']}; color: {t['fg']}; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 20px; border-radius: 5px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QCheckBox, QRadioButton {{ font-weight: bold; font-size: 13px; spacing: 8px; }}
            QCheckBox::indicator, QRadioButton::indicator {{
                width: 18px; height: 18px; border-radius: 3px;
                border: 2px solid {t['border']}; background: {t['input_bg']};
            }}
            QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border: 2px solid {t['primary']}; }}
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
                background: {t['input_bg']}; border: 2px solid {t['primary']};
                image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect x='7' y='7' width='10' height='10' fill='{t['primary']}' rx='2'/></svg>");
            }}
            QRadioButton::indicator {{ border-radius: 9px; }}
            QRadioButton::indicator:checked {{
                image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><circle cx='12' cy='12' r='5' fill='{t['primary']}'/></svg>");
            }}
        """)

        # Theme the sidebar container
        if hasattr(self, '_sidebar_container'):
            self._sidebar_container.setStyleSheet(f"background-color: {t['sidebar_bg']}; border-right: 1px solid {t['border']};")

        # Adjust the sidebar branding text color for light vs dark mode seamlessly
        if hasattr(self, 'title_lbl'):
            arch_color = "#1e293b" if "Light" in theme_name else "#ffffff"
            self.title_lbl.setText(f'<span style="color:{arch_color};">Arch</span><span style="color:#0ea5e9;">Vault</span>')

        # Sidebar QListWidget
        if hasattr(self, 'sidebar'):
            self.sidebar.setStyleSheet(f"""
                QListWidget {{
                    background-color: transparent; color: {t['sidebar_fg']};
                    border: none; font-size: 13px; font-weight: 600; outline: 0;
                    padding-top: 8px;
                }}
                QListWidget::item {{ padding: 10px 16px; border-radius: 8px; margin: 1px 8px; }}
                QListWidget::item:hover {{ background-color: {t['sidebar_hover']}; color: {t['fg']}; }}
                QListWidget::item:selected {{ background-color: {t['sidebar_sel']}; color: {t['primary']}; }}
            """)

        # Top bar
        if hasattr(self, '_top_bar'):
            self._top_bar.setStyleSheet(
                f"background-color: {t['topbar_bg']}; "
                f"border-bottom: 1px solid {t['border']};")
            if hasattr(self, '_top_page_lbl'):
                self._top_page_lbl.setStyleSheet(
                    f"color: {t['fg']}; font-size: 17px; font-weight: bold; background:transparent;")
            if hasattr(self, '_top_version_lbl'):
                self._top_version_lbl.setStyleSheet(
                    f"color: {t['sidebar_fg']}; font-size: 11px; background:transparent; border:none; padding-right:8px;")
            if hasattr(self, '_top_ver_badge'):
                self._top_ver_badge.setStyleSheet(
                    f"background-color: {t['primary']}; color: {t['primary_fg']}; "
                    f"font-size: 10px; font-weight: bold; border-radius: 4px; "
                    f"border:none; padding: 2px 7px;")

        # Status strip
        if hasattr(self, '_status_strip'):
            self._status_strip.setStyleSheet(
                f"background-color: {t['status_bg']}; "
                f"border-top: 1px solid {t['border']};")
        if hasattr(self, '_status_log_lbl'):
            self._status_log_lbl.setStyleSheet(
                f"color: {t['sidebar_fg']}; font-size: 11px; "
                f"font-family: monospace; background:transparent; border:none;")
        if hasattr(self, '_btn_toggle_logs'):
            self._btn_toggle_logs.setStyleSheet(
                f"background-color: {t['input_bg']}; color: {t['sidebar_fg']}; "
                f"border: 1px solid {t['border']}; border-radius: 4px; "
                f"font-size: 11px; padding: 2px 10px; font-weight: bold;")

        # Console
        if hasattr(self, 'console'):
            self.console.setStyleSheet(
                f"background-color: #000000; color: #10b981; "
                f"border: none; border-top: 1px solid {t['border']}; "
                f"padding: 8px; font-family: monospace; font-size: 10pt;")

        # Update header row colours in sidebar
        if hasattr(self, '_header_rows'):
            for row in self._header_rows:
                item = self.sidebar.item(row)
                if item:
                    item.setForeground(QColor(t['primary']))

        # Re-theme dashboard widgets when theme changes
        if hasattr(self, 'retheme_dashboard'):
            self.retheme_dashboard(t)

        # ── Force-style every QComboBox popup view ────────────────────────────
        # The QAbstractItemView popup spawns as a top-level widget, so the main
        # window stylesheet doesn't always reach it.  Walk the widget tree and
        # apply the dropdown colours directly to each combobox's view().
        from PyQt6.QtWidgets import QComboBox as _QCB
        _dropdown_ss = (
            f"QAbstractItemView {{"
            f"  background-color: {t['panel']}; color: {t['fg']};"
            f"  border: 1px solid {t['border']};"
            f"  selection-background-color: {t['primary']};"
            f"  selection-color: {t['primary_fg']};"
            f"  outline: none;"
            f"}}"
        )
        for combo in self.findChildren(_QCB):
            view = combo.view()
            if view:
                view.setStyleSheet(_dropdown_ss)
                # Also set the view's parent (popup container) background
                popup = view.parentWidget()
                if popup:
                    popup.setStyleSheet(f"background-color: {t['panel']}; border: 1px solid {t['border']};")

    def _add_nav_item(self, emoji, label):
        item = QListWidgetItem(f"  {emoji}  {label}")
        item.setSizeHint(QSize(0, 40))
        self.sidebar.addItem(item)

    def _add_category_header(self, label):
        item = QListWidgetItem(f"   {label}")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = QFont("Segoe UI", 9, QFont.Weight.Bold)
        item.setFont(f)
        item.setSizeHint(QSize(0, 28))
        item.setForeground(QColor("#818cf8"))
        self.sidebar.addItem(item)
        if not hasattr(self, '_header_rows'):
            self._header_rows = []
        self._header_rows.append(self.sidebar.count() - 1)

    # ── Full UI init ────────────────────────────────────────────────────────
    def init_ui(self):
        from PyQt6.QtWidgets import QApplication

        # ── 1. GLOBAL LINUX TASKBAR FIX ──
        # Write SVG to disk so Wayland/GNOME compositors have a physical file to read
        icon_dir = os.path.expanduser("~/.local/share/icons")
        os.makedirs(icon_dir, exist_ok=True)
        icon_path = os.path.join(icon_dir, "archvault_logo.svg")
        with open(icon_path, "wb") as f:
            f.write(_LOGO_SVG_DATA)

        # Generate a temporary desktop file mapping "archvault-dev" to the new icon
        desktop_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(desktop_dir, exist_ok=True)
        desktop_file = os.path.join(desktop_dir, "archvault-dev.desktop")
        try:
            with open(desktop_file, "w") as f:
                f.write(f"[Desktop Entry]\nVersion=1.0\nName=ArchVault\nExec={sys.executable} {os.path.abspath(sys.argv[0])}\nIcon={icon_path}\nType=Application\nTerminal=false\n")
        except Exception:
            pass

        app = QApplication.instance()
        if app:
            # Bind the running Python process to the .desktop file we just created
            app.setDesktopFileName("archvault-dev.desktop")
            app.setWindowIcon(QIcon(icon_path))
            
        self.setWindowIcon(QIcon(icon_path))

        cw = QWidget()
        cw.setAutoFillBackground(True)
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── TOP BAR ──────────────────────────────────────────────────────────
        self._top_bar = QFrame()
        self._top_bar.setFixedHeight(48)
        self._top_bar.setStyleSheet("background-color: #0d0f1a; border-bottom: 1px solid #2e3246;")
        tb_lay = QHBoxLayout(self._top_bar)
        tb_lay.setContentsMargins(20, 0, 20, 0)

        # Dynamic page title (restored to "Dashboard" initial state)
        self._top_page_lbl = QLabel("Dashboard")
        self._top_page_lbl.setStyleSheet("color: #e2e8f0; font-size: 17px; font-weight: bold; background:transparent;")
        tb_lay.addWidget(self._top_page_lbl)
        tb_lay.addStretch()

        self._top_version_lbl = QLabel("ArchVault")
        self._top_version_lbl.setStyleSheet("color: #64748b; font-size: 11px; background:transparent; border:none; padding-right:8px;")
        tb_lay.addWidget(self._top_version_lbl)

        from archvault import VERSION as _VER
        self._top_ver_badge = QLabel(_VER)
        self._top_ver_badge.setStyleSheet(
            "background-color: #818cf8; color: white; font-size: 10px; "
            "font-weight: bold; border-radius: 4px; border:none; padding: 2px 7px;")
        tb_lay.addWidget(self._top_ver_badge)

        root.addWidget(self._top_bar)

        # ── CONTENT ROW (sidebar + stack) ────────────────────────────────────
        content_row = QWidget()
        cr_lay = QHBoxLayout(content_row)
        cr_lay.setContentsMargins(0, 0, 0, 0)
        cr_lay.setSpacing(0)

        # Sidebar Container
        self._sidebar_container = QWidget()
        self._sidebar_container.setFixedWidth(230)
        sidebar_lay = QVBoxLayout(self._sidebar_container)
        sidebar_lay.setContentsMargins(0, 20, 0, 0)
        sidebar_lay.setSpacing(0)

        # BRANDING HEADER
        branding_lay = QHBoxLayout()
        branding_lay.setContentsMargins(16, 0, 16, 20)
        branding_lay.setSpacing(12)

        self.logo_lbl = QLabel()
        logo_pix = QPixmap()
        logo_pix.loadFromData(_LOGO_SVG_DATA, "SVG")
        self.logo_lbl.setPixmap(logo_pix.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.logo_lbl.setStyleSheet("background: transparent; border: none; outline: none;")
        branding_lay.addWidget(self.logo_lbl)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("font-family: 'Segoe UI', system-ui, sans-serif; font-size: 20px; font-weight: 700; background: transparent; border: none; outline: none;")
        self.title_lbl.setText('<span style="color:#ffffff;">Arch</span><span style="color:#0ea5e9;">Vault</span>')
        branding_lay.addWidget(self.title_lbl)
        branding_lay.addStretch()

        sidebar_lay.addLayout(branding_lay)

        # Sidebar List
        self.sidebar = QListWidget()
        self._header_rows = []

        for kind, emoji, label, stack_idx in _NAV:
            if kind == "hdr":
                self._add_category_header(label)
            else:
                self._add_nav_item(emoji, label)

        # Build row→stack mapping from _NAV
        self._row_to_stack = {}
        for row, (kind, emoji, label, stack_idx) in enumerate(_NAV):
            if kind == "nav" and stack_idx is not None:
                self._row_to_stack[row] = stack_idx
        # Store label lookup too
        self._row_to_label = {r: label for r, (kind, emoji, label, _) in enumerate(_NAV) if kind == "nav"}

        sidebar_lay.addWidget(self.sidebar)

        # Stack
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(20, 16, 20, 8)
        self.stack.addWidget(self.build_dashboard_page())       # 0
        self.stack.addWidget(self.build_backups_page())         # 1
        self.stack.addWidget(self.build_restore_page())         # 2
        self.stack.addWidget(self.build_snapshot_browser_page())# 3
        self.stack.addWidget(self.build_tasks_page())           # 4
        self.stack.addWidget(self.build_network_page())         # 5
        self.stack.addWidget(self.build_cloud_page())           # 6
        self.stack.addWidget(self.build_local_page())           # 7
        self.stack.addWidget(self.build_usb_page())             # 8
        self.stack.addWidget(self.build_sftp_page())            # 9
        self.stack.addWidget(self.build_jobs_page())            # 10
        self.stack.addWidget(self.build_settings_page())        # 11

        self.stack.setAutoFillBackground(True)
        
        # ── TAB SWITCH ANIMATION (Fade in) ───────────────────────────────────
        self.stack_effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self.stack_effect)
        
        self.stack_anim = QPropertyAnimation(self.stack_effect, b"opacity")
        self.stack_anim.setDuration(180)
        self.stack_anim.setStartValue(0.0)
        self.stack_anim.setEndValue(1.0)
        self.stack_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def handle_sidebar_click(sidebar_row):
            stack_idx = self._row_to_stack.get(sidebar_row)
            if stack_idx is None:
                return
            if self.stack.currentIndex() == stack_idx:
                return
                
            # Trigger smooth fade-in
            self.stack_anim.stop()
            self.stack.setCurrentIndex(stack_idx)
            self.stack_anim.start()

            label = self._row_to_label.get(sidebar_row, "")
            self._top_page_lbl.setText(label)
            
            # Page-specific hooks
            if label == "Jobs":
                if hasattr(self, 'sync_jobs_from_disk'):   self.sync_jobs_from_disk()
                elif hasattr(self, 'refresh_jobs_ui'):     self.refresh_jobs_ui()
            elif label == "Snapshot Browser":
                if hasattr(self, 'snapshot_browser_on_enter'): self.snapshot_browser_on_enter()
            elif label == "Dashboard":
                if hasattr(self, 'refresh_dashboard'):     self.refresh_dashboard()

        self.sidebar.currentRowChanged.connect(handle_sidebar_click)
        self.sidebar.setCurrentRow(0)

        cr_lay.addWidget(self._sidebar_container)
        cr_lay.addWidget(self.stack, 1)
        root.addWidget(content_row, 1)

        # ── BOTTOM: STATUS STRIP + COLLAPSIBLE CONSOLE ───────────────────────
        bottom = QWidget()
        bottom.setAutoFillBackground(True)
        b_lay = QVBoxLayout(bottom)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(0)

        # Status strip — always visible, 32px
        self._status_strip = QFrame()
        self._status_strip.setFixedHeight(32)
        self._status_strip.setStyleSheet("background-color: #070810; border-top: 1px solid #2e3246;")
        ss_lay = QHBoxLayout(self._status_strip)
        ss_lay.setContentsMargins(16, 0, 10, 0)

        self._status_log_lbl = QLabel("Ready.")
        self._status_log_lbl.setStyleSheet(
            "color: #64748b; font-size: 11px; font-family: monospace; background:transparent; border:none;")
        self._status_log_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._status_log_lbl.setMaximumWidth(900)
        ss_lay.addWidget(self._status_log_lbl)
        ss_lay.addStretch()

        self._btn_toggle_logs = QPushButton("▲  Logs")
        self._btn_toggle_logs.setFixedSize(80, 22)
        self._btn_toggle_logs.setStyleSheet(
            "background-color: #151722; color: #94a3b8; border: 1px solid #2e3246; "
            "border-radius: 4px; font-size: 11px; font-weight: bold;")
        self._btn_toggle_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle_logs.clicked.connect(self._toggle_console)
        ss_lay.addWidget(self._btn_toggle_logs)

        b_lay.addWidget(self._status_strip)

        # Console — starts collapsed
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Monospace", 10))
        self.console.setMinimumHeight(0)
        self.console.setMaximumHeight(0)   # collapsed
        self.console.setStyleSheet(
            "background-color: #000000; color: #10b981; border: none; "
            "border-top: 1px solid #2e3246; padding: 8px;")
        b_lay.addWidget(self.console)

        self._console_open = False
        self._console_target_height = 220

        # Animation
        self._console_anim = QPropertyAnimation(self.console, b"maximumHeight")
        self._console_anim.setDuration(220)
        self._console_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        root.addWidget(bottom)
        self._bottom_log_bar = bottom

        # Apply initial log bar visibility from settings
        show_log_bar = getattr(self, "settings", {}).get("show_log_bar", True)
        self._bottom_log_bar.setVisible(show_log_bar)

    def _toggle_console(self):
        """Slide the console open or closed."""
        self._console_open = not self._console_open
        target = self._console_target_height if self._console_open else 0
        current = self.console.maximumHeight()
        self._console_anim.stop()
        self._console_anim.setStartValue(current)
        self._console_anim.setEndValue(target)
        self._console_anim.start()
        self._btn_toggle_logs.setText("▼  Logs" if self._console_open else "▲  Logs")

    def open_console_drawer(self):
        """Open the console drawer if it's currently closed (called when a job starts)."""
        if not self._console_open:
            self._toggle_console()

    def update_status_strip(self, text: str):
        """Update the one-line status strip with the latest log message."""
        if hasattr(self, '_status_log_lbl'):
            # Trim long lines so they don't overflow
            short = text if len(text) <= 110 else text[:107] + "…"
            self._status_log_lbl.setText(short)
