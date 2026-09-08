from __future__ import annotations

import copy
import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes, canonical_sha256, sha256_file
from crypto_lab.host_acceptance import (
    ACCEPTANCE_SCHEMA, ATTESTATION_RELATIVE, ATTESTATION_SCHEMA, PHASE_LABELS,
    SOURCE_FILES, SOURCE_TREES, RUNNER, capture_acceptance_source,
    product_source_identity, verify_host_acceptance_attestation,
    build_host_acceptance_attestation,
)
from tests.helpers import initialize_product_repository

ROOT = Path(__file__).resolve().parents[2]


class AcceptanceFixture:
    """Real files and Git commits; synthetic gate records, never a real acceptance claim."""

    def __init__(self, root: Path):
        self.root = initialize_product_repository(root)
        for relative in SOURCE_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        for tree in SOURCE_TREES:
            (root / tree).mkdir(exist_ok=True)
            (root / tree / 'fixture.txt').write_text('synthetic source binding\n')
        script = root / RUNNER
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / RUNNER, script)
        (root / 'tests/__init__.py').write_text('')
        (root / 'tests/test_fixture.py').write_text(
            'import unittest\nclass Fixture(unittest.TestCase):\n'
            '    def test_one(self):\n        self.assertEqual(1+1, 2)\n')
        self.test_id = 'tests.test_fixture.Fixture.test_one'
        self.logs = []
        for _ in range(3):
            process = subprocess.run(
                [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-v'],
                cwd=root, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONPATH':''},
                check=True, capture_output=True, text=True,
            )
            self.logs.append(process.stdout + process.stderr)
        plan_dir = Path('evidence/audit/adversarial-remediation-002/execution-plans')
        for p in (ROOT / plan_dir).rglob('*.json'):
            destination = root / p.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, destination)
        pointer_path = root / plan_dir / 'ACTIVE.json'
        pointer = json.loads(pointer_path.read_bytes())
        for item in pointer['historical_plans']:
            destination = root / item['plan_ref']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / item['plan_ref'], destination)
            item['plan_identity'] = json.loads(destination.read_bytes())['plan_identity']
        self.write(pointer_path, pointer)
        plan = json.loads((root / pointer['plan_ref']).read_bytes())
        for item in plan['dataset_releases'].values():
            destination = root / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / item['path'], destination)
        self.commit('synthetic source and release fixtures')
        source = capture_acceptance_source(root)
        old = json.loads((ROOT / 'evidence/audit/adversarial-remediation-002/final-acceptance-retry-012/acceptance.json').read_bytes())
        old.update(source)
        old['schema'] = ACCEPTANCE_SCHEMA
        old['epoch'] = plan['epoch']
        old['plan_sha256'] = sha256_file(root / pointer['plan_ref'])
        old['test_ids'] = [self.test_id]
        old['full_run_test_counts'] = [1,1,1]
        qualification = ROOT / 'evidence/audit/adversarial-remediation-002/qualification-retry-019'
        runtime_path = next((qualification / 'runs/spot-primary').glob('*/runtime_identity.json'))
        old['runtime_identity'] = json.loads(runtime_path.read_bytes())
        rebuild_ref = 'evidence/audit/adversarial-remediation-002/data-rebuild-validation.json'
        rebuild = json.loads((root / rebuild_ref).read_bytes())
        data = {
            'data_rebuild_validation_ref':rebuild_ref,
            'data_rebuild_validation_sha256':sha256_file(root / rebuild_ref),
            'official_active_raw_object_count':2231,
        }
        for label in ('primary','independent'):
            data[f'official_{label}_duckdb_path'] = f'data/duckdb/adversarial-remediation-002/retry-010/{label}.duckdb'
            data[f'official_{label}_duckdb_sha256'] = rebuild['comparison'][f'{label}_file_sha256']
        for label, profile in [('spot','BINANCE_SPOT_CASH_LONG_ONLY'),
                               ('perpetual','BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING')]:
            release = plan['dataset_releases'][profile]
            for name in ('dataset_release_id','catalog_identity','raw_inventory_identity'):
                data[f'{label}_{name}'] = release[name]
            data[f'official_{label}_raw_object_count'] = release['raw_object_count']
        old['data_identities'] = data
        old['data_database_path'] = data['official_primary_duckdb_path']
        old['data_database_sha256'] = data['official_primary_duckdb_sha256']
        self.directory = root / 'evidence/acceptance-fixture'
        self.directory.mkdir()
        (self.directory / 'logs').mkdir()
        (self.directory / 'reverse-order').mkdir()
        reverse = {'status':'PASS','execution_occurrences':1,'unique_discovered_test_cases':1,
                   'errors':0,'failures':0,'skipped':0,
                   'tests':[{'ordinal':1,'test_id':self.test_id,'status':'PASS'}]}
        self.write(self.directory / 'reverse-order/result.json', reverse)
        phases = []
        for index, label in enumerate(PHASE_LABELS, 1):
            testing = index in {1,2,3,4,5,6,8,11,20}
            body = self.logs[0] if testing and index != 3 else '{"status":"PASS"}\n'
            command = ['synthetic-gate',label]
            if index == 20:
                header_command = ['FRESH_LOCKED_WHEEL_ENVIRONMENT', shlex.join(command)]
                command = [command]
            else:
                header_command = command
            text = '$ ' + shlex.join(header_command) + '\nexit_code=0\nduration_seconds=0.001000\n\n' + body
            log = self.directory / f'logs/{index:02}.log'
            log.write_text(text)
            phases.append({'ordinal':index,'label':label,'status':'PASS',
                           'command':command, 'exit_code':0,
                           'duration_seconds':0.001,'tests_run':1 if testing else None,
                           'errors':0,'failures':0,'skipped':0,
                           'log_path':log.relative_to(self.directory).as_posix(),
                           'log_sha256':sha256_file(log)})
        old['phases'] = phases
        wheel = self.directory / 'project-wheel/fixture.whl'
        wheel.parent.mkdir()
        with zipfile.ZipFile(wheel, 'w') as archive:
            for p in (root / 'src/crypto_lab').rglob('*.py'):
                archive.writestr(p.relative_to(root / 'src').as_posix(), p.read_bytes())
        old['project_wheel_filename'] = wheel.name
        old['project_wheel_sha256'] = sha256_file(wheel)
        old['supporting_artifacts'] = [
            {'path':p.relative_to(self.directory).as_posix(),'size_bytes':p.stat().st_size,'sha256':sha256_file(p)}
            for p in sorted(self.directory.rglob('*')) if p.is_file()
        ]
        old['supporting_artifact_count'] = len(old['supporting_artifacts'])
        self.acceptance = old
        self.path = self.directory / 'acceptance.json'
        self.bind()

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value) + b'\n')

    def commit(self, message):
        subprocess.run(['git','add','.'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git','-c','core.hooksPath=/dev/null','commit','--allow-empty','-m',message],
                       cwd=self.root, check=True, capture_output=True)

    def bind(self):
        material = dict(self.acceptance)
        material.pop('acceptance_identity', None)
        self.acceptance['acceptance_identity'] = canonical_sha256(material)
        self.write(self.path, self.acceptance)
        value = {
            'schema':ATTESTATION_SCHEMA,'status':'CURRENT',
            'product_source_identity':self.acceptance['product_source_identity'],
            'product_source_file_count':self.acceptance['product_source_file_count'],
            'data_identities':self.acceptance['data_identities'],
            'acceptance':{'host_acceptance_ref':self.path.relative_to(self.root).as_posix(),
                          'host_acceptance_sha256':sha256_file(self.path),
                          'host_acceptance_identity':self.acceptance['acceptance_identity'],
                          'status':'HOST_ACCEPTANCE_PASS','runner':RUNNER,
                          'runner_sha256':self.acceptance['runner_sha256']},
            'official_acceptance':True,'portable_ci_is_official_acceptance':False,
        }
        value['attestation_identity'] = canonical_sha256(value)
        self.write(self.root / ATTESTATION_RELATIVE, value)
        self.commit('synthetic attestation test inputs; not real acceptance')


class HostAcceptanceAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='l3-record-test-')
        self.addCleanup(self.temporary.cleanup)
        self.fixture = AcceptanceFixture(Path(self.temporary.name))

    def verify(self, portable=True):
        return verify_host_acceptance_attestation(self.fixture.root, portable_only=portable)

    def test_product_source_identity_is_stable_and_excludes_attestation_bytes(self):
        before = product_source_identity(self.fixture.root)
        self.assertEqual(self.verify()['status'], 'PASS')
        self.assertFalse(self.verify()['official_acceptance'])
        self.assertEqual(before, product_source_identity(self.fixture.root))

    def test_missing_attestation_fails_closed(self):
        (self.fixture.root / ATTESTATION_RELATIVE).unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.verify()

    def test_material_edit_invalidates_attestation(self):
        path = self.fixture.root / 'src/crypto_lab/sealing.py'
        path.write_bytes(path.read_bytes() + b'\n# mutation\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.verify()

    def test_discovered_missing_acceptance_false_pass_is_closed_in_both_modes(self):
        self.fixture.path.unlink()
        for portable in (True,False):
            with self.subTest(portable=portable), self.assertRaisesRegex(ValueError, 'missing'):
                self.verify(portable)

    def test_discovered_failed_acceptance_false_pass_is_closed_in_both_modes(self):
        value = dict(self.fixture.acceptance, status='FAIL')
        self.fixture.write(self.fixture.path, value)
        for portable in (True,False):
            with self.subTest(portable=portable), self.assertRaisesRegex(ValueError, 'hash differs'):
                self.verify(portable)

    def test_discovered_empty_data_and_failed_acceptance_host_false_pass_is_closed(self):
        self.fixture.acceptance.update(data_identities={}, status='FAIL')
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'not PASS'):
            self.verify(False)

    def test_empty_data_is_rejected_even_when_acceptance_claims_pass(self):
        self.fixture.acceptance['data_identities'] = {}
        self.fixture.bind()
        for portable in (True, False):
            with self.subTest(portable=portable), self.assertRaisesRegex(ValueError, 'mandatory data'):
                self.verify(portable)

    def test_rehashed_failed_record_is_rejected(self):
        self.fixture.acceptance['status'] = 'FAIL'
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'not PASS'):
            self.verify()

    def test_rehashed_missing_data_fields_are_rejected(self):
        original = copy.deepcopy(self.fixture.acceptance['data_identities'])
        for name in original:
            self.fixture.acceptance['data_identities'] = {k:v for k,v in original.items() if k != name}
            self.fixture.bind()
            with self.subTest(field=name), self.assertRaisesRegex(ValueError, 'mandatory data'):
                self.verify()

    def test_rehashed_empty_gate_or_test_set_cannot_pass(self):
        self.fixture.acceptance.update(phases=[], phase_count=0, passed_phase_count=0,
                                       full_run_test_counts=[0,0,0], test_ids=[])
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'gate set'):
            self.verify()

    def test_rehashed_test_set_substitution_is_rejected(self):
        self.fixture.acceptance['test_ids'] = ['tests.test_fixture.Fixture.test_unexecuted']
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'test set differs'):
            self.verify()

    def test_rehashed_phase_failure_skip_and_wrong_count_are_rejected(self):
        original = copy.deepcopy(self.fixture.acceptance)
        for key, value in [('exit_code',1), ('skipped',1), ('errors',1), ('tests_run',0)]:
            self.fixture.acceptance = copy.deepcopy(original)
            self.fixture.acceptance['phases'][0][key] = value
            self.fixture.bind()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()

    def test_missing_or_mutated_supporting_log_is_rejected(self):
        log = self.fixture.directory / 'logs/01.log'
        before = log.read_bytes()
        log.write_bytes(before + b'tampered\n')
        with self.assertRaisesRegex(ValueError, 'artifact hash'):
            self.verify()
        log.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.verify()

    def test_wrong_record_identity_and_uncommitted_record_are_rejected(self):
        self.fixture.acceptance['acceptance_identity'] = 'f' * 64
        self.fixture.write(self.fixture.path, self.fixture.acceptance)
        attestation_path = self.fixture.root / ATTESTATION_RELATIVE
        attestation = json.loads(attestation_path.read_bytes())
        attestation['acceptance']['host_acceptance_sha256'] = sha256_file(self.fixture.path)
        attestation.pop('attestation_identity')
        attestation['attestation_identity'] = canonical_sha256(attestation)
        self.fixture.write(attestation_path, attestation)
        with self.assertRaisesRegex(ValueError, 'acceptance identity differs'):
            self.verify()

    def test_attestation_cannot_reference_another_acceptance_identity(self):
        path = self.fixture.root / ATTESTATION_RELATIVE
        value = json.loads(path.read_bytes())
        value['acceptance']['host_acceptance_identity'] = '0' * 64
        value.pop('attestation_identity')
        value['attestation_identity'] = canonical_sha256(value)
        self.fixture.write(path, value)
        self.fixture.commit('mutated acceptance identity')
        with self.assertRaisesRegex(ValueError, 'attestation/acceptance binding differs'):
            self.verify()

    def test_uncommitted_acceptance_is_not_authority(self):
        self.fixture.acceptance['finished_at_utc'] = '2026-09-08T00:00:00Z'
        value = dict(self.fixture.acceptance)
        value.pop('acceptance_identity')
        value['acceptance_identity'] = canonical_sha256(value)
        self.fixture.write(self.fixture.path, value)
        path = self.fixture.root / ATTESTATION_RELATIVE
        attestation = json.loads(path.read_bytes())
        attestation['acceptance']['host_acceptance_identity'] = value['acceptance_identity']
        attestation['acceptance']['host_acceptance_sha256'] = sha256_file(self.fixture.path)
        attestation.pop('attestation_identity')
        attestation['attestation_identity'] = canonical_sha256(attestation)
        self.fixture.write(path, attestation)
        with self.assertRaisesRegex(ValueError, 'not committed'):
            self.verify()

    def test_legacy_schema_missing_field_and_unknown_field_fail_closed(self):
        original = copy.deepcopy(self.fixture.acceptance)
        for mode in ('legacy', 'missing', 'extra'):
            self.fixture.acceptance = copy.deepcopy(original)
            if mode == 'legacy':
                self.fixture.acceptance['schema'] = 'adversarial-remediation-002-acceptance-v1'
            elif mode == 'missing':
                self.fixture.acceptance.pop('runtime_identity')
            else:
                self.fixture.acceptance['unexpected'] = 'unbound'
            self.fixture.bind()
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'schema/mandatory fields'):
                self.verify()

    def test_runtime_and_source_tree_mismatches_fail_closed(self):
        original = copy.deepcopy(self.fixture.acceptance)
        self.fixture.acceptance['runtime_identity']['installed_payload_sha256'] = '0' * 64
        self.fixture.bind()
        with self.assertRaisesRegex(RuntimeError, 'RUNTIME_LOCK_MISMATCH'):
            self.verify()
        self.fixture.acceptance = original
        self.fixture.acceptance['source_tree'] = 'f' * 40
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'tree'):
            self.verify()

    def test_extra_supporting_file_is_rejected(self):
        (self.fixture.directory / 'unbound.txt').write_text('unbound\n')
        with self.assertRaisesRegex(ValueError, 'inventory is incomplete'):
            self.verify()

    def test_absolute_and_parent_escape_references_are_rejected(self):
        path = self.fixture.root / ATTESTATION_RELATIVE
        original = json.loads(path.read_bytes())
        for reference in (str(self.fixture.path), '../acceptance.json'):
            value = copy.deepcopy(original)
            value['acceptance']['host_acceptance_ref'] = reference
            value.pop('attestation_identity')
            value['attestation_identity'] = canonical_sha256(value)
            self.fixture.write(path, value)
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, 'unsafe'):
                self.verify()

    def test_symlink_and_absolute_acceptance_references_are_rejected(self):
        path = self.fixture.path
        target = self.fixture.root / 'preserved.json'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.verify()

    def test_host_requires_the_physical_data_but_portable_does_not(self):
        self.assertEqual(self.verify()['status'], 'PASS')
        with self.assertRaises((ValueError, RuntimeError)):
            self.verify(False)

    def test_builder_does_not_bless_missing_acceptance(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            build_host_acceptance_attestation(self.fixture.root, acceptance_ref='absent.json')

    def test_real_cli_rejects_missing_record_without_mocks(self):
        self.fixture.path.unlink()
        process = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/verify_host_acceptance_attestation.py'),
             '--repository',str(self.fixture.root),'--portable-only'],
            cwd=self.fixture.root, env={**os.environ,'PYTHONPATH':str(ROOT / 'src'),
                                       'PYTHONDONTWRITEBYTECODE':'1'},
            capture_output=True, text=True,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('missing', process.stderr)


if __name__ == '__main__':
    unittest.main()
