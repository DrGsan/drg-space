#!/usr/bin/env python3
"""Create the complete documented configuration without replacing existing keys."""
import base64
import os
from pathlib import Path
import re
import secrets


def configuration():
    template = Path(__file__).resolve().parents[1] / '.env.example'
    body = template.read_text(encoding='utf-8')
    generated = {
        'APP_SECRET': secrets.token_urlsafe(48),
        'SETUP_TOKEN': secrets.token_urlsafe(32),
        'DATA_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'POSTGRES_ADMIN_PASSWORD': secrets.token_urlsafe(32),
    }
    for name, value in generated.items():
        body, count = re.subn(r'^' + name + r'=.*$', name + '=' + value, body, flags=re.MULTILINE)
        if count != 1:
            raise SystemExit('.env.example must contain exactly one ' + name + '= entry.')
    return body


def main():
    path = Path('.env')
    message = '.env already exists; keep it, especially APP_SECRET and DATA_ENCRYPTION_KEY.'
    if path.exists() or path.is_symlink():
        raise SystemExit(message)
    body = configuration()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(message) from None
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(body)
    print('Created .env from .env.example. Read SETUP_TOKEN locally for first-account setup. Never commit .env.')


if __name__ == '__main__':
    main()
