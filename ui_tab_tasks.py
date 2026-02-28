from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QComboBox, QStackedWidget,
                             QTimeEdit, QSpinBox, QFrame, QMessageBox, QScrollArea)
from PyQt6.QtCore import Qt, QTime
from soft_ui_components import (BTN_PRIMARY, BTN_SUCCESS, BTN_DANGER, BTN_WARNING, mk_page_title)
from ui_widgets import ToggleSwitch, confirm_action

_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")

_MODE_ACTIVE   = ("background: #3b82f6; color:#fff; "
                  "font-weight:700; font-size:12px; padding:8px 18px; "
                  "border-radius:8px; border:none;")
_MODE_INACTIVE = ("background:transparent; color:#64748b; font-weight:600; "
                  "font-size:12px; padding:8px 18px; border-radius:8px; "
                  "border:1px solid rgba(100,116,139,0.3);")

def _hsep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f

class TasksPageWidget(QWidget):
    def __init__(self, mixin_ref):
        super().__init__()
        self.mixin_ref = mixin_ref
        self.setStyleSheet("background:transparent;")

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self.mixin_ref, 'refresh_tasks_ui'):
            self.mixin_ref.refresh_tasks_ui()


class TasksMixin:
    def build_tasks_page(self):
        page = TasksPageWidget(self)
        root = QVBoxLayout(page)
        root.setSpacing(18)
        root.setContentsMargins(0, 0, 0, 0)

        # ── PAGE HEADER ───────────────────────────────────────────────────────
        root.addWidget(mk_page_title(
            "Scheduled Tasks",
            "Create or modify automated background systemd timers"))
        root.addWidget(_hsep())

        # ── MODE SELECTOR ─────────────────────────────────────────────────────
        mode_lbl = QLabel("TASK MODE")
        mode_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(mode_lbl)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.setContentsMargins(0, 0, 0, 0)

        self.btn_mode_create = QPushButton("Create Task")
        self.btn_mode_modify = QPushButton("Modify Task")
        
        self.btn_mode_create.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_modify.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_mode_create.clicked.connect(lambda: self._set_task_mode("create"))
        self.btn_mode_modify.clicked.connect(lambda: self._set_task_mode("modify"))

        mode_row.addWidget(self.btn_mode_create)
        mode_row.addWidget(self.btn_mode_modify)
        mode_row.addStretch()
        root.addLayout(mode_row)

        root.addWidget(_hsep())

        # ── TASK CONFIGURATION FORM (SCROLLABLE) ──────────────────────────────
        cfg_lbl = QLabel("CONFIGURATION")
        cfg_lbl.setStyleSheet(_SEC_LABEL)
        root.addWidget(cfg_lbl)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("background:transparent;")
        
        editor_frame = QWidget()
        editor_lay = QVBoxLayout(editor_frame)
        editor_lay.setContentsMargins(0, 0, 16, 0)
        editor_lay.setSpacing(16)

        # 1. Identifier Stack (Name Input vs Combo Box)
        self.task_ident_stack = QStackedWidget()
        self.task_ident_stack.setFixedHeight(45)
        
        w_create_ident = QWidget()
        l_create_ident = QHBoxLayout(w_create_ident)
        l_create_ident.setContentsMargins(0, 0, 0, 0)
        self.task_name_input = QLineEdit()
        self.task_name_input.setPlaceholderText("e.g., Nightly Cloud Sync")
        l_create_ident.addWidget(QLabel("Task Name:"), 0)
        l_create_ident.addWidget(self.task_name_input, 1)
        self.task_ident_stack.addWidget(w_create_ident)

        w_modify_ident = QWidget()
        l_modify_ident = QHBoxLayout(w_modify_ident)
        l_modify_ident.setContentsMargins(0, 0, 0, 0)
        self.modify_task_combo = QComboBox()
        self.modify_task_combo.currentIndexChanged.connect(self._on_modify_combo_changed)
        l_modify_ident.addWidget(QLabel("Select Task:"), 0)
        l_modify_ident.addWidget(self.modify_task_combo, 1)
        self.task_ident_stack.addWidget(w_modify_ident)

        editor_lay.addWidget(self.task_ident_stack)

        # 2. Target and Engine Row
        r2 = QHBoxLayout()
        self.task_target_combo = QComboBox()
        self.task_engine_combo = QComboBox()
        self.task_engine_combo.addItems([
            "Btrfs Native  (.btrfs snapshot)",
            "Ext4 / Universal  (.tar.gz archive)",
            "rsync Incremental  (hardlink, space-efficient)"
        ])
        r2.addWidget(QLabel("Target Profile:"), 0)
        r2.addWidget(self.task_target_combo, 1)
        r2.addSpacing(20)
        r2.addWidget(QLabel("Engine:"), 0)
        r2.addWidget(self.task_engine_combo, 1)
        editor_lay.addLayout(r2)

        # 3. Time and Days Row
        r3 = QHBoxLayout()
        self.task_time_input = QTimeEdit()
        self.task_time_input.setDisplayFormat("HH:mm")
        
        r3.addWidget(QLabel("Execution Time:"), 0)
        r3.addWidget(self.task_time_input, 0)
        r3.addSpacing(30)
        r3.addWidget(QLabel("Active Days:"), 0)
        
        self.day_buttons = {}
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for day in days:
            btn = QPushButton(day)
            btn.setCheckable(True)
            btn.setFixedSize(45, 32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton { background-color: rgba(130, 140, 160, 0.15); border-radius: 6px; border: 1px solid rgba(130, 140, 160, 0.3); font-weight: bold; }
                QPushButton:checked { background-color: #3b82f6; color: white; border: none; }
                QPushButton:hover:!checked { background-color: rgba(130, 140, 160, 0.25); }
            """)
            self.day_buttons[day] = btn
            r3.addWidget(btn)
        r3.addStretch()
        editor_lay.addLayout(r3)

        editor_lay.addWidget(_hsep())

        # ── ADVANCED TOGGLES ──────────────────────────────────────────────────
        
        # Prune and Validation
        r4 = QHBoxLayout()
        self.task_retention_spin = QSpinBox()
        self.task_retention_spin.setRange(1, 3650)
        self.task_retention_spin.setSuffix(" days")
        self.task_validate_chk = ToggleSwitch()
        r4.addWidget(QLabel("Prune Older Than:"), 0)
        r4.addWidget(self.task_retention_spin, 0)
        r4.addSpacing(30)
        r4.addWidget(QLabel("Validate Backup Upon Completion:"), 0)
        r4.addWidget(self.task_validate_chk, 0)
        r4.addStretch()
        editor_lay.addLayout(r4)

        # Missed Run (Persistent Timer)
        r_missed = QHBoxLayout()
        self.chk_missed_run = ToggleSwitch()
        self.chk_missed_run.setChecked(True)
        r_missed.addWidget(QLabel("Run task immediately if scheduled time was missed (System downtime):"))
        r_missed.addWidget(self.chk_missed_run)
        r_missed.addStretch()
        editor_lay.addLayout(r_missed)

        # Logged In Only
        r_logged = QHBoxLayout()
        self.chk_only_logged_in = ToggleSwitch()
        r_logged.addWidget(QLabel("Run task ONLY when a user is actively logged in to the system:"))
        r_logged.addWidget(self.chk_only_logged_in)
        r_logged.addStretch()
        editor_lay.addLayout(r_logged)

        # Retry on Failure
        r_retry = QHBoxLayout()
        self.chk_retry_fail = ToggleSwitch()
        r_retry.addWidget(QLabel("Attempt to run task again automatically if it fails:"))
        r_retry.addWidget(self.chk_retry_fail)
        r_retry.addStretch()
        editor_lay.addLayout(r_retry)

        self.frm_retry = QWidget()
        frm_retry_lay = QHBoxLayout(self.frm_retry)
        frm_retry_lay.setContentsMargins(40, 0, 0, 10)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(1, 10)
        frm_retry_lay.addWidget(QLabel("Maximum Retry Attempts:"))
        frm_retry_lay.addWidget(self.spin_retries)
        frm_retry_lay.addStretch()
        self.frm_retry.hide()
        editor_lay.addWidget(self.frm_retry)
        self.chk_retry_fail.toggled.connect(self.frm_retry.setVisible)

        # Other Account
        r_acc = QHBoxLayout()
        self.chk_other_account = ToggleSwitch()
        r_acc.addWidget(QLabel("Use another account to perform this task (Requires specific permissions):"))
        r_acc.addWidget(self.chk_other_account)
        r_acc.addStretch()
        editor_lay.addLayout(r_acc)

        self.frm_account = QWidget()
        frm_acc_lay = QHBoxLayout(self.frm_account)
        frm_acc_lay.setContentsMargins(40, 0, 0, 10)
        self.txt_account_user = QLineEdit()
        self.txt_account_user.setPlaceholderText("Linux Username")
        self.txt_account_pass = QLineEdit()
        self.txt_account_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_account_pass.setPlaceholderText("Password")
        frm_acc_lay.addWidget(QLabel("Account:"))
        frm_acc_lay.addWidget(self.txt_account_user)
        frm_acc_lay.addSpacing(10)
        frm_acc_lay.addWidget(QLabel("Password:"))
        frm_acc_lay.addWidget(self.txt_account_pass)
        frm_acc_lay.addStretch()
        self.frm_account.hide()
        editor_lay.addWidget(self.frm_account)
        self.chk_other_account.toggled.connect(self.frm_account.setVisible)

        # Notifications
        r_notif = QHBoxLayout()
        self.chk_notifications = ToggleSwitch()
        r_notif.addWidget(QLabel("Set up Alerting / Notifications for this specific task:"))
        r_notif.addWidget(self.chk_notifications)
        r_notif.addStretch()
        editor_lay.addLayout(r_notif)

        self.frm_notif = QWidget()
        frm_notif_lay = QVBoxLayout(self.frm_notif)
        frm_notif_lay.setContentsMargins(40, 0, 0, 10)
        frm_notif_lay.setSpacing(10)

        n_row1 = QHBoxLayout()
        self.notif_on = QComboBox()
        self.notif_on.addItems(["Always", "Success Only", "Failure Only"])
        self.notif_channel = QComboBox()
        self.notif_channel.addItems(["Email", "Webhook (Discord / Slack)", "Both"])
        n_row1.addWidget(QLabel("Trigger On:"))
        n_row1.addWidget(self.notif_on)
        n_row1.addSpacing(20)
        n_row1.addWidget(QLabel("Channel:"))
        n_row1.addWidget(self.notif_channel)
        n_row1.addStretch()
        frm_notif_lay.addLayout(n_row1)

        self.frm_notif_email = QWidget()
        email_lay = QHBoxLayout(self.frm_notif_email)
        email_lay.setContentsMargins(0, 0, 0, 0)
        self.txt_notif_host = QLineEdit(); self.txt_notif_host.setPlaceholderText("SMTP Host")
        self.txt_notif_port = QLineEdit(); self.txt_notif_port.setPlaceholderText("Port")
        self.txt_notif_user = QLineEdit(); self.txt_notif_user.setPlaceholderText("Username")
        self.txt_notif_pass = QLineEdit(); self.txt_notif_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_notif_to = QLineEdit(); self.txt_notif_to.setPlaceholderText("To Email")
        email_lay.addWidget(self.txt_notif_host)
        email_lay.addWidget(self.txt_notif_port)
        email_lay.addWidget(self.txt_notif_user)
        email_lay.addWidget(self.txt_notif_pass)
        email_lay.addWidget(self.txt_notif_to)
        frm_notif_lay.addWidget(self.frm_notif_email)

        self.frm_notif_webhook = QWidget()
        web_lay = QHBoxLayout(self.frm_notif_webhook)
        web_lay.setContentsMargins(0, 0, 0, 0)
        self.txt_notif_url = QLineEdit()
        self.txt_notif_url.setPlaceholderText("https://discord.com/api/webhooks/...")
        web_lay.addWidget(QLabel("URL:"))
        web_lay.addWidget(self.txt_notif_url)
        frm_notif_lay.addWidget(self.frm_notif_webhook)

        self.frm_notif.hide()
        self.frm_notif_webhook.hide()
        editor_lay.addWidget(self.frm_notif)

        self.chk_notifications.toggled.connect(self.frm_notif.setVisible)
        self.notif_channel.currentIndexChanged.connect(self._toggle_notif_fields)

        editor_lay.addStretch()
        scroll_area.setWidget(editor_frame)
        root.addWidget(scroll_area, 1)

        # ── ACTION BUTTONS STACK ──────────────────────────────────────────────
        self.task_action_stack = QStackedWidget()
        self.task_action_stack.setFixedHeight(50)
        
        w_action_create = QWidget()
        l_action_create = QHBoxLayout(w_action_create)
        l_action_create.setContentsMargins(0, 10, 0, 0)
        self.btn_create_task = QPushButton("Create Task")
        self.btn_create_task.setStyleSheet(BTN_SUCCESS)
        self.btn_create_task.clicked.connect(self._create_task)
        l_action_create.addWidget(self.btn_create_task)
        l_action_create.addStretch()
        self.task_action_stack.addWidget(w_action_create)

        w_action_modify = QWidget()
        l_action_modify = QHBoxLayout(w_action_modify)
        l_action_modify.setContentsMargins(0, 10, 0, 0)
        self.btn_update_task = QPushButton("✎  Update Task")
        self.btn_update_task.setStyleSheet(BTN_WARNING)
        self.btn_update_task.clicked.connect(self._update_task)
        self.btn_delete_task = QPushButton("✕  Delete Task")
        self.btn_delete_task.setStyleSheet(BTN_DANGER)
        self.btn_delete_task.clicked.connect(self._delete_task)
        l_action_modify.addWidget(self.btn_update_task)
        l_action_modify.addWidget(self.btn_delete_task)
        l_action_modify.addStretch()
        self.task_action_stack.addWidget(w_action_modify)

        root.addWidget(self.task_action_stack)

        self._set_task_mode("create")
        return page

    # ── UI LOGIC AND EVENT HANDLERS ───────────────────────────────────────────

    def _toggle_notif_fields(self):
        c = self.notif_channel.currentText()
        self.frm_notif_email.setVisible("Email" in c or "Both" in c)
        self.frm_notif_webhook.setVisible("Webhook" in c or "Both" in c)

    def _set_task_mode(self, mode):
        if mode == "create":
            self.btn_mode_create.setStyleSheet(_MODE_ACTIVE)
            self.btn_mode_modify.setStyleSheet(_MODE_INACTIVE)
            self.task_ident_stack.setCurrentIndex(0)
            self.task_action_stack.setCurrentIndex(0)
            self._clear_form_for_new()
        else:
            self.btn_mode_create.setStyleSheet(_MODE_INACTIVE)
            self.btn_mode_modify.setStyleSheet(_MODE_ACTIVE)
            self.task_ident_stack.setCurrentIndex(1)
            self.task_action_stack.setCurrentIndex(1)
            self._refresh_modify_combo()

    def refresh_tasks_ui(self):
        # Prevent crash if this function is called before the UI is built
        if not hasattr(self, 'task_target_combo'):
            return

        current_target = self.task_target_combo.currentText()
        self.task_target_combo.blockSignals(True)
        self.task_target_combo.clear()
        
        profiles = getattr(self, "profiles", {})
        if isinstance(profiles, dict):
            for cat, prof_dict in profiles.items():
                if isinstance(prof_dict, dict):
                    for name in prof_dict.keys():
                        self.task_target_combo.addItem(f"{cat.upper()}: {name}")
        
        idx = self.task_target_combo.findText(current_target)
        if idx >= 0: self.task_target_combo.setCurrentIndex(idx)
        self.task_target_combo.blockSignals(False)

        if self.task_ident_stack.currentIndex() == 1:
            self._refresh_modify_combo()

    def _refresh_modify_combo(self):
        current_task = self.modify_task_combo.currentText()
        self.modify_task_combo.blockSignals(True)
        self.modify_task_combo.clear()
        
        tasks = getattr(self, "scheduled_tasks", {})
        for t_name, t_data in tasks.items():
            if t_data.get("task_type") != "verification":
                self.modify_task_combo.addItem(t_name)
        
        idx = self.modify_task_combo.findText(current_task)
        if idx >= 0:
            self.modify_task_combo.setCurrentIndex(idx)
        elif self.modify_task_combo.count() > 0:
            self.modify_task_combo.setCurrentIndex(0)
            
        self.modify_task_combo.blockSignals(False)
        
        if self.modify_task_combo.count() > 0:
            self._load_task_into_form(self.modify_task_combo.currentText())
            self.btn_update_task.setEnabled(True)
            self.btn_delete_task.setEnabled(True)
        else:
            self._clear_form_for_new()
            self.btn_update_task.setEnabled(False)
            self.btn_delete_task.setEnabled(False)

    def _on_modify_combo_changed(self, index):
        if index >= 0:
            task_name = self.modify_task_combo.itemText(index)
            self._load_task_into_form(task_name)

    def _clear_form_for_new(self):
        self.task_name_input.clear()
        self.task_time_input.setTime(QTime(0, 0))
        for btn in self.day_buttons.values(): btn.setChecked(False)
        self.task_retention_spin.setValue(getattr(self, "settings", {}).get("global_retention", 7))
        self.task_validate_chk.setChecked(getattr(self, "settings", {}).get("auto_validate", False))
        
        self.chk_missed_run.setChecked(True)
        self.chk_only_logged_in.setChecked(False)
        self.chk_retry_fail.setChecked(False)
        self.spin_retries.setValue(3)
        self.chk_other_account.setChecked(False)
        self.txt_account_user.clear()
        self.txt_account_pass.clear()
        
        self.chk_notifications.setChecked(False)
        self.notif_on.setCurrentIndex(0)
        self.notif_channel.setCurrentIndex(0)
        self.txt_notif_host.clear()
        self.txt_notif_port.clear()
        self.txt_notif_user.clear()
        self.txt_notif_pass.clear()
        self.txt_notif_to.clear()
        self.txt_notif_url.clear()

    def _load_task_into_form(self, task_name):
        task = getattr(self, "scheduled_tasks", {}).get(task_name)
        if not task: return

        t_idx = self.task_target_combo.findText(task.get("target", ""))
        if t_idx >= 0: self.task_target_combo.setCurrentIndex(t_idx)

        e_idx = self.task_engine_combo.findText(task.get("engine", ""))
        if e_idx >= 0: self.task_engine_combo.setCurrentIndex(e_idx)

        t = QTime.fromString(task.get("time", "00:00"), "HH:mm")
        if t.isValid(): self.task_time_input.setTime(t)

        for day, btn in self.day_buttons.items():
            btn.setChecked(task.get("days", {}).get(day, False))

        self.task_retention_spin.setValue(task.get("retention", 7))
        self.task_validate_chk.setChecked(task.get("validate", False))

        self.chk_missed_run.setChecked(task.get("missed_run", True))
        self.chk_only_logged_in.setChecked(task.get("only_logged_in", False))
        self.chk_retry_fail.setChecked(task.get("retry_fail", False))
        self.spin_retries.setValue(task.get("retry_count", 3))
        
        self.chk_other_account.setChecked(task.get("other_account", False))
        self.txt_account_user.setText(task.get("account_user", ""))
        decrypted_pass = getattr(self, "decrypt_pw", lambda x: x)(task.get("account_pass", ""))
        self.txt_account_pass.setText(decrypted_pass)

        self.chk_notifications.setChecked(task.get("notifications", False))
        n_cfg = task.get("notif_settings", {})
        
        idx_on = self.notif_on.findText(n_cfg.get("notif_on", "Always"))
        if idx_on >= 0: self.notif_on.setCurrentIndex(idx_on)
        idx_ch = self.notif_channel.findText(n_cfg.get("notif_channel", "Email"))
        if idx_ch >= 0: self.notif_channel.setCurrentIndex(idx_ch)
        
        self.txt_notif_host.setText(n_cfg.get("notif_smtp_host", ""))
        self.txt_notif_port.setText(n_cfg.get("notif_smtp_port", ""))
        self.txt_notif_user.setText(n_cfg.get("notif_smtp_user", ""))
        decrypted_smtp = getattr(self, "decrypt_pw", lambda x: x)(n_cfg.get("notif_smtp_pass", ""))
        self.txt_notif_pass.setText(decrypted_smtp)
        self.txt_notif_to.setText(n_cfg.get("notif_to", ""))
        self.txt_notif_url.setText(n_cfg.get("notif_webhook_url", ""))

    # ── CRUD OPERATIONS ───────────────────────────────────────────────────────

    def _get_form_data(self):
        days = {day: btn.isChecked() for day, btn in self.day_buttons.items()}
        enc = getattr(self, "encrypt_pw", lambda x: x)
        return {
            "task_type": "backup",
            "target": self.task_target_combo.currentText(),
            "engine": self.task_engine_combo.currentText(),
            "time": self.task_time_input.time().toString("HH:mm"),
            "days": days,
            "retention": self.task_retention_spin.value(),
            "validate": self.task_validate_chk.isChecked(),
            "missed_run": self.chk_missed_run.isChecked(),
            "only_logged_in": self.chk_only_logged_in.isChecked(),
            "retry_fail": self.chk_retry_fail.isChecked(),
            "retry_count": self.spin_retries.value(),
            "other_account": self.chk_other_account.isChecked(),
            "account_user": self.txt_account_user.text().strip(),
            "account_pass": enc(self.txt_account_pass.text()),
            "notifications": self.chk_notifications.isChecked(),
            "notif_settings": {
                "notif_on": self.notif_on.currentText(),
                "notif_channel": self.notif_channel.currentText(),
                "notif_smtp_host": self.txt_notif_host.text().strip(),
                "notif_smtp_port": self.txt_notif_port.text().strip(),
                "notif_smtp_user": self.txt_notif_user.text().strip(),
                "notif_smtp_pass": enc(self.txt_notif_pass.text()),
                "notif_to": self.txt_notif_to.text().strip(),
                "notif_webhook_url": self.txt_notif_url.text().strip()
            }
        }

    def _create_task(self):
        name = self.task_name_input.text().strip()
        if not name: return QMessageBox.warning(self, "Error", "Task Name cannot be empty.")
        if name in getattr(self, "scheduled_tasks", {}): return QMessageBox.warning(self, "Error", "A task with this name already exists.")
        target = self.task_target_combo.currentText()
        if not target: return QMessageBox.warning(self, "Error", "Please select a target profile.")
        
        data = self._get_form_data()
        if not any(data["days"].values()): return QMessageBox.warning(self, "Error", "Select at least one day.")

        self.scheduled_tasks[name] = data
        self.write_tasks(success_msg=f"Task '{name}' created and scheduled in systemd.")
        self._clear_form_for_new()

    def _update_task(self):
        name = self.modify_task_combo.currentText()
        if not name or name not in getattr(self, "scheduled_tasks", {}): return
        data = self._get_form_data()
        if not any(data["days"].values()): return QMessageBox.warning(self, "Error", "Select at least one day.")

        self.scheduled_tasks[name] = data
        self.write_tasks(success_msg=f"Task '{name}' updated successfully.")

    def _delete_task(self):
        name = self.modify_task_combo.currentText()
        if not name: return
        if confirm_action(
                self, "Delete Scheduled Task",
                f"Are you sure you want to delete the task '{name}'?",
                detail="This will remove its systemd timer and all "
                       "scheduling configuration.",
                confirm_text="Delete Task", destructive=True,
                icon_char="🗑"):
            if name in self.scheduled_tasks:
                del self.scheduled_tasks[name]
                self.write_tasks(success_msg=f"Task '{name}' deleted.")
                self._refresh_modify_combo()
