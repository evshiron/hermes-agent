"""Profile-owned POSIX Node transactions for embedded Hermes runtimes.

The host bridges security.private_node to HERMES_PRIVATE_NODE for shell helpers.
Only the package manifest survives replacement; native addons are always rebuilt.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def private_node_enabled() -> bool:
    if os.environ.get("HERMES_PRIVATE_NODE") == "1":
        return True
    from hermes_cli.config import load_config
    return bool(load_config().get("security", {}).get("private_node", False))


def automatic_installs_allowed() -> bool:
    # Unlike the Python durable-target policy, a Node install cannot use a
    # Python target directory to bypass the host's prohibition.
    if os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") == "1":
        return False
    from hermes_cli.config import load_config
    return bool(load_config().get("security", {}).get("allow_lazy_installs", True))


def node_env(node: Path) -> dict[str, str]:
    from tools.environments.local import hermes_subprocess_env
    env = hermes_subprocess_env(inherit_credentials=False)
    # Inherited npm flags must not redirect the transaction to the user's tree.
    for key in list(env):
        if key.lower().startswith("npm_config_"):
            del env[key]
    env.update(PATH=str(node / "bin") + os.pathsep + env.get("PATH", ""),
               NPM_CONFIG_PREFIX=str(node), NPM_CONFIG_USERCONFIG=os.devnull,
               NPM_CONFIG_CACHE=str(node.parent / "npm-cache"),
               XDG_CACHE_HOME=str(node.parent / "cache"), CAMOFOX_SKIP_DOWNLOAD="1")
    return env


def packages(node: Path) -> dict[str, str]:
    root = node / "lib" / "node_modules"
    result = {}
    for entry in root.glob("*"):
        for manifest in (entry.glob("*/package.json") if entry.name.startswith("@") else [entry / "package.json"]):
            if not manifest.is_file():
                raise RuntimeError(f"Cannot record installed package: {manifest}")
            data = json.loads(manifest.read_text())
            if data["name"] not in ("npm", "corepack"):
                result[data["name"]] = data["version"]
    return result


def write_manifest(home: Path, installed: dict[str, str]) -> None:
    target = home / "node-packages.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(installed, indent=2, sort_keys=True) + "\n")
    temporary.replace(target)


def validate(node: Path) -> None:
    env = node_env(node)
    for name in ("node", "npm", "npx"):
        subprocess.run([str(node / "bin" / name), "--version"], env=env, check=True,
                       stdout=subprocess.DEVNULL, timeout=30)
    for manifest in (node / "lib/node_modules").glob("**/package.json"):
        # Only top-level packages own global bins; dependency bins are local.
        relative = manifest.relative_to(node / "lib/node_modules")
        if len(relative.parts) != (3 if relative.parts[0].startswith("@") else 2):
            continue
        data = json.loads(manifest.read_text())
        bins = data.get("bin", {})
        names = [data["name"].split("/")[-1]] if isinstance(bins, str) else bins
        for name in names:
            if not (node / "bin" / name).is_file():
                raise RuntimeError(f"Installed command is missing: {name}")


def install_packages(node: Path, specs: list[str], cwd: Path) -> None:
    env = node_env(node)
    npm = str(node / "bin/npm")
    version = subprocess.check_output([npm, "--version"], env=env, text=True).strip()
    flags = []
    if int(version.split(".")[0]) >= 12:
        # Browser tools need these lifecycle scripts (including the native SQLite
        # addon). New script-bearing dependencies must be reviewed before adoption.
        flags = ["--allow-scripts=agent-browser,@askjo/camofox-browser,better-sqlite3",
                 "--strict-allow-scripts"]
    subprocess.run([npm, "install", "--global", *flags, *specs], env=env,
                   cwd=cwd, check=True, timeout=600)
    # Some postinstall scripts replace npm's relative links with absolute paths.
    # Keep those links valid when the staged tree is moved into its final place.
    for link in (node / "bin").iterdir():
        if link.is_symlink():
            target = Path(os.readlink(link))
            if target.is_absolute() and target.is_relative_to(node):
                link.unlink()
                link.symlink_to(os.path.relpath(target, link.parent))


def provision(home: Path, *, upgrade: bool = False, browser: bool = False) -> None:
    import fcntl
    home.mkdir(parents=True, exist_ok=True)
    with (home / "node-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _provision_locked(home, upgrade=upgrade, browser=browser)


def _provision_locked(home: Path, *, upgrade: bool, browser: bool) -> None:
    node = home / "node"
    manifest = home / "node-packages.json"
    wanted = json.loads(manifest.read_text()) if manifest.exists() else {}
    wanted.update(packages(node))
    healthy = False
    if node.exists():
        try:
            validate(node)
            healthy = True
        except (OSError, subprocess.SubprocessError, RuntimeError):
            pass
    # Keep the old tool list even when a damaged tree must be replaced.
    write_manifest(home, wanted)
    if healthy and not upgrade and not browser:
        (node / "etc").mkdir(exist_ok=True)
        (node / "etc/npmrc").write_text(f"prefix={node}\n")
        return
    with tempfile.TemporaryDirectory(prefix=".node-stage-", dir=home) as directory:
        stage_home = Path(directory)
        candidate = stage_home / "node"
        if healthy and not upgrade:
            # Adding tools uses a staged copy of the same Node, not a Node upgrade.
            shutil.copytree(node, candidate, symlinks=True)
        else:
            helper = Path(__file__).resolve().parents[1] / "scripts/lib/node-bootstrap.sh"
            major = os.environ.get("HERMES_NODE_TARGET_MAJOR", "26")
            if healthy:
                current = subprocess.check_output([str(node / "bin/node"), "--version"], text=True).strip()
                major = str(max(int(major), int(current.lstrip("v").split(".")[0])))
            env = {**node_env(candidate), "HERMES_NODE_TARGET_MAJOR": major, "HERMES_HOME": str(stage_home), "HERMES_PRIVATE_NODE": "1",
                   "HERMES_PRIVATE_NODE_STAGE": "1", "HERMES_NODE_SKIP_LINKS": "1"}
            subprocess.run(["bash", "-c", 'source "$1" && _nb_install_bundled_node', "bash", str(helper)],
                           env=env, check=True, timeout=600)
            if wanted:
                install_packages(candidate, [f"{name}@{version}" for name, version in sorted(wanted.items())], stage_home)
        if browser:
            required = {"@askjo/camofox-browser": "^1.5.2", "agent-browser": "^0.26.0"}
            missing = [f"{name}@{version}" for name, version in required.items() if name not in wanted]
            if missing:
                install_packages(candidate, missing, stage_home)
        validate(candidate)
        installed = packages(candidate)
        (candidate / "etc").mkdir(exist_ok=True)
        (candidate / "etc/npmrc").write_text(f"prefix={node}\n")
        backup = home / ".node-previous"
        if backup.exists():
            raise RuntimeError(f"Unfinished Node replacement: inspect {backup} before retrying")
        if node.exists():
            node.rename(backup)
        try:
            candidate.rename(node)
            validate(node)
            write_manifest(home, installed)
        except BaseException:
            if node.exists():
                shutil.rmtree(node)
            if backup.exists():
                backup.rename(node)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    from hermes_constants import get_hermes_home
    provision(get_hermes_home(), upgrade=args.upgrade, browser=args.browser)


if __name__ == "__main__":
    main()
