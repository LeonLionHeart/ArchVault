from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QMessageBox, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QListWidget, QListWidgetItem, QApplication, QFrame, QSizePolicy,
    QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from ui_widgets import ToggleSwitch
from soft_ui_components import (BTN_PRIMARY, BTN_SUCCESS, BTN_DANGER, BTN_WARNING,
                                 BTN_SECONDARY, mk_page_title)
import os
import subprocess
from datetime import datetime


class _MountScanWorker(QThread):
    """Mount a network/SFTP share and scan for backup files off the main thread."""
    status   = pyqtSignal(str)
    success  = pyqtSignal(list, str)
    error    = pyqtSignal(str)

    def __init__(self, mount_cmd, mnt, cat):
        super().__init__()
        self.mount_cmd = mount_cmd
        self.mnt       = mnt
        self.cat       = cat

    def run(self):
        subprocess.run(
            f"umount -l {self.mnt} 2>/dev/null; fusermount -u {self.mnt} 2>/dev/null; true",
            shell=True
        )
        os.makedirs(self.mnt, exist_ok=True)

        self.status.emit("⏳  Mounting share…")
        try:
            result = subprocess.run(
                self.mount_cmd, shell=True,
                capture_output=True, text=True, timeout=25
            )
            if result.returncode != 0:
                err = (result.stderr.strip() or result.stdout.strip() or "Mount failed")[:140]
                self.error.emit(f"❌  Mount failed: {err}")
                return
        except subprocess.TimeoutExpired:
            self.error.emit("❌  Mount timed out — check network connectivity.")
            return
        except Exception as e:
            self.error.emit(f"❌  Error during mount: {e}")
            return

        self.status.emit("🔍  Scanning for backups…")
        exts  = ('.tar.gz', '.tar.zst', '.btrfs', '.img.gz', '.gpg')
        found = []
        try:
            for fname in sorted(os.listdir(self.mnt)):
                if not any(fname.endswith(e) for e in exts):
                    continue
                fpath = os.path.join(self.mnt, fname)
                if not os.path.isfile(fpath):
                    continue
                size_mb  = os.path.getsize(fpath) / (1024 * 1024)
                date_str = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M")
                found.append((fname, fpath, round(size_mb, 1), date_str))
        except Exception as e:
            self.error.emit(f"❌  Could not scan share: {e}")
            return

        self.success.emit(found, self.mnt)


class _SSHListWorker(QThread):
    """List rsync snapshot directories on a remote host via SSH, without FUSE.

    Emits success(found_list, status_note) or error(msg).
    found_list entries: (display_name, remote_path_string, size_mb, date_str)
    """
    status  = pyqtSignal(str)
    success = pyqtSignal(list, str)
    error   = pyqtSignal(str)

    def __init__(self, conn_str: str, prof: dict, app_ref):
        super().__init__()
        # conn_str format: "user@host:port:rpath"
        self.conn_str = conn_str
        self.prof     = prof
        self.app_ref  = app_ref   # needed for decrypt_pw

    def run(self):
        import shutil as _sh, os as _os
        conn   = self.conn_str          # "user@host:port:rpath"
        parts  = conn.split(":", 2)     # ["user@host", "port", "rpath"]
        if len(parts) < 3:
            self.error.emit("❌  Malformed SFTP profile — missing port or path.")
            return

        userhost, port, rpath = parts[0], parts[1], parts[2]
        user, host = (userhost.split("@", 1) + [""])[:2]
        auth  = self.prof.get("auth_method", "SSH Key")
        kfile = self.prof.get("key_file", "").strip()

        ssh_base = (
            f"ssh -p {port} -o StrictHostKeyChecking=no "
            f"-o ConnectTimeout=10 -o BatchMode={'yes' if auth == 'SSH Key' else 'no'}"
        )
        if auth == "SSH Key" and kfile:
            if _os.path.exists(kfile):
                _os.chmod(kfile, 0o600)
            ssh_base += f" -i '{kfile}'"

        if auth == "Password":
            if not _sh.which("sshpass"):
                self.error.emit("❌  Password auth requires sshpass (sudo pacman -S sshpass).")
                return
            pw = self.app_ref.decrypt_pw(self.prof.get("password", "")).replace("'", "\\'")
            prefix = f"sshpass -p '{pw}' "
        else:
            prefix = ""

        self.status.emit("⏳  Connecting to remote host…")

        # First: connectivity test
        test_cmd = f"{prefix}{ssh_base} {userhost} 'echo ARCHVAULT_OK' 2>&1"
        try:
            r = subprocess.run(test_cmd, shell=True, capture_output=True, text=True, timeout=15)
            if r.returncode != 0 or "ARCHVAULT_OK" not in (r.stdout + r.stderr):
                err = (r.stdout + r.stderr).strip()[:120] or f"exit {r.returncode}"
                self.error.emit(f"❌  SSH connection failed: {err}")
                return
        except subprocess.TimeoutExpired:
            self.error.emit("❌  SSH timed out — check hostname, port, and credentials.")
            return

        self.status.emit("🔍  Listing remote snapshots…")

        # List entries in rpath: print name, size (du -sb), and mtime
        # LS format: "YYYY-MM-DD HH:MM  <name>  <size_bytes>"
        list_cmd = (
            f"{prefix}{ssh_base} {userhost} "
            f"\"ls -1 '{rpath}' 2>/dev/null | grep -v '^latest$'\" 2>&1"
        )
        try:
            r = subprocess.run(list_cmd, shell=True, capture_output=True, text=True, timeout=20)
            raw = r.stdout.strip()
        except subprocess.TimeoutExpired:
            self.error.emit("❌  Timed out listing remote directory.")
            return

        if not raw:
            note = f"{userhost}:{rpath}"
            self.success.emit([], note)
            return

        found = []
        for entry in sorted(raw.splitlines(), reverse=True):
            entry = entry.strip()
            if not entry or len(entry) < 10 or entry[4] != "-":
                continue   # skip non-date-named entries
            remote_path = f"{rpath}/{entry}"
            # Get size and mtime via SSH du + stat
            stat_cmd = (
                f"{prefix}{ssh_base} {userhost} "
                f"\"du -sb '{remote_path}' 2>/dev/null | awk '{{print \\$1}}'; "
                f"stat -c '%Y' '{remote_path}' 2>/dev/null\" 2>/dev/null"
            )
            size_b = 0
            date_str = entry[:16].replace("_", " ")  # fallback from name
            try:
                sr = subprocess.run(stat_cmd, shell=True, capture_output=True, text=True, timeout=10)
                lines = sr.stdout.strip().splitlines()
                if lines:
                    try: size_b = int(lines[0])
                    except ValueError: pass
                if len(lines) > 1:
                    try:
                        from datetime import datetime as _dt
                        date_str = _dt.fromtimestamp(int(lines[1])).strftime("%Y-%m-%d %H:%M")
                    except (ValueError, OSError):
                        pass
            except subprocess.TimeoutExpired:
                pass

            size_mb = size_b / (1024 * 1024)
            found.append((entry, remote_path, round(size_mb, 1), date_str))

        note = f"{userhost}:{rpath}"
        self.success.emit(found, note)


def _decomp_cmd(file_path: str) -> list:
    """Return the fastest available decompressor for this archive."""
    import shutil
    if file_path.endswith(".tar.zst"):
        return ["zstd", "-T0", "-dc", file_path]
    if shutil.which("pigz"):
        return ["pigz", "-dc", file_path]
    return ["zcat", file_path]


def _open_tar_list(file_path: str):
    """Open decomp | tar -t pipeline. Returns (decomp_proc, tar_proc)."""
    decomp = subprocess.Popen(
        _decomp_cmd(file_path),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    tar = subprocess.Popen(
        ["tar", "-t"], stdin=decomp.stdout,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    decomp.stdout.close()
    return decomp, tar


def _norm(entry: str) -> str:
    """Strip leading ./ or / and trailing /."""
    e = entry.strip()
    if e.startswith("./"): e = e[2:]
    return e.lstrip("/").rstrip("/")


import sqlite3, hashlib
_IDX_DIR = "/tmp/archvault_idx"


def _db_path_for(archive: str) -> str:
    h = hashlib.md5(archive.encode()).hexdigest()[:12]
    return os.path.join(_IDX_DIR, f"{h}.db")


class _ArchiveIndexer(QThread):
    """Stream ALL tar entries, emit each new top-level name the instant it's found.

    No early-kill heuristic. A sorted full-system backup packs thousands of
    files under home/chase/.config/ before reaching home/chase/Documents/,
    Pictures/, Videos/ etc. Killing early = missing top-level folders.

    entries appear live as tar decompresses. Phase 2 (SQLite index) only
    starts when the user first clicks a folder ▶.
    """
    entry_found   = pyqtSignal(str, bool)  # (name, is_dir) — live
    entry_upgrade = pyqtSignal(str)        # name was file, now known to be a dir
    top_done      = pyqtSignal(int)
    index_ready   = pyqtSignal(str)
    progress      = pyqtSignal(str)
    error         = pyqtSignal(str)

    def __init__(self, archive: str):
        super().__init__()
        self.archive    = archive
        self._stop      = False
        self._build_idx = False

    def stop(self):
        self._stop = True

    def request_index(self):
        """Called when user first expands a folder."""
        self._build_idx = True
        if not self.isRunning():
            t = _IndexBuildThread(self.archive)
            t.progress.connect(self.progress)
            t.ready.connect(self.index_ready)
            self._idx_thread = t
            t.start()

    def run(self):
        # ── Phase 1: read ALL entries, emit each new top-level name immediately ─
        seen  = {}
        count = 0
        decomp, tar = _open_tar_list(self.archive)
        try:
            for raw in tar.stdout:
                if self._stop:
                    break
                raw_s = raw.strip()
                # Detect directory BEFORE _norm strips the trailing slash
                # tar marks directory entries with a trailing /
                raw_is_dir = raw_s.endswith("/")
                e = _norm(raw_s)
                if not e or e == ".":
                    continue
                first = e.split("/")[0]
                if not first:
                    continue
                # is_dir if: tar marked it as dir (trailing /) OR it has sub-path
                is_dir = raw_is_dir or "/" in e

                if first not in seen:
                    seen[first] = is_dir
                    self.entry_found.emit(first, is_dir)
                elif is_dir and not seen[first]:
                    # We emitted this as a file earlier — upgrade to directory
                    seen[first] = True
                    self.entry_upgrade.emit(first)   # tell UI to add expand arrow

                count += 1
                if count % 50_000 == 0:
                    self.progress.emit(
                        f"⏳  Scanning… {len(seen)} items found ({count:,} entries read)"
                    )
        except Exception as ex:
            self.error.emit(str(ex))
            return
        finally:
            tar.stdout.close()
            try: tar.kill(); tar.wait()
            except Exception: pass
            try: decomp.kill(); decomp.wait()
            except Exception: pass

        if not seen:
            self.error.emit("Archive appears empty or could not be read.")
            return

        self.top_done.emit(len(seen))

        # ── Phase 2: build index only if expand was already requested ─────────
        if self._build_idx and not self._stop:
            self._do_build_index()

    def _do_build_index(self):
        db = _db_path_for(self.archive)
        os.makedirs(_IDX_DIR, exist_ok=True)
        if self._db_valid(db):
            self.index_ready.emit(db)
            return
        self.progress.emit("⏳  Building subfolder index…")
        try:
            self._build_db(db)
            if not self._stop:
                self.index_ready.emit(db)
        except Exception as e:
            self.progress.emit(f"⚠  Index failed: {e}")

    def _build_db(self, db):
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE IF NOT EXISTS entries(path TEXT PRIMARY KEY, is_dir INTEGER, parent TEXT NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_parent ON entries(parent)")
        con.execute("DELETE FROM entries")
        seen = set(); batch = []; count = 0

        def flush():
            con.executemany("INSERT OR IGNORE INTO entries VALUES(?,?,?)", batch)
            batch.clear()

        decomp, tar = _open_tar_list(self.archive)
        try:
            for raw in tar.stdout:
                if self._stop: break
                path = _norm(raw)
                if not path or path == ".": continue
                parts = path.split("/")
                for d in range(1, len(parts) + 1):
                    anc = "/".join(parts[:d])
                    if anc in seen: continue
                    seen.add(anc)
                    parent = "/".join(parts[:d-1])
                    batch.append((anc, 1 if d < len(parts) else 0, parent))
                count += 1
                if len(batch) >= 20_000:
                    flush()
                    if count % 200_000 == 0:
                        self.progress.emit(f"⏳  Indexing… {count:,} paths")
        finally:
            tar.stdout.close()
            try: tar.kill(); tar.wait()
            except Exception: pass
            try: decomp.kill(); decomp.wait()
            except Exception: pass

        flush(); con.commit(); con.close()

    def _db_valid(self, db):
        if not os.path.exists(db): return False
        try:
            con = sqlite3.connect(db)
            n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            con.close()
            return n > 0
        except Exception: return False


class _QueryWorker(QThread):
    """Query the SQLite archive index for children of a given parent path.

    Signals:
        done(parent_item, list_of_(name, is_dir))
        error(parent_item, message)
    """
    done  = pyqtSignal(object, list)   # (QTreeWidgetItem | None, [(name, is_dir)])
    error = pyqtSignal(object, str)    # (QTreeWidgetItem | None, msg)

    def __init__(self, db_path: str, parent_path: str, parent_item):
        super().__init__()
        self.db_path     = db_path
        self.parent_path = parent_path
        self.parent_item = parent_item

    def run(self):
        import sqlite3 as _sql, os as _os
        try:
            con = _sql.connect(self.db_path, timeout=10)
            con.row_factory = _sql.Row
            # The DB stores full relative paths; parent column holds the parent dir.
            # For the root level parent is "" or "."; normalise both.
            p = self.parent_path.lstrip("./").rstrip("/")
            rows = con.execute(
                "SELECT path, is_dir FROM entries WHERE parent = ? ORDER BY is_dir DESC, path ASC",
                (p,)
            ).fetchall()
            con.close()
            children = [(_os.path.basename(r["path"]) or r["path"], bool(r["is_dir"])) for r in rows]
            self.done.emit(self.parent_item, children)
        except Exception as exc:
            self.error.emit(self.parent_item, str(exc))


class _IndexBuildThread(QThread):
    """Builds the SQLite index standalone (used when Phase 1 already finished)."""
    progress = pyqtSignal(str)
    ready    = pyqtSignal(str)

    def __init__(self, archive):
        super().__init__()
        self.archive = archive

    def run(self):
        db = _db_path_for(self.archive)
        os.makedirs(_IDX_DIR, exist_ok=True)
        if os.path.exists(db):
            try:
                con = sqlite3.connect(db)
                n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                con.close()
                if n > 0:
                    self.ready.emit(db)
                    return
            except Exception: pass
        self.progress.emit("⏳  Building subfolder index…")
        # reuse _ArchiveIndexer._build_db via composition
        tmp = _ArchiveIndexer(self.archive)
        try:
            tmp._build_db(db)
            self.ready.emit(db)
        except Exception as e:
            self.progress.emit(f"⚠  Index failed: {e}")


class _UnmountWorker(QThread):
    """Run umount/fusermount off the main thread."""
    done = pyqtSignal()

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        subprocess.run(self.cmd, shell=True)
        self.done.emit()

VERSION = "v5.0.0-beta"

_SRC_ACTIVE   = ("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                 "stop:0 #1d4ed8,stop:1 #3b82f6); color:#fff; "
                 "font-weight:700; font-size:12px; padding:8px 18px; border-radius:8px; border:none;")
_SRC_INACTIVE = ("background:transparent; color:#64748b; font-weight:600; "
                 "font-size:12px; padding:8px 18px; border-radius:8px; "
                 "border:1px solid rgba(100,116,139,0.3);")

_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")
LABEL_STYLE = ("background:transparent; border:none; "
               "font-weight:600; font-size:12px; color:#64748b;")
HINT_STYLE  = "background:transparent; border:none; color:#64748b; font-size:11px;"


def _hsep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f


class _NetFilesProxy:
    """Duck-type stand-in for the removed QGroupBox('Backups Found on Share').
    hide()/show() delegate to both the label and list widget."""
    def __init__(self, label, listwidget):
        self._lbl = label
        self._lst = listwidget
    def hide(self):
        self._lbl.hide(); self._lst.hide()
    def show(self):
        self._lbl.show(); self._lst.show()


class RestoreMixin:
    # ─────────────────────────────────────────────────────────────────────────
    # PAGE BUILD
    # ─────────────────────────────────────────────────────────────────────────
    def build_restore_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        root = QVBoxLayout(page)
        root.setSpacing(18)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Page header ───────────────────────────────────────────────────────
        root.addWidget(mk_page_title(
            "Execute Restore",
            "Select a source, configure the destination, and stream a restore"))

        root.addWidget(_hsep())

        # ── SOURCE TYPE pill switcher ──────────────────────────────────────────
        src_lbl = QLabel("RESTORE SOURCE")
        src_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(src_lbl)

        pill_wrap = QWidget()
        pill_wrap.setStyleSheet("background:transparent;")
        pill_lay = QHBoxLayout(pill_wrap)
        pill_lay.setContentsMargins(0, 0, 0, 0)
        pill_lay.setSpacing(6)
        self._btn_src_local   = QPushButton("📁  Local File")
        self._btn_src_network = QPushButton("🌐  Network / SFTP")
        self._btn_src_btrfs   = QPushButton("⬡  Btrfs Snapshot")
        self._btn_src_local.setStyleSheet(_SRC_ACTIVE)
        self._btn_src_network.setStyleSheet(_SRC_INACTIVE)
        self._btn_src_btrfs.setStyleSheet(_SRC_INACTIVE)
        self._btn_src_local.clicked.connect(lambda: self._set_restore_source_type("local"))
        self._btn_src_network.clicked.connect(lambda: self._set_restore_source_type("network"))
        self._btn_src_btrfs.clicked.connect(lambda: self._set_restore_source_type("btrfs"))
        pill_lay.addWidget(self._btn_src_local)
        pill_lay.addWidget(self._btn_src_network)
        pill_lay.addWidget(self._btn_src_btrfs)
        pill_lay.addStretch()
        root.addWidget(pill_wrap)

        root.addWidget(_hsep())

        # ── LOCAL FILE PANEL ──────────────────────────────────────────────────
        self._rst_local_panel = QWidget()
        self._rst_local_panel.setStyleSheet("background:transparent;")
        local_lay = QHBoxLayout(self._rst_local_panel)
        local_lay.setContentsMargins(0, 0, 0, 0)
        local_lay.setSpacing(10)
        _lbl = QLabel("BACKUP FILE")
        _lbl.setStyleSheet(_SEC_LABEL)
        _lbl.setFixedWidth(130)
        self.rst_source = QLineEdit()
        self.rst_source.setPlaceholderText("Select a .tar.gz, .btrfs, or .img.gz file…")
        self.rst_source.setReadOnly(True)
        btn_browse = QPushButton("Browse")
        btn_browse.setStyleSheet(BTN_SECONDARY)
        btn_browse.clicked.connect(self.select_restore_source)
        local_lay.addWidget(_lbl)
        local_lay.addWidget(self.rst_source)
        local_lay.addWidget(btn_browse)
        root.addWidget(self._rst_local_panel)

        # ── NETWORK SOURCE PANEL ───────────────────────────────────────────────
        self._rst_net_panel = QWidget()
        self._rst_net_panel.setStyleSheet("background:transparent;")
        self._rst_net_panel.hide()
        net_lay = QVBoxLayout(self._rst_net_panel)
        net_lay.setContentsMargins(0, 0, 0, 0)
        net_lay.setSpacing(10)

        net_top = QHBoxLayout()
        _nl = QLabel("PROFILE")
        _nl.setStyleSheet(_SEC_LABEL)
        _nl.setFixedWidth(130)
        self.rst_net_profile_combo = QComboBox()
        self.rst_net_profile_combo.setMinimumWidth(240)
        btn_mount_scan = QPushButton("⬆  Mount Share")
        btn_mount_scan.setStyleSheet(BTN_PRIMARY)
        btn_mount_scan.clicked.connect(self._mount_and_scan_for_backups)
        self._btn_mount = btn_mount_scan
        self._btn_rst_unmount = QPushButton("✕  Unmount")
        self._btn_rst_unmount.setStyleSheet(BTN_DANGER)
        self._btn_rst_unmount.hide()
        self._btn_rst_unmount.clicked.connect(self._unmount_restore_share)
        net_top.addWidget(_nl)
        net_top.addWidget(self.rst_net_profile_combo)
        net_top.addWidget(btn_mount_scan)
        net_top.addWidget(self._btn_rst_unmount)
        net_top.addStretch()
        net_lay.addLayout(net_top)

        self._rst_mount_status = QLabel("")
        self._rst_mount_status.setStyleSheet(
            "background:transparent; border:none; color:#64748b; font-size:12px;")
        net_lay.addWidget(self._rst_mount_status)

        # Net files list (label + list, no QGroupBox)
        _nf_lbl = QLabel("BACKUPS FOUND ON SHARE")
        _nf_lbl.setStyleSheet(_SEC_LABEL)
        _nf_lbl.hide()
        net_lay.addWidget(_nf_lbl)
        self._rst_net_files_lbl = _nf_lbl

        self._rst_net_files_list = QListWidget()
        self._rst_net_files_list.setMaximumHeight(160)
        self._rst_net_files_list.setStyleSheet("""
            QListWidget {
                background:transparent; border:1px solid rgba(100,116,139,0.25);
                border-radius:6px; outline:0;
            }
            QListWidget::item { padding:8px 12px; border-radius:4px; }
            QListWidget::item:hover { background:rgba(59,130,246,0.08); }
            QListWidget::item:selected { background:rgba(59,130,246,0.15); color:#3b82f6; }
        """)
        self._rst_net_files_list.currentItemChanged.connect(self._on_net_backup_selected)
        self._rst_net_files_list.hide()
        net_lay.addWidget(self._rst_net_files_list)
        # Wrap list + label so hide/show works as a unit
        self._rst_net_files_group = _NetFilesProxy(self._rst_net_files_lbl, self._rst_net_files_list)

        net_sel = QHBoxLayout()
        _nsl = QLabel("SELECTED")
        _nsl.setStyleSheet(_SEC_LABEL)
        _nsl.setFixedWidth(130)
        self._rst_net_selected = QLineEdit()
        self._rst_net_selected.setReadOnly(True)
        self._rst_net_selected.setPlaceholderText("Select a backup from the list above…")
        net_sel.addWidget(_nsl)
        net_sel.addWidget(self._rst_net_selected)
        net_lay.addLayout(net_sel)
        root.addWidget(self._rst_net_panel)

        # ── BTRFS SNAPSHOT PANEL ───────────────────────────────────────────────
        self._rst_btrfs_panel = QWidget()
        self._rst_btrfs_panel.setStyleSheet("background:transparent;")
        self._rst_btrfs_panel.hide()
        btrfs_lay = QVBoxLayout(self._rst_btrfs_panel)
        btrfs_lay.setContentsMargins(0, 0, 0, 0)
        btrfs_lay.setSpacing(12)

        btrfs_top = QHBoxLayout()
        _btl = QLabel("LIVE BTRFS SNAPSHOTS")
        _btl.setStyleSheet(_SEC_LABEL)
        _btl.setFixedWidth(170)
        self._btn_btrfs_scan = QPushButton("⟳  Scan")
        self._btn_btrfs_scan.setStyleSheet(BTN_SECONDARY)
        self._btn_btrfs_scan.clicked.connect(self._btrfs_rst_scan)
        self._rst_btrfs_status = QLabel("Click Scan to enumerate Btrfs snapshots.")
        self._rst_btrfs_status.setStyleSheet(
            "color:#64748b; font-size:12px; background:transparent; border:none;")
        btrfs_top.addWidget(_btl)
        btrfs_top.addWidget(self._btn_btrfs_scan)
        btrfs_top.addWidget(self._rst_btrfs_status, 1)
        btrfs_lay.addLayout(btrfs_top)

        self._rst_btrfs_list = QListWidget()
        self._rst_btrfs_list.setMaximumHeight(160)
        self._rst_btrfs_list.setStyleSheet("""
            QListWidget {
                background:transparent; border:1px solid rgba(100,116,139,0.25);
                border-radius:6px; outline:0;
            }
            QListWidget::item { padding:8px 12px; border-radius:4px; }
            QListWidget::item:hover { background:rgba(163,230,53,0.08); }
            QListWidget::item:selected { background:rgba(163,230,53,0.15); color:#65a30d; }
        """)
        self._rst_btrfs_list.currentItemChanged.connect(self._on_btrfs_snap_selected)
        btrfs_lay.addWidget(self._rst_btrfs_list)

        btrfs_sel = QHBoxLayout()
        _bsl = QLabel("SELECTED")
        _bsl.setStyleSheet(_SEC_LABEL)
        _bsl.setFixedWidth(130)
        self._rst_btrfs_selected = QLineEdit()
        self._rst_btrfs_selected.setReadOnly(True)
        self._rst_btrfs_selected.setPlaceholderText("Select a snapshot from the list above…")
        btrfs_sel.addWidget(_bsl)
        btrfs_sel.addWidget(self._rst_btrfs_selected)
        btrfs_lay.addLayout(btrfs_sel)

        btrfs_dest_row = QHBoxLayout()
        _bdl = QLabel("DESTINATION")
        _bdl.setStyleSheet(_SEC_LABEL)
        _bdl.setFixedWidth(130)
        self._rst_btrfs_dest = QLineEdit("/")
        self._rst_btrfs_dest.setPlaceholderText("/mnt/restore_target  (must be a Btrfs filesystem)")
        btn_btrfs_dest = QPushButton("Browse")
        btn_btrfs_dest.setStyleSheet(BTN_SECONDARY)
        btn_btrfs_dest.clicked.connect(lambda: self.open_safe_folder_dialog(self._rst_btrfs_dest))
        btrfs_dest_row.addWidget(_bdl)
        btrfs_dest_row.addWidget(self._rst_btrfs_dest)
        btrfs_dest_row.addWidget(btn_btrfs_dest)
        btrfs_lay.addLayout(btrfs_dest_row)

        btrfs_hint = QLabel(
            "ℹ  Uses btrfs send | btrfs receive — destination must be a mounted Btrfs filesystem.")
        btrfs_hint.setWordWrap(True)
        btrfs_hint.setStyleSheet("color:#64748b; font-size:11px; background:transparent; border:none;")
        btrfs_lay.addWidget(btrfs_hint)
        root.addWidget(self._rst_btrfs_panel)
        self._rst_btrfs_worker = None

        root.addWidget(_hsep())

        # ── AUTO-DETECTED ENGINE ──────────────────────────────────────────────
        fs_row = QHBoxLayout()
        _fl = QLabel("DETECTED ENGINE")
        _fl.setStyleSheet(_SEC_LABEL)
        _fl.setFixedWidth(130)
        self.rst_engine = QComboBox()
        self.rst_engine.addItems([
            "Ext4 / Universal (.tar.gz)",
            "Btrfs Native (.btrfs)",
            "Bare Metal Image (.img.gz)"
        ])
        self.rst_engine.setEnabled(False)
        fs_row.addWidget(_fl)
        fs_row.addWidget(self.rst_engine)
        fs_row.addStretch()
        root.addLayout(fs_row)

        root.addWidget(_hsep())

        # ── RESTORE MODE ──────────────────────────────────────────────────────
        mode_lbl = QLabel("RESTORE MODE")
        mode_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(mode_lbl)

        toggle_col = QVBoxLayout()
        toggle_col.setSpacing(10)
        toggle_col.setContentsMargins(0, 0, 0, 0)

        def _toggle_cell(toggle, label, hint):
            cell = QHBoxLayout()
            cell.setSpacing(10)
            txt = QVBoxLayout()
            txt.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-weight:700; font-size:13px; background:transparent; border:none;")
            hnt = QLabel(hint)
            hnt.setStyleSheet("color:#64748b; font-size:11px; background:transparent; border:none;")
            txt.addWidget(lbl)
            txt.addWidget(hnt)
            cell.addWidget(toggle)
            cell.addLayout(txt)
            cell.addStretch()
            w = QWidget()
            w.setStyleSheet("background:transparent;")
            w.setLayout(cell)
            return w

        self.rst_toggle_full = ToggleSwitch()
        self.rst_toggle_full.setChecked(True)
        self.rst_toggle_selective = ToggleSwitch()
        self.rst_toggle_selective.setChecked(False)

        self.rst_selected_label = QLabel("Items selected: 0")
        self.rst_selected_label.setStyleSheet(
            "color:#10b981; font-weight:700; font-size:12px; background:transparent; border:none;")
        self.rst_selected_label.hide()

        toggle_col.addWidget(_toggle_cell(self.rst_toggle_full, "Full Restore", "Extract every file from the backup archive"))
        toggle_col.addWidget(_toggle_cell(self.rst_toggle_selective, "Selective Restore", "Pick individual files and directories to extract"))
        root.addLayout(toggle_col)
        root.addWidget(self.rst_selected_label)

        self.rst_toggle_full.toggled.connect(self._on_full_toggled)
        self.rst_toggle_selective.toggled.connect(self._on_selective_toggled)

        root.addWidget(_hsep())

        # ── DESTINATION ───────────────────────────────────────────────────────
        dest_lbl = QLabel("DESTINATION")
        dest_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(dest_lbl)

        dest_row = QHBoxLayout()
        self.rst_dest_path = QLineEdit("/")
        dest_row.addWidget(self.rst_dest_path)
        self.rst_dest_btn = QPushButton("Browse")
        self.rst_dest_btn.setStyleSheet(BTN_SECONDARY)
        self.rst_dest_btn.clicked.connect(
            lambda _, w=self.rst_dest_path: self.open_safe_folder_dialog(w))
        dest_row.addWidget(self.rst_dest_btn)
        self.rst_dest_drive = QComboBox()
        self.rst_dest_drive.hide()
        dest_row.addWidget(self.rst_dest_drive)
        self.rst_refresh_drives = QPushButton("⟳  Rescan")
        self.rst_refresh_drives.setStyleSheet(BTN_SECONDARY)
        self.rst_refresh_drives.hide()
        self.rst_refresh_drives.clicked.connect(
            lambda: self.analyze_restore_file(self.rst_source.text()))
        dest_row.addWidget(self.rst_refresh_drives)
        root.addLayout(dest_row)

        self.rst_bm_warning = QLabel(
            "⚠  CRITICAL: You cannot restore a Bare Metal image to the drive currently running this OS.")
        self.rst_bm_warning.setStyleSheet(
            "color:#ef4444; font-size:11px; font-weight:600; "
            "background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.2); "
            "border-radius:6px; padding:8px 12px;")
        self.rst_bm_warning.setWordWrap(True)
        self.rst_bm_warning.hide()
        root.addWidget(self.rst_bm_warning)

        root.addWidget(_hsep())

        # ── FILE TREE (selective mode only) ───────────────────────────────────
        tree_hdr = QHBoxLayout()
        tree_hdr.setContentsMargins(0, 0, 0, 0)
        tree_title_lbl = QLabel("SELECT FILES TO RESTORE")
        tree_title_lbl.setStyleSheet(_SEC_LABEL)
        btn_sel_all = QPushButton("Select All")
        btn_sel_all.setStyleSheet(BTN_SECONDARY)
        btn_sel_all.clicked.connect(
            lambda: self.toggle_all_tree_items(self.rst_file_tree, Qt.CheckState.Checked))
        btn_desel_all = QPushButton("Deselect All")
        btn_desel_all.setStyleSheet(BTN_SECONDARY)
        btn_desel_all.clicked.connect(
            lambda: self.toggle_all_tree_items(self.rst_file_tree, Qt.CheckState.Unchecked))
        self._tree_index_status = QLabel("Indexing archive…")
        self._tree_index_status.setStyleSheet(
            "background:transparent; border:none; color:#f59e0b; font-size:11px; font-weight:700;")
        self._tree_index_status.hide()
        tree_hdr.addWidget(tree_title_lbl)
        tree_hdr.addStretch()
        tree_hdr.addWidget(self._tree_index_status)
        tree_hdr.addWidget(btn_sel_all)
        tree_hdr.addWidget(btn_desel_all)

        self._tree_index_subtitle = QLabel(
            "Reading large archives may take time depending on drive and network speed.")
        self._tree_index_subtitle.setStyleSheet(
            "background:transparent; border:none; color:#64748b; font-size:11px; font-style:italic;")
        self._tree_index_subtitle.setWordWrap(True)
        self._tree_index_subtitle.hide()

        self.rst_file_tree = QTreeWidget()
        self.rst_file_tree.setHeaderLabels(["Path", "Type"])
        self.rst_file_tree.setColumnWidth(0, 420)
        self.rst_file_tree.setMinimumHeight(260)
        self.rst_file_tree.setStyleSheet("""
            QTreeWidget {
                background:transparent; border:1px solid rgba(100,116,139,0.25);
                border-radius:6px; outline:0; padding:4px;
            }
            QTreeWidget::item { padding:4px 8px; border-radius:4px; border:none; }
            QTreeWidget::item:hover { background:rgba(59,130,246,0.08); }
            QTreeWidget::item:selected { background:transparent; }
            QTreeWidget::indicator {
                width:16px; height:16px; border-radius:3px;
                border:2px solid rgba(100,116,139,0.4); background:transparent;
            }
            QTreeWidget::indicator:hover { border-color:#3b82f6; }
            QTreeWidget::indicator:checked {
                border-color:#10b981; background:#10b981;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNIDMgOCBMIDYgMTEgTCAxMyA0IiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiIGZpbGw9Im5vbmUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg==);
            }
            QHeaderView::section {
                background:transparent; color:#64748b; font-size:10px;
                font-weight:700; letter-spacing:1px; border:none;
                border-bottom:1px solid rgba(100,116,139,0.2); padding:6px 10px;
            }
        """)
        self.rst_file_tree.itemChanged.connect(self._on_tree_item_changed)
        self.rst_file_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self._query_workers = []
        self._tree_db_path  = None

        # Wrap tree section in a container for unified show/hide
        self.rst_file_tree_group = QWidget()
        self.rst_file_tree_group.setStyleSheet("background:transparent;")
        _tg_lay = QVBoxLayout(self.rst_file_tree_group)
        _tg_lay.setContentsMargins(0, 0, 0, 0)
        _tg_lay.setSpacing(6)
        _tg_lay.addLayout(tree_hdr)
        _tg_lay.addWidget(self._tree_index_subtitle)
        _tg_lay.addWidget(self.rst_file_tree)
        self.rst_file_tree_group.hide()
        root.addWidget(self.rst_file_tree_group, stretch=1)

        # ── ACTION BUTTONS ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_run_restore = QPushButton("▼  Start Restore")
        self.btn_run_restore.setStyleSheet(BTN_SUCCESS)
        self.btn_run_restore.clicked.connect(self.start_restore_process)
        self.btn_restore_pause = QPushButton("⏸  Pause")
        self.btn_restore_pause.setStyleSheet(BTN_WARNING)
        self.btn_restore_pause.setEnabled(False)
        self.btn_restore_pause.clicked.connect(self.toggle_pause)
        self.btn_restore_stop = QPushButton("✕  Cancel")
        self.btn_restore_stop.setStyleSheet(BTN_DANGER)
        self.btn_restore_stop.setEnabled(False)
        self.btn_restore_stop.clicked.connect(self.stop_process)
        btn_row.addWidget(self.btn_run_restore)
        btn_row.addWidget(self.btn_restore_pause)
        btn_row.addWidget(self.btn_restore_stop)
        btn_row.addStretch()
        root.addLayout(btn_row)

        root.addWidget(_hsep())

        # ── STREAM PROGRESS CARD ──────────────────────────────────────────────
        rst_prog_sec = QLabel("STREAM PROGRESS")
        rst_prog_sec.setStyleSheet(_SEC_LABEL)
        root.addWidget(rst_prog_sec)

        self._restore_prog_card = QWidget()
        self._restore_prog_card.setStyleSheet(
            "background: rgba(100,116,139,0.06); "
            "border: 1px solid rgba(100,116,139,0.15); border-radius: 10px;")
        rcard_lay = QVBoxLayout(self._restore_prog_card)
        rcard_lay.setContentsMargins(16, 12, 16, 12)
        rcard_lay.setSpacing(6)

        # Status line — phase of operation
        self._restore_status_label = QLabel("Idle — no active restore")
        self._restore_status_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; background: transparent; border: none;")
        rcard_lay.addWidget(self._restore_status_label)

        # Progress bar
        self._restore_progress_bar = QProgressBar()
        self._restore_progress_bar.setRange(0, 1)
        self._restore_progress_bar.setValue(0)
        self._restore_progress_bar.setTextVisible(False)
        self._restore_progress_bar.setFixedHeight(12)
        self._restore_progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(100,116,139,0.18);
                border-radius: 6px; border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #f59e0b, stop:1 #fbbf24);
                border-radius: 6px;
            }
        """)
        rcard_lay.addWidget(self._restore_progress_bar)

        # Stats line
        self._restore_stats_label = QLabel("")
        self._restore_stats_label.setStyleSheet(
            "font-size: 12px; color: #64748b; background: transparent; border: none; "
            "font-family: 'Segoe UI', system-ui, monospace;")
        rcard_lay.addWidget(self._restore_stats_label)

        # Directory / path line
        self._restore_dir_label = QLabel("")
        self._restore_dir_label.setStyleSheet(
            "font-size: 12px; color: #64748b; background: transparent; border: none;")
        self._restore_dir_label.setWordWrap(True)
        rcard_lay.addWidget(self._restore_dir_label)

        # Size info line
        self._restore_size_label = QLabel("")
        self._restore_size_label.setStyleSheet(
            "font-size: 11px; color: #64748b; background: transparent; border: none;")
        rcard_lay.addWidget(self._restore_size_label)

        root.addWidget(self._restore_prog_card)

        root.addStretch(1)

        return page
    # ─────────────────────────────────────────────────────────────────────────
    # TOGGLE INTERLOCK
    # ─────────────────────────────────────────────────────────────────────────
    def _on_full_toggled(self, checked: bool):
        if checked:
            # Turn selective OFF without re-emitting its signal
            self.rst_toggle_selective.blockSignals(True)
            self.rst_toggle_selective.setChecked(False)
            self.rst_toggle_selective.blockSignals(False)
            self.rst_file_tree_group.hide()
            self.rst_selected_label.hide()
        else:
            # Don't allow both OFF — force selective ON
            self.rst_toggle_selective.blockSignals(True)
            self.rst_toggle_selective.setChecked(True)
            self.rst_toggle_selective.blockSignals(False)
            self._show_tree_if_ready()

    def _on_selective_toggled(self, checked: bool):
        if checked:
            self.rst_toggle_full.blockSignals(True)
            self.rst_toggle_full.setChecked(False)
            self.rst_toggle_full.blockSignals(False)
            self._show_tree_if_ready()
        else:
            # Don't allow both OFF — force full ON
            self.rst_toggle_full.blockSignals(True)
            self.rst_toggle_full.setChecked(True)
            self.rst_toggle_full.blockSignals(False)
            self.rst_file_tree_group.hide()
            self.rst_selected_label.hide()

    def _show_tree_if_ready(self):
        if self.rst_source.text().strip() and self.rst_file_tree.topLevelItemCount() > 0:
            self.rst_file_tree_group.show()
            self.rst_selected_label.show()
        else:
            self.rst_file_tree_group.hide()

    def _reset_restore_progress_card(self):
        """Reset the restore progress card to idle state."""
        if hasattr(self, '_restore_status_label'):
            self._restore_status_label.setText("Idle — no active restore")
        if hasattr(self, '_restore_progress_bar'):
            self._restore_progress_bar.setRange(0, 1)
            self._restore_progress_bar.setValue(0)
        if hasattr(self, '_restore_stats_label'):
            self._restore_stats_label.setText("")
        if hasattr(self, '_restore_dir_label'):
            self._restore_dir_label.setText("")
        if hasattr(self, '_restore_size_label'):
            self._restore_size_label.setText("")

    def on_restore_mode_changed(self):
        """Public alias kept for any external callers."""
        if self.rst_toggle_selective.isChecked():
            self._show_tree_if_ready()
        else:
            self.rst_file_tree_group.hide()
            self.rst_selected_label.hide()

    # ─────────────────────────────────────────────────────────────────────────
    # SOURCE TYPE SWITCHING
    # ─────────────────────────────────────────────────────────────────────────
    def _set_restore_source_type(self, mode: str):
        # Reset all pill buttons
        self._btn_src_local.setStyleSheet(_SRC_INACTIVE)
        self._btn_src_network.setStyleSheet(_SRC_INACTIVE)
        if hasattr(self, '_btn_src_btrfs'):
            self._btn_src_btrfs.setStyleSheet(_SRC_INACTIVE)
        # Hide all panels
        self._rst_local_panel.hide()
        self._rst_net_panel.hide()
        if hasattr(self, '_rst_btrfs_panel'):
            self._rst_btrfs_panel.hide()

        if mode == "local":
            self._btn_src_local.setStyleSheet(_SRC_ACTIVE)
            self._rst_local_panel.show()
            self._unmount_restore_share(silent=True)
        elif mode == "network":
            self._btn_src_network.setStyleSheet(_SRC_ACTIVE)
            self._rst_net_panel.show()
            self._populate_net_restore_profiles()
        elif mode == "btrfs":
            self._btn_src_btrfs.setStyleSheet(_SRC_ACTIVE)
            self._rst_btrfs_panel.show()
            if self._rst_btrfs_list.count() == 0:
                self._btrfs_rst_scan()

    # ── BTRFS SNAPSHOT RESTORE ────────────────────────────────────────────────
    def _btrfs_rst_scan(self):
        """Enumerate native Btrfs snapshots using btrfs subvolume list + .snapshots dirs."""
        import subprocess, shutil as _sh
        self._rst_btrfs_list.clear()
        self._rst_btrfs_selected.clear()
        self._rst_btrfs_status.setText("⏳  Scanning for Btrfs snapshots…")
        self._btn_btrfs_scan.setEnabled(False)

        if not _sh.which("btrfs"):
            self._rst_btrfs_status.setText("❌  'btrfs' not found — install btrfs-progs.")
            self._btn_btrfs_scan.setEnabled(True)
            return

        from ui_tab_snapshot_browser import _BtrfsSnapWorker
        worker = _BtrfsSnapWorker()
        worker.found.connect(self._on_btrfs_rst_found)
        worker.finished.connect(self._on_btrfs_rst_done)
        worker.error.connect(lambda msg: self._rst_btrfs_status.setText(f"⚠  {msg}"))
        self._rst_btrfs_worker = worker
        worker.start()

    def _on_btrfs_rst_found(self, row: dict):
        from PyQt6.QtWidgets import QListWidgetItem as _LWI
        from PyQt6.QtCore import Qt as _Qt
        from datetime import datetime as _dt
        label = f"[{row['profile'].strip('[]')}]  {row['filename']}  —  {row['date']}"
        item = _LWI(label)
        item.setData(_Qt.ItemDataRole.UserRole, row["full_path"])
        self._rst_btrfs_list.addItem(item)

    def _on_btrfs_rst_done(self, total: int):
        self._btn_btrfs_scan.setEnabled(True)
        self._rst_btrfs_worker = None
        if total:
            self._rst_btrfs_status.setText(
                f"✅  {total} snapshot{'s' if total != 1 else ''} found — select one then click Start Restore."
            )
        else:
            self._rst_btrfs_status.setText("No Btrfs snapshots found on this system.")

    def _on_btrfs_snap_selected(self, current, _prev):
        from PyQt6.QtCore import Qt as _Qt
        if current:
            path = current.data(_Qt.ItemDataRole.UserRole)
            self._rst_btrfs_selected.setText(path)
            # Point rst_source at this path so the engine picks it up
            self.rst_source.setText(path)
            self.rst_engine.setCurrentText("Btrfs Native (.btrfs)")

    def _populate_net_restore_profiles(self):
        """Refresh the profile combo with all network + SFTP profiles."""
        self.rst_net_profile_combo.clear()
        for name in self.profiles.get("network", {}).keys():
            self.rst_net_profile_combo.addItem(f"NETWORK: {name}", ("network", name))
        for name in self.profiles.get("sftp", {}).keys():
            self.rst_net_profile_combo.addItem(f"SFTP: {name}", ("sftp", name))
        if self.rst_net_profile_combo.count() == 0:
            self.rst_net_profile_combo.addItem("— No network/SFTP profiles saved —", None)

    def _mount_and_scan_for_backups(self):
        """Mount the selected profile's share in a background thread, then scan for backups."""
        idx  = self.rst_net_profile_combo.currentIndex()
        data = self.rst_net_profile_combo.itemData(idx)
        if not data:
            QMessageBox.warning(self, "No Profile", "Please save a Network or SFTP profile first.")
            return

        cat, name = data
        prof = self.profiles.get(cat, {}).get(name, {})

        mnt = "/tmp/archvault_rst_net"
        mount_cmd = self._build_restore_mount_cmd(cat, prof, mnt)
        if not mount_cmd:
            self._rst_mount_status.setText("❌  Unsupported profile type.")
            return

        # Disable button while working
        self._btn_mount.setEnabled(False)
        self._rst_net_files_list.clear()
        self._rst_net_files_group.hide()
        self._rst_net_selected.clear()
        self._rst_mount_status.setText("⏳  Connecting…")

        # Store name for use in success callback
        self._pending_mount_name = name
        self._pending_mount_cat  = cat

        # rsync_ssh profiles return a sentinel — use SSH ls to list remote snapshots
        if mount_cmd.startswith("__rsync_ssh__"):
            _, conn_str = mount_cmd.split("__rsync_ssh__", 1)
            worker = _SSHListWorker(conn_str, prof, self)
            worker.status.connect(self._rst_mount_status.setText)
            worker.success.connect(self._on_ssh_list_success)
            worker.error.connect(self._on_mount_scan_error)
            self._mount_worker = worker
            worker.start()
            return

        worker = _MountScanWorker(mount_cmd, mnt, cat)
        worker.status.connect(self._rst_mount_status.setText)
        worker.success.connect(self._on_mount_scan_success)
        worker.error.connect(self._on_mount_scan_error)
        # Keep reference so it isn't GC'd
        self._mount_worker = worker
        worker.start()

    def _on_ssh_list_success(self, found: list, note: str):
        """Callback when SSH directory listing completes for rsync_ssh profiles."""
        self._btn_mount.setEnabled(True)
        self._restore_mount_point = None   # no local mount point
        self._restore_mount_type  = "sftp_rsync_ssh"
        self._btn_rst_unmount.hide()       # nothing to unmount

        if not found:
            self._rst_mount_status.setText(f"⚠  {note}  — no snapshots found.")
            return

        self._rst_net_files_list.clear()
        for fname, fpath, size_mb, date_str in found:
            item = QListWidgetItem(f"{fname}  —  {size_mb:.0f} MB  —  {date_str}  [remote]")
            item.setData(Qt.ItemDataRole.UserRole, fpath)
            self._rst_net_files_list.addItem(item)

        self._rst_net_files_group.show()
        self._rst_mount_status.setText(
            f"✅  {note}  |  {len(found)} snapshot(s) found  "
            "— select one, then click Restore to pull and restore over SSH"
        )

    def _on_mount_scan_success(self, found: list, mnt: str):
        """Called on the main thread when mount+scan completes."""
        self._btn_mount.setEnabled(True)
        cat  = self._pending_mount_cat
        name = self._pending_mount_name

        self._restore_mount_point = mnt
        self._restore_mount_type  = cat
        self._btn_rst_unmount.show()

        if not found:
            self._rst_mount_status.setText(f"⚠  Share mounted at {mnt} — no backup files found.")
            return

        self._rst_net_files_list.clear()
        for fname, fpath, size_mb, date_str in found:
            item = QListWidgetItem(f"{fname}  —  {size_mb:.0f} MB  —  {date_str}")
            item.setData(Qt.ItemDataRole.UserRole, fpath)
            self._rst_net_files_list.addItem(item)

        self._rst_net_files_group.show()
        self._rst_mount_status.setText(
            f"✅  Mounted {name}  |  {len(found)} backup(s) found"
        )

    def _on_mount_scan_error(self, msg: str):
        """Called on the main thread when mount/scan fails."""
        self._btn_mount.setEnabled(True)
        self._rst_mount_status.setText(msg)

    def _build_restore_mount_cmd(self, cat: str, prof: dict, mnt: str) -> str:
        if cat == "network":
            pw  = self.decrypt_pw(prof.get("password", "")).replace("'", "'\\''")
            dom = prof.get("domain", "")
            if "SMB" in prof.get("protocol", "SMB"):
                dom_s = f",domain='{dom}'" if dom else ""
                return (
                    f"mount -t cifs -o username='{prof.get('username','')}',password='{pw}'"
                    f"{dom_s},noserverino,nocase,vers=3.0 '{prof['path']}' {mnt}"
                )
            else:
                return f"mount -t nfs '{prof['path']}' {mnt}"
        elif cat == "sftp":
            host  = prof.get("hostname", "")
            port  = prof.get("port", "22")
            user  = prof.get("username", "")
            rpath = prof.get("remote_path", "/backup").rstrip("/")
            auth  = prof.get("auth_method", "SSH Key")
            tm    = prof.get("transfer_mode", "rsync_ssh")
            opts  = f"StrictHostKeyChecking=no,port={port},reconnect,ServerAliveInterval=15,ConnectTimeout=10"

            if tm == "rsync_ssh":
                # rsync_ssh profiles are not FUSE-mountable — return sentinel for SSH listing
                return f"__rsync_ssh__{user}@{host}:{port}:{rpath}"

            if auth == "SSH Key":
                kfile = prof.get("key_file", "")
                return f"sshfs {user}@{host}:{rpath} {mnt} -o {opts},IdentityFile='{kfile}'"
            else:
                import shutil as _sh
                pw = self.decrypt_pw(prof.get("password", "")).replace("'", "\'")
                if _sh.which("sshpass"):
                    return f"sshpass -p '{pw}' sshfs {user}@{host}:{rpath} {mnt} -o {opts}"
                else:
                    return (
                        f"echo '{pw}' | sshfs {user}@{host}:{rpath} {mnt} "
                        f"-o {opts},password_stdin"
                    )
        return ""

    def _on_net_backup_selected(self, current, previous):
        if not current:
            return
        fpath = current.data(Qt.ItemDataRole.UserRole)
        self._rst_net_selected.setText(fpath)
        # Mirror into rst_source so the engine picks it up
        self.rst_source.setText(fpath)
        self.analyze_restore_file(fpath)
        # Store cleanup cmd for after restore
        mnt = getattr(self, "_restore_mount_point", None)
        cat = getattr(self, "_restore_mount_type", "network")
        if mnt:
            if cat == "sftp":
                self._rst_cleanup_cmd = f"fusermount -u {mnt} >/dev/null 2>&1 || umount -l {mnt} >/dev/null 2>&1"
            else:
                self._rst_cleanup_cmd = f"umount -l {mnt} >/dev/null 2>&1"
        else:
            self._rst_cleanup_cmd = ""

    def _unmount_restore_share(self, silent: bool = False):
        mnt = getattr(self, "_restore_mount_point", None)
        if not mnt:
            return
        cat = getattr(self, "_restore_mount_type", "network")
        cmd = (f"fusermount -u {mnt} 2>/dev/null || umount -l {mnt} 2>/dev/null; true"
               if cat == "sftp" else
               f"umount -l {mnt} 2>/dev/null; true")
        self._restore_mount_point = None
        self._rst_cleanup_cmd     = ""
        # Run umount off the main thread — it can block on stale CIFS/NFS mounts
        w = _UnmountWorker(cmd)
        self._unmount_worker = w   # prevent GC
        w.start()
        if not silent:
            self._rst_mount_status.setText("🔌  Unmounting…")
            self._btn_rst_unmount.hide()
            self._rst_net_files_list.clear()
            self._rst_net_files_group.hide()
            self._rst_net_selected.clear()
            self.rst_source.clear()
            w.done.connect(lambda: self._rst_mount_status.setText("🔌  Share unmounted."))

    # ─────────────────────────────────────────────────────────────────────────
    # FILE ANALYSIS
    # ─────────────────────────────────────────────────────────────────────────
    def select_restore_source(self):
        path = self._run_custom_file_dialog(
            "Select Backup to Restore", "/",
            "Archives (*.btrfs *.tar.gz *.img.gz);;All (*)", is_folder=False
        )
        if path:
            self.analyze_restore_file(path)

    def analyze_restore_file(self, file_path: str):
        if not file_path:
            return
        self.rst_source.setText(file_path)

        if file_path.endswith(".tar.gz"):
            self.rst_engine.setCurrentText("Ext4 / Universal (.tar.gz)")
            self.rst_dest_path.show(); self.rst_dest_btn.show()
            self.rst_dest_drive.hide(); self.rst_refresh_drives.hide()
            self.rst_bm_warning.hide()
            self.rst_toggle_selective.setChecked(False)   # reset to full
            self.rst_toggle_full.setChecked(True)
            # Enable selective, populate tree in background
            self._populate_file_tree(file_path)

        elif file_path.endswith(".btrfs"):
            self.rst_engine.setCurrentText("Btrfs Native (.btrfs)")
            self.rst_dest_path.show(); self.rst_dest_btn.show()
            self.rst_dest_drive.hide(); self.rst_refresh_drives.hide()
            self.rst_bm_warning.hide()
            self.rst_toggle_selective.setChecked(False)
            self.rst_toggle_full.setChecked(True)
            self._populate_file_tree(file_path)

        elif file_path.endswith(".img.gz"):
            self.rst_engine.setCurrentText("Bare Metal Image (.img.gz)")
            self.rst_dest_path.hide(); self.rst_dest_btn.hide()
            self.rst_dest_drive.show(); self.rst_refresh_drives.show()
            self.rst_bm_warning.show()
            # Selective not supported for bare metal
            self.rst_toggle_selective.blockSignals(True)
            self.rst_toggle_selective.setChecked(False)
            self.rst_toggle_selective.blockSignals(False)
            self.rst_toggle_selective.setEnabled(False)
            self.rst_toggle_full.blockSignals(True)
            self.rst_toggle_full.setChecked(True)
            self.rst_toggle_full.blockSignals(False)
            self.rst_file_tree_group.hide()
            if hasattr(self, 'get_drives_data'):
                self.rst_dest_drive.clear()
                for dev in self.get_drives_data():
                    size_gb = int(dev.get("size", 0)) / (1024 ** 3)
                    self.rst_dest_drive.addItem(
                        f"{dev['path']} ({size_gb:.1f} GB) — {dev.get('type', '')}"
                    )

    def populate_file_tree(self, file_path: str):
        """Public alias for external callers."""
        self._populate_file_tree(file_path)
    _DUMMY_TEXT = "__lazy_placeholder__"

    def _populate_file_tree(self, file_path: str):
        """Show top-level entries live as they stream in. Index only on first expand."""
        self.rst_file_tree.clear()
        self._query_workers  = []
        self._tree_db_path   = None
        self._expand_queue   = []
        self._index_triggered = False

        prev = getattr(self, "_indexer", None)
        if prev and prev.isRunning():
            prev.stop()

        engine = self.rst_engine.currentText()
        if "btrfs" in engine.lower():
            item = QTreeWidgetItem(["<Btrfs — tree available after extraction>", ""])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.rst_file_tree.addTopLevelItem(item)
            return
        if "tar.gz" not in engine and "tar.zst" not in engine:
            return

        self.rst_file_tree_group.show()
        self.rst_selected_label.show()
        self._tree_index_status.setStyleSheet(
            "background: transparent; border: none; color: #f59e0b; font-size: 12px; font-weight: bold;"
        )
        self._tree_index_status.setText("⏳  Reading archive…")
        self._tree_index_status.show()
        self._tree_index_subtitle.show()

        indexer = _ArchiveIndexer(file_path)
        indexer.entry_found.connect(self._on_entry_found)
        indexer.entry_upgrade.connect(self._on_entry_upgrade)
        indexer.top_done.connect(self._on_top_done)
        indexer.index_ready.connect(self._on_index_ready)
        indexer.progress.connect(self._tree_index_status.setText)
        indexer.error.connect(self._on_file_tree_error)
        self._indexer = indexer
        indexer.start()

    def _on_entry_found(self, name: str, is_dir: bool):
        """Add one top-level row the instant the worker finds it."""
        self.rst_file_tree.blockSignals(True)
        item = QTreeWidgetItem([name, "Directory" if is_dir else "File"])
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setData(0, Qt.ItemDataRole.UserRole, name)
        if is_dir:
            dummy = QTreeWidgetItem([self._DUMMY_TEXT, ""])
            dummy.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.addChild(dummy)
        self.rst_file_tree.addTopLevelItem(item)
        self.rst_file_tree.blockSignals(False)
        self._update_selected_count()

    def _on_entry_upgrade(self, name: str):
        """A top-level item was emitted as File but is actually a Directory — fix it."""
        root = self.rst_file_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0) == name:
                self.rst_file_tree.blockSignals(True)
                item.setText(1, "Directory")
                if item.childCount() == 0:
                    dummy = QTreeWidgetItem([self._DUMMY_TEXT, ""])
                    dummy.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.addChild(dummy)
                self.rst_file_tree.blockSignals(False)
                break

    def _on_top_done(self, total: int):
        """Top-level scan complete — update status, index builds on first expand."""
        self._tree_index_subtitle.hide()
        self._tree_index_status.setStyleSheet(
            "background: transparent; border: none; color: #10b981; font-size: 12px; font-weight: bold;"
        )
        self._tree_index_status.setText(
            f"✅  {total} item{'s' if total!=1 else ''} — click a folder to expand"
        )

    def _on_index_ready(self, db_path: str):
        """SQLite index ready — flush any queued expand requests."""
        self._tree_db_path = db_path
        for queued_item in self._expand_queue:
            self._run_expand_query(queued_item)
        self._expand_queue = []

    def _on_query_done(self, parent_item, children: list):
        """Add children to tree (top-level when parent_item is None)."""
        self.rst_file_tree.blockSignals(True)

        if parent_item is None:
            # Top-level population
            self.rst_file_tree.clear()
            for name, is_dir in children:
                item = QTreeWidgetItem([name, "Directory" if is_dir else "File"])
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                if is_dir:
                    dummy = QTreeWidgetItem([self._DUMMY_TEXT, ""])
                    dummy.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.addChild(dummy)
                self.rst_file_tree.addTopLevelItem(item)
            n = len(children)
            self._tree_index_status.setText(
                f"✅  {n} top-level item{'s' if n != 1 else ''} — click a folder to expand"
            )
        else:
            # Subfolder expansion — remove placeholder, add children
            for i in range(parent_item.childCount() - 1, -1, -1):
                c = parent_item.child(i)
                if c.text(0) in ("⏳  Loading…", self._DUMMY_TEXT):
                    parent_item.removeChild(c)
            parent_path = parent_item.data(0, Qt.ItemDataRole.UserRole) or parent_item.text(0)
            for name, is_dir in children:
                full_path = f"{parent_path}/{name}"
                child = QTreeWidgetItem([name, "Directory" if is_dir else "File"])
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, Qt.ItemDataRole.UserRole, full_path)
                if is_dir:
                    dummy = QTreeWidgetItem([self._DUMMY_TEXT, ""])
                    dummy.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    child.addChild(dummy)
                parent_item.addChild(child)

        self.rst_file_tree.blockSignals(False)
        self._update_selected_count()
        self._query_workers = [w for w in self._query_workers if w.isRunning()]

    def _on_tree_item_expanded(self, item: QTreeWidgetItem):
        """User clicked ▶ — trigger index build on first expand, then query."""
        if item.childCount() != 1:
            return
        dummy = item.child(0)
        if not dummy or dummy.text(0) != self._DUMMY_TEXT:
            return

        # Replace dummy with loading placeholder
        self.rst_file_tree.blockSignals(True)
        item.removeChild(dummy)
        ph = QTreeWidgetItem(["⏳  Loading…", ""])
        ph.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.addChild(ph)
        self.rst_file_tree.blockSignals(False)

        # First expand ever — kick off index build
        if not getattr(self, "_index_triggered", False):
            self._index_triggered = True
            self._tree_index_status.setStyleSheet(
                "background: transparent; border: none; color: #f59e0b; font-size: 12px; font-weight: bold;"
            )
            self._tree_index_status.setText("⏳  Building subfolder index…")
            indexer = getattr(self, "_indexer", None)
            if indexer:
                indexer.request_index()

        db = getattr(self, "_tree_db_path", None)
        if db:
            self._run_expand_query(item)
        else:
            if not hasattr(self, "_expand_queue"):
                self._expand_queue = []
            self._expand_queue.append(item)

    def _run_expand_query(self, item: QTreeWidgetItem):
        """Fire a _QueryWorker for the given tree item using the cached DB."""
        db = getattr(self, "_tree_db_path", None)
        if not db:
            return
        parent_path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        worker = _QueryWorker(db, parent_path, item)
        worker.done.connect(self._on_query_done)
        worker.error.connect(lambda it, msg: self._on_folder_expand_error(it))
        if not hasattr(self, "_query_workers"):
            self._query_workers = []
        self._query_workers.append(worker)
        worker.start()

    def _on_folder_expand_error(self, parent_item: QTreeWidgetItem):
        self.rst_file_tree.blockSignals(True)
        for i in range(parent_item.childCount() - 1, -1, -1):
            parent_item.removeChild(parent_item.child(i))
        err = QTreeWidgetItem(["⚠  Could not load contents", ""])
        err.setFlags(Qt.ItemFlag.ItemIsEnabled)
        parent_item.addChild(err)
        self.rst_file_tree.blockSignals(False)
        self._query_workers = [w for w in self._query_workers if w.isRunning()]

    def _on_file_tree_error(self, msg: str):
        self._tree_index_subtitle.hide()
        self.rst_file_tree.clear()
        item = QTreeWidgetItem([f"⚠  {msg}", ""])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.rst_file_tree.addTopLevelItem(item)
        self._tree_index_status.setText(f"⚠  {msg}")
        self._tree_index_status.setStyleSheet(
            "background: transparent; border: none; color: #ef4444; font-size: 12px; font-weight: bold;"
        )

    def populate_file_tree(self, file_path: str):
        """Public alias for external callers."""
        self._populate_file_tree(file_path)

    # ─────────────────────────────────────────────────────────────────────────
    # TREE HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _on_tree_item_changed(self, item, column):
        """Cascade check state down to all children, and update parent state upward."""
        if column != 0:
            return
        self.rst_file_tree.blockSignals(True)
        state = item.checkState(0)
        # ── Propagate DOWN to all descendants ──────────────────────────────
        def _set_children(node, s):
            for i in range(node.childCount()):
                child = node.child(i)
                child.setCheckState(0, s)
                _set_children(child, s)
        _set_children(item, state)
        # ── Propagate UP — set parent to partial / checked / unchecked ─────
        def _update_parent(node):
            parent = node.parent()
            if parent is None:
                return
            total    = parent.childCount()
            checked  = sum(1 for i in range(total) if parent.child(i).checkState(0) == Qt.CheckState.Checked)
            partial  = sum(1 for i in range(total) if parent.child(i).checkState(0) == Qt.CheckState.PartiallyChecked)
            if checked == total:
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif checked == 0 and partial == 0:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            _update_parent(parent)
        _update_parent(item)
        self.rst_file_tree.blockSignals(False)
        self._update_selected_count()

    def toggle_all_tree_items(self, tree, state):
        tree.blockSignals(True)
        def _recurse(item):
            item.setCheckState(0, state)
            for i in range(item.childCount()):
                _recurse(item.child(i))
        for i in range(tree.topLevelItemCount()):
            _recurse(tree.topLevelItem(i))
        tree.blockSignals(False)
        self._update_selected_count()

    def _update_selected_count(self):
        if not hasattr(self, "rst_file_tree") or not hasattr(self, "rst_selected_label"):
            return
        count = 0
        def _count(item):
            nonlocal count
            if item.checkState(0) == Qt.CheckState.Checked:
                count += 1
            for i in range(item.childCount()):
                _count(item.child(i))
        for i in range(self.rst_file_tree.topLevelItemCount()):
            _count(self.rst_file_tree.topLevelItem(i))
        self.rst_selected_label.setText(f"Items selected: {count}")

    def update_selected_count(self):
        """Public alias."""
        self._update_selected_count()

    # ─────────────────────────────────────────────────────────────────────────
    # MISC
    # ─────────────────────────────────────────────────────────────────────────
    def copy_restore_logs(self):
        QApplication.clipboard().setText(self.console.toPlainText())
        QMessageBox.information(self, "Copied", "Activity logs copied to clipboard.")

    def browse_validation_file(self):
        path = self._run_custom_file_dialog(
            "Select Backup", "/",
            "Archives (*.btrfs *.tar.gz *.img.gz);;All (*)", is_folder=False
        )
        if path and hasattr(self, "val_path_input"):
            self.val_path_input.setText(path)

    def validate_net_path(self, text, proto_combo, err_lbl):
        proto = proto_combo.currentText()
        if not text:
            err_lbl.hide()
            return
        if "SMB" in proto and not (text.startswith("//") or text.startswith("\\\\")):
            err_lbl.setText("Warning: SMB paths usually start with // or \\\\")
            err_lbl.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: bold; background-color: transparent;"
            )
            err_lbl.show()
        elif "NFS" in proto and ":/" not in text:
            err_lbl.setText("Warning: NFS paths usually require ':/' (e.g. IP:/share)")
            err_lbl.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: bold; background-color: transparent;"
            )
            err_lbl.show()
        else:
            err_lbl.hide()
