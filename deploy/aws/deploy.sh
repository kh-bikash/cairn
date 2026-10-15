#!/bin/bash
# Roll out a release on the VM from S3.   deploy.sh <bucket> [--reseed]
#   release/cairn-release.tar.gz  source + prebuilt web (required)
#   release/cairn.env             runtime configuration and secrets (required)
#   release/dump.rdb              graph data; restored on first deploy, or with --reseed
#   release/llm-cache.tar.gz      cached model responses (optional)
set -euo pipefail
BUCKET="$1"
RESEED="${2:-}"
APP=/opt/cairn
DATA=/opt/cairn-data
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file .env)
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ap-south-1}"

test -f /var/lib/cairn-provisioned || { echo "host not provisioned yet"; exit 1; }
mkdir -p "$DATA/falkordb" "$DATA/api"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

aws s3 cp --quiet "s3://$BUCKET/release/cairn-release.tar.gz" "$work/release.tar.gz"
rm -rf "$APP.new" && mkdir -p "$APP.new"
tar -xzf "$work/release.tar.gz" -C "$APP.new"
aws s3 cp --quiet "s3://$BUCKET/release/cairn.env" "$APP.new/.env"
chmod 600 "$APP.new/.env"

if [ "$RESEED" = "--reseed" ] || [ ! -f "$DATA/falkordb/dump.rdb" ]; then
  if aws s3 ls "s3://$BUCKET/release/dump.rdb" > /dev/null 2>&1; then
    if [ -d "$APP" ]; then (cd "$APP" && "${COMPOSE[@]}" stop falkordb api || true); fi
    aws s3 cp --quiet "s3://$BUCKET/release/dump.rdb" "$DATA/falkordb/dump.rdb"
    echo "graph data restored from release"
  fi
fi
if aws s3 ls "s3://$BUCKET/release/llm-cache.tar.gz" > /dev/null 2>&1; then
  aws s3 cp --quiet "s3://$BUCKET/release/llm-cache.tar.gz" "$work/llm-cache.tar.gz"
  tar -xzf "$work/llm-cache.tar.gz" -C "$DATA/api"
fi
chown -R 999:999 "$DATA/falkordb"     # falkordb user in the image
chown -R 10001:10001 "$DATA/api"      # cairn user in the API image

rm -rf "$APP.old"
if [ -d "$APP" ]; then mv "$APP" "$APP.old"; fi
mv "$APP.new" "$APP"
cd "$APP"
"${COMPOSE[@]}" up -d --build --remove-orphans
docker image prune -f > /dev/null

for i in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' cairn-api-1 2>/dev/null)" = "healthy" ]; then
    echo "api healthy"
    "${COMPOSE[@]}" ps
    exit 0
  fi
  sleep 5
done
echo "api did not become healthy"; "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail 80 api; exit 1
