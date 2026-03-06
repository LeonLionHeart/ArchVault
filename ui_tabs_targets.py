import subprocess
import json
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QComboBox, QMessageBox,
                             QGroupBox, QListWidget, QTabWidget, QListWidgetItem,
                             QSpinBox, QScrollArea, QFrame, QSizePolicy)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
from ui_widgets import ToggleSwitch, confirm_action
from soft_ui_components import (BTN_PRIMARY, BTN_SUCCESS, BTN_DANGER,
                                 BTN_WARNING, BTN_SECONDARY, BTN_INFO,
                                 mk_page_title)

VERSION = "v5.0.3-beta"

# ── Shared micro-styles ──────────────────────────────────────────────────────
_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")
_FIELD_LBL = ("font-size:12px; font-weight:600; "
              "background:transparent; border:none;")
_NOTE_STYLE = "color:#64748b; font-size:11px; background:transparent; border:none;"
_ERR_STYLE  = "color:#ef4444; font-weight:bold; background:transparent; border:none;"
_EDIT_BADGE = ("font-size:13px; font-weight:700; color:#0ea5e9; "
               "background:transparent; border:none;")

ENDPOINT_PROVIDERS = {"Wasabi", "Generic S3", "Backblaze B2"}
REGION_PROVIDERS   = {"AWS S3", "Google Cloud Storage"}
GCS_PROVIDERS      = {"Google Cloud Storage"}


def _hsep():
    """Thin horizontal separator matching backup/restore tabs."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f


def _sec_label(text):
    """Section header label — uppercase, small, muted."""
    lbl = QLabel(text)
    lbl.setStyleSheet(_SEC_LABEL)
    return lbl


def _field_row(label_text, widget, extra_widgets=None, label_width=None):
    """Standard form row: fixed-width label + expanding widget."""
    row = QHBoxLayout()
    row.setSpacing(10)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(_FIELD_LBL)
    if label_width:
        lbl.setFixedWidth(label_width)
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    if extra_widgets:
        for w in extra_widgets:
            row.addWidget(w)
    return row


def _update_cloud_fields(provider, region_w, endpoint_w, key_lbl, key_f, secret_lbl, secret_f):
    region_w.setVisible(provider in REGION_PROVIDERS)
    endpoint_w.setVisible(provider in ENDPOINT_PROVIDERS)
    if provider in GCS_PROVIDERS:
        key_lbl.setText("Service Account JSON Path:")
        key_f.setPlaceholderText("/path/to/service-account.json")
        secret_lbl.hide(); secret_f.hide()
    else:
        key_lbl.setText("Access Key:")
        key_f.setPlaceholderText("")
        secret_lbl.show(); secret_f.show()


class UITabsTargetsMixin:

    # ─────────────────────────────────────────────────────────────────────
    # DRIVE HELPERS
    # ─────────────────────────────────────────────────────────────────────
    def get_drives_data(self):
        try:
            out = subprocess.check_output(
                ["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,MOUNTPOINT,SIZE", "-e", "7"]).decode()
            return json.loads(out).get("blockdevices", [])
        except Exception as e:
            self.log(f"SYS Error reading drives: {e}"); return []

    def populate_all_partitions(self, part_list):
        part_list.clear()
        for dev in self.get_drives_data():
            if dev.get("type") == "disk":
                for child in dev.get("children", []):
                    mount   = child.get("mountpoint") or "Unmounted"
                    size_gb = int(child.get("size", 0)) / (1024 ** 3)
                    item    = QListWidgetItem(f"{child['path']} - {mount} ({size_gb:.1f} GB)")
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    item.setData(Qt.ItemDataRole.UserRole, child)
                    part_list.addItem(item)

    def populate_mounted_drives(self, combo):
        combo.clear()
        mounts = []
        for dev in self.get_drives_data():
            if dev.get("mountpoint"):
                mounts.append((dev["mountpoint"], dev.get("size", 0), dev["path"]))
            for child in dev.get("children", []):
                if child.get("mountpoint"):
                    mounts.append((child["mountpoint"], child.get("size", 0), child["path"]))
        for mount, size, path in sorted(mounts):
            combo.addItem(f"{mount}  ({int(size)/(1024**3):.1f} GB)  [{path}]", mount)

    # ─────────────────────────────────────────────────────────────────────
    # REUSABLE SECTION BUILDERS  (no QGroupBox — section label + fields)
    # ─────────────────────────────────────────────────────────────────────
    def build_source_group(self, prefix):
        """Backup Source section — dropdown drives or custom path."""
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        sl = QVBoxLayout(container)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)

        sl.addWidget(_hsep())
        sl.addWidget(_sec_label("BACKUP SOURCE"))

        mode_combo = QComboBox()
        mode_combo.addItems([
            "Full System", "Specific Drive",
            "Custom Path", "Bare Metal (Clone Partitions)"])
        setattr(self, f"{prefix}_src_mode", mode_combo)
        sl.addLayout(_field_row("Source Mode:", mode_combo))

        # Full system warning
        fs_w = QWidget()
        fsl = QVBoxLayout(fs_w); fsl.setContentsMargins(0, 0, 0, 0)
        fs_lbl = QLabel(
            '⚠️ <b style="color:#ef4444">Warning:</b> '
            'All partitions/volumes under root (/) will be backed up. '
            'May be very large.')
        fs_lbl.setWordWrap(True)
        fs_lbl.setStyleSheet(_NOTE_STYLE)
        fsl.addWidget(fs_lbl)
        sl.addWidget(fs_w)

        # Specific drive
        sd_w = QWidget()
        sdl = QHBoxLayout(sd_w); sdl.setContentsMargins(0, 0, 0, 0)
        sd_combo = QComboBox()
        setattr(self, f"{prefix}_sd_combo", sd_combo)
        btn_ref = QPushButton("↻  Refresh")
        btn_ref.setStyleSheet(BTN_SECONDARY)
        btn_ref.clicked.connect(lambda _, c=sd_combo: self.populate_mounted_drives(c))
        sdl.addWidget(QLabel("Mounted Drive:"))
        sdl.addWidget(sd_combo, 1)
        sdl.addWidget(btn_ref)
        sl.addWidget(sd_w)

        # Custom path
        cp_w = QWidget()
        cpl = QHBoxLayout(cp_w); cpl.setContentsMargins(0, 0, 0, 0)
        cp_inp = QLineEdit("/")
        setattr(self, f"{prefix}_src_path", cp_inp)
        btn_cp = QPushButton("Browse")
        btn_cp.setStyleSheet(BTN_SECONDARY)
        btn_cp.clicked.connect(lambda _, w=cp_inp: self.open_safe_folder_dialog(w))
        cpl.addWidget(QLabel("Path:"))
        cpl.addWidget(cp_inp, 1)
        cpl.addWidget(btn_cp)
        sl.addWidget(cp_w)

        # Bare metal partitions
        bm_w = QWidget()
        bml = QVBoxLayout(bm_w); bml.setContentsMargins(0, 0, 0, 0)
        bml.addWidget(QLabel(
            '<span style="color:#ef4444;font-weight:bold">Exclude</span> '
            'partitions from Bare Metal image:'))
        pl = QListWidget()
        setattr(self, f"{prefix}_bm_parts", pl)
        self.populate_all_partitions(pl)
        bml.addWidget(pl)
        sl.addWidget(bm_w)

        def toggle_ui(text):
            for w in [fs_w, sd_w, cp_w, bm_w]:
                w.hide()
            if text == "Full System":
                fs_w.show()
            elif text == "Specific Drive":
                sd_w.show()
                if sd_combo.count() == 0:
                    self.populate_mounted_drives(sd_combo)
            elif text == "Custom Path":
                cp_w.show()
            elif "Bare Metal" in text:
                bm_w.show()

        mode_combo.currentTextChanged.connect(toggle_ui)
        toggle_ui("Full System")
        return container

    def build_encryption_group(self, prefix):
        """Per-profile GPG AES-256 symmetric encryption section."""
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(_hsep())
        lay.addWidget(_sec_label("ENCRYPTION AT REST"))

        row1 = QHBoxLayout()
        tog = ToggleSwitch()
        setattr(self, f"{prefix}_enc_toggle", tog)
        lbl = QLabel("Encrypt archive with GPG AES-256 after backup completes")
        lbl.setStyleSheet(_FIELD_LBL)
        row1.addWidget(tog)
        row1.addWidget(lbl)
        row1.addStretch()
        lay.addLayout(row1)

        pass_w = QWidget()
        pl = QHBoxLayout(pass_w)
        pl.setContentsMargins(0, 0, 0, 0)
        enc_pass = QLineEdit()
        enc_pass.setEchoMode(QLineEdit.EchoMode.Password)
        enc_pass.setPlaceholderText("Strong passphrase — stored encrypted in vault")
        setattr(self, f"{prefix}_enc_pass", enc_pass)
        btn_eye = QPushButton("Show")
        btn_eye.setStyleSheet(BTN_SECONDARY)
        btn_eye.clicked.connect(lambda: self.toggle_pw(enc_pass, btn_eye))
        pl.addWidget(QLabel("Passphrase:"))
        pl.addWidget(enc_pass, 1)
        pl.addWidget(btn_eye)
        lay.addWidget(pass_w)

        note = QLabel(
            "Encrypted archives gain a .gpg extension. "
            "Store your passphrase safely — there is no recovery without it.")
        note.setStyleSheet(_NOTE_STYLE)
        note.setWordWrap(True)
        lay.addWidget(note)

        pass_w.setVisible(tog.isChecked())
        tog.toggled.connect(pass_w.setVisible)
        return container

    def build_compression_group(self, prefix):
        """Compression algorithm and level section."""
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(_hsep())
        lay.addWidget(_sec_label("COMPRESSION SETTINGS"))

        row = QHBoxLayout()
        row.setSpacing(10)
        algo = QComboBox()
        algo.addItems(["gzip  (compatible, .tar.gz)", "zstd  (fast, .tar.zst)"])
        setattr(self, f"{prefix}_comp_algo", algo)
        level = QSpinBox()
        level.setRange(1, 9)
        level.setValue(6)
        level.setToolTip("1 = fastest / largest     9 = slowest / smallest")
        setattr(self, f"{prefix}_comp_level", level)
        row.addWidget(QLabel("Algorithm:"))
        row.addWidget(algo, 1)
        row.addSpacing(20)
        row.addWidget(QLabel("Level:"))
        row.addWidget(level)
        row.addStretch()
        lay.addLayout(row)

        def on_algo(text):
            if "zstd" in text:
                level.setRange(1, 19); level.setValue(3)
            else:
                level.setRange(1, 9); level.setValue(6)
        algo.currentTextChanged.connect(on_algo)

        note = QLabel(
            "Applies to Ext4/Universal archives only. "
            "zstd is ~3× faster than gzip at similar compression ratios. "
            "Btrfs and rsync engines manage compression internally.")
        note.setStyleSheet(_NOTE_STYLE)
        note.setWordWrap(True)
        lay.addWidget(note)
        return container

    def build_hooks_group(self, prefix):
        """Pre/post backup shell hooks section."""
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(_hsep())
        lay.addWidget(_sec_label("PRE / POST BACKUP HOOKS"))

        note = QLabel(
            "Commands run as <b>root</b> in bash immediately before and "
            "after the backup stream. A non-zero exit from the pre-hook "
            "<b>aborts the backup</b>. The post-hook runs only on success.")
        note.setStyleSheet(_NOTE_STYLE)
        note.setWordWrap(True)
        lay.addWidget(note)

        pre = QLineEdit()
        pre.setPlaceholderText(
            "e.g.  systemctl stop postgresql   |   docker pause mydb")
        setattr(self, f"{prefix}_pre_hook", pre)
        lay.addLayout(_field_row("Pre-backup:", pre, label_width=90))

        post = QLineEdit()
        post.setPlaceholderText(
            "e.g.  systemctl start postgresql   |   docker unpause mydb")
        setattr(self, f"{prefix}_post_hook", post)
        lay.addLayout(_field_row("Post-backup:", post, label_width=90))
        return container

    def build_notification_group(self, prefix):
        """Per-profile email + webhook notifications section."""
        container = QWidget()
        container.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(_hsep())
        lay.addWidget(_sec_label("NOTIFICATIONS"))

        # Trigger + channel
        top = QHBoxLayout()
        top.setSpacing(10)
        n_on = QComboBox()
        n_on.addItems(["Never", "Failure Only", "Success Only", "Always"])
        setattr(self, f"{prefix}_notif_on", n_on)
        n_ch = QComboBox()
        n_ch.addItems(["Email", "Webhook (Discord / Slack)", "Both"])
        setattr(self, f"{prefix}_notif_channel", n_ch)
        top.addWidget(QLabel("Notify on:"))
        top.addWidget(n_on)
        top.addSpacing(20)
        top.addWidget(QLabel("Via:"))
        top.addWidget(n_ch)
        top.addStretch()
        lay.addLayout(top)

        # Email fields — in a card container
        email_card = QWidget()
        email_card.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 8px;")
        el = QVBoxLayout(email_card)
        el.setContentsMargins(14, 10, 14, 10)
        el.setSpacing(8)
        el.addWidget(QLabel("Email Settings"))

        to_f = QLineEdit()
        to_f.setPlaceholderText("you@example.com")
        setattr(self, f"{prefix}_notif_to", to_f)
        from_f = QLineEdit()
        from_f.setPlaceholderText("archvault@example.com")
        setattr(self, f"{prefix}_notif_from", from_f)
        er1 = QHBoxLayout()
        er1.addWidget(QLabel("To:"))
        er1.addWidget(to_f, 1)
        er1.addSpacing(10)
        er1.addWidget(QLabel("From:"))
        er1.addWidget(from_f, 1)
        el.addLayout(er1)

        smtp_h = QLineEdit()
        smtp_h.setPlaceholderText("smtp.gmail.com")
        setattr(self, f"{prefix}_notif_smtp_host", smtp_h)
        smtp_p = QSpinBox()
        smtp_p.setRange(1, 65535)
        smtp_p.setValue(587)
        setattr(self, f"{prefix}_notif_smtp_port", smtp_p)
        er2 = QHBoxLayout()
        er2.addWidget(QLabel("SMTP Host:"))
        er2.addWidget(smtp_h, 1)
        er2.addSpacing(10)
        er2.addWidget(QLabel("Port:"))
        er2.addWidget(smtp_p)
        el.addLayout(er2)

        smtp_u = QLineEdit()
        setattr(self, f"{prefix}_notif_smtp_user", smtp_u)
        smtp_pw = QLineEdit()
        smtp_pw.setEchoMode(QLineEdit.EchoMode.Password)
        setattr(self, f"{prefix}_notif_smtp_pass", smtp_pw)
        btn_sp = QPushButton("Show")
        btn_sp.setStyleSheet(BTN_SECONDARY)
        btn_sp.clicked.connect(lambda: self.toggle_pw(smtp_pw, btn_sp))
        er3 = QHBoxLayout()
        er3.addWidget(QLabel("SMTP User:"))
        er3.addWidget(smtp_u, 1)
        er3.addSpacing(10)
        er3.addWidget(QLabel("SMTP Pass:"))
        er3.addWidget(smtp_pw, 1)
        er3.addWidget(btn_sp)
        el.addLayout(er3)
        lay.addWidget(email_card)

        # Webhook field — in a card container
        wh_card = QWidget()
        wh_card.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 8px;")
        wl = QVBoxLayout(wh_card)
        wl.setContentsMargins(14, 10, 14, 10)
        wl.setSpacing(8)
        wl.addWidget(QLabel("Webhook Settings"))
        wh_url = QLineEdit()
        wh_url.setPlaceholderText(
            "https://discord.com/api/webhooks/...  or  https://hooks.slack.com/...")
        setattr(self, f"{prefix}_notif_webhook_url", wh_url)
        wl.addLayout(_field_row("URL:", wh_url))
        lay.addWidget(wh_card)

        def update_vis():
            ch = n_ch.currentText()
            active = n_on.currentText() != "Never"
            n_ch.setEnabled(active)
            email_card.setVisible(active and ch in ("Email", "Both"))
            wh_card.setVisible(active and ch in ("Webhook (Discord / Slack)", "Both"))
        n_on.currentTextChanged.connect(lambda _: update_vis())
        n_ch.currentTextChanged.connect(lambda _: update_vis())
        update_vis()
        return container

    # ─────────────────────────────────────────────────────────────────────
    # DATA GET / POPULATE HELPERS
    # ─────────────────────────────────────────────────────────────────────
    def get_hooks_data(self, prefix):
        pre_w  = getattr(self, f"{prefix}_pre_hook",  None)
        post_w = getattr(self, f"{prefix}_post_hook", None)
        return {
            "pre_hook":  pre_w.text().strip()  if pre_w  else "",
            "post_hook": post_w.text().strip() if post_w else "",
        }

    def populate_hooks_data(self, prefix, prof):
        pre_w  = getattr(self, f"{prefix}_pre_hook",  None)
        post_w = getattr(self, f"{prefix}_post_hook", None)
        if pre_w:  pre_w.setText(prof.get("pre_hook",  ""))
        if post_w: post_w.setText(prof.get("post_hook", ""))

    def get_source_data(self, prefix):
        mode = getattr(self, f"{prefix}_src_mode").currentText()
        if mode == "Full System":
            return {"source_mode": mode, "source_path": "/"}
        elif mode == "Specific Drive":
            return {"source_mode": mode,
                    "source_path": getattr(self, f"{prefix}_sd_combo").currentData() or "/"}
        elif mode == "Custom Path":
            return {"source_mode": mode,
                    "source_path": getattr(self, f"{prefix}_src_path").text().strip()}
        elif "Bare Metal" in mode:
            pl = getattr(self, f"{prefix}_bm_parts")
            inc, exc = [], []
            for i in range(pl.count()):
                item = pl.item(i)
                cd = item.data(Qt.ItemDataRole.UserRole)
                (exc if item.checkState() == Qt.CheckState.Checked else inc).append(cd["path"])
            return {"source_mode": mode, "bm_included": inc, "bm_excluded": exc}

    def populate_source_data(self, prefix, prof):
        mode = prof.get("source_mode", "Full System")
        if mode == "Custom Folder":
            mode = "Custom Path"
        getattr(self, f"{prefix}_src_mode").setCurrentText(mode)
        if mode == "Custom Path":
            getattr(self, f"{prefix}_src_path").setText(prof.get("source_path", "/"))
        elif mode == "Specific Drive":
            sd = getattr(self, f"{prefix}_sd_combo")
            if sd.count() == 0:
                self.populate_mounted_drives(sd)
            for i in range(sd.count()):
                if sd.itemData(i) == prof.get("source_path", ""):
                    sd.setCurrentIndex(i); break
        if hasattr(self, f"{prefix}_bm_parts"):
            pl = getattr(self, f"{prefix}_bm_parts")
            exc = prof.get("bm_excluded", [])
            for i in range(pl.count()):
                item = pl.item(i)
                cd = item.data(Qt.ItemDataRole.UserRole)
                item.setCheckState(
                    Qt.CheckState.Checked if cd["path"] in exc
                    else Qt.CheckState.Unchecked)

    def get_encryption_data(self, prefix):
        return {
            "encrypt":      getattr(self, f"{prefix}_enc_toggle").isChecked(),
            "encrypt_pass": self.encrypt_pw(
                getattr(self, f"{prefix}_enc_pass").text().strip()),
        }

    def populate_encryption_data(self, prefix, prof):
        getattr(self, f"{prefix}_enc_toggle").setChecked(prof.get("encrypt", False))
        raw = prof.get("encrypt_pass", "")
        getattr(self, f"{prefix}_enc_pass").setText(
            self.decrypt_pw(raw) if raw else "")

    def get_compression_data(self, prefix):
        algo_text = getattr(self, f"{prefix}_comp_algo").currentText()
        return {
            "compress_algo":  "zstd" if "zstd" in algo_text else "gzip",
            "compress_level": getattr(self, f"{prefix}_comp_level").value(),
        }

    def populate_compression_data(self, prefix, prof):
        algo = prof.get("compress_algo", "gzip")
        combo = getattr(self, f"{prefix}_comp_algo")
        for i in range(combo.count()):
            if algo in combo.itemText(i):
                combo.setCurrentIndex(i); break
        getattr(self, f"{prefix}_comp_level").setValue(
            prof.get("compress_level", 6))

    def get_notification_data(self, prefix):
        return {
            "notif_on":          getattr(self, f"{prefix}_notif_on").currentText(),
            "notif_channel":     getattr(self, f"{prefix}_notif_channel").currentText(),
            "notif_to":          getattr(self, f"{prefix}_notif_to").text().strip(),
            "notif_from":        getattr(self, f"{prefix}_notif_from").text().strip(),
            "notif_smtp_host":   getattr(self, f"{prefix}_notif_smtp_host").text().strip(),
            "notif_smtp_port":   getattr(self, f"{prefix}_notif_smtp_port").value(),
            "notif_smtp_user":   getattr(self, f"{prefix}_notif_smtp_user").text().strip(),
            "notif_smtp_pass":   self.encrypt_pw(
                getattr(self, f"{prefix}_notif_smtp_pass").text().strip()),
            "notif_webhook_url": getattr(self, f"{prefix}_notif_webhook_url").text().strip(),
        }

    def populate_notification_data(self, prefix, prof):
        def _c(attr, val):
            w = getattr(self, f"{prefix}_{attr}", None)
            if w and hasattr(w, "setCurrentText"):
                w.setCurrentText(val)
        def _t(attr, val):
            w = getattr(self, f"{prefix}_{attr}", None)
            if w and hasattr(w, "setText"):
                w.setText(val)
        def _s(attr, val):
            w = getattr(self, f"{prefix}_{attr}", None)
            if w and hasattr(w, "setValue"):
                w.setValue(val)
        _c("notif_on",          prof.get("notif_on", "Never"))
        _c("notif_channel",     prof.get("notif_channel", "Email"))
        _t("notif_to",          prof.get("notif_to", ""))
        _t("notif_from",        prof.get("notif_from", ""))
        _t("notif_smtp_host",   prof.get("notif_smtp_host", ""))
        _s("notif_smtp_port",   prof.get("notif_smtp_port", 587))
        _t("notif_smtp_user",   prof.get("notif_smtp_user", ""))
        raw = prof.get("notif_smtp_pass", "")
        _t("notif_smtp_pass",   self.decrypt_pw(raw) if raw else "")
        _t("notif_webhook_url", prof.get("notif_webhook_url", ""))

    def _scrollable(self, widget):
        """Wrap any widget in a frameless scroll area."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setWidget(widget)
        return sa

    # ═════════════════════════════════════════════════════════════════════
    #  NETWORK PAGE
    # ═════════════════════════════════════════════════════════════════════
    def build_network_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(mk_page_title(
            "Network Locations",
            "SMB/CIFS and NFS share profiles for network-attached storage"))

        tabs = QTabWidget()

        # ── Create Profile ─────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(inner)
        fl.setSpacing(14)
        fl.setContentsMargins(8, 16, 8, 16)

        self.net_err = QLabel()
        self.net_err.setStyleSheet(_ERR_STYLE)
        self.net_err.hide()
        fl.addWidget(self.net_err)

        fl.addWidget(_sec_label("CONNECTION DETAILS"))

        self.net_name = QLineEdit()
        self.net_name.setPlaceholderText("e.g., Synology NAS")
        fl.addLayout(_field_row("Profile Name:", self.net_name))

        r_proto = QHBoxLayout()
        r_proto.setSpacing(10)
        self.net_proto = QComboBox()
        self.net_proto.addItems(["SMB/CIFS", "NFS"])
        r_proto.addWidget(QLabel("Protocol:"))
        r_proto.addWidget(self.net_proto)
        r_proto.addStretch()
        fl.addLayout(r_proto)

        self.net_path = QLineEdit()
        self.net_path.setPlaceholderText("//192.168.1.100/Backups")
        fl.addLayout(_field_row("Remote Share Path:", self.net_path))
        self.net_path_err = QLabel()
        self.net_path_err.hide()
        fl.addWidget(self.net_path_err)
        self.net_path.textChanged.connect(
            lambda t: self.validate_net_path(t, self.net_proto, self.net_path_err))

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("AUTHENTICATION"))

        self.net_domain = QLineEdit()
        self.net_domain.setPlaceholderText("Optional")
        fl.addLayout(_field_row("Domain:", self.net_domain))

        auth_row = QHBoxLayout()
        auth_row.setSpacing(10)
        self.net_user = QLineEdit()
        self.net_pass = QLineEdit()
        self.net_pass.setEchoMode(QLineEdit.EchoMode.Password)
        b_eye = QPushButton("Show")
        b_eye.setStyleSheet(BTN_SECONDARY)
        b_eye.clicked.connect(lambda: self.toggle_pw(self.net_pass, b_eye))
        auth_row.addWidget(QLabel("User:"))
        auth_row.addWidget(self.net_user, 1)
        auth_row.addSpacing(10)
        auth_row.addWidget(QLabel("Password:"))
        auth_row.addWidget(self.net_pass, 1)
        auth_row.addWidget(b_eye)
        fl.addLayout(auth_row)

        # Reusable sections
        fl.addWidget(self.build_source_group("net"))
        fl.addWidget(self.build_hooks_group("net"))
        fl.addWidget(self.build_encryption_group("net"))
        fl.addWidget(self.build_compression_group("net"))
        fl.addWidget(self.build_notification_group("net"))

        fl.addWidget(_hsep())
        bb = QHBoxLayout()
        bs = QPushButton("💾  Save Profile")
        bs.setStyleSheet(BTN_SUCCESS)
        bs.clicked.connect(self.save_net_profile)
        bb.addWidget(bs)
        bb.addStretch()
        fl.addLayout(bb)
        fl.addStretch()

        tab_c = QWidget()
        tc_l = QVBoxLayout(tab_c)
        tc_l.setContentsMargins(0, 0, 0, 0)
        tc_l.addWidget(self._scrollable(inner))
        tabs.addTab(tab_c, "Create Profile")

        # ── Manage Profiles ────────────────────────────────────────────────
        tab_m = QWidget()
        tab_m.setStyleSheet("background:transparent;")
        ml = QVBoxLayout(tab_m)
        ml.setSpacing(12)
        ml.setContentsMargins(8, 16, 8, 8)

        ml.addWidget(_sec_label("SAVED PROFILES"))
        self.net_list = QListWidget()
        ml.addWidget(self.net_list)

        mb = QHBoxLayout()
        mb.setSpacing(8)
        bm = QPushButton("✏  Modify")
        bm.setStyleSheet(BTN_PRIMARY)
        bm.clicked.connect(self.open_net_edit)
        bd = QPushButton("🗑  Delete")
        bd.setStyleSheet(BTN_DANGER)
        bd.clicked.connect(lambda: self.delete_profile("network"))
        mb.addWidget(bm)
        mb.addWidget(bd)
        mb.addStretch()
        ml.addLayout(mb)

        # Edit panel
        self.net_edit_grp = QWidget()
        self.net_edit_grp.hide()
        self.net_edit_grp.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 10px;")
        el = QVBoxLayout(self.net_edit_grp)
        el.setSpacing(12)
        el.setContentsMargins(16, 14, 16, 14)

        self.net_e_err = QLabel()
        self.net_e_err.setStyleSheet(_ERR_STYLE)
        self.net_e_err.hide()
        el.addWidget(self.net_e_err)
        self.net_e_name = QLabel("Editing:")
        self.net_e_name.setStyleSheet(_EDIT_BADGE)
        el.addWidget(self.net_e_name)

        self.net_e_proto = QComboBox()
        self.net_e_proto.addItems(["SMB/CIFS", "NFS"])
        el.addLayout(_field_row("Protocol:", self.net_e_proto))

        self.net_e_path = QLineEdit()
        el.addLayout(_field_row("Remote Path:", self.net_e_path))
        self.net_e_path_err = QLabel()
        self.net_e_path_err.hide()
        el.addWidget(self.net_e_path_err)
        self.net_e_path.textChanged.connect(
            lambda t: self.validate_net_path(t, self.net_e_proto, self.net_e_path_err))

        er3 = QHBoxLayout()
        er3.setSpacing(10)
        self.net_e_domain = QLineEdit()
        self.net_e_domain.setPlaceholderText("Optional")
        self.net_e_user = QLineEdit()
        self.net_e_pass = QLineEdit()
        self.net_e_pass.setEchoMode(QLineEdit.EchoMode.Password)
        be_eye = QPushButton("Show")
        be_eye.setStyleSheet(BTN_SECONDARY)
        be_eye.clicked.connect(lambda: self.toggle_pw(self.net_e_pass, be_eye))
        er3.addWidget(QLabel("Domain:"))
        er3.addWidget(self.net_e_domain, 1)
        er3.addSpacing(10)
        er3.addWidget(QLabel("User:"))
        er3.addWidget(self.net_e_user, 1)
        er3.addSpacing(10)
        er3.addWidget(QLabel("Pass:"))
        er3.addWidget(self.net_e_pass, 1)
        er3.addWidget(be_eye)
        el.addLayout(er3)

        el.addWidget(self.build_source_group("net_e"))
        el.addWidget(self.build_hooks_group("net_e"))
        el.addWidget(self.build_encryption_group("net_e"))
        el.addWidget(self.build_compression_group("net_e"))
        el.addWidget(self.build_notification_group("net_e"))

        eb = QHBoxLayout()
        eb.setSpacing(8)
        be_s = QPushButton("💾  Save Changes")
        be_s.setStyleSheet(BTN_SUCCESS)
        be_s.clicked.connect(self.save_net_edit)
        be_c = QPushButton("Cancel")
        be_c.setStyleSheet(BTN_SECONDARY)
        be_c.clicked.connect(lambda: self.net_edit_grp.hide())
        eb.addWidget(be_s)
        eb.addWidget(be_c)
        eb.addStretch()
        el.addLayout(eb)

        ml.addWidget(self._scrollable(self.net_edit_grp))
        tabs.addTab(tab_m, "Manage Profiles")

        layout.addWidget(tabs)
        return page

    # ═════════════════════════════════════════════════════════════════════
    #  LOCAL STORAGE PAGE
    # ═════════════════════════════════════════════════════════════════════
    def build_local_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(mk_page_title(
            "Local Storage",
            "Backup to a secondary drive, partition, or local directory"))

        tabs = QTabWidget()

        # ── Create Profile ─────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(inner)
        fl.setSpacing(14)
        fl.setContentsMargins(8, 16, 8, 16)

        self.loc_err = QLabel()
        self.loc_err.setStyleSheet(_ERR_STYLE)
        self.loc_err.hide()
        fl.addWidget(self.loc_err)

        fl.addWidget(_sec_label("PROFILE"))
        self.loc_name = QLineEdit()
        self.loc_name.setPlaceholderText("e.g., Secondary NVMe")
        fl.addLayout(_field_row("Profile Name:", self.loc_name))

        fl.addWidget(self.build_source_group("loc"))

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("DESTINATION"))
        self.loc_path = QLineEdit()
        self.loc_path.setPlaceholderText("/mnt/Drive2/Backups")
        d_br = QPushButton("Browse")
        d_br.setStyleSheet(BTN_SECONDARY)
        d_br.clicked.connect(
            lambda _, w=self.loc_path: self.open_safe_folder_dialog(w))
        fl.addLayout(_field_row("Path:", self.loc_path, [d_br]))

        fl.addWidget(self.build_hooks_group("loc"))
        fl.addWidget(self.build_encryption_group("loc"))
        fl.addWidget(self.build_compression_group("loc"))
        fl.addWidget(self.build_notification_group("loc"))

        fl.addWidget(_hsep())
        bb = QHBoxLayout()
        bs = QPushButton("💾  Save Profile")
        bs.setStyleSheet(BTN_SUCCESS)
        bs.clicked.connect(self.save_loc_profile)
        bb.addWidget(bs)
        bb.addStretch()
        fl.addLayout(bb)
        fl.addStretch()

        tab_c = QWidget()
        tc_l = QVBoxLayout(tab_c)
        tc_l.setContentsMargins(0, 0, 0, 0)
        tc_l.addWidget(self._scrollable(inner))
        tabs.addTab(tab_c, "Create Profile")

        # ── Manage Profiles ────────────────────────────────────────────────
        tab_m = QWidget()
        tab_m.setStyleSheet("background:transparent;")
        ml = QVBoxLayout(tab_m)
        ml.setSpacing(12)
        ml.setContentsMargins(8, 16, 8, 8)

        ml.addWidget(_sec_label("SAVED PROFILES"))
        self.loc_list = QListWidget()
        ml.addWidget(self.loc_list)

        mb = QHBoxLayout()
        mb.setSpacing(8)
        bm = QPushButton("✏  Modify")
        bm.setStyleSheet(BTN_PRIMARY)
        bm.clicked.connect(self.open_loc_edit)
        bd = QPushButton("🗑  Delete")
        bd.setStyleSheet(BTN_DANGER)
        bd.clicked.connect(lambda: self.delete_profile("local"))
        mb.addWidget(bm)
        mb.addWidget(bd)
        mb.addStretch()
        ml.addLayout(mb)

        self.loc_edit_grp = QWidget()
        self.loc_edit_grp.hide()
        self.loc_edit_grp.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 10px;")
        el = QVBoxLayout(self.loc_edit_grp)
        el.setSpacing(12)
        el.setContentsMargins(16, 14, 16, 14)

        self.loc_e_err = QLabel()
        self.loc_e_err.setStyleSheet(_ERR_STYLE)
        self.loc_e_err.hide()
        el.addWidget(self.loc_e_err)
        self.loc_e_name = QLabel("Editing:")
        self.loc_e_name.setStyleSheet(_EDIT_BADGE)
        el.addWidget(self.loc_e_name)

        el.addWidget(self.build_source_group("loc_e"))

        self.loc_e_path = QLineEdit()
        ed_br = QPushButton("Browse")
        ed_br.setStyleSheet(BTN_SECONDARY)
        ed_br.clicked.connect(
            lambda _, w=self.loc_e_path: self.open_safe_folder_dialog(w))
        el.addLayout(_field_row("Dest Path:", self.loc_e_path, [ed_br]))

        el.addWidget(self.build_hooks_group("loc_e"))
        el.addWidget(self.build_encryption_group("loc_e"))
        el.addWidget(self.build_compression_group("loc_e"))
        el.addWidget(self.build_notification_group("loc_e"))

        eb = QHBoxLayout()
        eb.setSpacing(8)
        be_s = QPushButton("💾  Save Changes")
        be_s.setStyleSheet(BTN_SUCCESS)
        be_s.clicked.connect(self.save_loc_edit)
        be_c = QPushButton("Cancel")
        be_c.setStyleSheet(BTN_SECONDARY)
        be_c.clicked.connect(lambda: self.loc_edit_grp.hide())
        eb.addWidget(be_s)
        eb.addWidget(be_c)
        eb.addStretch()
        el.addLayout(eb)

        ml.addWidget(self._scrollable(self.loc_edit_grp))
        tabs.addTab(tab_m, "Manage Profiles")

        layout.addWidget(tabs)
        return page

    # ═════════════════════════════════════════════════════════════════════
    #  USB / REMOVABLE PAGE
    # ═════════════════════════════════════════════════════════════════════
    def build_usb_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(mk_page_title(
            "USB / Removable",
            "Backup to external USB drives, flash storage, or removable media"))

        tabs = QTabWidget()

        # ── Create Profile ─────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(inner)
        fl.setSpacing(14)
        fl.setContentsMargins(8, 16, 8, 16)

        self.usb_err = QLabel()
        self.usb_err.setStyleSheet(_ERR_STYLE)
        self.usb_err.hide()
        fl.addWidget(self.usb_err)

        fl.addWidget(_sec_label("PROFILE"))
        self.usb_name = QLineEdit()
        self.usb_name.setPlaceholderText("e.g., External SSD")
        fl.addLayout(_field_row("Profile Name:", self.usb_name))

        fl.addWidget(self.build_source_group("usb"))

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("DESTINATION"))
        self.usb_path = QLineEdit()
        self.usb_path.setPlaceholderText("/run/media/user/USB_DRIVE")
        d_br = QPushButton("Browse")
        d_br.setStyleSheet(BTN_SECONDARY)
        d_br.clicked.connect(
            lambda _, w=self.usb_path: self.open_safe_folder_dialog(w))
        fl.addLayout(_field_row("Mount Path:", self.usb_path, [d_br]))

        fl.addWidget(self.build_hooks_group("usb"))
        fl.addWidget(self.build_encryption_group("usb"))
        fl.addWidget(self.build_compression_group("usb"))
        fl.addWidget(self.build_notification_group("usb"))

        fl.addWidget(_hsep())
        bb = QHBoxLayout()
        bs = QPushButton("💾  Save Profile")
        bs.setStyleSheet(BTN_SUCCESS)
        bs.clicked.connect(self.save_usb_profile)
        bb.addWidget(bs)
        bb.addStretch()
        fl.addLayout(bb)
        fl.addStretch()

        tab_c = QWidget()
        tc_l = QVBoxLayout(tab_c)
        tc_l.setContentsMargins(0, 0, 0, 0)
        tc_l.addWidget(self._scrollable(inner))
        tabs.addTab(tab_c, "Create Profile")

        # ── Manage Profiles ────────────────────────────────────────────────
        tab_m = QWidget()
        tab_m.setStyleSheet("background:transparent;")
        ml = QVBoxLayout(tab_m)
        ml.setSpacing(12)
        ml.setContentsMargins(8, 16, 8, 8)

        ml.addWidget(_sec_label("SAVED PROFILES"))
        self.usb_list = QListWidget()
        ml.addWidget(self.usb_list)

        mb = QHBoxLayout()
        mb.setSpacing(8)
        bm = QPushButton("✏  Modify")
        bm.setStyleSheet(BTN_PRIMARY)
        bm.clicked.connect(self.open_usb_edit)
        bd = QPushButton("🗑  Delete")
        bd.setStyleSheet(BTN_DANGER)
        bd.clicked.connect(lambda: self.delete_profile("usb"))
        mb.addWidget(bm)
        mb.addWidget(bd)
        mb.addStretch()
        ml.addLayout(mb)

        self.usb_edit_grp = QWidget()
        self.usb_edit_grp.hide()
        self.usb_edit_grp.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 10px;")
        el = QVBoxLayout(self.usb_edit_grp)
        el.setSpacing(12)
        el.setContentsMargins(16, 14, 16, 14)

        self.usb_e_err = QLabel()
        self.usb_e_err.setStyleSheet(_ERR_STYLE)
        self.usb_e_err.hide()
        el.addWidget(self.usb_e_err)
        self.usb_e_name = QLabel("Editing:")
        self.usb_e_name.setStyleSheet(_EDIT_BADGE)
        el.addWidget(self.usb_e_name)

        el.addWidget(self.build_source_group("usb_e"))

        self.usb_e_path = QLineEdit()
        ed_br = QPushButton("Browse")
        ed_br.setStyleSheet(BTN_SECONDARY)
        ed_br.clicked.connect(
            lambda _, w=self.usb_e_path: self.open_safe_folder_dialog(w))
        el.addLayout(_field_row("Mount Path:", self.usb_e_path, [ed_br]))

        el.addWidget(self.build_hooks_group("usb_e"))
        el.addWidget(self.build_encryption_group("usb_e"))
        el.addWidget(self.build_compression_group("usb_e"))
        el.addWidget(self.build_notification_group("usb_e"))

        eb = QHBoxLayout()
        eb.setSpacing(8)
        be_s = QPushButton("💾  Save Changes")
        be_s.setStyleSheet(BTN_SUCCESS)
        be_s.clicked.connect(self.save_usb_edit)
        be_c = QPushButton("Cancel")
        be_c.setStyleSheet(BTN_SECONDARY)
        be_c.clicked.connect(lambda: self.usb_edit_grp.hide())
        eb.addWidget(be_s)
        eb.addWidget(be_c)
        eb.addStretch()
        el.addLayout(eb)

        ml.addWidget(self._scrollable(self.usb_edit_grp))
        tabs.addTab(tab_m, "Manage Profiles")

        layout.addWidget(tabs)
        return page

    # ═════════════════════════════════════════════════════════════════════
    #  CLOUD PROVIDERS PAGE
    # ═════════════════════════════════════════════════════════════════════
    def build_cloud_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(mk_page_title(
            "Cloud Providers",
            "AWS S3, Backblaze B2, Google Cloud, Azure, Wasabi, and S3-compatible"))

        tabs = QTabWidget()

        # ── Create Profile ─────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(inner)
        fl.setSpacing(14)
        fl.setContentsMargins(8, 16, 8, 16)

        self.cld_err = QLabel()
        self.cld_err.setStyleSheet(_ERR_STYLE)
        self.cld_err.hide()
        fl.addWidget(self.cld_err)

        fl.addWidget(_sec_label("PROVIDER & BUCKET"))

        self.cld_name = QLineEdit()
        self.cld_name.setPlaceholderText("e.g., S3 Archive")
        fl.addLayout(_field_row("Profile Name:", self.cld_name))

        self.cld_prov = QComboBox()
        self.cld_prov.addItems([
            "AWS S3", "Backblaze B2", "Google Cloud Storage",
            "Azure Blob", "Wasabi", "Generic S3"])
        fl.addLayout(_field_row("Provider:", self.cld_prov))

        self.cld_buck = QLineEdit()
        self.cld_buck.setPlaceholderText("my-archvault-bucket")
        fl.addLayout(_field_row("Bucket / Container:", self.cld_buck))

        # Dynamic region / endpoint
        self.cld_region_widget = QWidget()
        self.cld_region_widget.setStyleSheet("background:transparent;")
        rrl = QHBoxLayout(self.cld_region_widget)
        rrl.setContentsMargins(0, 0, 0, 0)
        self.cld_region = QLineEdit()
        self.cld_region.setPlaceholderText("us-east-1")
        rrl.addWidget(QLabel("Region:"))
        rrl.addWidget(self.cld_region, 1)
        fl.addWidget(self.cld_region_widget)

        self.cld_endpoint_widget = QWidget()
        self.cld_endpoint_widget.setStyleSheet("background:transparent;")
        rel = QHBoxLayout(self.cld_endpoint_widget)
        rel.setContentsMargins(0, 0, 0, 0)
        self.cld_endpoint = QLineEdit()
        self.cld_endpoint.setPlaceholderText("https://s3.wasabisys.com")
        rel.addWidget(QLabel("Endpoint URL:"))
        rel.addWidget(self.cld_endpoint, 1)
        fl.addWidget(self.cld_endpoint_widget)

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("CREDENTIALS"))

        r4 = QHBoxLayout()
        r4.setSpacing(10)
        self.cld_key_lbl = QLabel("Access Key:")
        self.cld_user = QLineEdit()
        self.cld_secret_lbl = QLabel("Secret Key:")
        self.cld_pass = QLineEdit()
        self.cld_pass.setEchoMode(QLineEdit.EchoMode.Password)
        b_eye = QPushButton("Show")
        b_eye.setStyleSheet(BTN_SECONDARY)
        b_eye.clicked.connect(lambda: self.toggle_pw(self.cld_pass, b_eye))
        r4.addWidget(self.cld_key_lbl)
        r4.addWidget(self.cld_user, 1)
        r4.addSpacing(10)
        r4.addWidget(self.cld_secret_lbl)
        r4.addWidget(self.cld_pass, 1)
        r4.addWidget(b_eye)
        fl.addLayout(r4)

        fl.addWidget(self.build_source_group("cld"))
        fl.addWidget(self.build_notification_group("cld"))

        fl.addWidget(_hsep())
        bb = QHBoxLayout()
        bs = QPushButton("💾  Save Profile")
        bs.setStyleSheet(BTN_SUCCESS)
        bs.clicked.connect(self.save_cloud_profile)
        bb.addWidget(bs)
        bb.addStretch()
        fl.addLayout(bb)
        fl.addStretch()

        self.cld_prov.currentTextChanged.connect(
            lambda p: _update_cloud_fields(
                p, self.cld_region_widget, self.cld_endpoint_widget,
                self.cld_key_lbl, self.cld_user, self.cld_secret_lbl, self.cld_pass))
        _update_cloud_fields(
            self.cld_prov.currentText(), self.cld_region_widget,
            self.cld_endpoint_widget, self.cld_key_lbl, self.cld_user,
            self.cld_secret_lbl, self.cld_pass)

        tab_c = QWidget()
        tc_l = QVBoxLayout(tab_c)
        tc_l.setContentsMargins(0, 0, 0, 0)
        tc_l.addWidget(self._scrollable(inner))
        tabs.addTab(tab_c, "Create Profile")

        # ── Manage Profiles ────────────────────────────────────────────────
        tab_m = QWidget()
        tab_m.setStyleSheet("background:transparent;")
        ml = QVBoxLayout(tab_m)
        ml.setSpacing(12)
        ml.setContentsMargins(8, 16, 8, 8)

        ml.addWidget(_sec_label("SAVED PROFILES"))
        self.cld_list = QListWidget()
        ml.addWidget(self.cld_list)

        mb = QHBoxLayout()
        mb.setSpacing(8)
        bm = QPushButton("✏  Modify")
        bm.setStyleSheet(BTN_PRIMARY)
        bm.clicked.connect(self.open_cloud_edit)
        bd = QPushButton("🗑  Delete")
        bd.setStyleSheet(BTN_DANGER)
        bd.clicked.connect(lambda: self.delete_profile("cloud"))
        mb.addWidget(bm)
        mb.addWidget(bd)
        mb.addStretch()
        ml.addLayout(mb)

        self.cld_edit_grp = QWidget()
        self.cld_edit_grp.hide()
        self.cld_edit_grp.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 10px;")
        el = QVBoxLayout(self.cld_edit_grp)
        el.setSpacing(12)
        el.setContentsMargins(16, 14, 16, 14)

        self.cld_e_err = QLabel()
        self.cld_e_err.setStyleSheet(_ERR_STYLE)
        self.cld_e_err.hide()
        el.addWidget(self.cld_e_err)
        self.cld_e_name = QLabel("Editing:")
        self.cld_e_name.setStyleSheet(_EDIT_BADGE)
        el.addWidget(self.cld_e_name)

        self.cld_e_prov = QComboBox()
        self.cld_e_prov.addItems([
            "AWS S3", "Backblaze B2", "Google Cloud Storage",
            "Azure Blob", "Wasabi", "Generic S3"])
        el.addLayout(_field_row("Provider:", self.cld_e_prov))

        self.cld_e_buck = QLineEdit()
        el.addLayout(_field_row("Bucket:", self.cld_e_buck))

        self.cld_e_region_widget = QWidget()
        self.cld_e_region_widget.setStyleSheet("background:transparent;")
        errl = QHBoxLayout(self.cld_e_region_widget)
        errl.setContentsMargins(0, 0, 0, 0)
        self.cld_e_region = QLineEdit()
        errl.addWidget(QLabel("Region:"))
        errl.addWidget(self.cld_e_region, 1)
        el.addWidget(self.cld_e_region_widget)

        self.cld_e_endpoint_widget = QWidget()
        self.cld_e_endpoint_widget.setStyleSheet("background:transparent;")
        erel = QHBoxLayout(self.cld_e_endpoint_widget)
        erel.setContentsMargins(0, 0, 0, 0)
        self.cld_e_endpoint = QLineEdit()
        erel.addWidget(QLabel("Endpoint URL:"))
        erel.addWidget(self.cld_e_endpoint, 1)
        el.addWidget(self.cld_e_endpoint_widget)

        er4 = QHBoxLayout()
        er4.setSpacing(10)
        self.cld_e_key_lbl = QLabel("Access Key:")
        self.cld_e_user = QLineEdit()
        self.cld_e_secret_lbl = QLabel("Secret Key:")
        self.cld_e_pass = QLineEdit()
        self.cld_e_pass.setEchoMode(QLineEdit.EchoMode.Password)
        be_eye = QPushButton("Show")
        be_eye.setStyleSheet(BTN_SECONDARY)
        be_eye.clicked.connect(lambda: self.toggle_pw(self.cld_e_pass, be_eye))
        er4.addWidget(self.cld_e_key_lbl)
        er4.addWidget(self.cld_e_user, 1)
        er4.addSpacing(10)
        er4.addWidget(self.cld_e_secret_lbl)
        er4.addWidget(self.cld_e_pass, 1)
        er4.addWidget(be_eye)
        el.addLayout(er4)

        self.cld_e_prov.currentTextChanged.connect(
            lambda p: _update_cloud_fields(
                p, self.cld_e_region_widget, self.cld_e_endpoint_widget,
                self.cld_e_key_lbl, self.cld_e_user,
                self.cld_e_secret_lbl, self.cld_e_pass))

        el.addWidget(self.build_source_group("cld_e"))
        el.addWidget(self.build_notification_group("cld_e"))

        eb = QHBoxLayout()
        eb.setSpacing(8)
        be_s = QPushButton("💾  Save Changes")
        be_s.setStyleSheet(BTN_SUCCESS)
        be_s.clicked.connect(self.save_cloud_edit)
        be_c = QPushButton("Cancel")
        be_c.setStyleSheet(BTN_SECONDARY)
        be_c.clicked.connect(lambda: self.cld_edit_grp.hide())
        eb.addWidget(be_s)
        eb.addWidget(be_c)
        eb.addStretch()
        el.addLayout(eb)

        ml.addWidget(self._scrollable(self.cld_edit_grp))
        tabs.addTab(tab_m, "Manage Profiles")

        layout.addWidget(tabs)
        return page

    # ═════════════════════════════════════════════════════════════════════
    #  SFTP / SSH PAGE
    # ═════════════════════════════════════════════════════════════════════
    def build_sftp_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(mk_page_title(
            "SFTP / SSH Targets",
            "rsync-over-SSH incremental backups or sshfs FUSE-mounted archives"))

        tabs = QTabWidget()

        # ── Create Profile ─────────────────────────────────────────────────
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(inner)
        fl.setSpacing(14)
        fl.setContentsMargins(8, 16, 8, 16)

        self.sftp_err = QLabel()
        self.sftp_err.setStyleSheet(_ERR_STYLE)
        self.sftp_err.hide()
        fl.addWidget(self.sftp_err)

        fl.addWidget(_sec_label("CONNECTION"))

        self.sftp_name = QLineEdit()
        self.sftp_name.setPlaceholderText("e.g., Home NAS")
        fl.addLayout(_field_row("Profile Name:", self.sftp_name))

        r1 = QHBoxLayout()
        r1.setSpacing(10)
        self.sftp_host = QLineEdit()
        self.sftp_host.setPlaceholderText("192.168.1.50 or backup.example.com")
        self.sftp_port = QLineEdit("22")
        self.sftp_port.setFixedWidth(60)
        r1.addWidget(QLabel("Hostname / IP:"))
        r1.addWidget(self.sftp_host, 1)
        r1.addSpacing(10)
        r1.addWidget(QLabel("Port:"))
        r1.addWidget(self.sftp_port)
        fl.addLayout(r1)

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("AUTHENTICATION"))

        r2 = QHBoxLayout()
        r2.setSpacing(10)
        self.sftp_user = QLineEdit()
        self.sftp_auth = QComboBox()
        self.sftp_auth.addItems(["SSH Key (recommended)", "Password"])
        r2.addWidget(QLabel("Username:"))
        r2.addWidget(self.sftp_user, 1)
        r2.addSpacing(10)
        r2.addWidget(QLabel("Auth Method:"))
        r2.addWidget(self.sftp_auth)
        fl.addLayout(r2)

        # Key credential panel
        self._sftp_key_widget = QWidget()
        self._sftp_key_widget.setStyleSheet("background:transparent;")
        kl = QHBoxLayout(self._sftp_key_widget)
        kl.setContentsMargins(0, 0, 0, 0)
        self.sftp_key = QLineEdit()
        self.sftp_key.setPlaceholderText("~/.ssh/id_rsa  (leave blank for ssh-agent)")
        btn_sftp_key_br = QPushButton("Browse")
        btn_sftp_key_br.setStyleSheet(BTN_SECONDARY)
        btn_sftp_key_br.clicked.connect(
            lambda: self._browse_file(self.sftp_key, "Select SSH Key", ""))
        kl.addWidget(QLabel("SSH Key File:"))
        kl.addWidget(self.sftp_key, 1)
        kl.addWidget(btn_sftp_key_br)
        fl.addWidget(self._sftp_key_widget)

        # Password credential panel
        self._sftp_pw_widget = QWidget()
        self._sftp_pw_widget.setStyleSheet("background:transparent;")
        pw_lay = QHBoxLayout(self._sftp_pw_widget)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        self.sftp_pass = QLineEdit()
        self.sftp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        btn_sftp_eye = QPushButton("Show")
        btn_sftp_eye.setStyleSheet(BTN_SECONDARY)
        btn_sftp_eye.clicked.connect(
            lambda: self.toggle_pw(self.sftp_pass, btn_sftp_eye))
        _sftp_pw_note = QLabel("Requires sshpass  (sudo pacman -S sshpass)")
        _sftp_pw_note.setStyleSheet("color:#f59e0b; font-size:11px; background:transparent;")
        pw_lay.addWidget(QLabel("Password:"))
        pw_lay.addWidget(self.sftp_pass, 1)
        pw_lay.addWidget(btn_sftp_eye)
        pw_lay.addWidget(_sftp_pw_note)
        self._sftp_pw_widget.hide()
        fl.addWidget(self._sftp_pw_widget)

        def _sftp_auth_changed(text):
            self._sftp_key_widget.setVisible("SSH Key" in text)
            self._sftp_pw_widget.setVisible("Password" in text)
        self.sftp_auth.currentTextChanged.connect(_sftp_auth_changed)
        _sftp_auth_changed(self.sftp_auth.currentText())

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("REMOTE DESTINATION"))

        r3 = QHBoxLayout()
        r3.setSpacing(10)
        self.sftp_rpath = QLineEdit()
        self.sftp_rpath.setPlaceholderText("/backup/archvault")
        btn_test_c = QPushButton("🔌  Test Connection")
        btn_test_c.setStyleSheet(BTN_INFO)
        btn_test_c.clicked.connect(lambda: self._sftp_test_connection(
            self.sftp_host, self.sftp_port, self.sftp_user, self.sftp_auth,
            self.sftp_key, self.sftp_pass, self.sftp_rpath, self.sftp_err))
        r3.addWidget(QLabel("Remote Path:"))
        r3.addWidget(self.sftp_rpath, 1)
        r3.addWidget(btn_test_c)
        fl.addLayout(r3)

        fl.addWidget(_hsep())
        fl.addWidget(_sec_label("TRANSFER MODE"))

        self.sftp_transfer_mode = QComboBox()
        self.sftp_transfer_mode.addItems([
            "rsync over SSH  \u2014 incremental hardlink snapshots (recommended)",
            "sshfs (FUSE mount)  \u2014 streams a tar/btrfs archive to remote",
        ])
        fl.addWidget(self.sftp_transfer_mode)

        tm_note = QLabel(
            "<b>rsync over SSH</b>: Pushes only changed files via rsync -e ssh. "
            "Hardlink snapshots mean each run uses barely more space than the delta. "
            "Requires rsync on both ends — no FUSE, no sshfs.<br>"
            "<b>sshfs</b>: Mounts the remote directory locally, then streams a full "
            "tar or btrfs archive. Requires sshfs on this machine.")
        tm_note.setStyleSheet(_NOTE_STYLE)
        tm_note.setWordWrap(True)
        fl.addWidget(tm_note)

        fl.addWidget(self.build_source_group("sftp"))
        fl.addWidget(self.build_hooks_group("sftp"))

        fl.addWidget(_hsep())
        bb = QHBoxLayout()
        bs = QPushButton("💾  Save Profile")
        bs.setStyleSheet(BTN_SUCCESS)
        bs.clicked.connect(self.save_sftp_profile)
        bb.addWidget(bs)
        bb.addStretch()
        fl.addLayout(bb)
        fl.addStretch()

        tab_c = QWidget()
        tc_l = QVBoxLayout(tab_c)
        tc_l.setContentsMargins(0, 0, 0, 0)
        tc_l.addWidget(self._scrollable(inner))
        tabs.addTab(tab_c, "Create Profile")

        # ── Manage Profiles ────────────────────────────────────────────────
        tab_m = QWidget()
        tab_m.setStyleSheet("background:transparent;")
        ml = QVBoxLayout(tab_m)
        ml.setSpacing(12)
        ml.setContentsMargins(8, 16, 8, 8)

        ml.addWidget(_sec_label("SAVED PROFILES"))
        self.sftp_list = QListWidget()
        ml.addWidget(self.sftp_list)

        mb = QHBoxLayout()
        mb.setSpacing(8)
        bm = QPushButton("✏  Modify")
        bm.setStyleSheet(BTN_PRIMARY)
        bm.clicked.connect(self.open_sftp_edit)
        bd = QPushButton("🗑  Delete")
        bd.setStyleSheet(BTN_DANGER)
        bd.clicked.connect(lambda: self.delete_profile("sftp"))
        mb.addWidget(bm)
        mb.addWidget(bd)
        mb.addStretch()
        ml.addLayout(mb)

        self.sftp_edit_grp = QWidget()
        self.sftp_edit_grp.hide()
        self.sftp_edit_grp.setStyleSheet(
            "background: rgba(100,116,139,0.04); "
            "border: 1px solid rgba(100,116,139,0.12); border-radius: 10px;")
        el = QVBoxLayout(self.sftp_edit_grp)
        el.setSpacing(12)
        el.setContentsMargins(16, 14, 16, 14)

        self.sftp_e_err = QLabel()
        self.sftp_e_err.setStyleSheet(_ERR_STYLE)
        self.sftp_e_err.hide()
        el.addWidget(self.sftp_e_err)
        self.sftp_e_name_lbl = QLabel("Editing:")
        self.sftp_e_name_lbl.setStyleSheet(_EDIT_BADGE)
        el.addWidget(self.sftp_e_name_lbl)

        er1 = QHBoxLayout()
        er1.setSpacing(10)
        self.sftp_e_host = QLineEdit()
        self.sftp_e_port = QLineEdit("22")
        self.sftp_e_port.setFixedWidth(60)
        er1.addWidget(QLabel("Hostname:"))
        er1.addWidget(self.sftp_e_host, 1)
        er1.addSpacing(10)
        er1.addWidget(QLabel("Port:"))
        er1.addWidget(self.sftp_e_port)
        el.addLayout(er1)

        er2 = QHBoxLayout()
        er2.setSpacing(10)
        self.sftp_e_user = QLineEdit()
        self.sftp_e_auth = QComboBox()
        self.sftp_e_auth.addItems(["SSH Key (recommended)", "Password"])
        er2.addWidget(QLabel("Username:"))
        er2.addWidget(self.sftp_e_user, 1)
        er2.addSpacing(10)
        er2.addWidget(QLabel("Auth:"))
        er2.addWidget(self.sftp_e_auth)
        el.addLayout(er2)

        self._sftp_e_key_widget = QWidget()
        self._sftp_e_key_widget.setStyleSheet("background:transparent;")
        ekl = QHBoxLayout(self._sftp_e_key_widget)
        ekl.setContentsMargins(0, 0, 0, 0)
        self.sftp_e_key = QLineEdit()
        btn_e_key_br = QPushButton("Browse")
        btn_e_key_br.setStyleSheet(BTN_SECONDARY)
        btn_e_key_br.clicked.connect(
            lambda: self._browse_file(self.sftp_e_key, "Select SSH Key", ""))
        ekl.addWidget(QLabel("SSH Key File:"))
        ekl.addWidget(self.sftp_e_key, 1)
        ekl.addWidget(btn_e_key_br)
        el.addWidget(self._sftp_e_key_widget)

        self._sftp_e_pw_widget = QWidget()
        self._sftp_e_pw_widget.setStyleSheet("background:transparent;")
        epw = QHBoxLayout(self._sftp_e_pw_widget)
        epw.setContentsMargins(0, 0, 0, 0)
        self.sftp_e_pass = QLineEdit()
        self.sftp_e_pass.setEchoMode(QLineEdit.EchoMode.Password)
        btn_e_eye = QPushButton("Show")
        btn_e_eye.setStyleSheet(BTN_SECONDARY)
        btn_e_eye.clicked.connect(
            lambda: self.toggle_pw(self.sftp_e_pass, btn_e_eye))
        epw.addWidget(QLabel("Password:"))
        epw.addWidget(self.sftp_e_pass, 1)
        epw.addWidget(btn_e_eye)
        self._sftp_e_pw_widget.hide()
        el.addWidget(self._sftp_e_pw_widget)

        def _sftp_e_auth_changed(text):
            self._sftp_e_key_widget.setVisible("SSH Key" in text)
            self._sftp_e_pw_widget.setVisible("Password" in text)
        self.sftp_e_auth.currentTextChanged.connect(_sftp_e_auth_changed)

        er4 = QHBoxLayout()
        er4.setSpacing(10)
        self.sftp_e_rpath = QLineEdit()
        btn_e_test = QPushButton("🔌  Test")
        btn_e_test.setStyleSheet(BTN_INFO)
        btn_e_test.clicked.connect(lambda: self._sftp_test_connection(
            self.sftp_e_host, self.sftp_e_port, self.sftp_e_user,
            self.sftp_e_auth, self.sftp_e_key, self.sftp_e_pass,
            self.sftp_e_rpath, self.sftp_e_err))
        er4.addWidget(QLabel("Remote Path:"))
        er4.addWidget(self.sftp_e_rpath, 1)
        er4.addWidget(btn_e_test)
        el.addLayout(er4)

        etm_row = QHBoxLayout()
        etm_row.setSpacing(10)
        self.sftp_e_transfer_mode = QComboBox()
        self.sftp_e_transfer_mode.addItems([
            "rsync over SSH  \u2014 incremental hardlink snapshots (recommended)",
            "sshfs (FUSE mount)  \u2014 streams a tar/btrfs archive to remote",
        ])
        etm_row.addWidget(QLabel("Transfer Mode:"))
        etm_row.addWidget(self.sftp_e_transfer_mode, 1)
        el.addLayout(etm_row)

        el.addWidget(self.build_source_group("sftp_e"))
        el.addWidget(self.build_hooks_group("sftp_e"))

        eb = QHBoxLayout()
        eb.setSpacing(8)
        be_save = QPushButton("💾  Save Changes")
        be_save.setStyleSheet(BTN_SUCCESS)
        be_save.clicked.connect(self.save_sftp_edit)
        be_canc = QPushButton("Cancel")
        be_canc.setStyleSheet(BTN_SECONDARY)
        be_canc.clicked.connect(lambda: self.sftp_edit_grp.hide())
        eb.addWidget(be_save)
        eb.addWidget(be_canc)
        eb.addStretch()
        el.addLayout(eb)

        ml.addWidget(self._scrollable(self.sftp_edit_grp))
        tabs.addTab(tab_m, "Manage Profiles")

        layout.addWidget(tabs)
        return page

    # ═════════════════════════════════════════════════════════════════════
    #  SAVE / EDIT / DELETE
    # ═════════════════════════════════════════════════════════════════════
    def delete_profile(self, cat):
        lst = {
            "network": self.net_list, "local": self.loc_list,
            "usb": self.usb_list, "cloud": self.cld_list,
            "sftp": self.sftp_list,
        }.get(cat)
        if not lst or not lst.currentItem():
            return
        name = lst.currentItem().text()
        if confirm_action(
                self, "Delete Profile",
                f"Are you sure you want to delete the profile '{name}'?",
                detail=f"Category: {cat.upper()}\n"
                       f"This will remove all saved settings for this profile.",
                confirm_text="Delete", destructive=True, icon_char="🗑"):
            del self.profiles[cat][name]
            self.write_profiles(f"Deleted '{name}'.")

    def save_net_profile(self):
        if not self.validate_inputs(
                [(self.net_name, "Profile Name"),
                 (self.net_path, "Remote Share Path")], self.net_err):
            return
        n = self.net_name.text().strip()
        data = {
            "protocol": self.net_proto.currentText(),
            "path": self.net_path.text().strip(),
            "domain": self.net_domain.text().strip(),
            "username": self.net_user.text().strip(),
            "password": self.encrypt_pw(self.net_pass.text().strip()),
        }
        data.update(self.get_source_data("net"))
        data.update(self.get_hooks_data("net"))
        data.update(self.get_encryption_data("net"))
        data.update(self.get_compression_data("net"))
        data.update(self.get_notification_data("net"))
        self.profiles["network"][n] = data
        self.write_profiles("Network profile saved.")
        for w in [self.net_name, self.net_path, self.net_domain,
                  self.net_user, self.net_pass]:
            w.clear()

    def save_loc_profile(self):
        if not self.validate_inputs(
                [(self.loc_name, "Profile Name"),
                 (self.loc_path, "Destination Path")], self.loc_err):
            return
        n = self.loc_name.text().strip()
        data = {"path": self.loc_path.text().strip()}
        data.update(self.get_source_data("loc"))
        data.update(self.get_hooks_data("loc"))
        data.update(self.get_encryption_data("loc"))
        data.update(self.get_compression_data("loc"))
        data.update(self.get_notification_data("loc"))
        self.profiles["local"][n] = data
        self.write_profiles("Local profile saved.")
        self.loc_name.clear()
        self.loc_path.clear()

    def save_usb_profile(self):
        if not self.validate_inputs(
                [(self.usb_name, "Profile Name"),
                 (self.usb_path, "USB Mount Path")], self.usb_err):
            return
        n = self.usb_name.text().strip()
        data = {"path": self.usb_path.text().strip()}
        data.update(self.get_source_data("usb"))
        data.update(self.get_hooks_data("usb"))
        data.update(self.get_encryption_data("usb"))
        data.update(self.get_compression_data("usb"))
        data.update(self.get_notification_data("usb"))
        self.profiles["usb"][n] = data
        self.write_profiles("USB profile saved.")
        self.usb_name.clear()
        self.usb_path.clear()

    def save_cloud_profile(self):
        if not self.validate_inputs(
                [(self.cld_name, "Profile Name"),
                 (self.cld_buck, "Bucket Name")], self.cld_err):
            return
        n = self.cld_name.text().strip()
        data = {
            "provider": self.cld_prov.currentText(),
            "bucket": self.cld_buck.text().strip(),
            "region": self.cld_region.text().strip(),
            "endpoint_url": self.cld_endpoint.text().strip(),
            "access_key": self.cld_user.text().strip(),
            "secret_key": self.encrypt_pw(self.cld_pass.text().strip()),
        }
        data.update(self.get_source_data("cld"))
        data.update(self.get_notification_data("cld"))
        self.profiles["cloud"][n] = data
        self.write_profiles("Cloud profile saved.")
        for w in [self.cld_name, self.cld_buck, self.cld_region,
                  self.cld_endpoint, self.cld_user, self.cld_pass]:
            w.clear()

    # ── Open / Save edit panels ────────────────────────────────────────
    def open_net_edit(self):
        if not self.net_list.currentItem():
            return
        n = self.net_list.currentItem().text()
        p = self.profiles["network"][n]
        self.currently_editing = {"category": "network", "name": n}
        self.net_e_name.setText(f"✏  Modifying: {n}")
        self.net_e_proto.setCurrentIndex(
            self.net_e_proto.findText(p.get("protocol", "")))
        self.net_e_path.setText(p.get("path", ""))
        self.net_e_domain.setText(p.get("domain", ""))
        self.net_e_user.setText(p.get("username", ""))
        self.net_e_pass.setText(self.decrypt_pw(p.get("password", "")))
        self.populate_source_data("net_e", p)
        self.populate_hooks_data("net_e", p)
        self.populate_encryption_data("net_e", p)
        self.populate_compression_data("net_e", p)
        self.populate_notification_data("net_e", p)
        self.net_e_err.hide()
        self.net_edit_grp.show()

    def save_net_edit(self):
        if not self.validate_inputs(
                [(self.net_e_path, "Remote Share Path")], self.net_e_err):
            return
        n = self.currently_editing["name"]
        if n:
            data = {
                "protocol": self.net_e_proto.currentText(),
                "path": self.net_e_path.text().strip(),
                "domain": self.net_e_domain.text().strip(),
                "username": self.net_e_user.text().strip(),
                "password": self.encrypt_pw(self.net_e_pass.text().strip()),
            }
            data.update(self.get_source_data("net_e"))
            data.update(self.get_hooks_data("net_e"))
            data.update(self.get_encryption_data("net_e"))
            data.update(self.get_compression_data("net_e"))
            data.update(self.get_notification_data("net_e"))
            self.profiles["network"][n].update(data)
            self.write_profiles(f"Updated '{n}'.")
            self.net_edit_grp.hide()

    def open_loc_edit(self):
        if not self.loc_list.currentItem():
            return
        n = self.loc_list.currentItem().text()
        p = self.profiles["local"][n]
        self.currently_editing = {"category": "local", "name": n}
        self.loc_e_name.setText(f"✏  Modifying: {n}")
        self.loc_e_path.setText(p.get("path", ""))
        self.populate_source_data("loc_e", p)
        self.populate_hooks_data("loc_e", p)
        self.populate_encryption_data("loc_e", p)
        self.populate_compression_data("loc_e", p)
        self.populate_notification_data("loc_e", p)
        self.loc_e_err.hide()
        self.loc_edit_grp.show()

    def save_loc_edit(self):
        if not self.validate_inputs(
                [(self.loc_e_path, "Destination Path")], self.loc_e_err):
            return
        n = self.currently_editing["name"]
        if n:
            data = {"path": self.loc_e_path.text().strip()}
            data.update(self.get_source_data("loc_e"))
            data.update(self.get_hooks_data("loc_e"))
            data.update(self.get_encryption_data("loc_e"))
            data.update(self.get_compression_data("loc_e"))
            data.update(self.get_notification_data("loc_e"))
            self.profiles["local"][n].update(data)
            self.write_profiles(f"Updated '{n}'.")
            self.loc_edit_grp.hide()

    def open_usb_edit(self):
        if not self.usb_list.currentItem():
            return
        n = self.usb_list.currentItem().text()
        p = self.profiles["usb"][n]
        self.currently_editing = {"category": "usb", "name": n}
        self.usb_e_name.setText(f"✏  Modifying: {n}")
        self.usb_e_path.setText(p.get("path", ""))
        self.populate_source_data("usb_e", p)
        self.populate_hooks_data("usb_e", p)
        self.populate_encryption_data("usb_e", p)
        self.populate_compression_data("usb_e", p)
        self.populate_notification_data("usb_e", p)
        self.usb_e_err.hide()
        self.usb_edit_grp.show()

    def save_usb_edit(self):
        if not self.validate_inputs(
                [(self.usb_e_path, "USB Mount Path")], self.usb_e_err):
            return
        n = self.currently_editing["name"]
        if n:
            data = {"path": self.usb_e_path.text().strip()}
            data.update(self.get_source_data("usb_e"))
            data.update(self.get_hooks_data("usb_e"))
            data.update(self.get_encryption_data("usb_e"))
            data.update(self.get_compression_data("usb_e"))
            data.update(self.get_notification_data("usb_e"))
            self.profiles["usb"][n].update(data)
            self.write_profiles(f"Updated '{n}'.")
            self.usb_edit_grp.hide()

    def open_cloud_edit(self):
        if not self.cld_list.currentItem():
            return
        n = self.cld_list.currentItem().text()
        p = self.profiles["cloud"][n]
        self.currently_editing = {"category": "cloud", "name": n}
        self.cld_e_name.setText(f"✏  Modifying: {n}")
        self.cld_e_prov.setCurrentIndex(
            self.cld_e_prov.findText(p.get("provider", "")))
        self.cld_e_buck.setText(p.get("bucket", ""))
        self.cld_e_region.setText(p.get("region", ""))
        self.cld_e_endpoint.setText(p.get("endpoint_url", ""))
        self.cld_e_user.setText(p.get("access_key", ""))
        self.cld_e_pass.setText(self.decrypt_pw(p.get("secret_key", "")))
        _update_cloud_fields(
            p.get("provider", "AWS S3"),
            self.cld_e_region_widget, self.cld_e_endpoint_widget,
            self.cld_e_key_lbl, self.cld_e_user,
            self.cld_e_secret_lbl, self.cld_e_pass)
        self.populate_source_data("cld_e", p)
        self.populate_notification_data("cld_e", p)
        self.cld_e_err.hide()
        self.cld_edit_grp.show()

    def save_cloud_edit(self):
        if not self.validate_inputs(
                [(self.cld_e_buck, "Bucket Name")], self.cld_e_err):
            return
        n = self.currently_editing["name"]
        if n:
            data = {
                "provider": self.cld_e_prov.currentText(),
                "bucket": self.cld_e_buck.text().strip(),
                "region": self.cld_e_region.text().strip(),
                "endpoint_url": self.cld_e_endpoint.text().strip(),
                "access_key": self.cld_e_user.text().strip(),
                "secret_key": self.encrypt_pw(self.cld_e_pass.text().strip()),
            }
            data.update(self.get_source_data("cld_e"))
            data.update(self.get_notification_data("cld_e"))
            self.profiles["cloud"][n].update(data)
            self.write_profiles(f"Updated '{n}'.")
            self.cld_edit_grp.hide()

    # ── SFTP save / edit ───────────────────────────────────────────────
    def _browse_file(self, widget, title, name_filter):
        path = self._run_custom_file_dialog(
            title, widget.text() or "/", name_filter or "All (*)",
            is_folder=False)
        if path:
            widget.setText(path)

    def _sftp_test_connection(self, host_w, port_w, user_w, auth_w,
                               key_w, pass_w, rpath_w, err_lbl):
        """Non-blocking SSH connectivity + remote mkdir test."""
        import shutil as _sh
        host = host_w.text().strip()
        port = port_w.text().strip() or "22"
        user = user_w.text().strip()
        auth = auth_w.currentText()
        kfile = key_w.text().strip()
        pw = pass_w.text().strip() if hasattr(pass_w, "text") else ""
        rpath = rpath_w.text().strip() or "/backup"

        if not host or not user:
            err_lbl.setText("\u26a0  Enter hostname and username before testing.")
            err_lbl.setStyleSheet("color: #f59e0b; font-weight: bold;")
            err_lbl.show()
            return

        if "Password" in auth:
            if not _sh.which("sshpass"):
                err_lbl.setText(
                    "\u274c  Password auth requires sshpass  "
                    "(sudo pacman -S sshpass)")
                err_lbl.setStyleSheet("color: #ef4444; font-weight: bold;")
                err_lbl.show()
                return
            if not pw:
                err_lbl.setText("\u274c  Enter a password before testing.")
                err_lbl.setStyleSheet("color: #ef4444; font-weight: bold;")
                err_lbl.show()
                return

        err_lbl.setText("\U0001f50c  Testing connection\u2026")
        err_lbl.setStyleSheet("color: #0ea5e9; font-weight: bold;")
        err_lbl.show()

        def _run():
            try:
                ssh_base = (
                    f"ssh -p {port} -o StrictHostKeyChecking=no "
                    f"-o ConnectTimeout=8 "
                    f"-o BatchMode={'yes' if 'SSH Key' in auth else 'no'}")
                if "SSH Key" in auth and kfile:
                    import os as _os
                    if _os.path.exists(kfile):
                        _os.chmod(kfile, 0o600)
                    ssh_base += f" -i '{kfile}'"
                prefix = f"sshpass -p '{pw}' " if "Password" in auth else ""
                cmd = (f"{prefix}{ssh_base} {user}@{host} "
                       f"'mkdir -p {rpath} && echo ARCHVAULT_OK'")
                r = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=14)
                out = (r.stdout + r.stderr).strip()
                if r.returncode == 0 and "ARCHVAULT_OK" in out:
                    err_lbl.setText(
                        f"\u2705  Connected  \u2014  {user}@{host}:{port}"
                        f"  |  {rpath} is ready")
                    err_lbl.setStyleSheet("color: #10b981; font-weight: bold;")
                else:
                    snippet = out.replace("\n", " ")[:110] or f"exit {r.returncode}"
                    err_lbl.setText(f"\u274c  {snippet}")
                    err_lbl.setStyleSheet("color: #ef4444; font-weight: bold;")
            except subprocess.TimeoutExpired:
                err_lbl.setText(
                    "\u274c  Timed out \u2014 check hostname and port")
                err_lbl.setStyleSheet("color: #ef4444; font-weight: bold;")
            except Exception as exc:
                err_lbl.setText(f"\u274c  {exc}")
                err_lbl.setStyleSheet("color: #ef4444; font-weight: bold;")

        threading.Thread(target=_run, daemon=True).start()

    def save_sftp_profile(self):
        if not self.validate_inputs(
                [(self.sftp_name, "Profile Name"),
                 (self.sftp_host, "Hostname"),
                 (self.sftp_user, "Username"),
                 (self.sftp_rpath, "Remote Path")], self.sftp_err):
            return
        n = self.sftp_name.text().strip()
        tm = ("rsync_ssh" if "rsync" in self.sftp_transfer_mode.currentText()
              else "sshfs")
        auth = ("SSH Key" if "SSH Key" in self.sftp_auth.currentText()
                else "Password")
        data = {
            "hostname": self.sftp_host.text().strip(),
            "port": self.sftp_port.text().strip() or "22",
            "username": self.sftp_user.text().strip(),
            "auth_method": auth,
            "password": self.encrypt_pw(self.sftp_pass.text().strip()),
            "key_file": self.sftp_key.text().strip(),
            "remote_path": self.sftp_rpath.text().strip(),
            "transfer_mode": tm,
        }
        data.update(self.get_source_data("sftp"))
        data.update(self.get_hooks_data("sftp"))
        self.profiles.setdefault("sftp", {})[n] = data
        self.write_profiles(f"SFTP profile '{n}' saved.")
        for w in [self.sftp_name, self.sftp_host, self.sftp_user,
                  self.sftp_pass, self.sftp_key, self.sftp_rpath]:
            w.clear()

    def open_sftp_edit(self):
        if not self.sftp_list.currentItem():
            return
        n = self.sftp_list.currentItem().text()
        p = self.profiles["sftp"][n]
        self.currently_editing = {"category": "sftp", "name": n}
        self.sftp_e_name_lbl.setText(f"✏  Modifying: {n}")
        self.sftp_e_host.setText(p.get("hostname", ""))
        self.sftp_e_port.setText(p.get("port", "22"))
        self.sftp_e_user.setText(p.get("username", ""))
        saved_auth = p.get("auth_method", "SSH Key")
        for i in range(self.sftp_e_auth.count()):
            if saved_auth in self.sftp_e_auth.itemText(i):
                self.sftp_e_auth.setCurrentIndex(i); break
        self._sftp_e_key_widget.setVisible("SSH Key" in self.sftp_e_auth.currentText())
        self._sftp_e_pw_widget.setVisible("Password" in self.sftp_e_auth.currentText())
        self.sftp_e_pass.setText(self.decrypt_pw(p.get("password", "")))
        self.sftp_e_key.setText(p.get("key_file", ""))
        self.sftp_e_rpath.setText(p.get("remote_path", ""))
        saved_tm = p.get("transfer_mode", "rsync_ssh")
        for i in range(self.sftp_e_transfer_mode.count()):
            if ("rsync" in self.sftp_e_transfer_mode.itemText(i)) == (saved_tm == "rsync_ssh"):
                self.sftp_e_transfer_mode.setCurrentIndex(i); break
        self.populate_source_data("sftp_e", p)
        self.populate_hooks_data("sftp_e", p)
        self.sftp_e_err.hide()
        self.sftp_edit_grp.show()

    def save_sftp_edit(self):
        if not self.validate_inputs(
                [(self.sftp_e_host, "Hostname"),
                 (self.sftp_e_rpath, "Remote Path")], self.sftp_e_err):
            return
        n = self.currently_editing["name"]
        if n:
            tm = ("rsync_ssh"
                  if "rsync" in self.sftp_e_transfer_mode.currentText()
                  else "sshfs")
            auth = ("SSH Key"
                    if "SSH Key" in self.sftp_e_auth.currentText()
                    else "Password")
            data = {
                "hostname": self.sftp_e_host.text().strip(),
                "port": self.sftp_e_port.text().strip() or "22",
                "username": self.sftp_e_user.text().strip(),
                "auth_method": auth,
                "password": self.encrypt_pw(self.sftp_e_pass.text().strip()),
                "key_file": self.sftp_e_key.text().strip(),
                "remote_path": self.sftp_e_rpath.text().strip(),
                "transfer_mode": tm,
            }
            data.update(self.get_source_data("sftp_e"))
            data.update(self.get_hooks_data("sftp_e"))
            self.profiles["sftp"][n].update(data)
            self.write_profiles(f"Updated '{n}'.")
            self.sftp_edit_grp.hide()
