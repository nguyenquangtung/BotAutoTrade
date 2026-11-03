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

# 2. Sanity check: service.py loads index.html from its own folder (/config/dashboard).
#    This script does not copy files: deploy the whole dashboard/ folder
#    (service.py, analytics_db.py, metrics.py, index.html) to /config/dashboard first.
docker exec mt5_trading bash -c '
for f in service.py analytics_db.py metrics.py index.html; do
  [ -f "/config/dashboard/$f" ] || echo "[WARN] /config/dashboard/$f is missing - copy the dashboard/ folder first"
done
'

# 3. Check if service.py is running; if not, launch it via nohup.
#    service.py binds to 127.0.0.1:8001 by default (DASHBOARD_HOST/DASHBOARD_PORT env);
#    it is reachable only through the Nginx /dashboard/ proxy above.
#    After updating the Python files, kill the old service.py process so this step restarts it.
docker exec -u abc mt5_trading bash -c '
if ! ps aux | grep -v grep | grep -q "service.py"; then
  echo "[INFO] Starting dashboard/service.py in background..."
  nohup wine python Z:/config/dashboard/service.py > /config/dashboard/stdout.log 2>&1 &
  sleep 3
fi
'

# 4. Test endpoint
docker exec mt5_trading curl -s http://127.0.0.1:8001/health || true
echo "[DONE] Dashboard & Watchdog setup finished."

