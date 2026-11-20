"""Isolated pacman search/inspection and Reflector mirror optimization."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

from .models import PackageDetails, SearchedPackage
from .runner import CommandRunner


TEMP_PACMAN_DIR = Path("/tmp/my-arch-install-pacdb")
MIRRORLIST_PATH = Path("/etc/pacman.d/mirrorlist")


def configure_pacman_conf(
    content: str,
    *,
    color: bool = True,
    verbose_pkg_lists: bool = True,
    i_love_candy: bool = False,
    parallel_downloads: int = 5,
    enable_multilib: bool = False,
) -> str:
    """Transform a standard Arch /etc/pacman.conf string according to user toggles."""
    lines = content.splitlines()
    out: list[str] = []
    in_multilib_comment_block = False
    saw_color = False
    saw_verbose = False
    saw_parallel = False
    saw_ilovecandy = False

    for line in lines:
        stripped = line.strip()

        if stripped in {"#[multilib]", "[multilib]"}:
            in_multilib_comment_block = True
            out.append("[multilib]" if enable_multilib else "#[multilib]")
            continue

        if in_multilib_comment_block:
            if stripped in {
                "#Include = /etc/pacman.d/mirrorlist",
                "Include = /etc/pacman.d/mirrorlist",
            }:
                out.append(
                    "Include = /etc/pacman.d/mirrorlist"
                    if enable_multilib
                    else "#Include = /etc/pacman.d/mirrorlist"
                )
                in_multilib_comment_block = False
                continue
            in_multilib_comment_block = False

        if stripped in {"#Color", "Color"}:
            saw_color = True
            out.append("Color" if color else "#Color")
            if i_love_candy and not saw_ilovecandy:
                out.append("ILoveCandy")
                saw_ilovecandy = True
            continue

        if stripped == "ILoveCandy":
            saw_ilovecandy = True
            if i_love_candy:
                out.append("ILoveCandy")
            continue

        if stripped in {"#VerbosePkgLists", "VerbosePkgLists"}:
            saw_verbose = True
            out.append("VerbosePkgLists" if verbose_pkg_lists else "#VerbosePkgLists")
            continue

        if re.match(r"^#?ParallelDownloads\s*=\s*\d+", stripped):
            saw_parallel = True
            out.append(f"ParallelDownloads = {max(1, min(parallel_downloads, 16))}")
            continue

        out.append(line)

    if not (saw_color and saw_verbose and saw_parallel):
        final_lines: list[str] = []
        inserted = False
        for line in out:
            final_lines.append(line)
            if line.strip() == "[options]" and not inserted:
                inserted = True
                if not saw_color and color:
                    final_lines.append("Color")
                if not saw_verbose and verbose_pkg_lists:
                    final_lines.append("VerbosePkgLists")
                if i_love_candy and not saw_ilovecandy:
                    final_lines.append("ILoveCandy")
                if not saw_parallel:
                    final_lines.append(f"ParallelDownloads = {max(1, min(parallel_downloads, 16))}")
        out = final_lines

    return "\n".join(out) + "\n"


def build_temp_pacman_conf(*, enable_multilib: bool) -> Path:
    """Create a temporary pacman.conf in /tmp for isolated pacman -Ss/-Si queries."""
    TEMP_PACMAN_DIR.mkdir(parents=True, exist_ok=True)
    base_conf = Path("/etc/pacman.conf")
    if base_conf.exists():
        content = base_conf.read_text(encoding="utf-8")
    else:
        content = (
            "[options]\n"
            "Architecture = auto\n"
            "ParallelDownloads = 5\n"
            "[core]\nInclude = /etc/pacman.d/mirrorlist\n"
            "[extra]\nInclude = /etc/pacman.d/mirrorlist\n"
            "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
        )
    rendered = configure_pacman_conf(
        content,
        color=False,
        verbose_pkg_lists=False,
        i_love_candy=False,
        parallel_downloads=5,
        enable_multilib=enable_multilib,
    )
    conf_path = TEMP_PACMAN_DIR / "pacman.conf"
    conf_path.write_text(rendered, encoding="utf-8")
    return conf_path


def sync_temp_pacman_db(*, enable_multilib: bool, runner: CommandRunner | None = None) -> Path:
    """Synchronize temporary pacman sync database without modifying Live ISO /var/lib/pacman."""
    conf_path = build_temp_pacman_conf(enable_multilib=enable_multilib)
    db_path = TEMP_PACMAN_DIR / "db"
    db_path.mkdir(parents=True, exist_ok=True)
    cmd = ["pacman", "--config", str(conf_path), "--dbpath", str(db_path), "-Sy"]
    if runner is not None:
        runner.run(cmd)
    else:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    return db_path


def parse_pacman_ss(output: str) -> list[SearchedPackage]:
    """Parse `pacman -Ss` stdout into SearchedPackage entries."""
    results: list[SearchedPackage] = []
    lines = output.splitlines()
    i = 0
    header_re = re.compile(r"^([a-zA-Z0-9_-]+)/([^\s]+)\s+([^\s]+)(?:\s+(.*))?$")
    while i < len(lines):
        line = lines[i]
        m = header_re.match(line.strip()) if line and not line.startswith(" ") else None
        if m:
            repo, name, version, tail = m.groups()
            installed = bool(tail and "[installed" in tail)
            desc = ""
            if i + 1 < len(lines) and lines[i + 1].startswith(" "):
                desc = lines[i + 1].strip()
                i += 1
            results.append(
                SearchedPackage(
                    repo=repo,
                    name=name,
                    version=version,
                    description=desc,
                    installed=installed,
                )
            )
        i += 1
    return results


def parse_pacman_si(output: str) -> PackageDetails | None:
    """Parse `pacman -Si <pkg>` output into PackageDetails."""
    fields: dict[str, str] = {}
    current_key: str | None = None
    for raw in output.splitlines():
        if not raw.strip():
            continue
        if not raw.startswith(" ") and ":" in raw:
            key, val = raw.split(":", 1)
            current_key = key.strip()
            fields[current_key] = val.strip()
        elif current_key is not None:
            fields[current_key] = f"{fields[current_key]} {raw.strip()}".strip()

    if "Name" not in fields:
        return None

    return PackageDetails(
        repo=fields.get("Repository", ""),
        name=fields.get("Name", ""),
        version=fields.get("Version", ""),
        description=fields.get("Description", ""),
        url=fields.get("URL", ""),
        download_size=fields.get("Download Size", ""),
        installed_size=fields.get("Installed Size", ""),
        depends_on=fields.get("Depends On", ""),
        optional_deps=fields.get("Optional Deps", ""),
    )


def search_official_packages(
    query: str,
    *,
    enable_multilib: bool,
    sync_first: bool = False,
) -> list[SearchedPackage]:
    """Query official Arch repositories via isolated `pacman -Ss`."""
    cleaned = query.strip()
    if not cleaned:
        return []

    conf_path = build_temp_pacman_conf(enable_multilib=enable_multilib)
    db_path = TEMP_PACMAN_DIR / "db"
    needs_multilib_sync = enable_multilib and not (db_path / "sync" / "multilib.db").exists()
    if sync_first or not (db_path / "sync").exists() or needs_multilib_sync:
        sync_temp_pacman_db(enable_multilib=enable_multilib)

    terms = cleaned.split()
    res = subprocess.run(
        ["pacman", "--config", str(conf_path), "--dbpath", str(db_path), "-Ss", *terms],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode != 0 and not res.stdout.strip():
        return []
    return parse_pacman_ss(res.stdout)


def inspect_official_package(
    pkg_name: str,
    *,
    enable_multilib: bool,
) -> PackageDetails | None:
    """Fetch extended metadata via isolated `pacman -Si <pkg>`."""
    conf_path = build_temp_pacman_conf(enable_multilib=enable_multilib)
    db_path = TEMP_PACMAN_DIR / "db"
    if not (db_path / "sync").exists():
        return None
    res = subprocess.run(
        ["pacman", "--config", str(conf_path), "--dbpath", str(db_path), "-Si", pkg_name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if res.returncode != 0:
        return None
    return parse_pacman_si(res.stdout)


def parse_reflector_countries(output: str) -> list[str]:
    """Parse `reflector --list-countries` table output into country names."""
    countries: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith(("Country", "---")):
            continue
        match = re.match(r"^(.+?)\s+[A-Z]{2}\s+\d+$", line)
        if match:
            countries.append(match.group(1).strip())
    return countries


def list_reflector_countries() -> list[str]:
    """Return available countries from `reflector --list-countries`."""
    try:
        res = subprocess.run(
            ["reflector", "--list-countries"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0:
            parsed = parse_reflector_countries(res.stdout)
            if parsed:
                return parsed
    except (OSError, subprocess.SubprocessError):
        pass
    return ["Indonesia", "Singapore", "Japan", "Australia", "Germany", "United States"]


def read_active_mirrors(path: Path = MIRRORLIST_PATH, limit: int = 8) -> list[str]:
    """Read active Server = ... URLs from /etc/pacman.d/mirrorlist."""
    if not path.exists():
        return []
    servers: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("Server = "):
            servers.append(line.removeprefix("Server = ").strip())
            if len(servers) >= limit:
                break
    return servers


def run_reflector(
    *,
    mode: str,
    countries: list[str],
    latest: int = 20,
    runner: CommandRunner | None = None,
) -> list[str]:
    """Run reflector to rank fastest HTTPS mirrors and save to /etc/pacman.d/mirrorlist."""
    if mode == "keep":
        return read_active_mirrors()

    cmd = ["reflector", "--protocol", "https", "--sort", "rate"]
    if mode == "country" and countries:
        cmd.extend(["--country", ",".join(countries)])
    cmd.extend(
        [
            "--latest",
            str(latest),
            "--connection-timeout",
            "5",
            "--download-timeout",
            "5",
            "--save",
            str(MIRRORLIST_PATH),
        ]
    )

    if runner is not None:
        runner.run(cmd)
        if runner.dry_run:
            return ["https://mirror.example.org/archlinux/$repo/os/$arch (dry-run)"]
    else:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)

    return read_active_mirrors()
