"""Small DB-API boundary: SQLite/SQLCipher or PostgreSQL, one fresh schema.

Routes keep bound SQL parameters. PostgreSQL translations are parsed, cached and
reject unsupported encrypted writes rather than silently storing plaintext.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from functools import lru_cache
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from app.data_crypto import DataCipher, StorageError
from app.schema import SCHEMA


# SQLCipher memory-security hooks are process-wide. Serialize encrypted SQLite
# connection lifetimes so opening/keying and closing never race across threads.
_SQLCIPHER_CONNECTIONS = threading.RLock()


PROTECTED = {
    'chat_recordings': {'payload'},
    'notion_connections': {'payload'},
    'notion_roots': {'payload'},
    'notion_pages': {'payload'},
    'notion_staging': {'payload'},
    'notion_events': {'payload'},
    'tracker_groups': {'payload'},
    'tracker_projects': {'payload'},
    'tracker_issues': {'payload'},
    'tracker_comments': {'body'},
    'tracker_history': {'payload'},
    'tracker_imports': {'payload'},
    'tracker_uploads': {'payload'},
    'tracker_upload_chunks': {'body'},
    'federation_metadata': {'value'},
    'federation_peers': {'descriptor'},
    'federation_peer_certificates': {'certificate'},
    'federation_devices': {'card'},
    'federation_contacts': {'card'},
    'federation_profiles': {'profile'},
    'federation_contact_devices': {'card'},
    'federation_uploads': {'name','mime'},
    'federation_upload_chunks': {'payload'},
    'federation_events': {'result'},
    'federation_calls': {'payload'},
    'federation_call_links': {'payload'},
    'guest_call_rooms': {'payload'},
    'guest_call_participants': {'payload'},
    'guest_call_links': {'payload'},
    'calls': {'payload'},
    'push_subscriptions': {'payload'},
    'note_assets': {'payload'},
    'notifications': {'payload'},
    'session_info': {'agent','ip'},
    'files': {'name','mime'},
    'file_shares': {'password','allowed'},
    'users': {'login', 'password', 'totp', 'settings'},
    'notes': {'title', 'body', 'blocks', 'icon', 'cover'},
    'note_revisions': {'title', 'body', 'blocks'},
    'shares': {'password', 'allowed'},
    'tasks': {'title', 'day'},
    'task_lists': {'payload'},
    'task_details': {'payload'},
    'spaces': {'name'},
    'note_databases': {'properties', 'views'},
    'database_records': {'properties'},
    'note_comments': {'body', 'mentions'},
    'collaborative_docs': {'state'},
    'messages': {'body'},
    'media': {'mime'},
    'invites': {'login'},
    'app_settings': {'value'},
    'authenticators': {'label','issuer','secret'},
    'expense_events': {'title','payload'},
    'expense_entries': {'payload'},
    'auth_failures': {'ip'},
    'security_bans': {'ip'},
    'security_events': {'ip', 'subject', 'agent', 'details'},
    'security_incidents': {'ip', 'subject'},
}
LOOKUP = {'login', 'ip'}


def scope(table, column):
    return 'lookup:' + column if column in LOOKUP else 'column:' + table + '.' + column


def portable_connection():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript(SCHEMA)
    return connection


with portable_connection() as _schema:
    SCHEMA_ROWS = [tuple(r) for r in _schema.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    SCHEMA_HASH = hashlib.sha256(json.dumps(SCHEMA_ROWS).encode()).hexdigest()
    # The only legacy archive difference is the expanded tracker status CHECK.
    TRACKER_LEGACY_SCHEMA_HASH = hashlib.sha256(json.dumps([tuple(value.replace("status IN ('todo','work','review','done','cancelled')","status IN ('todo','work','review','done')") if isinstance(value,str) else value for value in row) for row in SCHEMA_ROWS]).encode()).hexdigest()
    COLUMNS = {r[0]: [dict(c) for c in _schema.execute('PRAGMA table_info(' + r[0] + ')')]
               for r in _schema.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
_schema.close()


class Row:
    """Match sqlite3.Row's named lookup and value iteration."""
    def __init__(self, names, values): self.names, self.values = names, tuple(values)
    def __getitem__(self, key): return self.values[self.names.index(key)] if isinstance(key, str) else self.values[key]
    def __iter__(self): return iter(self.values)
    def __len__(self): return len(self.values)
    def keys(self): return self.names


class Cursor:
    def __init__(self, connection, cursor=None, rows=None, lastrowid=None):
        self.connection, self.cursor, self.rows = connection, cursor, iter(rows) if rows is not None else None
        self.rowcount = cursor.rowcount if cursor is not None else 0
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, 'lastrowid', None)
        self.fields = []
        if connection.backend.kind == 'postgres' and cursor is not None and cursor.description:
            result = cursor.pgresult
            for index, description in enumerate(cursor.description):
                table = connection.backend.table_oids.get(result.ftable(index))
                columns = COLUMNS.get(table, [])
                ordinal = result.ftablecol(index)
                column = columns[ordinal-1]['name'] if 0 < ordinal <= len(columns) else None
                self.fields.append((description.name, table, column))

    def _convert(self, row):
        if row is None or not self.fields: return row
        names, values = [], []
        for (name, table, column), value in zip(self.fields, row):
            if name == '_rowid': continue
            if column in PROTECTED.get(table, set()):
                value = self.connection.backend.cipher.decode(value, scope(table, column), column in LOOKUP)
            elif isinstance(value, memoryview): value = bytes(value)
            names.append(name); values.append(value)
        return Row(names, values)

    def fetchone(self): return self._convert(next(self.rows, None) if self.rows is not None else self.cursor.fetchone())
    def fetchall(self): return list(self)
    def __iter__(self): return self
    def __next__(self):
        row = self.fetchone()
        if row is None: raise StopIteration
        return row


@lru_cache(maxsize=2048)
def query_plan(query):
    """Return generated SQL and encrypted parameter bindings, never actual data."""
    tokens = sqlglot.tokenize(query, read='sqlite')
    positions = [t for t in tokens if t.token_type == TokenType.PLACEHOLDER and t.text == '?']
    numbered = query
    for index, token in reversed(list(enumerate(positions))):
        numbered = numbered[:token.start] + ':p' + str(index) + numbered[token.end+1:]
    tree = sqlglot.parse_one(numbered, read='sqlite')
    bindings, literals = {}, {}
    table = None

    def protect(value, table, column):
        if column not in PROTECTED.get(table, set()): return value
        if isinstance(value, exp.Null): return value
        if isinstance(value, exp.Placeholder):
            name = value.name
        elif isinstance(value, exp.Literal):
            name = 'literal' + str(len(literals)); literals[name] = value.this
            value = exp.Placeholder(this=name)
        elif isinstance(value, exp.Column) and value.table.lower() == 'excluded':
            if value.name != column: raise StorageError('Unsupported encrypted upsert assignment.')
            return value
        else: raise StorageError('Unsupported encrypted write expression; add an explicit storage mapping.')
        bindings[name] = (scope(table, column), column in LOOKUP)
        return value

    if isinstance(tree, exp.Insert):
        target = tree.this
        table = target.this.name if isinstance(target, exp.Schema) else target.name
        fields = [c.name for c in target.expressions] if isinstance(target, exp.Schema) else [c['name'] for c in COLUMNS[table]]
        if not isinstance(tree.expression, exp.Values): raise StorageError('Only bound VALUES inserts are supported.')
        original = list(fields)
        defaults = {c['name']: c['dflt_value'] for c in COLUMNS[table] if c['name'] in PROTECTED.get(table, set()) and c['name'] not in fields and c['dflt_value'] is not None}
        fields.extend(defaults)
        for row in tree.expression.expressions:
            if len(row.expressions) != len(original): raise StorageError('Insert does not match the canonical schema.')
            values = list(row.expressions) + [sqlglot.parse_one(v, read='sqlite') for v in defaults.values()]
            row.set('expressions', [protect(value, table, column) for column, value in zip(fields, values)])
        tree.set('this', exp.Schema(this=exp.to_table(table), expressions=[exp.to_identifier(f) for f in fields]))
        if tree.args.get('alternative') == 'IGNORE':
            tree.set('alternative', None)
            tree.set('conflict', exp.OnConflict(action=exp.Var(this='DO NOTHING')))
        conflict = tree.args.get('conflict')
        if conflict:
            conflict.set('conflict_keys', [key.this if isinstance(key,exp.Ordered) else key for key in conflict.args.get('conflict_keys',[])])
            for assignment in conflict.expressions:
                assignment.set('expression', protect(assignment.expression, table, assignment.this.name))
        if table in ('messages','notifications'): tree.set('returning', exp.Returning(expressions=[exp.column('id')]))
    elif isinstance(tree, exp.Update):
        table = tree.this.name
        for assignment in tree.expressions:
            assignment.set('expression', protect(assignment.expression, table, assignment.this.name))

    aliases = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    # Lookup fields use deterministic AES-SIV, allowing indexed equality without
    # putting plaintext logins/IPs in SQL or exposing a database decryption key.
    for comparison in list(tree.find_all(exp.EQ, exp.NEQ)):
        for left, right in ((comparison.this, comparison.expression), (comparison.expression, comparison.this)):
            if not isinstance(left, exp.Column) or left.name not in LOOKUP: continue
            source = aliases.get(left.table, table)
            if source is None:
                candidates = {t.name for t in tree.find_all(exp.Table) if left.name in PROTECTED.get(t.name, set())}
                if len(candidates) == 1: source = candidates.pop()
            if left.name not in PROTECTED.get(source, set()): continue
            if isinstance(right, exp.Placeholder): bindings[right.name] = (scope(source, left.name), True)
            elif isinstance(right, exp.Literal): right.replace(protect(right, source, left.name))

    for column in tree.find_all(exp.Column):
        if column.name == 'rowid': column.set('this', exp.to_identifier('_rowid'))
    for condition in list(tree.find_all(exp.Is)):
        if isinstance(condition.expression, exp.Null):
            if isinstance(condition.this, exp.Placeholder):
                condition.set('this', exp.Cast(this=condition.this.copy(), to=exp.DataType.build('TEXT')))
        else: condition.replace(exp.NullSafeEQ(this=condition.this.copy(), expression=condition.expression.copy()))
    return tree.sql(dialect='postgres'), bindings, literals, len(positions)


class Connection:
    def __init__(self, backend, raw):
        self.backend, self.raw = backend, raw
        self.quota_guard=False;self.quota_disabled=False;self.quota_deltas={};self.quota_recount=set()
        self.realtime_dirty=False

    def execute(self,query,parameters=()):
        if self.quota_guard or self.quota_disabled:return self._execute(query,parameters)
        from app import quota
        self.quota_guard=True
        try:
            info=quota.before(self,query,parameters)
            result=self._execute(query,parameters)
            quota.after(self,info,result)
            return result
        finally:self.quota_guard=False

    def _execute(self, query, parameters=()):
        mutation=re.match(r'^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)',query,re.I)
        if mutation and mutation[1].lower() in {'notes','federation_grants','collaborative_docs'}:self.federation_collaborative_ids=None
        if mutation and mutation[1].lower() in {'notes','note_permissions','spaces','space_members','users','app_settings'}:self.note_access_index=None
        invalidate=mutation and (mutation[1].lower() in {'notes','collaborative_docs','messages','chat_reads','notifications','push_deliveries','note_permissions','spaces','space_members','users','sessions','note_unlocks','app_settings','security_bans','social_requests'} or mutation[1].lower().startswith('federation_'))
        # Reordering failed deliveries is a retry bookkeeping write. Waking
        # the dispatcher on its own failure would bypass its retry interval.
        if mutation and mutation[1].lower()=='federation_revocations' and query.lstrip().upper().startswith('UPDATE'):
            invalidate=False
        if self.backend.kind == 'sqlite':
            cursor=self.raw.execute(query, parameters)
            if invalidate and cursor.rowcount!=0:self.realtime_dirty=True
            return Cursor(self,cursor)
        upper = query.strip().upper()
        if upper.startswith('BEGIN'):
            self.raw.execute('SELECT pg_advisory_xact_lock(712941583)')
            return Cursor(self, rows=[])
        if upper == 'PRAGMA DEFER_FOREIGN_KEYS=ON':
            self.raw.execute('SET CONSTRAINTS ALL DEFERRED'); return Cursor(self, rows=[])
        if upper == 'PRAGMA FOREIGN_KEY_CHECK':
            self.raw.execute('SET CONSTRAINTS ALL IMMEDIATE')
            # Explicit IDs from a portable restore must advance the chat sequence.
            for table in ('messages','notifications'):
                self.raw.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {table}")
            return Cursor(self, rows=[])
        if upper.startswith('PRAGMA'): raise StorageError('Unsupported engine-specific operation.')
        sql, bindings, literals, count = query_plan(query)
        if len(parameters) != count: raise StorageError('Incorrect SQL parameter count.')
        values = {'p' + str(index): int(value) if isinstance(value,bool) else value for index, value in enumerate(parameters)} | literals
        for name, (context, deterministic) in bindings.items():
            values[name] = self.backend.cipher.encode(values[name], context, deterministic)
        cursor = self.raw.execute(sql, values)
        if invalidate and cursor.rowcount!=0:self.realtime_dirty=True
        if upper.startswith('INSERT') and 'RETURNING id' in sql:
            value = cursor.fetchone()
            return Cursor(self, cursor, lastrowid=value[0] if value else None)
        return Cursor(self, cursor)

    def executemany(self, query, rows):
        for parameters in rows: self.execute(query, parameters)

    def backup(self, target):
        """Portable logical snapshot into an in-memory standard SQLite database."""
        if self.backend.kind == 'sqlite':
            if not self.raw.in_transaction:self.raw.execute('BEGIN')
        else:self.raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        target.executescript(SCHEMA)
        target.execute('BEGIN'); target.execute('PRAGMA defer_foreign_keys=ON')
        for table, columns in COLUMNS.items():
            names = [c['name'] for c in columns]
            order = ' ORDER BY rowid' if table in ('note_revisions', 'note_comments') else ''
            rows = self.execute('SELECT ' + ','.join(names) + ' FROM ' + table + order)
            target.executemany('INSERT INTO ' + table + '(' + ','.join(names) + ') VALUES(' + ','.join('?' for _ in names) + ')', [tuple(row) for row in rows])
        target.commit()


class Database:
    def __init__(self, root, url=None, cipher=None, schema=None, redis_url=None):
        self.root = Path(root); self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / 'site.sqlite3'
        self.url = url if url is not None else os.getenv('DATABASE_URL', '')
        self.kind = 'postgres' if self.url else 'sqlite'
        if self.url and not self.url.startswith(('postgresql://', 'postgres://')):
            raise StorageError('DATABASE_URL must be a PostgreSQL connection URL; leave empty for SQLite.')
        # New .env files name drg_site explicitly; preserve legacy installations
        # that relied on the fallback instead of naming their schema.
        self.schema = schema or os.getenv('DATABASE_SCHEMA', 'drg')
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', self.schema): raise StorageError('Invalid DATABASE_SCHEMA.')
        self.cipher = cipher or DataCipher.from_env()
        self.table_oids = {}
        self.storage_id = None
        from app.redis_cache import RedisCache
        self.redis_cache=RedisCache(self,redis_url)
        self._pool = None
        self._pool_lock = threading.Lock()

    def start_pool(self):
        """Called after Gunicorn forks; schema initialization stays unpooled."""
        if self.kind!='postgres':return
        from psycopg_pool import ConnectionPool
        with self._pool_lock:
            if self._pool is not None:return
            def configure(raw):
                raw.execute('SET search_path TO '+self.schema+', pg_catalog')
                raw.commit()
            def reset(raw):
                raw.execute('RESET ALL')
                configure(raw)
            pool=ConnectionPool(self.url,min_size=1,max_size=4,open=False,
                kwargs={'connect_timeout':10,'application_name':'drg-space'},
                configure=configure,reset=reset,check=ConnectionPool.check_connection,
                timeout=10,max_waiting=128,max_idle=60,num_workers=1)
            pool.open()
            try:pool.wait(timeout=10)
            except Exception:
                pool.close()
                raise StorageError('Cannot prepare PostgreSQL connections.') from None
            self._pool=pool

    def close_pool(self):
        with self._pool_lock:
            pool,self._pool=self._pool,None
        if pool is not None:pool.close()

    def drain_pool(self):
        # Restore replaces rows in the same schema, without replacing tables.
        # Discard idle sessions and any returned borrowed session afterwards.
        with self._pool_lock:
            if self._pool is not None:self._pool.drain()

    @contextmanager
    def _raw_connection(self):
        pool=self._pool
        if pool is None:
            raw=self._connect()
            try:yield raw
            finally:raw.close()
        else:
            from psycopg_pool import PoolTimeout,PoolClosed,TooManyRequests
            try:raw=pool.getconn(timeout=10)
            except (PoolTimeout,PoolClosed,TooManyRequests):
                raise StorageError('PostgreSQL connections are busy or unavailable. Retry later.') from None
            try:yield raw
            finally:pool.putconn(raw)

    def _root_marker(self):
        path=self.root/'.storage-format.json'
        if not path.exists():
            if self.kind=='postgres' and self.path.exists():
                raise StorageError('This data directory already contains SQLite. Use a fresh directory/volume and an encrypted archive to change engines.')
            return None
        try:
            if path.is_symlink():raise ValueError()
            saved=json.loads(path.read_text())
            if saved.get('format')!=1 or saved.get('engine')!=self.kind or saved.get('encrypted')!=self.cipher.enabled:raise ValueError()
            if not re.fullmatch(r'[a-f0-9]{32}',saved.get('storage_id','')):raise ValueError()
            context='data-root:'+saved['storage_id']+':'+self.kind+':'+str(self.cipher.enabled)
            if self.cipher.decode(saved.get('check'),context)!='drg-data-root-v1':raise ValueError()
            return saved
        except (ValueError,TypeError,AttributeError):
            raise StorageError('Data directory belongs to another storage format, engine or encryption mode. Use a fresh directory and a portable archive.') from None

    def _bind_root(self,raw,metadata,marker):
        if not re.fullmatch(r'[a-f0-9]{32}',metadata.get('storage_id','')):
            raise StorageError('Database has an unsupported storage format marker. Automatic conversion is disabled.')
        if marker:
            if marker.get('storage_id')!=metadata['storage_id']:
                raise StorageError('Data directory belongs to another database. Use a fresh directory/volume and restore a portable archive.')
            return
        files={p.name for p in (self.root/'media').glob('*') if p.is_file()}
        assets={r[0] for r in raw.execute('SELECT id FROM media')}
        if files!=assets:
            raise StorageError('Existing media does not match this database. No files were removed. Use the matching directory or a portable archive.')
        context='data-root:'+metadata['storage_id']+':'+self.kind+':'+str(self.cipher.enabled)
        payload={'format':1,'engine':self.kind,'encrypted':self.cipher.enabled,
                 'storage_id':metadata['storage_id'],'check':self.cipher.encode('drg-data-root-v1',context)}
        return payload

    def _persist_root(self,payload):
        if payload is None:return
        temporary=self.root/('.storage-format.tmp-'+secrets.token_hex(8))
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w') as file:
                json.dump(payload,file);file.flush();os.fsync(file.fileno())
            os.link(temporary,self.root/'.storage-format.json')
            directory=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:temporary.unlink(missing_ok=True)

    def _connect(self):
        if self.kind == 'sqlite':
            if self.cipher.enabled:
                import sqlcipher3
                connection = sqlcipher3.connect(self.path, timeout=10)
                connection.row_factory = sqlcipher3.Row
                connection.execute("PRAGMA key=\"x'" + self.cipher.sqlite_key.hex() + "'\"")
                connection.execute('PRAGMA cipher_memory_security=ON')
            else:
                connection = sqlite3.connect(self.path, timeout=10); connection.row_factory = sqlite3.Row
            try:
                connection.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
                connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA temp_store=MEMORY')
                connection.execute('PRAGMA secure_delete=ON')
            except Exception:
                connection.close()
                raise StorageError('Cannot open SQLite: wrong encryption key/mode, damaged data or an older plaintext database. Automatic conversion is disabled.') from None
            return connection
        import psycopg
        try:
            connection = psycopg.connect(self.url, connect_timeout=10, application_name='drg-space')
            connection.execute('SET search_path TO ' + self.schema + ', pg_catalog')
            return connection
        except psycopg.Error:
            raise StorageError('Cannot connect to PostgreSQL. Check DATABASE_URL, database permissions and TLS settings.') from None

    @contextmanager
    def connection(self):
        lock = _SQLCIPHER_CONNECTIONS if self.kind == 'sqlite' and self.cipher.enabled else nullcontext()
        with lock:
            with self._transaction() as connection:
                yield connection

    @contextmanager
    def _transaction(self):
        with self._raw_connection() as raw:
            try:
                connection=Connection(self,raw)
                yield connection
                if not connection.quota_disabled:
                    from app.quota import finish
                    connection.quota_guard=True
                    finish(connection)
                if connection.realtime_dirty and self.kind=='postgres':
                    from app.multiprocess import notify_transaction
                    notify_transaction(connection)
                barrier=self.redis_cache.write_barrier() if connection.realtime_dirty else nullcontext()
                with barrier:raw.commit()
            except BaseException:
                try:raw.rollback()
                except Exception:raw.close()
                raise

    def initialize(self, create=True):
        marker=self._root_marker()
        raw = self._connect()
        try:
            if self.kind == 'sqlite':
                existing = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            else:
                raw.execute('SELECT pg_advisory_xact_lock(712941583)')
                # PostgreSQL checks database CREATE permission even for IF NOT
                # EXISTS. A prepared owned schema needs only its own privileges.
                if create and not raw.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(self.schema,)).fetchone():
                    raw.execute('CREATE SCHEMA ' + self.schema)
                existing = {r[0] for r in raw.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (self.schema,))}
            if existing and 'storage_metadata' not in existing:
                raise StorageError('Existing database has no current storage format marker. Preserve existing data and prepare a tested deployment migration; automatic conversion is disabled.')
            if not existing and not create:raise StorageError('Initialized database not found.')
            if existing:
                saved = dict(raw.execute('SELECT key,value FROM storage_metadata').fetchall())
                if saved.get('schema') != SCHEMA_HASH or saved.get('encrypted') != str(self.cipher.enabled):
                    raise StorageError('Storage schema or encryption mode differs. Preserve existing data; verify configuration and apply a tested deployment migration when required. Automatic conversion is disabled.')
                if self.cipher.enabled and self.cipher.decode(saved.get('check'), 'storage-check') != 'drg-storage-v1':
                    raise StorageError('Storage key does not match this database.')
            else:
                if self.kind == 'sqlite': raw.executescript(SCHEMA)
                else:
                    for statement in postgres_schema(self.cipher): raw.execute(statement)
                raw.execute('CREATE TABLE storage_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                placeholder = '?' if self.kind == 'sqlite' else '%s'
                saved={'schema': SCHEMA_HASH, 'encrypted': str(self.cipher.enabled), 'storage_id':secrets.token_hex(16),'check': self.cipher.encode('drg-storage-v1', 'storage-check')}
                for key, value in saved.items():
                    raw.execute('INSERT INTO storage_metadata VALUES(' + placeholder + ',' + placeholder + ')', (key, value))
            payload=self._bind_root(raw,saved,marker)
            self.storage_id=saved['storage_id']
            raw.commit()
            self._persist_root(payload)
            if self.kind == 'sqlite':
                raw.execute('PRAGMA journal_mode=WAL'); os.chmod(self.path, 0o600)
            else:
                self.table_oids = {oid: table for oid, table in raw.execute("SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r'", (self.schema,))}
        except BaseException:
            raw.rollback(); raise
        finally: raw.close()

    def physical_bytes(self):
        if self.kind == 'sqlite':
            total=0
            for path in self.root.glob('site.sqlite3*'):
                try:
                    if path.is_file():total+=path.stat().st_size
                except FileNotFoundError:
                    # SQLite can remove WAL/SHM between the existence check and stat.
                    continue
            return total
        with self.connection() as connection:
            return connection.raw.execute("SELECT COALESCE(SUM(pg_total_relation_size(c.oid)),0) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r'", (self.schema,)).fetchone()[0]


def postgres_schema(cipher):
    """Topological table order; all foreign keys can be deferred on bulk restore."""
    tables, indices = {}, []
    for tree in sqlglot.parse(SCHEMA, read='sqlite'):
        if not isinstance(tree, exp.Create): continue
        if tree.args['kind'] == 'TABLE': tables[tree.this.this.name] = tree
        else: indices.append(tree)
    ordered, remaining = [], dict(tables)
    while remaining:
        ready = [name for name, tree in remaining.items() if {r.this.this.name for r in tree.find_all(exp.Reference)} - {name} <= set(ordered)]
        if not ready: raise StorageError('Schema contains an unsupported foreign-key cycle.')
        for name in ready: ordered.append(name); remaining.pop(name)
    for name in ordered:
        tree = tables[name]
        for dtype in tree.find_all(exp.DataType):
            if dtype.this == exp.DataType.Type.INT: dtype.set('this', exp.DataType.Type.BIGINT)
        # Encrypted defaults are added to INSERT values by the adapter; a raw SQL
        # insert cannot accidentally use a plaintext default for a content field.
        for definition in tree.this.expressions:
            if isinstance(definition, exp.ColumnDef) and definition.name in PROTECTED.get(name, set()) and cipher.enabled:
                definition.set('constraints', [c for c in definition.constraints if not isinstance(c.args.get('kind'), exp.DefaultColumnConstraint)])
        sql = tree.sql(dialect='postgres')
        sql = re.sub(r'(REFERENCES \w+ \([^)]*\)(?: ON DELETE (?:CASCADE|SET NULL))?)', r'\1 DEFERRABLE INITIALLY IMMEDIATE', sql)
        if name in ('note_revisions', 'note_comments'):
            sql = sql[:-1] + ', _rowid BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE)'
        yield sql
    for tree in indices:
        sql = tree.sql(dialect='postgres')
        # COALESCE is SQL special syntax and needs expression parentheses here.
        sql = re.sub(r"COALESCE\(space_id, ''\)", "(COALESCE(space_id, ''))", sql)
        yield sql
