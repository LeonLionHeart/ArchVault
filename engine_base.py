import os
import re
import signal
import shlex
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtWidgets import QMessageBox
from ui_widgets import confirm_action

VERSION = "v5.0.0-beta"


def _human_size(nbytes):
    """Return a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:,.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:,.1f} PB"


# ═══════════════════════════════════════════════════════════════════════════
#  SIGNAL NUMBER → NAME MAP  (Linux x86_64)
# ═══════════════════════════════════════════════════════════════════════════
_SIGNAL_NAMES = {
    1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL",
    6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 9: "SIGKILL",
    11: "SIGSEGV", 13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM",
    24: "SIGXCPU", 25: "SIGXFSZ",
}


class EngineBaseMixin:
    # ─────────────────────────────────────────────────────────────────────────
    # JOB TRACKER & LOGGING
    # ─────────────────────────────────────────────────────────────────────────
    def register_job(self, op_type, target):
        fmt = ("%Y-%m-%d %I:%M:%S %p"
               if getattr(self, "settings", {}).get("time_format") == "12 Hour"
               else "%Y-%m-%d %H:%M:%S")
        self.current_job_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self.current_job_log = []
        self._stderr_error_lines = []
        self._user_cancelled = False
        job = {
            "id": self.current_job_id,
            "time": datetime.now().strftime(fmt),
            "type": op_type, "target": target,
            "status": "Running",
            "description": "Stream active..."
        }
        self.job_history.append(job)
        self.write_jobs()

    def update_job_state(self, status=None, desc=None, append_log=False,
                         pid=None):
        if getattr(self, 'current_job_id', None):
            for j in self.job_history:
                if j["id"] == self.current_job_id:
                    if status:
                        j["status"] = status
                    if desc:
                        j["description"] = desc
                    if append_log:
                        j["log"] = "\n".join(self.current_job_log[-100:])
                    if pid:
                        j["pid"] = pid
                    break
            self.write_jobs()

    def log(self, m):
        fmt = ('%I:%M:%S %p'
               if getattr(self, "settings", {}).get("time_format") == "12 Hour"
               else '%H:%M:%S')
        log_str = f"[{datetime.now().strftime(fmt)}] {m}"
        self.console.append(log_str)
        self.console.verticalScrollBar().setValue(
            self.console.verticalScrollBar().maximum())

        if hasattr(self, 'update_status_strip'):
            self.update_status_strip(log_str)

        if hasattr(self, 'current_job_log'):
            self.current_job_log.append(log_str)
        if hasattr(self, '_stderr_error_lines') and any(
            kw in m.lower() for kw in (
                "error", "failed", "denied", "no space",
                "broken pipe", "corrupt", "warning", "skipping",
                "mount", "permission", "timeout", "refused",
                "not found", "no such", "critical"
            )
        ):
            self._stderr_error_lines.append(log_str)

    # ─────────────────────────────────────────────────────────────────────────
    # STDOUT / STDERR → PROGRESS
    # ─────────────────────────────────────────────────────────────────────────
    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode().strip()
        if data:
            for line in data.split('\n'):
                self.log(line)
                self._parse_dir_from_line(line)

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode()
        if not data:
            return
        for line in data.replace('\r\n', '\n').split('\r'):
            clean_line = line.strip()
            if clean_line:
                self._emit_progress(clean_line)
                self._parse_dir_from_line(clean_line)
                if ("copied" in clean_line
                        or "records processed" in clean_line):
                    self.log(f"PROGRESS: {clean_line}")
                else:
                    self.log(f"SYS: {clean_line}")

    def _emit_progress(self, line: str):
        """Parse progress from pv / tar / rsync / rclone and route to the
        correct tab's progress widgets based on active_job_type."""
        m_pv = re.match(
            r"(\d+)%\|([\d.]+\s*\w+)\|([\d.]+\s*[\w./]+)\|(\S+)",
            line.strip())
        m_rsync = re.search(
            r"([\d.]+\w+)\s+(\d+)%\s+([\d.]+\w+/s)\s+(\d+:\d+:\d+)", line)
        m_rclone = re.search(
            r"Transferred:.+?([\d.]+\s*\w+)\s*/\s*([\d.]+\s*\w+),"
            r"\s*(\d+)%,\s*([\d.]+\s*\w+/s),\s*ETA\s*(\S+)", line)
        m_tar = re.search(r"(\d[\d,]*)\s+records processed", line)

        if m_pv:
            pct = int(m_pv.group(1))
            txt = (f"  ⚡  {m_pv.group(1)}%  ·  {m_pv.group(2)} transferred"
                   f"  ·  {m_pv.group(3)}  ·  ETA {m_pv.group(4)}")
            self._update_progress_ui(pct, txt)
        elif m_rsync:
            pct = int(m_rsync.group(2))
            txt = (f"  ⚡  {m_rsync.group(2)}%  ·  "
                   f"{m_rsync.group(1)} transferred"
                   f"  ·  {m_rsync.group(3)}  ·  ETA {m_rsync.group(4)}")
            self._update_progress_ui(pct, txt)
        elif m_rclone:
            pct = int(m_rclone.group(3))
            txt = (f"  ☁  {m_rclone.group(3)}%  ·  {m_rclone.group(1)} / "
                   f"{m_rclone.group(2)}  ·  {m_rclone.group(4)}  ·  "
                   f"ETA {m_rclone.group(5)}")
            self._update_progress_ui(pct, txt)
        elif m_tar:
            n = int(m_tar.group(1).replace(",", ""))
            self._update_progress_ui(
                -1,  # indeterminate
                f"  ☕  {n:,} tar records archived")

    def _update_progress_ui(self, pct, label_text):
        """Route progress percentage and label to the correct tab."""
        job = getattr(self, 'active_job_type', None)

        if job == "restore":
            bar = getattr(self, '_restore_progress_bar', None)
            lbl = getattr(self, '_restore_stats_label', None)
            status = getattr(self, '_restore_status_label', None)
        else:
            bar = getattr(self, 'progress_bar', None)
            lbl = getattr(self, 'progress_label', None)
            status = getattr(self, '_backup_status_label', None)

        if bar:
            if pct < 0:
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 100)
                bar.setValue(pct)
        if lbl:
            lbl.setText(label_text)
        if status and pct >= 0:
            status.setText(f"Streaming — {pct}% complete")

    def _parse_dir_from_line(self, line: str):
        """Extract a directory/file path from output and update the
        dir label."""
        job = getattr(self, 'active_job_type', None)
        if not job:
            return

        path = None
        m = re.match(
            r"^((?:/|[a-zA-Z0-9._])[\w./_\-]+(?:/[\w./_\-]*)*)$",
            line.strip())
        if m and len(m.group(1)) > 3:
            path = m.group(1)

        if not path:
            m2 = re.match(r"^([\w._\-]+/[\w._/\-]+)$", line.strip())
            if m2 and len(m2.group(1)) > 5:
                path = m2.group(1)

        if not path:
            return

        dir_part = os.path.dirname(path) or path
        if len(dir_part) > 120:
            dir_part = "…" + dir_part[-117:]

        if job == "restore":
            lbl = getattr(self, '_restore_dir_label', None)
            if lbl:
                lbl.setText(f"📂  Restoring:  {dir_part}")
        else:
            lbl = getattr(self, '_backup_dir_label', None)
            if lbl:
                lbl.setText(f"📂  Archiving:  {dir_part}")

    # ─────────────────────────────────────────────────────────────────────────
    # PRE-FLIGHT SIZE CALCULATION
    # ─────────────────────────────────────────────────────────────────────────
    def _preflight_size(self, source_path, dest_path=None):
        """Calculate source size and destination free space."""
        src_bytes = 0
        dest_free = 0
        try:
            if os.path.isfile(source_path):
                src_bytes = os.path.getsize(source_path)
            elif os.path.isdir(source_path):
                result = subprocess.run(
                    ["du", "-sb", source_path],
                    capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    src_bytes = int(result.stdout.split()[0])
        except Exception:
            pass

        try:
            if dest_path and os.path.exists(dest_path):
                usage = shutil.disk_usage(dest_path)
                dest_free = usage.free
        except Exception:
            pass

        parts = []
        if src_bytes > 0:
            parts.append(f"Source: {_human_size(src_bytes)}")
        if dest_free > 0:
            parts.append(f"Destination free: {_human_size(dest_free)}")
        size_text = "  ·  ".join(parts) if parts else ""

        job = getattr(self, 'active_job_type', None)
        if job == "restore":
            lbl = getattr(self, '_restore_size_label', None)
        else:
            lbl = getattr(self, '_backup_size_label', None)
        if lbl and size_text:
            lbl.setText(f"💾  {size_text}")

        return src_bytes, dest_free

    # ─────────────────────────────────────────────────────────────────────────
    # PAUSE / STOP
    # ─────────────────────────────────────────────────────────────────────────
    def toggle_pause(self):
        _BTN_RESUME = (
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #2563eb,stop:1 #3b82f6); color: #ffffff; "
            "font-weight: 700; font-size: 12px; padding: 9px 20px; "
            "border-radius: 8px; border: none; letter-spacing: 0.3px;")
        _BTN_PAUSE = (
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #f59e0b,stop:1 #fbbf24); color: #1c1917; "
            "font-weight: 700; font-size: 12px; padding: 9px 20px; "
            "border-radius: 8px; border: none; letter-spacing: 0.3px;")

        if self.process.state() == QProcess.ProcessState.Running:
            pid = self.process.processId()
            if not self.is_paused:
                subprocess.run(["pkill", "-STOP", "-P", str(pid)])
                os.kill(pid, signal.SIGSTOP)
                self.is_paused = True
                self.update_job_state("Stalled", "User paused stream.")
                self.log("WARNING: Stream PAUSED.")
                self._set_status_label("⏸  Paused")
                for btn_name in ('btn_backup_pause', 'btn_restore_pause'):
                    btn = getattr(self, btn_name, None)
                    if btn:
                        btn.setText("▶  Resume")
                        btn.setStyleSheet(_BTN_RESUME)
            else:
                os.kill(pid, signal.SIGCONT)
                subprocess.run(["pkill", "-CONT", "-P", str(pid)])
                self.is_paused = False
                self.update_job_state("Running", "Stream active...")
                self.log("INFO: Stream RESUMED.")
                self._set_status_label("▶  Streaming…")
                for btn_name in ('btn_backup_pause', 'btn_restore_pause'):
                    btn = getattr(self, btn_name, None)
                    if btn:
                        btn.setText("⏸  Pause")
                        btn.setStyleSheet(_BTN_PAUSE)
        elif (hasattr(self, '_cloud_worker') and self._cloud_worker
              and self._cloud_worker.isRunning()):
            self.log("WARNING: Cloud uploads cannot be paused mid-transfer.")

    def _set_status_label(self, text):
        """Set the status label on the correct tab's progress card."""
        job = getattr(self, 'active_job_type', None)
        if job == "restore":
            lbl = getattr(self, '_restore_status_label', None)
        else:
            lbl = getattr(self, '_backup_status_label', None)
        if lbl:
            lbl.setText(text)

    def stop_process(self):
        """Terminate the running process — flag as user-cancelled."""
        if self.process.state() != QProcess.ProcessState.Running:
            return
        job_type = getattr(self, 'active_job_type', 'operation')
        if not confirm_action(
                self, f"Cancel {job_type.title()}",
                f"Are you sure you want to cancel the running "
                f"{job_type}?",
                detail="The process will be killed immediately. "
                       "Any partially written data may be incomplete.",
                confirm_text=f"Cancel {job_type.title()}",
                destructive=True, icon_char="⏹"):
            return
        self._user_cancelled = True
        self.log("INFO: Cancellation requested — stopping stream…")
        pid = self.process.processId()
        if self.is_paused:
            os.kill(pid, signal.SIGCONT)
            subprocess.run(["pkill", "-CONT", "-P", str(pid)])
        subprocess.run(["pkill", "-9", "-P", str(pid)])
        self.process.terminate()
        if (hasattr(self, '_cloud_worker') and self._cloud_worker
                and self._cloud_worker.isRunning()):
            self.log("INFO: Aborting cloud upload…")
            self._cloud_worker.abort()

    def start_validation(self):
        target_raw = self.val_path_input.text().strip()
        if not target_raw or not os.path.exists(target_raw):
            return QMessageBox.warning(
                self, "Error", "Please select a valid backup file.")

        target_file = shlex.quote(target_raw)
        self.active_job_type = "validation"
        self.console.clear()

        is_ext4 = target_raw.endswith(".tar.gz")
        check_cmd = (
            f"tar -tzf {target_file} > /dev/null" if is_ext4
            else f"btrfs receive --dump -f {target_file} > /dev/null")

        bash_script = f"""#!/bin/bash
        echo "--- Starting Integrity Check on {target_file} ---"
        {check_cmd}
        if [ $? -eq 0 ]; then
            echo "SUCCESS: File is fully intact."
            exit 0
        else
            echo "CRITICAL: Stream corruption detected!"
            exit 1
        fi
        """
        self.process.start("bash", ["-c", bash_script])

    # ─────────────────────────────────────────────────────────────────────────
    # DIAGNOSTICS & FINISH ROUTINE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_failure_reason(self, exit_code, job_type):
        """Build a human-readable failure description from all available
        context: cancellation flag, captured stderr lines, and exit code."""

        # ── 1. User explicitly cancelled ──────────────────────────────────
        if getattr(self, '_user_cancelled', False):
            return {
                "short": "Cancelled by user",
                "detail": "The operation was manually stopped before "
                          "it could complete.",
                "status_label": "⏹  Cancelled by user",
                "status_strip": "⏹  Operation cancelled by user."
            }

        # ── 2. Try to extract a reason from captured stderr lines ─────────
        stderr = getattr(self, '_stderr_error_lines', [])
        # Flatten to a single searchable block (last 15 lines)
        stderr_text = "\n".join(stderr[-15:]).lower()

        # Pattern-match common Linux/filesystem errors
        if "permission denied" in stderr_text:
            reason = (
                "Permission denied — the process could not read the "
                "source or write to the destination. Check that ArchVault "
                "is running as root and that the target path is writable.")
        elif "no space left" in stderr_text:
            reason = (
                "No space left on destination device. Free up disk space "
                "or choose a different target.")
        elif ("mount" in stderr_text and "failed" in stderr_text
              or "mount error" in stderr_text):
            reason = (
                "Network share mount failed — the share could not be "
                "reached. Check the hostname, credentials, and that the "
                "remote server is online.")
        elif "connection refused" in stderr_text:
            reason = (
                "Connection refused by the remote host. Verify the "
                "server address, port, and that the service is running.")
        elif ("connection timed out" in stderr_text
              or "timeout" in stderr_text):
            reason = (
                "Connection timed out — the remote host did not respond. "
                "Check network connectivity and firewall rules.")
        elif "host not found" in stderr_text or "name or service not known" in stderr_text:
            reason = (
                "Hostname could not be resolved. Check the server "
                "address and DNS configuration.")
        elif "broken pipe" in stderr_text:
            reason = (
                "Broken pipe — the connection to the destination was "
                "lost during transfer. The remote host may have "
                "disconnected or the network dropped.")
        elif "not a btrfs subvolume" in stderr_text:
            reason = (
                "The source path is not a Btrfs subvolume. Btrfs Native "
                "backup requires a subvolume as the source. Use the "
                "Ext4/Universal engine for standard directories.")
        elif "corrupt" in stderr_text:
            reason = (
                "Data corruption detected in the stream. The backup "
                "file may be damaged or incomplete.")
        elif ("authentication" in stderr_text
              or "login incorrect" in stderr_text
              or "logon failure" in stderr_text):
            reason = (
                "Authentication failed — username or password was "
                "rejected by the remote server.")
        elif "read-only file system" in stderr_text:
            reason = (
                "Destination is a read-only file system. Remount with "
                "write permissions or choose a different target.")
        elif "input/output error" in stderr_text:
            reason = (
                "I/O error — a disk read or write failed. This may "
                "indicate a failing drive or disconnected storage.")
        elif "no such file or directory" in stderr_text:
            reason = (
                "A required file or directory was not found. The source "
                "path may have been moved, deleted, or a mount point "
                "is not attached.")
        else:
            reason = None

        if reason:
            return {
                "short": reason[:120],
                "detail": reason,
                "status_label": f"❌  Failed — {reason[:80]}",
                "status_strip": f"❌  {reason[:100]}"
            }

        # ── 3. Interpret well-known exit codes ─────────────────────────────

        # Exit 130 = our bash trap for SIGINT/SIGTERM (user cancel)
        if exit_code == 130:
            return {
                "short": "Cancelled by user",
                "detail": "The operation was manually stopped before "
                          "it could complete.",
                "status_label": "⏹  Cancelled by user",
                "status_strip": "⏹  Operation cancelled by user."
            }

        # Exit 10 = our custom code for mount failure
        if exit_code == 10:
            return {
                "short": "Network share mount failed",
                "detail": (
                    "Could not mount the network share or prepare the "
                    "destination. Check that the server is reachable, "
                    "credentials are correct, and the share path exists."),
                "status_label": (
                    "❌  Failed — network share could not be mounted"),
                "status_strip": (
                    "❌  Network share mount failed. Check server, "
                    "credentials, and share path.")
            }

        # Rsync-specific exit codes
        _RSYNC_CODES = {
            1: "Rsync syntax or usage error.",
            2: "Rsync protocol incompatibility.",
            3: "Errors selecting input/output files or directories.",
            5: "Error starting the client-server protocol.",
            10: "Error in rsync socket I/O.",
            11: "Error in file I/O.",
            12: "Error in rsync protocol data stream.",
            20: "Received SIGUSR1 or SIGINT.",
            21: "Some error returned by waitpid().",
            22: "Error allocating core memory buffers.",
            23: "Partial transfer — some files could not be transferred.",
            24: "Some source files vanished before they could be transferred.",
            25: "The --max-delete limit was reached.",
            30: "Timeout in data send/receive.",
            35: "Timeout waiting for daemon connection.",
        }
        if exit_code in _RSYNC_CODES:
            reason = _RSYNC_CODES[exit_code]
            return {
                "short": reason,
                "detail": reason,
                "status_label": f"❌  Failed — {reason[:80]}",
                "status_strip": f"❌  {reason[:100]}"
            }

        # Codes 128+ mean "killed by signal N" (128 + signal number)
        if exit_code > 128:
            sig_num = exit_code - 128
            sig_name = _SIGNAL_NAMES.get(sig_num, f"signal {sig_num}")
            if sig_num in (9, 15):
                # SIGKILL / SIGTERM — process was killed externally
                reason = (
                    f"Process was terminated by {sig_name}. This "
                    f"usually means the operation was stopped by "
                    f"the system, another process, or a timeout.")
            else:
                reason = (
                    f"Process was killed by {sig_name} "
                    f"(exit code {exit_code}).")
            return {
                "short": reason,
                "detail": reason,
                "status_label": f"❌  {reason[:80]}",
                "status_strip": f"❌  {reason[:100]}"
            }

        # ── 4. Show last stderr line as best-effort context ───────────────
        if stderr:
            # Strip timestamps from captured lines
            last_err = stderr[-1]
            # Remove "[HH:MM:SS]" prefix if present
            m = re.match(r"\[[\d:]+(?:\s*[AP]M)?\]\s*(.*)", last_err)
            last_clean = m.group(1) if m else last_err
            # Remove "SYS: " prefix too
            last_clean = re.sub(r"^SYS:\s*", "", last_clean).strip()
            if last_clean:
                return {
                    "short": last_clean[:120],
                    "detail": last_clean,
                    "status_label": f"❌  Failed — {last_clean[:80]}",
                    "status_strip": f"❌  {last_clean[:100]}"
                }

        # ── 5. Generic fallback ───────────────────────────────────────────
        job_label = (job_type or "Operation").title()
        return {
            "short": (
                f"{job_label} failed (exit code {exit_code}). "
                f"Check the log console for details."),
            "detail": (
                f"The process exited with code {exit_code}. No specific "
                f"error was captured. Review the full log output above "
                f"for clues."),
            "status_label": (
                f"❌  {job_label} failed — see log for details"),
            "status_strip": (
                f"❌  {job_label} failed (code {exit_code}). "
                f"See Logs for details.")
        }

    def handle_finished(self, exit_code, exit_status=0):
        if exit_code == 0 and getattr(self, '_pending_cloud_upload', None):
            self.log("SUCCESS: Phase 1 staging complete.")
            self._start_cloud_phase2()
            return

        finished_job_type = getattr(self, 'active_job_type', None)
        self.active_job_type = None

        if exit_code == 0:
            self.update_job_state(
                "Completed", "Successfully committed to disk.",
                append_log=False)
            self.log("SUCCESS: Operation finished cleanly.")

            # ── System notification: success ──────────────────────────────
            job_label = (finished_job_type or "operation").title()
            target_str = getattr(self, '_last_backup_target_str', '')
            self._send_system_notification(
                f"ArchVault — {job_label} Complete",
                f"{job_label} finished successfully."
                + (f"\nTarget: {target_str}" if target_str else ""),
                icon="dialog-information",
                urgency="normal")

            if finished_job_type == "backup":
                last_prof = getattr(self, "_last_backup_prof", None)
                last_file = getattr(self, "_last_backup_file", None)
                if (last_prof and last_prof.get("encrypt")
                        and last_file and os.path.exists(last_file)):
                    self._gpg_encrypt_archive(last_file, last_prof)
                if last_prof:
                    self.dispatch_notification(
                        last_prof, True, "Backup",
                        getattr(self, "_last_backup_target_str", ""))
                dest_dir = getattr(self, '_last_backup_dest', None)
                if dest_dir:
                    self.run_retention_pruning(
                        dest_dir,
                        getattr(self, '_last_backup_profile', None),
                        getattr(self, "settings", {}).get(
                            "global_retention", 7))
        else:
            # ── Build a meaningful error description ──────────────────────
            diag = self._build_failure_reason(exit_code, finished_job_type)
            job_label = (finished_job_type or "operation").title()

            if getattr(self, '_user_cancelled', False) or exit_code == 130:
                self.update_job_state(
                    "Cancelled", diag["short"], append_log=True)
                self.log(f"INFO: {diag['detail']}")

                # ── System notification: cancelled ────────────────────────
                self._send_system_notification(
                    f"ArchVault — {job_label} Cancelled",
                    "The operation was stopped before completion.",
                    icon="dialog-warning",
                    urgency="low")
            else:
                self.update_job_state(
                    "Failed", diag["short"], append_log=True)
                self.log(f"FAILED: {diag['detail']}")
                # Show the last captured stderr lines for context
                if (hasattr(self, '_stderr_error_lines')
                        and self._stderr_error_lines):
                    self.log("── Captured error output ──")
                    for line in self._stderr_error_lines[-10:]:
                        self.log(f"  {line}")

                # ── System notification: failure ──────────────────────────
                self._send_system_notification(
                    f"ArchVault — {job_label} Failed",
                    diag["short"],
                    icon="dialog-error",
                    urgency="critical")

        self.current_job_id = None
        self._user_cancelled = False

        for btn in ['btn_run_backup', 'btn_run_restore']:
            if hasattr(self, btn):
                getattr(self, btn).setEnabled(True)
        for btn in ['btn_backup_pause', 'btn_backup_stop',
                     'btn_restore_pause', 'btn_restore_stop']:
            if hasattr(self, btn):
                getattr(self, btn).setEnabled(False)
        self.is_paused = False

        # Reset pause buttons
        _BTN_PAUSE = (
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #f59e0b,stop:1 #fbbf24); color: #1c1917; "
            "font-weight: 700; font-size: 12px; padding: 9px 20px; "
            "border-radius: 8px; border: none; letter-spacing: 0.3px;")
        for btn_name in ('btn_backup_pause', 'btn_restore_pause'):
            btn = getattr(self, btn_name, None)
            if btn:
                btn.setText("⏸  Pause")
                btn.setStyleSheet(_BTN_PAUSE)

        # ── Update progress cards to final state ──────────────────────────
        if exit_code == 0:
            success_text = (
                "✅  Backup completed successfully"
                if finished_job_type == "backup"
                else "✅  Restore completed successfully"
                if finished_job_type == "restore"
                else "✅  Operation completed successfully")
            status_strip_text = "✅  Operation completed successfully."
        else:
            diag = self._build_failure_reason(exit_code, finished_job_type)
            success_text = diag["status_label"]
            status_strip_text = diag["status_strip"]

        if finished_job_type == "backup":
            if hasattr(self, '_backup_status_label'):
                if exit_code == 0:
                    self._backup_status_label.setText(success_text)
                else:
                    self._backup_status_label.setText(success_text)
            if hasattr(self, 'progress_bar'):
                if exit_code == 0:
                    self.progress_bar.setRange(0, 100)
                    self.progress_bar.setValue(100)
                else:
                    self.progress_bar.setRange(0, 1)
                    self.progress_bar.setValue(0)
        elif finished_job_type == "restore":
            if hasattr(self, '_restore_status_label'):
                self._restore_status_label.setText(success_text)
            if hasattr(self, '_restore_progress_bar'):
                if exit_code == 0:
                    self._restore_progress_bar.setRange(0, 100)
                    self._restore_progress_bar.setValue(100)
                else:
                    self._restore_progress_bar.setRange(0, 1)
                    self._restore_progress_bar.setValue(0)

        if hasattr(self, 'refresh_dashboard'):
            self.refresh_dashboard()

        if hasattr(self, 'update_status_strip'):
            self.update_status_strip(status_strip_text)

    # ─────────────────────────────────────────────────────────────────────────
    # NOTIFICATION DISPATCH (stub — overridden by core_engine)
    # ─────────────────────────────────────────────────────────────────────────
    def dispatch_notification(self, prof, success, job_type, target):
        import urllib.request, json as _json, smtplib
        from email.mime.text import MIMEText
        # Stripped notification boilerplate for brevity; keeps standard behavior
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # NATIVE LINUX DESKTOP NOTIFICATION  (notify-send via D-Bus)
    # ─────────────────────────────────────────────────────────────────────────
    def _send_system_notification(self, title, body,
                                  icon="dialog-information",
                                  urgency="normal"):
        """Send a native freedesktop notification to the logged-in user.

        Because ArchVault runs as root (sudo), we must target the real
        user's D-Bus session — otherwise notify-send talks to root's
        (nonexistent) session and silently fails.

        Urgency: "low", "normal", "critical"
        Icons:   "dialog-information", "dialog-warning", "dialog-error"
        """
        if not getattr(self, "settings", {}).get("tray_notifications", True):
            return

        sudo_user = os.environ.get("SUDO_USER", "")
        if not sudo_user:
            # Headless / no SUDO_USER — try from systemd service env
            # or fall back to first logged-in user
            try:
                result = subprocess.run(
                    ["loginctl", "list-users", "--no-legend", "--no-pager"],
                    capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    # First line: "1000 chase"
                    first_line = result.stdout.strip().split("\n")[0]
                    parts = first_line.split()
                    if len(parts) >= 2:
                        sudo_user = parts[1]
            except Exception:
                pass
        if not sudo_user:
            return

        if not shutil.which("notify-send"):
            return

        try:
            uid = subprocess.check_output(
                ["id", "-u", sudo_user],
                text=True, timeout=5).strip()
        except Exception:
            return

        dbus_addr = f"unix:path=/run/user/{uid}/bus"

        # Build the notify-send command
        notify_cmd = (
            f'notify-send '
            f'--app-name="ArchVault" '
            f'--icon={shlex.quote(icon)} '
            f'--urgency={shlex.quote(urgency)} '
            f'{shlex.quote(title)} '
            f'{shlex.quote(body)}')

        try:
            subprocess.Popen(
                ["su", sudo_user, "-c",
                 f"DBUS_SESSION_BUS_ADDRESS={dbus_addr} {notify_cmd}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception:
            pass
