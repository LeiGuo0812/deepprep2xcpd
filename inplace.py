"""Transactional publication into an existing derivatives root (Linux container).

The image converter builds and validates a temporary dataset first. This module
preserves identical files, backs up replacements, journals writes, and rolls
back if validation of the combined dataset fails. Backups have opaque names
so BIDS readers cannot index them as another copy of a subject image.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    os.replace(temp, path)


def safe_target(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'Unsafe publication path: {relative}')
    target = root / relative
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f'Refusing a symlinked destination directory: {parent}')
    return target


@contextmanager
def dataset_lock(root):
    lock = safe_target(root, 'code/adapter/.inplace.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_symlink():
        raise ValueError('Adapter lock may not be a symlink')
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another in-place adapter operation holds the dataset lock')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.adapter-write-', dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_journal(root, journal_path):
    """Restore only transaction-owned changes; reject files edited afterwards."""
    journal = json.loads(journal_path.read_text())
    errors = []
    for item in reversed(journal['changes']):
        try:
            target = safe_target(root, item['path'])
            if target.is_symlink():
                raise ValueError(f'Destination changed to symlink: {target}')
            current = sha(target) if target.is_file() else None
            if current == item['before_sha256']:
                continue  # Not yet written, or already restored.
            if current != item['after_sha256']:
                raise ValueError(f'File changed outside this transaction: {target}')
            if item['backup']:
                backup = journal_path.parent / item['backup']
                if sha(backup) != item['before_sha256']:
                    raise ValueError(f'Backup checksum mismatch: {backup}')
                atomic_copy(backup, target)
            else:
                target.unlink()
        except Exception as exc:
            errors.append(str(exc))
    journal['state'] = 'ROLLBACK_FAILED' if errors else 'ROLLED_BACK'
    journal['rollback_errors'] = errors
    save(journal_path, journal)
    if errors:
        raise RuntimeError('; '.join(errors))


def rollback(root, transaction):
    root = Path(root).resolve()
    with dataset_lock(root):
        transactions = root / 'code/adapter/inplace-runs'
        journal = (transactions / transaction / 'journal.json').resolve()
        if not journal.is_relative_to(transactions.resolve()) or not journal.is_file():
            raise ValueError('Unknown in-place transaction')
        restore_journal(root, journal)
    print(f'ROLLED BACK: {transaction}; backups and journal retained')


def publish(stage, root, replace_paths, validate):
    """Publish with an explicit replacement allowlist and post-commit validation."""
    stage, root = Path(stage), Path(root).resolve()
    txroot = safe_target(root, 'code/adapter/inplace-runs')
    if txroot.is_symlink():
        raise ValueError('Transaction directory may not be a symlink')
    txroot.mkdir(parents=True, exist_ok=True)
    for old in txroot.glob('*/journal.json'):
        state = json.loads(old.read_text())['state']
        if state not in ('COMMITTED', 'ROLLED_BACK'):
            raise ValueError(f'Unfinished transaction: {old}; use rollback before retrying')
    changes, reused = [], []
    # Complete preflight before modifying any dataset files.
    for source in sorted(stage.rglob('*')):
        if not source.is_file():
            continue
        rel = str(source.relative_to(stage))
        target = safe_target(root, rel)
        if target.is_file() and (os.path.samefile(source, target) or sha(source) == sha(target)):
            reused.append(rel)
            continue
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file():
                raise ValueError(f'Cannot replace nonregular destination: {target}')
            if rel not in replace_paths:
                raise ValueError(f'Conflicting existing file; not overwritten: {target}')
        changes.append({'path': rel, 'before_sha256': sha(target) if target.is_file() else None,
                        'after_sha256': sha(source), 'backup': None})
    ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    tx = txroot / ident
    tx.mkdir()
    # Keep each invocation's filters, manifest, metrics and code independently.
    shutil.copytree(stage / 'code/adapter', tx / 'records')
    journal = {'state': 'PREPARING', 'transaction': ident, 'dataset': str(root),
               'reused': reused, 'changes': changes}
    journal_path = tx / 'journal.json'
    save(journal_path, journal)
    try:
        for i, item in enumerate(changes):
            target = safe_target(root, item['path'])
            if item['before_sha256'] is not None:
                item['backup'] = f'backups/{i:05d}.backup'
                backup = tx / item['backup']
                backup.parent.mkdir(exist_ok=True)
                shutil.copy2(target, backup)
                if sha(backup) != item['before_sha256']:
                    raise ValueError(f'File changed while backing up: {target}')
        journal['state'] = 'COMMITTING'
        save(journal_path, journal)
        for item in changes:
            target = safe_target(root, item['path'])
            current = sha(target) if target.is_file() else None
            if target.is_symlink() or current != item['before_sha256']:
                raise ValueError(f'Destination changed before publication: {target}')
            atomic_copy(stage / item['path'], target)
        journal['state'] = 'VALIDATING'
        save(journal_path, journal)
        result = validate(root)
        save(tx / 'reader_validation.json', result)
        journal['state'] = 'COMMITTED'
        save(journal_path, journal)
    except BaseException:
        # Includes Ctrl-C. A hard kill retains the journal for explicit rollback.
        save(journal_path, journal)
        restore_journal(root, journal_path)
        raise
    return {'transaction': ident, 'reused': len(reused), 'written': len(changes),
            'journal': str(journal_path)}
