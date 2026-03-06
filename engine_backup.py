import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timedelta
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import QProcessEnvironment
from ui_widgets import confirm_action

VERSION = "v5.0.3-beta"


class EngineBackupMixin:

    def start_backup_process(self):
        t_str = self.target_combo.currentText()
        if not t_str:
            return QMessageBox.warning(self, "Error", "No target selected.")

        cat_raw, name = t_str.split(": ", 1)
        cat = cat_raw.lower()
        prof = self.profiles[cat][name]
        engine = self.engine_combo.currentText()
        src = ("/" if prof.get("source_mode", "Full System") == "Full System"
               else prof.get("source_path", "/"))

        if not confirm_action(
                self, "Start Backup",
                f"Ready to back up to '{name}'?",
                detail=f"Target: {t_str}\n"
                       f"Engine: {engine}\n"
                       f"Source: {src}",
                confirm_text="Start Backup", icon_char="▲"):
            return

        self.active_job_type = "backup"
        self.btn_run_backup.setEnabled(False)
        self.btn_backup_stop.setEnabled(True)
        if hasattr(self, 'btn_backup_pause'):
            self.btn_backup_pause.setEnabled(True)
        self.console.clear()

        is_ext4 = "Ext4" in engine
        src_path = src

        self.register_job("Backup", t_str)
        self._last_backup_prof = prof
        self._last_backup_target_str = t_str

        # ── Update progress card — preparing ──────────────────────────────
        if hasattr(self, '_backup_status_label'):
            self._backup_status_label.setText("⏳  Preparing backup…")
        if hasattr(self, 'progress_bar'):
            self.progress_bar.setRange(0, 0)   # indeterminate
        if hasattr(self, 'progress_label'):
            self.progress_label.setText("")
        if hasattr(self, '_backup_dir_label'):
            self._backup_dir_label.setText(f"📂  Source:  {src_path}")

        if cat == "cloud":
            return self.start_cloud_backup(name, prof, t_str)

        # SECURE INPUT SANITIZATION
        safe_src = shlex.quote(src_path)
        safe_name = shlex.quote(name.replace(" ", "_"))
        safe_path = shlex.quote(prof.get('path', '/tmp'))

        if "rsync" in self.engine_combo.currentText().lower():
            if cat == "network":
                _mnt = tempfile.mkdtemp(prefix="archvault_nas_")
                _pw = self.decrypt_pw(prof.get("password", ""))
                _opts = f"username={prof.get('username','')},password={_pw}"
                if prof.get("domain"):
                    _opts += f",domain={prof.get('domain')}"
                _opts += ",noserverino,nocase,vers=3.0"

                _mnt_cmd = (
                    f"mount -t cifs -o {shlex.quote(_opts)} {safe_path} {_mnt}"
                    if "SMB" in prof.get("protocol", "SMB")
                    else f"mount -t nfs {safe_path} {_mnt}")
                _dest_base = f"{_mnt}/{safe_name}_incremental"
                _cleanup = (
                    f"umount -l {_mnt} >/dev/null 2>&1; "
                    f"rmdir {_mnt} >/dev/null 2>&1")
            else:
                _dest_base = f"{prof.get('path', '')}/{safe_name}_incremental"
                _mnt_cmd = f"mkdir -p {shlex.quote(_dest_base)}"
                _cleanup = ""

            # Pre-flight size
            self._preflight_size(src_path, prof.get('path', '/tmp'))

            if hasattr(self, '_backup_status_label'):
                self._backup_status_label.setText(
                    "▶  Streaming rsync incremental…")
            return self.start_rsync_backup(
                safe_src, shlex.quote(_dest_base), _mnt_cmd, _cleanup)

        # Standard Backup
        custom_name = (
            getattr(self, "settings", {})
            .get("backup_name_format", "ArchVault_%profile%_%datetime%")
            .replace("%profile%", name.replace(" ", "_"))
            .replace("%datetime%", datetime.now().strftime("%Y-%m-%d_%H%M")))
        safe_custom_name = shlex.quote(custom_name)

        if cat == "network":
            mnt = tempfile.mkdtemp(prefix="archvault_nas_")
            _pw = self.decrypt_pw(prof.get("password", ""))
            _opts = f"username={prof.get('username','')},password={_pw}"
            if prof.get("domain"):
                _opts += f",domain={prof.get('domain')}"
            _opts += ",noserverino,nocase,vers=3.0"

            mount_cmd = (
                f"mount -t cifs -o {shlex.quote(_opts)} {safe_path} {mnt}"
                if "SMB" in prof.get("protocol", "SMB")
                else f"mount -t nfs {safe_path} {mnt}")
            out_base = f"{mnt}/{custom_name}"
            cleanup_cmd = (
                f"umount -l {mnt} >/dev/null 2>&1; "
                f"rmdir {mnt} >/dev/null 2>&1")
        elif cat == "sftp":
            host = shlex.quote(prof.get("hostname", ""))
            port = shlex.quote(prof.get("port", "22"))
            user = shlex.quote(prof.get("username", ""))
            rpath = shlex.quote(
                prof.get("remote_path", "/backup").rstrip("/"))

            mnt = tempfile.mkdtemp(prefix="archvault_sftp_")
            opts = shlex.quote(
                f"StrictHostKeyChecking=accept-new,"
                f"port={prof.get('port','22')},reconnect")

            mount_cmd = (
                f"sshpass -e sshfs {user}@{host}:{rpath} {mnt} -o {opts}")
            out_base = f"{mnt}/{custom_name}"
            cleanup_cmd = (
                f"fusermount -u {mnt} >/dev/null 2>&1; "
                f"rmdir {mnt} >/dev/null 2>&1")
        else:
            mount_cmd = f"mkdir -p {safe_path}"
            out_base = f"{prof['path']}/{custom_name}"
            cleanup_cmd = ""

        safe_out_file = shlex.quote(
            f"{out_base}{'.tar.gz' if is_ext4 else '.btrfs'}")
        self._last_backup_file = safe_out_file

        # Pre-flight size calculation
        dest_dir = prof.get('path', '/tmp')
        self._preflight_size(src_path, dest_dir)

        engine_cmd = (
            f"tar --checkpoint=10000 -cpzf {safe_out_file} {safe_src} "
            f"& CMD_PID=$!; wait $CMD_PID"
            if is_ext4
            else
            f"btrfs subvolume snapshot -r {safe_src} /.archvault_snapshot && "
            f"btrfs send /.archvault_snapshot -f {safe_out_file} "
            f"& CMD_PID=$!; wait $CMD_PID"
        )

        bash_script = f"""#!/bin/bash
set -o pipefail

# ── Signal handler: user cancelled ────────────────────────────
trap 'echo "ERROR: Operation cancelled by user." >&2; kill -9 $CMD_PID 2>/dev/null; exit 130' SIGINT SIGTERM

# ── Mount phase ───────────────────────────────────────────────
echo "Mounting destination…" >&2
{mount_cmd}
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to mount network share or prepare destination." >&2
    echo "Check that the server is reachable, credentials are correct," >&2
    echo "and the share path exists." >&2
    exit 10
fi

# ── Backup engine ─────────────────────────────────────────────
{engine_cmd}
SEND_STATUS=$?
btrfs subvolume delete /.archvault_snapshot >/dev/null 2>&1 || true

# ── Cleanup mount ─────────────────────────────────────────────
{cleanup_cmd}

# ── Final status ──────────────────────────────────────────────
if [ $SEND_STATUS -ne 0 ]; then
    echo "ERROR: Backup engine exited with status $SEND_STATUS." >&2
    echo "The archive may be incomplete or corrupted." >&2
fi
exit $SEND_STATUS
"""

        # INJECT ENVIRONMENT VARIABLE SECURELY
        env = QProcessEnvironment.systemEnvironment()
        if cat == "sftp":
            env.insert("SSHPASS", self.decrypt_pw(prof.get("password", "")))
        self.process.setProcessEnvironment(env)

        # Update status label — streaming
        if hasattr(self, '_backup_status_label'):
            engine_name = "tar.gz" if is_ext4 else "btrfs send"
            self._backup_status_label.setText(
                f"▶  Streaming via {engine_name}…")

        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())

    def start_rsync_backup(self, safe_src, safe_dest_base, mount_cmd,
                           cleanup_cmd):
        snap_name = shlex.quote(
            datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        bash_script = f"""#!/bin/bash
set -o pipefail

# ── Signal handler: user cancelled ────────────────────────────
trap 'echo "ERROR: Operation cancelled by user." >&2; kill -9 $CMD_PID 2>/dev/null; exit 130' SIGINT SIGTERM

# ── Mount phase ───────────────────────────────────────────────
echo "Mounting destination…" >&2
{mount_cmd}
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to mount network share or prepare destination." >&2
    echo "Check that the server is reachable, credentials are correct," >&2
    echo "and the share path exists." >&2
    exit 10
fi

# ── Rsync incremental ────────────────────────────────────────
mkdir -p {safe_dest_base}/{snap_name}
LINK=""
if [ -L {safe_dest_base}/latest ]; then
    LINK="--link-dest=$(readlink -f {safe_dest_base}/latest)"
fi

rsync -avz --delete --info=progress2 $LINK {safe_src}/ {safe_dest_base}/{snap_name}/ & CMD_PID=$!
wait $CMD_PID
RSYNC_STATUS=$?

if [ $RSYNC_STATUS -eq 0 ]; then
    ln -sfn {safe_dest_base}/{snap_name} {safe_dest_base}/latest
elif [ $RSYNC_STATUS -eq 23 ]; then
    echo "WARNING: Rsync completed with partial transfer (some files could not be read)." >&2
elif [ $RSYNC_STATUS -eq 24 ]; then
    echo "WARNING: Some source files vanished during transfer." >&2
else
    echo "ERROR: Rsync failed with status $RSYNC_STATUS." >&2
fi

# ── Cleanup ───────────────────────────────────────────────────
{cleanup_cmd}
exit $RSYNC_STATUS
"""
        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())

    def run_retention_pruning(self, dest_dir, profile_name, retention_days):
        pass

    def _gpg_encrypt_archive(self, archive_path, prof):
        passphrase = self.decrypt_pw(prof.get("encrypt_pass", ""))
        if not passphrase:
            return
        try:
            subprocess.run(
                ["gpg", "--batch", "--yes", "--symmetric",
                 "--cipher-algo", "AES256", "--passphrase-fd", "0",
                 "--output", f"{archive_path}.gpg", archive_path],
                input=passphrase.encode(), capture_output=True, timeout=3600
            )
            os.remove(archive_path)
        except Exception as e:
            self.log(f"ENCRYPT ERROR: {e}")
