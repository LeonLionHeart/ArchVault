import os
import shlex
import tempfile
import shutil
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal

class CloudUploadWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    # [Keep your existing Boto3/GCS/Azure logic here]

class EngineCloudMixin:
    
    def start_cloud_backup(self, name, prof, t_str):
        is_ext4 = "Ext4" in self.engine_combo.currentText()
        src_mode = prof.get("source_mode", "Full System")
        src_path = "/" if src_mode == "Full System" else prof.get("source_path", "/")

        custom_name = getattr(self, "settings", {}).get("backup_name_format", "ArchVault_%profile%_%datetime%").replace("%profile%", name.replace(" ", "_")).replace("%datetime%", datetime.now().strftime("%Y-%m-%d_%H%M"))
        
        # SECURE TEMP DIRECTORY
        staging_dir = tempfile.mkdtemp(prefix="archvault_cloud_")
        local_file = os.path.join(staging_dir, f"{custom_name}{'.tar.gz' if is_ext4 else '.btrfs'}")

        safe_src = shlex.quote(src_path)
        safe_out = shlex.quote(local_file)
        safe_staging = shlex.quote(staging_dir)
        
        if is_ext4:
            engine_cmd = f"tar --checkpoint=10000 -cpzf {safe_out} {safe_src} & CMD_PID=$!; wait $CMD_PID"
        else:
            engine_cmd = f"btrfs subvolume snapshot -r {safe_src} /.archvault_snapshot && btrfs send /.archvault_snapshot -f {safe_out} & CMD_PID=$!; wait $CMD_PID"

        bash_script = f"""#!/bin/bash
        trap 'kill -9 $CMD_PID 2>/dev/null; rm -rf {safe_staging}; exit 1' SIGINT SIGTERM
        {engine_cmd}
        SEND_STATUS=$?
        btrfs subvolume delete /.archvault_snapshot >/dev/null 2>&1 || true
        exit $SEND_STATUS
        """
        self._pending_cloud_upload = {
            "provider": prof.get("provider", ""), "profile": prof,
            "local_file": local_file, "staging_dir": staging_dir, 
            "object_key": f"{custom_name}{'.tar.gz' if is_ext4 else '.btrfs'}"
        }
        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())

    def _start_cloud_phase2(self):
        ctx = getattr(self, '_pending_cloud_upload', None)
        if not ctx: return
        self._pending_cloud_upload = None
        
        provider = ctx["provider"]
        prof = ctx["profile"]
        safe_local = shlex.quote(ctx["local_file"])
        safe_staging = shlex.quote(ctx["staging_dir"])
        bucket = shlex.quote(prof.get("bucket", ""))
        obj_key = shlex.quote(ctx["object_key"])
        
        access = shlex.quote(prof.get("access_key", ""))
        secret = shlex.quote(self.decrypt_pw(prof.get("secret_key", "")))
        region = shlex.quote(prof.get("region", "us-east-1"))
        
        # [Keep your rclone remote path generation logic here]
        remote_path = f":s3,access_key_id={access},secret_access_key={secret},region={region}:{bucket}/{obj_key}" # Example

        rclone_cmd = f"rclone copyto --progress --stats 5s {safe_local} {remote_path}"
        
        bash_script = f"""#!/bin/bash
        trap 'rm -rf {safe_staging}; exit 1' SIGINT SIGTERM
        {rclone_cmd}
        STATUS=$?
        rm -rf {safe_staging}
        exit $STATUS
        """
        self.process.start("bash", ["-c", bash_script])
        self.update_job_state(pid=self.process.processId())
