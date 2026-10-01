"""Single Source of Truth (SSOT) Model and Provider Registry.

Loads model metadata, providers, brand colors, and taxonomy from synhalees/models.json.
Provides helper functions for:
  - Resolving vendor / family taxonomy and open-source status
  - Finding provider brand colors and logos (with theme-friendly color fallbacks)
  - Resolving submission paths (submissions/<vendor>/<family>/<model>.csv)
  - Formatting clean display names for models and providers
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
MODELS_JSON = Path(__file__).resolve().parent / "models.json"


# Kaggle pins a model with an '@' separator ('claude-opus-5@default'), or dashes
# the pin into the slug itself ('claude-haiku-4-5-20251001'). Both forms carry a
# version marker that is not part of the model identity, so submissions key on the
# bare slug and the pin lives in the display name only.
_VERSION_SUFFIX = re.compile(r"-(?:default|\d{8})$")


def canonical_model_slug(name: str) -> str:
    """'anthropic/claude-opus-5@default' -> 'claude-opus-5'.

    Drops the host prefix, turns Kaggle's '@' into '-', then strips the trailing
    version marker ('-default' / '-20251001'). Imports, re-imports and the
    leaderboard therefore all key on one identifier instead of drifting apart
    into per-pin duplicate scorecards.
    """
    slug = str(name or "").split("/")[-1].replace("@", "-")
    return _VERSION_SUFFIX.sub("", slug)


@dataclass
class FamilyInfo:
    slug: str
    name: str
    open_source: bool = False
    match_patterns: List[str] = field(default_factory=list)


@dataclass
class ProviderInfo:
    slug: str
    name: str
    logo: str
    color_name: str
    color_hex: str
    aliases: List[str] = field(default_factory=list)
    families: Dict[str, FamilyInfo] = field(default_factory=dict)


class Registry:
    def __init__(self, data_path: Path = MODELS_JSON):
        self.data_path = data_path
        self._raw_data: Dict[str, Any] = {}
        self.named_colors: Dict[str, str] = {}
        self.default_color_name: str = "gray"
        self.providers: Dict[str, ProviderInfo] = {}
        self.display_names: Dict[str, str] = {}
        self._alias_map: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if not self.data_path.exists():
            return
        self._raw_data = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.named_colors = self._raw_data.get("named_colors", {})
        self.default_color_name = self._raw_data.get("default_color", "gray")
        self.display_names = self._raw_data.get("display_names", {})

        self.providers.clear()
        self._alias_map.clear()

        for prov_slug, prov_dict in self._raw_data.get("providers", {}).items():
            col_name = prov_dict.get("color", self.default_color_name)
            col_hex = self.named_colors.get(
                col_name,
                col_name if col_name.startswith("#") else self.named_colors.get(self.default_color_name, "#78909C")
            )

            fams: Dict[str, FamilyInfo] = {}
            for fam_slug, fam_dict in prov_dict.get("families", {}).items():
                fams[fam_slug] = FamilyInfo(
                    slug=fam_slug,
                    name=fam_dict.get("name", fam_slug.title()),
                    open_source=fam_dict.get("open_source", False),
                    match_patterns=fam_dict.get("match_patterns", [fam_slug]),
                )

            p_info = ProviderInfo(
                slug=prov_slug,
                name=prov_dict.get("name", prov_slug.title()),
                logo=prov_dict.get("logo", prov_slug),
                color_name=col_name,
                color_hex=col_hex,
                aliases=prov_dict.get("aliases", [prov_slug]),
                families=fams,
            )
            self.providers[prov_slug] = p_info

            self._alias_map[prov_slug.lower()] = prov_slug
            for al in p_info.aliases:
                self._alias_map[al.lower()] = prov_slug

    def resolve_color_hex(self, color_name_or_hex: str) -> str:
        """Resolve a named color (e.g. 'blue', 'coral') to its theme-safe hex code."""
        if color_name_or_hex.startswith("#"):
            return color_name_or_hex
        return self.named_colors.get(
            color_name_or_hex,
            self.named_colors.get(self.default_color_name, "#78909C")
        )

    def resolve_provider(self, name_or_alias: str) -> Optional[ProviderInfo]:
        """Find a ProviderInfo by name, slug, or alias."""
        clean = name_or_alias.strip().lower()
        slug = self._alias_map.get(clean)
        if slug:
            return self.providers.get(slug)
        tokens = [t for t in clean.replace("-", " ").replace("_", " ").split() if t]
        for t in tokens:
            if t in self._alias_map:
                return self.providers.get(self._alias_map[t])
        return None

    def resolve_taxonomy(self, model_slug: str) -> Tuple[str, str, bool]:
        """Infer (vendor_slug, family_slug, open_source) from a model slug."""
        slug_lower = model_slug.lower()
        tokens = [t for t in slug_lower.replace("_", "-").split("-") if t]

        for prov_slug, prov in self.providers.items():
            for fam_slug, fam in prov.families.items():
                for pat in fam.match_patterns:
                    pat_lower = pat.lower()
                    if pat_lower in slug_lower or pat_lower in tokens:
                        return (prov_slug, fam_slug, fam.open_source)

        for prov_slug, prov in self.providers.items():
            for alias in prov.aliases:
                if slug_lower.startswith(alias.lower()):
                    return (prov_slug, "other", False)

        return ("other", "other", False)

    def get_model_display_name(self, model_slug: str) -> str:
        """Get formatted model display name."""
        if model_slug in self.display_names:
            return self.display_names[model_slug]

        clean = model_slug.replace("_", "-")
        parts = clean.split("-")
        out_words: List[str] = []
        for p in parts:
            p_lower = p.lower()
            if p_lower in ("flash", "pro", "ultra", "nano", "preview", "lite", "chat"):
                out_words.append(p.title())
            elif any(c.isdigit() for c in p):
                out_words.append(p)
            else:
                out_words.append(p.title())
        return " ".join(out_words)

    def get_provider_display_name(self, provider_slug: str) -> str:
        """Get official provider display name."""
        prov = self.resolve_provider(provider_slug)
        if prov:
            return prov.name
        return provider_slug.title()

    def resolve_submission_path(self, model_slug: str, base_dir: Optional[Path] = None) -> Path:
        """Find existing submission CSV or compute the target hierarchical path.

        Scorecards written before the Kaggle '@' normalization are still named
        ``claude-opus-5@default.csv``. When a normalized slug resolves to a path
        whose legacy '@' twin is still on disk, that file is renamed onto the
        canonical name (and its stale '@' model column rewritten) so a re-import
        updates that file instead of silently creating a second one.
        """
        target_dir = base_dir or (ROOT / "submissions")
        clean_slug = model_slug.lower().removesuffix(".csv")

        matches = list(target_dir.rglob(f"{clean_slug}.csv"))
        if matches:
            self.adopt_legacy_scorecards(target_dir)
            return matches[0]

        # No normalized file yet: a legacy '@' one may be sitting under the very
        # path we are about to create, so adopt it before the caller writes.
        vendor, family, _ = self.resolve_taxonomy(clean_slug)
        target = target_dir / vendor / family / f"{clean_slug}.csv"
        self.adopt_legacy_scorecards(target_dir)
        return target

    def adopt_legacy_scorecards(self, target_dir: Path) -> None:
        """Rename every legacy '<slug>@<version>.csv' under submissions/.

        A scorecard written before the Kaggle '@' normalization keeps that name,
        so a re-import would otherwise create a second file for the same model and
        the leaderboard would show it twice. Renaming is skipped whenever the
        normalized twin already exists (a current file is never overwritten) or
        the pair lives in different families, which would imply a real collision.
        """
        for legacy in sorted(target_dir.rglob("*.csv")):
            clean = legacy.with_name(canonical_model_slug(legacy.stem) + ".csv")
            if clean == legacy or clean.exists() or legacy.parent != clean.parent:
                continue
            # bytes, not text: universal-newline translation would rewrite the
            # scorecards' CRLF endings and show up as a whole-file diff
            raw = legacy.read_bytes().decode("utf-8")
            rows = []
            for i, line in enumerate(raw.splitlines(keepends=True)):
                if i == 0:
                    rows.append(line)
                    continue
                head, sep, tail = line.partition(",")
                rows.append((canonical_model_slug(head) + sep + tail) if sep else line)
            legacy.write_bytes("".join(rows).encode("utf-8"))
            legacy.rename(clean)
            print(f"renamed legacy scorecard: {legacy.name} -> {clean.name}")


default_registry = Registry()