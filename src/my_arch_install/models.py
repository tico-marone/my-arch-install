"""Data models for my-arch-install configuration, discovery, and progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


AurHelperChoice = Literal["none", "paru-bin", "paru"]
MirrorMode = Literal["keep", "country", "worldwide"]


@dataclass(frozen=True, slots=True)
class PackageGroup:
    """Metadata describing a logical package group in the curated catalog."""

    id: str
    title: str
    description: str
    locked: bool = False


@dataclass(frozen=True, slots=True)
class PackageItem:
    """Curated package entry with built-in explanation and inclusion rationale."""

    name: str
    repo: str
    group: str
    required: bool
    default: bool
    summary: str
    reason: str


@dataclass(frozen=True, slots=True)
class SearchedPackage:
    """Live package result returned by `pacman -Ss`."""

    repo: str
    name: str
    version: str
    description: str
    installed: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.repo}/{self.name}"


@dataclass(frozen=True, slots=True)
class PackageDetails:
    """Extended package metadata returned by `pacman -Si`."""

    repo: str
    name: str
    version: str
    description: str
    url: str = ""
    download_size: str = ""
    installed_size: str = ""
    depends_on: str = ""
    optional_deps: str = ""


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """Physical block device suitable as an installation target."""

    name: str
    path: str
    size_bytes: int
    model: str
    transport: str
    removable: bool

    @property
    def size_gib(self) -> float:
        return self.size_bytes / (1024**3)

    @property
    def label(self) -> str:
        model_text = self.model.strip() or "Unknown model"
        tran_text = (self.transport or "disk").upper()
        rm_tag = " [USB]" if self.removable or tran_text == "USB" else ""
        return f"{self.path}  ({self.size_gib:.1f} GiB · {tran_text}{rm_tag} · {model_text})"


@dataclass(frozen=True, slots=True)
class PreflightStatus:
    """Environment verification results from the Live ISO."""

    is_uefi: bool
    uefi_bitness: str
    has_internet: bool
    time_synced: bool
    reflector_service_active: bool
    missing_tools: tuple[str, ...] = ()

    @property
    def can_install(self) -> bool:
        return self.is_uefi and self.has_internet and not self.missing_tools


@dataclass(slots=True)
class PacmanOptions:
    """Interactive options written to /etc/pacman.conf and cache management."""

    color: bool = True
    verbose_pkg_lists: bool = True
    i_love_candy: bool = False
    parallel_downloads: int = 5
    enable_multilib: bool = False
    clear_cache_after_install: bool = True
    enable_paccache_timer: bool = True


@dataclass(slots=True)
class MirrorOptions:
    """Mirror optimization settings using Reflector."""

    mode: MirrorMode = "keep"
    countries: list[str] = field(default_factory=lambda: ["Indonesia", "Singapore"])
    latest: int = 20
    install_reflector_on_target: bool = False
    active_mirrors_preview: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InstallConfig:
    """Complete installation plan collected interactively from the user."""

    # Localization
    timezone: str = "Asia/Makassar"
    locale: str = "en_US.UTF-8"
    keymap: str = "us"

    # Identity
    hostname: str = ""
    username: str = ""
    user_password: str = ""
    root_password: str = ""
    same_root_password: bool = True

    # Storage & Boot
    target_disk: DiskInfo | None = None
    esp_size_mib: int = 1024
    boot_silent: bool = False
    boot_timeout: int = 3

    # Options & AUR
    pacman: PacmanOptions = field(default_factory=PacmanOptions)
    mirrors: MirrorOptions = field(default_factory=MirrorOptions)
    aur_helper: AurHelperChoice = "paru-bin"

    # Packages
    selected_curated_packages: set[str] = field(default_factory=set)
    custom_packages: dict[str, SearchedPackage] = field(default_factory=dict)

    def effective_root_password(self) -> str:
        return self.user_password if self.same_root_password else self.root_password

    def all_packages(self, catalog_required: set[str]) -> list[str]:
        """Return deduplicated, sorted list of packages to install via pacstrap."""
        pkgs = set(catalog_required)
        pkgs.update(self.selected_curated_packages)
        pkgs.update(self.custom_packages.keys())

        if self.pacman.enable_paccache_timer:
            pkgs.add("pacman-contrib")
        if self.mirrors.install_reflector_on_target:
            pkgs.add("reflector")
        if self.aur_helper != "none":
            pkgs.add("base-devel")
            pkgs.add("git")

        return sorted(pkgs)


class StepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Structured event emitted by the installer for the minimalist progress TUI."""

    step_index: int
    total_steps: int
    step_title: str
    step_state: StepState
    overall_percent: float
    task_label: str
    task_percent: float | None = None
    log_line: str | None = None
    error_message: str | None = None
