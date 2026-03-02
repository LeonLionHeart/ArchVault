# ArchVault

**Backup & Restore Manager for Arch Linux**

A full-featured, themeable GUI application for managing system backups on Arch Linux. Supports multiple backup engines, network/local/cloud targets, scheduled tasks via systemd, and a customizable dashboard.

![Version](https://img.shields.io/badge/version-5.0.0--beta-blue) ![License](https://img.shields.io/badge/license-GPL--3.0-green) ![Platform](https://img.shields.io/badge/platform-Arch%20Linux-1793d1) ![Python](https://img.shields.io/badge/python-3.10%2B-yellow)
## Installation

### From AUR (recommended)
```
yay -S archvault
```
or

```
paru -S archvault
```

### Manual Install

```
git clone https://github.com/LeonLionHeart/archvault.git  
cd archvault  
makepkg -si
```
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

## Screenshots
<img width="3833" height="2073" alt="image" src="https://github.com/user-attachments/assets/e03f5258-776c-492c-ab27-df8d3ba004de" />
<img width="3833" height="2073" alt="image" src="https://github.com/user-attachments/assets/b928e061-0ec1-4beb-a44f-62bd1df8519f" />
<img width="3833" height="2073" alt="image" src="https://github.com/user-attachments/assets/70feaf55-5247-41b7-b958-8098b45be729" />

### Security

- Password-protected access with PBKDF2 key derivation

- Optional GPG encryption for backup archives

- Profile credentials encrypted at rest

- File permissions locked to `0600`

### Dependencies

**Required:**

- `python python-pyqt6 python-cryptography rsync btrfs-progs systemd`

- `tar`, `gzip`, `coreutils`
**Optional:**

- `rclone` — Cloud backup support

- `python-boto3` — Direct AWS S3 uploads

- `zstd` — Zstandard compression

- `cifs-utils` — SMB/CIFS shares

- `nfs-utils` — NFS shares

- `sshfs` — SSHFS mounts

- `openssh` — SFTP targets

- `libnotify` — Desktop notifications


## Usage

```
\# Launch the GUI (will prompt for root via polkit)  
archvault  
  
\# Run a scheduled task headlessly (used by systemd timers)  
archvault-task "task-name"
```

ArchVault requires root privileges for system-level backup operations. The launcher uses `pkexec` (polkit) for graphical privilege escalation, falling back to `sudo` if polkit is unavailable.


## Configuration

All configuration is stored in `/etc/archvault/`:

| File | Purpose |
| - | - |
| `app\_settings.json` | Application settings & theme |
| `archvault\_profiles.json` | Backup target profiles |
| `archvault\_tasks.json` | Scheduled task definitions |
| `archvault\_jobs.json` | Job history log |
| `archvault.salt` | Password salt (PBKDF2) |
| `archvault.verify` | Password verification token |



## Project Structure

```
archvault.py               \# Entry point & main window  
core\_backend.py            \# Settings, profiles, encryption, jobs  
core\_engine.py             \# Shared engine logic (legacy compat)  
engine\_base.py             \# Process management, progress, validation  
engine\_backup.py           \# Backup execution (all engines)  
engine\_restore.py          \# Restore execution (all engines)  
engine\_cloud.py            \# Cloud upload via rclone / boto3  
ui\_shell.py                \# Theme gallery, sidebar, top bar  
ui\_tab\_dashboard.py        \# Dashboard with 2D tile grid  
ui\_tab\_backup.py           \# Backup page UI  
ui\_tab\_restore.py          \# Restore page UI  
ui\_tab\_jobs.py             \# Job manager (active, history, errors)  
ui\_tab\_tasks.py            \# Scheduled tasks editor  
ui\_tab\_settings.py         \# Settings, changelog, about  
ui\_tab\_snapshot\_browser.py \# Browse & manage backup snapshots  
ui\_tabs\_main.py            \# Main tab builder  
ui\_tabs\_targets.py         \# Target profile editors (all 5 types)  
ui\_widgets.py              \# ToggleSwitch, ConfirmDialog  
soft\_ui\_components.py      \# Shared button styles & page titles
```


## License

This project is licensed under the **GNU General Public License v3.0** — see [LICENSE](file:///home/chase/Downloads/files%20(1)/archvault-5.0.0-beta/LICENSE) for details.


## Contributing

Contributions are welcome! 

Please open an issue or pull request on GitHub.

