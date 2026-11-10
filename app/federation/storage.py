"""Federation state in the same transaction and encrypted DB as application data."""
from contextlib import contextmanager
import re
from .state import PrototypeState

TABLES = ('metadata','peers','devices','contacts','grants','challenges','replay','events','profiles','peer_certificates')
_TABLE_PATTERN = re.compile(r'\b(' + '|'.join(TABLES) + r')\b')


class StateConnection:
    """Prefix only developer-authored SQL; user data remains bound parameters."""
    def __init__(self, connection):
        self.application_connection = connection
        self.rollback_actions = []

    def execute(self, query, values=()):
        query = _TABLE_PATTERN.sub(lambda match: 'federation_' + match[0], query)
        # The canonical metadata column is encrypted TEXT on both engines.
        if 'federation_metadata' in query:
            values = tuple(value.decode('utf-8') if isinstance(value, bytes) else value for value in values)
        return self.application_connection.execute(query, values)

    def executemany(self, query, rows):
        for row in rows:
            self.execute(query, row)


class FederationState(PrototypeState):
    canonical = True

    def __init__(self, db, cipher, root):
        self.db, self.cipher, self.root = db, cipher, root

    @contextmanager
    def transaction(self):
        wrapped = None
        try:
            with self.db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                wrapped = StateConnection(connection)
                yield wrapped
        except BaseException:
            if wrapped:
                for action in reversed(wrapped.rollback_actions):
                    try: action()
                    except Exception: pass
            raise
