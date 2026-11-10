#!/usr/bin/env python3
"""Build a minimal, secret-free context and run isolated TLS probes in Docker.

All fixtures are newly generated test data. Cleanup targets only this run's
unique Compose project, image tag, and temporary directory. No .env is read.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.federation.identity import SigningIdentity

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'scripts/federation'


def write_private(path, key):
    body = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    with path.open('xb') as output:
        path.chmod(0o600)
        output.write(body)


def fixtures(root, *, client_auth=False):
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Ephemeral DrG federation test CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False,
                                      key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    (root / 'ca.pem').write_bytes(ca_pem)
    identities = {name: SigningIdentity.generate('https://' + name + '.federation.test:8443')
                  for name in ('alice', 'bob')}
    for name, identity in identities.items():
        directory = root / name
        directory.mkdir(mode=0o700)
        (directory / 'descriptor.json').write_text(identity.descriptor.model_dump_json())
        other = identities['bob' if name == 'alice' else 'alice']
        (directory / 'peer.json').write_text(other.descriptor.model_dump_json())
        write_private(directory / 'signing.pem', identity.private_key)
        tls_key = ec.generate_private_key(ec.SECP256R1())
        hostname = name + '.federation.test'
        tls_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
        tls = (x509.CertificateBuilder().subject_name(tls_name).issuer_name(ca_name)
               .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(tls_key.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                           key_encipherment=False, data_encipherment=False,
                                           key_agreement=False, key_cert_sign=False, crl_sign=False,
                                           encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH] +
                                  ([ExtendedKeyUsageOID.CLIENT_AUTH] if client_auth else [])), critical=False)
               .sign(ca_key, hashes.SHA256()))
        (directory / 'ca.pem').write_bytes(ca_pem)
        (directory / 'tls.pem').write_bytes(tls.public_bytes(serialization.Encoding.PEM))
        write_private(directory / 'tls-key.pem', tls_key)


def build_context(context):
    # Explicit allowlist: never send the repository root, .env, data, .git,
    # untracked tokens, fixtures, or user files to the Docker builder.
    paths = ['requirements.lock.txt', 'scripts/federation-smoke.py',
             'scripts/federation/docker_node.py', 'tests/test_federation_protocol.py',
             'tests/test_federation_probe.py']
    paths += [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'app/federation').glob('*.py'))]
    for relative in paths:
        target = context / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    shutil.copyfile(RECIPE / 'Dockerfile', context / 'Dockerfile')


def run():
    subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], check=True, timeout=15)
    run_id = secrets.token_hex(6)
    project, image = 'drg-federation-' + run_id, 'drg-federation-check:' + run_id
    with tempfile.TemporaryDirectory(prefix='drg-federation-docker-') as temporary:
        temporary = Path(temporary)
        context, data, state = (temporary / name for name in ('context', 'fixtures', 'state'))
        for directory in (context, data, state):
            directory.mkdir(mode=0o700)
        build_context(context)
        fixtures(data)
        environment = os.environ | {
            'FEDERATION_TEST_IMAGE': image, 'FEDERATION_TEST_FIXTURES': str(data),
            'FEDERATION_TEST_STATE': str(state), 'FEDERATION_TEST_UID': str(os.getuid()),
            'FEDERATION_TEST_GID': str(os.getgid()),
        }
        # Never inherit Compose file selection, project overrides, or profiles.
        for name in tuple(environment):
            if name.startswith('COMPOSE_'):
                environment.pop(name)
        compose = ['docker', 'compose', '--env-file', os.devnull, '--project-name', project,
                   '--file', str(RECIPE / 'compose.yaml')]
        def execute(arguments, *, timeout=120):
            subprocess.run(arguments, check=True, timeout=timeout, env=environment, cwd=temporary)
        try:
            execute(['docker', 'build', '--tag', image, str(context)], timeout=900)
            execute(compose + ['up', '--detach', '--wait', 'alice', 'bob'])
            execute(compose + ['run', '--rm', '--no-deps', 'verifier', 'python', '-m', 'pytest',
                               '-q', '-p', 'no:cacheprovider', '--basetemp=/tmp/pytest', 'tests/'])
            execute(compose + ['run', '--rm', '--no-deps', 'verifier'])
            execute(compose + ['stop', '--timeout', '3', 'bob'])
            execute(compose + ['run', '--rm', '--no-deps', 'verifier', 'python',
                               'scripts/federation/docker_node.py', 'offline'])
            execute(compose + ['up', '--detach', '--wait', 'bob'])
            execute(compose + ['run', '--rm', '--no-deps', 'verifier', 'python',
                               'scripts/federation/docker_node.py', 'restarted'])
        finally:
            # No prune, no production project, no existing named volumes.
            cleanup_failed = False
            try:
                cleanup = subprocess.run(compose + ['down', '--timeout', '3', '--volumes', '--remove-orphans'],
                                         env=environment, cwd=temporary, timeout=60)
                cleanup_failed = cleanup.returncode != 0
            except subprocess.SubprocessError:
                cleanup_failed = True
            try:
                present = subprocess.run(['docker', 'image', 'inspect', image], timeout=15,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if present.returncode == 0:
                    removed = subprocess.run(['docker', 'image', 'rm', image], timeout=30)
                    cleanup_failed = cleanup_failed or removed.returncode != 0
            except subprocess.SubprocessError:
                cleanup_failed = True
            if cleanup_failed:
                raise RuntimeError('Cleanup needs inspection for this test project: ' + project)
        print('DONE: isolated Docker checks passed; the volatile replay cache remains a production blocker.')
    print('CLEANED: test containers, network, image tag, certificates, keys, and fixtures removed.')


if __name__ == '__main__':
    try:
        run()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except (subprocess.SubprocessError, OSError, KeyboardInterrupt):
        raise SystemExit('Docker federation check interrupted or failed; inspect the preceding test output.') from None
