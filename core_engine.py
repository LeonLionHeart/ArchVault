import os
import signal
import subprocess
import threading
from datetime import datetime, timedelta
from PyQt6.QtCore import QProcess, QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import QMessageBox

CLOUD_STAGING_DIR = "/tmp/archvault_cloud_staging"
MULTIPART_THRESHOLD = 100 * 1024 * 1024   # 100 MB — files above this use multipart
MULTIPART_CHUNK     =  50 * 1024 * 1024   # 50 MB per part


# ---------------------------------------------------------------------------
# Cloud upload worker — runs off the main thread so the UI stays responsive
# ---------------------------------------------------------------------------
class CloudUploadWorker(QThread):
    progress_signal = pyqtSignal(str)   # log line
    finished_signal = pyqtSignal(int)   # exit code: 0 = success, 1 = failure

    def __init__(self, provider, profile, local_file, object_key):
        super().__init__()
        self.provider   = provider
        self.profile    = profile
        self.local_file = local_file
        self.object_key = object_key
        self._abort     = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            provider = self.provider
            if provider in ("AWS S3", "Backblaze B2", "Wasabi", "Generic S3"):
                self._upload_s3()
            elif provider == "Google Cloud Storage":
                self._upload_gcs()
            elif provider == "Azure Blob":
                self._upload_azure()
            else:
                self.progress_signal.emit(f"ERROR: Unknown provider '{provider}'.")
                self.finished_signal.emit(1)
        except Exception as e:
            self.progress_signal.emit(f"CLOUD CRITICAL: Unhandled upload error: {e}")
            self.finished_signal.emit(1)

    # -----------------------------------------------------------------------
    # S3-compatible: AWS, Backblaze B2, Wasabi, Generic S3
    # -----------------------------------------------------------------------
    def _upload_s3(self):
        try:
            import boto3
            from boto3.s3.transfer import TransferConfig
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            self.progress_signal.emit("ERROR: boto3 is not installed. Run: pip install boto3 --break-system-packages")
            self.finished_signal.emit(1)
            return

        prof     = self.profile
        bucket   = prof.get("bucket", "")
        region   = prof.get("region", "") or "us-east-1"
        endpoint = prof.get("endpoint_url", "").strip() or None

        # Provider-specific endpoint defaults
        provider = self.provider
        if provider == "Backblaze B2" and not endpoint:
            # B2 requires the endpoint from the bucket settings — warn if missing
            self.progress_signal.emit("WARNING: Backblaze B2 requires an Endpoint URL in your profile (e.g. https://s3.us-west-004.backblazeb2.com). Upload may fail without it.")
        elif provider == "Wasabi" and not endpoint:
            endpoint = f"https://s3.wasabisys.com"

        self.progress_signal.emit(f"CLOUD: Connecting to {provider} bucket '{bucket}'...")

        try:
            session = boto3.session.Session()
            s3 = session.client(
                "s3",
                region_name=region if region else None,
                endpoint_url=endpoint,
                aws_access_key_id=prof.get("access_key", ""),
                aws_secret_access_key=prof.get("secret_key", ""),
            )
        except Exception as e:
            self.progress_signal.emit(f"CLOUD ERROR: Failed to create S3 client: {e}")
            self.finished_signal.emit(1)
            return

        file_size = os.path.getsize(self.local_file)
        size_mb   = file_size / (1024 * 1024)
        self.progress_signal.emit(f"CLOUD: Local staging file ready. Size: {size_mb:.1f} MB")
        self.progress_signal.emit(f"CLOUD: Starting multipart upload → s3://{bucket}/{self.object_key}")

        uploaded_bytes = [0]

        def progress_cb(bytes_transferred):
            if self._abort:
                raise InterruptedError("Upload aborted by user.")
            uploaded_bytes[0] += bytes_transferred
            pct = min(100, int(uploaded_bytes[0] / file_size * 100))
            uploaded_mb = uploaded_bytes[0] / (1024 * 1024)
            self.progress_signal.emit(f"UPLOAD: {uploaded_mb:.1f} MB / {size_mb:.1f} MB  ({pct}%)")

        config = TransferConfig(
            multipart_threshold=MULTIPART_THRESHOLD,
            multipart_chunksize=MULTIPART_CHUNK,
            max_concurrency=4,
            use_threads=True,
        )

        try:
            s3.upload_file(
                self.local_file,
                bucket,
                self.object_key,
                Callback=progress_cb,
                Config=config,
            )
        except InterruptedError:
            self.progress_signal.emit("CLOUD: Upload aborted by user.")
            self.finished_signal.emit(1)
            return
        except Exception as e:
            self.progress_signal.emit(f"CLOUD ERROR: Upload failed: {e}")
            self.finished_signal.emit(1)
            return

        self.progress_signal.emit(f"CLOUD: Upload complete. Object key: {self.object_key}")
        self.finished_signal.emit(0)

    # -----------------------------------------------------------------------
    # Google Cloud Storage
    # -----------------------------------------------------------------------
    def _upload_gcs(self):
        try:
            from google.cloud import storage as gcs_storage
            from google.oauth2 import service_account
            import google.auth
        except ImportError:
            self.progress_signal.emit("ERROR: google-cloud-storage is not installed. Run: pip install google-cloud-storage --break-system-packages")
            self.finished_signal.emit(1)
            return

        prof       = self.profile
        bucket_name = prof.get("bucket", "")
        key_json    = prof.get("access_key", "").strip()  # GCS uses a service account JSON path

        self.progress_signal.emit(f"CLOUD: Connecting to Google Cloud Storage bucket '{bucket_name}'...")

        try:
            if key_json and os.path.exists(key_json):
                credentials = service_account.Credentials.from_service_account_file(key_json)
                client = gcs_storage.Client(credentials=credentials)
            else:
                # Fall back to application default credentials
                self.progress_signal.emit("CLOUD: No service account key file found — using application default credentials.")
                client = gcs_storage.Client()
        except Exception as e:
            self.progress_signal.emit(f"CLOUD ERROR: GCS authentication failed: {e}")
            self.finished_signal.emit(1)
            return

        file_size = os.path.getsize(self.local_file)
        size_mb   = file_size / (1024 * 1024)
        self.progress_signal.emit(f"CLOUD: Local staging file ready. Size: {size_mb:.1f} MB")
        self.progress_signal.emit(f"CLOUD: Starting resumable upload → gs://{bucket_name}/{self.object_key}")

        try:
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(self.object_key)

            uploaded_bytes = [0]

            # GCS resumable upload with chunk reporting
            chunk_size = MULTIPART_CHUNK
            with open(self.local_file, "rb") as f:
                blob.chunk_size = chunk_size
                blob.upload_from_file(f, size=file_size, timeout=300)
                if self._abort:
                    self.progress_signal.emit("CLOUD: Upload aborted by user.")
                    self.finished_signal.emit(1)
                    return

        except Exception as e:
            self.progress_signal.emit(f"CLOUD ERROR: GCS upload failed: {e}")
            self.finished_signal.emit(1)
            return

        self.progress_signal.emit(f"CLOUD: Upload complete. Object key: {self.object_key}")
        self.finished_signal.emit(0)

    # -----------------------------------------------------------------------
    # Azure Blob Storage
    # -----------------------------------------------------------------------
    def _upload_azure(self):
        try:
            from azure.storage.blob import BlobServiceClient, BlobClient
            from azure.core.exceptions import AzureError
        except ImportError:
            self.progress_signal.emit("ERROR: azure-storage-blob is not installed. Run: pip install azure-storage-blob --break-system-packages")
            self.finished_signal.emit(1)
            return

        prof            = self.profile
        container_name  = prof.get("bucket", "")
        account_name    = prof.get("access_key", "").strip()
        account_key     = prof.get("secret_key", "").strip()
        endpoint        = prof.get("endpoint_url", "").strip()

        if endpoint:
            connect_str = f"DefaultEndpointsProtocol=https;AccountName={account_name};AccountKey={account_key};BlobEndpoint={endpoint};"
        else:
            connect_str = f"DefaultEndpointsProtocol=https;AccountName={account_name};AccountKey={account_key};EndpointSuffix=core.windows.net"

        self.progress_signal.emit(f"CLOUD: Connecting to Azure Blob container '{container_name}'...")

        file_size = os.path.getsize(self.local_file)
        size_mb   = file_size / (1024 * 1024)
        self.progress_signal.emit(f"CLOUD: Local staging file ready. Size: {size_mb:.1f} MB")
        self.progress_signal.emit(f"CLOUD: Starting chunked upload → azure://{container_name}/{self.object_key}")

        try:
            blob_service = BlobServiceClient.from_connection_string(connect_str)
            blob_client  = blob_service.get_blob_client(container=container_name, blob=self.object_key)

            uploaded_bytes = [0]

            def progress_cb(current, total):
                if self._abort:
                    raise InterruptedError("Upload aborted by user.")
                pct = min(100, int(current / total * 100)) if total else 0
                current_mb = current / (1024 * 1024)
                self.progress_signal.emit(f"UPLOAD: {current_mb:.1f} MB / {size_mb:.1f} MB  ({pct}%)")

            with open(self.local_file, "rb") as f:
                blob_client.upload_blob(
                    f,
                    overwrite=True,
                    max_concurrency=4,
                    progress_hook=progress_cb,
                )

        except InterruptedError:
            self.progress_signal.emit("CLOUD: Upload aborted by user.")
            self.finished_signal.emit(1)
            return
        except Exception as e:
            self.progress_signal.emit(f"CLOUD ERROR: Azure upload failed: {e}")
            self.finished_signal.emit(1)
            return

        self.progress_signal.emit(f"CLOUD: Upload complete. Object key: {self.object_key}")
        self.finished_signal.emit(0)


# ---------------------------------------------------------------------------
# EngineMixin
# ---------------------------------------------------------------------------
class EngineMixin:

    # --- JOB TRACKER ---
    def register_job(self, op_type, target):
        fmt = "%Y-%m-%d %I:%M:%S %p" if getattr(self, "settings", {}).get("time_format") == "12 Hour" else "%Y-%m-%d %H:%M:%S"
        self.current_job_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self.current_job_log = []
        self._stderr_error_lines = []   # collects error-bearing lines for post-mortem
        job = {
            "id": self.current_job_id,
            "time": datetime.now().strftime(fmt),
            "type": op_type,
            "target": target,
            "status": "Running",
            "description": "Stream active..."
        }
        self.job_history.append(job)
        self.write_jobs()

    def update_job_state(self, status=None, desc=None, append_log=False, pid=None):
        if getattr(self, 'current_job_id', None):
            for j in self.job_history:
                if j["id"] == self.current_job_id:
                    if status: j["status"] = status
                    if desc: j["description"] = desc
                    if append_log: j["log"] = "\n".join(self.current_job_log[-100:])
                    if pid: j["pid"] = pid
                    break
            self.write_jobs()

    def log(self, m):
        fmt = '%I:%M:%S %p' if getattr(self, "settings", {}).get("time_format") == "12 Hour" else '%H:%M:%S'
        timestamp = datetime.now().strftime(fmt)
        log_str = f"[{timestamp}] {m}"

        # Global activity console (always)
        self.console.append(log_str)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

        # Status strip — always shows the latest log line
        if hasattr(self, 'update_status_strip'):
            self.update_status_strip(log_str)

        # Per-tab consoles removed — all logging goes through global console + collapsible drawer

        # Job log memory (for error export)
        if hasattr(self, 'current_job_log'):
            self.current_job_log.append(log_str)

        # Stderr error buffer (for post-mortem diagnosis)
        if hasattr(self, '_stderr_error_lines') and any(
            kw in m for kw in ("Cannot", "error", "Error", "ERROR", "failed", "Failed",
                               "FAILED", "denied", "No space", "Broken pipe", "corrupt",
                               "cannot", "warning", "Warning", "WARNING", "skipping", "Skipping")
        ):
            self._stderr_error_lines.append(log_str)

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode().strip()
        if data: [self.log(line) for line in data.split('\n')]

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode()
        if not data: return
        lines = data.replace('\r\n', '\n').split('\r')
        for line in lines:
            clean_line = line.strip()
            if clean_line:
                self._emit_progress(clean_line)
                if "copied" in clean_line or "records processed" in clean_line:
                    self.log(f"PROGRESS: {clean_line}")
                else:
                    self.log(f"SYS: {clean_line}")

    def _emit_progress(self, line: str):
        """Parse progress output from pv/tar/rsync/rclone and update progress widgets."""
        import re

        # ── pv pipe-format: "45%|2.34GiB|125MiB/s|0:00:18" ─────────────────
        # We configure pv with -F "%p|%b|%r|%e" — four pipe-delimited fields
        m = re.match(r"(\d+)%\|([\d.]+\s*\w+)\|([\d.]+\s*[\w./]+)\|(\S+)", line.strip())
        if m:
            pct, transferred, rate, eta = m.group(1), m.group(2), m.group(3), m.group(4)
            pct_int = int(pct)
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct_int)
                self.progress_bar.show()
            if hasattr(self, "progress_label"):
                eta_clean = eta if re.match(r"\d+:\d+", eta) else "calculating…"
                self.progress_label.setText(
                    f"  ⚡  {pct}%  ·  {transferred} transferred  ·  {rate}  ·  ETA {eta_clean}"
                )
                self.progress_label.show()
            return

        # ── pv -n bare integer percentage (fallback when format isn't available) ──
        m = re.match(r"^\s*(\d{1,3})\s*$", line.strip())
        if m:
            pct_int = int(m.group(1))
            if 0 <= pct_int <= 100:
                if hasattr(self, "progress_bar"):
                    self.progress_bar.setRange(0, 100)
                    self.progress_bar.setValue(pct_int)
                    self.progress_bar.show()
                if hasattr(self, "progress_label"):
                    self.progress_label.setText(f"  ⚡  {pct_int}% transferred")
                    self.progress_label.show()
                return

        # ── tar checkpoint: "2026-02-18 22:47: 10000 records processed" ──────
        m = re.search(r"(\d[\d,]*)\s+records processed", line)
        if m:
            n = int(m.group(1).replace(",", ""))
            if hasattr(self, "progress_label"):
                self.progress_label.setText(
                    f"  ☕  {n:,} tar records archived  "
                    f"(install 'pv' for bytes/speed/ETA)"
                )
                self.progress_label.show()
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 0)   # indeterminate spinner
                self.progress_bar.show()
            return

        # ── rsync --info=progress2: "  1.23G  45%  10.23MB/s    0:01:23" ─────
        m = re.search(r"([\d.]+\w+)\s+(\d+)%\s+([\d.]+\w+/s)\s+(\d+:\d+:\d+)", line)
        if m:
            transferred, pct, rate, eta = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.show()
            if hasattr(self, "progress_label"):
                self.progress_label.setText(
                    f"  ⚡  {pct}%  ·  {transferred} transferred  ·  {rate}  ·  ETA {eta}"
                )
                self.progress_label.show()
            return

        # ── rsync (simpler pattern without ETA) ──────────────────────────────
        m = re.search(r"\s+(\d+)%\s+[\d.]+\w+/s", line)
        if m:
            pct = int(m.group(1))
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.show()
            if hasattr(self, "progress_label"):
                self.progress_label.setText(f"  ⚡  {pct}% transferred")
                self.progress_label.show()
            return

        # ── rclone: "Transferred:  1.234 GiB / 5.678 GiB, 22%, 10 MiB/s, ETA 6m40s" ──
        m = re.search(r"Transferred:.+?([\d.]+\s*\w+)\s*/\s*([\d.]+\s*\w+),\s*(\d+)%,\s*([\d.]+\s*\w+/s),\s*ETA\s*(\S+)", line)
        if m:
            done, total, pct, rate, eta = m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5)
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.show()
            if hasattr(self, "progress_label"):
                self.progress_label.setText(
                    f"  ☁  {pct}%  ·  {done} / {total}  ·  {rate}  ·  ETA {eta}"
                )
                self.progress_label.show()
            return

        # ── rclone simpler fallback ───────────────────────────────────────────
        m = re.search(r"Transferred:.+?(\d+)%", line)
        if m:
            pct = int(m.group(1))
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.show()
            if hasattr(self, "progress_label"):
                self.progress_label.setText(f"  ☁  {line.strip()}")
                self.progress_label.show()


    # ─────────────────────────────────────────────────────────────────────
    # ERROR DIAGNOSTICS — translate exit codes into human language
    # ─────────────────────────────────────────────────────────────────────
    def _diagnose_exit_code(self, code, job_type):
        """Return a dict with short summary, detail lines, and suggested actions."""

        # ── tar codes ────────────────────────────────────────────────────
        TAR = {
            1: {
                "short": "Some files could not be read — permission denied or files vanished (non-live-change error)",
                "detail": [
                    "Note: For Full System (/) backups, exit code 1 from 'file changed mid-read' is",
                    "automatically promoted to success (WARNING) by ArchVault — you should not see this",
                    "for a standard full-system tar job.",
                    "",
                    "If you do see this, it means a file was unreadable for a different reason:",
                    "  · Permission denied on a specific file or directory",
                    "  · A file disappeared between directory scan and the read attempt",
                    "  · Filesystem error on the source",
                ],
                "actions": [
                    "Check the captured error output above for the specific file path.",
                    "Ensure you are running ArchVault as root (sudo) — required for full-system access.",
                    "Exclude the problematic path if it is not critical.",
                    "Enable 'Validate Backup Upon Completion' to confirm the archive is readable.",
                    "Use Btrfs engine for a true frozen snapshot (requires btrfs on source).",
                ]
            },
            2: {
                "short": "Fatal tar error — archive is likely incomplete or corrupt",
                "detail": [
                    "tar exits 2 on a fatal error such as:",
                    "  · Destination ran out of disk space",
                    "  · Read permission denied on source files",
                    "  · Destination path does not exist or is not writable",
                    "  · Network share disconnected mid-stream",
                ],
                "actions": [
                    "Check the captured error output above for the specific file or path.",
                    "Verify the destination has sufficient free space (check 'df -h').",
                    "Ensure you have read access to all source files.",
                    "If backing up to a network share, verify the mount is stable.",
                    "Try running 'sudo tar -cpzf /tmp/test.tar.gz /etc' to test basic access.",
                ]
            },
        }
        # ── rsync codes ──────────────────────────────────────────────────
        RSYNC = {
            1:  {"short": "rsync syntax or usage error", "detail": ["The rsync command line was rejected — likely a bad path or option."], "actions": ["Check that the source path exists and is accessible.", "Review the captured SYS: lines above for the exact rsync message."]},
            2:  {"short": "rsync protocol incompatibility", "detail": ["Local and remote rsync versions are incompatible."], "actions": ["Run 'rsync --version' on both sides and ensure versions are close."]},
            5:  {"short": "rsync: error starting client-server protocol", "detail": ["Could not establish connection to the remote host."], "actions": ["Verify the network share is mounted and accessible.", "Check that rsync is installed on the remote server."]},
            11: {"short": "rsync: error in file I/O — likely out of disk space", "detail": ["Destination ran out of space mid-transfer."], "actions": ["Free up space on the destination drive.", "Review retention policy to prune old snapshots."]},
            23: {"short": "rsync partial transfer — some files could not be sent", "detail": ["rsync transferred most files but skipped some due to permission or I/O errors."], "actions": ["Check the captured error lines for specific files.", "Files owned by root or other users may need sudo access."]},
            24: {"short": "rsync: source files vanished mid-transfer", "detail": ["Files disappeared between the directory scan and the copy, likely due to active processes."], "actions": ["This is usually harmless for temporary/cache files.", "Stop processes that delete files during the backup if critical files are missing."]},
            30: {"short": "rsync: timeout — connection dropped", "detail": ["The rsync stream timed out, usually a network issue."], "actions": ["Check network stability to the destination.", "Increase rsync timeout if backing up over a slow or unreliable link."]},
        }
        # ── btrfs codes ──────────────────────────────────────────────────
        BTRFS = {
            1: {"short": "btrfs error — subvolume or stream operation failed", "detail": [
                "Common causes:",
                "  · Source is not a btrfs subvolume (use Ext4/Universal engine instead)",
                "  · Destination does not have enough space",
                "  · A previous snapshot at the same path already exists",
            ], "actions": [
                "Verify the source path is a btrfs subvolume: 'btrfs subvolume show <path>'",
                "Ensure destination is also on a btrfs filesystem for native receive.",
                "Check 'btrfs filesystem df <dest>' for space.",
                "Delete any stale /.archvault_snapshot if it exists: 'btrfs subvolume delete /.archvault_snapshot'",
            ]},
        }
        # ── dd / bare metal codes ────────────────────────────────────────
        DD = {
            1: {"short": "dd error — read or write failed during bare metal imaging", "detail": [
                "dd exits 1 on any I/O error. Common causes:",
                "  · Source drive has bad sectors",
                "  · Destination out of space or write-protected",
                "  · Source device disappeared (USB disconnect)",
            ], "actions": [
                "Run 'smartctl -a /dev/<device>' to check source drive health.",
                "Verify the destination has enough free space.",
                "Try a smaller block size: check if bs=512 works where bs=4M did not.",
            ]},
        }
        # ── Generic / mount codes ────────────────────────────────────────
        GENERIC = {
            126: {"short": "Permission denied — cannot execute the backup script", "detail": ["The backup script could not be executed (permission error)."], "actions": ["ArchVault should set the script executable. Try restarting the application as root."]},
            127: {"short": "Command not found — a required tool is missing", "detail": ["A binary called by the backup script was not found (e.g. tar, btrfs, rsync, gpg)."], "actions": ["Install the missing tool. Check the captured SYS: lines for which command failed.", "'which tar && which rsync && which btrfs' to verify availability."]},
            130: {"short": "Backup was interrupted (SIGINT / Ctrl-C)", "detail": ["The user or system interrupted the process."], "actions": ["Re-run the backup when ready."]},
            137: {"short": "Process killed — likely out of memory (OOM)", "detail": ["The OS killed the backup process (signal 9/SIGKILL). Usually caused by RAM exhaustion."], "actions": ["Close other applications to free memory.", "Back up a smaller source path, or split the backup into multiple profiles."]},
            143: {"short": "Process terminated by system (SIGTERM)", "detail": ["The process was asked to stop gracefully by the OS or another process."], "actions": ["Check if a system shutdown or service restart occurred.", "Re-run the backup manually."]},
        }

        # Determine which engine was active
        engine_text = ""
        if hasattr(self, 'engine_combo'):
            engine_text = self.engine_combo.currentText().lower()
        elif job_type == "restore":
            if hasattr(self, 'rst_engine'): engine_text = self.rst_engine.currentText().lower()

        lookup = GENERIC.get(code)
        if not lookup:
            if "btrfs" in engine_text:       lookup = BTRFS.get(code)
            elif "rsync" in engine_text:      lookup = RSYNC.get(code)
            elif "bare metal" in engine_text: lookup = DD.get(code)
            else:                             lookup = TAR.get(code)  # default: tar

        if not lookup:
            lookup = {
                "short": f"Unknown exit code {code} — see captured error output for details",
                "detail": ["This exit code is not in the ArchVault diagnostic database.",
                           "The captured error lines above are your best guide to what went wrong."],
                "actions": [
                    "Review the 'Captured Error Output' section above carefully.",
                    "Search for the specific error message online with the engine name.",
                    "Export the full error log from Jobs → Errors for offline analysis.",
                ]
            }
        return lookup

    def handle_finished(self, exit_code, exit_status=0):
        finished_job_type = getattr(self, 'active_job_type', None)
        self.active_job_type = None
        if exit_code == 0:
            self.update_job_state("Completed", "Successfully committed to disk.", append_log=False)
            self.log("SUCCESS: Operation finished cleanly.")
            if finished_job_type == "backup":
                # FEATURE: GPG encryption at rest — encrypt the archive if profile requests it
                last_prof = getattr(self, "_last_backup_prof", None)
                last_file = getattr(self, "_last_backup_file", None)
                if last_prof and last_prof.get("encrypt") and last_file and os.path.exists(last_file):
                    self._gpg_encrypt_archive(last_file, last_prof)
                # FEATURE: Send notification
                if last_prof:
                    t_str_notif = getattr(self, "_last_backup_target_str", "")
                    self.dispatch_notification(last_prof, True, "Backup", t_str_notif)
                dest_dir       = getattr(self, '_last_backup_dest', None)
                profile_name   = getattr(self, '_last_backup_profile', None)
                retention_days = getattr(self, '_last_backup_retention', None)
                if retention_days is None:
                    retention_days = getattr(self, "settings", {}).get("global_retention", 7)
                if dest_dir and profile_name:
                    self.run_retention_pruning(dest_dir, profile_name, retention_days)
        else:
            # ── Build human-readable diagnostic ──────────────────────────
            diagnosis = self._diagnose_exit_code(exit_code, finished_job_type)
            job_desc  = f"Exit {exit_code}: {diagnosis['short']}"
            self.update_job_state("Failed", job_desc, append_log=True)

            self.log("━" * 60)
            self.log(f"FAILED: Operation exited with code {exit_code}")
            self.log(f"CAUSE:  {diagnosis['short']}")
            for detail_line in diagnosis['detail']:
                self.log(f"  ›  {detail_line}")

            # Pull the most relevant stderr lines collected during the run
            error_lines = getattr(self, '_stderr_error_lines', [])
            if error_lines:
                self.log("─── Captured Error Output ───────────────────────────────")
                # Show last 15 most relevant, skip pure progress lines
                shown = [l for l in error_lines if "records processed" not in l][-15:]
                for l in shown:
                    self.log(f"  {l}")

            self.log("─── Suggested Actions ───────────────────────────────────")
            for action in diagnosis['actions']:
                self.log(f"  ✦  {action}")
            self.log("━" * 60)

            # Append diagnostic summary to job log so it appears in error export
            if hasattr(self, 'current_job_log'):
                summary = [
                    "═" * 60,
                    f"EXIT CODE : {exit_code}",
                    f"CAUSE     : {diagnosis['short']}",
                    "DETAIL:",
                ] + [f"  › {d}" for d in diagnosis['detail']] + [
                    "CAPTURED ERRORS:",
                ] + (([f"  {l}" for l in error_lines[-20:]] if error_lines else ["  (none captured)"])) + [
                    "ACTIONS:",
                ] + [f"  ✦ {a}" for a in diagnosis['actions']] + ["═" * 60]
                self.current_job_log.extend(summary)
                self.write_jobs()

            # FEATURE: failure notification
            last_prof = getattr(self, "_last_backup_prof", None)
            if last_prof and finished_job_type == "backup":
                t_str_notif = getattr(self, "_last_backup_target_str", "")
                self.dispatch_notification(last_prof, False, "Backup", t_str_notif)
        self.current_job_id = None
        if hasattr(self, 'btn_run_backup'): self.btn_run_backup.setEnabled(True)
        if hasattr(self, 'btn_run_restore'): self.btn_run_restore.setEnabled(True)
        for btn in ['btn_backup_pause', 'btn_backup_stop', 'btn_restore_pause', 'btn_restore_stop']:
            if hasattr(self, btn): getattr(self, btn).setEnabled(False)
        self.is_paused = False
        for btn in ['btn_backup_pause', 'btn_restore_pause']:
            if hasattr(self, btn):
                getattr(self, btn).setText("Pause")
                getattr(self, btn).setStyleSheet("background-color: #ffc107; color: black; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        # Reset progress bar
        if hasattr(self, "progress_bar"):
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            from PyQt6.QtCore import QTimer as _QT
            _QT.singleShot(2500, lambda: [
                w.hide() for w in
                [getattr(self, "progress_bar", None), getattr(self, "progress_label", None)]
                if w
            ])
        if hasattr(self, 'refresh_dashboard'):
            self.refresh_dashboard()
        # Update status strip to show final state after job completes
        if hasattr(self, 'update_status_strip'):
            if exit_code == 0:
                self.update_status_strip("\u2705  Operation completed successfully.")
            else:
                self.update_status_strip(f"\u274c  Operation failed \u2014 exit code {exit_code}. See Logs for details.")

    # --- RETENTION PRUNING ---
    def run_retention_pruning(self, dest_dir, profile_name, retention_days):
        self.log(f"--- Retention Policy: Scanning '{dest_dir}' for backups older than {retention_days} day(s) ---")
        import shutil
        cutoff = datetime.now() - timedelta(days=retention_days)
        safe_profile = profile_name.replace(" ", "_")
        deleted_count = 0
        error_count   = 0
        try:
            if not os.path.isdir(dest_dir):
                self.log(f"RETENTION: Destination '{dest_dir}' is not accessible. Skipping prune.")
                return
            # Check both the flat dest_dir AND the _incremental subdir (rsync snapshots)
            scan_dirs = [dest_dir]
            incremental_dir = os.path.join(dest_dir, f"{safe_profile}_incremental")
            if os.path.isdir(incremental_dir):
                scan_dirs.append(incremental_dir)

            candidate_extensions = ('.tar.gz', '.tar.zst', '.btrfs', '.img.gz', '.gpg')
            for scan_dir in scan_dirs:
                is_incremental = (scan_dir == incremental_dir)
                for fname in sorted(os.listdir(scan_dir)):
                    full_path = os.path.join(scan_dir, fname)
                    # rsync incremental: prune dated snapshot dirs (YYYY-MM-DD_HHMMSS format)
                    if is_incremental and os.path.isdir(full_path) and fname != "latest":
                        try:
                            mtime    = datetime.fromtimestamp(os.path.getmtime(full_path))
                            age_days = (datetime.now() - mtime).days
                            if mtime < cutoff:
                                shutil.rmtree(full_path)
                                self.log(f"RETENTION: Pruned incremental snapshot '{fname}' (age: {age_days} days).")
                                deleted_count += 1
                            else:
                                self.log(f"RETENTION: Keeping snapshot '{fname}' (age: {age_days} days).")
                        except Exception as e:
                            self.log(f"RETENTION ERROR: Could not prune snapshot '{fname}': {e}")
                            error_count += 1
                        continue
                    # Standard archive files
                    if safe_profile.lower() not in fname.lower(): continue
                    if not any(fname.endswith(ext) for ext in candidate_extensions): continue
                    if not os.path.isfile(full_path): continue
                    try:
                        mtime    = datetime.fromtimestamp(os.path.getmtime(full_path))
                        age_days = (datetime.now() - mtime).days
                        if mtime < cutoff:
                            os.remove(full_path)
                            self.log(f"RETENTION: Pruned '{fname}' (age: {age_days} days, limit: {retention_days} days).")
                            deleted_count += 1
                        else:
                            self.log(f"RETENTION: Keeping '{fname}' (age: {age_days} days).")
                    except Exception as e:
                        self.log(f"RETENTION ERROR: Could not process '{fname}': {e}")
                        error_count += 1
            if deleted_count == 0 and error_count == 0:
                self.log("RETENTION: No expired backups found. Policy satisfied.")
            elif deleted_count > 0:
                self.log(f"RETENTION: Pruning complete. {deleted_count} item(s) removed.")
            if error_count > 0:
                self.log(f"RETENTION WARNING: {error_count} item(s) could not be processed.")
        except Exception as e:
            self.log(f"RETENTION CRITICAL: Pruning scan failed: {e}")

    # --- PAUSE / STOP ---
    def _get_all_descendant_pids(self, pid):
        """Recursively find all descendant PIDs of the given PID using /proc."""
        descendants = []
        try:
            children = subprocess.check_output(
                ["pgrep", "-P", str(pid)], text=True, stderr=subprocess.DEVNULL
            ).strip().split("\n")
            for child in children:
                child = child.strip()
                if child and child.isdigit():
                    child_pid = int(child)
                    descendants.append(child_pid)
                    descendants.extend(self._get_all_descendant_pids(child_pid))
        except (subprocess.CalledProcessError, ValueError):
            pass
        return descendants

    def toggle_pause(self):
        if self.process.state() == QProcess.ProcessState.Running:
            pid = self.process.processId()
            if not self.is_paused:
                # Collect ALL descendant PIDs (children, grandchildren, etc.)
                all_pids = self._get_all_descendant_pids(pid) + [pid]
                for p in all_pids:
                    try:
                        os.kill(p, signal.SIGSTOP)
                    except (ProcessLookupError, PermissionError):
                        pass
                self.is_paused = True
                self.update_job_state("Stalled", "User paused stream.")
                for btn in ['btn_backup_pause', 'btn_restore_pause']:
                    if hasattr(self, btn):
                        getattr(self, btn).setText("Resume")
                        getattr(self, btn).setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
                self.log("WARNING: Stream PAUSED.")
            else:
                # Resume ALL descendants + parent
                all_pids = [pid] + self._get_all_descendant_pids(pid)
                for p in all_pids:
                    try:
                        os.kill(p, signal.SIGCONT)
                    except (ProcessLookupError, PermissionError):
                        pass
                self.is_paused = False
                self.update_job_state("Running", "Stream active...")
                for btn in ['btn_backup_pause', 'btn_restore_pause']:
                    if hasattr(self, btn):
                        getattr(self, btn).setText("Pause")
                        getattr(self, btn).setStyleSheet("background-color: #ffc107; color: black; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
                self.log("INFO: Stream RESUMED.")
        elif hasattr(self, '_cloud_worker') and self._cloud_worker and self._cloud_worker.isRunning():
            self.log("WARNING: Cloud uploads cannot be paused mid-transfer.")

    def stop_process(self):
        # Stop a local shell backup
        if self.process.state() == QProcess.ProcessState.Running:
            self.log("CRITICAL: Cancel signal sent! Destroying incomplete stream...")
            pid = self.process.processId()
            # Resume all processes first if paused (can't kill stopped processes reliably)
            if self.is_paused:
                all_pids = [pid] + self._get_all_descendant_pids(pid)
                for p in all_pids:
                    try:
                        os.kill(p, signal.SIGCONT)
                    except (ProcessLookupError, PermissionError):
                        pass
            # Kill entire process tree
            all_pids = self._get_all_descendant_pids(pid) + [pid]
            for p in all_pids:
                try:
                    os.kill(p, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            self.process.terminate()
        # Stop a cloud upload
        if hasattr(self, '_cloud_worker') and self._cloud_worker and self._cloud_worker.isRunning():
            self.log("CRITICAL: Aborting cloud upload...")
            self._cloud_worker.abort()

    # --- VALIDATION ---
    def start_validation(self):
        target_file = self.val_path_input.text().strip()
        if not target_file or not os.path.exists(target_file):
            return QMessageBox.warning(self, "Error", "Please select a valid backup file.")
        self.active_job_type = "validation"
        self.console.clear()
        is_ext4   = target_file.endswith(".tar.gz")
        check_cmd = f"tar -tzf \"{target_file}\" > /dev/null" if is_ext4 else f"btrfs receive --dump -f \"{target_file}\" > /dev/null"
        bash_script = f"#!/bin/bash\necho \"--- Starting Integrity Check on {target_file} ---\"\n{check_cmd}\nif [ $? -eq 0 ]; then echo \"SUCCESS: File is fully intact.\"; exit 0\nelse echo \"CRITICAL: Stream corruption detected!\"; exit 1; fi\n"
        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")

    # --- CLOUD BACKUP (rclone backend) ---
    def start_cloud_backup(self, name, prof, t_str):
        """
        Two-phase cloud backup using rclone:
        Phase 1 — create the archive locally in CLOUD_STAGING_DIR.
        Phase 2 — upload via rclone (handles S3/GCS/Azure/B2/Wasabi/any rclone remote).
        rclone must be installed: pacman -S rclone  or  apt install rclone
        """
        provider = prof.get("provider", "")
        src_mode = prof.get("source_mode", "Full System")
        src_path = prof.get("source_path", "/")
        if src_mode == "Full System": src_path = "/"

        is_ext4 = "Ext4" in self.engine_combo.currentText()

        date_str     = datetime.now().strftime("%Y-%m-%d")
        time_str     = datetime.now().strftime("%H%M")
        datetime_str = datetime.now().strftime("%Y-%m-%d_%H%M")
        hostname     = getattr(os.uname(), "nodename", "Linux")

        name_fmt    = getattr(self, "settings", {}).get("backup_name_format", "ArchVault_%profile%_%datetime%")
        custom_name = (name_fmt
                       .replace("%profile%", name.replace(" ", "_"))
                       .replace("%date%", date_str)
                       .replace("%time%", time_str)
                       .replace("%datetime%", datetime_str)
                       .replace("%hostname%", hostname))

        os.makedirs(CLOUD_STAGING_DIR, exist_ok=True)
        ext        = ".tar.gz" if is_ext4 else ".btrfs"
        local_file = os.path.join(CLOUD_STAGING_DIR, f"{custom_name}{ext}")
        snap_pt    = "/.archvault_snapshot"

        self.log(f"--- Cloud Backup Phase 1: Staging archive to {local_file} ---")

        if "Bare Metal" in src_mode:
            self.log("ERROR: Bare Metal imaging is not supported for cloud targets.")
            return self.handle_finished(1, 0)

        if not is_ext4:
            pre_flight = (
                f'echo "--- Verifying Btrfs Subvolume ---"\n'
                f'if ! btrfs subvolume show "{src_path}" >/dev/null 2>&1; then\n'
                f'    echo "CRITICAL: {src_path} is not a Btrfs subvolume!"; exit 1\nfi\n'
            )
        else:
            pre_flight = ""

        if is_ext4:
            ckpt = "--checkpoint=10000 --checkpoint-action=echo='%{%Y-%m-%d %H:%M:%S}t: %d0000 records processed'"
            excl = ""
            if getattr(self, "settings", {}).get("exclude_cache", True):
                excl = f"--exclude='*/.cache/*' --exclude='*/.local/share/Trash/*'"
            if src_path == "/":
                engine_cmd = (f"tar {ckpt} {excl} --exclude=/proc --exclude=/sys --exclude=/dev "
                              f"--exclude=/tmp --exclude=/run --exclude={CLOUD_STAGING_DIR} "
                              f"-cpzf '{local_file}' / & CMD_PID=$!; wait $CMD_PID")
            else:
                engine_cmd = f"tar {ckpt} {excl} -cpzf '{local_file}' '{src_path}' & CMD_PID=$!; wait $CMD_PID"
            btrfs_clean = ""
        else:
            engine_cmd  = (f"btrfs subvolume snapshot -r '{src_path}' {snap_pt} && "
                           f"btrfs send {snap_pt} -f '{local_file}' & CMD_PID=$!; wait $CMD_PID")
            btrfs_clean = f"btrfs subvolume delete {snap_pt} >/dev/null 2>&1;"

        trap_cmd = f"trap 'kill -9 $CMD_PID 2>/dev/null; rm -f \"{local_file}\"; {btrfs_clean} exit 1' SIGINT SIGTERM"
        bash_script = (
            f"#!/bin/bash\n{trap_cmd}\n{pre_flight}"
            f"EST=$(du -sh \"{src_path}\" 2>/dev/null | awk '{{print $1}}')\n"
            f"echo \"Source size: $EST\"\n"
            f"{engine_cmd}\n{btrfs_clean}\n"
            f"SEND_STATUS=$?\n"
            f"if [ $SEND_STATUS -ne 0 ]; then exit $SEND_STATUS; fi\n"
            f"echo \"SUCCESS: Staging complete.\"\nexit 0\n"
        )

        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)

        self._pending_cloud_upload = {
            "provider":    provider,
            "profile":     prof,
            "local_file":  local_file,
            "object_key":  f"{custom_name}{ext}",
            "profile_name": name,
        }
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())

    def _start_cloud_phase2(self):
        """Phase 2: upload staged file via rclone subprocess (progress captured live)."""
        ctx = getattr(self, '_pending_cloud_upload', None)
        if not ctx:
            return
        self._pending_cloud_upload = None
        local_file = ctx["local_file"]

        if not os.path.exists(local_file):
            self.log(f"CLOUD ERROR: Staged file not found: {local_file}")
            self.handle_finished(1, 0)
            return

        prof     = ctx["profile"]
        provider = ctx["provider"]
        bucket   = prof.get("bucket", "")
        obj_key  = ctx["object_key"]

        # Build rclone remote config inline via env vars / flags
        # Supports: AWS S3, Backblaze B2, Wasabi, Generic S3, GCS, Azure
        access_key = prof.get("access_key", "")
        secret_key = self.decrypt_pw(prof.get("secret_key", "")) if prof.get("secret_key") else ""
        region     = prof.get("region", "us-east-1") or "us-east-1"
        endpoint   = prof.get("endpoint_url", "").strip()

        if provider in ("AWS S3", "Backblaze B2", "Wasabi", "Generic S3"):
            rtype = "s3"
            remote_path = f":s3,access_key_id='{access_key}',secret_access_key='{secret_key}',region='{region}'"
            if endpoint:
                remote_path += f",endpoint='{endpoint}'"
            if provider == "Wasabi" and not endpoint:
                remote_path += ",endpoint='s3.wasabisys.com'"
            remote_path += f":{bucket}/{obj_key}"
        elif provider == "Google Cloud Storage":
            # GCS: access_key field holds path to service account JSON
            sa_file = access_key
            remote_path = f":gcs,service_account_file='{sa_file}':{bucket}/{obj_key}"
        elif provider == "Azure Blob":
            # Azure: access_key=account name, secret_key=account key
            remote_path = f":azureblob,account='{access_key}',key='{secret_key}':{bucket}/{obj_key}"
        else:
            self.log(f"CLOUD ERROR: Unknown provider '{provider}'. Cannot upload.")
            self.handle_finished(1, 0)
            return

        self.log(f"--- Cloud Backup Phase 2: rclone upload → {provider} bucket '{bucket}' ---")
        self.update_job_state("Running", f"Uploading to {provider}…")

        # Build rclone command; --progress --stats 5s feeds live % to stderr → _emit_progress
        rclone_cmd = (
            f"rclone copyto --progress --stats 5s "
            f"--transfers 4 --checkers 8 --contimeout 60s --timeout 300s "
            f"'{local_file}' {remote_path}"
        )
        cleanup = f"rm -f '{local_file}'"
        bash = (
            f"#!/bin/bash\n"
            f"trap '{cleanup}; exit 1' SIGINT SIGTERM\n"
            f"echo \"CLOUD: Uploading {obj_key} ({os.path.getsize(local_file)//1024//1024} MB)…\"\n"
            f"{rclone_cmd}\n"
            f"STATUS=$?\n"
            f"{cleanup}\n"
            f"if [ $STATUS -ne 0 ]; then echo \"CLOUD ERROR: rclone upload failed (exit $STATUS).\"; exit $STATUS; fi\n"
            f"echo \"CLOUD: Upload complete → {provider}/{bucket}/{obj_key}\"\n"
            f"exit 0\n"
        )
        with open("/tmp/archvault_cloud.sh", "w") as f: f.write(bash)
        os.chmod("/tmp/archvault_cloud.sh", 0o755)

        # Reuse the QProcess (it's idle after phase 1 finished)
        self.process.start("/tmp/archvault_cloud.sh")
        self.update_job_state(pid=self.process.processId())

    def _on_cloud_upload_done(self, exit_code):
        """Legacy hook kept for compatibility; rclone now runs via QProcess directly."""
        self.handle_finished(exit_code, 0)


    # ─────────────────────────────────────────────────────────────────────
    # RSYNC INCREMENTAL ENGINE
    # ─────────────────────────────────────────────────────────────────────
    def start_rsync_backup(self, name, prof, cat, dest_base, mount_cmd, cleanup_cmd):
        """Time Machine-style incremental: hardlinks unchanged, copies changed."""
        src_mode = prof.get("source_mode", "Full System")
        src_path = prof.get("source_path", "/")
        if src_mode == "Full System": src_path = "/"
        snap_name  = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        snap_dir   = f"{dest_base}/{snap_name}"
        latest_lnk = f"{dest_base}/latest"
        excl = ""
        if getattr(self, "settings", {}).get("exclude_cache", True):
            excl = "--exclude=\'*/.cache/*\' --exclude=\'*/.local/share/Trash/*\' "
            self.log("SYS: Smart Exclusion: ignoring ~/.cache and Trash.")
        sys_excl = ""
        if src_path == "/":
            for d in ["/proc", "/sys", "/dev", "/tmp", "/run"]:
                sys_excl += f"--exclude={d}/ "
        bash_script = (
            f"#!/bin/bash\n"
            f"trap \'kill -9 $CMD_PID 2>/dev/null; rm -rf \"{snap_dir}\"; {cleanup_cmd}; exit 1\' SIGINT SIGTERM\n"
            f"{mount_cmd}\n"
            f"if [ $? -ne 0 ]; then echo \"ERROR: Pre-flight mount failed.\"; exit 1; fi\n"
            f"echo \"--- Pre-Flight Disk Space Check ---\"\n"
            f"SRC_HUMAN=$(du -sh \"{src_path}\" 2>/dev/null | awk \'{{print $1}}\')\n"
            f"echo \"Source size: $SRC_HUMAN\"\n"
            f"DEST_FREE=$(df -h \"{dest_base}\" 2>/dev/null | awk \'NR==2{{print $4}}\')\n"
            f"echo \"Destination free: $DEST_FREE\"\n"
            f"echo \"--- Starting Incremental rsync Backup ---\"\n"
            f"echo \"Snapshot directory: {snap_dir}\"\n"
            f"mkdir -p \"{snap_dir}\"\n"
            f"LINK=\"\"\n"
            f"if [ -L \"{latest_lnk}\" ]; then LINK=\"--link-dest=$(readlink -f \"{latest_lnk}\")\"; fi\n"
            f"rsync -avz --delete {excl}{sys_excl}$LINK \"{src_path}/\" \"{snap_dir}/\" & CMD_PID=$!\n"
            f"wait $CMD_PID\n"
            f"SEND_STATUS=$?\n"
            f"if [ $SEND_STATUS -eq 0 ]; then\n"
            f"    ln -sfn \"{snap_dir}\" \"{latest_lnk}\"\n"
            f"    echo \"SUCCESS: Incremental snapshot complete. latest -> {snap_name}\"\n"
            f"fi\n"
            f"{cleanup_cmd}\n"
            f"if [ $SEND_STATUS -ne 0 ]; then exit $SEND_STATUS; fi\n"
            f"exit 0\n"
        )
        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())


    # ─────────────────────────────────────────────────────────────────────
    # NOTIFICATION DISPATCH (email + webhook) — called from handle_finished
    # ─────────────────────────────────────────────────────────────────────
    def dispatch_notification(self, prof, success, job_type, target):
        import threading
        notif_on = prof.get("notif_on", "Never")
        if notif_on == "Never": return
        if notif_on == "Failure Only" and success: return
        if notif_on == "Success Only" and not success: return
        status_word = "Succeeded" if success else "FAILED"
        subject = f"ArchVault — {job_type} {status_word}: {target}"
        body    = (f"ArchVault Notification\n\n"
                   f"Operation : {job_type}\n"
                   f"Target    : {target}\n"
                   f"Result    : {status_word}\n\n"
                   f"Check the Jobs tab for full logs.")
        channel = prof.get("notif_channel", "Email")
        def _send():
            if channel in ("Email", "Both"): self._send_email(prof, subject, body)
            if channel in ("Webhook (Discord / Slack)", "Both"): self._send_webhook(prof, subject, body, success)
        threading.Thread(target=_send, daemon=True).start()

    def _send_email(self, prof, subject, body):
        import smtplib
        from email.mime.text import MIMEText
        try:
            host = prof.get("notif_smtp_host", ""); port = int(prof.get("notif_smtp_port", 587))
            user = prof.get("notif_smtp_user", ""); pw = self.decrypt_pw(prof.get("notif_smtp_pass", ""))
            to = prof.get("notif_to", ""); frm = prof.get("notif_from", "") or user
            if not (host and to): self.log("NOTIF: Email skipped — SMTP host or recipient not configured."); return
            msg = MIMEText(body); msg["Subject"] = subject; msg["From"] = frm; msg["To"] = to
            with smtplib.SMTP(host, port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                if user and pw: srv.login(user, pw)
                srv.sendmail(frm, [to], msg.as_string())
            self.log(f"NOTIF: Email notification sent to {to}")
        except Exception as e:
            self.log(f"NOTIF ERROR: Email failed: {e}")

    def _send_webhook(self, prof, subject, body, success):
        import urllib.request, json as _json
        url = prof.get("notif_webhook_url", "")
        if not url: self.log("NOTIF: Webhook skipped — URL not configured."); return
        color = 0x10b981 if success else 0xef4444
        payload = _json.dumps({
            "content": None,
            "embeds": [{"title": subject, "description": body.replace("\\n", "\n"), "color": color}]
        }).encode()
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            self.log("NOTIF: Webhook delivered successfully.")
        except Exception as e:
            self.log(f"NOTIF ERROR: Webhook failed: {e}")


    # ─────────────────────────────────────────────────────────────────────
    # FEATURE 2: GPG ENCRYPTION AT REST
    # ─────────────────────────────────────────────────────────────────────
    def _gpg_encrypt_archive(self, archive_path, prof):
        """Encrypt a finished archive with GPG AES-256 symmetric, then delete plaintext."""
        passphrase = self.decrypt_pw(prof.get("encrypt_pass", ""))
        if not passphrase:
            self.log("ENCRYPT ERROR: No passphrase set — skipping encryption."); return
        enc_path = archive_path + ".gpg"
        self.log(f"ENCRYPT: Encrypting {os.path.basename(archive_path)} with GPG AES-256...")
        try:
            import subprocess as _sp
            result = _sp.run(
                ["gpg", "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
                 "--passphrase-fd", "0", "--output", enc_path, archive_path],
                input=passphrase.encode(), capture_output=True, timeout=3600
            )
            if result.returncode == 0:
                os.remove(archive_path)
                self.log(f"ENCRYPT: Archive encrypted → {os.path.basename(enc_path)}")
                self.log("ENCRYPT: Original plaintext archive removed.")
            else:
                self.log(f"ENCRYPT ERROR: GPG failed: {result.stderr.decode().strip()}")
        except FileNotFoundError:
            self.log("ENCRYPT ERROR: gpg not found — install gnupg and retry.")
        except Exception as e:
            self.log(f"ENCRYPT ERROR: {e}")

    # --- MAIN BACKUP ENTRY POINT ---
    def start_backup_process(self):
        t_str = self.target_combo.currentText()
        if not t_str: return QMessageBox.warning(self, "Error", "No target selected.")
        self.active_job_type = "backup"
        self.btn_run_backup.setEnabled(False)
        self.btn_backup_pause.setEnabled(True)
        self.btn_backup_stop.setEnabled(True)
        self.console.clear()

        cat_raw, name = t_str.split(": ", 1)
        cat = cat_raw.lower()
        if name not in self.profiles.get(cat, {}):
            self.log(f"ERROR: Could not locate profile '{name}'.")
            return self.handle_finished(1, 0)

        prof     = self.profiles[cat][name]
        is_ext4  = "Ext4" in self.engine_combo.currentText()
        src_mode = prof.get("source_mode", "Full System")
        src_path = prof.get("source_path", "/")
        if src_mode == "Full System": src_path = "/"

        self.register_job("Backup", t_str)
        self.log(f"--- Executing ArchVault '{name}' Target: {src_mode} ---")
        # Store for handle_finished encryption + notification
        self._last_backup_prof       = prof
        self._last_backup_target_str = t_str
        self._last_backup_file       = None  # filled in once out_file is known

        # --- CLOUD PATH ---
        if cat == "cloud":
            return self.start_cloud_backup(name, prof, t_str)

        # --- LOCAL / NETWORK / USB PATH ---
        # Check if rsync incremental engine selected
        if "rsync" in self.engine_combo.currentText().lower():
            if cat == "cloud":
                self.log("ERROR: rsync engine not supported for cloud targets.")
                return self.handle_finished(1, 0)
            # Build mount + dest identically to the standard path below, but route to rsync
            if cat == "network":
                _mnt   = "/tmp/archvault_nas"
                _pw    = self.decrypt_pw(prof.get("password", "")).replace("'", "'\''")
                _dom   = prof.get("domain", "")
                if "SMB" in prof.get("protocol", "SMB"):
                    _ds      = f",domain='{_dom}'" if _dom else ""
                    _mnt_cmd = (f"umount -l {_mnt} 2>/dev/null; mkdir -p {_mnt}; "
                                f"mount -t cifs -o username='{prof.get('username','')}',password='{_pw}'"
                                f"{_ds},noserverino,nocase,vers=3.0 '{prof['path']}' {_mnt}")
                else:
                    _mnt_cmd = f"umount -l {_mnt} 2>/dev/null; mkdir -p {_mnt}; mount -t nfs '{prof['path']}' {_mnt}"
                _dest_base = f"{_mnt}/{name.replace(' ', '_')}_incremental"
                _cleanup   = f"umount -l {_mnt} >/dev/null 2>&1"
                self._last_backup_dest = None
            else:
                _dest      = prof['path']
                _dest_base = f"{_dest}/{name.replace(' ', '_')}_incremental"
                _mnt_cmd   = f"mkdir -p '{_dest_base}'"
                _cleanup   = ""
                self._last_backup_dest = _dest_base
            self._last_backup_profile = name
            return self.start_rsync_backup(name, prof, cat, _dest_base, _mnt_cmd, _cleanup)

        date_str     = datetime.now().strftime("%Y-%m-%d")
        time_str_fmt = datetime.now().strftime("%H%M")
        datetime_str = datetime.now().strftime("%Y-%m-%d_%H%M")
        hostname     = getattr(os.uname(), "nodename", "Linux")

        name_fmt    = getattr(self, "settings", {}).get("backup_name_format", "ArchVault_%profile%_%datetime%")
        custom_name = (name_fmt
                       .replace("%profile%", name.replace(" ", "_"))
                       .replace("%date%", date_str)
                       .replace("%time%", time_str_fmt)
                       .replace("%datetime%", datetime_str)
                       .replace("%hostname%", hostname))

        snap_pt = "/.archvault_snapshot"

        if cat == "network":
            mnt    = "/tmp/archvault_nas"
            pw     = self.decrypt_pw(prof.get("password", "")).replace("'", "'\\''")
            domain = prof.get("domain", "")
            if "SMB" in prof.get("protocol", "SMB"):
                dom_str   = f",domain='{domain}'" if domain else ""
                mount_cmd = f"umount -l {mnt} 2>/dev/null; mkdir -p {mnt}; mount -t cifs -o username='{prof.get('username','')}',password='{pw}'{dom_str},noserverino,nocase,vers=3.0 '{prof['path']}' {mnt}"
            else:
                mount_cmd = f"umount -l {mnt} 2>/dev/null; mkdir -p {mnt}; mount -t nfs '{prof['path']}' {mnt}"
            out_base    = f"{mnt}/{custom_name}"
            cleanup_cmd = f"umount -l {mnt} >/dev/null 2>&1"
            self._last_backup_dest = None
        elif cat == "sftp":
            host    = prof.get("hostname", "")
            port    = prof.get("port", "22")
            user    = prof.get("username", "")
            rpath   = prof.get("remote_path", "/backup").rstrip("/")
            auth    = prof.get("auth_method", "Password")
            kfile   = prof.get("key_file", "").strip()
            tm      = prof.get("transfer_mode", "rsync_ssh")   # "rsync_ssh" | "sshfs"

            # ── Shared SSH credential helpers ─────────────────────────────────
            # Build the reusable ssh/rsync -e argument once, for both modes.
            # For password auth we prefix commands with sshpass.
            import shutil as _shutil
            _ssh_base = f"ssh -p {port} -o StrictHostKeyChecking=no -o BatchMode={'yes' if auth == 'SSH Key' else 'no'} -o ConnectTimeout=10"
            if auth == "SSH Key" and kfile:
                _ssh_base += f" -i '{kfile}'"
            # sshpass wrapper (password mode only)
            pw_plain = self.decrypt_pw(prof.get("password", "")).replace("'", "'\\''") if auth == "Password" else ""
            if auth == "Password":
                if not _shutil.which("sshpass"):
                    self.log("CRITICAL: Password auth requires 'sshpass' (sudo pacman -S sshpass). Aborting.")
                    return self.handle_finished(1, 0)
                _sshpass_prefix = f"sshpass -p '{pw_plain}' "
                _ssh_cmd        = f"{_sshpass_prefix}{_ssh_base}"
            else:
                _sshpass_prefix = ""
                _ssh_cmd        = _ssh_base
                # Ensure key has correct permissions — SSH rejects keys with wrong mode
                if kfile and os.path.exists(kfile):
                    os.chmod(kfile, 0o600)

            if tm == "rsync_ssh":
                # ── rsync over SSH ────────────────────────────────────────────
                # Direct push — no FUSE, no mount point.
                # Time Machine-style hardlink snapshots via --link-dest=../latest.
                # Each run creates  rpath/<timestamp>/  and updates rpath/latest → it.
                if "Bare Metal" in src_mode:
                    self.log("ERROR: Bare Metal imaging is not supported with rsync-over-SSH.")
                    self.log("TIP: Use a Local Storage or USB profile for Bare Metal backups.")
                    return self.handle_finished(1, 0)

                remote_snap_dir = f"{rpath}/{custom_name}"
                remote_latest   = f"{rpath}/latest"

                if src_mode == "Full System":
                    rsync_src = "/"
                else:
                    rsync_src = src_path.rstrip("/") + "/"

                excl = "--exclude=/proc --exclude=/sys --exclude=/dev --exclude=/tmp --exclude=/run"
                if getattr(self, "settings", {}).get("exclude_cache", True):
                    excl += " --exclude='*/.cache/*' --exclude='*/.local/share/Trash/*'"

                rsync_e   = f"-e \"{_ssh_base}\""
                # --link-dest uses a path relative to the *destination* on the remote,
                # so ../latest resolves correctly regardless of snapshot name.
                rsync_cmd = (
                    f"{_sshpass_prefix}rsync --archive --partial --delete "
                    f"--info=progress2 --human-readable --compress "
                    f"--link-dest=../latest "
                    f"{excl} {rsync_e} "
                    f"'{rsync_src}' '{user}@{host}:{remote_snap_dir}/'"
                )
                # SSH helper for remote commands (mkdir, symlink)
                _ssh_remote = f"{_ssh_cmd} {user}@{host}"

                pre_hook  = prof.get("pre_hook", "").strip()
                post_hook = prof.get("post_hook", "").strip()
                pre_hook_block = ""
                if pre_hook:
                    self.log(f"SYS: Pre-backup hook: {pre_hook}")
                    pre_hook_block = (
                        'echo "--- Running Pre-Backup Hook ---"\n'
                        f"{pre_hook}\n"
                        'if [ $? -ne 0 ]; then echo "CRITICAL: Pre-hook failed — aborting."; exit 1; fi\n'
                    )
                post_hook_block = ""
                if post_hook:
                    self.log(f"SYS: Post-backup hook: {post_hook}")
                    post_hook_block = (
                        "if [ $SEND_STATUS -eq 0 ]; then\n"
                        '    echo "--- Running Post-Backup Hook ---"\n'
                        f"    {post_hook}\n"
                        "fi\n"
                    )

                self.log(f"SYS: rsync-over-SSH (auth={auth}) → {user}@{host}:{port}{remote_snap_dir}")
                self.log("SYS: Incremental hardlink snapshot — only changed files transferred.")

                bash_script = f"""#!/bin/bash
set -o pipefail
trap 'kill -9 $CMD_PID 2>/dev/null; exit 1' SIGINT SIGTERM

echo "--- Pre-flight: Testing SSH connectivity to {user}@{host}:{port} ---"
{_ssh_remote} "echo SSH_OK" 2>&1
if [ $? -ne 0 ]; then
    echo "CRITICAL: Cannot connect to {user}@{host}:{port} — check hostname, port, credentials, and that the remote SSH server is running."
    exit 1
fi
echo "Pre-flight: Connection OK"

echo "--- Pre-flight: Ensuring remote directory {rpath} exists ---"
{_ssh_remote} "mkdir -p '{rpath}'" 2>&1
if [ $? -ne 0 ]; then
    echo "CRITICAL: Could not create remote directory '{rpath}' — check permissions."
    exit 1
fi

{pre_hook_block}
echo "--- Starting rsync transfer to {user}@{host}:{remote_snap_dir} ---"
{rsync_cmd} & CMD_PID=$!; wait $CMD_PID
SEND_STATUS=$?

if [ $SEND_STATUS -eq 0 ]; then
    echo "--- Updating 'latest' symlink on remote ---"
    {_ssh_remote} "ln -sfn '{remote_snap_dir}' '{remote_latest}'" 2>/dev/null || true
fi
{post_hook_block}
if [ $SEND_STATUS -ne 0 ]; then
    echo "FAILED: rsync exited with code $SEND_STATUS"
    exit $SEND_STATUS
fi
echo "SUCCESS: Snapshot committed to {user}@{host}:{remote_snap_dir}"
exit 0
"""
                with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
                os.chmod("/tmp/archvault_run.sh", 0o755)
                self.process.start("/tmp/archvault_run.sh")
                self.update_job_state(pid=self.process.processId())
                return  # ← rsync_ssh is fully self-contained; skip normal script builder

            else:
                # ── sshfs mount ───────────────────────────────────────────────
                mnt   = "/tmp/archvault_sftp"
                _fuse_opts = f"StrictHostKeyChecking=no,port={port},reconnect,ServerAliveInterval=15,ConnectTimeout=10"
                if auth == "SSH Key" and kfile:
                    mount_cmd = (
                        f"umount -l {mnt} 2>/dev/null; fusermount -u {mnt} 2>/dev/null; "
                        f"mkdir -p {mnt}; "
                        f"sshfs {user}@{host}:{rpath} {mnt} -o {_fuse_opts},IdentityFile='{kfile}'"
                    )
                else:
                    # sshpass pipes password directly into sshfs
                    mount_cmd = (
                        f"umount -l {mnt} 2>/dev/null; fusermount -u {mnt} 2>/dev/null; "
                        f"mkdir -p {mnt}; "
                        f"{_sshpass_prefix}sshfs {user}@{host}:{rpath} {mnt} -o {_fuse_opts}"
                    )
                out_base    = f"{mnt}/{custom_name}"
                cleanup_cmd = f"fusermount -u {mnt} >/dev/null 2>&1; umount -l {mnt} >/dev/null 2>&1; true"
                self._last_backup_dest = None
                self.log(f"SYS: SFTP sshfs (auth={auth}) — {user}@{host}:{port}{rpath}")
        else:
            dest        = prof['path']
            mount_cmd   = f"mkdir -p '{dest}'"
            out_base    = f"{dest}/{custom_name}"
            cleanup_cmd = ""
            self._last_backup_dest    = dest
            self._last_backup_profile = name

        task_retention = None
        for task_name, task_data in getattr(self, "scheduled_tasks", {}).items():
            if task_data.get("target") == t_str:
                task_retention = task_data.get("retention")
                break
        self._last_backup_retention = task_retention

        btrfs_clean      = ""
        val_block        = ""
        pre_flight_btrfs = ""
        # ext may be overridden later by compression algo
        _default_ext = ".tar.gz" if is_ext4 else ".btrfs"
        out_file = f"{out_base}{_default_ext}"

        if "Bare Metal" in src_mode:
            parts = prof.get("bm_included", [])
            if not parts:
                self.log("CRITICAL ERROR: No partitions selected for Bare Metal imaging.")
                return self.handle_finished(1, 0)
            engine_cmd = "echo 'Starting Bare Metal Block-Level Imaging...'\nSEND_STATUS=0\n"
            for p in parts:
                safe_name  = p.replace("/dev/", "")
                part_out   = f"{out_base}_{safe_name}.img.gz"
                engine_cmd += f"echo 'Imaging {p} to {part_out} (compressed)...'\n"
                engine_cmd += f"dd if='{p}' bs=4M status=progress | gzip > '{part_out}' & CMD_PID=$!; wait $CMD_PID\n"
                engine_cmd += f"if [ $? -ne 0 ]; then SEND_STATUS=1; echo 'Failed on {p}'; fi\n"
            trap_cmd = f"trap 'kill -9 $CMD_PID 2>/dev/null; rm -f \"{out_base}\"*.img.gz; {cleanup_cmd}; exit 1' SIGINT SIGTERM"
        else:
            # Track whether this is a full-system tar run (needed for exit-1 promotion below)
            self._is_full_system_tar = is_ext4 and (src_mode == "Full System")

            if not is_ext4:
                pre_flight_btrfs = (
                    f'echo "--- Verifying Btrfs Subvolume Status ---"\n'
                    f'if ! btrfs subvolume show "{src_path}" >/dev/null 2>&1; then\n'
                    f'    echo "CRITICAL ERROR: {src_path} is a standard directory, not a Btrfs subvolume!"\n'
                    f'    echo "Change your Filesystem Engine to Ext4 / Universal to backup standard folders."\n'
                    f'    exit 1\n'
                    f'fi\n'
                )
            if is_ext4:
                # FEATURE: compression algo + level from profile settings
                comp_algo  = prof.get("compress_algo", "gzip")
                comp_level = prof.get("compress_level", 6)
                if comp_algo == "zstd":
                    out_file   = f"{out_base}.tar.zst"
                    comp_flag  = f"--use-compress-program='zstd -T0 -{comp_level}'"
                    val_cmd    = f"zstd -t '{out_file}' > /dev/null"
                    self.log(f"SYS: Compression: zstd level {comp_level} (multi-threaded)")
                else:
                    out_file   = f"{out_base}.tar.gz"
                    # Use pigz (parallel gzip) if available — 4-8x faster on multi-core
                    comp_flag  = (
                        f"--use-compress-program='pigz -{comp_level}'"
                        if __import__('shutil').which('pigz')
                        else f"--use-compress-program='gzip -{comp_level}'"
                    )
                    _comp_name = "pigz" if __import__('shutil').which('pigz') else "gzip"
                    val_cmd    = f"tar -tzf '{out_file}' > /dev/null"
                    self.log(f"SYS: Compression: {_comp_name} level {comp_level}{' (parallel)' if _comp_name == 'pigz' else ''}")
                ckpt = "--checkpoint=10000 --checkpoint-action=echo='%{%Y-%m-%d %H:%M:%S}t: %d0000 records processed'"
                cache_excl = ""
                if getattr(self, "settings", {}).get("exclude_cache", True):
                    cache_excl = "--exclude='*/.cache/*' --exclude='*/.local/share/Trash/*'"
                    self.log("SYS: Smart Exclusion Enabled: Ignoring ~/.cache and Trash folders.")

                # Live-system safety flags:
                #   --ignore-failed-read  : skip files that change/vanish mid-read instead of aborting
                #   --warning=no-file-changed : suppress the per-file "changed as we read it" stderr
                #     spam so logs stay readable (we handle exit code 1 in the script instead)
                # These flags are always applied for / and recommended for any live path.
                live_flags = "--ignore-failed-read --warning=no-file-changed"
                is_full_system = (src_path == "/")

                tar_opts = f"{ckpt} {live_flags} {cache_excl}".strip()

                # ── pv detection: real bytes/speed/ETA progress ───────────────
                import shutil as _sh
                _pv = _sh.which("pv")
                if _pv:
                    self.log("SYS: pv detected — real-time bytes/speed/ETA progress enabled.")
                    # Determine the compression binary to use in the explicit pipe
                    if comp_algo == "zstd":
                        _comp_bin = f"zstd -T0 -{comp_level} -c"
                    else:
                        _comp_bin = (f"pigz -{comp_level} -c" if _sh.which("pigz")
                                     else f"gzip -{comp_level} -c")
                    # pv -F "PROGRESS|BYTES|RATE|ETA" — one line every 2 s to stderr
                    # -s gives pv the total size for % calculation
                    _pv_fmt = '"%p|%b|%r|%e"'
                    if is_full_system:
                        _src_for_size = "/"
                        _tar_src = "/"
                        _excludes = "--exclude=/proc --exclude=/sys --exclude=/dev --exclude=/tmp --exclude=/run"
                        self.log("SYS: Live-system mode: --ignore-failed-read enabled.")
                        self.log("SYS: Note: For a byte-perfect frozen snapshot, use Btrfs engine or LVM snapshots.")
                    else:
                        _src_for_size = src_path
                        _tar_src = f"-C '{src_path}' ."
                        _excludes = ""
                    engine_cmd = (
                        f"SRCSIZE=$(du -sb '{_src_for_size}' 2>/dev/null | awk '{{print $1}}' || echo 0); "
                        f"(tar {tar_opts} {_excludes} --ignore-failed-read --warning=no-file-changed -cpO {_tar_src} "
                        f"| pv -F {_pv_fmt} -i 2 -n -s $SRCSIZE "
                        f"| {_comp_bin} > '{out_file}') & CMD_PID=$!; wait $CMD_PID"
                    )
                else:
                    self.log("SYS: pv not found — install 'pv' for real-time bytes/speed progress. Using checkpoint fallback.")
                    if is_full_system:
                        self.log("SYS: Live-system mode: --ignore-failed-read enabled.")
                        self.log("SYS: Note: For a byte-perfect frozen snapshot, use Btrfs engine or LVM snapshots.")
                        engine_cmd = f"tar {tar_opts} {comp_flag} --exclude=/proc --exclude=/sys --exclude=/dev --exclude=/tmp --exclude=/run -cpf '{out_file}' / & CMD_PID=$!; wait $CMD_PID"
                    else:
                        engine_cmd = f"tar {tar_opts} {comp_flag} -C '{src_path}' -cpf '{out_file}' . & CMD_PID=$!; wait $CMD_PID"
            else:
                engine_cmd  = f"btrfs subvolume snapshot -r '{src_path}' {snap_pt} && btrfs send {snap_pt} -f '{out_file}' & CMD_PID=$!; wait $CMD_PID"
                btrfs_clean = f"btrfs subvolume delete {snap_pt} >/dev/null 2>&1;"
                val_cmd     = f"btrfs receive --dump -f '{out_file}' > /dev/null"

            engine_cmd += "\nSEND_STATUS=$?"
            # For full-system tar backups, exit code 1 means "some files changed mid-read"
            # which is completely normal on a live OS. Promote it to 0 with a clear warning
            # so the job doesn't incorrectly show as Failed.
            if is_ext4 and getattr(self, '_is_full_system_tar', False):
                engine_cmd += (
                    "\nif [ $SEND_STATUS -eq 1 ]; then"
                    "\n    echo 'WARNING: Some files were modified during backup (normal on a live OS).'"
                    "\n    echo 'WARNING: --ignore-failed-read was active; changed files skipped. Archive is usable.'"
                    "\n    echo 'WARNING: For a frozen snapshot on ext4, use Btrfs engine or stop active services.'"
                    "\n    SEND_STATUS=0"
                    "\nfi"
                )
            do_validate = (self.chk_val_backup.isChecked() if hasattr(self, 'chk_val_backup')
                           else getattr(self, "settings", {}).get("auto_validate", False))
            if do_validate:
                val_block = (
                    f'if [ $SEND_STATUS -eq 0 ]; then '
                    f'echo "--- Running Post-Backup Auto-Validation ---"; '
                    f'{val_cmd}; '
                    f'if [ $? -ne 0 ]; then echo "CRITICAL ERROR: VALIDATION FAILED!"; SEND_STATUS=1; '
                    f'else echo "SUCCESS: Auto-validation passed."; fi; fi'
                )
            trap_cmd = f"trap 'kill -9 $CMD_PID 2>/dev/null; rm -f \"{out_file}\"; {btrfs_clean} {cleanup_cmd}; exit 1' SIGINT SIGTERM"

        # ── Pre-flight disk space check (real df, not just estimate) ─────────
        if "Bare Metal" not in src_mode:
            _sp_dest = "/tmp/archvault_nas" if cat == "network" else dest
            _div = 2 if is_ext4 else 5
            _aq = "'"
            space_check_block = (
                "echo \"--- Pre-Flight Disk Space Check ---\"\n"
                f"SRC_BYTES=$(du -sb \"{src_path}\" 2>/dev/null | awk {_aq}{{print $1}}{_aq})\n"
                f"SRC_HUMAN=$(du -sh \"{src_path}\" 2>/dev/null | awk {_aq}{{print $1}}{_aq})\n"
                "echo \"Source size: $SRC_HUMAN\"\n"
                f"EST_BYTES=$(($SRC_BYTES / {_div}))\n"
                f"DEST_FREE=$(df -B1 \"{_sp_dest}\" 2>/dev/null | awk {_aq}NR==2{{print $4}}{_aq})\n"
                f"FREE_HUMAN=$(df -h \"{_sp_dest}\" 2>/dev/null | awk {_aq}NR==2{{print $4}}{_aq})\n"
                "echo \"Destination free: $FREE_HUMAN\"\n"
                "if [ -n \"$DEST_FREE\" ] && [ \"$EST_BYTES\" -gt \"$DEST_FREE\" ]; then\n"
                "    echo \"CRITICAL: Insufficient disk space -- estimated archive exceeds destination free space. Aborting.\"\n"
                "    exit 1\n"
                "fi\n"
                "echo \"Space check: PASSED\"\n"
            )
        else:
            space_check_block = ""

        # ── Network retention: prune old NAS backups before unmounting ────────
        if cat == "network":
            _ndays = getattr(self, "_last_backup_retention", None) or getattr(self, "settings", {}).get("global_retention", 7)
            _nsafe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)
            _aq2 = "'"
            net_retention_block = (
                "if [ $SEND_STATUS -eq 0 ]; then\n"
                f"    echo \"--- Network Retention: Pruning {name} backups older than {_ndays} days ---\"\n"
                f"    find \"/tmp/archvault_nas\" -maxdepth 1 -type f"
                " \\( -name \"*.tar.gz\" -o -name \"*.btrfs\" -o -name \"*.img.gz\" \\)"
                f" -iname \"*{_nsafe}*\" -mtime +{_ndays}"
                " | while IFS= read -r OLD_FILE; do\n"
                "        rm -f \"$OLD_FILE\" && echo \"RETENTION: Pruned $OLD_FILE\"\n"
                "    done\n"
                "fi\n"
            )
        else:
            net_retention_block = ""

        self._last_backup_file = out_file  # for GPG encryption in handle_finished

        # ── Pre / Post hook injection ─────────────────────────────────────────
        pre_hook  = prof.get("pre_hook", "").strip()
        post_hook = prof.get("post_hook", "").strip()
        pre_hook_block = ""
        if pre_hook:
            self.log(f"SYS: Pre-backup hook: {pre_hook}")
            pre_hook_block = (
                'echo "--- Running Pre-Backup Hook ---"\n'
                f"{pre_hook}\n"
                'if [ $? -ne 0 ]; then echo "ERROR: Pre-backup hook failed — aborting backup."; exit 1; fi\n'
                'echo "Pre-backup hook: OK"\n'
            )
        post_hook_block = ""
        if post_hook:
            self.log(f"SYS: Post-backup hook: {post_hook}")
            post_hook_block = (
                "if [ $SEND_STATUS -eq 0 ]; then\n"
                '    echo "--- Running Post-Backup Hook ---"\n'
                f"    {post_hook}\n"
                '    if [ $? -ne 0 ]; then echo "WARNING: Post-backup hook failed (backup succeeded)."; fi\n'
                "fi\n"
            )

        bash_script = "#!/bin/bash\n" + trap_cmd + "\n" + mount_cmd + "\n"
        bash_script += "if [ $? -ne 0 ]; then echo \"ERROR: Pre-flight access failed.\"; exit 1; fi\n"
        bash_script += pre_hook_block
        bash_script += pre_flight_btrfs
        bash_script += space_check_block
        bash_script += "echo \"Streaming data to destination...\"\n"
        bash_script += engine_cmd + "\n" + btrfs_clean + "\n" + val_block + "\n"
        bash_script += post_hook_block
        bash_script += net_retention_block

        # ── Manifest generation (tar.gz / tar.zst only — not bare metal / btrfs) ──
        # Write a gzip-compressed newline-delimited path list next to the archive.
        # Listing takes microseconds; the restore browser loads from this instead of
        # re-reading the archive. Only generated when the backup succeeded.
        if not ("Bare Metal" in src_mode) and is_ext4:
            manifest_file = out_file.replace(".tar.gz", ".manifest.gz").replace(".tar.zst", ".manifest.gz")
            bash_script += (
                "if [ $SEND_STATUS -eq 0 ]; then\n"
                f'    echo "--- Generating Browse Manifest ---"\n'
                f'    tar -tzf "{out_file}" 2>/dev/null | gzip -1 > "{manifest_file}" && '
                f'    echo "INFO: Manifest written to {manifest_file}" || '
                f'    echo "WARNING: Manifest generation failed (backup still valid)."\n'
                "fi\n"
            )

        bash_script += cleanup_cmd + "\n"
        bash_script += "if [ $SEND_STATUS -ne 0 ]; then exit $SEND_STATUS; fi\n"
        bash_script += "echo \"SUCCESS: Backup stream successfully committed to disk.\"\n"
        bash_script += "exit 0\n"
        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())

    # Override handle_finished to intercept cloud phase-2 trigger

    # ─────────────────────────────────────────────────────────────────────
    # ERROR DIAGNOSTICS — translate exit codes into human language
    # ─────────────────────────────────────────────────────────────────────
    def _diagnose_exit_code(self, code, job_type):
        """Return a dict with short summary, detail lines, and suggested actions."""

        # ── tar codes ────────────────────────────────────────────────────
        TAR = {
            1: {
                "short": "Some files could not be read — permission denied or files vanished (non-live-change error)",
                "detail": [
                    "Note: For Full System (/) backups, exit code 1 from 'file changed mid-read' is",
                    "automatically promoted to success (WARNING) by ArchVault — you should not see this",
                    "for a standard full-system tar job.",
                    "",
                    "If you do see this, it means a file was unreadable for a different reason:",
                    "  · Permission denied on a specific file or directory",
                    "  · A file disappeared between directory scan and the read attempt",
                    "  · Filesystem error on the source",
                ],
                "actions": [
                    "Check the captured error output above for the specific file path.",
                    "Ensure you are running ArchVault as root (sudo) — required for full-system access.",
                    "Exclude the problematic path if it is not critical.",
                    "Enable 'Validate Backup Upon Completion' to confirm the archive is readable.",
                    "Use Btrfs engine for a true frozen snapshot (requires btrfs on source).",
                ]
            },
            2: {
                "short": "Fatal tar error — archive is likely incomplete or corrupt",
                "detail": [
                    "tar exits 2 on a fatal error such as:",
                    "  · Destination ran out of disk space",
                    "  · Read permission denied on source files",
                    "  · Destination path does not exist or is not writable",
                    "  · Network share disconnected mid-stream",
                ],
                "actions": [
                    "Check the captured error output above for the specific file or path.",
                    "Verify the destination has sufficient free space (check 'df -h').",
                    "Ensure you have read access to all source files.",
                    "If backing up to a network share, verify the mount is stable.",
                    "Try running 'sudo tar -cpzf /tmp/test.tar.gz /etc' to test basic access.",
                ]
            },
        }
        # ── rsync codes ──────────────────────────────────────────────────
        RSYNC = {
            1:  {"short": "rsync syntax or usage error", "detail": ["The rsync command line was rejected — likely a bad path or option."], "actions": ["Check that the source path exists and is accessible.", "Review the captured SYS: lines above for the exact rsync message."]},
            2:  {"short": "rsync protocol incompatibility", "detail": ["Local and remote rsync versions are incompatible."], "actions": ["Run 'rsync --version' on both sides and ensure versions are close."]},
            5:  {"short": "rsync: error starting client-server protocol", "detail": ["Could not establish connection to the remote host."], "actions": ["Verify the network share is mounted and accessible.", "Check that rsync is installed on the remote server."]},
            11: {"short": "rsync: error in file I/O — likely out of disk space", "detail": ["Destination ran out of space mid-transfer."], "actions": ["Free up space on the destination drive.", "Review retention policy to prune old snapshots."]},
            23: {"short": "rsync partial transfer — some files could not be sent", "detail": ["rsync transferred most files but skipped some due to permission or I/O errors."], "actions": ["Check the captured error lines for specific files.", "Files owned by root or other users may need sudo access."]},
            24: {"short": "rsync: source files vanished mid-transfer", "detail": ["Files disappeared between the directory scan and the copy, likely due to active processes."], "actions": ["This is usually harmless for temporary/cache files.", "Stop processes that delete files during the backup if critical files are missing."]},
            30: {"short": "rsync: timeout — connection dropped", "detail": ["The rsync stream timed out, usually a network issue."], "actions": ["Check network stability to the destination.", "Increase rsync timeout if backing up over a slow or unreliable link."]},
        }
        # ── btrfs codes ──────────────────────────────────────────────────
        BTRFS = {
            1: {"short": "btrfs error — subvolume or stream operation failed", "detail": [
                "Common causes:",
                "  · Source is not a btrfs subvolume (use Ext4/Universal engine instead)",
                "  · Destination does not have enough space",
                "  · A previous snapshot at the same path already exists",
            ], "actions": [
                "Verify the source path is a btrfs subvolume: 'btrfs subvolume show <path>'",
                "Ensure destination is also on a btrfs filesystem for native receive.",
                "Check 'btrfs filesystem df <dest>' for space.",
                "Delete any stale /.archvault_snapshot if it exists: 'btrfs subvolume delete /.archvault_snapshot'",
            ]},
        }
        # ── dd / bare metal codes ────────────────────────────────────────
        DD = {
            1: {"short": "dd error — read or write failed during bare metal imaging", "detail": [
                "dd exits 1 on any I/O error. Common causes:",
                "  · Source drive has bad sectors",
                "  · Destination out of space or write-protected",
                "  · Source device disappeared (USB disconnect)",
            ], "actions": [
                "Run 'smartctl -a /dev/<device>' to check source drive health.",
                "Verify the destination has enough free space.",
                "Try a smaller block size: check if bs=512 works where bs=4M did not.",
            ]},
        }
        # ── Generic / mount codes ────────────────────────────────────────
        GENERIC = {
            126: {"short": "Permission denied — cannot execute the backup script", "detail": ["The backup script could not be executed (permission error)."], "actions": ["ArchVault should set the script executable. Try restarting the application as root."]},
            127: {"short": "Command not found — a required tool is missing", "detail": ["A binary called by the backup script was not found (e.g. tar, btrfs, rsync, gpg)."], "actions": ["Install the missing tool. Check the captured SYS: lines for which command failed.", "'which tar && which rsync && which btrfs' to verify availability."]},
            130: {"short": "Backup was interrupted (SIGINT / Ctrl-C)", "detail": ["The user or system interrupted the process."], "actions": ["Re-run the backup when ready."]},
            137: {"short": "Process killed — likely out of memory (OOM)", "detail": ["The OS killed the backup process (signal 9/SIGKILL). Usually caused by RAM exhaustion."], "actions": ["Close other applications to free memory.", "Back up a smaller source path, or split the backup into multiple profiles."]},
            143: {"short": "Process terminated by system (SIGTERM)", "detail": ["The process was asked to stop gracefully by the OS or another process."], "actions": ["Check if a system shutdown or service restart occurred.", "Re-run the backup manually."]},
        }

        # Determine which engine was active
        engine_text = ""
        if hasattr(self, 'engine_combo'):
            engine_text = self.engine_combo.currentText().lower()
        elif job_type == "restore":
            if hasattr(self, 'rst_engine'): engine_text = self.rst_engine.currentText().lower()

        lookup = GENERIC.get(code)
        if not lookup:
            if "btrfs" in engine_text:       lookup = BTRFS.get(code)
            elif "rsync" in engine_text:      lookup = RSYNC.get(code)
            elif "bare metal" in engine_text: lookup = DD.get(code)
            else:                             lookup = TAR.get(code)  # default: tar

        if not lookup:
            lookup = {
                "short": f"Unknown exit code {code} — see captured error output for details",
                "detail": ["This exit code is not in the ArchVault diagnostic database.",
                           "The captured error lines above are your best guide to what went wrong."],
                "actions": [
                    "Review the 'Captured Error Output' section above carefully.",
                    "Search for the specific error message online with the engine name.",
                    "Export the full error log from Jobs → Errors for offline analysis.",
                ]
            }
        return lookup

    def handle_finished(self, exit_code, exit_status=0):
        # If a cloud upload is staged and phase 1 succeeded, kick off phase 2
        if exit_code == 0 and getattr(self, '_pending_cloud_upload', None):
            self.log("SUCCESS: Phase 1 staging complete.")
            self._start_cloud_phase2()
            return

        finished_job_type = getattr(self, 'active_job_type', None)
        self.active_job_type = None

        if exit_code == 0:
            self.update_job_state("Completed", "Successfully committed to disk.", append_log=False)
            self.log("SUCCESS: Operation finished cleanly.")
            if finished_job_type == "backup":
                # FEATURE: GPG encryption at rest — encrypt the archive if profile requests it
                last_prof = getattr(self, "_last_backup_prof", None)
                last_file = getattr(self, "_last_backup_file", None)
                if last_prof and last_prof.get("encrypt") and last_file and os.path.exists(last_file):
                    self._gpg_encrypt_archive(last_file, last_prof)
                # FEATURE: Send notification
                if last_prof:
                    t_str_notif = getattr(self, "_last_backup_target_str", "")
                    self.dispatch_notification(last_prof, True, "Backup", t_str_notif)
                dest_dir       = getattr(self, '_last_backup_dest', None)
                profile_name   = getattr(self, '_last_backup_profile', None)
                retention_days = getattr(self, '_last_backup_retention', None)
                if retention_days is None:
                    retention_days = getattr(self, "settings", {}).get("global_retention", 7)
                if dest_dir and profile_name:
                    self.run_retention_pruning(dest_dir, profile_name, retention_days)
        else:
            # ── Build human-readable diagnostic ──────────────────────────
            diagnosis = self._diagnose_exit_code(exit_code, finished_job_type)
            job_desc  = f"Exit {exit_code}: {diagnosis['short']}"
            self.update_job_state("Failed", job_desc, append_log=True)

            self.log("━" * 60)
            self.log(f"FAILED: Operation exited with code {exit_code}")
            self.log(f"CAUSE:  {diagnosis['short']}")
            for detail_line in diagnosis['detail']:
                self.log(f"  ›  {detail_line}")

            # Pull the most relevant stderr lines collected during the run
            error_lines = getattr(self, '_stderr_error_lines', [])
            if error_lines:
                self.log("─── Captured Error Output ───────────────────────────────")
                # Show last 15 most relevant, skip pure progress lines
                shown = [l for l in error_lines if "records processed" not in l][-15:]
                for l in shown:
                    self.log(f"  {l}")

            self.log("─── Suggested Actions ───────────────────────────────────")
            for action in diagnosis['actions']:
                self.log(f"  ✦  {action}")
            self.log("━" * 60)

            # Append diagnostic summary to job log so it appears in error export
            if hasattr(self, 'current_job_log'):
                summary = [
                    "═" * 60,
                    f"EXIT CODE : {exit_code}",
                    f"CAUSE     : {diagnosis['short']}",
                    "DETAIL:",
                ] + [f"  › {d}" for d in diagnosis['detail']] + [
                    "CAPTURED ERRORS:",
                ] + (([f"  {l}" for l in error_lines[-20:]] if error_lines else ["  (none captured)"])) + [
                    "ACTIONS:",
                ] + [f"  ✦ {a}" for a in diagnosis['actions']] + ["═" * 60]
                self.current_job_log.extend(summary)
                self.write_jobs()

            # FEATURE: failure notification
            last_prof = getattr(self, "_last_backup_prof", None)
            if last_prof and finished_job_type == "backup":
                t_str_notif = getattr(self, "_last_backup_target_str", "")
                self.dispatch_notification(last_prof, False, "Backup", t_str_notif)

        self.current_job_id = None
        if hasattr(self, 'btn_run_backup'): self.btn_run_backup.setEnabled(True)
        if hasattr(self, 'btn_run_restore'): self.btn_run_restore.setEnabled(True)
        for btn in ['btn_backup_pause', 'btn_backup_stop', 'btn_restore_pause', 'btn_restore_stop']:
            if hasattr(self, btn): getattr(self, btn).setEnabled(False)
        self.is_paused = False
        for btn in ['btn_backup_pause', 'btn_restore_pause']:
            if hasattr(self, btn):
                getattr(self, btn).setText("Pause")
                getattr(self, btn).setStyleSheet("background-color: #ffc107; color: black; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        # Reset progress bar
        if hasattr(self, "progress_bar"):
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            from PyQt6.QtCore import QTimer as _QT
            _QT.singleShot(2500, lambda: [
                w.hide() for w in
                [getattr(self, "progress_bar", None), getattr(self, "progress_label", None)]
                if w
            ])
        if hasattr(self, 'refresh_dashboard'):
            self.refresh_dashboard()
        # Unmount any network share that was used as a restore source
        if finished_job_type == "restore":
            cleanup = getattr(self, "_rst_cleanup_cmd", "")
            if cleanup:
                import subprocess as _sp2
                _sp2.run(cleanup, shell=True)
                self._rst_cleanup_cmd = ""
                if hasattr(self, "_rst_mount_status"):
                    self._rst_mount_status.setText("🔌  Share unmounted after restore.")
                if hasattr(self, "_btn_rst_unmount"):
                    self._btn_rst_unmount.hide()
                    self._restore_mount_point = None

    def start_restore_process(self):
        source = self.rst_source.text().strip()
        engine = self.rst_engine.currentText()
        if not source or not os.path.exists(source):
            return QMessageBox.warning(self, "Error", "Please select a valid local or mounted backup file.")

        # ── BTRFS LIVE SUBVOLUME RESTORE ─────────────────────────────────────
        # When the Btrfs panel is active and source is a directory (live subvolume),
        # use  btrfs send | btrfs receive  instead of the file-based flow.
        btrfs_panel_active = (
            hasattr(self, '_rst_btrfs_panel') and
            self._rst_btrfs_panel.isVisible() and
            os.path.isdir(source)
        )
        if btrfs_panel_active:
            return self._start_btrfs_subvol_restore(source)


        is_selective = hasattr(self, 'rst_toggle_selective') and self.rst_toggle_selective.isChecked()
        
        if is_selective and "Bare Metal" not in engine:
            return self.start_selective_restore(source, engine)
        
        # Full restore (original logic)
        self.active_job_type = "restore"
        self.btn_run_restore.setEnabled(False)
        if hasattr(self, 'btn_restore_pause'): self.btn_restore_pause.setEnabled(True)
        if hasattr(self, 'btn_restore_stop'):  self.btn_restore_stop.setEnabled(True)
        self.console.clear()

        self.register_job("Restore", source)

        if "Bare Metal" in engine:
            dest = self.rst_dest_drive.currentText().split(" ")[0]
            if not dest: return self.handle_finished(1, 0)
            reply = QMessageBox.critical(self, "CRITICAL WARNING",
                f"You are about to irreversibly overwrite the raw block device:\n{dest}\n\n"
                "If this is your active running system drive, this will crash your OS instantly.\n\n"
                "Are you absolutely certain you want to proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                self.log("Restore aborted by user.")
                return self.handle_finished(1, 0)
            self.log(f"--- INIT BARE METAL RESTORE: {source} -> {dest} ---")
            engine_cmd = f"gunzip -c '{source}' | dd of='{dest}' bs=4M status=progress & CMD_PID=$!; wait $CMD_PID"
        else:
            dest = self.rst_dest_path.text().strip()
            if not dest:
                self.log("ERROR: No destination path specified.")
                return self.handle_finished(1, 0)
            
            # Auto-create destination folder if it doesn't exist
            if not os.path.exists(dest):
                try:
                    os.makedirs(dest, exist_ok=True)
                    self.log(f"SYS: Created destination folder: {dest}")
                except Exception as e:
                    self.log(f"ERROR: Failed to create destination folder: {e}")
                    return self.handle_finished(1, 0)
            
            self.log(f"--- INIT FILESYSTEM RESTORE: {source} -> {dest} ---")
            if "Ext4" in engine:
                engine_cmd = f"tar -xzf '{source}' -C '{dest}' --strip-components=0 & CMD_PID=$!; wait $CMD_PID" 
            elif "Btrfs" in engine:
                engine_cmd = f"btrfs receive '{dest}' -f '{source}' & CMD_PID=$!; wait $CMD_PID"

        trap_cmd    = "trap 'kill -9 $CMD_PID 2>/dev/null; exit 1' SIGINT SIGTERM"
        bash_script = (
            f"#!/bin/bash\n{trap_cmd}\n"
            f"echo \"Commencing Restore Stream...\"\n"
            f"{engine_cmd}\n"
            f"SEND_STATUS=$?\n"
            f"if [ $SEND_STATUS -ne 0 ]; then exit $SEND_STATUS; fi\n"
            f"echo \"SUCCESS: Restore process complete.\"\n"
            f"exit 0"
        )
        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())
    
    def start_selective_restore(self, source, engine):
        """Handle selective file/folder restore."""
        dest = self.rst_dest_path.text().strip()
        if not dest:
            self.log("ERROR: No destination path specified.")
            return self.handle_finished(1, 0)
        
        # Auto-create destination folder if it doesn't exist
        if not os.path.exists(dest):
            try:
                os.makedirs(dest, exist_ok=True)
                self.log(f"SYS: Created destination folder: {dest}")
            except Exception as e:
                self.log(f"ERROR: Failed to create destination folder: {e}")
                return self.handle_finished(1, 0)
        
        # Collect selected paths from the file tree
        # Tree structure: top-level = directories, children = files/subdirs
        # Fully checked top-level -> include whole directory (tar handles recursion)
        # Partially checked top-level -> include only checked children individually
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
                            selected_files.append(f"{top_name}/{child.text(0).strip()}")
        
        if not selected_files:
            return QMessageBox.warning(self, "Error", "No files selected for restore.")
        
        self.active_job_type = "restore"
        self.btn_run_restore.setEnabled(False)
        if hasattr(self, 'btn_restore_pause'): self.btn_restore_pause.setEnabled(True)
        if hasattr(self, 'btn_restore_stop'):  self.btn_restore_stop.setEnabled(True)
        self.console.clear()
        
        self.register_job("Selective Restore", source)
        self.log(f"--- SELECTIVE RESTORE: {len(selected_files)} file(s) selected ---")
        
        if "Ext4" in engine:
            # Create files list for tar --files-from
            files_list_path = "/tmp/archvault_restore_files.txt"
            try:
                with open(files_list_path, "w") as f:
                    for file_path in selected_files:
                        # Remove leading slash for tar
                        clean_path = file_path.lstrip('/')
                        f.write(f"{clean_path}\n")
                        self.log(f"  → {clean_path}")
            except Exception as e:
                self.log(f"ERROR: Failed to create file list: {e}")
                return self.handle_finished(1, 0)
            
            engine_cmd = f"tar -xzf '{source}' -C '{dest}' --files-from='{files_list_path}' & CMD_PID=$!; wait $CMD_PID"
            
        elif "Btrfs" in engine:
            # Btrfs: extract to temp, then copy selected files
            temp_dir = "/tmp/archvault_btrfs_temp"
            self.log(f"SYS: Extracting Btrfs snapshot to temp location for selective restore...")
            
            engine_cmd = f"""
mkdir -p '{temp_dir}'
btrfs receive '{temp_dir}' -f '{source}' & CMD_PID=$!
wait $CMD_PID
if [ $? -ne 0 ]; then echo "CRITICAL: Btrfs extraction failed"; exit 1; fi

# Copy selected files
echo "Copying selected files to destination..."
"""
            for file_path in selected_files:
                clean_path = file_path.lstrip('/')
                src_file = f"'{temp_dir}/{clean_path}'"
                dest_file = f"'{dest}/{clean_path}'"
                engine_cmd += f"mkdir -p $(dirname {dest_file})\n"
                engine_cmd += f"cp -a {src_file} {dest_file}\n"
                self.log(f"  → {clean_path}")
            
            engine_cmd += f"\nrm -rf '{temp_dir}'\n"
        
        trap_cmd = "trap 'kill -9 $CMD_PID 2>/dev/null; exit 1' SIGINT SIGTERM"
        bash_script = (
            f"#!/bin/bash\n{trap_cmd}\n"
            f"echo \"Commencing Selective Restore...\"\n"
            f"{engine_cmd}\n"
            f"SEND_STATUS=$?\n"
            f"if [ $SEND_STATUS -ne 0 ]; then exit $SEND_STATUS; fi\n"
            f"echo \"SUCCESS: Selective restore complete.\"\n"
            f"exit 0"
        )
        with open("/tmp/archvault_run.sh", "w") as f: f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())

    # ── BTRFS LIVE SUBVOLUME RESTORE ─────────────────────────────────────────
    def _start_btrfs_subvol_restore(self, source: str):
        """Restore a live Btrfs subvolume/snapshot to a destination using
        btrfs send | btrfs receive.  Source must be a read-only subvolume
        (or we'll make it temporarily read-only).
        """
        dest = ""
        if hasattr(self, '_rst_btrfs_dest'):
            dest = self._rst_btrfs_dest.text().strip()
        if not dest:
            if hasattr(self, 'rst_dest_path'):
                dest = self.rst_dest_path.text().strip()
        if not dest:
            return QMessageBox.warning(self, "Error",
                "Please specify a restore destination (must be a mounted Btrfs filesystem).")
        if not os.path.isdir(dest):
            return QMessageBox.warning(self, "Error",
                f"Destination does not exist or is not a directory:\n{dest}")

        snap_name = os.path.basename(source.rstrip("/")) or "restored_snapshot"
        final_path = os.path.join(dest, snap_name)

        reply = QMessageBox.warning(self, "Confirm Btrfs Subvolume Restore",
            f"This will stream:\n\n  {source}\n\nto:\n\n  {final_path}\n\n"
            "The destination will be created as a new read-only subvolume.\n"
            "This does NOT overwrite your running system.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            self.log("Btrfs restore aborted by user.")
            return

        self.active_job_type = "restore"
        self.btn_run_restore.setEnabled(False)
        if hasattr(self, 'btn_restore_pause'): self.btn_restore_pause.setEnabled(True)
        if hasattr(self, 'btn_restore_stop'):  self.btn_restore_stop.setEnabled(True)
        self.console.clear()
        self.register_job("Restore (Btrfs Subvol)", source)

        self.log(f"--- INIT BTRFS SUBVOLUME RESTORE: {source} → {final_path} ---")

        # btrfs send requires a read-only snapshot.
        # If the source is already read-only we stream it directly.
        # If not, we create a temporary read-only snapshot first.
        tmp_snap = f"/tmp/archvault_btrfs_send_snap"
        bash_script = f"""#!/bin/bash
trap 'kill -9 $CMD_PID 2>/dev/null; btrfs subvolume delete "{tmp_snap}" 2>/dev/null; exit 1' SIGINT SIGTERM

echo "--- Checking source subvolume read-only status ---"
RO=$(btrfs property get "{source}" ro 2>/dev/null | grep -c "ro=true")
USED_TMP=0

if [ "$RO" -eq 0 ]; then
    echo "SYS: Source is read-write — creating temporary read-only snapshot for send…"
    btrfs subvolume snapshot -r "{source}" "{tmp_snap}"
    if [ $? -ne 0 ]; then
        echo "CRITICAL: Failed to create read-only snapshot of source."
        exit 1
    fi
    SEND_SOURCE="{tmp_snap}"
    USED_TMP=1
else
    echo "SYS: Source is already read-only — streaming directly."
    SEND_SOURCE="{source}"
fi

echo "--- Streaming subvolume to {dest} ---"
btrfs send "$SEND_SOURCE" | btrfs receive "{dest}" & CMD_PID=$!
wait $CMD_PID
SEND_STATUS=$?

if [ "$USED_TMP" -eq 1 ]; then
    btrfs subvolume delete "{tmp_snap}" 2>/dev/null
    echo "SYS: Cleaned up temporary snapshot."
fi

if [ $SEND_STATUS -ne 0 ]; then
    echo "CRITICAL: btrfs send/receive failed (exit $SEND_STATUS)."
    exit $SEND_STATUS
fi

echo "SUCCESS: Btrfs subvolume restored to {final_path}"
exit 0
"""
        with open("/tmp/archvault_run.sh", "w") as f:
            f.write(bash_script)
        os.chmod("/tmp/archvault_run.sh", 0o755)
        self.process.start("/tmp/archvault_run.sh")
        self.update_job_state(pid=self.process.processId())
