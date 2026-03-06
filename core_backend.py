import os
import json
import sys
import base64
import secrets
import subprocess
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from PyQt6.QtWidgets import (QMessageBox, QDialog, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QApplication)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

VERSION = "v5.0.3-beta"

CONFIG_DIR        = "/etc/archvault"
SALT_FILE         = os.path.join(CONFIG_DIR, "archvault.salt")
VERIFY_FILE       = os.path.join(CONFIG_DIR, "archvault.verify")
LEGACY_KEY_FILE   = os.path.join(CONFIG_DIR, "archvault.key")
PROFILES_FILE     = os.path.join(CONFIG_DIR, "archvault_profiles.json")
TASKS_FILE        = os.path.join(CONFIG_DIR, "archvault_tasks.json")
SETTINGS_FILE     = os.path.join(CONFIG_DIR, "app_settings.json")
JOBS_FILE         = os.path.join(CONFIG_DIR, "archvault_jobs.json")

VERIFY_PLAINTEXT  = b"ARCHVAULT_OK"
PBKDF2_ITERATIONS = 600_000
MAX_ATTEMPTS      = 5

DIALOG_STYLE = """
    QDialog {
        background-color: #0f111a;
        color: #e2e8f0;
        font-family: 'Segoe UI', system-ui, sans-serif;
        font-size: 13px;
    }
    QLabel {
        background-color: transparent;
    }
    QLineEdit {
        background-color: #151722;
        color: #e2e8f0;
        border: 1px solid #2e3246;
        padding: 10px 14px;
        border-radius: 6px;
        font-size: 13px;
    }
    QLineEdit:focus {
        border: 1px solid #818cf8;
    }
"""

BTN_OK   = "background-color: #10b981; color: white; font-weight: bold; padding: 10px 20px; border-radius: 6px; border: none; font-size: 13px;"
BTN_PRI  = "background-color: #6366f1; color: white; font-weight: bold; padding: 10px 20px; border-radius: 6px; border: none; font-size: 13px;"
BTN_QUIT = "background-color: #3f3f46; color: white; font-weight: bold; padding: 10px 20px; border-radius: 6px; border: none; font-size: 13px;"

def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

class _SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ArchVault — Set Master Password")
        self.setMinimumWidth(520)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.password = None

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(36, 36, 36, 36)

        title = QLabel("🔐  Create Master Password")
        title.setFont(QFont("Arial", 17, QFont.Weight.Bold))
        title.setStyleSheet("color: #818cf8; background-color: transparent;")
        layout.addWidget(title)

        info = QLabel(
            "This password protects all stored credentials (SMB passwords, cloud keys).\n"
            "You will be prompted for it each time ArchVault starts.\n\n"
            "Choose a strong password — it cannot be recovered if lost."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #94a3b8; font-size: 12px; background-color: transparent;")
        layout.addWidget(info)

        layout.addSpacing(6)

        lbl1 = QLabel("New Master Password:")
        lbl1.setStyleSheet("font-weight: bold; background-color: transparent;")
        layout.addWidget(lbl1)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw1.setPlaceholderText("Enter a strong password...")
        self.pw1.setMinimumHeight(40)
        layout.addWidget(self.pw1)

        layout.addSpacing(4)

        lbl2 = QLabel("Confirm Password:")
        lbl2.setStyleSheet("font-weight: bold; background-color: transparent;")
        layout.addWidget(lbl2)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2.setPlaceholderText("Re-enter to confirm...")
        self.pw2.setMinimumHeight(40)
        self.pw2.returnPressed.connect(self._attempt)
        layout.addWidget(self.pw2)

        self.err_lbl = QLabel("")
        self.err_lbl.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold; background-color: transparent;")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.hide()
        layout.addWidget(self.err_lbl)

        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_ok = QPushButton("Set Password and Continue")
        btn_ok.setStyleSheet(BTN_OK)
        btn_ok.setMinimumHeight(42)
        btn_ok.clicked.connect(self._attempt)
        btn_quit = QPushButton("Quit")
        btn_quit.setStyleSheet(BTN_QUIT)
        btn_quit.setMinimumHeight(42)
        btn_quit.clicked.connect(lambda: sys.exit(0))
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_quit)
        layout.addLayout(btn_row)

        self.setStyleSheet(DIALOG_STYLE)

    def _attempt(self):
        p1 = self.pw1.text()
        p2 = self.pw2.text()
        if not p1:
            self.err_lbl.setText("Password cannot be empty.")
            self.err_lbl.show(); return
        if len(p1) < 8:
            self.err_lbl.setText("Password must be at least 8 characters.")
            self.err_lbl.show(); return
        if p1 != p2:
            self.err_lbl.setText("Passwords do not match. Please try again.")
            self.pw2.clear(); self.err_lbl.show(); return
        self.password = p1
        self.accept()

class _UnlockDialog(QDialog):
    def __init__(self, attempts_left: int, bad_attempt: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ArchVault — Enter Master Password")
        self.setMinimumWidth(460)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.password = None

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(36, 36, 36, 36)

        title = QLabel("🔐  ArchVault Locked")
        title.setFont(QFont("Arial", 17, QFont.Weight.Bold))
        title.setStyleSheet("color: #818cf8; background-color: transparent;")
        layout.addWidget(title)

        if bad_attempt:
            warn = QLabel(f"⚠  Incorrect password. {attempts_left} attempt(s) remaining.")
            warn.setStyleSheet("color: #ef4444; font-weight: bold; font-size: 12px; background-color: transparent;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        lbl = QLabel("Master Password:")
        lbl.setStyleSheet("color: #8d8d8d; font-weight: bold; background-color: transparent;")
        layout.addWidget(lbl)

        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("Enter your master password...")
        self.pw.setMinimumHeight(40)
        self.pw.returnPressed.connect(self._attempt)
        layout.addWidget(self.pw)

        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_ok = QPushButton("Unlock")
        btn_ok.setStyleSheet(BTN_PRI)
        btn_ok.setMinimumHeight(42)
        btn_ok.clicked.connect(self._attempt)
        btn_quit = QPushButton("Quit")
        btn_quit.setStyleSheet(BTN_QUIT)
        btn_quit.setMinimumHeight(42)
        btn_quit.clicked.connect(lambda: sys.exit(0))
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_quit)
        layout.addLayout(btn_row)

        self.setStyleSheet(DIALOG_STYLE)

    def _attempt(self):
        if not self.pw.text():
            return
        self.password = self.pw.text()
        self.accept()

class BackendMixin:

    def init_encryption(self, headless: bool = False):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, mode=0o700)

        is_first_run = not os.path.exists(SALT_FILE) or not os.path.exists(VERIFY_FILE)
        has_legacy   = os.path.exists(LEGACY_KEY_FILE)

        if headless:
            self._init_headless(is_first_run)
            return

        if is_first_run:
            self._run_setup(has_legacy)
        else:
            self._run_unlock()

    def _init_headless(self, is_first_run: bool):
        pw = os.environ.get("ARCHVAULT_MASTER_PW", "")
        if not pw:
            print("CRITICAL: ARCHVAULT_MASTER_PW environment variable is not set. "
                  "Headless tasks cannot decrypt credentials without it. Exiting.")
            sys.exit(2)

        if is_first_run:
            salt = secrets.token_bytes(32)
            key  = _derive_key(pw, salt)
            self.cipher = Fernet(key)
            self._write_salt_and_verify(salt)
            return

        salt = self._read_salt()
        key  = _derive_key(pw, salt)
        self.cipher = Fernet(key)

        if not self._verify_token():
            print("CRITICAL: ARCHVAULT_MASTER_PW is incorrect. Cannot decrypt credentials. Exiting.")
            sys.exit(2)

    def _run_setup(self, has_legacy: bool):
        dlg = _SetupDialog()
        dlg.exec()
        if not dlg.password: sys.exit(0)

        pw   = dlg.password
        salt = secrets.token_bytes(32)
        key  = _derive_key(pw, salt)
        self.cipher = Fernet(key)
        self._write_salt_and_verify(salt)
        self._write_env_password(pw)

        if has_legacy: self._migrate_legacy_credentials()

    def _run_unlock(self):
        salt = self._read_salt()
        bad  = False

        for attempt in range(MAX_ATTEMPTS):
            attempts_left = MAX_ATTEMPTS - attempt
            dlg = _UnlockDialog(attempts_left=attempts_left - 1, bad_attempt=bad)
            dlg.exec()
            if not dlg.password: sys.exit(0)

            key = _derive_key(dlg.password, salt)
            self.cipher = Fernet(key)

            if self._verify_token():
                self._write_env_password(dlg.password)
                return

            bad = True
            if attempt == MAX_ATTEMPTS - 1:
                QMessageBox.critical(None, "Too Many Attempts", f"Incorrect password entered {MAX_ATTEMPTS} times.\nArchVault will now exit.")
                sys.exit(1)

    def _write_env_password(self, pw: str):
        env_file = os.path.join(CONFIG_DIR, "archvault.env")
        try:
            existing = {}
            if os.path.exists(env_file):
                for line in open(env_file).readlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        existing[k.strip()] = v.strip()
            existing["ARCHVAULT_MASTER_PW"] = pw
            with open(env_file, "w") as f:
                f.write("# ArchVault headless task environment\n")
                f.write("# Auto-written by ArchVault — do not edit manually.\n")
                f.write("# This file is readable only by root (chmod 600).\n#\n")
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
            os.chmod(env_file, 0o600)
        except Exception as e:
            if hasattr(self, "log"): self.log(f"SYS WARNING: Could not write archvault.env: {e}")

    def _write_salt_and_verify(self, salt: bytes):
        with open(SALT_FILE, "wb") as f: f.write(salt)
        os.chmod(SALT_FILE, 0o600)
        token = self.cipher.encrypt(VERIFY_PLAINTEXT)
        with open(VERIFY_FILE, "wb") as f: f.write(token)
        os.chmod(VERIFY_FILE, 0o600)

    def _read_salt(self) -> bytes:
        try:
            with open(SALT_FILE, "rb") as f: salt = f.read()
            if len(salt) != 32: raise ValueError("Salt file is corrupt (wrong length).")
            return salt
        except (OSError, ValueError) as e:
            QMessageBox.critical(None, "Vault Error", "Cannot read salt file. Vault may be corrupt.")
            sys.exit(1)

    def _verify_token(self) -> bool:
        try:
            with open(VERIFY_FILE, "rb") as f: token = f.read()
            return self.cipher.decrypt(token) == VERIFY_PLAINTEXT
        except Exception:
            return False

    def _migrate_legacy_credentials(self):
        try:
            with open(LEGACY_KEY_FILE, "rb") as f: old_key = f.read()
            old_cipher = Fernet(old_key)
        except Exception: return
        if os.path.exists(PROFILES_FILE):
            try:
                with open(PROFILES_FILE, "r") as f: data = json.load(f)
                for cat in data.values():
                    if not isinstance(cat, dict): continue
                    for profile in cat.values():
                        if not isinstance(profile, dict): continue
                        for field in ("password", "secret_key"):
                            val = profile.get(field, "")
                            if val:
                                try:
                                    plaintext = old_cipher.decrypt(val.encode()).decode()
                                    profile[field] = self.cipher.encrypt(plaintext.encode()).decode()
                                except Exception: pass
                with open(PROFILES_FILE, "w") as f: json.dump(data, f, indent=4)
                os.chmod(PROFILES_FILE, 0o600)
            except Exception: pass
        try: os.remove(LEGACY_KEY_FILE)
        except OSError: pass

    def encrypt_pw(self, pw: str) -> str:
        if not pw: return ""
        if self.cipher is None: return pw
        try: return self.cipher.encrypt(pw.encode("utf-8")).decode()
        except Exception: return ""

    def decrypt_pw(self, pw: str) -> str:
        if not pw: return ""
        if self.cipher is None: return ""
        try: return self.cipher.decrypt(pw.encode()).decode("utf-8")
        except Exception: return ""

    def load_settings(self):
        if not os.path.exists(SETTINGS_FILE): return
        try:
            with open(SETTINGS_FILE, "r") as f: self.settings.update(json.load(f))
        except Exception: pass

    def load_profiles(self):
        if not os.path.exists(PROFILES_FILE): return
        try:
            with open(PROFILES_FILE, "r") as f: data = json.load(f)
            if isinstance(data, dict) and "network" in data: self.profiles.update(data)
            for _cat in ("cloud", "sftp"):
                if _cat not in self.profiles: self.profiles[_cat] = {}
        except Exception: pass

    def write_profiles(self, success_msg=""):
        try:
            with open(PROFILES_FILE, "w") as f: json.dump(self.profiles, f, indent=4)
            os.chmod(PROFILES_FILE, 0o600)
            self.refresh_dropdowns()
            if success_msg:
                self.log(f"INFO: {success_msg}")
                QMessageBox.information(self, "Success", success_msg)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to save profiles:\n{e}")

    def load_tasks(self):
        if not os.path.exists(TASKS_FILE): return
        try:
            with open(TASKS_FILE, "r") as f: self.scheduled_tasks = json.load(f)
        except Exception: pass

    def write_tasks(self, success_msg=""):
        try:
            with open(TASKS_FILE, "w") as f: json.dump(self.scheduled_tasks, f, indent=4)
            os.chmod(TASKS_FILE, 0o600)
            self.refresh_dropdowns()
            self.sync_systemd_timers()
            if success_msg:
                self.log(f"INFO: {success_msg}")
                QMessageBox.information(self, "Success", success_msg)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to save tasks:\n{e}")

    def sync_systemd_timers(self):
        systemd_dir = "/etc/systemd/system"
        script_path = os.path.abspath(sys.argv[0])
        script_dir  = os.path.dirname(script_path)
        python_bin  = sys.executable or "/usr/bin/python3"

        try:
            for fname in os.listdir(systemd_dir):
                if fname.startswith("archvault-task-") and (
                        fname.endswith(".timer") or fname.endswith(".service")):
                    path = os.path.join(systemd_dir, fname)
                    subprocess.run(
                        ["systemctl", "stop", fname], capture_output=True)
                    subprocess.run(
                        ["systemctl", "disable", fname], capture_output=True)
                    if os.path.exists(path):
                        os.remove(path)
            subprocess.run(
                ["systemctl", "daemon-reload"], capture_output=True)
            self.log("SYS: Cleared existing archvault timers.")
        except Exception as e:
            self.log(f"SYS: Error clearing old timers: {e}")

        # Detect SUDO_USER for HOME fallback
        sudo_user = os.environ.get("SUDO_USER", "")
        home_dir = f"/home/{sudo_user}" if sudo_user else "/root"

        written = []
        for name, task in self.scheduled_tasks.items():
            if task.get("task_type") == "verification":
                continue

            active_days = [
                d for d, active in task.get("days", {}).items() if active]
            if not active_days:
                continue

            safe_name = "".join(
                ch if ch.isalnum() else "_" for ch in name)
            service_path = f"{systemd_dir}/archvault-task-{safe_name}.service"
            timer_path = f"{systemd_dir}/archvault-task-{safe_name}.timer"

            time_str = task.get("time", "00:00")
            days_str = ",".join(active_days)
            calendar = f"{days_str} *-*-* {time_str}:00"

            # ── DYNAMIC SERVICE DIRECTIVES FROM UI TOGGLES ──
            exec_cond = ""
            if task.get("only_logged_in", False):
                exec_cond = (
                    "ExecCondition=/bin/bash -c "
                    "'users | wc -w | grep -qv \"^0$\"'\n")

            retry_str = ""
            if task.get("retry_fail", False):
                burst = task.get("retry_count", 3)
                retry_str = (
                    f"Restart=on-failure\n"
                    f"RestartSec=60\n"
                    f"StartLimitBurst={burst}\n"
                    f"StartLimitIntervalSec=3600\n")

            persistent_str = (
                "true" if task.get("missed_run", True) else "false")

            user_str = ""
            env_str = ""
            if task.get("other_account", False):
                run_user = task.get("account_user", "")
                if run_user:
                    user_str = f"User={run_user}\n"
                    run_pass = getattr(
                        self, "decrypt_pw", lambda x: x)(
                            task.get("account_pass", ""))
                    if run_pass:
                        env_str = (
                            f'Environment="ARCHVAULT_TASK_PW={run_pass}"\n')

            # Escape % to %% in systemd unit files (% is a specifier prefix)
            safe_exec_name = name.replace("%", "%%")

            try:
                with open(service_path, "w") as f:
                    f.write(
                        f"[Unit]\n"
                        f"Description=ArchVault Headless Task: {safe_exec_name}\n"
                        f"After=network-online.target\n"
                        f"Wants=network-online.target\n\n"
                        f"[Service]\n"
                        f"Type=simple\n"
                        f"{user_str}"
                        f"{exec_cond}"
                        f"{retry_str}"
                        f"WorkingDirectory={script_dir}\n"
                        f'Environment="HOME={home_dir}"\n'
                        f'Environment="QT_QPA_PLATFORM=offscreen"\n'
                        f'Environment="PYTHONUNBUFFERED=1"\n'
                        f'Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\n'
                        f'Environment="XDG_CACHE_HOME=/root/.cache/archvault"\n'
                        f"TimeoutStartSec=0\n"
                        f"TimeoutStopSec=300\n"
                        f"KillMode=control-group\n"
                        f"StandardOutput=journal\n"
                        f"StandardError=journal\n"
                        f"SyslogIdentifier=archvault\n"
                        f"EnvironmentFile=-/etc/archvault/archvault.env\n"
                        f"{env_str}"
                        f'ExecStart={python_bin} {script_path} --run-task "{safe_exec_name}"\n'
                    )
                with open(timer_path, "w") as f:
                    f.write(
                        f"[Unit]\n"
                        f"Description=ArchVault Timer: {name}\n\n"
                        f"[Timer]\n"
                        f"OnCalendar={calendar}\n"
                        f"AccuracySec=1s\n"
                        f"RandomizedDelaySec=0\n"
                        f"Persistent={persistent_str}\n"
                        f"Unit=archvault-task-{safe_name}.service\n\n"
                        f"[Install]\n"
                        f"WantedBy=timers.target\n"
                    )
                written.append((name, safe_name, calendar))
                self.log(f"SYS TIMER: Wrote unit files for '{name}'")
            except Exception as e:
                self.log(
                    f"SYS TIMER ERROR: Could not write unit files "
                    f"for '{name}': {e}")

        result = subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.log(
                f"SYS TIMER ERROR: daemon-reload failed: "
                f"{result.stderr.strip()}")

        for name, safe_name, calendar in written:
            unit = f"archvault-task-{safe_name}.timer"
            result = subprocess.run(
                ["systemctl", "enable", "--now", unit],
                capture_output=True, text=True)
            if result.returncode != 0:
                self.log(
                    f"SYS TIMER ERROR: Failed to enable '{unit}': "
                    f"{result.stderr.strip()}")
            else:
                self.log(
                    f"SYS TIMER: '{name}' enabled and scheduled → "
                    f"{calendar}")

        # ── Verification: log all active archvault timers ──
        if written:
            verify = subprocess.run(
                ["systemctl", "list-timers", "--no-pager",
                 "archvault-task-*"],
                capture_output=True, text=True)
            if verify.stdout.strip():
                for line in verify.stdout.strip().split("\n")[:8]:
                    self.log(f"SYS TIMER VERIFY: {line.strip()}")
            self.log(
                f"SYS TIMER: {len(written)} timer(s) installed.  "
                f"Python: {python_bin}  |  Script: {script_path}")

    def load_jobs(self):
        if not os.path.exists(JOBS_FILE):
            self.job_history = []
            return
        try:
            with open(JOBS_FILE, "rb") as f: data = f.read()
            if not data:
                self.job_history = []
                return
            if hasattr(self, 'cipher') and self.cipher:
                try:
                    decrypted = self.cipher.decrypt(data).decode('utf-8')
                    self.job_history = json.loads(decrypted)
                    return
                except (InvalidToken, TypeError): pass
            self.job_history = json.loads(data.decode('utf-8'))
        except Exception: pass

    def write_jobs(self):
        try:
            self.job_history = self.job_history[-100:]
            data = json.dumps(self.job_history, indent=4).encode('utf-8')
            if hasattr(self, 'cipher') and self.cipher: data = self.cipher.encrypt(data)
            with open(JOBS_FILE, "wb") as f: f.write(data)
            os.chmod(JOBS_FILE, 0o600)
            if hasattr(self, 'refresh_jobs_ui'): self.refresh_jobs_ui()
            if hasattr(self, 'refresh_dashboard'): self.refresh_dashboard()
        except Exception: pass
