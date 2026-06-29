#!/usr/bin/env bash
# Перезапуск staging-сервиса на текущем коде + health-check.
# Запускается НА сервере (CI или вручную) ПОСЛЕ того, как рабочее дерево уже
# обновлено до нужного коммита (git reset делает вызывающий — workflow), чтобы не
# переписывать этот скрипт во время его же выполнения.
#
# Ручной запуск:
#   cd /opt/pdf-editor-staging && git fetch origin main && git reset --hard origin/main \
#     && /bin/bash deploy/staging-deploy.sh
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

APP=/opt/pdf-editor-staging
cd "$APP"

echo "deploy commit: $(git rev-parse --short HEAD)"

# Зависимости (idempotent — быстро, если ничего не менялось)
"$APP/venv/bin/pip" install -q -r requirements.txt

# Рестарт только staging-сервиса (разрешён в /etc/sudoers.d/pdf-editor-staging)
sudo systemctl restart pdf-editor-staging
sleep 2

state=$(systemctl is-active pdf-editor-staging || true)
echo "service: $state"
[ "$state" = "active" ] || { echo "ОШИБКА: сервис не active"; journalctl -u pdf-editor-staging -n 30 --no-pager || true; exit 1; }

# Health-check: с включённой авторизацией ждём 401 (или 200, если auth выключена)
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/ || echo 000)
echo "staging health: HTTP $code"
[ "$code" = "401" ] || [ "$code" = "200" ] || { echo "ОШИБКА health-check"; exit 1; }

echo "staging deploy OK"
