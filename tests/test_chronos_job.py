import json
import msvcrt
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from perpetual_engine import chronos_job
from perpetual_engine.dashboard_service import DashboardPaths
from perpetual_engine.io import canonical_json


class EvaluationJobTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.paths = DashboardPaths.from_root(self.root)
        self.request = self.paths.chronos_output_root / 'requests' / ('a' * 64) / 'request.json'
        self.request.parent.mkdir(parents=True)
        self.request.write_bytes(canonical_json({'frozen': True}))
        self.result = self.paths.chronos_output_root / 'evaluations' / ('a' * 64)
        self.result.mkdir(parents=True)
        self.prepare = self.enterContext(patch.object(chronos_job, 'prepare_direct_evaluation_request', return_value=self.request))
        self.launcher = Mock()

    def start(self, launcher=None):
        return chronos_job.start_evaluation_job(self.paths, object(), launcher=launcher or self.launcher)

    def worker_args(self, call=-1):
        command = self.launcher.call_args_list[call].args[0]
        return tuple(Path(command[command.index(flag) + 1]) for flag in ('--project-root', '--request', '--state', '--lock'))

    def write_job(self, **changes):
        payload = json.loads(self.paths.chronos_job.read_bytes())
        payload.update(changes)
        self.paths.chronos_job.write_bytes(canonical_json(payload))

    def test_evaluation_job_launch_is_hidden_detached_and_duplicate_clicks_reuse_state(self):
        first = self.start()
        self.assertEqual(self.start(), first)
        self.assertEqual(self.launcher.call_count, 1)
        command = self.launcher.call_args.args[0]
        self.assertEqual(command[:5], [sys.executable, '-m', 'perpetual_engine', 'chronos', 'dashboard-evaluate-worker'])
        self.assertEqual(self.worker_args()[:3], (self.root, self.request, self.paths.chronos_job))
        options = self.launcher.call_args.kwargs
        self.assertEqual(options['creationflags'], subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertEqual(options['stderr'], subprocess.STDOUT)
        self.assertTrue(options['close_fds'])
        self.assertEqual(options['cwd'], self.root)
        self.assertTrue(options['stdout'].closed)
        self.assertEqual(first.state, 'STARTING')
        self.assertEqual(first.progress, 0)
        self.assertIsNone(first.result_path)
        raw = self.paths.chronos_job.read_bytes()
        self.assertEqual(raw, canonical_json(json.loads(raw)))
        self.assertFalse(Path(json.loads(raw)['request_path']).is_absolute())

    def test_evaluation_job_concurrent_starts_launch_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: self.start(), range(2)))
        self.assertEqual(jobs[0].job_id, jobs[1].job_id)
        self.assertEqual(self.launcher.call_count, 1)

    def test_evaluation_job_grace_expiry_dead_running_and_retry_identity(self):
        first = self.start()
        self.assertEqual(chronos_job.read_evaluation_job(self.paths, now=first.started_at + timedelta(seconds=29)).state, 'STARTING')
        self.assertEqual(chronos_job.read_evaluation_job(self.paths, now=first.started_at + timedelta(seconds=30)).state, 'INTERRUPTED')
        retry = self.start()
        self.assertNotEqual(first.job_id, retry.job_id)
        self.write_job(state='RUNNING')
        self.assertEqual(chronos_job.read_evaluation_job(self.paths).state, 'INTERRUPTED')

    def test_evaluation_job_locked_starting_and_running_remain_active(self):
        first = self.start()
        with self.worker_args()[3].open('r+b') as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                for state in ('STARTING', 'RUNNING'):
                    self.write_job(state=state)
                    self.assertEqual(chronos_job.read_evaluation_job(self.paths, now=first.started_at + timedelta(seconds=31)).state, state)
                    self.assertEqual(self.start().job_id, first.job_id)
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def test_evaluation_job_worker_progress_success_and_lock_through_terminal_write(self):
        first = self.start()
        original_write = chronos_job._atomic_write
        seen = []

        def observe_write(path, payload):
            if json.loads(payload)['state'] == 'SUCCEEDED':
                with self.worker_args()[3].open('r+b') as handle, self.assertRaises(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            original_write(path, payload)

        def evaluate(root, request, *, predictor, progress):
            self.assertEqual((root, request), (self.root, self.request))
            before = chronos_job.read_evaluation_job(self.paths)
            progress(0.4, 'Valutazione variabili Chronos')
            current = chronos_job.read_evaluation_job(self.paths)
            self.assertEqual(current, replace(before, progress=0.4, message='Valutazione variabili Chronos', updated_at=current.updated_at))
            self.assertEqual(self.start().job_id, first.job_id)
            seen.append(current)
            return self.result

        with patch.object(chronos_job, 'evaluate_direct_request', side_effect=evaluate), patch.object(chronos_job, '_atomic_write', side_effect=observe_write):
            self.assertEqual(chronos_job.run_evaluation_worker(*self.worker_args()), 0)
        self.assertEqual(len(seen), 1)
        done = chronos_job.read_evaluation_job(self.paths)
        self.assertEqual((done.state, done.progress, done.result_path), ('SUCCEEDED', 1, self.result))
        self.assertNotEqual(self.start().job_id, first.job_id)

    def test_evaluation_job_failure_is_sanitized_and_launcher_handle_closes_on_failure(self):
        first = self.start()
        with patch.object(chronos_job, 'evaluate_direct_request', side_effect=RuntimeError('Traceback token=secret /private')):
            self.assertEqual(chronos_job.run_evaluation_worker(*self.worker_args()), 2)
        failed = chronos_job.read_evaluation_job(self.paths)
        self.assertEqual(failed.state, 'FAILED')
        self.assertNotIn('secret', failed.message)
        self.assertNotIn('Traceback', failed.message)
        launcher = Mock(side_effect=OSError('secret'))
        retry = self.start(launcher)
        self.assertNotEqual(first.job_id, retry.job_id)
        self.assertEqual(retry.state, 'FAILED')
        self.assertTrue(launcher.call_args.kwargs['stdout'].closed)
        self.assertNotIn('secret', self.paths.chronos_job.read_text())
        self.assertEqual(self.start().state, 'STARTING')

    def test_evaluation_job_late_worker_cannot_run_or_overwrite_retry(self):
        first = self.start()
        old_args = self.worker_args()
        chronos_job.read_evaluation_job(self.paths, now=first.started_at + timedelta(seconds=31))
        retry = self.start()
        with patch.object(chronos_job, 'evaluate_direct_request', side_effect=AssertionError('old worker must not evaluate')):
            self.assertEqual(chronos_job.run_evaluation_worker(*old_args), 2)
        self.assertEqual(chronos_job.read_evaluation_job(self.paths), retry)

    def test_evaluation_job_stale_callback_and_completion_cannot_overwrite_newer_state(self):
        self.start()
        callbacks = []

        def evaluate(*args, progress, **kwargs):
            callbacks.append(progress)
            self.write_job(job_id='b' * 32, state='STARTING', progress=0)
            progress(0.7, 'token=secret')
            return self.result

        with patch.object(chronos_job, 'evaluate_direct_request', side_effect=evaluate):
            self.assertEqual(chronos_job.run_evaluation_worker(*self.worker_args()), 2)
        before = self.paths.chronos_job.read_bytes()
        callbacks[0](1, 'late')
        self.assertEqual(self.paths.chronos_job.read_bytes(), before)

    def test_evaluation_job_rejects_invalid_paths_state_and_result(self):
        self.assertIsNone(chronos_job.read_evaluation_job(self.paths))
        with self.assertRaises(ValueError):
            chronos_job.start_evaluation_job(replace(self.paths, chronos_job=self.root / 'other.json'), object(), launcher=self.launcher)
        self.start()
        args = self.worker_args()
        for index in (1, 2, 3):
            invalid = list(args)
            invalid[index] = self.root / 'wrong'
            with self.subTest(index=index), self.assertRaises(ValueError):
                chronos_job.run_evaluation_worker(*invalid)
        original = self.paths.chronos_job.read_bytes()
        for changes in ({'progress': float('nan')}, {'progress': True}, {'state': 'BOGUS'}, {'request_path': '../outside'}, {'result_path': 'outputs/elsewhere'}, {'started_at': '2026-01-01'}, {'job_id': '../escape'}):
            self.paths.chronos_job.write_bytes(original)
            self.write_job(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                chronos_job.read_evaluation_job(self.paths)
        self.paths.chronos_job.write_bytes(original)
        with patch.object(chronos_job, 'evaluate_direct_request', return_value=self.root):
            self.assertEqual(chronos_job.run_evaluation_worker(*args), 2)
        self.assertEqual(chronos_job.read_evaluation_job(self.paths).state, 'FAILED')

    def test_evaluation_job_rejects_links_before_reading_or_writing_external_targets(self):
        self.start()
        args = self.worker_args()
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / 'target'
            target.write_bytes(b'untouched')
            for path in (self.paths.chronos_job, args[3], self.request):
                original = path.read_bytes()
                path.unlink()
                try:
                    path.symlink_to(target)
                except OSError as error:
                    path.write_bytes(original)
                    self.skipTest(str(error))
                try:
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        chronos_job.read_evaluation_job(self.paths)
                    self.assertEqual(target.read_bytes(), b'untouched')
                finally:
                    path.unlink()
                    path.write_bytes(original)

    def test_evaluation_job_completed_state_requires_full_progress(self):
        self.start()
        self.write_job(state='SUCCEEDED', progress=0.2, result_path=self.result.relative_to(self.root).as_posix())
        with self.assertRaises(ValueError):
            chronos_job.read_evaluation_job(self.paths)

    def test_evaluation_job_reader_normalizes_supplied_local_time_before_persisting(self):
        first = self.start()
        local_now = (first.started_at + timedelta(seconds=31)).astimezone(timezone(timedelta(hours=2)))
        chronos_job.read_evaluation_job(self.paths, now=local_now)
        self.assertEqual(chronos_job.read_evaluation_job(self.paths).state, 'INTERRUPTED')

    def test_evaluation_job_real_request_and_evaluator_publish_offline_success(self):
        from tests.test_dashboard_chronos import DirectEvaluationTests
        from perpetual_engine.dashboard_chronos import prepare_direct_evaluation_request

        fixture = DirectEvaluationTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.refresh_counts(**{ticker: 49 for ticker in fixture.current_prices()})
        with patch.object(chronos_job, 'prepare_direct_evaluation_request', side_effect=prepare_direct_evaluation_request), patch(
            'perpetual_engine.dashboard_chronos.load_covariate_table', return_value=(fixture.macro, fixture.macro_vintage_id),
        ):
            job = chronos_job.start_evaluation_job(fixture.paths, fixture.state, launcher=self.launcher)
        with patch('perpetual_engine.dashboard_chronos.load_covariate_vintage', return_value=fixture.macro), patch(
            'perpetual_engine.chronos_data.refresh_chronos_data', side_effect=AssertionError('network forbidden'),
        ), patch('perpetual_engine.dashboard_service.refresh_dashboard_data', side_effect=AssertionError('network forbidden')):
            self.assertEqual(chronos_job.run_evaluation_worker(*self.worker_args(), predictor=Mock(side_effect=AssertionError('short history must not load model'))), 0)
        done = chronos_job.read_evaluation_job(fixture.paths)
        self.assertEqual(done.job_id, job.job_id)
        self.assertEqual(done.state, 'SUCCEEDED')
        self.assertTrue((done.result_path / 'covariate_contribution.csv').is_file())

    def test_evaluation_job_exclusive_log_creation_never_truncates_collision(self):
        job_id = 'c' * 32
        log = self.paths.chronos_job.parent / 'chronos_jobs' / f'{job_id}.log'
        log.parent.mkdir(parents=True)
        log.write_bytes(b'previous log')
        with patch.object(chronos_job, 'uuid4', return_value=Mock(hex=job_id)):
            failed = self.start()
        self.assertEqual(failed.state, 'FAILED')
        self.assertEqual(log.read_bytes(), b'previous log')
        self.assertEqual(self.launcher.call_count, 0)
        self.assertEqual(self.start().state, 'STARTING')

    def test_evaluation_job_rejects_linked_output_root_and_log_before_launch(self):
        with tempfile.TemporaryDirectory() as external:
            external_root = Path(external)
            linked_paths = DashboardPaths.from_root(self.root / 'project')
            linked_paths.project_root.mkdir()
            linked_paths.chronos_output_root.parent.mkdir()
            try:
                linked_paths.chronos_output_root.symlink_to(external_root, target_is_directory=True)
            except OSError as error:
                self.skipTest(str(error))
            with self.assertRaises(ValueError):
                chronos_job.start_evaluation_job(linked_paths, object(), launcher=self.launcher)
            job_id = 'd' * 32
            log = self.paths.chronos_job.parent / 'chronos_jobs' / f'{job_id}.log'
            log.parent.mkdir(parents=True)
            log.symlink_to(external_root / 'missing')
            with patch.object(chronos_job, 'uuid4', return_value=Mock(hex=job_id)), self.assertRaises(ValueError):
                self.start()
            self.assertFalse((external_root / 'missing').exists())
            self.assertEqual(self.launcher.call_count, 0)

    def test_evaluation_job_actual_detached_cli_persists_failure_without_parent_handle(self):
        # The immutable request is deliberately malformed: the real offline worker must fail closed.
        processes = []

        def launcher(*args, **kwargs):
            process = subprocess.Popen(*args, **kwargs)
            processes.append(process)
            return process

        def cleanup_worker():
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)

        self.addCleanup(cleanup_worker)
        with patch.dict(os.environ, {'PYTHONPATH': str(Path.cwd())}):
            job = self.start(launcher)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = chronos_job.read_evaluation_job(self.paths)
            if job.state in chronos_job.TERMINAL_STATES:
                break
            time.sleep(0.05)
        self.assertEqual(job.state, 'FAILED')
        self.assertEqual(processes[0].wait(timeout=5), 2)
        log = self.paths.chronos_job.parent / 'chronos_jobs' / f'{job.job_id}.log'
        self.assertNotIn('Traceback', log.read_text())

    def test_evaluation_job_duplicate_worker_and_invalid_progress_fail_closed(self):
        self.start()
        args = self.worker_args()

        def evaluate(*_args, progress, **_kwargs):
            self.assertEqual(chronos_job.run_evaluation_worker(*args), 2)
            progress(float('nan'), 'secret')
            self.fail('non-finite progress must be rejected')

        with patch.object(chronos_job, 'evaluate_direct_request', side_effect=evaluate):
            self.assertEqual(chronos_job.run_evaluation_worker(*args), 2)
        self.assertEqual(chronos_job.read_evaluation_job(self.paths).state, 'FAILED')
