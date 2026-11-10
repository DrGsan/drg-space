# Worker deployment

Gunicorn runs the application with threaded workers. Defaults are one worker and 64 threads. `GUNICORN_WORKERS` accepts 1–16 and `GUNICORN_THREADS` accepts 1–128. Choose values for the actual CPU, memory, database and connection load; a thread count is not a throughput guarantee.

```dotenv
GUNICORN_WORKERS=1
GUNICORN_THREADS=64
```

Supported workers share the same local data volume. Storage locks, maintenance leadership, cache invalidation and live-room coordination use that shared installation state. Independent replicas on different hosts are not supported by this deployment model.

PostgreSQL workers maintain their own connection pools and notification connections. Increasing workers therefore increases both memory and database connections. SQLite retains its own serialization constraints. Optional Redis provides shared coordination and caching without replacing the database or filesystem locks.

Application services initialize after Gunicorn forks. Do not enable `--preload`. The supplied configuration allows long uploads and archives without the default short worker timeout and provides a graceful shutdown interval.

Use the installation's existing Compose project name and all overlay files when updating. Keep the data volume, environment, schema and keys unchanged. Do not run offline storage tools alongside active writers. Validate a new worker setting under representative load and inspect health, memory, connection counts and background operations. See [updates](updates.md) and [storage](data-storage.md).
