"""Exercise the real shell download/adoption path against an offline Node fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from hermes_cli.private_node import provision

ROOT = Path(__file__).resolve().parents[2]


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def offline_node(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    profile = home / "profile"
    profile.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-installer")
    monkeypatch.setenv("HERMES_PYTHON", sys.executable)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HERMES_PRIVATE_NODE", "1")
    monkeypatch.setenv("HERMES_NODE_TARGET_MAJOR", "26")
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(home / "public-npm"))
    monkeypatch.setenv("CARGO_HOME", str(home / "public-cargo"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(home / "public-uv"))
    monkeypatch.setenv("HERMES_NPM_TARGET_RANGE", ">=12")
    fixture = tmp_path / "node-v26.8.2-fixture"
    executable(fixture / "bin/node", '#!/bin/sh\necho v26.8.2\n')
    npm = '''#!/usr/bin/env python3
import json, os, pathlib, sys
assert "OPENAI_API_KEY" not in os.environ
if '--version' in sys.argv:
 print('12.0.0'); sys.exit(0)
if os.environ.get('FAIL_NODE_REINSTALL') == '1': sys.exit(42)
assert "CARGO_HOME" not in os.environ
assert "UV_TOOL_BIN_DIR" not in os.environ
# Simulate a lifecycle script using a conventional user-local installation.
local = pathlib.Path.home() / '.local/bin'
local.mkdir(parents=True, exist_ok=True)
(local / 'lifecycle-tool').write_text('fixture')
root = pathlib.Path(os.environ['NPM_CONFIG_PREFIX'])
for spec in sys.argv[1:]:
 if spec.startswith('-') or '@' not in spec: continue
 name, version = spec.rsplit('@', 1)
 if version.startswith('^'): version = version[1:]
 package = root / 'lib/node_modules' / name
 package.mkdir(parents=True, exist_ok=True)
 (package / 'package.json').write_text(json.dumps({'name':name, 'version':version}))
'''
    executable(fixture / "bin/npm", npm)
    executable(fixture / "bin/npx", npm)
    archive = tmp_path / "node.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(fixture, arcname=fixture.name)
    fakebin = tmp_path / "transport"
    executable(fakebin / "curl", f'''#!/bin/sh
while [ "$#" -gt 0 ]; do
 if [ "$1" = -o ]; then cp '{archive}' "$2"; exit; fi
 shift
done
printf 'node-v26.8.2-linux-x64.tar.gz node-v26.8.2-darwin-arm64.tar.gz node-v26.8.2-darwin-x64.tar.gz'
''')
    monkeypatch.setenv("PATH", str(fakebin) + os.pathsep + os.environ["PATH"])
    return home, profile


@pytest.mark.parametrize("entry", ["manager", "installer", "bootstrap"])
@pytest.mark.parametrize("failure", [False, True])
def test_private_install_upgrade_and_heal_never_publish_to_user(offline_node, monkeypatch, failure, entry):
    home, profile = offline_node
    if entry == "installer":
        subprocess.run(["bash", str(ROOT / "scripts/install.sh"), "--ensure", "node"], check=True)
    elif entry == "bootstrap":
        subprocess.run(["bash", "-c", 'source "$1" && ensure_node', "bash", str(ROOT / "scripts/lib/node-bootstrap.sh")], check=True)
    else:
        provision(profile)
    node = profile / "node"
    package = node / "lib/node_modules/example/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({'name': 'example', 'version': '1.2.3'}))
    (node / "old-native-addon").write_text("must not survive upgrade")
    if failure:
        monkeypatch.setenv("FAIL_NODE_REINSTALL", "1")
        with pytest.raises(subprocess.CalledProcessError):
            provision(profile, upgrade=True)
        assert (node / "old-native-addon").is_file()
        assert json.loads(package.read_text())["version"] == "1.2.3"
        monkeypatch.delenv("FAIL_NODE_REINSTALL")
    provision(profile, upgrade=True)
    assert not (node / "old-native-addon").exists()
    assert json.loads(package.read_text())["version"] == "1.2.3"
    executable(node / "bin/node", '#!/bin/sh\nexit 1\n')
    provision(profile)
    subprocess.run([str(node / "bin/node"), "--version"], check=True)
    provision(profile, browser=True)
    assert json.loads((profile / 'node-packages.json').read_text())['example'] == '1.2.3'
    assert (node / 'etc/npmrc').read_text() == f'prefix={node}\n'
    assert (profile / 'home/.local/bin/lifecycle-tool').is_file()
    assert not (home / '.local').exists()
    assert not (home / 'public-npm').exists()
    assert not (home / '.npmrc').exists()
    assert not (home / '.bashrc').exists()


@pytest.mark.parametrize("policy", ["environment", "config"])
def test_disabled_install_blocks_browser_and_broken_node(offline_node, monkeypatch, policy):
    _, profile = offline_node
    monkeypatch.delenv('HERMES_DISABLE_LAZY_INSTALLS', raising=False)
    if policy == 'environment':
        monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    else:
        (profile / 'config.yaml').write_text('security:\n  allow_lazy_installs: false\n  private_node: true\n')
    executable(profile / 'node/bin/node', '#!/bin/sh\nexit 1\n')
    import hermes_constants
    from hermes_cli.dep_ensure import ensure_dependency
    assert not ensure_dependency('browser', interactive=False)
    assert not hermes_constants.heal_hermes_managed_node()
    assert not hermes_constants._run_node_bootstrap('_nb_install_bundled_node', timeout=5)
    assert not (profile / 'node/bin/npm').exists()
    (profile / 'node').rename(profile / 'broken-node')
    user_bin = profile.parent / 'user-node/bin'
    for name in ('node', 'npm', 'npx'):
        executable(user_bin / name, '#!/bin/sh\necho v26.8.2\n')
    monkeypatch.setenv('PATH', str(user_bin) + os.pathsep + os.environ['PATH'])
    from tools.browser_tool_install import _resolve_npx_bin, _agent_browser_candidates
    assert hermes_constants.find_node_executable('npm') is None
    assert _resolve_npx_bin() is None
    assert list(_agent_browser_candidates(str(user_bin))) == [str(profile / 'node/bin/agent-browser')]

