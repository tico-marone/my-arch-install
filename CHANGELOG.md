# Changelog

All notable changes to `my-arch-install` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-11-20

### Fixed
- Expose the `control` property on `CleanToggle.Changed` so Textual `@on(..., "#selector")` handlers match without raising `OnDecoratorError`.
- Increase CSS selector specificity on `#window-frame` and `#modal-box` buttons and inputs so Textual's default `tall` borders do not reappear on primary, hovered, or active states.

## [0.3.0] - 2026-11-20

### Changed
- Rewrite the TUI layout and styling for larger screen utilization (`96%` width up to `152` cols, `94%` height up to `46` rows) and cleaner typography.
- Replace built-in `Switch`, `RadioSet`, `TabbedContent`, `ProgressBar`, and `tall` borders with console-safe custom widgets (`CleanToggle`, `CleanProgressBar`, `CleanScrollBarRender`, flat tab switchers, and solid borders) to eliminate `#` rendering artifacts on Linux TTY.
- Register custom `tty-clean` and `modern-dark` themes calibrated for solid-black TTY contrast and 16-color ANSI mapping.
- Consolidate Step 5 options into a single arrow-navigable list and enable arrow-key (`Up`/`Down`/`Left`/`Right`) navigation across all screens without requiring `Tab`.
- Trim verbose, promotional, and redundant text across screens and `config/packages.toml`.

## [0.2.1] - 2026-11-20

### Fixed
- Move temporary AUR helper builds from RAM-capped `/tmp` `tmpfs` to disk-backed `/var/tmp/my-arch-install-aur` (still wiped cleanly via `trap EXIT` and `finally` cleanup) to prevent `No space left on device` during `paru` compilation.
- Pass a temporary `makepkg.conf` with `OPTIONS+=(!debug)` and `CARGO_INCREMENTAL=0` when building the AUR helper to avoid `debuginfo=2` disk bloat and unwanted `-debug` package installation.
- Extract diagnostic error lines (`error:`, `==> ERROR:`, `No space left`) in `CommandError` so cleanup logs do not hide the actual failure reason.

## [0.2.0] - 2026-11-20

### Added
- Display the application version in the TUI window header across all screens and in the session log header.
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
