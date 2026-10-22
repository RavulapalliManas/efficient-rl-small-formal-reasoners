"""Record the pinned Lean/mathlib pair from clean Git checkouts and a live binary."""
import json
from pathlib import Path
import subprocess

from .sources import external_output
from .storage import file_sha256


def git_revision(root):
    """Require this directory's own clean checkout, including untracked source files."""
    def git(*arguments):
        return subprocess.run(['git', '-C', str(root), *arguments], check=True, text=True,
                              capture_output=True, timeout=30).stdout.strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != root.resolve():
        raise ValueError('Expected a Git checkout at ' + str(root))
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Source checkout has changes: ' + str(root))
    return git('rev-parse', '--verify', 'HEAD')


def check_repositories(environment, config):
    """Check mathlib and every Git dependency against mathlib's Lake manifest."""
    mathlib = environment / 'mathlib'
    packages = json.loads((mathlib / 'lake-manifest.json').read_text())['packages']
    expected = [('mathlib', mathlib, config['mathlib'])]
    for package in packages:
        if package.get('type', 'git') != 'git' or not package.get('rev'):
            raise ValueError('Only revision-pinned Git dependencies are supported.')
        name = package['name']
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid dependency name.')
        expected.append((name, mathlib / '.lake/packages' / name, package['rev']))
    if len({name for name, _, _ in expected}) != len(expected):
        raise ValueError('Dependency names repeat.')
    repositories = []
    for name, root, revision in expected:
        if git_revision(root) != revision:
            raise ValueError('Live repository differs from the registered pin: ' + name)
        repositories.append(dict(name=name, revision=revision))
    return repositories


def record_identity(environment, config, output):
    """Create the verifier receipt publicly; do not download or build any runtime."""
    from .verification import LeanVerifier
    environment = environment.resolve()
    repositories = check_repositories(environment, config)
    toolchain = (environment / 'mathlib/lean-toolchain').read_text().strip()
    if toolchain != 'leanprover/lean4:' + config['lean']:
        raise ValueError('Lean toolchain differs from the configured pin.')
    output = external_output(output)
    output.mkdir(parents=True, exist_ok=False)
    receipt = dict(clean=True, toolchain=toolchain, repositories=repositories)
    path = output / 'identity.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    try:
        backend = LeanVerifier(config, environment, path)
        result = dict(status='source_identity_checked', lean_version=backend.version,
                      lean_binary_sha256=file_sha256(backend.lean), identity_sha256=file_sha256(path),
                      source_module_sha256=file_sha256(Path(__file__)),
                      scope='Source revisions, clean worktrees and Lean version; compiled caches are not attested.')
    except Exception as error:
        (output / 'manifest.json').write_text(json.dumps(dict(status='failed', error_type=type(error).__name__)) + '\n')
        raise
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
