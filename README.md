# Write Blocker

Software USB write blocker with a PySide6 GUI for forensic acquisition on Linux.

> **TL;DR:** Write Blocker automatically detects USB drives, locks them read-only at the kernel level, and lets you choose which drives to unlock for writing. Designed to prepare forensic acquisitions with tools like Guymager.

## Features

- **Automatic USB detection** via udev (hot-plug support)
- **Default policy: read-only** — every new USB drive is immediately unmounted and write-blocked
- **Kernel-level udev rule** — a temporary udev rule (`/run/udev/rules.d/`) forces `blockdev --setro` on USB devices *before* any desktop automount can occur, eliminating the RW window
- **GNOME automount disabled** at startup (restored on exit) to prevent the desktop from remounting devices behind the blocker's back
- **RO/RW toggle** per drive with mandatory confirmation before enabling writes
- **Proper remount on RW switch** — partitions are unmounted, set RW, and remounted with correct user ownership (`uid`/`gid`)
- **Full protection** — both the raw disk and all its partitions are covered (`blockdev --setro`)
- **Dashboard** — device path, model, vendor, size, serial number, and status at a glance
- **Clean teardown** — udev rule removed and automount restored on exit, `atexit` + signal handlers as safety net; rule lives in `/run/` so it vanishes on reboot even after a hard crash

## How it works

### On startup

1. A temporary udev rule is installed in `/run/udev/rules.d/99-write-blocker.rules` — it tells the kernel to run `blockdev --setro` on every USB block device as soon as it appears, **before** any desktop environment can automount it
2. GNOME automount is disabled via `gsettings` (if available)
3. Already-connected USB drives are scanned and displayed

### When a USB drive is plugged in

1. The udev rule fires immediately → `blockdev --setro` on the raw device and partitions at kernel level
2. The pyudev monitor detects the drive → the app unmounts any leftover mounts and confirms the RO flag
3. A popup asks whether the drive is the **source** (evidence, keep RO) or **target** (destination, switch to RW)

### Switching to RW (target drive)

1. All partitions are **unmounted** (a mount started as RO stays RO even after `blockdev --setrw`, so a fresh remount is required)
2. `blockdev --setrw` is applied to the disk and all partitions
3. Partitions are **mounted fresh** under `/media/<user>/` with the real user's `uid`/`gid` so the file manager can read and write normally

### Switching to RO (evidence drive)

1. All partitions are **unmounted**
2. `blockdev --setro` is applied to the disk and all partitions
3. Nothing is remounted — the drive is fully protected

### On exit

1. The udev rule is removed and `udevadm control --reload-rules` is called
2. GNOME automount is restored to its previous state

> **Note:** This is a software write blocker — it protects against accidental software writes, which is sufficient for standard forensic acquisitions. It is not a hardware write blocker.

## Requirements

- Linux (tested on Ubuntu)
- Python 3.10+
- Root privileges (sudo)
- `libxcb-cursor0` — system dependency required by Qt/PySide6 for cursor rendering under X11/XWayland

## Quick start

```bash
./run.sh
```

The `run.sh` script handles everything automatically:

1. **System dependencies** — installs `libxcb-cursor0` if missing (via `apt-get`)
2. **Virtual environment** — creates the `env` venv and installs Python dependencies if the directory doesn't exist
3. **Display** — forces X11 mode (`QT_QPA_PLATFORM=xcb`) so the window gets proper decorations (close, minimize, maximize) even under Wayland
4. **Execution** — runs the program as `sudo` with the necessary display and D-Bus environment variables

## Manual installation

If you prefer not to use `run.sh`:

```bash
sudo apt-get install -y libxcb-cursor0
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
sudo ./env/bin/python write_blocker.py
```

## Typical forensic workflow

1. Launch the write blocker
2. Plug in the **source** drive (evidence) → keep it in **READ-ONLY** (default)
3. Plug in the **target** drive (destination) → choose **READ-WRITE** in the popup
4. Launch **Guymager** (or any acquisition tool) to image the source onto the target
5. Unplug the drives

## License

MIT
