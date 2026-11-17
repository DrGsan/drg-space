"""Derivatives save work without weakening encryption, bounds or physical GC."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import secrets
import threading
import time

import pytest

from app.data_crypto import DataCipher
from app.file_store import FileStore
from app.file_thumbnail_cache import ThumbnailCache,ENTRY


def store(root, encrypted=True):
    return FileStore(root, DataCipher(b't'*32) if encrypted else DataCipher(enabled=False), backend='local')


@pytest.mark.parametrize('encrypted',[False,True])
def test_warm_derivative_skips_build_and_authenticates_bytes_and_context(tmp_path,encrypted):
    cache=ThumbnailCache(store(tmp_path,encrypted));identity='a'*32;calls=[];body=b'PRIVATE-PREVIEW-'*30
    def build():calls.append(True);return body
    assert cache.get(identity,build)==body==cache.get(identity,build)
    assert len(calls)==1
    path=cache.path(identity);assert path.stat().st_mode&0o777==0o600
    assert cache.root.stat().st_mode&0o777==0o700
    if encrypted:assert body not in path.read_bytes() and path.read_bytes().startswith(DataCipher.MAGIC)
    damaged=bytearray(path.read_bytes());damaged[-1]^=1;path.write_bytes(damaged)
    assert cache.get(identity,build)==body and len(calls)==2
    if encrypted:
        cache.path('b'*32).write_bytes(path.read_bytes())
        assert cache.get('b'*32,build)==body and len(calls)==3


def test_budget_entry_limit_expiry_and_missing_index_recover(tmp_path):
    cache=ThumbnailCache(store(tmp_path),maximum=2200,items=2,ttl=60);ids=['a'*32,'b'*32,'c'*32]
    for identity in ids:assert cache.get(identity,lambda:b'x'*600)==b'x'*600
    entries=[p for p in cache.root.iterdir() if ENTRY.fullmatch(p.name)]
    assert len(entries)==2 and sum(p.stat().st_size for p in entries)<=2200
    cache.root.joinpath('index').unlink()
    cache.prune(referenced={ids[-1]});assert cache.path(ids[-1]).exists()
    assert not cache.path(ids[1]).exists()
    os.utime(cache.path(ids[-1]),(time.time()-61,time.time()-61))
    cache.prune(referenced={ids[-1]});assert not cache.path(ids[-1]).exists()
    assert cache._index()['items']==0


def test_interrupted_publication_never_undercounts_and_stale_work_is_removed(tmp_path,monkeypatch):
    cache=ThumbnailCache(store(tmp_path),maximum=2200,items=2);atomic=cache._atomic
    def interrupted(path,body):
        if path.name!='index':raise OSError('Synthetic failed cache publication')
        atomic(path,body)
    with monkeypatch.context() as patch:
        patch.setattr(cache,'_atomic',interrupted)
        assert cache.get('a'*32,lambda:b'body')==b'body'
    assert not cache.path('a'*32).exists() and cache._index()['items']==1
    stale=cache.root/('.tmp-'+'f'*32);stale.write_bytes(b'synthetic incomplete work')
    cache.prune(referenced=set());assert cache._index()['items']==0 and not stale.exists()


def parallel_same_source(root,barrier,counter,results):
    try:
        cache=ThumbnailCache(store(root));barrier.wait(timeout=15)
        def build():
            with counter.get_lock():counter.value+=1
            time.sleep(.08);return b'one rendering'
        results.put(cache.get('a'*32,build))
    except BaseException as error:results.put(type(error).__name__)


def test_three_processes_build_one_derivative(tmp_path):
    store(tmp_path);ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(3);counter=ctx.Value('i',0);results=ctx.Queue()
    workers=[ctx.Process(target=parallel_same_source,args=(tmp_path,barrier,counter,results)) for _ in range(3)]
    try:
        for worker in workers:worker.start()
        assert [results.get(timeout=25) for _ in workers]==[b'one rendering']*3
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert counter.value==1
        assert len([p for p in ThumbnailCache(store(tmp_path)).root.iterdir() if ENTRY.fullmatch(p.name)])==1
    finally:
        for worker in workers:
            if worker.is_alive():worker.kill();worker.join()


def test_build_slots_bound_concurrent_distinct_sources(tmp_path):
    cache=ThumbnailCache(store(tmp_path));barrier=threading.Barrier(6);lock=threading.Lock();active=0;peak=0
    def request(index):
        barrier.wait(timeout=10)
        def build():
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            time.sleep(.04)
            with lock:active-=1
            return b'preview'
        return cache.get(f'{index:02x}'+'0'*30,build)
    with ThreadPoolExecutor(max_workers=6) as pool:assert list(pool.map(request,range(6)))==[b'preview']*6
    assert peak==2


def test_source_gc_waits_for_pending_derivative_and_leaves_neither_object(tmp_path):
    objects=store(tmp_path);cache=ThumbnailCache(objects);identity='a'*32
    source=objects.root/identity;source.write_bytes(b'synthetic source')
    building=threading.Event();release=threading.Event()
    def build():building.set();assert release.wait(5);return b'preview'
    with ThreadPoolExecutor(max_workers=2) as pool:
        request=pool.submit(cache.get,identity,build);assert building.wait(5)
        deleted=pool.submit(objects.delete,identity);time.sleep(.05);assert not deleted.done()
        release.set();assert request.result(timeout=5)==b'preview';deleted.result(timeout=5)
    assert not source.exists() and not cache.path(identity).exists()


def test_read_only_cache_does_not_break_authorized_render(tmp_path,monkeypatch):
    cache=ThumbnailCache(store(tmp_path))
    monkeypatch.setattr(cache,'_put',lambda *args:(_ for _ in ()).throw(OSError('Synthetic cache full')))
    assert cache.get('a'*32,lambda:b'preview')==b'preview'
    assert not cache.path('a'*32).exists()
