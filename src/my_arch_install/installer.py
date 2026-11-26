"""Core installation pipeline with structured progress events."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import shlex
import shutil
import time

from .catalog import PackageCatalog
from .models import InstallConfig, ProgressEvent, StepState
from .pacman_ops import configure_pacman_conf
from .runner import CommandRunner
from .system_probe import partition_paths
from .zsh_setup import (
    ATUIN_CONFIG_CONTENT,
    KONSOLE_PROFILE_CONTENT,
    KONSOLE_RC_CONTENT,
    STARSHIP_TOML_CONTENT,
    ZSHRC_CONTENT,
)


TARGET_ROOT = Path("/mnt")
TARGET_BOOT = Path("/mnt/boot")

HOOKS_LINE = (
    "HOOKS=(base systemd autodetect microcode modconf kms keyboard "
    "sd-vconsole block filesystems fsck)"
)

SILENT_BOOT_PARAMS = (
    "quiet loglevel=3 systemd.show_status=auto "
    "rd.udev.log_level=3 vt.global_cursor_default=0"
)


def configure_mkinitcpio_conf(content: str) -> str:
    """Ensure mkinitcpio.conf uses modern systemd-based initramfs hooks."""
    pattern = re.compile(r"^HOOKS=\([^)]*\)", re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(HOOKS_LINE, content, count=1)
    suffix = "" if content.endswith("\n") or not content else "\n"
    return f"{content}{suffix}{HOOKS_LINE}\n"


def render_zram_generator_conf() -> str:
    """Return /etc/systemd/zram-generator.conf content using zstd compression."""
    return (
        "[zram0]\n"
        "zram-size = min(ram / 2, 16384)\n"
        "compression-algorithm = zstd\n"
        "swap-priority = 100\n"
        "fs-type = swap\n"
    )


def render_loader_conf(*, timeout: int = 3) -> str:
    """Return /boot/loader/loader.conf content for systemd-boot."""
    safe_timeout = max(0, min(timeout, 60))
    return (
        "default arch.conf\n"
        f"timeout {safe_timeout}\n"
        "console-mode max\n"
        "editor no\n"
    )


def render_boot_entry(
    root_uuid: str,
    kernel: str = "linux-zen",
    *,
    silent_boot: bool = False,
) -> str:
    """Return /boot/loader/entries/arch.conf content (microcode packed in initramfs)."""
    options = f"root=UUID={root_uuid} rw"
    if silent_boot:
        options = f"{options} {SILENT_BOOT_PARAMS}"
    return (
        f"title   Arch Linux ({kernel})\n"
        f"linux   /vmlinuz-{kernel}\n"
        f"initrd  /initramfs-{kernel}.img\n"
        f"options {options}\n"
    )


def render_hosts(hostname: str) -> str:
    """Return standard /etc/hosts content for the configured hostname."""
    return (
        "127.0.0.1   localhost\n"
        "::1         localhost\n"
        f"127.0.1.1   {hostname}.localdomain {hostname}\n"
    )


def clear_target_pkg_cache(target_root: Path, *, dry_run: bool, runner: CommandRunner) -> None:
    """Deterministically remove cached package archives from target /var/cache/pacman/pkg."""
    cache_dir = target_root / "var" / "cache" / "pacman" / "pkg"
    runner.log(f"CLEAR CACHE {cache_dir}")
    if dry_run:
        runner.log("[DRY-RUN] Skipped cache cleanup")
        return
    if not cache_dir.exists():
        return
    for item in cache_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


class PacmanProgressTracker:
    """Tracks monotonic pacstrap/pacman progress across download, verify, install, and hooks."""

    _STEP_RE = re.compile(r"^\(\s*(\d+)/(\d+)\)\s+(.+)$")
    _PACKAGES_COUNT_RE = re.compile(r"^Packages?\s+\((\d+)\)")
    _DOWNLOADING_RE = re.compile(r"^([^\s]+)\s+downloading\.\.\.$")
    _INSTALLING_RE = re.compile(r"^(installing|upgrading)\s+(\S+?)\.\.\.$", re.IGNORECASE)
    _VERIFY_SIMPLE_RE = re.compile(
        r"^(checking keyring|checking package integrity|loading package files|"
        r"checking for file conflicts|checking available disk space)\.\.\.$",
        re.IGNORECASE,
    )
    _PKG_BASENAME_RE = re.compile(r"^(.+)-[^-]+-[^-]+-[^-]+$")

    def __init__(self) -> None:
        self.percent: float = 0.0
        self.label: str = "Preparing pacstrap transaction..."
        self.expected_downloads: int = 0
        self.downloaded_count: int = 0
        self.installed_count: int = 0

    @classmethod
    def _clean_pkg_name(cls, filename: str) -> str:
        m = cls._PKG_BASENAME_RE.match(filename)
        return m.group(1) if m else filename

    def feed(self, line: str) -> tuple[float, str] | None:
        stripped = line.strip()
        if not stripped:
            return None

        pkg_count_m = self._PACKAGES_COUNT_RE.match(stripped)
        if pkg_count_m:
            self.expected_downloads = max(1, int(pkg_count_m.group(1)))
            self.label = f"Resolving {self.expected_downloads} packages..."
            return self.percent, self.label

        if stripped.startswith(":: Synchronizing package databases"):
            self.percent = max(self.percent, 2.0)
            self.label = "Synchronizing package databases..."
            return self.percent, self.label

        if stripped.startswith(":: Retrieving packages"):
            self.percent = max(self.percent, 5.0)
            self.label = "Retrieving packages..."
            return self.percent, self.label

        dl_m = self._DOWNLOADING_RE.match(stripped)
        if dl_m:
            raw_name = dl_m.group(1)
            if raw_name.endswith(".sig"):
                return None
            self.downloaded_count += 1
            pkg_name = self._clean_pkg_name(raw_name)
            if self.expected_downloads > 0:
                total = max(self.expected_downloads, self.downloaded_count)
                ratio = min(1.0, self.downloaded_count / total)
                pct = 5.0 + ratio * 30.0
                self.label = f"Downloading ({self.downloaded_count}/{total}): {pkg_name}"
            else:
                pct = min(35.0, max(5.0, self.percent + 0.25))
                self.label = f"Downloading ({self.downloaded_count}): {pkg_name}"
            self.percent = max(self.percent, pct)
            return self.percent, self.label

        ver_m = self._VERIFY_SIMPLE_RE.match(stripped)
        if ver_m:
            self.percent = max(self.percent, 40.0)
            self.label = f"Verifying: {ver_m.group(1)}..."
            return self.percent, self.label

        inst_m = self._INSTALLING_RE.match(stripped)
        if inst_m:
            action, pkg_name = inst_m.groups()
            self.installed_count += 1
            total = max(self.expected_downloads, self.downloaded_count, self.installed_count, 1)
            ratio = min(1.0, self.installed_count / total)
            pct = 45.0 + ratio * 45.0
            verb = "Upgrading" if action.lower() == "upgrading" else "Installing"
            self.label = f"{verb} ({self.installed_count}/{total}): {pkg_name}"
            self.percent = max(self.percent, pct)
            return self.percent, self.label

        if stripped.startswith(":: Running post-transaction hooks"):
            self.percent = max(self.percent, 90.0)
            self.label = "Running post-transaction hooks..."
            return self.percent, self.label

        m = self._STEP_RE.match(stripped)
        if not m:
            return None

        cur = int(m.group(1))
        total = max(1, int(m.group(2)))
        detail = m.group(3)
        ratio = min(1.0, cur / total)

        lower = detail.lower()
        if "downloading" in lower or lower.endswith(".pkg.tar.zst"):
            pct = 5.0 + ratio * 30.0
            self.label = f"Downloading ({cur}/{total}) {detail.split()[0]}"
        elif any(
            k in lower
            for k in (
                "checking keys",
                "checking package integrity",
                "loading package files",
                "checking for file conflicts",
                "checking available disk space",
            )
        ):
            pct = 35.0 + ratio * 10.0
            self.label = f"Verifying ({cur}/{total}) {detail}"
        elif "installing" in lower or "upgrading" in lower:
            pkg_part = detail.split()[1] if len(detail.split()) > 1 else detail
            pct = 45.0 + ratio * 45.0
            self.label = f"Installing ({cur}/{total}) {pkg_part}"
        else:
            pct = 90.0 + ratio * 10.0
            self.label = f"Hooks ({cur}/{total}) {detail}"

        self.percent = max(self.percent, min(100.0, pct))
        return self.percent, self.label


STEP_TITLES = (
    "Partition and format target SSD (ESP + ext4)",
    "Mount target filesystems",
    "Install base system, kernel, and KDE Plasma",
    "Configure localization, identity, shell, zram, and initramfs",
    "Install and configure systemd-boot",
    "Install AUR helper and clean up target system",
)


class ArchInstaller:
    """Executes the full Arch Linux installation pipeline."""

    def __init__(
        self,
        config: InstallConfig,
        catalog: PackageCatalog,
        runner: CommandRunner,
        *,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.runner = runner
        self.on_progress = on_progress
        self.total_steps = len(STEP_TITLES)

    def _emit(
        self,
        step_index: int,
        state: StepState,
        task_label: str,
        *,
        task_percent: float | None = None,
        log_line: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if not self.on_progress:
            return
        if state == StepState.DONE:
            overall = ((step_index + 1) / self.total_steps) * 100.0
        elif task_percent is not None:
            overall = ((step_index + (task_percent / 100.0)) / self.total_steps) * 100.0
        else:
            overall = (step_index / self.total_steps) * 100.0

        self.on_progress(
            ProgressEvent(
                step_index=step_index,
                total_steps=self.total_steps,
                step_title=STEP_TITLES[step_index],
                step_state=state,
                overall_percent=min(100.0, overall),
                task_label=task_label,
                task_percent=task_percent,
                log_line=log_line,
                error_message=error_message,
            )
        )

    def validate_config(self) -> None:
        if self.config.target_disk is None:
            raise ValueError("Target disk must be selected.")
        if not self.config.hostname.strip():
            raise ValueError("Hostname must not be empty.")
        if not self.config.username.strip():
            raise ValueError("Username must not be empty.")
        if not self.config.user_password:
            raise ValueError("User password must not be empty.")
        if not self.config.effective_root_password():
            raise ValueError("Root password must not be empty.")

    def run(self) -> None:
        """Execute all installation steps sequentially."""
        self.validate_config()
        steps = (
            self._step_partition_and_format,
            self._step_mount_filesystems,
            self._step_pacstrap_packages,
            self._step_configure_system,
            self._step_install_bootloader,
            self._step_aur_and_cleanup,
        )
        for idx, step_fn in enumerate(steps):
            self._emit(idx, StepState.RUNNING, STEP_TITLES[idx], task_percent=None)
            try:
                step_fn(idx)
            except Exception as exc:
                self.runner.log(f"ERROR in step {idx + 1}: {exc}")
                self._emit(
                    idx,
                    StepState.FAILED,
                    f"Failed: {STEP_TITLES[idx]}",
                    error_message=str(exc),
                )
                raise
            self._emit(idx, StepState.DONE, "Completed", task_percent=100.0)

    def _step_partition_and_format(self, idx: int) -> None:
        assert self.config.target_disk is not None
        disk = self.config.target_disk.path
        esp_part, root_part = partition_paths(disk)

        self._emit(idx, StepState.RUNNING, f"Wiping partition table on {disk}...", task_percent=15.0)
        self.runner.run(["wipefs", "-af", disk])
        self.runner.run(["sgdisk", "--zap-all", disk])

        self._emit(idx, StepState.RUNNING, f"Creating GPT partitions on {disk}...", task_percent=40.0)
        self.runner.run(
            [
                "sgdisk",
                "-n",
                f"1:0:+{self.config.esp_size_mib}M",
                "-t",
                "1:ef00",
                "-c",
                "1:EFI",
                "-n",
                "2:0:0",
                "-t",
                "2:8300",
                "-c",
                "2:ROOT",
                disk,
            ]
        )
        self.runner.run(["partprobe", disk])
        if not self.runner.dry_run:
            time.sleep(1.0)
            self.runner.run(["udevadm", "settle"])

        self._emit(idx, StepState.RUNNING, f"Formatting {esp_part} (FAT32)...", task_percent=65.0)
        self.runner.run(["mkfs.fat", "-F", "32", "-n", "EFI", esp_part])

        self._emit(idx, StepState.RUNNING, f"Formatting {root_part} (ext4)...", task_percent=85.0)
        self.runner.run(["mkfs.ext4", "-F", "-L", "archroot", root_part])

    def _step_mount_filesystems(self, idx: int) -> None:
        assert self.config.target_disk is not None
        esp_part, root_part = partition_paths(self.config.target_disk.path)

        self._emit(idx, StepState.RUNNING, f"Mounting {root_part} to {TARGET_ROOT}...", task_percent=40.0)
        self.runner.run(["mount", root_part, str(TARGET_ROOT)])

        self._emit(idx, StepState.RUNNING, f"Mounting {esp_part} to {TARGET_BOOT}...", task_percent=80.0)
        self.runner.run(
            [
                "mount",
                "--mkdir",
                "-o",
                "uid=0,gid=0,fmask=0077,dmask=0077",
                esp_part,
                str(TARGET_BOOT),
            ]
        )

    def _step_pacstrap_packages(self, idx: int) -> None:
        packages = self.config.all_packages(self.catalog.required_names)

        self._emit(idx, StepState.RUNNING, "Initializing base directory layout...", task_percent=2.0)
        self.runner.run(["pacstrap", "-K", str(TARGET_ROOT), "filesystem"])
        self.runner.write_file(
            TARGET_ROOT / "etc" / "vconsole.conf",
            f"KEYMAP={self.config.keymap}\n",
        )

        live_pacman_conf = Path("/etc/pacman.conf")
        base_conf_text = (
            live_pacman_conf.read_text(encoding="utf-8")
            if live_pacman_conf.exists()
            else (
                "[options]\nParallelDownloads = 5\n"
                "[core]\nInclude = /etc/pacman.d/mirrorlist\n"
                "[extra]\nInclude = /etc/pacman.d/mirrorlist\n"
                "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
            )
        )
        rendered_pacman_conf = configure_pacman_conf(
            base_conf_text,
            color=self.config.pacman.color,
            verbose_pkg_lists=self.config.pacman.verbose_pkg_lists,
            i_love_candy=self.config.pacman.i_love_candy,
            parallel_downloads=self.config.pacman.parallel_downloads,
            enable_multilib=self.config.pacman.enable_multilib,
        )
        temp_pacstrap_conf = Path("/tmp/my-arch-install-pacstrap.conf")

        tracker = PacmanProgressTracker()

        def handle_line(line: str) -> None:
            parsed = tracker.feed(line)
            if parsed is not None:
                pct, label = parsed
                self._emit(idx, StepState.RUNNING, label, task_percent=pct, log_line=line)
            else:
                self._emit(idx, StepState.RUNNING, tracker.label, task_percent=tracker.percent, log_line=line)

        try:
            if not self.runner.dry_run:
                temp_pacstrap_conf.write_text(rendered_pacman_conf, encoding="utf-8")
            else:
                for pct, name in ((15.0, "base"), (35.0, "linux-zen"), (65.0, "plasma-desktop"), (90.0, "firefox")):
                    self._emit(
                        idx,
                        StepState.RUNNING,
                        f"[DRY-RUN] Installing {name} ({len(packages)} total)...",
                        task_percent=pct,
                    )
                    time.sleep(0.03)

            self.runner.run(
                ["pacstrap", "-C", str(temp_pacstrap_conf), str(TARGET_ROOT), *packages],
                on_line=handle_line,
            )
        finally:
            if temp_pacstrap_conf.exists():
                temp_pacstrap_conf.unlink()

        self.runner.write_file(TARGET_ROOT / "etc" / "pacman.conf", rendered_pacman_conf)

    def _step_configure_system(self, idx: int) -> None:
        self._emit(idx, StepState.RUNNING, "Generating /etc/fstab...", task_percent=10.0)
        if self.runner.dry_run:
            self.runner.run(["genfstab", "-U", str(TARGET_ROOT)])
        else:
            res = self.runner.run(["genfstab", "-U", str(TARGET_ROOT)])
            self.runner.write_file(TARGET_ROOT / "etc" / "fstab", res.stdout)

        self._emit(idx, StepState.RUNNING, f"Setting timezone ({self.config.timezone})...", task_percent=20.0)
        self.runner.arch_chroot(
            TARGET_ROOT,
            ["ln", "-sf", f"/usr/share/zoneinfo/{self.config.timezone}", "/etc/localtime"],
        )
        self.runner.arch_chroot(TARGET_ROOT, ["hwclock", "--systohc"])

        self._emit(idx, StepState.RUNNING, f"Generating locale ({self.config.locale})...", task_percent=32.0)
        locale_gen_path = TARGET_ROOT / "etc" / "locale.gen"
        if locale_gen_path.exists() and not self.runner.dry_run:
            lines = locale_gen_path.read_text(encoding="utf-8").splitlines()
            updated_lines: list[str] = []
            found = False
            for line in lines:
                stripped = line.lstrip("#").strip()
                if stripped.startswith(f"{self.config.locale} "):
                    updated_lines.append(stripped)
                    found = True
                else:
                    updated_lines.append(line)
            if not found:
                updated_lines.append(f"{self.config.locale} UTF-8")
            self.runner.write_file(locale_gen_path, "\n".join(updated_lines) + "\n")
        else:
            self.runner.write_file(locale_gen_path, f"{self.config.locale} UTF-8\n")

        self.runner.arch_chroot(TARGET_ROOT, ["locale-gen"])
        self.runner.write_file(TARGET_ROOT / "etc" / "locale.conf", f"LANG={self.config.locale}\n")
        self.runner.write_file(TARGET_ROOT / "etc" / "vconsole.conf", f"KEYMAP={self.config.keymap}\n")

        self._emit(idx, StepState.RUNNING, f"Configuring hostname ({self.config.hostname})...", task_percent=42.0)
        self.runner.write_file(TARGET_ROOT / "etc" / "hostname", f"{self.config.hostname}\n")
        self.runner.write_file(TARGET_ROOT / "etc" / "hosts", render_hosts(self.config.hostname))

        self._emit(idx, StepState.RUNNING, "Configuring zram-generator (zstd)...", task_percent=52.0)
        self.runner.write_file(
            TARGET_ROOT / "etc" / "systemd" / "zram-generator.conf",
            render_zram_generator_conf(),
        )

        self._emit(idx, StepState.RUNNING, "Building initramfs (systemd + microcode hooks)...", task_percent=64.0)
        mkinit_path = TARGET_ROOT / "etc" / "mkinitcpio.conf"
        mkinit_raw = mkinit_path.read_text(encoding="utf-8") if mkinit_path.exists() else ""
        self.runner.write_file(mkinit_path, configure_mkinitcpio_conf(mkinit_raw))
        self.runner.arch_chroot(TARGET_ROOT, ["mkinitcpio", "-P"])

        user_shell = "/usr/bin/zsh" if self.config.shell == "zsh" else "/bin/bash"
        self._emit(idx, StepState.RUNNING, f"Creating user account ({self.config.username})...", task_percent=76.0)
        self.runner.arch_chroot(
            TARGET_ROOT,
            ["useradd", "-m", "-G", "wheel", "-s", user_shell, self.config.username],
        )
        self.runner.arch_chroot(
            TARGET_ROOT,
            ["chpasswd"],
            input_text=f"root:{self.config.effective_root_password()}\n{self.config.username}:{self.config.user_password}\n",
            redact_stdin=True,
        )
        self.runner.write_file(
            TARGET_ROOT / "etc" / "sudoers.d" / "10-wheel",
            "%wheel ALL=(ALL:ALL) ALL\n",
            mode=0o440,
        )

        if self.config.shell == "zsh":
            self._configure_zsh_for_user(idx)

        self._emit(idx, StepState.RUNNING, "Enabling systemd services...", task_percent=95.0)
        services = ["NetworkManager.service", "plasmalogin.service", "systemd-timesyncd.service"]
        if self.config.pacman.enable_paccache_timer:
            services.append("paccache.timer")
        if self.config.mirrors.install_reflector_on_target:
            services.append("reflector.timer")
        self.runner.arch_chroot(TARGET_ROOT, ["systemctl", "enable", *services])

    def _configure_zsh_for_user(self, idx: int) -> None:
        """Install curated Zsh, Starship, Atuin, Zinit, pkgfile, and git-delta config."""
        username = self.config.username
        home_target = TARGET_ROOT / "home" / username
        all_pkgs = self.config.all_packages(self.catalog.required_names)

        self._emit(idx, StepState.RUNNING, "Writing Zsh, Starship, and Atuin configs...", task_percent=82.0)
        self.runner.write_file(home_target / ".zshrc", ZSHRC_CONTENT)
        self.runner.write_file(home_target / ".config" / "starship.toml", STARSHIP_TOML_CONTENT)
        self.runner.write_file(home_target / ".config" / "atuin" / "config.toml", ATUIN_CONFIG_CONTENT)

        if "konsole" in all_pkgs:
            self.runner.write_file(home_target / ".config" / "konsolerc", KONSOLE_RC_CONTENT)
            self.runner.write_file(
                home_target / ".local" / "share" / "konsole" / "Default.profile",
                KONSOLE_PROFILE_CONTENT,
            )

        self.runner.arch_chroot(
            TARGET_ROOT,
            ["chown", "-R", f"{username}:{username}", f"/home/{username}"],
        )

        self._emit(idx, StepState.RUNNING, "Updating pkgfile database...", task_percent=86.0)
        self.runner.arch_chroot(TARGET_ROOT, ["pkgfile", "--update"])

        self._emit(idx, StepState.RUNNING, "Initializing Zinit, git-delta, and Atuin...", task_percent=90.0)
        user_zsh_script = (
            "set -euo pipefail; "
            "mkdir -p ~/.config/atuin ~/.local/state/zsh ~/.cache/zsh ~/.local/share/zinit; "
            "if [ ! -d ~/.local/share/zinit/zinit.git ]; then "
            "git clone https://github.com/zdharma-continuum/zinit.git ~/.local/share/zinit/zinit.git; "
            "fi; "
            "git config --global core.pager delta; "
            'git config --global interactive.diffFilter "delta --color-only"; '
            "git config --global delta.navigate true; "
            "git config --global delta.line-numbers true; "
            "git config --global delta.side-by-side true; "
            "git config --global merge.conflictstyle zdiff3; "
            'export ATUIN_CONFIG_DIR="$HOME/.config/atuin"; '
            "atuin import auto || true; "
            "rm -f ~/.zcompdump* ~/.cache/zsh/.zcompdump*"
        )
        self.runner.arch_chroot(
            TARGET_ROOT,
            ["su", "-s", "/bin/bash", username, "-c", user_zsh_script],
        )

    def _step_install_bootloader(self, idx: int) -> None:
        assert self.config.target_disk is not None
        _, root_part = partition_paths(self.config.target_disk.path)

        self._emit(idx, StepState.RUNNING, "Installing systemd-boot to ESP...", task_percent=35.0)
        self.runner.arch_chroot(TARGET_ROOT, ["bootctl", "install"])

        self._emit(idx, StepState.RUNNING, "Writing loader.conf and Arch boot entry...", task_percent=75.0)
        if self.runner.dry_run:
            root_uuid = "00000000-0000-0000-0000-000000000000"
        else:
            blkid = self.runner.run(["blkid", "-s", "UUID", "-o", "value", root_part])
            root_uuid = blkid.stdout.strip()
            if not root_uuid:
                raise RuntimeError(f"Could not determine UUID for root partition {root_part}")

        self.runner.write_file(
            TARGET_ROOT / "boot" / "loader" / "loader.conf",
            render_loader_conf(timeout=self.config.boot_timeout),
        )
        self.runner.write_file(
            TARGET_ROOT / "boot" / "loader" / "entries" / "arch.conf",
            render_boot_entry(
                root_uuid,
                kernel="linux-zen",
                silent_boot=self.config.boot_silent,
            ),
        )

    def _step_aur_and_cleanup(self, idx: int) -> None:
        if self.config.aur_helper != "none":
            aur_pkg = self.config.aur_helper
            username = self.config.username
            temp_sudoers = TARGET_ROOT / "etc" / "sudoers.d" / "99-my-arch-install-aur"
            chroot_build_dir = "/var/tmp/my-arch-install-aur"
            build_dir_target = TARGET_ROOT / "var" / "tmp" / "my-arch-install-aur"
            makepkg_cfg = f"{chroot_build_dir}/makepkg.conf"

            self._emit(idx, StepState.RUNNING, f"Building and installing {aur_pkg}...", task_percent=25.0)
            try:
                self.runner.write_file(
                    temp_sudoers,
                    f"{username} ALL=(ALL:ALL) NOPASSWD: ALL\n",
                    mode=0o440,
                )
                user_build_cmd = (
                    "set -euo pipefail; "
                    f"cd {chroot_build_dir}; "
                    f"git clone --depth 1 https://aur.archlinux.org/{aur_pkg}.git; "
                    f"cd {aur_pkg}; "
                    f"export CARGO_HOME={chroot_build_dir}/cargo; "
                    f"export RUSTUP_HOME={chroot_build_dir}/rustup; "
                    "export CARGO_INCREMENTAL=0; "
                    f"makepkg --config {makepkg_cfg} -sirc --noconfirm"
                )
                chroot_script = (
                    "set -euo pipefail; "
                    f"trap 'rm -rf {chroot_build_dir}' EXIT; "
                    f"install -d -m 0755 -o {shlex.quote(username)} -g {shlex.quote(username)} {chroot_build_dir}; "
                    "{\n"
                    "  cat /etc/makepkg.conf\n"
                    '  for f in /etc/makepkg.conf.d/*.conf; do if [ -r "$f" ]; then cat "$f"; fi; done\n'
                    "  printf '\\nOPTIONS+=(!debug)\\n'\n"
                    f"}} > {makepkg_cfg}; "
                    f"chown {shlex.quote(username)}:{shlex.quote(username)} {makepkg_cfg}; "
                    f"su -s /bin/bash {shlex.quote(username)} -c {shlex.quote(user_build_cmd)}"
                )
                self.runner.arch_chroot(
                    TARGET_ROOT,
                    ["bash", "-c", chroot_script],
                    on_line=lambda line: self._emit(
                        idx,
                        StepState.RUNNING,
                        f"Building and installing {aur_pkg}...",
                        task_percent=40.0,
                        log_line=line,
                    ),
                )
            finally:
                self._emit(idx, StepState.RUNNING, "Cleaning temporary AUR build files...", task_percent=60.0)
                if not self.runner.dry_run:
                    if temp_sudoers.exists():
                        temp_sudoers.unlink()
                    if build_dir_target.exists():
                        self.runner.run(["rm", "-rf", str(build_dir_target)])

        self._emit(idx, StepState.RUNNING, "Removing orphan packages if present...", task_percent=75.0)
        if not self.runner.dry_run:
            orphans_cmd = 'orphans=$(pacman -Qtdq || true); [ -n "$orphans" ] && pacman -Rns --noconfirm $orphans || true'
            self.runner.arch_chroot(TARGET_ROOT, ["bash", "-c", orphans_cmd])
        else:
            self.runner.arch_chroot(TARGET_ROOT, ["bash", "-c", "pacman -Qtdq || true"])

        if self.config.pacman.clear_cache_after_install:
            self._emit(idx, StepState.RUNNING, "Clearing installation package cache...", task_percent=90.0)
            clear_target_pkg_cache(TARGET_ROOT, dry_run=self.runner.dry_run, runner=self.runner)

        self._emit(idx, StepState.RUNNING, "Flushing filesystem buffers...", task_percent=98.0)
        self.runner.run(["sync"])
