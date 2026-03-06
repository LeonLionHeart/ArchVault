from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QMessageBox, QTabWidget,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QFont, QColor, QBrush
from ui_widgets import ToggleSwitch, confirm_action
from soft_ui_components import (BTN_PRIMARY, BTN_SUCCESS, BTN_DANGER,
                                 BTN_WARNING, BTN_SECONDARY, BTN_INFO,
                                 mk_page_title)
import os
import subprocess
import json
from datetime import datetime

VERSION = "v5.0.3-beta"

_SEC_LABEL = ("font-size:10px; font-weight:700; letter-spacing:1.5px; "
              "color:#64748b; background:transparent; border:none;")


def _hsep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(100,116,139,0.18); border:none;")
    return f


def _sec_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(_SEC_LABEL)
    return lbl


class JobsMixin:
    def build_jobs_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(page)
        layout.setSpacing(18)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Page header ───────────────────────────────────────────────────
        layout.addWidget(mk_page_title(
            "Job Management",
            "Track active, completed, and failed backup and restore jobs"))

        layout.addWidget(_hsep())

        tabs = QTabWidget()

        # ── Active Jobs ───────────────────────────────────────────────────
        tab_active = QWidget()
        tab_active.setStyleSheet("background:transparent;")
        act_lay = QVBoxLayout(tab_active)
        act_lay.setSpacing(12)
        act_lay.setContentsMargins(8, 16, 8, 8)

        act_lay.addWidget(_sec_label("RUNNING JOBS"))
        self.tbl_active = self.build_jobs_table()
        act_lay.addWidget(self.tbl_active)

        cb = QHBoxLayout()
        cb.setSpacing(8)
        btn_cancel_job = QPushButton("\u2715  Cancel Selected Job")
        btn_cancel_job.setStyleSheet(BTN_DANGER)
        btn_cancel_job.clicked.connect(self.cancel_active_job_from_table)
        cb.addWidget(btn_cancel_job)
        cb.addStretch()
        act_lay.addLayout(cb)

        tabs.addTab(tab_active, "Active")

        # ── History ───────────────────────────────────────────────────────
        tab_hist = QWidget()
        tab_hist.setStyleSheet("background:transparent;")
        hist_lay = QVBoxLayout(tab_hist)
        hist_lay.setSpacing(12)
        hist_lay.setContentsMargins(8, 16, 8, 8)

        hist_lay.addWidget(_sec_label("ALL JOBS"))
        self.tbl_history = self.build_jobs_table()
        hist_lay.addWidget(self.tbl_history)

        tabs.addTab(tab_hist, "History")

        # ── Errors ────────────────────────────────────────────────────────
        tab_err = QWidget()
        tab_err.setStyleSheet("background:transparent;")
        err_lay = QVBoxLayout(tab_err)
        err_lay.setSpacing(12)
        err_lay.setContentsMargins(8, 16, 8, 8)

        err_lay.addWidget(_sec_label("FAILED JOBS"))
        self.tbl_errors = self.build_jobs_table()
        err_lay.addWidget(self.tbl_errors)

        eb = QHBoxLayout()
        eb.setSpacing(8)
        btn_exp_err = QPushButton("\U0001f4e4  Export Verbose Error Log")
        btn_exp_err.setStyleSheet(BTN_SECONDARY)
        btn_exp_err.clicked.connect(self.export_error_log)
        eb.addWidget(btn_exp_err)
        eb.addStretch()
        err_lay.addLayout(eb)

        tabs.addTab(tab_err, "Errors")

        layout.addWidget(tabs)
        return page

    def build_jobs_table(self):
        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels([
            "Date & Time", "Type", "Target", "Status", "Details"])
        t.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        return t

    def cancel_active_job_from_table(self):
        row = self.tbl_active.currentRow()
        if row < 0:
            return QMessageBox.warning(
                self, "Selection Required",
                "Please select a running job from the table to cancel.")

        job_id = self.tbl_active.item(row, 0).data(Qt.ItemDataRole.UserRole)

        if (getattr(self, 'current_job_id', None) == job_id
                and self.process.state() == QProcess.ProcessState.Running):
            if confirm_action(
                    self, "Cancel Active Job",
                    "Are you sure you want to forcibly terminate "
                    "this active job?",
                    detail="The running process will be killed immediately. "
                           "Any partially written data may be incomplete.",
                    confirm_text="Kill Process", destructive=True,
                    icon_char="⛔"):
                self.stop_process()
        else:
            target_job = next(
                (j for j in self.job_history
                 if j.get("id") == job_id), None)

            unit = target_job.get("systemd_unit", "") if target_job else ""

            if unit:
                if confirm_action(
                        self, "Stop Scheduled Task",
                        "This is a running scheduled task managed "
                        "by systemd.",
                        detail=f"Unit: {unit}\n\n"
                               f"The systemd service will be stopped.",
                        confirm_text="Stop Task", destructive=True,
                        icon_char="⏹"):
                    result = subprocess.run(
                        ["systemctl", "stop", unit],
                        capture_output=True, text=True)
                    if result.returncode != 0:
                        pid = target_job.get("pid")
                        if pid:
                            try:
                                subprocess.run(
                                    ["pkill", "-9", "-P", str(pid)],
                                    stderr=subprocess.DEVNULL)
                                os.kill(int(pid), 9)
                            except Exception:
                                pass
                    target_job["status"] = "Failed"
                    target_job["description"] = (
                        "Cancelled by user from Job Manager "
                        f"(systemctl stop {unit}).")
                    self.write_jobs()
                    self.log(
                        f"SYS: Scheduled task {unit} stopped by user.")
                    self.refresh_jobs_ui()
            else:
                if confirm_action(
                        self, "Terminate Background Job",
                        "This job is running autonomously in the "
                        "background (or is a stuck ghost task).",
                        detail="It will be forcefully killed and "
                               "marked as failed.",
                        confirm_text="Kill Job", destructive=True,
                        icon_char="⛔"):
                    if target_job:
                        pid = target_job.get("pid")
                        if pid:
                            try:
                                subprocess.run(
                                    ["pkill", "-9", "-P", str(pid)],
                                    stderr=subprocess.DEVNULL)
                                os.kill(int(pid), 9)
                            except Exception:
                                pass
                        target_job["status"] = "Failed"
                        target_job["description"] = (
                            "Forcefully terminated by user via "
                            "Job Manager.")
                        self.write_jobs()
                        self.log(
                            f"SYS: Background job {job_id} forcefully "
                            f"terminated.")
                        self.refresh_jobs_ui()

    def refresh_jobs_ui(self):
        if not hasattr(self, 'tbl_active'):
            return

        self.tbl_active.setRowCount(0)
        self.tbl_history.setRowCount(0)
        self.tbl_errors.setRowCount(0)

        def create_row(j):
            s = j.get("status", "")
            items = [
                QTableWidgetItem(j.get("time", "")),
                QTableWidgetItem(j.get("type", "")),
                QTableWidgetItem(j.get("target", "")),
                QTableWidgetItem(s),
                QTableWidgetItem(j.get("description", "")),
            ]
            c_cell = items[3]
            c_cell.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            if s == "Completed":
                c_cell.setForeground(QBrush(QColor("#10b981")))
            elif s == "Running":
                c_cell.setForeground(QBrush(QColor("#0ea5e9")))
            elif s == "Stalled":
                c_cell.setForeground(QBrush(QColor("#f59e0b")))
            elif s == "Cancelled":
                c_cell.setForeground(QBrush(QColor("#a78bfa")))
            elif s in ["Failed", "Error"]:
                c_cell.setForeground(QBrush(QColor("#ef4444")))
            return items

        for job in reversed(self.job_history):
            s = job.get("status", "")

            # History — all jobs
            rc_hist = self.tbl_history.rowCount()
            self.tbl_history.insertRow(rc_hist)
            for i, item in enumerate(create_row(job)):
                self.tbl_history.setItem(rc_hist, i, item)

            # Active — running / stalled
            if s in ["Running", "Stalled"]:
                rc = self.tbl_active.rowCount()
                self.tbl_active.insertRow(rc)
                row_items = create_row(job)
                row_items[0].setData(
                    Qt.ItemDataRole.UserRole, job.get("id"))
                for i, item in enumerate(row_items):
                    self.tbl_active.setItem(rc, i, item)

            # Errors — failed
            if s in ["Failed", "Error"]:
                rc_err = self.tbl_errors.rowCount()
                self.tbl_errors.insertRow(rc_err)
                row_items = create_row(job)
                row_items[0].setData(
                    Qt.ItemDataRole.UserRole, job.get("id"))
                for i, item in enumerate(row_items):
                    self.tbl_errors.setItem(rc_err, i, item)

        # Keep dashboard in sync
        if hasattr(self, 'refresh_dashboard'):
            self.refresh_dashboard()

    def export_error_log(self):
        row = self.tbl_errors.currentRow()
        if row < 0:
            return QMessageBox.warning(
                self, "Selection Required",
                "Please select a failed job to export its log.")

        job_id = self.tbl_errors.item(row, 0).data(
            Qt.ItemDataRole.UserRole)
        target_job = next(
            (j for j in self.job_history if j.get("id") == job_id), None)
        if not target_job or not target_job.get("log"):
            return QMessageBox.information(
                self, "No Log",
                "No verbose error log was captured for this job.")

        sudo_user = os.environ.get("SUDO_USER", "root")
        downloads_dir = (
            f"/home/{sudo_user}/Downloads"
            if sudo_user != "root" else "/root/Downloads")
        if not os.path.exists(downloads_dir):
            os.makedirs(downloads_dir, exist_ok=True)

        path = os.path.join(
            downloads_dir, f"ArchVault_Error_Log_{job_id}.txt")
        try:
            with open(path, "w") as f:
                f.write(target_job["log"])
            if sudo_user != "root":
                uid = int(subprocess.check_output(
                    ["id", "-u", sudo_user]).strip())
                gid = int(subprocess.check_output(
                    ["id", "-g", sudo_user]).strip())
                os.chown(path, uid, gid)
            QMessageBox.information(
                self, "Exported",
                f"\u2714 Verbose error log saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
