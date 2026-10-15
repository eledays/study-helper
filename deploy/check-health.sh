#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE=compose.yaml:compose.prod.yaml
for service in web bot; do
  container=$(docker compose ps -q "$service")
  if [[ -z "$container" ]]; then
    echo "$service: missing" >&2
    exit 1
  fi
  state=$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$container")
  if [[ "$state" != 'running healthy' ]]; then
    echo "$service: $state" >&2
    exit 1
  fi
done
echo 'web and bot: healthy'
