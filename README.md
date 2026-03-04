# ArchVault

**Backup & Restore Manager for Arch Linux**

A full-featured, themeable GUI application for managing system backups on Arch Linux. Supports multiple backup engines, network/local/cloud targets, scheduled tasks via systemd, and a customizable dashboard.

![Version](https://img.shields.io/badge/version-5.0.2--beta-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Platform](https://img.shields.io/badge/platform-Arch%20Linux-1793d1)
![Python](https://img.shields.io/badge/python-3.10%2B-yellow)

---
## Screenshots
<img width="3833" height="2073" alt="Screenshot From 2026-02-28 21-38-08" src="https://github.com/user-attachments/assets/ff8f9670-1bd6-4a57-a204-6af4292ef0f0" />
<img width="3833" height="2073" alt="Screenshot From 2026-02-28 21-39-54" src="https://github.com/user-attachments/assets/ff432afc-ec62-47c0-8ca6-fc673b2eca46" />
<img width="3833" height="2073" alt="Screenshot From 2026-02-28 21-40-34" src="https://github.com/user-attachments/assets/da0b8b4b-a511-43f9-9bf2-d5902330882f" />
<img width="3833" height="2073" alt="Screenshot From 2026-02-28 21-47-13" src="https://github.com/user-attachments/assets/bc5eec96-d02c-4651-9241-c560eecd04ea" />

## Features

### Backup Engines
- **Ext4 tar.gz** — Compressed full-system archives
- **Btrfs Native** — Native btrfs send/receive snapshots
- **Rsync Incremental** — Hardlink-based incremental backups
- **Bare Metal Image** — Full disk image via `dd`
- **Cloud Upload** — rclone-powered uploads to any cloud provider

### Backup Targets
- **Network** — SMB, NFS, SSHFS remote shares
- **Local** — Any mounted local directory
- **USB** — Removable drives with auto-detection
- **SFTP** — Secure FTP with key or password auth
- **Cloud** — AWS S3, Google Cloud, Azure, Backblaze B2, Dropbox, Google Drive

### Scheduling & Automation
- Scheduled tasks via **systemd timers**
- Per-day scheduling with retention policies
- Headless execution for unattended backups
- Native Linux desktop notifications on completion/failure

### Dashboard
- 13 available tiles: stats, charts, disk usage, recent jobs, system health, and more
- Drag-and-drop tile reordering
- 2D grid with free resize from any edge
- Layout persistence across sessions

### UI & Themes
- 6 built-in themes (dark & light)
- Real-time theme preview with instant switching
- Themed confirmation dialogs for all destructive actions
- Clean, modern design with no theme bleed

### Security
- Password-protected access with PBKDF2 key derivation
- Optional GPG encryption for backup archives
- Profile credentials encrypted at rest
- File permissions locked to `0600`

---

## Installation

### From AUR (recommended)

```bash
yay -S archvault
```

Or with any AUR helper:

```bash
paru -S archvault
```

### Manual Install

```bash
git clone https://github.com/YOUR_USERNAME/archvault.git
cd archvault
makepkg -si
```

### Dependencies

**Required:**
- `python` (3.10+)
- `python-pyqt6`
- `python-cryptography`
- `rsync`
- `btrfs-progs`
- `tar`, `gzip`, `coreutils`
- `systemd`

**Optional:**
- `rclone` — Cloud backup support
- `python-boto3` — Direct AWS S3 uploads
- `zstd` — Zstandard compression
- `cifs-utils` — SMB/CIFS shares
- `nfs-utils` — NFS shares
- `sshfs` — SSHFS mounts
- `openssh` — SFTP targets
- `libnotify` — Desktop notifications

---

## Usage

```bash
# Launch the GUI (will prompt for root via polkit)
archvault

# Run a scheduled task headlessly (used by systemd timers)
archvault-task "task-name"
```

ArchVault requires root privileges for system-level backup operations. The launcher uses `pkexec` (polkit) for graphical privilege escalation, falling back to `sudo` if polkit is unavailable.

---

## Configuration

All configuration is stored in `/etc/archvault/`:

| File | Purpose |
|------|---------|
| `app_settings.json` | Application settings & theme |
| `archvault_profiles.json` | Backup target profiles |
| `archvault_tasks.json` | Scheduled task definitions |
| `archvault_jobs.json` | Job history log |
| `archvault.salt` | Password salt (PBKDF2) |
| `archvault.verify` | Password verification token |

---

## Project Structure

```
archvault.py               # Entry point & main window
core_backend.py            # Settings, profiles, encryption, jobs
core_engine.py             # Shared engine logic (legacy compat)
engine_base.py             # Process management, progress, validation
engine_backup.py           # Backup execution (all engines)
engine_restore.py          # Restore execution (all engines)
engine_cloud.py            # Cloud upload via rclone / boto3
ui_shell.py                # Theme gallery, sidebar, top bar
ui_tab_dashboard.py        # Dashboard with 2D tile grid
ui_tab_backup.py           # Backup page UI
ui_tab_restore.py          # Restore page UI
ui_tab_jobs.py             # Job manager (active, history, errors)
ui_tab_tasks.py            # Scheduled tasks editor
ui_tab_settings.py         # Settings, changelog, about
ui_tab_snapshot_browser.py # Browse & manage backup snapshots
ui_tabs_main.py            # Main tab builder
ui_tabs_targets.py         # Target profile editors (all 5 types)
ui_widgets.py              # ToggleSwitch, ConfirmDialog
soft_ui_components.py      # Shared button styles & page titles
```

---

## License

This project is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE) for details.

---

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.
