"""Transformations used by ha-patch/apply.py.

The script is standalone and stdlib-only so it can run inside the Home
Assistant container, so it is loaded from its path rather than imported.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "ha-patch" / "apply.py"
_spec = importlib.util.spec_from_file_location("ha_patch_apply", _PATH)
assert _spec is not None and _spec.loader is not None
apply_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_mod)

REPO = "https://github.com/latetedemelon/aionanoleaf2"


# --------------------------------------------------------------------------
# Requirement strings
# --------------------------------------------------------------------------

def test_a_branch_becomes_a_heads_archive() -> None:
    assert apply_mod.requirement_for(REPO, "master") == (
        f"aionanoleaf2 @ {REPO}/archive/refs/heads/master.tar.gz"
    )


@pytest.mark.parametrize("ref", ["v1.1.0", "1.1.0", "v2", "1.0"])
def test_a_version_like_ref_becomes_a_tags_archive(ref: str) -> None:
    assert f"/archive/refs/tags/{ref}.tar.gz" in apply_mod.requirement_for(REPO, ref)


@pytest.mark.parametrize("ref", ["master", "main", "feat/digital-twin", "fix-123"])
def test_a_branch_like_ref_becomes_a_heads_archive(ref: str) -> None:
    assert f"/archive/refs/heads/{ref}.tar.gz" in apply_mod.requirement_for(REPO, ref)


def test_a_trailing_slash_on_the_repo_is_tolerated() -> None:
    assert "//archive" not in apply_mod.requirement_for(REPO + "/", "master")


@pytest.mark.parametrize(
    ("requirement", "name"),
    [
        ("aionanoleaf==0.2.1", "aionanoleaf"),
        ("aionanoleaf2==1.0.2", "aionanoleaf2"),
        ("aionanoleaf2 @ https://example.invalid/x.tar.gz", "aionanoleaf2"),
        ("aionanoleaf2>=1.0,<2", "aionanoleaf2"),
        ("some_other-pkg==1.0", "someotherpkg"),
    ],
)
def test_requirement_names_are_recognised(requirement: str, name: str) -> None:
    assert apply_mod._requirement_name(requirement) == name


# --------------------------------------------------------------------------
# Manifest rewriting
# --------------------------------------------------------------------------

LEGACY_MANIFEST = json.dumps(
    {
        "domain": "nanoleaf",
        "name": "Nanoleaf",
        "config_flow": True,
        "loggers": ["aionanoleaf"],
        "requirements": ["aionanoleaf==0.2.1"],
        "zeroconf": ["_nanoleafapi._tcp.local."],
    }
)

CURRENT_MANIFEST = json.dumps(
    {
        "domain": "nanoleaf",
        "loggers": ["aionanoleaf2"],
        "requirements": ["aionanoleaf2==1.0.2"],
    }
)


def test_the_legacy_requirement_is_replaced() -> None:
    out, _ = apply_mod.patch_manifest(LEGACY_MANIFEST, "aionanoleaf2 @ url", "2026.2.3")
    assert json.loads(out)["requirements"] == ["aionanoleaf2 @ url"]


def test_the_current_pin_is_replaced() -> None:
    out, _ = apply_mod.patch_manifest(CURRENT_MANIFEST, "aionanoleaf2 @ url", "2026.9.4")
    assert json.loads(out)["requirements"] == ["aionanoleaf2 @ url"]


def test_other_requirements_are_kept() -> None:
    raw = json.dumps({"requirements": ["aionanoleaf2==1.0.2", "somethingelse==2.0"]})
    out, _ = apply_mod.patch_manifest(raw, "aionanoleaf2 @ url", "1.0.0")
    assert json.loads(out)["requirements"] == ["aionanoleaf2 @ url", "somethingelse==2.0"]


def test_the_logger_name_is_migrated() -> None:
    out, _ = apply_mod.patch_manifest(LEGACY_MANIFEST, "x @ url", "1.0.0")
    assert json.loads(out)["loggers"] == ["aionanoleaf2"]


def test_a_version_is_added_because_the_loader_requires_one() -> None:
    out, _ = apply_mod.patch_manifest(LEGACY_MANIFEST, "x @ url", "2026.2.3")
    assert json.loads(out)["version"] == "2026.2.3"


def test_everything_else_is_left_alone() -> None:
    out, _ = apply_mod.patch_manifest(LEGACY_MANIFEST, "x @ url", "1.0.0")
    patched = json.loads(out)
    original = json.loads(LEGACY_MANIFEST)
    for key in ("domain", "name", "config_flow", "zeroconf"):
        assert patched[key] == original[key]


# --------------------------------------------------------------------------
# Import rewriting
# --------------------------------------------------------------------------

def test_the_legacy_import_is_rewritten() -> None:
    out, count = apply_mod.patch_python(
        "from aionanoleaf import InvalidToken, Nanoleaf, Unavailable\n"
    )
    assert out == "from aionanoleaf2 import InvalidToken, Nanoleaf, Unavailable\n"
    assert count == 1


def test_an_already_migrated_import_is_untouched() -> None:
    source = "from aionanoleaf2 import Nanoleaf\nimport aionanoleaf2\n"
    out, count = apply_mod.patch_python(source)
    assert out == source
    assert count == 0


def test_rewriting_is_idempotent() -> None:
    once, _ = apply_mod.patch_python("import aionanoleaf\n")
    twice, count = apply_mod.patch_python(once)
    assert twice == once
    assert count == 0


def test_a_longer_name_containing_the_module_is_not_mangled() -> None:
    source = "import aionanoleaf2_extra\nfrom aionanoleaf2.nanoleaf import Nanoleaf\n"
    out, count = apply_mod.patch_python(source)
    assert out == source
    assert count == 0


# --------------------------------------------------------------------------
# Version resolution
# --------------------------------------------------------------------------

def test_an_explicit_version_wins() -> None:
    assert apply_mod.resolve_version("2026.9.4", "2026.2.3") == "2026.9.4"


def test_the_detected_version_is_used_otherwise() -> None:
    assert apply_mod.resolve_version(None, "2026.2.3") == "2026.2.3"


def test_an_undetermined_version_is_refused() -> None:
    """Writing one Home Assistant rejects would silently block the integration."""
    with pytest.raises(apply_mod.Problem, match="--version"):
        apply_mod.resolve_version(None, None)


@pytest.mark.parametrize("version", ["unknown", "", "dev", "v1.0"])
def test_an_implausible_version_is_refused(version: str) -> None:
    with pytest.raises(apply_mod.Problem):
        apply_mod.resolve_version(version, None)


@pytest.mark.parametrize("version", ["2026.9.4", "1.0.0", "2026.2.3b0", "2026.2.3+fork"])
def test_a_plausible_version_is_accepted(version: str) -> None:
    assert apply_mod.resolve_version(version, None) == version
