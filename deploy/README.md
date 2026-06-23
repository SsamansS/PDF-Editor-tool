# Деплой PDF Editor на pdf.runpro.in

Публикация инструмента как поддомена за существующим nginx на сервере
`13.59.170.121` (Ubuntu 24.04). HTTPS — через уже установленный Certbot.

> ⚠️ **Guardrails (не сломать работающий сайт/API):**
> - НЕ трогать порты 80/443/8080/9000/9001/5432 и сервисы nginx, runpro-bot, postgresql, minio.
> - НЕ редактировать nginx-блок `runpro.in`. Добавляем только **новый** блок `pdf.runpro.in`.
> - Наш процесс слушает только `127.0.0.1:8765` (за nginx), наружу не выставляется.
> - Новые порты в Security Group НЕ открывать (80/443 уже открыты).

## 0. Предусловие: DNS (делает владелец домена)

Создать A-запись у DNS-провайдера runpro.in:

```
pdf.runpro.in.  A  13.59.170.121
```

Проверить распространение (должен вернуть IP сервера):
```bash
dig +short pdf.runpro.in
```
Без этого `certbot` не выпустит сертификат.

## 1. Код и зависимости

```bash
sudo mkdir -p /opt/pdf-editor && sudo chown ubuntu:ubuntu /opt/pdf-editor
git clone <repo-url> /opt/pdf-editor        # или скопировать файлы
cd /opt/pdf-editor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Настройка .env

```bash
cp .env.example .env
# отредактировать: задать PDF_EDITOR_USER / PDF_EDITOR_PASSWORD (включает авторизацию),
# оставить PDF_EDITOR_HOST=127.0.0.1 и PDF_EDITOR_PORT=8765
```

## 3. systemd-сервис

```bash
sudo cp deploy/pdf-editor.service /etc/systemd/system/pdf-editor.service
sudo systemctl daemon-reload
sudo systemctl enable --now pdf-editor
systemctl status pdf-editor          # active (running)
curl -i http://127.0.0.1:8765/        # 401 (если задана авторизация) или 200
```

## 4. nginx-блок поддомена

```bash
sudo cp deploy/nginx-pdf.runpro.in.conf /etc/nginx/sites-available/pdf.runpro.in
sudo ln -s /etc/nginx/sites-available/pdf.runpro.in /etc/nginx/sites-enabled/pdf.runpro.in
sudo nginx -t                         # обязательно: проверка конфигурации
sudo systemctl reload nginx
```

## 5. HTTPS через Certbot

```bash
sudo certbot --nginx -d pdf.runpro.in
# Certbot допишет 443-блок и редирект 80 -> 443
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Проверка

- С другого компьютера открыть `https://pdf.runpro.in` → авторизация → загрузить PDF → правка → скачать.
- Убедиться, что `https://runpro.in` и его `/api/` продолжают работать.

## Обновление версии

```bash
cd /opt/pdf-editor && git pull
venv/bin/pip install -r requirements.txt   # если менялись зависимости
sudo systemctl restart pdf-editor
```
