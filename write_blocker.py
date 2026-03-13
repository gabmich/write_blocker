#!/usr/bin/env python3
"""Write Blocker - Software USB write blocker pour acquisition forensique."""

import atexit
import os
import signal
import subprocess
import sys
from pathlib import Path

import pyudev
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


UDEV_RULE_PATH = Path("/run/udev/rules.d/99-write-blocker.rules")
UDEV_RULE_CONTENT = (
    '# Installed by Write Blocker — removed on exit\n'
    'ACTION=="add", SUBSYSTEM=="block", ENV{ID_BUS}=="usb", '
    'RUN+="/sbin/blockdev --setro %N"\n'
)

# gsettings keys for GNOME automount
_AUTOMOUNT_SCHEMA = "org.gnome.desktop.media-handling"
_AUTOMOUNT_KEYS = ("automount", "automount-open")


class SystemProtection:
    """Install/remove system-level write-block protections (udev rule + automount)."""

    def __init__(self):
        self._udev_installed = False
        self._automount_was_enabled: dict[str, bool] = {}

    # --- public API ---

    def install(self):
        self._install_udev_rule()
        self._disable_automount()

    def remove(self):
        self._remove_udev_rule()
        self._restore_automount()

    # --- udev rule ---

    def _install_udev_rule(self):
        try:
            UDEV_RULE_PATH.parent.mkdir(parents=True, exist_ok=True)
            UDEV_RULE_PATH.write_text(UDEV_RULE_CONTENT)
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                check=True, capture_output=True,
            )
            self._udev_installed = True
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"WARN: impossible d'installer la regle udev: {e}", file=sys.stderr)

    def _remove_udev_rule(self):
        if not self._udev_installed:
            return
        try:
            UDEV_RULE_PATH.unlink(missing_ok=True)
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                check=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"WARN: impossible de retirer la regle udev: {e}", file=sys.stderr)
        self._udev_installed = False

    # --- GNOME automount ---

    def _gsettings_cmd(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run gsettings as the real (non-root) user so it reaches the session dbus."""
        real_user = os.environ.get("SUDO_USER")
        if real_user:
            import pwd
            uid = pwd.getpwnam(real_user).pw_uid
            cmd = ["sudo", "-u", real_user, "env",
                   f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                   "gsettings"] + args
        else:
            cmd = ["gsettings"] + args
        return subprocess.run(cmd, capture_output=True, text=True)

    def _gsettings_get(self, key: str) -> bool | None:
        try:
            ret = self._gsettings_cmd(["get", _AUTOMOUNT_SCHEMA, key])
            if ret.returncode != 0:
                return None
            return ret.stdout.strip() == "true"
        except (FileNotFoundError, KeyError):
            return None

    def _gsettings_set(self, key: str, value: bool):
        try:
            self._gsettings_cmd(["set", _AUTOMOUNT_SCHEMA, key,
                                 "true" if value else "false"])
        except (FileNotFoundError, KeyError):
            pass

    def _disable_automount(self):
        for key in _AUTOMOUNT_KEYS:
            current = self._gsettings_get(key)
            if current is None:
                continue  # gsettings not available or schema not found
            self._automount_was_enabled[key] = current
            if current:
                self._gsettings_set(key, False)

    def _restore_automount(self):
        for key, was_enabled in self._automount_was_enabled.items():
            if was_enabled:
                self._gsettings_set(key, True)
        self._automount_was_enabled.clear()


def has_media(device_path: str) -> bool:
    """Check if a block device actually has media present."""
    try:
        output = subprocess.check_output(
            ["lsblk", "-dnbo", "SIZE", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return bool(output) and int(output) > 0
    except (subprocess.CalledProcessError, ValueError):
        return False


def get_block_size(device_path: str) -> str:
    """Get human-readable size of a block device."""
    try:
        output = subprocess.check_output(
            ["lsblk", "-dnbo", "SIZE", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        size_bytes = int(output)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
    except (subprocess.CalledProcessError, ValueError):
        return "?"
    return "?"


def get_ro_status(device_path: str) -> bool:
    """Check if device is read-only via blockdev."""
    try:
        output = subprocess.check_output(
            ["blockdev", "--getro", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return output == "1"
    except subprocess.CalledProcessError:
        return False


def get_mountpoints(device_path: str) -> list[tuple[str, str]]:
    """Return list of (partition, mountpoint) for a device and its partitions."""
    mounts = []
    try:
        output = subprocess.check_output(
            ["lsblk", "-lnpo", "NAME,MOUNTPOINT", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        for line in output.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[1]:
                mounts.append((parts[0], parts[1]))
    except subprocess.CalledProcessError:
        pass
    return mounts


def unmount_device(device_path: str) -> tuple[bool, str]:
    """Unmount all partitions of a device. Returns (success, error_message)."""
    mounts = get_mountpoints(device_path)
    if not mounts:
        return True, ""
    errors = []
    for part, mountpoint in mounts:
        ret = subprocess.run(
            ["umount", mountpoint],
            capture_output=True, text=True,
        )
        if ret.returncode != 0:
            errors.append(f"{mountpoint}: {ret.stderr.strip()}")
    if errors:
        return False, "\n".join(errors)
    return True, ""


def set_device_ro(device_path: str) -> tuple[bool, str]:
    """Unmount, then set device and all its partitions to read-only.
    Returns (success, error_message)."""
    # First unmount everything
    ok, err = unmount_device(device_path)
    if not ok:
        return False, f"Impossible de demonter le device:\n{err}"
    try:
        # Set the whole disk RO
        subprocess.check_call(["blockdev", "--setro", device_path],
                              stderr=subprocess.DEVNULL)
        # Also set all partitions RO
        partitions = subprocess.check_output(
            ["lsblk", "-lnpo", "NAME", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
        for part in partitions:
            part = part.strip()
            if part and part != device_path:
                subprocess.check_call(["blockdev", "--setro", part],
                                      stderr=subprocess.DEVNULL)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, str(e)


def set_device_rw(device_path: str) -> tuple[bool, str]:
    """Unmount, set device and all partitions to read-write, then remount.
    Returns (success, error_message)."""
    try:
        # Wait for all pending udev events (partition creation + our RO rule)
        subprocess.run(["udevadm", "settle", "--timeout=5"],
                       capture_output=True)

        # 1) Unmount everything first (a mount started as RO stays RO
        #    even after blockdev --setrw, so we must remount from scratch)
        ok, err = unmount_device(device_path)
        # (unmount may fail if nothing was mounted — that's fine)

        # 2) Set RW on disk + all partitions
        subprocess.check_call(["blockdev", "--setrw", device_path],
                              stderr=subprocess.DEVNULL)

        partitions = subprocess.check_output(
            ["lsblk", "-lnpo", "NAME", device_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
        for part in partitions:
            part = part.strip()
            if part and part != device_path:
                subprocess.check_call(["blockdev", "--setrw", part],
                                      stderr=subprocess.DEVNULL)

        # 3) Mount partitions fresh as RW
        _mount_partitions(device_path)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, str(e)


def _mount_partitions(device_path: str):
    """Mount all unmounted partitions of a device."""
    import json
    import pwd

    # Determine real user uid/gid for mount ownership
    real_user = os.environ.get("SUDO_USER", "")
    if real_user:
        pw = pwd.getpwnam(real_user)
        uid, gid = pw.pw_uid, pw.pw_gid
    else:
        uid, gid = os.getuid(), os.getgid()

    try:
        output = subprocess.check_output(
            ["lsblk", "-Jlnpo", "NAME,FSTYPE,MOUNTPOINT,LABEL", device_path],
            text=True, stderr=subprocess.DEVNULL,
        )
        data = json.loads(output)
    except (subprocess.CalledProcessError, ValueError):
        return

    for dev in data.get("blockdevices", []):
        part_name = dev.get("name", "")
        fstype = dev.get("fstype") or ""
        mountpoint = dev.get("mountpoint") or ""
        label = dev.get("label") or ""
        # Skip the raw disk and already-mounted partitions
        if part_name == device_path or mountpoint:
            continue

        # If lsblk didn't detect fstype, try blkid
        if not fstype:
            try:
                blkid_out = subprocess.check_output(
                    ["blkid", "-o", "value", "-s", "TYPE", part_name],
                    text=True, stderr=subprocess.DEVNULL,
                ).strip()
                if blkid_out:
                    fstype = blkid_out
            except subprocess.CalledProcessError:
                pass

        if not fstype:
            continue

        # Get label from blkid if lsblk didn't provide one
        if not label:
            try:
                label = subprocess.check_output(
                    ["blkid", "-o", "value", "-s", "LABEL", part_name],
                    text=True, stderr=subprocess.DEVNULL,
                ).strip()
            except subprocess.CalledProcessError:
                pass

        # Build a mount point under /media/<user>/<label_or_device_name>
        mount_name = label if label else os.path.basename(part_name)
        mount_dir = f"/media/{real_user or 'root'}/{mount_name}"
        os.makedirs(mount_dir, exist_ok=True)

        # FAT/exFAT/NTFS need uid/gid options for user-level write access
        _FS_NEEDS_UID = ("vfat", "fat32", "fat16", "exfat", "ntfs", "ntfs3")
        if fstype in _FS_NEEDS_UID:
            mount_opts = f"rw,uid={uid},gid={gid},dmask=0022,fmask=0133"
        else:
            mount_opts = "rw"

        ret = subprocess.run(
            ["mount", "-t", fstype, "-o", mount_opts, part_name, mount_dir],
            capture_output=True, text=True,
        )
        # For native Linux fs (ext4, xfs...), chown the root of the mount
        if ret.returncode == 0 and fstype not in _FS_NEEDS_UID:
            os.chown(mount_dir, uid, gid)


class UdevSignal(QObject):
    """Bridge between pyudev callbacks and Qt signals."""

    device_added = Signal(dict)
    device_removed = Signal(str)


class WriteBlockerWindow(QMainWindow):
    def __init__(self, protection: SystemProtection):
        super().__init__()
        self.setWindowTitle("Write Blocker - Forensic USB Controller")
        self.setMinimumSize(800, 400)
        self.devices: dict[str, dict] = {}  # device_path -> info dict
        self._protection = protection

        self._build_ui()
        self._start_udev_monitor()
        self._scan_existing_usb_devices()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Header ---
        header = QLabel("WRITE BLOCKER ACTIF")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setFont(QFont("Sans", 16, QFont.Weight.Bold))
        header.setStyleSheet(
            "background-color: #c0392b; color: white; padding: 10px; border-radius: 6px;"
        )
        layout.addWidget(header)

        info = QLabel(
            "Politique par defaut : tout nouveau disque USB est bloque en LECTURE SEULE.\n"
            "Utilisez les boutons pour basculer un disque en lecture/ecriture (cible d'acquisition)."
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("padding: 6px; color: #555;")
        layout.addWidget(info)

        # --- Table ---
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Device", "Modele", "Vendor", "Taille", "S/N", "Statut", "Action"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # --- Footer ---
        footer_layout = QHBoxLayout()
        refresh_btn = QPushButton("Rafraichir")
        refresh_btn.clicked.connect(self._refresh_all)
        footer_layout.addStretch()
        footer_layout.addWidget(refresh_btn)
        layout.addLayout(footer_layout)

    def _start_udev_monitor(self):
        self.udev_signal = UdevSignal()
        self.udev_signal.device_added.connect(self._on_device_added)
        self.udev_signal.device_removed.connect(self._on_device_removed)

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="block", device_type="disk")

        def _udev_event(device):
            if device.get("ID_BUS") != "usb":
                return
            action = device.action
            if action == "add":
                if not has_media(device.device_node):
                    return
                info = self._device_info(device)
                self.udev_signal.device_added.emit(info)
            elif action == "remove":
                self.udev_signal.device_removed.emit(device.device_node)

        self.observer = pyudev.MonitorObserver(monitor, callback=_udev_event)
        self.observer.daemon = True
        self.observer.start()

    def _device_info(self, device) -> dict:
        dev_path = device.device_node
        return {
            "path": dev_path,
            "model": device.get("ID_MODEL", "?"),
            "vendor": device.get("ID_VENDOR", "?"),
            "serial": device.get("ID_SERIAL_SHORT", "?"),
            "size": get_block_size(dev_path),
            "ro": get_ro_status(dev_path),
        }

    def _scan_existing_usb_devices(self):
        context = pyudev.Context()
        for device in context.list_devices(subsystem="block", DEVTYPE="disk"):
            if device.get("ID_BUS") == "usb" and has_media(device.device_node):
                info = self._device_info(device)
                self._add_device_to_table(info)

    @Slot(dict)
    def _on_device_added(self, info: dict):
        dev = info["path"]
        # Enforce default policy: unmount + set read-only immediately
        ok, err = set_device_ro(dev)
        if ok:
            info["ro"] = True
        else:
            QMessageBox.critical(
                self, "Erreur write-block",
                f"Impossible de proteger {dev} en lecture seule:\n{err}",
            )
        self._add_device_to_table(info)

        # Popup to let user choose
        reply = QMessageBox.question(
            self,
            "Nouveau disque USB detecte",
            f"Disque : {dev}\n"
            f"Modele : {info['model']}\n"
            f"Taille : {info['size']}\n"
            f"S/N : {info['serial']}\n\n"
            "Le disque a ete DEMONTE et mis en LECTURE SEULE (protege).\n\n"
            "Voulez-vous le passer en LECTURE/ECRITURE ?\n"
            "(Oui = cible d'acquisition, Non = source/evidence)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok, err = set_device_rw(dev)
            if ok:
                info["ro"] = False
                self._update_device_in_table(dev, info)
            else:
                QMessageBox.critical(
                    self, "Erreur",
                    f"Impossible de passer {dev} en RW:\n{err}",
                )

    @Slot(str)
    def _on_device_removed(self, device_path: str):
        if device_path in self.devices:
            row = self._find_row(device_path)
            if row is not None:
                self.table.removeRow(row)
            del self.devices[device_path]

    def _add_device_to_table(self, info: dict):
        dev = info["path"]
        if dev in self.devices:
            self._update_device_in_table(dev, info)
            return
        self.devices[dev] = info

        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, info)

    def _update_device_in_table(self, dev: str, info: dict):
        self.devices[dev] = info
        row = self._find_row(dev)
        if row is not None:
            self._set_row(row, info)

    def _set_row(self, row: int, info: dict):
        ro = info["ro"]
        status_text = "READ-ONLY" if ro else "READ-WRITE"
        status_color = QColor("#27ae60") if ro else QColor("#e67e22")
        btn_text = "Passer en RW" if ro else "Passer en RO"

        items = [
            info["path"],
            info["model"],
            info["vendor"],
            info["size"],
            info["serial"],
            status_text,
        ]
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            if col == 5:  # status column
                item.setForeground(status_color)
                item.setFont(QFont("Sans", -1, QFont.Weight.Bold))
            self.table.setItem(row, col, item)

        btn = QPushButton(btn_text)
        dev = info["path"]
        btn.clicked.connect(lambda checked, d=dev: self._toggle_ro(d))
        self.table.setCellWidget(row, 6, btn)

    def _toggle_ro(self, device_path: str):
        if device_path not in self.devices:
            return
        info = self.devices[device_path]
        if info["ro"]:
            # Going RW — confirm
            reply = QMessageBox.warning(
                self,
                "Confirmer le passage en LECTURE/ECRITURE",
                f"Vous allez AUTORISER L'ECRITURE sur {device_path}.\n\n"
                "Confirmez-vous que ce disque est la CIBLE (destination) "
                "et NON la source (evidence) ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            ok, err = set_device_rw(device_path)
            if not ok:
                QMessageBox.critical(self, "Erreur", f"Echec RW:\n{err}")
                return
            info["ro"] = False
        else:
            ok, err = set_device_ro(device_path)
            if not ok:
                QMessageBox.critical(self, "Erreur", f"Echec RO:\n{err}")
                return
            info["ro"] = True

        self._update_device_in_table(device_path, info)

    def _find_row(self, device_path: str) -> int | None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == device_path:
                return row
        return None

    def _refresh_all(self):
        """Refresh RO status for all tracked devices."""
        for dev, info in self.devices.items():
            info["ro"] = get_ro_status(dev)
            self._update_device_in_table(dev, info)

    def closeEvent(self, event):
        self.observer.stop()
        self._protection.remove()
        event.accept()


def main():
    if os.geteuid() != 0:
        print(
            "ERREUR: Ce programme doit etre lance en tant que root.\n"
            "Usage: sudo ./env/bin/python write_blocker.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Install system-level protections (udev rule + disable automount)
    protection = SystemProtection()
    protection.install()

    # Safety net: remove protections on crash / SIGTERM / SIGINT
    atexit.register(protection.remove)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = WriteBlockerWindow(protection)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
