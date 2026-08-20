#!/usr/bin/env sh
set -eu

backup_file=/srv/homeserver/rollback/healthpal-static-20260817-224934.tar.gz
target_dir=/srv/homeserver/compose

if [ ! -f "$backup_file" ]; then
  echo "Rollback archive not found: $backup_file" >&2
  exit 1
fi

cd "$target_dir/healthpal"
sudo systemctl stop healthpal-api.service 2>/dev/null || true
docker compose down
cd "$target_dir"
rm -rf "$target_dir/healthpal"
tar -xzf "$backup_file" -C "$target_dir"
cd "$target_dir/healthpal"
docker compose up -d
echo "Restored the static-only site from $backup_file"
