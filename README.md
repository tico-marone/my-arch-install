# my-arch-install

A personal, interactive TUI installer for Arch Linux tailored to a clean, minimal desktop workstation setup.

## Target System Profile

* **Hardware:** Intel Core i7-3770 (Ivy Bridge), Intel HD Graphics 4000 (`mesa` `crocus` + `libva-intel-driver` + `intel-ucode`), SATA SSD, Ethernet (`NetworkManager`)
* **Storage & Boot:** Single-boot UEFI GPT (`1 GiB` FAT32 ESP + `ext4` root), `zram-generator` (`zstd`), `linux-zen` (microcode packed in `initramfs` via `systemd` hooks), standard `systemd-boot`
* **Desktop:** Minimal curated KDE Plasma Wayland (`plasma-desktop`, `plasma-login-manager`, `pipewire`, core utilities) without `kde-applications` or `plasma-meta` bloat

## Key Features

* **Centered Responsive Grid TUI:** Built with Textual (preinstalled on the Arch Linux Live ISO) with automatic `ansi-dark` theme detection on the Linux TTY console (`TERM=linux`).
* **Interactive Configuration:** Timezone, UTF-8 locale, console keymap, hostname, username, passwords, `pacman.conf` toggles (`Color`, `VerbosePkgLists`, `ILoveCandy`, `ParallelDownloads`, `[multilib]`), AUR helper (`paru-bin`, `paru`, or `none`), and Reflector mirror optimization.
* **Built-in Package Inspector & Live Search:**
  * Curated packages include built-in summaries and rationale explaining why each package is included.
  * Search additional packages directly from official Arch repositories (`pacman -Ss` / `pacman -Si`) using an isolated temporary database in `/tmp`.
* **Minimalist Execution & Clean Target System:**
  * Real-time progress bars tracking `pacstrap` downloads, package installation, and hooks while writing logs only to `/tmp/my-arch-install.log` on the Live ISO (`L` toggles live log details).
  * `paru` / `paru-bin` builds run in `/tmp` with `CARGO_HOME` and `RUSTUP_HOME` isolated in `/tmp` and `makepkg -sirc` removes build-time Rust dependencies afterward.

## Running on the Arch Linux Live ISO

Option 1 — Using `git`:

```bash
pacman -Sy --noconfirm git
git clone https://github.com/tico-marone/my-arch-install.git
cd my-arch-install
python run.py
