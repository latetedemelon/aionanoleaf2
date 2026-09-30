#!/usr/bin/env python3
"""Regenerate custom_components/nanoleaf so Home Assistant uses this fork.

Home Assistant's built-in Nanoleaf integration pins the library it wants in
its manifest. This script copies that integration into custom_components --
where Home Assistant loads it in preference to the built-in one -- and rewrites
only the parts that choose the library.

Nothing else is changed, so the copy behaves exactly like the version of the
integration you are running. That is also why it has to be re-run after a Home
Assistant upgrade: the copy is a snapshot. `--check` tells you when it is stale,
and re-running is idempotent.

Typical use:

    python3 apply.py                       # generate or refresh
    python3 apply.py --check               # is it stale? exit 1 if so
    python3 apply.py --ref v1.1.0          # pin a tag instead of a branch
    python3 apply.py --revert              # remove it again

Only the standard library is used, so it runs inside the Home Assistant
container as-is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

DOMAIN = "nanoleaf"

# The distribution this fork provides. HA >= 2026.3 already requires this name
# (it pins the PyPI release); older versions require "aionanoleaf" and need
# their imports rewritten as well.
PACKAGE = "aionanoleaf2"
LEGACY_PACKAGE = "aionanoleaf"

DEFAULT_REPO = "https://github.com/latetedemelon/aionanoleaf2"
DEFAULT_REF = "master"

PROVENANCE = ".aionanoleaf2-fork.json"
TOOL_VERSION = 1

# Where the integration lives when we cannot import homeassistant, e.g. when
# running under a different interpreter than the one Home Assistant uses.
FALLBACK_HA_ROOTS = (
    Path("/usr/src/homeassistant/homeassistant"),
    Path("/srv/homeassistant/lib/python3/site-packages/homeassistant"),
)

# Candidate configuration directories, most specific first.
FALLBACK_CONFIG_DIRS = (
    Path("/config"),
    Path(os.path.expanduser("~/.homeassistant")),
    Path(os.path.expanduser("~/homeassistant")),
)

# Matches the module name but never "aionanoleaf2": the trailing \b cannot
# match between "f" and "2", since both are word characters.
LEGACY_IMPORT = re.compile(rf"\b{LEGACY_PACKAGE}\b")

# Home Assistant refuses to load a custom integration whose manifest version is
# not CalVer/SemVer/PEP440-ish, so a version we cannot parse must never be
# written out. This is deliberately permissive; it only needs to catch the
# realistic failure, which is a non-version string like "unknown".
PLAUSIBLE_VERSION = re.compile(r"^\d[0-9A-Za-z.+_!-]*$")


class Problem(Exception):
    """Something the user needs to fix before this can run."""


# --------------------------------------------------------------------------
# Locating things
# --------------------------------------------------------------------------

def find_source(explicit: str | None) -> Path:
    """Return the built-in integration directory to copy from."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not (path / "manifest.json").is_file():
            raise Problem(f"{path} does not look like an integration (no manifest.json)")
        return path

    # find_spec does not execute the module, so this works even though the
    # integration imports a library that may not be installed yet.
    try:
        import importlib.util

        spec = importlib.util.find_spec(f"homeassistant.components.{DOMAIN}")
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None
    if spec is not None and spec.origin:
        return Path(spec.origin).parent

    for root in FALLBACK_HA_ROOTS:
        candidate = root / "components" / DOMAIN
        if (candidate / "manifest.json").is_file():
            return candidate

    raise Problem(
        "Could not find Home Assistant's nanoleaf integration. Run this with the "
        "interpreter Home Assistant uses, or pass --ha-path."
    )


def find_config_dir(explicit: str | None) -> Path:
    """Return the Home Assistant configuration directory."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise Problem(f"{path} is not a directory")
        return path

    for candidate in FALLBACK_CONFIG_DIRS:
        if (candidate / "configuration.yaml").is_file():
            return candidate.resolve()

    raise Problem(
        "Could not find your configuration directory (no configuration.yaml in "
        f"{', '.join(str(p) for p in FALLBACK_CONFIG_DIRS)}). Pass --config."
    )


def ha_version(source: Path, explicit_source: bool) -> str | None:
    """Return the Home Assistant version the source integration belongs to.

    Read from the source tree first, so that --ha-path reports the version of
    the tree being copied rather than of whichever Home Assistant happens to be
    importable. Returns None when it cannot be determined.
    """
    # components/<domain> -> components -> homeassistant
    const = source.parent.parent / "const.py"
    if const.is_file():
        match = re.search(
            r'^__version__(?::\s*Final)?\s*=\s*["\']([^"\']+)', const.read_text(), re.M
        )
        if match:
            return match.group(1)

    if explicit_source:
        # An explicit --ha-path may be an unrelated tree, so do not report the
        # importable Home Assistant's version as if it described it.
        return None

    try:
        from homeassistant.const import __version__

        return str(__version__)
    except ImportError:
        return None


def resolve_version(explicit: str | None, detected: str | None) -> str:
    """Return a manifest version Home Assistant will accept, or explain why not."""
    version = explicit or detected
    if version is None:
        raise Problem(
            "Could not determine the Home Assistant version for the manifest, and "
            "a custom integration without a valid version is blocked from loading. "
            "Pass --version (for example --version 2026.9.4)."
        )
    if not PLAUSIBLE_VERSION.match(version):
        raise Problem(
            f"{version!r} is not a version Home Assistant will accept in a custom "
            "integration manifest. Pass --version with something like 2026.9.4."
        )
    return version


# --------------------------------------------------------------------------
# Deciding what to write
# --------------------------------------------------------------------------

def source_files(source: Path) -> list[Path]:
    """Return every file to copy, relative to the source directory."""
    return sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def digest(source: Path) -> str:
    """Fingerprint the source integration so staleness can be detected."""
    sha = hashlib.sha256()
    for relative in source_files(source):
        sha.update(str(relative).encode())
        sha.update(b"\0")
        sha.update((source / relative).read_bytes())
    return sha.hexdigest()[:16]


def requirement_for(repo: str, ref: str) -> str:
    """Return a PEP 508 requirement pointing at a branch or tag of the fork.

    A source archive URL is used rather than git+https so that no git binary is
    needed inside the container. Home Assistant treats any URL requirement as
    unverifiable and re-resolves it on every start, which is what keeps the
    pinned PyPI release from winning.
    """
    repo = repo.rstrip("/")
    kind = "tags" if re.fullmatch(r"v?\d+(\.\d+)*", ref) else "heads"
    return f"{PACKAGE} @ {repo}/archive/refs/{kind}/{ref}.tar.gz"


def patch_manifest(raw: str, requirement: str, version: str) -> tuple[str, list[str]]:
    """Return the rewritten manifest and a description of what changed."""
    manifest = json.loads(raw)
    notes = []

    original = list(manifest.get("requirements", []))
    kept = [r for r in original if _requirement_name(r) not in (PACKAGE, LEGACY_PACKAGE)]
    manifest["requirements"] = [requirement, *kept]
    replaced = [r for r in original if r not in kept]
    notes.append(f"requirements: {replaced or '[]'} -> {requirement}")

    if LEGACY_PACKAGE in manifest.get("loggers", []):
        manifest["loggers"] = [
            PACKAGE if logger == LEGACY_PACKAGE else logger
            for logger in manifest["loggers"]
        ]
        notes.append(f"loggers: {LEGACY_PACKAGE} -> {PACKAGE}")

    # Custom integrations without a valid version are refused by the loader.
    manifest["version"] = version
    notes.append(f"version: {version} (added; required for custom integrations)")

    return json.dumps(manifest, indent=2) + "\n", notes


def _requirement_name(requirement: str) -> str:
    """Return the distribution name of a requirement string, lowercased."""
    name = re.split(r"[\s<>=!~\[@;]", requirement.strip(), maxsplit=1)[0]
    return name.replace("_", "-").lower().replace("-", "")


def patch_python(raw: str) -> tuple[str, int]:
    """Point legacy `aionanoleaf` imports at `aionanoleaf2`."""
    patched, count = LEGACY_IMPORT.subn(PACKAGE, raw)
    return patched, count


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def read_provenance(target: Path) -> dict | None:
    path = target / PROVENANCE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def do_apply(args: argparse.Namespace) -> int:
    source = find_source(args.ha_path)
    config = find_config_dir(args.config)
    target = config / "custom_components" / DOMAIN
    version = resolve_version(args.version, ha_version(source, bool(args.ha_path)))
    requirement = args.requirement or requirement_for(args.repo, args.ref)

    existing = read_provenance(target)
    if target.exists() and existing is None and not args.force:
        raise Problem(
            f"{target} already exists and was not created by this script. "
            "Move it aside, or pass --force to overwrite it."
        )

    print(f"Home Assistant {version}")
    print(f"  source: {source}")
    print(f"  target: {target}")
    print(f"  library: {requirement}")

    files = source_files(source)
    rewritten = 0
    plan: list[tuple[Path, bytes]] = []
    notes: list[str] = []

    for relative in files:
        data = (source / relative).read_bytes()
        if relative.name == "manifest.json":
            patched, notes = patch_manifest(data.decode(), requirement, version)
            data = patched.encode()
        elif relative.suffix == ".py":
            patched_text, count = patch_python(data.decode())
            if count:
                rewritten += 1
                data = patched_text.encode()
        plan.append((relative, data))

    provenance = {
        "tool_version": TOOL_VERSION,
        "ha_version": version,
        "source": str(source),
        "source_digest": digest(source),
        "requirement": requirement,
    }

    print(f"  {len(files)} files, {rewritten} with rewritten imports")
    for note in notes:
        print(f"    manifest {note}")
    if rewritten:
        print(f"    rewrote `{LEGACY_PACKAGE}` -> `{PACKAGE}` in {rewritten} module(s)")
    else:
        print(f"    this Home Assistant already imports `{PACKAGE}`; no code changes needed")

    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0

    # Replace wholesale so files deleted upstream do not linger.
    if target.exists():
        shutil.rmtree(target)
    for relative, data in plan:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (target / PROVENANCE).write_text(json.dumps(provenance, indent=2) + "\n")

    print("\nWritten. Restart Home Assistant to pick it up.")
    print("Home Assistant will install the fork on start; the first start is slower.")
    return 0


def do_check(args: argparse.Namespace) -> int:
    config = find_config_dir(args.config)
    target = config / "custom_components" / DOMAIN
    provenance = read_provenance(target)

    if provenance is None:
        print(f"Not installed: {target} has no {PROVENANCE}")
        return 1

    source = find_source(args.ha_path)
    version = ha_version(source, bool(args.ha_path))
    current = digest(source)

    if version is None:
        print("Cannot tell: the Home Assistant version could not be determined")
        return 1
    if provenance.get("ha_version") != version:
        print(
            f"Stale: generated against Home Assistant {provenance.get('ha_version')}, "
            f"now running {version}. Re-run this script."
        )
        return 1
    if provenance.get("source_digest") != current:
        print("Stale: the built-in integration changed since this was generated. Re-run this script.")
        return 1
    if provenance.get("tool_version") != TOOL_VERSION:
        print("Stale: generated by an older version of this script. Re-run it.")
        return 1

    print(f"Up to date: Home Assistant {version}, library {provenance.get('requirement')}")
    return 0


def do_revert(args: argparse.Namespace) -> int:
    config = find_config_dir(args.config)
    target = config / "custom_components" / DOMAIN

    if not target.exists():
        print(f"Nothing to remove: {target} does not exist")
        return 0
    if read_provenance(target) is None and not args.force:
        raise Problem(
            f"{target} was not created by this script; refusing to delete it. "
            "Pass --force if you are sure."
        )

    if args.dry_run:
        print(f"Dry run: would remove {target}")
        return 0

    shutil.rmtree(target)
    print(f"Removed {target}")
    print("Restart Home Assistant to go back to the built-in integration.")
    print(f"The fork stays installed; Home Assistant will reinstall its pinned {PACKAGE} as needed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="Home Assistant configuration directory")
    parser.add_argument("--ha-path", help="built-in nanoleaf integration directory")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"fork URL (default: {DEFAULT_REPO})")
    parser.add_argument("--ref", default=DEFAULT_REF, help=f"branch or tag (default: {DEFAULT_REF})")
    parser.add_argument(
        "--requirement",
        help="complete requirement string, overriding --repo/--ref "
        "(e.g. a wheel URL from a GitHub release)",
    )
    parser.add_argument(
        "--version",
        help="manifest version to write (default: the detected Home Assistant version)",
    )
    parser.add_argument("--check", action="store_true", help="report staleness and exit")
    parser.add_argument("--revert", action="store_true", help="remove the custom integration")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen")
    parser.add_argument("--force", action="store_true", help="overwrite or delete unrecognised files")
    args = parser.parse_args(argv)

    if args.check and args.revert:
        parser.error("--check and --revert are mutually exclusive")

    try:
        if args.check:
            return do_check(args)
        if args.revert:
            return do_revert(args)
        return do_apply(args)
    except Problem as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
