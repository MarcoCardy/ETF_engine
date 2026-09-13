from __future__ import annotations

import errno
import json
import math
from perpetual_engine.file_lock import msvcrt
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from perpetual_engine.chronos import _is_sha256, _parse_utc, _publication_lock
from perpetual_engine.dashboard_chronos import evaluate_direct_request, prepare_direct_evaluation_request
from perpetual_engine.dashboard_service import DashboardPaths, DashboardState, _atomic_write
from perpetual_engine.io import canonical_json


TERMINAL_STATES = frozenset({'SUCCEEDED', 'FAILED', 'INTERRUPTED'})
_ACTIVE_STATES = frozenset({'STARTING', 'RUNNING'})
_MESSAGES = {
    'STARTING': 'Avvio della valutazione Chronos',
    'RUNNING': 'Valutazione variabili Chronos',
    'SUCCEEDED': 'Valutazione completata',
    'FAILED': 'Valutazione non riuscita. Controllare dati e modello locali e riprovare.',
    'INTERRUPTED': 'Valutazione interrotta. Riprovare la valutazione.',
}


@dataclass(frozen=True)
class EvaluationJob:
    job_id: str
    state: str
    progress: float
    request_path: Path
    result_path: Path | None
    message: str
    started_at: datetime
    updated_at: datetime


def _safe_path(root: Path, path: Path) -> Path:
    """Reject aliases, traversal and linked ancestors before touching job files."""
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):
        raise ValueError('Chronos job path escapes its project directory')
    for item in (path, *path.parents):
        if item == root.parent:
            break
        resolved = os.path.normcase(str(item.resolve()).removeprefix('\\\\?\\'))
        if item.is_symlink() or resolved != os.path.normcase(str(item)):
            raise ValueError('Chronos job path contains a link')
    return path


def _validated_paths(paths: DashboardPaths) -> DashboardPaths:
    root = paths.project_root
    if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
        raise ValueError('Chronos job project root is invalid')
    if paths != DashboardPaths.from_root(root):
        raise ValueError('Chronos job paths must use the expected project directories')
    for field in fields(paths):
        _safe_path(root, getattr(paths, field.name))
    for child in ('requests', 'evaluations', '.staging'):
        _safe_path(root, paths.chronos_output_root / child)
    _safe_path(root, paths.chronos_job.parent / 'chronos_jobs')
    return paths


def _request_path(paths: DashboardPaths, path: Path) -> Path:
    _safe_path(paths.project_root, path)
    if path.name != 'request.json' or not _is_sha256(path.parent.name) or path.parent.parent != paths.chronos_output_root / 'requests' or not path.is_file():
        raise ValueError('Chronos job request path is invalid')
    return path


def _job_file(paths: DashboardPaths, job_id: str, suffix: str) -> Path:
    if not isinstance(job_id, str) or len(job_id) != 32 or any(char not in '0123456789abcdef' for char in job_id):
        raise ValueError('Chronos job identity is invalid')
    return _safe_path(paths.project_root, paths.chronos_job.parent / 'chronos_jobs' / f'{job_id}.{suffix}')


def _result_path(paths: DashboardPaths, request: Path, result: Path) -> Path:
    _safe_path(paths.project_root, result)
    if result != paths.chronos_output_root / 'evaluations' / request.parent.name or not result.is_dir():
        raise ValueError('Chronos job result path is invalid')
    return result


def _write_job(paths: DashboardPaths, job: EvaluationJob) -> None:
    _validated_paths(paths)
    payload = {field.name: getattr(job, field.name) for field in fields(job)}
    for name in ('request_path', 'result_path'):
        payload[name] = None if payload[name] is None else payload[name].relative_to(paths.project_root).as_posix()
    for name in ('started_at', 'updated_at'):
        payload[name] = payload[name].isoformat()
    _atomic_write(paths.chronos_job, canonical_json({'schema_version': 'DIRECT_CHRONOS_JOB_V1', **payload}))


def _load_job(paths: DashboardPaths) -> EvaluationJob | None:
    _validated_paths(paths)
    if not paths.chronos_job.exists():
        return None
    try:
        raw = paths.chronos_job.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {'schema_version', *(field.name for field in fields(EvaluationJob))} or payload.pop('schema_version') != 'DIRECT_CHRONOS_JOB_V1':
            raise ValueError
        if canonical_json({'schema_version': 'DIRECT_CHRONOS_JOB_V1', **payload}) != raw:
            raise ValueError
        if payload['state'] not in _ACTIVE_STATES | TERMINAL_STATES or type(payload['progress']) not in (float, int) or not math.isfinite(payload['progress']) or not 0 <= payload['progress'] <= 1:
            raise ValueError
        if payload['message'] not in _MESSAGES.values():
            raise ValueError
        for name in ('request_path', 'result_path'):
            value = payload[name]
            if value is None and name == 'result_path':
                continue
            if not isinstance(value, str) or not value or Path(value).is_absolute() or Path(value).drive:
                raise ValueError
            payload[name] = _safe_path(paths.project_root, paths.project_root / value)
        for name in ('started_at', 'updated_at'):
            payload[name] = _parse_utc(payload[name], 'job timestamp')
        job = EvaluationJob(**payload)
        if job.updated_at < job.started_at or (job.state == 'SUCCEEDED') != (job.result_path is not None) or (job.state == 'SUCCEEDED' and job.progress != 1):
            raise ValueError
        _request_path(paths, job.request_path)
        _job_file(paths, job.job_id, 'lock')
        _job_file(paths, job.job_id, 'log')
        if job.result_path is not None:
            _result_path(paths, job.request_path, job.result_path)
        return job
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Chronos job state is invalid') from error


@contextmanager
def _worker_lock(paths: DashboardPaths, lock_path: Path):
    """The existing byte is never rewritten; an open handle owns worker liveness."""
    _safe_path(paths.project_root, lock_path)
    before = lock_path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != 1:
        raise ValueError('Chronos worker lock is invalid')
    with lock_path.open('r+b', buffering=0) as handle:
        _safe_path(paths.project_root, lock_path)
        opened = os.fstat(handle.fileno())
        current = lock_path.lstat()
        if len({(item.st_dev, item.st_ino) for item in (before, opened, current)}) != 1:
            raise ValueError('Chronos worker lock changed while opening')
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        try:
            if handle.read() != b'0':
                raise ValueError('Chronos worker lock is invalid')
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _read_locked(paths: DashboardPaths, now: datetime) -> EvaluationJob | None:
    job = _load_job(paths)
    if job is None or job.state in TERMINAL_STATES or (job.state == 'STARTING' and (now - job.started_at).total_seconds() < 30):
        return job
    with _worker_lock(paths, _job_file(paths, job.job_id, 'lock')) as acquired:
        if acquired:
            job = replace(job, state='INTERRUPTED', message=_MESSAGES['INTERRUPTED'], updated_at=max(now, job.updated_at))
            _write_job(paths, job)
    return job


def read_evaluation_job(paths: DashboardPaths, *, now: datetime | None = None) -> EvaluationJob | None:
    _validated_paths(paths)
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Chronos job timestamp must include a timezone')
    with _publication_lock(paths.chronos_job.parent):
        return _read_locked(paths, at.astimezone(timezone.utc))


def start_evaluation_job(paths: DashboardPaths, state: DashboardState, *, launcher=subprocess.Popen) -> EvaluationJob:
    _validated_paths(paths)
    with _publication_lock(paths.chronos_job.parent):
        current = _read_locked(paths, datetime.now(timezone.utc))
        if current is not None and current.state in _ACTIVE_STATES:
            return current
        request = _request_path(paths, prepare_direct_evaluation_request(paths, state))
        at = datetime.now(timezone.utc)
        job = EvaluationJob(uuid4().hex, 'STARTING', 0.0, request, None, _MESSAGES['STARTING'], at, at)
        lock_path = _job_file(paths, job.job_id, 'lock')
        log_path = _job_file(paths, job.job_id, 'log')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(paths.project_root, lock_path)
        with lock_path.open('xb') as handle:
            handle.write(b'0')
        _write_job(paths, job)
        try:
            # Exclusive creation prevents following a racing log link or truncating an old log.
            with log_path.open('xb') as log_handle:
                _safe_path(paths.project_root, log_path)
                launcher(
                    [sys.executable, '-m', 'perpetual_engine', 'chronos', 'dashboard-evaluate-worker',
                     '--project-root', str(paths.project_root), '--request', str(request),
                     '--state', str(paths.chronos_job), '--lock', str(lock_path)],
                    cwd=paths.project_root, stdin=subprocess.DEVNULL, stdout=log_handle,
                    stderr=subprocess.STDOUT, close_fds=True,
                    creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
        except Exception:
            job = replace(job, state='FAILED', message=_MESSAGES['FAILED'], updated_at=datetime.now(timezone.utc))
            _write_job(paths, job)
        return job


def run_evaluation_worker(project_root: Path, request_path: Path, state_path: Path, lock_path: Path, *, predictor=None) -> int:
    if not project_root.is_absolute() or project_root.resolve() != project_root:
        raise ValueError('Chronos worker project root is invalid')
    paths = _validated_paths(DashboardPaths.from_root(project_root))
    _request_path(paths, request_path)
    if state_path != paths.chronos_job or lock_path != _job_file(paths, lock_path.stem, 'lock'):
        raise ValueError('Chronos worker state or lock path is invalid')
    job_id = lock_path.stem
    # Claim liveness first; the serialized identity check rejects a reader's earlier retry.
    with _worker_lock(paths, lock_path) as acquired:
        if not acquired:
            return 2
        with _publication_lock(paths.chronos_job.parent):
            job = _load_job(paths)
            if job is None or job.job_id != job_id or job.request_path != request_path or job.state != 'STARTING':
                return 2
            job = replace(job, state='RUNNING', message=_MESSAGES['RUNNING'], updated_at=datetime.now(timezone.utc))
            _write_job(paths, job)

        def progress(value: float, message: str) -> None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Chronos job progress is invalid')
            with _publication_lock(paths.chronos_job.parent):
                current = _load_job(paths)
                if current is not None and current.job_id == job_id and current.state == 'RUNNING':
                    _write_job(paths, replace(current, progress=value, message=_MESSAGES['RUNNING'], updated_at=datetime.now(timezone.utc)))

        try:
            result = _result_path(paths, request_path, evaluate_direct_request(project_root, request_path, predictor=predictor, progress=progress))
            terminal = 'SUCCEEDED'
        except Exception:
            result, terminal = None, 'FAILED'
        with _publication_lock(paths.chronos_job.parent):
            current = _load_job(paths)
            if current is None or current.job_id != job_id or current.state != 'RUNNING':
                return 2
            _write_job(paths, replace(current, state=terminal, result_path=result, progress=1.0 if result is not None else current.progress, message=_MESSAGES[terminal], updated_at=datetime.now(timezone.utc)))
        return 0 if terminal == 'SUCCEEDED' else 2
