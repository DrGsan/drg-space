#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python=.venv/bin/python
if [[ ! -x "$python" || ! -d node_modules ]]; then
    echo 'Install the development dependencies from README.md first.' >&2
    exit 1
fi

# The test clients connect through loopback. Never provision services on a
# remote Docker daemon where the published ports would be elsewhere.
endpoint=$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')
case "$endpoint" in
    unix://*) ;;
    *) echo 'This check requires a local Docker daemon on a Unix socket.' >&2; exit 1 ;;
esac
docker info >/dev/null

postgres_image=postgres:17-alpine@sha256:526f86d93c6ea08fd3f1fe5605650cacc781b2c6013169a0ad8f42c7d3d84f32
redis_image=redis:8.8.0-alpine@sha256:9d317178eceac8454a2284a9e6df2466b93c745529947f0cd42a0fa9609d7005
run_dir=$(mktemp -d "${TMPDIR:-/tmp}/drg-extended.XXXXXXXX")
label=drg.extended-run=${run_dir##*.}
postgres=drg-check-pg-${run_dir##*.}
redis=drg-check-redis-${run_dir##*.}

cleanup() {
    local result=$? containers
    trap - EXIT
    if containers=$(docker ps -aq --filter "label=$label"); then
        if [[ -n "$containers" ]]; then
            while IFS= read -r container; do
                docker rm -fv "$container" >/dev/null || result=1
            done <<< "$containers"
        fi
    else
        result=1
    fi
    rm -rf -- "$run_dir" || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for image in "$postgres_image" "$redis_image"; do
    docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
done

export POSTGRES_PASSWORD
POSTGRES_PASSWORD=$("$python" -c 'import secrets; print(secrets.token_hex(24))')
docker run --rm -d --name "$postgres" --label "$label"     --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432     -e POSTGRES_USER=drg_test -e POSTGRES_DB=drg_test -e POSTGRES_PASSWORD "$postgres_image" >/dev/null
docker run --rm -d --name "$redis" --label "$label"     --tmpfs /data -p 127.0.0.1::6379 "$redis_image" redis-server --save '' --appendonly no >/dev/null

for attempt in {1..45}; do
    if docker exec "$postgres" pg_isready -q -h 127.0.0.1 -U drg_test -d drg_test &&
       [[ $(docker exec "$redis" redis-cli ping) == PONG ]]; then
        break
    fi
    if [[ "$attempt" == 45 ]]; then
        echo 'The disposable PostgreSQL or Redis service did not become ready.' >&2
        exit 1
    fi
    sleep 1
done

postgres_endpoint=$(docker port "$postgres" 5432/tcp)
redis_endpoint=$(docker port "$redis" 6379/tcp)
[[ "$postgres_endpoint" == 127.0.0.1:* && "$redis_endpoint" == 127.0.0.1:* ]]
printf 'Extended checks use disposable PostgreSQL and Redis on loopback.\n'

unset DATABASE_URL DATABASE_SCHEMA REDIS_URL FILE_STORAGE FAMILY_PHOTO_URL FAMILY_PHOTO_TOKEN
export PYTHON_DOTENV_DISABLED=1
export DRG_TEST_DATABASE_URL="postgresql://drg_test:$POSTGRES_PASSWORD@$postgres_endpoint/drg_test"
export DRG_STORAGE_TEST_POSTGRES_URL="$DRG_TEST_DATABASE_URL"
export DRG_TEST_REDIS_URL="redis://$redis_endpoint/0"
export DRG_TEST_ENCRYPTION_ENABLED=true
export DRG_INIT_ENV_POSTGRES_IMAGE="$postgres_image"
unset POSTGRES_PASSWORD

"$python" -m pytest -q -ra --basetemp="$run_dir/pytest"
npm run build
npm run test:i18n:browser -- --output "$run_dir/playwright"
