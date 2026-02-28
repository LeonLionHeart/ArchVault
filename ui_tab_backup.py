from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QComboBox, QMessageBox,
                             QApplication, QTextEdit, QFrame, QSizePolicy, QProgressBar)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from ui_widgets import ToggleSwitch
from soft_ui_components import (BTN_SUCCESS, BTN_DANGER, BTN_WARNING,
                                 BTN_SECONDARY, mk_page_title)
import os

VERSION = "v5.0.0-beta"

_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")

_ENG_ACTIVE   = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #1d4ed8,stop:1 #3b82f6); color:#fff; "
                 "font-weight:700; font-size:12px; padding:8px 18px; "
                 "border-radius:8px; border:none;")
_ENG_INACTIVE = ("background:transparent; color:#64748b; font-weight:600; "
                 "font-size:12px; padding:8px 18px; border-radius:8px; "
                 "border:1px solid rgba(100,116,139,0.3);")


def _hsep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f


class BackupMixin:
    def build_backups_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        root = QVBoxLayout(page)
        root.setSpacing(18)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Page header ───────────────────────────────────────────────────────
        root.addWidget(mk_page_title(
            "Execute Backup",
            "Configure your engine and target, then stream a backup"))

        root.addWidget(_hsep())

        # ── FILESYSTEM ENGINE ─────────────────────────────────────────────────
        eng_lbl = QLabel("FILESYSTEM ENGINE")
        eng_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(eng_lbl)

        eng_row = QHBoxLayout()
        eng_row.setSpacing(6)
        eng_row.setContentsMargins(0, 0, 0, 0)

        self._eng_btn_btrfs = QPushButton("⬡  Btrfs Native")
        self._eng_btn_tar   = QPushButton("📦  Ext4 / Universal")
        self._eng_btn_rsync = QPushButton("🔗  rsync Incremental")

        # Hidden combo keeps downstream engine_combo references working
        self.engine_combo = QComboBox()
        self.engine_combo.addItems([
            "Btrfs Native  (.btrfs snapshot)",
            "Ext4 / Universal  (.tar.gz archive)",
            "rsync Incremental  (hardlink, space-efficient)",
        ])
        self.engine_combo.hide()

        def _set_engine(idx, active_btn):
            self.engine_combo.setCurrentIndex(idx)
            for b in (self._eng_btn_btrfs, self._eng_btn_tar, self._eng_btn_rsync):
                b.setStyleSheet(_ENG_INACTIVE)
            active_btn.setStyleSheet(_ENG_ACTIVE)

        self._eng_btn_btrfs.setStyleSheet(_ENG_ACTIVE)
        self._eng_btn_tar.setStyleSheet(_ENG_INACTIVE)
        self._eng_btn_rsync.setStyleSheet(_ENG_INACTIVE)
        self._eng_btn_btrfs.clicked.connect(lambda: _set_engine(0, self._eng_btn_btrfs))
        self._eng_btn_tar.clicked.connect(lambda: _set_engine(1, self._eng_btn_tar))
        self._eng_btn_rsync.clicked.connect(lambda: _set_engine(2, self._eng_btn_rsync))

        eng_row.addWidget(self._eng_btn_btrfs)
        eng_row.addWidget(self._eng_btn_tar)
        eng_row.addWidget(self._eng_btn_rsync)
        eng_row.addStretch()
        root.addLayout(eng_row)

        root.addWidget(_hsep())

        # ── TARGET PROFILE ────────────────────────────────────────────────────
        tgt_lbl = QLabel("TARGET PROFILE")
        tgt_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(tgt_lbl)

        self.target_combo = QComboBox()
        self.target_combo.setMinimumHeight(36)
        self.target_combo.currentIndexChanged.connect(self.trigger_auto_detect_backup)
        root.addWidget(self.target_combo)

        root.addWidget(_hsep())

        # ── ACTION ROW ────────────────────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.setSpacing(10)

        self.btn_run_backup = QPushButton("▲  Start Backup")
        self.btn_run_backup.setStyleSheet(BTN_SUCCESS)
        self.btn_run_backup.clicked.connect(self.start_backup_process)

        self.btn_backup_pause = QPushButton("⏸  Pause")
        self.btn_backup_pause.setStyleSheet(BTN_WARNING)
        self.btn_backup_pause.setEnabled(False)
        self.btn_backup_pause.clicked.connect(self.toggle_pause)

        self.btn_backup_stop = QPushButton("✕  Cancel")
        self.btn_backup_stop.setStyleSheet(BTN_DANGER)
        self.btn_backup_stop.setEnabled(False)
        self.btn_backup_stop.clicked.connect(self.stop_process)

        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setFixedWidth(1)
        vdiv.setFixedHeight(28)
        vdiv.setStyleSheet("background:rgba(100,116,139,0.3); border:none;")

        self.chk_val_backup = ToggleSwitch()
        self.chk_val_backup.setChecked(
            getattr(self, "settings", {}).get("auto_validate", False))
        val_lbl = QLabel("Validate on Completion")
        val_lbl.setStyleSheet(
            "font-size:12px; font-weight:600; background:transparent; border:none;")

        act_row.addWidget(self.btn_run_backup)
        act_row.addWidget(self.btn_backup_pause)
        act_row.addWidget(self.btn_backup_stop)
        act_row.addStretch()
        act_row.addWidget(vdiv)
        act_row.addSpacing(8)
        act_row.addWidget(self.chk_val_backup)
        act_row.addWidget(val_lbl)
        root.addLayout(act_row)

        root.addWidget(_hsep())

        # ── STREAM PROGRESS CARD ──────────────────────────────────────────────
        prog_sec = QLabel("STREAM PROGRESS")
        prog_sec.setStyleSheet(_SEC_LABEL)
        root.addWidget(prog_sec)

        self._backup_prog_card = QWidget()
        self._backup_prog_card.setStyleSheet(
            "background: rgba(100,116,139,0.06); "
            "border: 1px solid rgba(100,116,139,0.15); border-radius: 10px;")
        card_lay = QVBoxLayout(self._backup_prog_card)
        card_lay.setContentsMargins(16, 12, 16, 12)
        card_lay.setSpacing(6)

        # Status line — phase of operation
        self._backup_status_label = QLabel("Idle — no active backup")
        self._backup_status_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; background: transparent; border: none;")
        card_lay.addWidget(self._backup_status_label)

        # Progress bar — thick, styled
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(100,116,139,0.18);
                border-radius: 6px; border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1d4ed8, stop:1 #60a5fa);
                border-radius: 6px;
            }
        """)
        card_lay.addWidget(self.progress_bar)

        # Stats line — percentage, transferred, rate, ETA
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(
            "font-size: 12px; color: #64748b; background: transparent; border: none; "
            "font-family: 'Segoe UI', system-ui, monospace;")
        card_lay.addWidget(self.progress_label)

        # Directory / path line
        self._backup_dir_label = QLabel("")
        self._backup_dir_label.setStyleSheet(
            "font-size: 12px; color: #64748b; background: transparent; border: none;")
        self._backup_dir_label.setWordWrap(True)
        card_lay.addWidget(self._backup_dir_label)

        # Size info line — source size, destination free space
        self._backup_size_label = QLabel("")
        self._backup_size_label.setStyleSheet(
            "font-size: 11px; color: #64748b; background: transparent; border: none;")
        card_lay.addWidget(self._backup_size_label)

        root.addWidget(self._backup_prog_card)
        root.addStretch(1)

        return page

    def _show_progress_card(self, show: bool):
        if hasattr(self, '_prog_card'):
            self._prog_card.setVisible(show)

    def _reset_backup_progress_card(self):
        """Reset the backup progress card to idle state."""
        if hasattr(self, '_backup_status_label'):
            self._backup_status_label.setText("Idle — no active backup")
        if hasattr(self, 'progress_bar'):
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
        if hasattr(self, 'progress_label'):
            self.progress_label.setText("")
        if hasattr(self, '_backup_dir_label'):
            self._backup_dir_label.setText("")
        if hasattr(self, '_backup_size_label'):
            self._backup_size_label.setText("")

    def analyze_restore_file(self, file_path):
        if not file_path: return
        self.rst_source.setText(file_path)
        if file_path.endswith(".tar.gz"):
            self.rst_engine.setCurrentText("Ext4 / Universal (.tar.gz)")
            self.rst_dest_path.show(); self.rst_dest_btn.show()
            self.rst_dest_drive.hide(); self.rst_refresh_drives.hide()
            self.rst_bm_warning.hide()
            self.rst_toggle_selective.setEnabled(True)
            self.populate_file_tree(file_path)
        elif file_path.endswith(".btrfs"):
            self.rst_engine.setCurrentText("Btrfs Native (.btrfs)")
            self.rst_dest_path.show(); self.rst_dest_btn.show()
            self.rst_dest_drive.hide(); self.rst_refresh_drives.hide()
            self.rst_bm_warning.hide()
            self.rst_toggle_selective.setEnabled(True)
            self.populate_file_tree(file_path)
        elif file_path.endswith(".img.gz"):
            self.rst_engine.setCurrentText("Bare Metal Image (.img.gz)")
            self.rst_dest_path.hide(); self.rst_dest_btn.hide()
            self.rst_dest_drive.show(); self.rst_refresh_drives.show()
            self.rst_bm_warning.show()
            self.rst_toggle_selective.setEnabled(False)
            self.rst_toggle_full.setChecked(True)
            if hasattr(self, 'get_drives_data'):
                self.rst_dest_drive.clear()
                for dev in self.get_drives_data():
                    size_gb = int(dev.get("size", 0)) / (1024**3)
                    self.rst_dest_drive.addItem(
                        f"{dev['path']} ({size_gb:.1f} GB) - {dev.get('type')}")

    def copy_backup_logs(self):
        QApplication.clipboard().setText(self.console.toPlainText())
        QMessageBox.information(self, "Copied", "Activity logs copied to clipboard.")

    def trigger_auto_detect_backup(self):
        try: self.auto_detect_fs(self.target_combo, self.engine_combo)
        except Exception: pass
