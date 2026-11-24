"""A local call action rejected at the storage gate consumes no proof or call transition."""
import asyncio
import json
import time
from uuid import uuid4

import pytest

from app.federation.actors import DeviceKeys
from app.federation.calls import FederatedCalls
from app.federation.prototype import Intent
from app.http import Application
from app.storage import StorageGate, StorageMiddleware
from tests.test_federation_storage import ACTOR, UID, configured
from tests.test_federation_calls import CALLEE_HANDLE, CALLER_HANDLE
from tests.test_storage_encryption import databases


@pytest.mark.parametrize('outlast_retry', [False, True])
def test_local_accept_storage_rejection_preserves_the_single_call(outlast_retry, databases):
    backend = databases()
    service, peer, caller_keys, caller_card = configured(backend)
    service.local_user = lambda: UID
    home = service.identity.descriptor.server_id
    keys = DeviceKeys.generate()
    device = keys.card(home, UID)
    service.calls = FederatedCalls(home, authorize=lambda *args: None, available=lambda *args: True)
    app = Application(__name__)
    app.include_router(service.router)
    app.add_middleware(StorageMiddleware, gate=StorageGate(backend.root))
    writer = StorageGate(backend.root)
    client = app.test_client()
    call_id = uuid4().hex

    def remote_call(connection, operation, payload):
        action = Intent(actor_server_id=peer.server_id, actor_id=ACTOR, target_actor_id=UID,
            device_id=caller_card.device_id, nonce='a'*64, event_id=uuid4().hex, operation=operation, payload=payload)
        proof = caller_keys.sign(caller_card, home, action.model_dump(), int(time.time()))
        return service.calls.remote_operation(connection, peer, action, int(time.time()),
            evidence={'intent':action.model_dump(), 'proof':proof})

    with service.state.transaction() as c:
        c.execute('INSERT INTO devices(id,user_id,card,active) VALUES(?,?,?,?)',
            (device.device_id, UID, device.model_dump_json(), 1))
        assert remote_call(c, 'call_invite', {'id':call_id,'device':CALLER_HANDLE,'sdp':'v=0'})['status']=='ringing'

    prepare = client.post('/api/federation/local/prepare',json={'device_id':device.device_id,
        'operation':'call_accept','payload':{'id':call_id,'device':CALLEE_HANDLE}})
    assert prepare.status_code==200,prepare.get_json()
    action=prepare.get_json()['intent']
    body={'intent':action,'proof':keys.sign(device,home,action,int(time.time()))}

    async def rejected_under_lock():
        async with writer.try_scheduled_maintenance() as available:
            assert available
            for attempt in range(2 if outlast_retry else 1):
                if attempt:
                    await asyncio.sleep(5)
                response=await asyncio.to_thread(client.post,'/api/federation/local/send',json=body)
                assert response.status_code==503
                assert response.headers['Retry-After']=='5' and response.get_json()['code']=='storage_busy'
    asyncio.run(rejected_under_lock())

    with service.state.transaction() as c:
        assert c.execute('SELECT COUNT(*) FROM challenges WHERE id=?',(action['nonce'],)).fetchone()[0]==1
        assert remote_call(c,'call_poll',{'id':call_id,'device':CALLER_HANDLE})['status']=='ringing'

    if outlast_retry:
        # The one retry also got a definite rejection. A later user action
        # gets its own nonce rather than silently replaying an old action.
        fresh=client.post('/api/federation/local/prepare',json={'device_id':device.device_id,
            'operation':'call_accept','payload':action['payload']}).get_json()['intent']
        body={'intent':fresh,'proof':keys.sign(device,home,fresh,int(time.time()))}
    response=client.post('/api/federation/local/send',json=body)
    assert response.status_code==200,response.get_json()
    assert client.post('/api/federation/local/send',json=body).status_code>=400
    with service.state.transaction() as c:
        result=remote_call(c,'call_poll',{'id':call_id,'device':CALLER_HANDLE})
        assert result['status']=='connecting'
        rows=c.execute('SELECT payload FROM federation_calls WHERE id=?',(call_id,)).fetchall()
        assert len(rows)==1
        details=json.loads(rows[0][0])
        assert sum(action['intent']['operation']=='call_accept' for action in details['recipient_actions'])==1
