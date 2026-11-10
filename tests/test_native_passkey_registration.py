"""Native enrollment uses existing session-bound, UV-required registration; browser gets no account cookie."""
from tests.test_security import owner, member, client, reset, m
from tests.test_passkeys import Authenticator, options, finish


def test_native_creation_page_is_public_uncached_and_contains_only_ceremony_ui():
    browser = client()
    response = browser.get('/native/passkey-create')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    import json
    manifest=m.STATIC/'asset-manifest.json'
    script=json.loads(manifest.read_text())['aliases']['native-passkey-create.js'] if manifest.is_file() else 'native-passkey-create.js'
    assert '/assets/'+script in response.text
    assert browser.get('/assets/'+script).status_code==200
    assert '<input' not in response.text
    assert browser.get('/api/me').status_code == 401


def test_anonymous_browser_proof_completes_only_in_issuing_native_session():
    native, _, codes = owner()
    device = Authenticator()
    ceremony = options(native, codes[0])
    proof = device.register(ceremony['options'])
    browser = client()
    # Neither the public options nor the signed callback credential authorizes the browser.
    assert finish(browser, ceremony, proof, True).status_code == 401
    other, _ = member(native)
    other.cookies.set('drg_passkey', native.cookies.get('drg_passkey'), path='/api/')
    assert finish(other, ceremony, proof, True).status_code == 403
    assert finish(native, ceremony, proof, True).status_code == 200
    assert finish(native, ceremony, proof, True).status_code == 401
    assert len(native.get('/api/security/passkeys').json()['items']) == 1
    assert browser.get('/api/me').status_code == 401


def test_changed_account_or_bad_origin_does_not_register_returned_credential():
    native, _, codes = owner()
    device = Authenticator()
    ceremony = options(native, codes[0])
    proof = device.register(ceremony['options'], origin='https://foreign.example')
    assert finish(native, ceremony, proof, True).status_code == 401
    assert native.get('/api/security/passkeys').json()['items'] == []
