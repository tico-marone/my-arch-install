"""Input validation and summary formatting helpers."""

from __future__ import annotations

import re

from .catalog import PackageCatalog
from .models import InstallConfig


HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RESERVED_USERNAMES = frozenset({"root", "bin", "daemon", "nobody", "archiso"})


def validate_hostname(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if not cleaned:
        return False, "Hostname is required."
    if not HOSTNAME_RE.match(cleaned):
        return False, "Use 1-63 lowercase letters, digits, or hyphens (no leading/trailing hyphen)."
    return True, "Valid hostname."


def validate_username(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if not cleaned:
        return False, "Username is required."
    if cleaned in RESERVED_USERNAMES:
        return False, f"Username '{cleaned}' is reserved by the system."
    if not USERNAME_RE.match(cleaned):
        return False, "Start with a lowercase letter or '_', followed by [a-z0-9_-] (max 32 chars)."
    return True, "Valid username."


def validate_passwords(
    user_pw: str,
    user_confirm: str,
    *,
    same_root: bool,
    root_pw: str = "",
    root_confirm: str = "",
) -> tuple[bool, str]:
    if not user_pw:
        return False, "User password is required."
    if user_pw != user_confirm:
        return False, "User passwords do not match."
    if not same_root:
        if not root_pw:
            return False, "Root password is required when separated from user password."
        if root_pw != root_confirm:
            return False, "Root passwords do not match."
    return True, "Password configuration is valid."


def format_install_summary(config: InstallConfig, catalog: PackageCatalog) -> str:
    """Build a concise human-readable plan summary for the confirmation screen."""
    disk_str = config.target_disk.label if config.target_disk else "(none selected)"
    all_pkgs = config.all_packages(catalog.required_names)
    optional_curated = sorted(
        p for p in config.selected_curated_packages if p not in catalog.required_names
    )
    custom_names = sorted(config.custom_packages.keys())

    lines = [
        "SYSTEM & LOCALIZATION",
        f"  Hostname        : {config.hostname}",
        f"  Username        : {config.username} (wheel sudo)",
        f"  Root Password   : {'Same as user' if config.same_root_password else 'Separate root password'}",
        f"  Timezone        : {config.timezone}",
        f"  Locale          : {config.locale}",
        f"  Keymap          : {config.keymap}",
        "",
        "STORAGE & BOOT",
        f"  Target Disk     : {disk_str}",
        f"  Partitioning    : GPT ({config.esp_size_mib} MiB FAT32 ESP + ext4 Root)",
        "  Swap            : zram-generator (zstd)",
        "  Kernel          : linux-zen (microcode embedded via mkinitcpio)",
        "  Bootloader      : systemd-boot (UEFI standard)",
        "",
        "PACMAN, MIRRORS & AUR",
        f"  pacman.conf     : Color={config.pacman.color}, VerbosePkgLists={config.pacman.verbose_pkg_lists}, "
        f"ILoveCandy={config.pacman.i_love_candy}, Parallel={config.pacman.parallel_downloads}",
        f"  [multilib]      : {'Enabled' if config.pacman.enable_multilib else 'Disabled'}",
        f"  Cache Cleanup   : Clear install cache={config.pacman.clear_cache_after_install}, "
        f"paccache.timer={config.pacman.enable_paccache_timer}",
        f"  Mirrors         : mode={config.mirrors.mode}"
        + (f" ({','.join(config.mirrors.countries)})" if config.mirrors.mode == "country" else ""),
        f"  AUR Helper      : {config.aur_helper}",
        "",
        f"PACKAGES ({len(all_pkgs)} total)",
        f"  Core Required   : {len(catalog.required_names)} packages",
        f"  Curated Optional: {', '.join(optional_curated) if optional_curated else '(none)'}",
        f"  Custom (pacman) : {', '.join(custom_names) if custom_names else '(none)'}",
    ]
    return "\n".join(lines)
