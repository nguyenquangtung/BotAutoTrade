#!/usr/bin/env bash
set -e

# 1. Update Nginx inside mt5_trading container
docker exec mt5_trading bash -c '
if ! grep -q "location /dashboard/" /etc/nginx/sites-available/default; then
  cat << "EOF" > /tmp/dash_block.conf

  location /dashboard/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
  }
EOF
  sed -i "/listen 3000 default_server;/r /tmp/dash_block.conf" /etc/nginx/sites-available/default
  nginx -t && nginx -s reload
  echo "[OK] Nginx updated with /dashboard/ proxy"
else
  echo "[INFO] Nginx already configured for /dashboard/"
fi
'

# 2. Check if service.py is running; if not, launch it via nohup
docker exec -u abc mt5_trading bash -c '
if ! ps aux | grep -v grep | grep -q "service.py"; then
  echo "[INFO] Starting dashboard/service.py in background..."
  nohup wine python Z:/config/dashboard/service.py > /config/dashboard/stdout.log 2>&1 &
  sleep 3
fi
'

# 3. Test endpoint
docker exec mt5_trading curl -s http://127.0.0.1:8001/health || true
echo "[DONE] Dashboard & Watchdog setup finished."

