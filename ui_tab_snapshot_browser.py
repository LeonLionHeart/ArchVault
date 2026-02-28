"""
ui_tab_snapshot_browser.py — ArchVault v4.3.8-beta
Snapshot Browser: scan all local/USB profile destinations, list every backup
with size, date, type, and which profile created it — plus one-click restore.

Auto-scans whenever the page is first shown.  Supports:
  • Flat archives     (.tar.gz, .tar.zst, .btrfs, .img.gz, .gpg)
  • Encrypted pairs   (archive.tar.gz + archive.tar.gz.gpg)
  • rsync snapshots   (dated directory trees YYYY-MM-DD_HHMMSS)
"""
import os
import shutil
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QComboBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush
from ui_widgets import ToggleSwitch, confirm_action
from soft_ui_components import (BTN_SUCCESS, BTN_SECONDARY, BTN_DANGER,
                                 BTN_PRIMARY, mk_page_title)
from PyQt6.QtWidgets import QFrame as _QFrame

_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")

def _hsep():
    f = _QFrame()
    f.setFrameShape(_QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f

BACKUP_EXTENSIONS = (".tar.gz", ".tar.zst", ".btrfs", ".img.gz", ".gpg")

_PATH_ROLE = Qt.ItemDataRole.UserRole
_SIZE_ROLE = Qt.ItemDataRole.UserRole + 1


def _human_size(size_b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_b < 1024:
            return f"{size_b:.1f} {unit}"
        size_b /= 1024
    return f"{size_b:.1f} PB"


def _age_str(mtime: datetime) -> str:
    delta = datetime.now() - mtime
    s = int(delta.total_seconds())
    if s < 60:       return "just now"
    if s < 3600:     return f"{s // 60}m ago"
    if s < 86400:    return f"{s // 3600}h ago"
    if s < 86400*7:  return f"{delta.days}d ago"
    if s < 86400*30: return f"{delta.days // 7}w ago"
    return mtime.strftime("%Y-%m-%d")


class _ScanWorker(QThread):
    found    = pyqtSignal(dict)
    finished = pyqtSignal(int)

    def __init__(self, scan_paths):
        super().__init__()
        self.scan_paths = scan_paths
        self._count = 0

    def run(self):
        for label, base_path, cat in self.scan_paths:
            if not base_path or not os.path.isdir(base_path):
                continue
            try:
                entries = sorted(os.listdir(base_path))
            except PermissionError:
                continue

            for name in entries:
                fpath = os.path.join(base_path, name)

                # Flat archives
                if os.path.isfile(fpath) and any(name.endswith(ext) for ext in BACKUP_EXTENSIONS):
                    try:
                        size_b = os.path.getsize(fpath)
                        mtime  = datetime.fromtimestamp(os.path.getmtime(fpath))
                        self.found.emit({
                            "profile":   label,
                            "filename":  name,
                            "full_path": fpath,
                            "size_b":    size_b,
                            "date":      mtime.strftime("%Y-%m-%d %H:%M"),
                            "mtime":     mtime,
                            "type":      self._detect_type(name),
                            "cat":       cat,
                            "kind":      "archive",
                        })
                        self._count += 1
                    except OSError:
                        pass

                # rsync incremental snapshot dirs (YYYY-MM-DD_HHMMSS pattern)
                elif (os.path.isdir(fpath)
                      and name != "latest"
                      and len(name) >= 10
                      and name[4] == "-"):
                    try:
                        size_b = self._dir_size(fpath)
                        mtime  = datetime.fromtimestamp(os.path.getmtime(fpath))
                        self.found.emit({
                            "profile":   label,
                            "filename":  f"[rsync] {name}",
                            "full_path": fpath,
                            "size_b":    size_b,
                            "date":      mtime.strftime("%Y-%m-%d %H:%M"),
                            "mtime":     mtime,
                            "type":      "rsync Incremental",
                            "cat":       cat,
                            "kind":      "rsync_dir",
                        })
                        self._count += 1
                    except OSError:
                        pass

        self.finished.emit(self._count)

    @staticmethod
    def _detect_type(fname):
        if fname.endswith(".tar.gz"):  return "Ext4 / Universal (.tar.gz)"
        if fname.endswith(".tar.zst"): return "Ext4 / Universal (.tar.zst)"
        if fname.endswith(".btrfs"):   return "Btrfs Native (.btrfs)"
        if fname.endswith(".img.gz"):  return "Bare Metal (.img.gz)"
        if fname.endswith(".gpg"):     return "Encrypted (.gpg)"
        return "Archive"

    @staticmethod
    def _dir_size(path):
        total = 0
        for dirpath, _, files in os.walk(path):
            for f in files:
                try: total += os.path.getsize(os.path.join(dirpath, f))
                except OSError: pass
        return total


class _BtrfsSnapWorker(QThread):
    """Enumerate native Btrfs snapshots on this machine via 'btrfs subvolume list'.

    Uses  -s  (snapshots only) and  -t  (tabular) for reliable parsing.
    Falls back to scanning common snapshot directories if the command fails.

    Emits:
        found(dict)     — one row dict per snapshot, same schema as _ScanWorker
        finished(int)   — total count
        error(str)      — non-fatal warning message
    """
    found    = pyqtSignal(dict)
    finished = pyqtSignal(int)
    error    = pyqtSignal(str)

    # Well-known snapshot root directories to check as fallback / supplement
    _SNAP_ROOTS = [
        "/",
        "/.snapshots",
        "/home/.snapshots",
        "/var/.snapshots",
    ]

    def run(self):
        import subprocess, shutil as _sh
        count = 0

        if not _sh.which("btrfs"):
            self.error.emit("'btrfs' command not found — install btrfs-progs.")
            self.finished.emit(0)
            return

        # --- Primary: btrfs subvolume list -s (snapshots only, recursive) ---
        try:
            result = subprocess.run(
                ["btrfs", "subvolume", "list", "-s", "/"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    # Format: "ID <n> gen <n> cgen <n> top level <n> otime <date> <time> path <path>"
                    parts = line.split()
                    if "path" not in parts:
                        continue
                    path_idx = parts.index("path") + 1
                    rel_path = " ".join(parts[path_idx:])
                    abs_path = os.path.join("/", rel_path)

                    # Try to get otime (snapshot creation time) from the line
                    date_str = ""
                    try:
                        ot_idx = parts.index("otime") + 1
                        date_str = f"{parts[ot_idx]} {parts[ot_idx+1]}"
                        mtime    = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    except (ValueError, IndexError):
                        try:
                            mtime = datetime.fromtimestamp(os.path.getmtime(abs_path))
                            date_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
                        except OSError:
                            mtime    = datetime.now()
                            date_str = "Unknown"

                    # Size: du is slow on large subvolumes, use 0 as placeholder
                    # and show "—" via _human_size(0) == "0 B" — tolerable
                    try:
                        size_b = self._subvol_size(abs_path)
                    except Exception:
                        size_b = 0

                    self.found.emit({
                        "profile":   "[BTRFS]",
                        "filename":  os.path.basename(rel_path) or rel_path,
                        "full_path": abs_path,
                        "size_b":    size_b,
                        "date":      mtime.strftime("%Y-%m-%d %H:%M"),
                        "mtime":     mtime,
                        "type":      "Btrfs Subvolume Snapshot",
                        "cat":       "btrfs",
                        "kind":      "btrfs_subvol",
                    })
                    count += 1

        except subprocess.TimeoutExpired:
            self.error.emit("btrfs subvolume list timed out.")
        except Exception as exc:
            self.error.emit(f"btrfs scan error: {exc}")

        # --- Supplement: snapper-style .snapshots directories ---
        seen_paths = set()
        for snap_root in self._SNAP_ROOTS:
            snap_dir = snap_root if snap_root.endswith(".snapshots") else \
                       os.path.join(snap_root, ".snapshots")
            if not os.path.isdir(snap_dir):
                continue
            try:
                for entry in sorted(os.listdir(snap_dir)):
                    entry_path = os.path.join(snap_dir, entry)
                    snap_path  = os.path.join(entry_path, "snapshot")
                    target     = snap_path if os.path.isdir(snap_path) else entry_path
                    if not os.path.isdir(target) or target in seen_paths:
                        continue
                    seen_paths.add(target)
                    try:
                        mtime  = datetime.fromtimestamp(os.path.getmtime(target))
                        size_b = 0  # avoid slow du on large subvolumes
                        # Try to read snapper info.xml for a description
                        desc = entry
                        info_xml = os.path.join(entry_path, "info.xml")
                        if os.path.exists(info_xml):
                            try:
                                with open(info_xml) as fx:
                                    txt = fx.read()
                                import re
                                m = re.search(r"<description>(.*?)</description>", txt)
                                if m and m.group(1):
                                    desc = f"{entry} — {m.group(1)}"
                            except Exception:
                                pass
                        self.found.emit({
                            "profile":   "[BTRFS / snapper]",
                            "filename":  desc,
                            "full_path": target,
                            "size_b":    size_b,
                            "date":      mtime.strftime("%Y-%m-%d %H:%M"),
                            "mtime":     mtime,
                            "type":      "Btrfs Subvolume Snapshot",
                            "cat":       "btrfs",
                            "kind":      "btrfs_subvol",
                        })
                        count += 1
                    except OSError:
                        pass
            except PermissionError:
                pass

        self.finished.emit(count)

    @staticmethod
    def _subvol_size(path: str) -> int:
        """Best-effort size using btrfs quota if available, else 0."""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["btrfs", "qgroup", "show", "--raw", path],
                capture_output=True, text=True, timeout=5
            )
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0].startswith("0/"):
                    return int(parts[1])
        except Exception:
            pass
        return 0


class SnapshotBrowserMixin:

    def build_snapshot_browser_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        root = QVBoxLayout(page)
        root.setSpacing(18)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Page header ───────────────────────────────────────────────────
        root.addWidget(mk_page_title(
            "Snapshot Browser",
            "Browse, restore, and delete backup archives across all targets"))

        root.addWidget(_hsep())

        # ── Controls row ──────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)

        # Btrfs toggle inline left side
        self._sb_btrfs_toggle = ToggleSwitch()
        self._sb_btrfs_toggle.setChecked(False)
        self._sb_btrfs_toggle.toggled.connect(self._on_btrfs_toggle)
        btrfs_lbl = QLabel("Show native Btrfs snapshots")
        btrfs_lbl.setStyleSheet(
            "font-size:12px; font-weight:600; background:transparent; border:none;")
        self._sb_btrfs_status = QLabel("")
        self._sb_btrfs_status.setStyleSheet(
            "color:#64748b; font-size:11px; background:transparent; border:none;")

        ctrl.addWidget(self._sb_btrfs_toggle)
        ctrl.addWidget(btrfs_lbl)
        ctrl.addWidget(self._sb_btrfs_status)
        ctrl.addStretch()

        # Filter combo
        filter_lbl = QLabel("FILTER")
        filter_lbl.setStyleSheet(_SEC_LABEL)
        self._sb_filter = QComboBox()
        self._sb_filter.setMinimumWidth(200)
        self._sb_filter.addItem("All Profiles")
        self._sb_filter.currentTextChanged.connect(self._sb_apply_filter)

        btn_scan = QPushButton("⟳  Refresh")
        btn_scan.setStyleSheet(BTN_PRIMARY)
        btn_scan.clicked.connect(self.scan_snapshots)

        ctrl.addWidget(filter_lbl)
        ctrl.addWidget(self._sb_filter)
        ctrl.addWidget(btn_scan)
        root.addLayout(ctrl)

        root.addWidget(_hsep())

        # ── Status label ──────────────────────────────────────────────────
        self._sb_status = QLabel("Preparing scan…")
        self._sb_status.setStyleSheet(
            "color:#64748b; font-size:12px; font-style:italic; "
            "background:transparent; border:none;")
        root.addWidget(self._sb_status)

        # ── Snapshot table ────────────────────────────────────────────────
        self._sb_table = QTableWidget(0, 6)
        self._sb_table.setHorizontalHeaderLabels(
            ["Profile", "Snapshot / Filename", "Type", "Size", "Date", "Age"])
        hh = self._sb_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._sb_table.verticalHeader().hide()
        self._sb_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sb_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sb_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._sb_table.setSortingEnabled(True)
        self._sb_table.setAlternatingRowColors(False)
        self._sb_table.setShowGrid(False)
        self._sb_table.setStyleSheet("""
            QTableWidget {
                background:transparent;
                border:1px solid rgba(100,116,139,0.25);
                border-radius:6px;
                outline:0;
            }
            QTableWidget::item {
                padding:8px 10px;
                border-bottom:1px solid rgba(100,116,139,0.1);
                background:transparent;
            }
            QTableWidget::item:selected {
                background:rgba(59,130,246,0.12);
                color: palette(text);
            }
            QHeaderView::section {
                background:transparent;
                color:#64748b;
                font-size:10px;
                font-weight:700;
                letter-spacing:1px;
                border:none;
                border-bottom:1px solid rgba(100,116,139,0.2);
                padding:8px 10px;
            }
        """)
        self._sb_table.currentCellChanged.connect(self._sb_show_path)
        root.addWidget(self._sb_table, 1)

        # ── Path detail strip ─────────────────────────────────────────────
        self._sb_path_lbl = QLabel("")
        self._sb_path_lbl.setStyleSheet(
            "color:#64748b; font-size:11px; font-family:monospace; "
            "background:transparent; border:none; padding:2px 0;")
        self._sb_path_lbl.setWordWrap(True)
        root.addWidget(self._sb_path_lbl)

        root.addWidget(_hsep())

        # ── Action buttons ────────────────────────────────────────────────
        act = QHBoxLayout()
        act.setSpacing(10)
        btn_restore = QPushButton("▼  Restore Selected")
        btn_restore.setStyleSheet(BTN_SUCCESS)
        btn_restore.clicked.connect(self._sb_restore_selected)
        btn_delete = QPushButton("🗑  Delete")
        btn_delete.setStyleSheet(BTN_DANGER)
        btn_delete.clicked.connect(self._sb_delete_selected)
        act.addWidget(btn_restore)
        act.addWidget(btn_delete)
        act.addStretch()
        root.addLayout(act)

        self._sb_all_rows = []
        self._sb_worker   = None
        self._sb_scanned  = False
        self._sb_btrfs_worker = None

        return page

    # ─────────────────────────────────────────────────────────────────────
    # AUTO-SCAN HOOK  —  called by ui_shell on sidebar navigation
    # ─────────────────────────────────────────────────────────────────────
    def snapshot_browser_on_enter(self):
        """Kick off a background scan the first time (or when list is empty)."""
        if not self._sb_scanned or self._sb_table.rowCount() == 0:
            QTimer.singleShot(150, self.scan_snapshots)

    # ─────────────────────────────────────────────────────────────────────
    # SCAN
    # ─────────────────────────────────────────────────────────────────────
    def scan_snapshots(self):
        if self._sb_worker and self._sb_worker.isRunning():
            self._sb_worker.quit()
            self._sb_worker.wait(500)

        self._sb_table.setRowCount(0)
        self._sb_all_rows = []
        self._sb_path_lbl.setText("")

        self._sb_filter.blockSignals(True)
        self._sb_filter.clear()
        self._sb_filter.addItem("All Profiles")
        self._sb_filter.blockSignals(False)

        self._sb_status.setText("⟳  Scanning destinations…")

        # Re-trigger btrfs scan if the toggle is still on after a full refresh
        if hasattr(self, '_sb_btrfs_toggle') and self._sb_btrfs_toggle.isChecked():
            self._sb_btrfs_status.setText("⏳  Scanning for Btrfs snapshots…")
            self._sb_btrfs_status.setStyleSheet(
                "color: #64748b; font-size: 11px; background: transparent; margin-left: 12px;"
            )
            if self._sb_btrfs_worker and self._sb_btrfs_worker.isRunning():
                self._sb_btrfs_worker.quit()
                self._sb_btrfs_worker.wait(300)
            worker = _BtrfsSnapWorker()
            worker.found.connect(self._sb_add_btrfs_row)
            worker.finished.connect(self._sb_btrfs_scan_done)
            worker.error.connect(self._sb_btrfs_error)
            self._sb_btrfs_worker = worker
            worker.start()

        scan_paths = []
        for cat in ("local", "usb", "network"):
            for pname, pdata in self.profiles.get(cat, {}).items():
                path = pdata.get("path", "")
                if path and os.path.isdir(path):
                    scan_paths.append((f"[{cat.upper()}] {pname}", path, cat))

        if not scan_paths:
            self._sb_status.setText(
                "No accessible local/USB destinations found. "
                "Add profiles under Local Storage or USB / Removable first."
            )
            return

        self._sb_worker = _ScanWorker(scan_paths)
        self._sb_worker.found.connect(self._sb_add_row)
        self._sb_worker.finished.connect(self._sb_scan_done)
        self._sb_worker.start()

    def _sb_add_row(self, row: dict):
        self._sb_all_rows.append(row)
        self._sb_render_row(row)
        prof = row["profile"]
        if self._sb_filter.findText(prof) < 0:
            self._sb_filter.addItem(prof)

    def _sb_render_row(self, row: dict):
        tbl = self._sb_table
        r   = tbl.rowCount()
        tbl.setSortingEnabled(False)
        tbl.insertRow(r)

        c0 = QTableWidgetItem(row["profile"])
        c0.setForeground(QBrush(QColor("#818cf8")))
        c0.setData(_PATH_ROLE, row["full_path"])
        tbl.setItem(r, 0, c0)

        c1 = QTableWidgetItem(row["filename"])
        c1.setToolTip(row["full_path"])
        tbl.setItem(r, 1, c1)

        TYPE_COLORS = {
            "Ext4 / Universal (.tar.gz)":  "#a5b4fc",
            "Ext4 / Universal (.tar.zst)": "#a5b4fc",
            "Btrfs Native (.btrfs)":       "#34d399",
            "Bare Metal (.img.gz)":        "#fb923c",
            "Encrypted (.gpg)":            "#fbbf24",
            "rsync Incremental":           "#38bdf8",
            "Btrfs Subvolume Snapshot":    "#a3e635",
        }
        c2 = QTableWidgetItem(row["type"])
        c2.setForeground(QBrush(QColor(TYPE_COLORS.get(row["type"], "#94a3b8"))))
        tbl.setItem(r, 2, c2)

        c3 = QTableWidgetItem(_human_size(row["size_b"]))
        c3.setForeground(QBrush(QColor("#10b981")))
        c3.setData(_SIZE_ROLE, row["size_b"])
        c3.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tbl.setItem(r, 3, c3)

        tbl.setItem(r, 4, QTableWidgetItem(row["date"]))
        tbl.setItem(r, 5, QTableWidgetItem(_age_str(row["mtime"])))

        tbl.setSortingEnabled(True)

    def _sb_scan_done(self, total: int):
        n_profiles = self._sb_filter.count() - 1
        if total:
            self._sb_status.setText(
                f"✓  Found {total} backup{'s' if total != 1 else ''} "
                f"across {n_profiles} profile{'s' if n_profiles != 1 else ''}. "
                "Select a row then click Restore to launch restore."
            )
        else:
            self._sb_status.setText(
                "No backups found in configured destinations. "
                "Run a backup first, or check that profile paths are accessible."
            )
        self._sb_scanned = True
        self._sb_worker  = None

    # ── BTRFS native snapshot toggle ──────────────────────────────────────
    def _on_btrfs_toggle(self, checked: bool):
        """Start or stop the Btrfs native snapshot scan when toggle changes."""
        if checked:
            self._sb_btrfs_status.setText("⏳  Scanning for Btrfs snapshots…")
            # Remove any existing btrfs rows first
            self._sb_remove_btrfs_rows()
            if self._sb_btrfs_worker and self._sb_btrfs_worker.isRunning():
                self._sb_btrfs_worker.quit()
                self._sb_btrfs_worker.wait(300)
            worker = _BtrfsSnapWorker()
            worker.found.connect(self._sb_add_btrfs_row)
            worker.finished.connect(self._sb_btrfs_scan_done)
            worker.error.connect(self._sb_btrfs_error)
            self._sb_btrfs_worker = worker
            worker.start()
        else:
            self._sb_remove_btrfs_rows()
            self._sb_btrfs_status.setText("")
            if self._sb_btrfs_worker and self._sb_btrfs_worker.isRunning():
                self._sb_btrfs_worker.quit()

    def _sb_add_btrfs_row(self, row: dict):
        """Add a Btrfs snapshot row — tracked in _sb_all_rows with kind=btrfs_subvol."""
        self._sb_all_rows.append(row)
        self._sb_render_row(row)
        prof = row["profile"]
        if self._sb_filter.findText(prof) < 0:
            self._sb_filter.addItem(prof)

    def _sb_remove_btrfs_rows(self):
        """Remove all rows with cat='btrfs' from both the table and _sb_all_rows."""
        self._sb_all_rows = [r for r in self._sb_all_rows if r.get("cat") != "btrfs"]
        tbl = self._sb_table
        tbl.setSortingEnabled(False)
        row = tbl.rowCount() - 1
        while row >= 0:
            item = tbl.item(row, 0)
            if item and item.text().startswith("[BTRFS"):
                tbl.removeRow(row)
            row -= 1
        tbl.setSortingEnabled(True)
        # Clean up filter entries that no longer have rows
        for label in ("[BTRFS]", "[BTRFS / snapper]"):
            idx = self._sb_filter.findText(label)
            if idx >= 0:
                self._sb_filter.removeItem(idx)

    def _sb_btrfs_scan_done(self, total: int):
        if total:
            self._sb_btrfs_status.setText(
                f"✓  {total} Btrfs snapshot{'s' if total != 1 else ''} found"
            )
            self._sb_btrfs_status.setStyleSheet(
                "color: #10b981; font-size: 11px; background: transparent; margin-left: 12px;"
            )
        else:
            self._sb_btrfs_status.setText("No Btrfs snapshots found on this system")
            self._sb_btrfs_status.setStyleSheet(
                "color: #f59e0b; font-size: 11px; background: transparent; margin-left: 12px;"
            )
        self._sb_btrfs_worker = None

    def _sb_btrfs_error(self, msg: str):
        self._sb_btrfs_status.setText(f"⚠  {msg}")
        self._sb_btrfs_status.setStyleSheet(
            "color: #ef4444; font-size: 11px; background: transparent; margin-left: 12px;"
        )

    def _sb_apply_filter(self, text: str):
        """Show only rows matching the selected profile label (or all)."""
        for r in range(self._sb_table.rowCount()):
            item = self._sb_table.item(r, 0)
            if item:
                self._sb_table.setRowHidden(
                    r, text != "All Profiles" and item.text() != text
                )


        for r in range(self._sb_table.rowCount()):
            item = self._sb_table.item(r, 0)
            if item:
                self._sb_table.setRowHidden(
                    r, text != "All Profiles" and item.text() != text
                )

    def _sb_show_path(self, row, *_):
        item = self._sb_table.item(row, 0) if row >= 0 else None
        if item:
            self._sb_path_lbl.setText(f"Path: {item.data(_PATH_ROLE) or ''}")
        else:
            self._sb_path_lbl.setText("")

    # ─────────────────────────────────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────────────────────────────────
    def _sb_selected_path(self):
        row = self._sb_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No Selection",
                "Click a row in the table to select a snapshot first.")
            return None
        item = self._sb_table.item(row, 0)
        return item.data(_PATH_ROLE) if item else None

    def _sb_restore_selected(self):
        path = self._sb_selected_path()
        if not path:
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Snapshot Not Found",
                f"This backup no longer exists:\n{path}\n\nClick ⟳ Refresh to update.")
            return
        try:
            self.analyze_restore_file(path)
            restore_row = next(
                (k for k, v in self._row_to_stack.items() if v == 2), None
            )
            if restore_row is not None:
                self.sidebar.setCurrentRow(restore_row)
            self.log(f"INFO: Snapshot Browser → loaded '{os.path.basename(path)}' into Restore tab.")
        except Exception as e:
            QMessageBox.critical(self, "Error",
                f"Could not load snapshot into Restore tab:\n{e}")

    def _sb_delete_selected(self):
        path = self._sb_selected_path()
        if not path:
            return
        name   = os.path.basename(path)
        is_dir = os.path.isdir(path)
        kind   = "directory" if is_dir else "file"
        if not confirm_action(
                self, "Permanently Delete Backup",
                f"Are you sure you want to permanently delete this "
                f"backup {kind}?",
                detail=f"{path}\n\nThis cannot be undone.",
                confirm_text="Delete Permanently", destructive=True,
                icon_char="🗑"):
            return
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
                # Remove companion manifest if present
                for suffix in (".tar.gz", ".tar.zst"):
                    manifest = path.replace(suffix, ".manifest.gz")
                    if manifest != path and os.path.exists(manifest):
                        os.remove(manifest)
                        self.log(f"BROWSER: Removed companion manifest for '{name}'.")
            self.log(f"BROWSER: Deleted snapshot '{name}'.")
            self.scan_snapshots()
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
