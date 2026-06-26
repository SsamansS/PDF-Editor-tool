# DEPLOY.md — публикация PDF Editor на https://pdf.runpro.in

Фактический журнал деплоя (выполнен 2026-06-23). Сервис работает как поддомен за
существующим nginx на сервере `13.59.170.121` (Ubuntu 24.04). HTTPS — Let's Encrypt.

- **URL:** https://pdf.runpro.in (HTTP→HTTPS редирект включён)
- **Авторизация:** HTTP Basic Auth, общие логин/пароль из `/opt/pdf-editor/.env`
- **Процесс:** `pdf-editor.service` (systemd) на `127.0.0.1:8765`, проксируется nginx
- **Код:** `/opt/pdf-editor`, venv `/opt/pdf-editor/venv`

> 🚧 **Guardrails (соблюдены):** не тронуты порты 80/443/8080/9000/9001/5432 и сервисы
> nginx, runpro-bot, postgresql, minio. Блок `runpro.in` в nginx не редактировался —
> добавлен только **новый** блок `pdf.runpro.in`. После деплоя проверено: `runpro.in` → 200.

---

## Что было сделано (по шагам)

### 0. Предусловие — DNS
A-запись у Hostinger: `pdf.runpro.in → 13.59.170.121`. Проверка: `nslookup pdf.runpro.in` → IP сервера.

### 1. Код на сервер (без git-push — через архив HEAD)
Локально:
```bash
git archive --format=tar.gz -o /tmp/pdf-editor.tar.gz HEAD
scp -i <key.pem> /tmp/pdf-editor.tar.gz ubuntu@13.59.170.121:/tmp/
```
На сервере:
```bash
sudo mkdir -p /opt/pdf-editor && sudo chown ubuntu:ubuntu /opt/pdf-editor
tar xzf /tmp/pdf-editor.tar.gz -C /opt/pdf-editor
```

### 2. venv + зависимости
```bash
sudo apt-get install -y python3.12-venv      # не был установлен
python3 -m venv /opt/pdf-editor/venv
/opt/pdf-editor/venv/bin/pip install --upgrade pip
/opt/pdf-editor/venv/bin/pip install -r /opt/pdf-editor/requirements.txt
```

### 3. .env (учётки + bind)
`/opt/pdf-editor/.env` (chmod 600):
```
PDF_EDITOR_USER=admin
PDF_EDITOR_PASSWORD=<секрет>
PDF_EDITOR_HOST=127.0.0.1
PDF_EDITOR_PORT=8765
```

### 4. systemd-сервис
```bash
sudo cp /opt/pdf-editor/deploy/pdf-editor.service /etc/systemd/system/pdf-editor.service
sudo systemctl daemon-reload
sudo systemctl enable --now pdf-editor
systemctl is-active pdf-editor                 # active
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/            # 401 (auth on)
curl -o /dev/null -w "%{http_code}" -u admin:*** http://127.0.0.1:8765/   # 200
```

### 5. nginx-блок поддомена
```bash
sudo cp /opt/pdf-editor/deploy/nginx-pdf.runpro.in.conf /etc/nginx/sites-available/pdf.runpro.in
sudo ln -sf /etc/nginx/sites-available/pdf.runpro.in /etc/nginx/sites-enabled/pdf.runpro.in
sudo nginx -t                                  # обязательно перед reload
sudo systemctl reload nginx
```

### 6. HTTPS через Certbot
```bash
sudo certbot --nginx -d pdf.runpro.in --non-interactive --agree-tos -m helpdesk@runpro.us --redirect
# сертификат: /etc/letsencrypt/live/pdf.runpro.in/ , истекает 2026-09-21, авто-renew настроен
```

### 7. Проверка (выполнена, все ✅)
| Проверка | Результат |
|----------|-----------|
| `https://pdf.runpro.in/` без авторизации | `401` + `WWW-Authenticate: Basic` |
| с авторизацией `admin` | `200`, `<title>PDF Editor</title>` |
| `http://pdf.runpro.in/` | `301` → https |
| Сертификат | Let's Encrypt, `CN=pdf.runpro.in`, verify ok |
| Загрузка PDF снаружи (`POST /api/upload`) | `200`, вернулся `doc_id`, 8 страниц |
| `https://runpro.in/` (не задет) | `200` |

---

## Эксплуатация

**Сменить логин/пароль:**
```bash
sudo nano /opt/pdf-editor/.env          # изменить PDF_EDITOR_USER / PDF_EDITOR_PASSWORD
sudo systemctl restart pdf-editor
```

**Обновить версию (после изменений в коде):**
```bash
# локально: git archive HEAD -> scp, или git pull, если будет настроен remote
cd /opt/pdf-editor && tar xzf /tmp/pdf-editor.tar.gz -C /opt/pdf-editor   # перезалить файлы
/opt/pdf-editor/venv/bin/pip install -r requirements.txt                 # если менялись зависимости
sudo systemctl restart pdf-editor
```

**Логи:**
```bash
journalctl -u pdf-editor -f
```

**Откат (полное удаление, без следов на runpro.in):**
```bash
sudo systemctl disable --now pdf-editor
sudo rm /etc/systemd/system/pdf-editor.service && sudo systemctl daemon-reload
sudo rm /etc/nginx/sites-enabled/pdf.runpro.in /etc/nginx/sites-available/pdf.runpro.in
sudo nginx -t && sudo systemctl reload nginx
sudo rm -rf /opt/pdf-editor
# (опц.) сертификат: sudo certbot delete --cert-name pdf.runpro.in
```

**Ограничения:**
- Загрузка PDF ≤ 50 МБ (`client_max_body_size 60m` в nginx + лимит в приложении).
- Загруженные файлы хранятся в `/opt/pdf-editor/_uploads/<doc_id>/` и авто-удаляются через 24ч.
- Авторизация общая (один логин/пароль на всех), не пер-пользовательская.
