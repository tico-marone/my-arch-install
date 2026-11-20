"""Unit tests for core my-arch-install modules (no Live ISO required)."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from my_arch_install import __version__
from my_arch_install.catalog import load_catalog
from my_arch_install.installer import (
    ArchInstaller,
    PacmanProgressTracker,
    clear_target_pkg_cache,
    configure_mkinitcpio_conf,
    render_boot_entry,
    render_loader_conf,
    render_zram_generator_conf,
)
from my_arch_install.models import DiskInfo, InstallConfig, ProgressEvent, SearchedPackage
from my_arch_install.pacman_ops import (
    configure_pacman_conf,
    parse_pacman_si,
    parse_pacman_ss,
    parse_reflector_countries,
)
from my_arch_install.runner import CommandError, CommandRunner, extract_error_summary
from my_arch_install.system_probe import parse_locale_gen, parse_lsblk_disks, partition_paths


class CatalogTests(unittest.TestCase):
    def test_load_curated_catalog(self) -> None:
        catalog = load_catalog()
        self.assertIn("base_system", catalog.groups)
        self.assertIn("hardware_intel", catalog.groups)
        self.assertIn("kde_core", catalog.groups)
        self.assertIn("linux-zen", catalog.required_names)
        self.assertIn("plasma-login-manager", catalog.required_names)
        self.assertIn("libva-intel-driver", catalog.default_selected_names)
        self.assertNotIn("vulkan-intel", catalog.default_selected_names)
        for pkg in catalog.packages:
            self.assertTrue(pkg.summary)
            self.assertTrue(pkg.reason)


class SystemProbeTests(unittest.TestCase):
    def test_partition_paths_sata_and_nvme(self) -> None:
        self.assertEqual(partition_paths("/dev/sda"), ("/dev/sda1", "/dev/sda2"))
        self.assertEqual(partition_paths("/dev/nvme0n1"), ("/dev/nvme0n1p1", "/dev/nvme0n1p2"))
        self.assertEqual(partition_paths("/dev/mmcblk0"), ("/dev/mmcblk0p1", "/dev/mmcblk0p2"))

    def test_parse_lsblk_filters_protected_and_loop(self) -> None:
        raw = """
        {
          "blockdevices": [
            {"name": "loop0", "path": "/dev/loop0", "size": 1048576, "model": null, "tran": null, "type": "loop", "ro": true, "rm": false},
            {"name": "sda", "path": "/dev/sda", "size": 256060514304, "model": "Samsung SSD 860", "tran": "sata", "type": "disk", "ro": false, "rm": false},
            {"name": "sdb", "path": "/dev/sdb", "size": 16000000000, "model": "Arch Live USB", "tran": "usb", "type": "disk", "ro": false, "rm": true}
          ]
        }
        """
        disks = parse_lsblk_disks(raw, protected_names={"sdb"})
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].path, "/dev/sda")
        self.assertIn("SATA", disks[0].label)

    def test_parse_locale_gen(self) -> None:
        sample = "# Configuration file for locale-gen\n#en_US.UTF-8 UTF-8  \n#id_ID.UTF-8 UTF-8\n#en_US ISO-8859-1\n"
        self.assertEqual(parse_locale_gen(sample), ["en_US.UTF-8", "id_ID.UTF-8"])


class PacmanOpsTests(unittest.TestCase):
    def test_configure_pacman_conf_toggles(self) -> None:
        sample = (
            "[options]\n"
            "#Color\n"
            "CheckSpace\n"
            "#VerbosePkgLists\n"
            "ParallelDownloads = 5\n"
            "#[multilib]\n"
            "#Include = /etc/pacman.d/mirrorlist\n"
        )
        updated = configure_pacman_conf(
            sample,
            color=True,
            verbose_pkg_lists=True,
            i_love_candy=True,
            parallel_downloads=8,
            enable_multilib=True,
        )
        self.assertIn("\nColor\nILoveCandy\n", updated)
        self.assertIn("\nVerbosePkgLists\n", updated)
        self.assertIn("ParallelDownloads = 8", updated)
        self.assertIn("[multilib]\nInclude = /etc/pacman.d/mirrorlist", updated)

    def test_parse_pacman_ss(self) -> None:
        out = (
            "extra/firefox 132.0-1\n"
            "    Fast, Private & Safe Web Browser\n"
            "multilib/steam 1.0.0.81-1 [installed]\n"
            "    Valve's digital software delivery system\n"
        )
        parsed = parse_pacman_ss(out)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].name, "firefox")
        self.assertFalse(parsed[0].installed)
        self.assertEqual(parsed[1].repo, "multilib")
        self.assertTrue(parsed[1].installed)

    def test_parse_pacman_si(self) -> None:
        out = (
            "Repository      : extra\n"
            "Name            : btop\n"
            "Version         : 1.4.0-1\n"
            "Description     : A monitor of system resources\n"
            "URL             : https://github.com/aristocratos/btop\n"
            "Depends On      : gcc-libs  glibc\n"
            "Download Size   : 560.20 KiB\n"
            "Installed Size  : 1.85 MiB\n"
        )
        details = parse_pacman_si(out)
        assert details is not None
        self.assertEqual(details.name, "btop")
        self.assertEqual(details.download_size, "560.20 KiB")

    def test_parse_reflector_countries(self) -> None:
        out = (
            "Country               Code    Count\n"
            "--------------------- ----    -----\n"
            "Indonesia             ID         10\n"
            "United States         US        112\n"
        )
        self.assertEqual(parse_reflector_countries(out), ["Indonesia", "United States"])


class RunnerAndInstallerTests(unittest.TestCase):
    def test_error_summary_preserves_diagnostic_lines(self) -> None:
        lines = [
            "Compiling foo",
            "error: No space left on device (os error 28)",
            "==> ERROR: A failure occurred in build().",
            "removing rust...",
            "removing llvm...",
            "removing lld...",
            "removing libgit2...",
            "removing llhttp...",
            "removing compiler-rt...",
            ":: Running post-transaction hooks...",
            "(1/1) Arming ConditionNeedsUpdate...",
        ]
        summary = extract_error_summary(lines)
        joined = "\n".join(summary)
        self.assertIn("No space left on device", joined)
        self.assertIn("A failure occurred in build().", joined)

        err = CommandError("makepkg", 4, lines)
        self.assertIn("No space left on device", str(err))

    def test_mkinitcpio_and_boot_templates(self) -> None:
        sample = "MODULES=()\nHOOKS=(base udev autodetect modconf kms keyboard keymap block filesystems fsck)\n"
        updated = configure_mkinitcpio_conf(sample)
        self.assertIn("systemd", updated)
        self.assertIn("microcode", updated)
        self.assertIn("sd-vconsole", updated)

        self.assertIn("zram-size", render_zram_generator_conf())
        self.assertIn("default arch.conf", render_loader_conf())
        entry = render_boot_entry("1234-ABCD")
        self.assertIn("linux   /vmlinuz-linux-zen", entry)
        self.assertIn("initrd  /initramfs-linux-zen.img", entry)
        self.assertNotIn("intel-ucode.img", entry)

    def test_pacman_progress_tracker_monotonic(self) -> None:
        tracker = PacmanProgressTracker()
        p1 = tracker.feed("(1/10) downloading firefox-132.0-1-x86_64.pkg.tar.zst")
        p2 = tracker.feed("(5/10) installing plasma-desktop")
        p3 = tracker.feed("(2/4) Updating icon theme caches...")
        assert p1 and p2 and p3
        self.assertLess(p1[0], p2[0])
        self.assertLess(p2[0], p3[0])

    def test_clear_target_pkg_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tr = Path(tmp)
            pkg_dir = tr / "var" / "cache" / "pacman" / "pkg"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "sample.pkg.tar.zst").write_text("data", encoding="utf-8")
            runner = CommandRunner(dry_run=False, log_path=tr / "test.log")
            clear_target_pkg_cache(tr, dry_run=False, runner=runner)
            self.assertEqual(list(pkg_dir.iterdir()), [])

    def test_dry_run_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "install.log"
            runner = CommandRunner(dry_run=True, log_path=log_file)
            catalog = load_catalog()
            cfg = InstallConfig(
                hostname="arch-desktop",
                username="alex",
                user_password="secret",
                aur_helper="paru",
                target_disk=DiskInfo("sda", "/dev/sda", 256 * 1024**3, "SSD", "sata", False),
                selected_curated_packages=set(catalog.default_selected_names),
                custom_packages={
                    "htop": SearchedPackage("extra", "htop", "3.3-1", "Interactive process viewer")
                },
            )
            events: list[ProgressEvent] = []
            installer = ArchInstaller(cfg, catalog, runner, on_progress=events.append)
            installer.run()

            self.assertTrue(any(e.overall_percent == 100.0 for e in events))
            cmds = "\n".join(runner.recorded_commands)
            self.assertIn(
                "mount --mkdir -o uid=0,gid=0,fmask=0077,dmask=0077 /dev/sda1 /mnt/boot",
                cmds,
            )
            self.assertIn("pacstrap -K /mnt filesystem", cmds)
            self.assertIn("arch-chroot -S /mnt bootctl install", cmds)
            aur_chroots = [c for c in runner.recorded_commands if "my-arch-install-aur" in c]
            self.assertEqual(len(aur_chroots), 1)
            self.assertIn("arch-chroot -S /mnt bash -c", aur_chroots[0])
            self.assertIn("CARGO_HOME=/var/tmp/my-arch-install-aur/cargo", aur_chroots[0])
            self.assertIn("OPTIONS+=(!debug)", aur_chroots[0])
            self.assertIn(
                "makepkg --config /var/tmp/my-arch-install-aur/makepkg.conf -sirc --noconfirm",
                aur_chroots[0],
            )
            log_text = log_file.read_text(encoding="utf-8")
            self.assertIn(f"my-arch-install v{__version__}", log_text)
            self.assertNotIn("secret", log_text)
            self.assertIn("[stdin redacted]", log_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
