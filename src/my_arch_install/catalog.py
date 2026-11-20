"""Load and query the curated TOML package catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from .models import PackageGroup, PackageItem


DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "packages.toml"


@dataclass(frozen=True, slots=True)
class PackageCatalog:
    """In-memory view of curated package groups and package entries."""

    groups: dict[str, PackageGroup]
    packages: tuple[PackageItem, ...]

    @property
    def required_names(self) -> set[str]:
        return {pkg.name for pkg in self.packages if pkg.required}

    @property
    def default_selected_names(self) -> set[str]:
        return {pkg.name for pkg in self.packages if pkg.required or pkg.default}

    def by_name(self, name: str) -> PackageItem | None:
        for pkg in self.packages:
            if pkg.name == name:
                return pkg
        return None

    def by_group(self, group_id: str) -> list[PackageItem]:
        return [pkg for pkg in self.packages if pkg.group == group_id]


def load_catalog(path: Path | None = None) -> PackageCatalog:
    """Load curated groups and packages from a TOML file."""
    catalog_path = path or DEFAULT_CATALOG_PATH
    with catalog_path.open("rb") as f:
        raw = tomllib.load(f)

    groups: dict[str, PackageGroup] = {}
    for group_id, group_data in raw.get("groups", {}).items():
        groups[group_id] = PackageGroup(
            id=group_id,
            title=str(group_data["title"]),
            description=str(group_data["description"]),
            locked=bool(group_data.get("locked", False)),
        )

    packages: list[PackageItem] = []
    seen_names: set[str] = set()
    for entry in raw.get("packages", []):
        name = str(entry["name"]).strip()
        if not name or name in seen_names:
            raise ValueError(f"Duplicate or empty package name in catalog: {name!r}")
        group_id = str(entry["group"])
        if group_id not in groups:
            raise ValueError(f"Package {name!r} references unknown group {group_id!r}")
        seen_names.add(name)
        packages.append(
            PackageItem(
                name=name,
                repo=str(entry.get("repo", "extra")),
                group=group_id,
                required=bool(entry.get("required", False)),
                default=bool(entry.get("default", False)),
                summary=str(entry["summary"]),
                reason=str(entry["reason"]),
            )
        )

    return PackageCatalog(groups=groups, packages=tuple(packages))
