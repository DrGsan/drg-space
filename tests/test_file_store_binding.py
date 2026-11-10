import json
import multiprocessing
import os

import pytest

from app.data_crypto import DataCipher
from app.file_store import FileStore


def create_binding(root, barrier, results):
    try:
        barrier.wait(timeout=10)
        FileStore(root,DataCipher(enabled=False),backend='local')
        results.put(json.loads((root/'.file-store.json').read_text()))
    except BaseException as error:
        results.put(type(error).__name__)


def test_fresh_file_store_binding_is_complete_for_three_processes(tmp_path):
    context=multiprocessing.get_context('spawn')
    barrier=context.Barrier(3);results=context.Queue()
    processes=[context.Process(target=create_binding,args=(tmp_path,barrier,results)) for _ in range(3)]
    try:
        for process in processes:process.start()
        entries=[results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(10);assert process.exitcode==0
        assert all(entry==entries[0] for entry in entries)
        assert entries[0]['version']==1 and entries[0]['storage_id']=='test'
    finally:
        for process in processes:
            if process.is_alive():process.kill();process.join()


def test_interrupted_binding_publication_does_not_leave_partial_marker(tmp_path,monkeypatch):
    def interrupted(*args):raise OSError('Simulated interruption before atomic publication')
    with monkeypatch.context() as patch:
        patch.setattr(os,'link',interrupted)
        with pytest.raises(OSError):FileStore(tmp_path,DataCipher(enabled=False),backend='local')
    assert not (tmp_path/'.file-store.json').exists()
    assert list(tmp_path.glob('.file-store.tmp-*'))==[]
    FileStore(tmp_path,DataCipher(enabled=False),backend='local')
    assert json.loads((tmp_path/'.file-store.json').read_text())['version']==1
