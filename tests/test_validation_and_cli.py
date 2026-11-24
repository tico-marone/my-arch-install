"""Unit tests for validation, summary formatting, and CLI parsing."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from my_arch_install import __version__
from my_arch_install.catalog import load_catalog
from my_arch_install.cli import build_parser
from my_arch_install.models import DiskInfo, InstallConfig, SearchedPackage
from my_arch_install.validation import (
    format_install_summary,
    validate_hostname,
    validate_passwords,
    validate_username,
)


class ValidationTests(unittest.TestCase):
    def test_hostname_validation(self) -> None:
        self.assertTrue(validate_hostname("arch-pc")[0])
        self.assertFalse(validate_hostname("")[0])
        self.assertFalse(validate_hostname("-bad")[0])
        self.assertFalse(validate_hostname("BadHost")[0])

    def test_username_validation(self) -> None:
        self.assertTrue(validate_username("alex")[0])
        self.assertFalse(validate_username("root")[0])
        self.assertFalse(validate_username("1user")[0])
        self.assertFalse(validate_username("Alex")[0])

    def test_password_validation(self) -> None:
        self.assertTrue(validate_passwords("pw", "pw", same_root=True)[0])
        self.assertFalse(validate_passwords("pw1", "pw2", same_root=True)[0])
        self.assertFalse(
            validate_passwords("pw", "pw", same_root=False, root_pw="", root_confirm="")[0]
        )
        self.assertTrue(
            validate_passwords("pw", "pw", same_root=False, root_pw="rpw", root_confirm="rpw")[0]
        )


class SummaryAndCliTests(unittest.TestCase):
    def test_format_summary_and_cli_args(self) -> None:
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        catalog = load_catalog()
        cfg = InstallConfig(
            hostname="arch-desktop",
            username="alex",
            user_password="secret",
            boot_silent=True,
            boot_timeout=0,
            target_disk=DiskInfo("sda", "/dev/sda", 256 * 1024**3, "SSD", "sata", False),
            selected_curated_packages=set(catalog.default_selected_names),
            custom_packages={"htop": SearchedPackage("extra", "htop", "3.3-1", "Process viewer")},
        )
        summary = format_install_summary(cfg, catalog)
        self.assertIn("arch-desktop", summary)
        self.assertIn("linux-zen", summary)
        self.assertIn("timeout=0s, silent=True", summary)
        self.assertIn("htop", summary)

        args = build_parser().parse_args(["--dry-run", "--theme", "ansi-dark"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.theme, "ansi-dark")


if __name__ == "__main__":
    unittest.main(verbosity=2)
