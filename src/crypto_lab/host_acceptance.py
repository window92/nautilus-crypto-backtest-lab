"""Verify Host Acceptance records, their executed gates, and source/data bindings."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import stat
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from crypto_lab.config import RuntimeLock
from crypto_lab.data import DatasetRelease, verify_dataset_raw_inventory
from crypto_lab.execution_plan import load_active_execution_plan
from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_json_bytes, canonical_sha256, sha256_file
from crypto_lab.runtime import validate_persisted_runtime_identity, verify_runtime_lock


ATTESTATION_SCHEMA = 'host-acceptance-attestation-v2'
ACCEPTANCE_SCHEMA = 'adversarial-remediation-002-acceptance-v2'
ATTESTATION_RELATIVE = Path('evidence/audit/adversarial-remediation-002/host-acceptance/attestation.json')
RUNNER = 'scripts/run_adversarial_remediation_002_acceptance.py'
SOURCE_FILES = (
    'SSOT.md', 'AGENTS.md', 'CHANGELOG.md', 'pyproject.toml', 'runtime.lock.json',
    'runtime-bootstrap-authority.json', 'requirements.lock.txt',
    'requirements.data.lock.txt', 'data-tool.lock.json', '.github/workflows/ci.yml',
    'evidence/audit/adversarial-remediation-002/data-rebuild-validation.json',
)
SOURCE_TREES = ('src', 'tests', 'scripts', 'schemas', 'configs')
RELEASE_DIR = Path('data/releases')
PHASE_LABELS = (
    'FULL_TEST_DISCOVERY', 'INDEPENDENT_FRESH_PROCESS_DISCOVERY', 'REVERSE_TEST_ORDER',
    'R2_TARGETED_REGRESSIONS', 'R2_MUTATION_NEGATIVE_CONTROLS', 'LEGACY_CONTRACT_REGRESSIONS',
    'RUNTIME_INSTALLED_PAYLOAD_VERIFIER', 'RUNTIME_STARTUP_INJECTION_NEGATIVES',
    'HISTORICAL_EXECUTABLE_VALIDATORS', 'CURRENT_M3_QUALIFICATION_VALIDATION',
    'JOURNAL_HOLDOUT_MULTIPROCESS_DURABILITY', 'RAW_OBJECT_AND_PUBLISHER_CHECKSUM_VALIDATION',
    'DATASET_RELEASE_DATABASE_CATALOG_SEMANTIC_IDENTITY', 'R2_SIX_RUNS_AND_REPLAYS',
    'COMPILEALL', 'PROJECT_PIP_CHECK', 'DATA_PIP_CHECK', 'GIT_DIFF_CHECK',
    'GIT_WORKTREE_CLEAN', 'FRESH_LOCKED_WHEEL_ENVIRONMENT',
)
DATA_FIELDS = {
    'data_rebuild_validation_ref', 'data_rebuild_validation_sha256',
    'official_primary_duckdb_path', 'official_primary_duckdb_sha256',
    'official_independent_duckdb_path', 'official_independent_duckdb_sha256',
    'official_active_raw_object_count', 'official_spot_raw_object_count',
    'official_perpetual_raw_object_count', 'spot_dataset_release_id',
    'perpetual_dataset_release_id', 'spot_catalog_identity', 'perpetual_catalog_identity',
    'spot_raw_inventory_identity', 'perpetual_raw_inventory_identity',
}
ACCEPTANCE_FIELDS = {
    'schema', 'epoch', 'status', 'started_at_utc', 'finished_at_utc', 'source_commit',
    'source_tree', 'repository_identity', 'product_source_identity', 'product_source_file_count',
    'runner_sha256', 'test_source_identity', 'test_ids', 'runtime_identity', 'data_identities',
    'plan_sha256', 'data_database_path', 'data_database_sha256', 'nautilus_wheel_filename',
    'nautilus_wheel_sha256', 'project_wheel_filename', 'project_wheel_sha256',
    'ssot_sha256', 'runtime_lock_sha256', 'dependency_lock_sha256', 'phase_count',
    'passed_phase_count', 'failed_phase_count', 'full_run_test_counts', 'phases',
    'supporting_artifact_count', 'supporting_artifacts', 'final_holdout_used',
    'live_trading_used', 'profitability_claim_authorized', 'network_used',
    'isolated_subprocess_home_removed', 'pycache_removed', 'acceptance_identity',
}


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError('EVIDENCE_INCOMPLETE: ' + detail)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _safe_path(base: Path, relative: Any) -> Path:
    _require(isinstance(relative, str) and bool(relative), 'file reference is missing')
    path = Path(relative)
    _require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == relative,
             'unsafe file reference')
    result = base
    for part in path.parts:
        result /= part
        _require(not result.is_symlink(), 'symlink in file reference: ' + relative)
    return result


def _regular(path: Path) -> bytes:
    _require(path.is_absolute(), 'file path must be absolute')
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        _require(not cursor.is_symlink(), 'symlink in file path')
    _require(path.is_file(), 'required file is missing: ' + str(path))
    return path.read_bytes()


def _json(path: Path) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    payload = _regular(path)
    value = json.loads(payload, object_pairs_hook=pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    _require(isinstance(value, dict), 'JSON object required')
    _require(payload == canonical_json_bytes(value) + b'\n', 'noncanonical JSON: ' + str(path))
    return value


def _git(repository: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(
        ['git', '--no-replace-objects', *args], cwd=repository, input=input,
        env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'GIT_CONFIG_NOSYSTEM':'1'},
        capture_output=True, check=True,
    ).stdout


def _is_source(relative: str) -> bool:
    path = Path(relative)
    return relative in SOURCE_FILES or (
        path.parts[0] in SOURCE_TREES and '__pycache__' not in path.parts and path.suffix != '.pyc'
    ) or (path.parent == RELEASE_DIR and path.suffix == '.json')


def product_source_inventory(repository_root: Path) -> list[dict[str, Any]]:
    root = require_repository_root(repository_root)
    paths = [_safe_path(root, name) for name in SOURCE_FILES]
    for name in (*SOURCE_TREES, RELEASE_DIR.as_posix()):
        base = _safe_path(root, name)
        _require(base.is_dir(), 'required source directory missing: ' + name)
        for path in base.rglob('*'):
            relative = path.relative_to(root).as_posix()
            _require(not path.is_symlink(), 'source symlink: ' + relative)
            if path.is_file() and _is_source(relative):
                paths.append(path)
    result = []
    for path in sorted(set(paths), key=lambda p:p.relative_to(root).as_posix()):
        payload = _regular(path)
        result.append({'path':path.relative_to(root).as_posix(),
                       'sha256':hashlib.sha256(payload).hexdigest(),
                       'byte_size':len(payload),
                       'mode':'100755' if path.stat().st_mode & 0o111 else '100644'})
    return result


def _commit_source_inventory(root: Path, commit: str) -> list[dict[str, Any]]:
    records = []
    for raw in _git(root, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if not raw:
            continue
        metadata, name = raw.split(b'\t', 1)
        relative = name.decode()
        if _is_source(relative):
            mode, kind, oid = metadata.decode().split()
            _require(kind == 'blob' and mode in {'100644','100755'}, 'invalid Git source mode')
            records.append((relative, mode, oid))
    batch = _git(root, 'cat-file', '--batch', input=''.join(x[2]+'\n' for x in records).encode())
    offset = 0
    result = []
    for relative, mode, oid in records:
        end = batch.index(b'\n', offset)
        found, kind, size = batch[offset:end].decode().split()
        _require(found == oid and kind == 'blob', 'Git source object mismatch')
        offset = end + 1
        payload = batch[offset:offset+int(size)]
        offset += int(size) + 1
        result.append({'path':relative, 'mode':mode, 'byte_size':len(payload),
                       'sha256':hashlib.sha256(payload).hexdigest()})
    return sorted(result, key=lambda x:x['path'])


def product_source_identity(repository_root: Path) -> str:
    return canonical_sha256({'files':product_source_inventory(repository_root)})


def capture_acceptance_source(repository_root: Path) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    _require(not _git(root, 'status', '--porcelain=v1', '--untracked-files=all'),
             'acceptance requires a clean worktree')
    commit = _git(root, 'rev-parse', 'HEAD').decode().strip()
    inventory = product_source_inventory(root)
    _require(inventory == _commit_source_inventory(root, commit), 'source differs from Git commit')
    return {
        'source_commit':commit, 'source_tree':_git(root, 'rev-parse', 'HEAD^{tree}').decode().strip(),
        'repository_identity':_git(root, 'remote', 'get-url', 'origin').decode().strip(),
        'product_source_identity':canonical_sha256({'files':inventory}),
        'product_source_file_count':len(inventory), 'runner_sha256':sha256_file(root / RUNNER),
        'test_source_identity':canonical_sha256([x for x in inventory if x['path'].startswith('tests/')]),
    }


def collect_data_identities(repository_root: Path, *, primary_database: Path,
                            independent_database: Path) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    plan = load_active_execution_plan(root)['plan']
    binding = plan['data_rebuild_validation']
    result = {'data_rebuild_validation_ref':binding['path'],
              'data_rebuild_validation_sha256':binding['sha256']}
    for label, path in [('primary',primary_database), ('independent',independent_database)]:
        _require(path.is_absolute(), 'database path must be explicit and absolute')
        relative = path.relative_to(root).as_posix()
        _require(_safe_path(root, relative).is_file(), 'database is missing')
        result[f'official_{label}_duckdb_path'] = relative
        result[f'official_{label}_duckdb_sha256'] = sha256_file(path)
    for label, profile in [('spot','BINANCE_SPOT_CASH_LONG_ONLY'),
                           ('perpetual','BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING')]:
        release = plan['dataset_releases'][profile]
        for key in ('dataset_release_id','catalog_identity','raw_inventory_identity'):
            result[f'{label}_{key}'] = release[key]
        result[f'official_{label}_raw_object_count'] = release['raw_object_count']
    result['official_active_raw_object_count'] = sum(result[f'official_{x}_raw_object_count']
                                                    for x in ('spot','perpetual'))
    _verify_data(root, result, portable_only=False)
    return result


def _verify_data(root: Path, data: Any, *, portable_only: bool) -> None:
    _require(isinstance(data, dict) and set(data) == DATA_FIELDS, 'mandatory data identities differ')
    _require(data['official_spot_raw_object_count'] == 774
             and data['official_perpetual_raw_object_count'] == 1457
             and data['official_active_raw_object_count'] == 2231, 'Official Raw counts differ')
    for key, value in data.items():
        if key.endswith(('_sha256','_identity','_id')):
            _require(_sha(value), 'invalid data identity: ' + key)
        elif key.endswith('_count'):
            _require(type(value) is int and value > 0, 'invalid data count: ' + key)
        else:
            _safe_path(root, value)
    plan = load_active_execution_plan(root)['plan']
    rebuild_path = _safe_path(root, data['data_rebuild_validation_ref'])
    _require(sha256_file(rebuild_path) == data['data_rebuild_validation_sha256'], 'rebuild hash differs')
    _require(plan['data_rebuild_validation']['path'] == data['data_rebuild_validation_ref']
             and plan['data_rebuild_validation']['sha256'] == data['data_rebuild_validation_sha256'],
             'rebuild identity differs from ACTIVE plan')
    rebuild = _json(rebuild_path)
    _require(rebuild['status'] == 'PASS', 'rebuild did not pass')
    raw_union = set()
    for label, profile in [('spot','BINANCE_SPOT_CASH_LONG_ONLY'),
                           ('perpetual','BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING')]:
        binding = plan['dataset_releases'][profile]
        path = _safe_path(root, binding['path'])
        payload = _regular(path)
        _require(sha256_file(path) == binding['sha256'], 'DatasetRelease file changed')
        release = DatasetRelease.from_json_bytes(payload)
        _require(release.has_full_raw_inventory and release.market_profile.value == profile,
                 'invalid DatasetRelease profile or inventory')
        for key in ('dataset_release_id','catalog_identity'):
            _require(getattr(release, key) == data[f'{label}_{key}'] == binding[key],
                     'DatasetRelease identity mismatch: ' + key)
        inventory = release.raw_inventory
        _require(inventory.raw_inventory_identity == data[f'{label}_raw_inventory_identity']
                 == binding['raw_inventory_identity'], 'Raw inventory identity differs')
        _require(inventory.raw_object_count == data[f'official_{label}_raw_object_count']
                 == binding['raw_object_count'], 'Raw inventory count differs')
        raw_union.update(item.raw_object_sha256 for item in inventory.raw_objects)
        material = rebuild['materialized_release_artifacts'][profile]
        _require(material['dataset_release_id'] == release.dataset_release_id
                 and material['catalog_identity'] == release.catalog_identity
                 and material['raw_inventory_identity'] == inventory.raw_inventory_identity,
                 'rebuild release binding differs')
        if not portable_only:
            verify_dataset_raw_inventory(release, root / 'data')
            catalog = _safe_path(root, 'data/catalog/' + release.catalog_identity)
            _require(catalog.is_dir(), 'catalog is missing')
            physical = []
            for file in sorted(catalog.rglob('*')):
                _require(not file.is_symlink(), 'catalog contains a symlink')
                if file.is_file():
                    physical.append({'path':file.relative_to(catalog).as_posix(),
                                     'size_bytes':file.stat().st_size, 'sha256':sha256_file(file)})
            _require(bool(physical) and canonical_sha256(physical) ==
                     material['catalog']['physical_inventory_identity'], 'physical catalog identity differs')
    _require(len(raw_union) == data['official_active_raw_object_count'], 'active Raw union differs')
    _require(data['official_primary_duckdb_path'] != data['official_independent_duckdb_path'],
             'independent database must be distinct')
    for label in ('primary','independent'):
        _require(data[f'official_{label}_duckdb_sha256'] == rebuild['comparison'][f'{label}_file_sha256'],
                 'database/rebuild identity differs')
        if not portable_only:
            path = _safe_path(root, data[f'official_{label}_duckdb_path'])
            _require(path.is_file(), 'official database is missing')
            _require(sha256_file(path) == data[f'official_{label}_duckdb_sha256'], 'official database changed')


def executed_test_ids(text: str) -> list[str]:
    return re.findall(r'^test[^\n]*? \((tests\.[^\s()]+)\)', text, flags=re.MULTILINE)


def verify_project_wheel(repository_root: Path, wheel_path: Path) -> None:
    root = require_repository_root(repository_root)
    _regular(wheel_path)
    expected = {p.relative_to(root / 'src').as_posix():p.read_bytes()
                for p in (root / 'src/crypto_lab').rglob('*.py')}
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        _require(len(names) == len(set(names)), 'duplicate Wheel member')
        for item in archive.infolist():
            name = Path(item.filename)
            metadata = (len(name.parts) == 2 and name.parts[0].endswith('.dist-info')
                        and name.name in {'METADATA','WHEEL','RECORD','top_level.txt'})
            _require(not name.is_absolute() and '..' not in name.parts
                     and not stat.S_ISLNK(item.external_attr >> 16)
                     and (item.filename in expected or metadata), 'unapproved project Wheel member')
        actual = {name:archive.read(name) for name in names
                  if name.startswith('crypto_lab/') and name.endswith('.py')}
    _require(actual == expected and bool(actual), 'project Wheel source differs')


def _verify_phases(record: dict[str, Any], directory: Path) -> None:
    phases = record['phases']
    _require(isinstance(phases, list) and [x.get('label') for x in phases] == list(PHASE_LABELS),
             'exact acceptance gate set/order differs')
    _require(record['phase_count'] == record['passed_phase_count'] == len(PHASE_LABELS)
             and type(record['failed_phase_count']) is int and record['failed_phase_count'] == 0,
             'acceptance phase counts differ')
    support = record['supporting_artifacts']
    _require(isinstance(support, list) and len(support) == record['supporting_artifact_count'],
             'supporting artifact inventory differs')
    seen = set()
    for item in support:
        _require(isinstance(item, dict) and set(item) == {'path','sha256','size_bytes'},
                 'supporting artifact fields differ')
        _require(item['path'] not in seen and item['path'] != 'acceptance.json', 'duplicate/cyclic support')
        seen.add(item['path'])
        path = _safe_path(directory, item['path'])
        payload = _regular(path)
        _require(_sha(item['sha256']) and sha256_file(path) == item['sha256']
                 and type(item['size_bytes']) is int and len(payload) == item['size_bytes'],
                 'supporting artifact hash/size differs: ' + item['path'])
    actual_files = set()
    for path in directory.rglob('*'):
        _require(not path.is_symlink(), 'supporting artifact symlink')
        if path.is_file():
            actual_files.add(path.relative_to(directory).as_posix())
    _require(actual_files == seen | {'acceptance.json'}, 'supporting artifact inventory is incomplete')
    tests = record['test_ids']
    _require(isinstance(tests, list) and bool(tests) and all(isinstance(x,str) for x in tests)
             and tests == sorted(set(tests)), 'exact test inventory invalid')
    _require(record['full_run_test_counts'] == [len(tests)] * 3
             and all(type(x) is int for x in record['full_run_test_counts']),
             'full/fresh/reverse test counts differ')
    for number, phase in enumerate(phases, 1):
        _require(phase.get('ordinal') == number and phase.get('status') == 'PASS'
                 and type(phase.get('exit_code')) is int and phase['exit_code'] == 0,
                 'acceptance phase failed')
        _require(all(type(phase.get(k)) is int and phase[k] == 0
                     for k in ('errors','failures','skipped')), 'failed/skipped test in acceptance')
        _require(type(phase.get('duration_seconds')) in (int,float)
                 and phase['duration_seconds'] >= 0, 'phase duration invalid')
        _require(isinstance(phase.get('command'), list) and bool(phase['command']), 'phase command missing')
        _require(phase.get('log_path') in seen, 'phase log is not bound')
        path = _safe_path(directory, phase['log_path'])
        _require(sha256_file(path) == phase.get('log_sha256'), 'phase log hash differs')
        log = path.read_text()
        command = phase['command']
        if phase['label'] == 'FRESH_LOCKED_WHEEL_ENVIRONMENT':
            command = ['FRESH_LOCKED_WHEEL_ENVIRONMENT', *map(shlex.join, command)]
        _require(log.startswith('$ ' + shlex.join(command) + '\nexit_code=0\n'
                                + f"duration_seconds={phase['duration_seconds']:.6f}\n"),
                 'phase command or duration differs from log')
        _require('\nexit_code=0\n' in log and not re.search(
            r'^(FAIL:|ERROR:|FAILED\s*\()|expected failure|unexpected success|skipped=', log, re.MULTILINE),
            'phase log reports failure or skipped test')
        if number in (1,2):
            ids = executed_test_ids(log)
            _require(sorted(ids) == tests and type(phase['tests_run']) is int
                     and phase['tests_run'] == len(tests),
                     'executed full/fresh test set differs')
        elif number == 3:
            reverse_path = 'reverse-order/result.json'
            _require(reverse_path in seen, 'reverse result is not bound')
            reverse = json.loads(_regular(_safe_path(directory, reverse_path)))
            _require(reverse.get('status') == 'PASS' and reverse.get('execution_occurrences') == len(tests)
                     and reverse.get('unique_discovered_test_cases') == len(tests)
                     and [x['test_id'] for x in reverse['tests']] == sorted(tests, reverse=True)
                     and all(x['status'] == 'PASS' for x in reverse['tests'])
                     and all(reverse.get(k) == 0 for k in ('errors','failures','skipped')),
                     'reverse executed test set differs')
        elif phase['label'] in {PHASE_LABELS[i] for i in (3,4,5,7,10,19)}:
            _require(type(phase.get('tests_run')) is int and phase['tests_run'] > 0,
                     'required test phase is empty')
            ids = executed_test_ids(log)
            _require(len(ids) == len(set(ids)) == phase['tests_run'] and set(ids) <= set(tests),
                     'targeted executed tests differ')


def verify_acceptance_record(repository_root: Path, acceptance_path: Path, *,
                             portable_only: bool = False) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    record = _json(acceptance_path)
    _require(set(record) == ACCEPTANCE_FIELDS and record['schema'] == ACCEPTANCE_SCHEMA,
             'acceptance record schema/mandatory fields differ')
    material = dict(record)
    identity = material.pop('acceptance_identity')
    _require(_sha(identity) and canonical_sha256(material) == identity, 'acceptance identity differs')
    _require(record['status'] == 'PASS', 'acceptance record is not PASS')
    _require(all(record[k] is False for k in ('final_holdout_used','live_trading_used',
             'profitability_claim_authorized','network_used')), 'acceptance exceeded authorized scope')
    for name in ('started_at_utc','finished_at_utc'):
        _require(isinstance(record[name],str) and record[name].endswith('Z'), 'acceptance time is not UTC')
    _require(datetime.fromisoformat(record['started_at_utc']) <=
             datetime.fromisoformat(record['finished_at_utc']), 'acceptance times reversed')
    inventory = product_source_inventory(root)
    commit = record['source_commit']
    _require(isinstance(commit,str) and re.fullmatch(r'[0-9a-f]{40}', commit) is not None,
             'acceptance source commit invalid')
    _require(isinstance(record['source_tree'],str)
             and re.fullmatch(r'[0-9a-f]{40}', record['source_tree']) is not None
             and isinstance(record['repository_identity'],str) and bool(record['repository_identity']),
             'acceptance repository/tree identity invalid')
    require_repository_root(root, expected_repository_identity=record['repository_identity'],
                            expected_git_commit=commit, expected_git_tree=record['source_tree'])
    _require(inventory == _commit_source_inventory(root, commit), 'acceptance source is stale')
    _require(record['product_source_identity'] == canonical_sha256({'files':inventory})
             and record['product_source_file_count'] == len(inventory), 'acceptance product identity differs')
    _require(record['test_source_identity'] == canonical_sha256(
        [x for x in inventory if x['path'].startswith('tests/')]), 'test source identity differs')
    for key, relative in [('runner_sha256',RUNNER), ('ssot_sha256','SSOT.md'),
                          ('runtime_lock_sha256','runtime.lock.json'),
                          ('dependency_lock_sha256','requirements.lock.txt')]:
        _require(record[key] == sha256_file(root / relative), 'acceptance source binding differs: ' + key)
    selected = load_active_execution_plan(root)
    _require(record['epoch'] == selected['plan']['epoch']
             and record['plan_sha256'] == sha256_file(root / selected['plan_path']), 'acceptance plan differs')
    _verify_phases(record, acceptance_path.parent)
    _verify_data(root, record['data_identities'], portable_only=portable_only)
    data = record['data_identities']
    _require(record['data_database_sha256'] == data['official_primary_duckdb_sha256']
             and record['data_database_path'] == data['official_primary_duckdb_path'], 'acceptance database differs')
    lock = RuntimeLock.from_json_bytes((root / 'runtime.lock.json').read_bytes())
    _require(record['nautilus_wheel_filename'] == lock.nautilus_wheel_filename
             and record['nautilus_wheel_sha256'] == lock.nautilus_wheel_sha256
             and _sha(record['project_wheel_sha256'])
             and isinstance(record['project_wheel_filename'],str)
             and record['project_wheel_filename'].endswith('.whl'), 'acceptance wheel identity differs')
    wheel_ref = 'project-wheel/' + record['project_wheel_filename']
    wheel = _safe_path(acceptance_path.parent, wheel_ref)
    _require(wheel_ref in {x['path'] for x in record['supporting_artifacts']}
             and sha256_file(wheel) == record['project_wheel_sha256'], 'project Wheel is not bound')
    verify_project_wheel(root, wheel)
    validate_persisted_runtime_identity(lock, record['runtime_identity'])
    if not portable_only:
        verify_runtime_lock(lock, dependency_lock_path=root / 'requirements.lock.txt')
    return record


def build_host_acceptance_attestation(repository_root: Path, *, acceptance_ref: str) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    path = _safe_path(root, acceptance_ref)
    record = verify_acceptance_record(root, path)
    _require(_git(root, 'show', 'HEAD:' + acceptance_ref) == _regular(path),
             'acceptance record is not committed')
    material = {
        'schema':ATTESTATION_SCHEMA, 'status':'CURRENT',
        'product_source_identity':record['product_source_identity'],
        'product_source_file_count':record['product_source_file_count'],
        'data_identities':record['data_identities'],
        'acceptance':{'host_acceptance_ref':acceptance_ref,
                      'host_acceptance_sha256':sha256_file(path),
                      'host_acceptance_identity':record['acceptance_identity'],
                      'status':'HOST_ACCEPTANCE_PASS', 'runner':RUNNER,
                      'runner_sha256':record['runner_sha256']},
        'official_acceptance':True, 'portable_ci_is_official_acceptance':False,
    }
    return {**material, 'attestation_identity':canonical_sha256(material)}


def load_attestation(repository_root: Path) -> dict[str, Any]:
    return _json(_safe_path(require_repository_root(repository_root), ATTESTATION_RELATIVE.as_posix()))


def verify_host_acceptance_attestation(repository_root: Path, *, portable_only: bool = False) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    value = load_attestation(root)
    _require(set(value) == {'schema','status','product_source_identity','product_source_file_count',
                           'data_identities','acceptance','official_acceptance',
                           'portable_ci_is_official_acceptance','attestation_identity'},
             'attestation mandatory fields differ')
    material = dict(value)
    identity = material.pop('attestation_identity')
    _require(value['schema'] == ATTESTATION_SCHEMA and value['status'] == 'CURRENT'
             and value['official_acceptance'] is True and value['portable_ci_is_official_acceptance'] is False
             and _sha(identity) and identity == canonical_sha256(material), 'attestation contract differs')
    binding = value['acceptance']
    _require(isinstance(binding,dict) and set(binding) == {'host_acceptance_ref','host_acceptance_sha256',
             'host_acceptance_identity','status','runner','runner_sha256'}, 'acceptance binding fields differ')
    _require(binding['status'] == 'HOST_ACCEPTANCE_PASS' and binding['runner'] == RUNNER,
             'attestation acceptance status/runner differs')
    path = _safe_path(root, binding['host_acceptance_ref'])
    payload = _regular(path)
    _require(_sha(binding['host_acceptance_sha256']) and sha256_file(path) == binding['host_acceptance_sha256'],
             'referenced acceptance hash differs')
    record = verify_acceptance_record(root, path, portable_only=portable_only)
    _require(record['acceptance_identity'] == binding['host_acceptance_identity']
             and record['runner_sha256'] == binding['runner_sha256']
             and record['product_source_identity'] == value['product_source_identity']
             and record['product_source_file_count'] == value['product_source_file_count']
             and record['data_identities'] == value['data_identities'], 'attestation/acceptance binding differs')
    _require(_git(root, 'show', 'HEAD:' + binding['host_acceptance_ref']) == payload,
             'referenced acceptance is not committed')
    _require(_git(root, 'show', 'HEAD:' + ATTESTATION_RELATIVE.as_posix()) ==
             _regular(root / ATTESTATION_RELATIVE), 'attestation is not committed')
    return {'status':'PASS', 'portable_only':portable_only,
            'product_source_identity':record['product_source_identity'],
            'attestation_identity':identity, 'host_acceptance_identity':record['acceptance_identity'],
            'official_acceptance':not portable_only, 'portable_ci_is_official_acceptance':False}
