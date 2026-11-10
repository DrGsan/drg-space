"""Release identity and user-facing notes shared by local and Docker installs."""
from datetime import date
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = (_ROOT / 'VERSION').read_text(encoding='utf-8').strip()


def release_from_changelog(changelog: str, version: str) -> dict:
    heading = re.search(r'^## \[' + re.escape(version) + r'\] — (\d{4}-\d{2}-\d{2}) — (.+)$', changelog, re.MULTILINE)
    if not heading:
        raise ValueError(f'CHANGELOG.md has no release entry for {version}')
    released = date.fromisoformat(heading[1]).isoformat()
    section = changelog[heading.end():].split('\n## ', 1)[0]
    changes = []
    # Opening bullets describe the product; bold deployment fields stay in the log.
    for line in section.splitlines():
        if line.startswith('- **'):
            break
        if line.startswith('- '):
            changes.append(line[2:].strip())
    if not changes:
        raise ValueError(f'CHANGELOG.md has no user-facing changes for {version}')
    return {'version': version, 'date': released, 'title': heading[2], 'changes': changes}


APP_RELEASE = release_from_changelog((_ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), APP_VERSION)
