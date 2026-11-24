"""Live ISO hardware, UEFI, network, disk, and localization discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

from .models import DiskInfo, PreflightStatus


REQUIRED_LIVE_TOOLS = (
    "lsblk",
    "sgdisk",
    "wipefs",
    "mkfs.fat",
    "mkfs.ext4",
    "mount",
    "pacstrap",
    "genfstab",
    "arch-chroot",
    "pacman",
    "reflector",
    "blkid",
)


def is_uefi_boot() -> tuple[bool, str]:
    """Verify UEFI boot mode via /sys/firmware/efi."""
    fw_size = Path("/sys/firmware/efi/fw_platform_size")
    if fw_size.exists():
        bitness = fw_size.read_text(encoding="utf-8").strip()
        return True, f"{bitness}-bit"
    if Path("/sys/firmware/efi/efivars").exists():
        return True, "unknown-bit"
    return False, "BIOS/CSM"


def check_internet(timeout: float = 3.0) -> bool:
    """Check network reachability to archlinux.org."""
    for host, port in (("archlinux.org", 443), ("1.1.1.1", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def is_time_synced() -> bool:
    """Check systemd-timesyncd synchronization status."""
    try:
        res = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.returncode == 0 and res.stdout.strip().lower() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def is_reflector_service_active() -> bool:
    """Check if the Live ISO boot-time reflector.service is still running."""
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "--quiet", "reflector.service"],
            timeout=2,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_preflight() -> PreflightStatus:
    """Collect pre-flight checks for the Live ISO environment."""
    uefi, bitness = is_uefi_boot()
    internet = check_internet()
    synced = is_time_synced()
    reflector_active = is_reflector_service_active()
    missing = tuple(t for t in REQUIRED_LIVE_TOOLS if shutil.which(t) is None)
    return PreflightStatus(
        is_uefi=uefi,
        uefi_bitness=bitness,
        has_internet=internet,
        time_synced=synced,
        reflector_service_active=reflector_active,
        missing_tools=missing,
    )


def _resolve_backing_disk_names(device_path: str) -> set[str]:
    """Walk inverse block hierarchy (`lsblk -s`) to find underlying physical disk names."""
    resolved = os.path.realpath(device_path)
    if not resolved.startswith("/dev/"):
        return set()
    names: set[str] = set()
    try:
        res = subprocess.run(
            ["lsblk", "-n", "-s", "-o", "NAME,TYPE", resolved],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            for raw in res.stdout.splitlines():
                parts = raw.split()
                if len(parts) >= 2 and parts[1].lower() == "disk":
                    names.add(parts[0].strip())
    except (OSError, subprocess.SubprocessError):
        pass
    if not names:
        try:
            pk = subprocess.run(
                ["lsblk", "-n", "-o", "PKNAME", resolved],
                capture_output=True,
                text=True,
                timeout=2,
            )
            parent = pk.stdout.strip()
            if parent:
                names.add(parent)
        except (OSError, subprocess.SubprocessError):
            pass
    return names


def _live_boot_disk_names() -> set[str]:
    """Identify physical disk names backing the Live ISO (dd, Rufus, Ventoy, copytoram) or host root."""
    protected: set[str] = set()
    mount_points = (
        "/",
        "/boot",
        "/run/archiso/bootmnt",
        "/run/archiso/img_dev",
        "/run/archiso/airootfs",
    )
    for mp in mount_points:
        try:
            res = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", mp],
                capture_output=True,
                text=True,
                timeout=2,
            )
            src = res.stdout.strip()
            if src.startswith("/dev/"):
                protected.update(_resolve_backing_disk_names(src))
        except (OSError, subprocess.SubprocessError):
            continue

    cmdline_path = Path("/proc/cmdline")
    if cmdline_path.exists():
        cmdline = cmdline_path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"(?:archisolabel|img_label)=([^\s]+)", cmdline):
            label = match.group(1)
            by_label = Path(f"/dev/disk/by-label/{label}")
            if by_label.exists():
                protected.update(_resolve_backing_disk_names(str(by_label)))
        for match in re.finditer(r"(?:archisodevice|img_dev)=([^\s]+)", cmdline):
            dev = match.group(1)
            if dev.startswith("/dev/"):
                protected.update(_resolve_backing_disk_names(dev))

    return protected


def _node_is_boot_or_root_media(node: dict) -> bool:
    """Recursively inspect a block device and its children for live ISO / root indicators."""
    mounts: list[str] = []
    if isinstance(node.get("mountpoints"), list):
        mounts.extend(str(m) for m in node["mountpoints"] if m)
    if node.get("mountpoint"):
        mounts.append(str(node["mountpoint"]))

    for mp in mounts:
        if mp in {"/", "/boot"} or mp.startswith("/run/archiso"):
            return True

    fstype = str(node.get("fstype") or "").lower()
    if fstype == "iso9660":
        return True

    label = str(node.get("label") or "")
    if label.upper().startswith("ARCH_") or label.lower() == "ventoy":
        return True

    for child in node.get("children") or []:
        if isinstance(child, dict) and _node_is_boot_or_root_media(child):
            return True
    return False


def parse_lsblk_disks(raw_json: str, protected_names: set[str] | None = None) -> list[DiskInfo]:
    """Parse `lsblk -J -b` JSON tree into candidate DiskInfo entries, excluding boot media."""
    protected = protected_names or set()
    data = json.loads(raw_json)
    disks: list[DiskInfo] = []
    for dev in data.get("blockdevices", []):
        dev_type = str(dev.get("type", "")).lower()
        name = str(dev.get("name", ""))
        ro = bool(dev.get("ro", False))
        if dev_type != "disk" or ro or not name:
            continue
        if name.startswith(("loop", "ram", "zram", "sr", "fd")):
            continue
        if name in protected or _node_is_boot_or_root_media(dev):
            continue
        size_val = int(dev.get("size") or 0)
        if size_val <= 0:
            continue
        disks.append(
            DiskInfo(
                name=name,
                path=str(dev.get("path") or f"/dev/{name}"),
                size_bytes=size_val,
                model=str(dev.get("model") or "").strip(),
                transport=str(dev.get("tran") or "").strip(),
                removable=bool(dev.get("rm", False)),
            )
        )
    return disks


def discover_disks() -> list[DiskInfo]:
    """List candidate physical target disks via full lsblk hierarchy."""
    try:
        res = subprocess.run(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,PATH,SIZE,MODEL,TRAN,TYPE,RO,RM,MOUNTPOINTS,LABEL,FSTYPE",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return parse_lsblk_disks(res.stdout, _live_boot_disk_names())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def partition_paths(disk_path: str) -> tuple[str, str]:
    """Return (esp_partition, root_partition) paths for SATA, NVMe, or MMC devices."""
    suffix = "p" if disk_path[-1].isdigit() else ""
    return f"{disk_path}{suffix}1", f"{disk_path}{suffix}2"


def list_timezones() -> list[str]:
    """Discover available timezones on the system."""
    try:
        res = subprocess.run(
            ["timedatectl", "list-timezones"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass

    zoneinfo = Path("/usr/share/zoneinfo")
    if zoneinfo.is_dir():
        zones: list[str] = []
        skip = {"posix", "right", "Etc"}
        for path in sorted(zoneinfo.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(zoneinfo)
            if (
                rel.parts[0] in skip
                or rel.name.endswith(".tab")
                or rel.name in {"leapseconds", "tzdata.zi", "zone.tab", "zone1970.tab", "iso3166.tab"}
            ):
                continue
            zones.append(rel.as_posix())
        if zones:
            return zones

    return ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "UTC"]


def parse_locale_gen(content: str) -> list[str]:
    """Extract UTF-8 locales from /etc/locale.gen."""
    locales: list[str] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("# ") or line == "#":
            continue
        candidate = line.lstrip("#").strip().split()[0]
        if "UTF-8" in line and candidate not in seen:
            seen.add(candidate)
            locales.append(candidate)
    return locales


def list_locales() -> list[str]:
    """Discover UTF-8 locales from /etc/locale.gen."""
    locale_gen = Path("/etc/locale.gen")
    if locale_gen.exists():
        parsed = parse_locale_gen(locale_gen.read_text(encoding="utf-8", errors="ignore"))
        if parsed:
            return parsed
    return ["en_US.UTF-8", "id_ID.UTF-8", "en_GB.UTF-8"]


def list_keymaps() -> list[str]:
    """Discover available console keymaps."""
    try:
        res = subprocess.run(
            ["localectl", "list-keymaps"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass

    kbd_dir = Path("/usr/share/kbd/keymaps")
    if kbd_dir.is_dir():
        maps = sorted({p.name.removesuffix(".map.gz") for p in kbd_dir.rglob("*.map.gz")})
        if maps:
            return maps

    return ["us", "uk", "de-latin1"]


def is_linux_tty() -> bool:
    """Return True when running inside the Linux kernel console (16-color ANSI)."""
    return os.environ.get("TERM", "") == "linux"
