#!/usr/bin/env bash
# ============================================================================
#  服务器上更新 site-monitor：拉最新镜像 → 重建容器 → 健康检查
#  用独立的空 DOCKER_CONFIG：镜像是公开的，匿名拉取，不受本机其他 ghcr 登录的影响
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
export DOCKER_CONFIG="$PWD/.docker"
mkdir -p "$DOCKER_CONFIG" data
chown 10001 data   # 容器以 uid 10001 运行，需要能写 SQLite
docker compose pull -q
docker compose up -d --remove-orphans
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8700/healthz >/dev/null 2>&1 && { echo "site-monitor 已就绪：$(docker compose images -q | head -c 12)"; exit 0; }
  sleep 1
done
docker compose logs --tail 50
exit 1
