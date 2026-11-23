#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python=.venv/bin/python
if [[ ! -x "$python" || ! -d node_modules ]]; then
    echo 'Install the development dependencies from README.md first.' >&2
    exit 1
fi
for tool in initdb pg_ctl createdb; do
    if ! command -v "$tool" >/dev/null; then
        echo "PostgreSQL server tools must be on PATH (missing $tool)." >&2
        exit 1
    fi
done

# Published test ports must be available on this host's loopback interface.
endpoint=$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')
case "$endpoint" in
    unix://*) ;;
    *) echo 'This check requires a local Docker daemon on a Unix socket.' >&2; exit 1 ;;
esac
docker info >/dev/null
npm run build

postgres_image=postgres:17-alpine@sha256:526f86d93c6ea08fd3f1fe5605650cacc781b2c6013169a0ad8f42c7d3d84f32
redis_image=redis:8.8.0-alpine@sha256:9d317178eceac8454a2284a9e6df2466b93c745529947f0cd42a0fa9609d7005
# Keep the Unix socket path short even when TMPDIR has a long path.
run_dir=$(mktemp -d /tmp/drg-extended.XXXXXXXX)
label=drg.extended-run=${run_dir##*.}
redis=drg-check-redis-${run_dir##*.}

cleanup() {
    local result=$? containers
    trap - EXIT
    local postgres_stopped=true
    if [[ -f "$run_dir/postgres/postmaster.pid" ]] &&
       ! pg_ctl -D "$run_dir/postgres" -m fast -w stop >/dev/null; then
        echo "Could not stop the test PostgreSQL under $run_dir." >&2
        postgres_stopped=false
        result=1
    fi
    if containers=$(docker ps -aq --filter "label=$label"); then
        if [[ -n "$containers" ]]; then
            while IFS= read -r container; do
                docker rm -fv "$container" >/dev/null || result=1
            done <<< "$containers"
        fi
    else
        result=1
    fi
    if [[ "$postgres_stopped" == true ]]; then rm -rf -- "$run_dir" || result=1; fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for image in "$postgres_image" "$redis_image"; do
    docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
done

postgres_password=$("$python" -c 'import secrets; print(secrets.token_hex(24))')
printf '%s\n' "$postgres_password" > "$run_dir/postgres-password"
initdb -D "$run_dir/postgres" -U drg_test --auth=scram-sha-256 \
    --pwfile="$run_dir/postgres-password" --no-locale --encoding=UTF8 > "$run_dir/initdb.log" 2>&1 || {
    cat "$run_dir/initdb.log" >&2
    exit 1
}
rm -- "$run_dir/postgres-password"
# A private Unix socket avoids TCP port churn in tests that reconnect often.
pg_ctl -D "$run_dir/postgres" -l "$run_dir/postgres.log" -w \
    -o "-h '' -p 5432 -k '$run_dir'" start >/dev/null || {
    tail -30 "$run_dir/postgres.log" >&2
    exit 1
}
PGPASSWORD="$postgres_password" createdb -h "$run_dir" -p 5432 -U drg_test drg_test

docker run --rm -d --name "$redis" --label "$label" \
    --tmpfs /data -p 127.0.0.1::6379 "$redis_image" redis-server --save '' --appendonly no >/dev/null
for attempt in {1..30}; do
    if [[ $(docker exec "$redis" redis-cli ping) == PONG ]]; then break; fi
    if [[ "$attempt" == 30 ]]; then
        echo 'The disposable Redis service did not become ready.' >&2
        exit 1
    fi
    sleep 1
done
redis_endpoint=$(docker port "$redis" 6379/tcp)
[[ "$redis_endpoint" == 127.0.0.1:* ]]
printf 'Extended checks use disposable PostgreSQL and Redis.\n'

unset DATABASE_URL DATABASE_SCHEMA REDIS_URL FILE_STORAGE FAMILY_PHOTO_URL FAMILY_PHOTO_TOKEN
export PYTHON_DOTENV_DISABLED=1
export DRG_TEST_DATABASE_URL="postgresql://drg_test:$postgres_password@localhost:5432/drg_test?host=$run_dir"
export DRG_STORAGE_TEST_POSTGRES_URL="$DRG_TEST_DATABASE_URL"
export DRG_TEST_REDIS_URL="redis://$redis_endpoint/0"
export DRG_TEST_ENCRYPTION_ENABLED=true
export DRG_INIT_ENV_POSTGRES_IMAGE="$postgres_image"
unset postgres_password

"$python" -m pytest -q -ra --basetemp="$run_dir/pytest"
npm run test:i18n:browser -- --output "$run_dir/playwright"
