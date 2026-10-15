#!/usr/bin/env bash
# Exercise the built image with a disposable volume and synthetic credentials.
set -euo pipefail
task_dir=$(mktemp -d)
task_project="study-smoke-$$"
task_image=${1:-study-helper:local}
cleanup() {
  docker compose --env-file /dev/null -p "$task_project" -f "$task_dir/compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$task_dir"
}
trap cleanup EXIT
cat > "$task_dir/compose.yaml" <<EOF
x-app: &app
  image: $task_image
  environment:
    ENVIRONMENT: production
    DEBUG: "false"
    COOKIE_SECURE: "true"
    BASE_URL: https://smoke.example.com
    BOT_TOKEN: "123456:smoke_token_never_used"
    BOT_USERNAME: smoke_test_bot
    SECRET_KEY: smoke-only-random-secret-not-for-real-deployment-123456789
    DATABASE_URL: sqlite:///data/study.db
  read_only: true
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
  tmpfs: [/tmp]
  volumes: [data:/app/data]
services:
  migrate:
    <<: *app
    command: python -m study.manage migrate
  web:
    <<: *app
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 1s
      timeout: 3s
      retries: 20
volumes:
  data:
EOF
task_compose=(docker compose --env-file /dev/null -p "$task_project" -f "$task_dir/compose.yaml")
"${task_compose[@]}" up -d --wait web
"${task_compose[@]}" exec -T web python - <<'PY'
import json, urllib.request, urllib.error
base='http://localhost:8000'
assert json.load(urllib.request.urlopen(base+'/health'))['status']=='ok'
response=urllib.request.urlopen(base+'/login')
assert response.headers['Strict-Transport-Security']=='max-age=31536000'
assert 'Secure' in response.headers['Set-Cookie']
assert 'Войти как первый пользователь' not in response.read().decode()
try:
    urllib.request.urlopen(urllib.request.Request(base+'/auth/debug',data=b''))
except urllib.error.HTTPError as error:
    assert error.code==404
else:
    raise AssertionError('Debug login exposed')
print('HTTP smoke OK')
PY
"${task_compose[@]}" exec -T web python -m study.manage backup /tmp/backup.db
"${task_compose[@]}" exec -T -e DATABASE_URL=sqlite:////tmp/restored.db web python -m study.manage restore /tmp/backup.db --confirm-stopped
"${task_compose[@]}" exec -T -e DATABASE_URL=sqlite:////tmp/restored.db web python -m study.manage check
if "${task_compose[@]}" run --rm --no-deps -e DEBUG=true migrate python -m study.manage check >"$task_dir/unsafe.log" 2>&1; then
  echo 'ERROR: unsafe production configuration accepted' >&2
  exit 1
fi
echo 'Production image smoke OK'
