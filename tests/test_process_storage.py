"""Storage coordination with real independent processes and worker shutdown."""
import asyncio
import multiprocessing
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.http import Application, HTTPError, StreamingResponse
from app.process_locks import async_file_lock, cleanup_temps, leased_temp, try_file_lock
from app.storage import StorageGate, StorageMiddleware, blocking


def exclusive_child(root, pipe):
    class AnnouncedGate(StorageGate):
        def _begin_exclusive(self):
            entry, data = super()._begin_exclusive()
            # Signal only after admission is closed. A parent-side diagnostic
            # SH probe must not race the child's nonblocking EX acquisition.
            pipe.send('pending')
            return entry, data

    async def run():
        async with AnnouncedGate(root).exclusive():
            pipe.send('entered')
            await asyncio.to_thread(pipe.recv)
    asyncio.run(run())


def temporary_child(root, pipe):
    with leased_temp(root, '.drg-backup-work-') as directory:
        (Path(directory) / 'encrypted-stage').write_bytes(b'ciphertext')
        pipe.send(directory)
        pipe.recv()


def initialization_child(root, pipe):
    pipe.send('starting')
    with StorageGate(root).exclusive_sync():
        pipe.send('entered')
        pipe.recv()


def deferred_maintenance_child(root, pipe):
    async def run():
        gate=StorageGate(root)
        async with gate.try_exclusive() as available:
            pipe.send(available)
            pipe.recv()
        async with gate.try_exclusive() as available:
            pipe.send(available)
            pipe.recv()
    asyncio.run(run())


def start_child(target, root):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(str(root), child))
    process.start()
    child.close()
    return process, parent


def receive(pipe):
    assert pipe.poll(10), 'Child process did not report progress.'
    return pipe.recv()


def stop_child(process, pipe):
    if process.is_alive():
        process.kill()
    process.join(10)
    pipe.close()
    assert not process.is_alive()


def test_stream_lease_blocks_other_process_and_rejects_new_requests(tmp_path):
    gate = StorageGate(tmp_path)
    request = SimpleNamespace(url=SimpleNamespace(path='/api/files/download'))
    async def download(request):
        return StreamingResponse(iter((b'first', b'second')))
    response = asyncio.run(StorageMiddleware(gate).handle(request, download, None))
    process, pipe = start_child(exclusive_child, tmp_path)
    try:
        assert receive(pipe) == 'pending'
        assert gate.exclusive_pending
        assert not pipe.poll(0.05)
        blocked = asyncio.run(StorageMiddleware(gate).handle(request, download, None))
        assert blocked.status_code == 503 and blocked.headers['Retry-After'] == '5'
        async def conflicting():
            with pytest.raises(HTTPError) as caught:
                async with StorageGate(tmp_path).exclusive():
                    pytest.fail('A second exclusive operation entered.')
            assert caught.value.status_code == 423
        asyncio.run(conflicting())
        response.close()  # No runtime exists; release must be synchronous.
        response.close()
        assert receive(pipe) == 'entered'
        pipe.send('finish')
        process.join(10)
        assert process.exitcode == 0 and gate.active == 0
    finally:
        response.close()
        stop_child(process, pipe)


def test_busy_background_sweep_does_not_close_http_admission_in_another_process(tmp_path):
    gate=StorageGate(tmp_path);active=gate.acquire_shared()
    process,pipe=start_child(deferred_maintenance_child,tmp_path)
    try:
        assert receive(pipe) is False
        assert not gate.exclusive_pending
        gate.acquire_shared().close()
        active.close();pipe.send('retry')
        assert receive(pipe) is True
        with pytest.raises(HTTPError) as caught:gate.acquire_shared()
        assert caught.value.status_code==503
        pipe.send('finish');process.join(10)
        assert process.exitcode==0
        gate.acquire_shared().close()
    finally:
        active.close();stop_child(process,pipe)


@pytest.mark.parametrize('file_backed',[False,True])
def test_background_probe_releases_admission_before_returning_busy(tmp_path,file_backed):
    gate=StorageGate(tmp_path if file_backed else None);active=gate.acquire_shared()
    async def run():
        async with gate.try_exclusive() as available:
            assert not available and not gate.exclusive_pending
            gate.acquire_shared().close()
        active.close()
        async with gate.try_exclusive() as available:
            assert available and gate.exclusive_pending
            with pytest.raises(HTTPError):gate.acquire_shared()
        gate.acquire_shared().close()
    try:asyncio.run(run())
    finally:active.close()


def test_worker_kill_releases_global_exclusive_without_changing_lock_inode(tmp_path):
    process, pipe = start_child(exclusive_child, tmp_path)
    try:
        assert receive(pipe) == 'pending'
        assert receive(pipe) == 'entered'
        entry = tmp_path / '.drg-process-locks' / 'storage-entry.lock'
        inode = entry.stat().st_ino
        assert StorageGate(tmp_path).exclusive_pending
        process.kill()
        process.join(10)
        async def replacement():
            async with StorageGate(tmp_path).exclusive():
                assert entry.stat().st_ino == inode
        asyncio.run(replacement())
    finally:
        stop_child(process, pipe)


def test_independent_shared_descriptors_cannot_release_one_another(tmp_path):
    gate = StorageGate(tmp_path)
    first, second = gate.acquire_shared(), gate.acquire_shared()
    try:
        first.close()
        assert gate.active == 1
        async def drain():
            async with gate.exclusive():
                return 'entered'
        async def run():
            task = asyncio.create_task(drain())
            await asyncio.sleep(0)
            assert gate.exclusive_pending and not task.done()
            second.close()
            assert await task == 'entered'
        asyncio.run(run())
    finally:
        first.close()
        second.close()


def test_cancelled_exclusive_waiter_reopens_admission(tmp_path):
    gate = StorageGate(tmp_path)
    active = gate.acquire_shared()
    async def run():
        async def drain():
            async with gate.exclusive():
                pytest.fail('Active shared operation should prevent entry.')
        task = asyncio.create_task(drain())
        await asyncio.sleep(0)
        assert gate.exclusive_pending
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not gate.exclusive_pending
        gate.acquire_shared().close()
    try:
        asyncio.run(run())
    finally:
        active.close()


def test_initialization_exclusion_is_shared_with_http_gate(tmp_path):
    process, pipe = start_child(initialization_child, tmp_path)
    try:
        assert receive(pipe) == 'starting'
        assert receive(pipe) == 'entered'
        with pytest.raises(HTTPError) as caught:
            StorageGate(tmp_path).acquire_shared()
        assert caught.value.status_code == 503
        pipe.send('finish')
        process.join(10)
        with StorageGate(tmp_path).exclusive_sync():
            pass
        assert process.exitcode == 0
    finally:
        stop_child(process, pipe)


def test_restarted_worker_initialization_waits_for_running_restore(tmp_path):
    async def run():
        gate = StorageGate(tmp_path)
        process = pipe = None
        try:
            async with gate.exclusive():
                process, pipe = start_child(initialization_child, tmp_path)
                assert receive(pipe) == 'starting'
                assert not pipe.poll(0.05)
            assert receive(pipe) == 'entered'
            pipe.send('finish')
            process.join(10)
            assert process.exitcode == 0
        finally:
            if process:
                stop_child(process, pipe)
    asyncio.run(run())


def test_async_file_lock_wait_is_cancellable_without_blocking_loop(tmp_path):
    owner = try_file_lock(tmp_path, 'service.lock')
    assert owner is not None
    async def run():
        async def contender():
            async with async_file_lock(tmp_path, 'service.lock'):
                pytest.fail('Contender acquired an owned lock.')
        task = asyncio.create_task(contender())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.close()
        async with async_file_lock(tmp_path, 'service.lock'):
            pass
    try:
        asyncio.run(run())
    finally:
        owner.close()


def test_restart_cleanup_skips_live_archive_and_collects_killed_owner(tmp_path):
    process, pipe = start_child(temporary_child, tmp_path)
    try:
        directory = Path(receive(pipe))
        cleanup_temps(tmp_path, '.drg-backup-work-')
        assert (directory / 'encrypted-stage').read_bytes() == b'ciphertext'
        process.kill()
        process.join(10)
        cleanup_temps(tmp_path, '.drg-backup-work-')
        assert not directory.exists()
    finally:
        stop_child(process, pipe)


def test_temporary_directory_api_and_legacy_orphan_cleanup(tmp_path):
    legacy = tmp_path / '.drg-backup-work-legacy'
    legacy.mkdir()
    (legacy / 'stage').write_bytes(b'abandoned')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'kept').write_bytes(b'kept')
    (tmp_path / '.drg-backup-work-symlink').symlink_to(outside, target_is_directory=True)
    temporary = leased_temp(tmp_path, '.drg-backup-work-')
    with temporary as name:
        assert name == temporary.name
        cleanup_temps(tmp_path, '.drg-backup-work-')
        assert Path(name).is_dir() and not legacy.exists()
        assert (outside / 'kept').read_bytes() == b'kept'
    temporary.cleanup()
    assert not Path(name).exists()


def test_cleaner_cannot_claim_directory_before_creation_installs_lease(tmp_path, monkeypatch):
    from app import process_locks
    opened, allow_lease, cleanup_started, cleaned = (threading.Event() for _ in range(4))
    original = process_locks._open_file
    temporary, errors = [], []
    def delay_owner_open(path):
        if Path(path).name == '.owner-lock':
            opened.set()
            assert allow_lease.wait(5)
        return original(path)
    monkeypatch.setattr(process_locks, '_open_file', delay_owner_open)
    def create():
        try:
            temporary.append(leased_temp(tmp_path, '.drg-backup-work-'))
        except BaseException as error:
            errors.append(error)
    def cleanup():
        cleanup_started.set()
        try:
            cleanup_temps(tmp_path, '.drg-backup-work-')
        except BaseException as error:
            errors.append(error)
        finally:
            cleaned.set()
    creator, cleaner = threading.Thread(target=create), threading.Thread(target=cleanup)
    try:
        creator.start()
        assert opened.wait(5)
        cleaner.start()
        assert cleanup_started.wait(5)
        assert not cleaned.wait(0.05)
        allow_lease.set()
        creator.join(5)
        cleaner.join(5)
        assert not creator.is_alive() and not cleaner.is_alive() and not errors
        assert len(temporary) == 1 and Path(temporary[0].name).is_dir()
        assert Path(temporary[0].name).stat().st_mode & 0o777 == 0o700
        assert (Path(temporary[0].name) / '.owner-lock').stat().st_mode & 0o777 == 0o600
    finally:
        allow_lease.set()
        if creator.ident:
            creator.join(5)
        if cleaner.ident:
            cleaner.join(5)
        for directory in temporary:
            directory.cleanup()


def test_repeated_cancellation_keeps_shared_until_sync_write_finishes(tmp_path):
    gate = StorageGate(tmp_path)
    started, finish = threading.Event(), threading.Event()
    marker = tmp_path / 'committed'
    def write(*, value):
        started.set()
        assert finish.wait(5)
        marker.write_text(value)
    async def run():
        async def request():
            async with gate.shared():
                await blocking(write, value='finished')
        task = asyncio.create_task(request())
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert gate.active == 1 and not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.active == 0 and marker.read_text() == 'finished'
    try:
        asyncio.run(run())
    finally:
        finish.set()


def test_runtime_shutdown_waits_for_sync_handler_before_releasing_gate(tmp_path):
    app = Application('shutdown-storage-test')
    gate = StorageGate(tmp_path)
    app.add_middleware(StorageMiddleware, gate=gate)
    started, finish = threading.Event(), threading.Event()
    marker = tmp_path / 'committed'
    def handler():
        started.set()
        assert finish.wait(5)
        marker.write_text('finished')
        return {'ok': True}
    request = SimpleNamespace(url=SimpleNamespace(path='/api/write'))
    pending = app.runtime.submit(app.handle(request, handler, {}))
    shutdown = threading.Thread(target=app.stop_services)
    try:
        assert started.wait(5)
        shutdown.start()
        time.sleep(0.05)
        assert gate.active == 1 and shutdown.is_alive() and not marker.exists()
        finish.set()
        shutdown.join(10)
        assert not shutdown.is_alive()
        assert gate.active == 0 and marker.read_text() == 'finished'
        assert pending.cancelled()
    finally:
        finish.set()
        if shutdown.ident:
            shutdown.join(10)
        app.stop_services()
