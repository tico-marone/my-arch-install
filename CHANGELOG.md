# Changelog

All notable changes to `my-arch-install` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-20

### Added
- Display the application version (`my-arch-install v0.2.0`) in the TUI window header across all screens and in the session log header.
- Configure `pyproject.toml` to read the package version dynamically from `my_arch_install.__version__`.
- Stream AUR helper build output lines to the collapsible log drawer (`L`) during installation.

### Fixed
- Run AUR temporary build directory creation and `makepkg -sirc` inside a single `arch-chroot -S` invocation so both share the same ephemeral `/tmp` `tmpfs` mount.
- Mount the EFI System Partition (`/mnt/boot`) with `-o uid=0,gid=0,fmask=0077,dmask=0077` to resolve `bootctl` world-accessible `/boot` warnings.
- Automatically sync `multilib.db` when running official package searches (`pacman -Ss`) if `[multilib]` was enabled after an earlier database sync.

## [0.1.0] - 2026-11-20

### Added
- Initial core installation engine (`ext4`, GPT UEFI, `zram-generator`, `linux-zen`, `systemd-boot`, minimal KDE Plasma).
- Curated package catalog (`config/packages.toml`) with built-in descriptions and rationale.
- Isolated `/tmp` official package search (`pacman -Ss` / `pacman -Si`) and Reflector mirror ranking.
- Centered responsive 2-column grid Textual TUI with `ansi-dark` Linux TTY support and minimalist progress screen.
