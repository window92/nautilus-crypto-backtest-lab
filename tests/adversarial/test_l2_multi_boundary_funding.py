"""Real persisted native multi-boundary funding regressions, without mocks."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.checker import check_evidence_directory, validate_official_funding_binding
from crypto_lab.sealing import verify_official_seal

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'runs/adversarial-remediation-002-retry-017-perpetual-benchmark-run-0ecef1839e9e'


def rows(name):
    with (RUN / name).open(newline='') as stream:
        return list(csv.DictReader(stream))


def native_case():
    result = json.loads((RUN / 'nautilus_result.json').read_bytes())
    checkpoints = result['native_funding_checkpoints']
    # Untampered persisted marks are the independent expected side of these
    # diagnostic mutations. Full catalog provenance is checked on the host.
    marks = {x['native_mark_price']['ts_event']:x['native_mark_price']
             for x in checkpoints if isinstance(x['native_mark_price'], dict)}
    return dict(
        source_events=json.loads((RUN / 'funding_source.json').read_bytes())['events'],
        mark_source_events=list(marks.values()), position_rows=rows('positions.csv'),
        checkpoints=checkpoints, funding_rows=rows('funding.csv'),
        dataset_contract=result['dataset_contract'], instrument_id='BTCUSDT-PERP.BINANCE',
    )


def mutate(case, name):
    cp = next(x for x in case['checkpoints'] if x['native_adjustments'])
    row = next(x for x in case['funding_rows'] if int(x['ts_event']) == cp['boundary_ns'])
    adjustment = cp['native_adjustments'][0]
    if name == 'MISSING':
        cp['native_adjustments'] = []
        cp['account_events_at_boundary'] = []
        cp['account_balances_after_boundary'] = copy.deepcopy(cp['account_balances_before_boundary'])
        case['funding_rows'].remove(row)
    elif name == 'DOUBLE_COUNT':
        cp['native_adjustments'].append(copy.deepcopy(adjustment))
    elif name == 'SIGN_INVALID':
        adjustment['pnl_change'] = row['pnl_change'] = str(-Decimal(row['pnl_change'].split()[0]))+' USDT'
    elif name == 'RATE_INVALID':
        cp['runtime_updates_at_boundary'][0]['rate'] = str(Decimal(cp['source_funding_rate'])+Decimal('.0001'))
    elif name == 'MARK_INVALID':
        cp['native_mark_price']['value'] = str(Decimal(cp['native_mark_price']['value'])+1)
    elif name == 'POSITION_INVALID':
        cp['open_positions'][0]['signed_qty'] = str(Decimal(cp['open_positions'][0]['signed_qty'])+1)
    elif name == 'BOUNDARY_INVALID':
        cp['boundary_ns'] += 1
    elif name == 'CURRENCY_INVALID':
        adjustment['pnl_change'] = adjustment['pnl_change'].replace('USDT','BTC')
    elif name == 'AMOUNT_INVALID':
        adjustment['pnl_change'] = row['pnl_change'] = '-1.00000000 USDT'
    elif name == 'ACCOUNT_DELTA_INVALID':
        cp['account_balances_after_boundary'][0]['total'] = cp['account_balances_before_boundary'][0]['total']
    elif name == 'UNEXPECTED_SETTLEMENT':
        flat = next(x for x in case['checkpoints'] if not x['open_positions'])
        fake = copy.deepcopy(adjustment)
        fake['ts_event'] = fake['ts_init'] = flat['boundary_ns']
        fake['reason'] = 'funding_settlement:unexpected-flat'
        flat['native_adjustments'] = [fake]
        extra = copy.deepcopy(row)
        extra['ts_event'], extra['reason'] = str(flat['boundary_ns']), fake['reason']
        case['funding_rows'].append(extra)
    else:
        raise AssertionError(name)


class RealMultiBoundaryFundingTests(unittest.TestCase):
    def setUp(self):
        self.case = native_case()
        # Detach expected marks from the checkpoints under mutation.
        self.case['mark_source_events'] = copy.deepcopy(self.case['mark_source_events'])

    def assertCodes(self, case, expected):
        before = copy.deepcopy(case)
        valid, codes, _ = validate_official_funding_binding(**case)
        self.assertEqual(valid, not expected)
        self.assertEqual(codes, tuple(expected))
        self.assertEqual(case, before, 'validator mutated native evidence')

    def test_real_native_636_boundaries_and_542_distinct_settlements_pass(self):
        self.assertEqual(len(self.case['source_events']), 636)
        self.assertEqual(len(self.case['funding_rows']), 542)
        self.assertEqual(len({x['ts_event'] for x in self.case['funding_rows']}), 542)
        self.assertCodes(self.case, [])

    def test_all_eleven_native_mutations_have_only_the_precise_code(self):
        for name in ('MISSING','DOUBLE_COUNT','SIGN_INVALID','RATE_INVALID','MARK_INVALID',
                     'POSITION_INVALID','BOUNDARY_INVALID','CURRENCY_INVALID','AMOUNT_INVALID',
                     'ACCOUNT_DELTA_INVALID','UNEXPECTED_SETTLEMENT'):
            with self.subTest(defect=name):
                case = copy.deepcopy(self.case)
                mutate(case, name)
                self.assertCodes(case, ['FUNDING_'+name])

    def test_csv_only_duplicate_and_wrong_currency_cannot_pass(self):
        case = copy.deepcopy(self.case)
        case['funding_rows'].append(copy.deepcopy(case['funding_rows'][0]))
        self.assertCodes(case, ['FUNDING_DOUBLE_COUNT'])
        case = copy.deepcopy(self.case)
        case['funding_rows'][0]['pnl_change'] = case['funding_rows'][0]['pnl_change'].replace('USDT','BTC')
        self.assertCodes(case, ['FUNDING_CURRENCY_INVALID'])

    def test_bad_mark_does_not_hide_real_duplicate_or_missing_settlement(self):
        for other, expected in [('DOUBLE_COUNT',['FUNDING_DOUBLE_COUNT','FUNDING_MARK_INVALID']),
                                ('MISSING',['FUNDING_MISSING','FUNDING_MARK_INVALID'])]:
            with self.subTest(other=other):
                case = copy.deepcopy(self.case)
                mutate(case, 'MARK_INVALID')
                mutate(case, other)
                self.assertCodes(case, expected)

    def test_csv_duplicate_with_other_amount_checks_every_row(self):
        extra = copy.deepcopy(self.case['funding_rows'][0])
        extra['pnl_change'] = '-1.00000000 USDT'
        self.case['funding_rows'].append(extra)
        self.assertCodes(self.case, ['FUNDING_DOUBLE_COUNT','FUNDING_AMOUNT_INVALID'])

    def test_missing_checkpoint_is_not_a_duplicate_settlement(self):
        cp = next(x for x in self.case['checkpoints'] if x['native_adjustments'])
        self.case['checkpoints'].remove(cp)
        self.assertCodes(self.case, ['FUNDING_BOUNDARY_INVALID'])

    def test_csv_timestamp_change_with_same_native_identity_is_boundary_failure(self):
        row = self.case['funding_rows'][0]
        row['ts_event'] = str(int(row['ts_event'])+1)
        self.assertCodes(self.case, ['FUNDING_BOUNDARY_INVALID'])

    def test_bad_csv_timestamp_does_not_hide_a_real_duplicate_or_other_missing_row(self):
        case = copy.deepcopy(self.case)
        extra = copy.deepcopy(case['funding_rows'][0])
        extra['ts_event'] = str(int(extra['ts_event'])+1)
        case['funding_rows'].append(extra)
        self.assertCodes(case, ['FUNDING_DOUBLE_COUNT','FUNDING_BOUNDARY_INVALID'])
        case = copy.deepcopy(self.case)
        case['funding_rows'][0]['ts_event'] = str(int(case['funding_rows'][0]['ts_event'])+1)
        case['funding_rows'].pop(1)
        self.assertCodes(case, ['FUNDING_MISSING','FUNDING_BOUNDARY_INVALID'])

    def test_csv_native_event_identity_must_match_without_timestamp_only_fallback(self):
        self.case['funding_rows'][0]['reason'] = 'funding_settlement:unbound-event'
        self.assertCodes(self.case, ['FUNDING_AMBIGUOUS'])


class HostRealFundingDiagnosticTests(unittest.TestCase):
    def test_real_full_checker_and_seal_reject_mark_and_position_without_false_duplicate(self):
        original = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in RUN.iterdir() if p.is_file()}
        for name in ('MARK_INVALID','POSITION_INVALID','CSV_BOUNDARY_INVALID'):
            with self.subTest(defect=name), tempfile.TemporaryDirectory() as temporary:
                copied = Path(temporary) / 'run'
                shutil.copytree(RUN, copied)
                case = native_case()
                case['mark_source_events'] = copy.deepcopy(case['mark_source_events'])
                if name == 'CSV_BOUNDARY_INVALID':
                    funding = case['funding_rows']
                    funding[0]['ts_event'] = str(int(funding[0]['ts_event'])+1)
                    with (copied/'funding.csv').open('w',newline='') as stream:
                        writer = csv.DictWriter(stream,fieldnames=list(funding[0]),lineterminator='\n')
                        writer.writeheader()
                        writer.writerows(funding)
                else:
                    mutate(case, name)
                    path = copied / 'nautilus_result.json'
                    result = json.loads(path.read_bytes())
                    result['native_funding_checkpoints'] = case['checkpoints']
                    path.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n')
                before = {p.name:p.read_bytes() for p in copied.iterdir() if p.is_file()}
                check = check_evidence_directory(copied,repository_root=ROOT,
                                                 source_revision_current_head_required=False)
                self.assertEqual(check.outcome.value, 'COMPONENT_CHECK_FAIL')
                if name == 'CSV_BOUNDARY_INVALID':
                    self.assertEqual([code for code in check.failure_codes if code.startswith('FUNDING_')],
                                     ['FUNDING_BOUNDARY_INVALID'])
                else:
                    self.assertEqual(check.failure_codes, ('FUNDING_'+name,))
                seal = verify_official_seal(copied,repository_root=ROOT,
                                           source_revision_current_head_required=False)
                self.assertNotEqual(seal.outcome.value, 'OFFICIAL_SEAL_PASS')
                self.assertEqual(before,{p.name:p.read_bytes() for p in copied.iterdir() if p.is_file()})
        self.assertEqual(original,{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in RUN.iterdir() if p.is_file()})
