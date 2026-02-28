import os
import shlex
import tempfile
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import Qt
from ui_widgets import confirm_action

VERSION = "v5.0.0-beta"


class EngineRestoreMixin:

    def start_restore_process(self):
        source_raw = self.rst_source.text().strip()
        engine = self.rst_engine.currentText()

        if not source_raw or not os.path.exists(source_raw):
            return QMessageBox.warning(
                self, "Error",
                "Please select a valid local or mounted backup file.")

        safe_source = shlex.quote(source_raw)

        is_selective = (hasattr(self, 'rst_toggle_selective')
                        and self.rst_toggle_selective.isChecked())
        if is_selective and "Bare Metal" not in engine:
            return self.start_selective_restore(safe_source, engine)

        # ── Confirmation — extra-scary for bare metal ─────────────────────
        if "Bare Metal" in engine:
            dest_drive = self.rst_dest_drive.currentText().split(" ")[0]
            if not confirm_action(
                    self, "⚠  Bare Metal Restore",
                    "This will COMPLETELY OVERWRITE the target drive "
                    "with a disk image. All existing data on the "
                    "drive will be destroyed.",
                    detail=f"Source: {source_raw}\n"
                           f"Target drive: {dest_drive}\n"
                           f"Engine: {engine}\n\n"
                           f"This operation is irreversible.",
                    confirm_text="Overwrite Drive",
                    destructive=True, icon_char="💀"):
                return
        else:
            dest_path = (self.rst_dest_path.text().strip()
                         if hasattr(self, 'rst_dest_path') else "/")
            if not confirm_action(
                    self, "Start Restore",
                    "This will restore files from a backup. "
                    "Existing files at the destination may be overwritten.",
                    detail=f"Source: {source_raw}\n"
                           f"Destination: {dest_path}\n"
                           f"Engine: {engine}",
                    confirm_text="Start Restore", destructive=True,
                    icon_char="▼"):
                return

        self.active_job_type = "restore"
        self.btn_run_restore.setEnabled(False)
        if hasattr(self, 'btn_restore_pause'):
            self.btn_restore_pause.setEnabled(True)
        if hasattr(self, 'btn_restore_stop'):
            self.btn_restore_stop.setEnabled(True)
        self.console.clear()
        self.register_job("Restore", source_raw)

        # ── Update restore progress card — preparing ──────────────────────
        if hasattr(self, '_restore_status_label'):
            self._restore_status_label.setText("⏳  Preparing restore…")
        if hasattr(self, '_restore_progress_bar'):
            self._restore_progress_bar.setRange(0, 0)   # indeterminate
        if hasattr(self, '_restore_stats_label'):
            self._restore_stats_label.setText("")
        if hasattr(self, '_restore_dir_label'):
            self._restore_dir_label.setText("")

        if "Bare Metal" in engine:
            dest_raw = self.rst_dest_drive.currentText().split(" ")[0]
            if not dest_raw:
                return self.handle_finished(1, 0)
            safe_dest = shlex.quote(dest_raw)
            engine_cmd = (
                f"gunzip -c {safe_source} | dd of={safe_dest} "
                f"bs=4M status=progress & CMD_PID=$!; wait $CMD_PID")
            engine_label = "bare-metal image"
            # Pre-flight
            self._preflight_size(source_raw, None)
            if hasattr(self, '_restore_dir_label'):
                self._restore_dir_label.setText(
                    f"📂  Restoring to device:  {dest_raw}")
            if hasattr(self, '_restore_status_label'):
                self._restore_status_label.setText(
                    "▶  Streaming bare-metal image restore…")
        else:
            dest_raw = self.rst_dest_path.text().strip()
            safe_dest = shlex.quote(dest_raw)
            os.makedirs(dest_raw, exist_ok=True)

            # Pre-flight size
            self._preflight_size(source_raw, dest_raw)
            if hasattr(self, '_restore_dir_label'):
                self._restore_dir_label.setText(
                    f"📂  Restoring to:  {dest_raw}")

            if "Ext4" in engine:
                engine_cmd = (
                    f"tar -xzf {safe_source} -C {safe_dest} "
                    f"--strip-components=0 & CMD_PID=$!; "
                    f"wait $CMD_PID")
                engine_label = "tar.gz extraction"
                if hasattr(self, '_restore_status_label'):
                    self._restore_status_label.setText(
                        "▶  Extracting tar.gz archive…")
            elif "Btrfs" in engine:
                engine_cmd = (
                    f"btrfs receive {safe_dest} -f {safe_source} "
                    f"& CMD_PID=$!; wait $CMD_PID")
                engine_label = "btrfs receive"
                if hasattr(self, '_restore_status_label'):
                    self._restore_status_label.setText(
                        "▶  Receiving btrfs snapshot…")

        bash_script = f"""#!/bin/bash
set -o pipefail

# ── Signal handler: user cancelled ────────────────────────────
trap 'echo "ERROR: Restore cancelled by user." >&2; kill -9 $CMD_PID 2>/dev/null; exit 130' SIGINT SIGTERM

# ── Restore engine ────────────────────────────────────────────
echo "Starting {engine_label} restore…" >&2
{engine_cmd}
RESTORE_STATUS=$?

# ── Final status ──────────────────────────────────────────────
if [ $RESTORE_STATUS -ne 0 ]; then
    echo "ERROR: Restore engine ({engine_label}) exited with status $RESTORE_STATUS." >&2
    echo "The restored data may be incomplete." >&2
fi
exit $RESTORE_STATUS
"""
        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())

    def start_selective_restore(self, safe_source, engine):
        dest_raw = self.rst_dest_path.text().strip()
        safe_dest = shlex.quote(dest_raw)
        os.makedirs(dest_raw, exist_ok=True)

        selected_files = []
        if hasattr(self, 'rst_file_tree'):
            for i in range(self.rst_file_tree.topLevelItemCount()):
                top = self.rst_file_tree.topLevelItem(i)
                top_name = top.text(0).strip()
                if not top_name or top_name.startswith("<"):
                    continue
                top_state = top.checkState(0)
                if top_state == Qt.CheckState.Checked:
                    selected_files.append(top_name)
                elif top_state == Qt.CheckState.PartiallyChecked:
                    for j in range(top.childCount()):
                        child = top.child(j)
                        if child.checkState(0) == Qt.CheckState.Checked:
                            selected_files.append(
                                f"{top_name}/{child.text(0).strip()}")

        if not selected_files:
            return QMessageBox.warning(
                self, "Error", "No files selected for restore.")

        self.active_job_type = "restore"
        self.btn_run_restore.setEnabled(False)
        if hasattr(self, 'btn_restore_pause'):
            self.btn_restore_pause.setEnabled(True)
        if hasattr(self, 'btn_restore_stop'):
            self.btn_restore_stop.setEnabled(True)
        self.register_job("Selective Restore", safe_source)

        # ── Update restore progress card ──────────────────────────────────
        if hasattr(self, '_restore_status_label'):
            self._restore_status_label.setText(
                f"▶  Selective restore — {len(selected_files)} item(s)…")
        if hasattr(self, '_restore_progress_bar'):
            self._restore_progress_bar.setRange(0, 0)   # indeterminate
        if hasattr(self, '_restore_stats_label'):
            self._restore_stats_label.setText("")
        if hasattr(self, '_restore_dir_label'):
            self._restore_dir_label.setText(
                f"📂  Restoring to:  {dest_raw}")

        # Pre-flight size (source is the archive file)
        src_raw = safe_source.strip("'\"")
        self._preflight_size(src_raw, dest_raw)

        # SECURE TEMP FILE
        fd, files_list_path = tempfile.mkstemp(prefix="archvault_restore_")
        with os.fdopen(fd, "w") as f:
            for file_path in selected_files:
                f.write(f"{file_path.lstrip('/')}\n")

        safe_files_list = shlex.quote(files_list_path)

        if "Ext4" in engine:
            engine_cmd = (
                f"tar -xzf {safe_source} -C {safe_dest} "
                f"--files-from={safe_files_list} "
                f"& CMD_PID=$!; wait $CMD_PID")
        elif "Btrfs" in engine:
            engine_cmd = "echo 'selective btrfs coming soon'"

        bash_script = f"""#!/bin/bash
set -o pipefail

# ── Signal handler: user cancelled ────────────────────────────
trap 'echo "ERROR: Selective restore cancelled by user." >&2; kill -9 $CMD_PID 2>/dev/null; rm -f {safe_files_list}; exit 130' SIGINT SIGTERM

# ── Selective restore engine ──────────────────────────────────
echo "Restoring {len(selected_files)} selected item(s)…" >&2
{engine_cmd}
STATUS=$?
rm -f {safe_files_list}

# ── Final status ──────────────────────────────────────────────
if [ $STATUS -ne 0 ]; then
    echo "ERROR: Selective restore failed with status $STATUS." >&2
    echo "Some files may not have been extracted." >&2
fi
exit $STATUS
"""
        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())
