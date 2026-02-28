from PyQt6.QtWidgets import QFileDialog, QMessageBox, QApplication, QLineEdit
from PyQt6.QtCore import QUrl
import os
import subprocess
import json

# Import all tab modules
from ui_tab_dashboard import DashboardMixin
from ui_tab_backup import BackupMixin
from ui_tab_restore import RestoreMixin
from ui_tab_tasks import TasksMixin
from ui_tab_jobs import JobsMixin
from ui_tab_settings import SettingsMixin
from ui_tab_snapshot_browser import SnapshotBrowserMixin

VERSION = "v5.0.0-beta"

# Modern button styles - shared across all modules
BTN_PRIMARY = "background-color: #6366f1; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"
BTN_SUCCESS = "background-color: #10b981; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"
BTN_DANGER = "background-color: #ef4444; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"
BTN_WARNING = "background-color: #f59e0b; color: #1c1917; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"
BTN_SECONDARY = "background-color: #3f3f46; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"
BTN_INFO = "background-color: #0ea5e9; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; border: none;"

class UITabsMainMixin(DashboardMixin, BackupMixin, RestoreMixin, SnapshotBrowserMixin, TasksMixin, JobsMixin, SettingsMixin):
    """Main coordinator that combines all tab modules with shared utilities."""
    
    def toggle_pw(self, field, btn):
        """Toggle password field visibility."""
        if field.echoMode() == QLineEdit.EchoMode.Password: 
            field.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("Hide")
        else: 
            field.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("Show")

    def _run_custom_file_dialog(self, title, start_dir, name_filter, is_folder=False):
        """Enhanced file dialog with quick access to mounted drives."""
        dialog = QFileDialog(self, title, start_dir, name_filter)
        if is_folder:
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        
        urls = [QUrl.fromLocalFile("/")]
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user: 
            urls.append(QUrl.fromLocalFile(f"/home/{sudo_user}"))
        
        try:
            out = subprocess.check_output(["lsblk", "-J", "-o", "MOUNTPOINT", "-e", "7"]).decode()
            data = json.loads(out).get("blockdevices", [])
            def extract_mounts(devices):
                mounts = []
                for dev in devices:
                    mnt = dev.get("mountpoint")
                    if mnt and mnt not in ["/", "[SWAP]"] and not mnt.startswith("/boot"): 
                        mounts.append(mnt)
                    if "children" in dev: 
                        mounts.extend(extract_mounts(dev["children"]))
                return mounts
            for m in sorted(set(extract_mounts(data))): 
                urls.append(QUrl.fromLocalFile(m))
        except Exception: 
            pass
        
        dialog.setSidebarUrls(urls)
        dialog.resize(850, 550)
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected: 
                return selected[0]
        return None

    def open_safe_folder_dialog(self, w):
        """Open folder dialog and set result to widget."""
        path = self._run_custom_file_dialog("Select Folder", w.text() if w.text() else "/", "", is_folder=True)
        if path: 
            w.setText(path)

    def export_global_logs(self):
        """One-click export of global activity logs to Downloads folder."""
        sudo_user = os.environ.get("SUDO_USER", "root")
        downloads_dir = f"/home/{sudo_user}/Downloads" if sudo_user != "root" else "/root/Downloads"
        
        if not os.path.exists(downloads_dir):
            os.makedirs(downloads_dir, exist_ok=True)
            if sudo_user != "root":
                uid = int(subprocess.check_output(["id", "-u", sudo_user]).strip())
                gid = int(subprocess.check_output(["id", "-g", sudo_user]).strip())
                os.chown(downloads_dir, uid, gid)
        
        path = os.path.join(downloads_dir, "ArchVault_Activity_Log.txt")
        try:
            with open(path, "w") as f: 
                f.write(self.console.toPlainText())
            if sudo_user != "root":
                uid = int(subprocess.check_output(["id", "-u", sudo_user]).strip())
                gid = int(subprocess.check_output(["id", "-g", sudo_user]).strip())
                os.chown(path, uid, gid)
            if hasattr(self, 'lbl_export_status'):
                self.lbl_export_status.setText(f"✓ Saved to Downloads")
                self.lbl_export_status.setStyleSheet("color: #10b981; font-weight: bold; background-color: transparent;")
            self.log(f"INFO: One-click exported logs to {path}")
        except Exception as e:
            if hasattr(self, 'lbl_export_status'):
                self.lbl_export_status.setText(f"✖ Failed: {e}")
                self.lbl_export_status.setStyleSheet("color: #ef4444; font-weight: bold; background-color: transparent;")

    def validate_inputs(self, fields_with_names, err_lbl):
        """Validate required fields and show errors."""
        has_error = False
        msgs = []
        for field, name in fields_with_names:
            field.setStyleSheet("")
            if not field.text().strip():
                has_error = True
                field.setStyleSheet("border: 1px solid #ef4444;")
                msgs.append(f"• {name} is required.")
        if has_error:
            err_lbl.setText("Please complete the required fields:\n" + "\n".join(msgs))
            err_lbl.show()
            return False
        else:
            err_lbl.hide()
            return True

    def apply_time_format(self):
        """Apply 12/24 hour format to time widgets."""
        fmt = "hh:mm AP" if getattr(self, "settings", {}).get("time_format") == "12 Hour" else "HH:mm"
        if hasattr(self, 't_c_time'): 
            self.t_c_time.setDisplayFormat(fmt)
        if hasattr(self, 't_e_time'): 
            self.t_e_time.setDisplayFormat(fmt)
        if hasattr(self, 'task_time_input'):
            self.task_time_input.setDisplayFormat(fmt)

    def auto_detect_fs(self, target_widget, engine_widget, silent=False):
        """Auto-detect filesystem type and set appropriate engine."""
        if not target_widget or not engine_widget: 
            return
        t_str = target_widget.currentText()
        if not t_str or ": " not in t_str: 
            return
        # Run detection deferred so the UI event loop isn't blocked by df/btrfs calls
        from PyQt6.QtCore import QTimer as _QT
        _QT.singleShot(0, lambda: self._auto_detect_fs_deferred(target_widget, engine_widget, silent))

    def _auto_detect_fs_deferred(self, target_widget, engine_widget, silent):
        """Actual filesystem detection — called deferred via singleShot(0) off the paint cycle."""
        try:
            t_str = target_widget.currentText()
            if not t_str or ": " not in t_str:
                return
            cat_raw, name = t_str.split(": ", 1)
            cat = cat_raw.lower()
            prof = self.profiles.get(cat, {}).get(name, {})
            src_mode = prof.get("source_mode", "Full System")
            src_path = prof.get("source_path", "/")
            if src_mode == "Full System":
                src_path = "/"
            if "Bare Metal" in src_mode:
                engine_widget.setEnabled(False)
                return
            else:
                engine_widget.setEnabled(True)
            out = subprocess.check_output(["df", "-T", src_path], timeout=5).decode().splitlines()
            if len(out) > 1:
                fstype = out[1].split()[1]
                if "btrfs" in fstype.lower():
                    is_subvol = subprocess.run(
                        ["btrfs", "subvolume", "show", src_path],
                        capture_output=True, timeout=5
                    ).returncode == 0
                    if is_subvol:
                        engine_widget.setCurrentIndex(0)
                        if not silent:
                            self.log("SYS: Auto-detected Btrfs Subvolume. Engine set to Native.")
                    else:
                        engine_widget.setCurrentIndex(1)
                        if not silent:
                            self.log("SYS: Auto-detected standard folder. Engine set to Universal Tar.")
                else:
                    engine_widget.setCurrentIndex(1)
                    if not silent:
                        self.log(f"SYS: Auto-detected {fstype}. Engine set to Universal Tar.")
        except Exception:
            pass

    def refresh_dropdowns(self):
        """Refresh all combo boxes and lists with current profile/task data."""
        def safe_combo_update(name, items):
            if name in self.__dict__ and self.__dict__[name] is not None:
                try:
                    w = self.__dict__[name]
                    w.blockSignals(True)
                    w.clear()
                    w.addItems(items)
                    w.blockSignals(False)
                except Exception: 
                    pass

        combo_items = []
        for cat in ["network", "cloud", "local", "usb", "sftp"]:
            for name in self.profiles.get(cat, {}).keys():
                combo_items.append(f"{cat.upper()}: {name}")

        safe_combo_update('target_combo', combo_items)
        safe_combo_update('sched_target_combo', combo_items)
        safe_combo_update('t_e_target', combo_items)

        def safe_list_update(name, items):
            if name in self.__dict__ and self.__dict__[name] is not None:
                try:
                    w = self.__dict__[name]
                    w.clear()
                    w.addItems(items)
                except Exception: 
                    pass

        safe_list_update('net_list', list(self.profiles.get("network", {}).keys()))
        safe_list_update('cld_list', list(self.profiles.get("cloud", {}).keys()))
        safe_list_update('loc_list', list(self.profiles.get("local", {}).keys()))
        safe_list_update('usb_list', list(self.profiles.get("usb", {}).keys()))
        safe_list_update('sftp_list', list(self.profiles.get("sftp", {}).keys()))
        safe_list_update('task_list', list(self.scheduled_tasks.keys()))

        # Refresh restore network profile combo if visible
        if hasattr(self, 'rst_net_profile_combo') and self._rst_net_panel.isVisible():
            self._populate_net_restore_profiles()

        def safe_detect(t_name, e_name):
            if t_name in self.__dict__ and e_name in self.__dict__:
                try: 
                    self.auto_detect_fs(self.__dict__[t_name], self.__dict__[e_name], silent=True)
                except Exception: 
                    pass

        safe_detect('target_combo', 'engine_combo')
        safe_detect('sched_target_combo', 't_c_engine')
        safe_detect('t_e_target', 't_e_engine')

    def validate_net_path(self, text, proto_combo, err_lbl):
        """Validate network path format."""
        proto = proto_combo.currentText()
        if not text: 
            err_lbl.hide()
            return
        if "SMB" in proto:
            if not (text.startswith("//") or text.startswith("\\\\")):
                err_lbl.setText("Warning: SMB paths usually start with // or \\\\")
                err_lbl.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; background-color: transparent;")
                err_lbl.show()
            else: 
                err_lbl.hide()
        elif "NFS" in proto:
            if ":/" not in text:
                err_lbl.setText("Warning: NFS paths usually require ':/' (e.g., IP:/share)")
                err_lbl.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; background-color: transparent;")
                err_lbl.show()
            else: 
                err_lbl.hide()
