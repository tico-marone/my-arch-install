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
        return False, "1-63 lowercase letters, digits, or hyphens."
    return True, "Valid"


def validate_username(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if not cleaned:
        return False, "Username is required."
    if cleaned in RESERVED_USERNAMES:
        return False, f"'{cleaned}' is reserved."
    if not USERNAME_RE.match(cleaned):
        return False, "Lowercase [a-z_][a-z0-9_-]* (max 32 chars)."
    return True, "Valid"


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
            return False, "Root password is required."
        if root_pw != root_confirm:
            return False, "Root passwords do not match."
    return True, "Valid"


def format_install_summary(config: InstallConfig, catalog: PackageCatalog) -> str:
    """Build a concise plan summary for the review screen."""
    disk_str = config.target_disk.label if config.target_disk else "(none)"
    all_pkgs = config.all_packages(catalog.required_names)
    optional_curated = sorted(
        p for p in config.selected_curated_packages if p not in catalog.required_names
    )
    custom_names = sorted(config.custom_packages.keys())
    shell_str = "zsh (Zinit + Starship + fzf + Atuin)" if config.shell == "zsh" else "bash (default)"

    lines = [
        "IDENTITY & LOCALIZATION",
        f"  Hostname        : {config.hostname}",
        f"  Username        : {config.username} (wheel)",
        f"  Shell           : {shell_str}",
        f"  Root Password   : {'Same as user' if config.same_root_password else 'Separate'}",
        f"  Timezone        : {config.timezone}",
        f"  Locale          : {config.locale}",
        f"  Keymap          : {config.keymap}",
        "",
        "STORAGE & BOOT",
        f"  Target Disk     : {disk_str}",
        f"  Layout          : GPT ({config.esp_size_mib} MiB FAT32 /boot + ext4 /)",
        "  Swap            : zram (zstd)",
        "  Kernel          : linux-zen",
        f"  Bootloader      : systemd-boot (timeout={config.boot_timeout}s, silent={config.boot_silent})",
        "",
        "SYSTEM & PACKAGES",
        f"  pacman.conf     : Color={config.pacman.color}, Verbose={config.pacman.verbose_pkg_lists}, "
        f"Candy={config.pacman.i_love_candy}, Parallel={config.pacman.parallel_downloads}",
        f"  [multilib]      : {'Enabled' if config.pacman.enable_multilib else 'Disabled'}",
        f"  Cache           : Clear={config.pacman.clear_cache_after_install}, "
        f"paccache.timer={config.pacman.enable_paccache_timer}",
        f"  Mirrors         : {config.mirrors.mode}"
        + (f" ({','.join(config.mirrors.countries)})" if config.mirrors.mode == "country" else ""),
        f"  AUR Helper      : {config.aur_helper}",
        f"  Packages        : {len(all_pkgs)} total ({len(catalog.required_names)} core)",
        f"  Optional        : {', '.join(optional_curated) if optional_curated else '(none)'}",
        f"  Custom (repo)   : {', '.join(custom_names) if custom_names else '(none)'}",
    ]
    return "\n".join(lines)
