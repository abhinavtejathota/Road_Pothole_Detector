#!/usr/bin/env bash
# Install SmartRoad multi-service nginx site (public :5005 → portal/upload/detect).
#
# Run on AceCloud:
#   sudo bash scripts/install_nginx_smartroad.sh
#
set -euo pipefail

SITE_AVAIL="/etc/nginx/sites-available/smartroad"
SITE_ENAB="/etc/nginx/sites-enabled/smartroad"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root → sudo bash scripts/install_nginx_smartroad.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y nginx
fi

cat >"$SITE_AVAIL" <<'EOF'
upstream smartroad_portal { server 127.0.0.1:5015; }
upstream smartroad_upload { server 127.0.0.1:5006; }
upstream smartroad_detect { server 127.0.0.1:5007; }

server {
    listen 5005;
    server_name _;

    client_max_body_size 4G;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_connect_timeout 60s;

    location /api/upload/ {
        proxy_pass http://smartroad_upload;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Access-Token $http_x_access_token;
        proxy_set_header X-Client $http_x_client;
        proxy_set_header X-Capture-Session-Id $http_x_capture_session_id;
        proxy_set_header X-Chunk-Index $http_x_chunk_index;
        proxy_set_header X-Lat $http_x_lat;
        proxy_set_header X-Lon $http_x_lon;
        proxy_set_header X-Accuracy $http_x_accuracy;
        proxy_request_buffering off;
    }

    location /api/detection/ {
        proxy_pass http://smartroad_detect;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Cookie $http_cookie;
        proxy_read_timeout 3600s;
    }

    location /api/model-bench/ {
        proxy_pass http://smartroad_detect;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Cookie $http_cookie;
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://smartroad_portal;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Access-Token $http_x_access_token;
        proxy_set_header X-Client $http_x_client;
        proxy_set_header Cookie $http_cookie;
    }
}
EOF

ln -sf "$SITE_AVAIL" "$SITE_ENAB"
rm -f /etc/nginx/sites-enabled/default

# Free :5005 if old monolith Flask/waitress is still bound there
fuser -k 5005/tcp 2>/dev/null || true
sleep 1

nginx -t

systemctl enable nginx
# reload fails when nginx was never started — start then reload
if systemctl is-active --quiet nginx; then
  systemctl reload nginx
else
  systemctl start nginx
fi

echo
echo "OK  nginx listening on :5005 → 5015/5006/5007"
echo "Next (as app user, from repo root):"
echo "  ./scripts/services.sh start"
echo "  ./scripts/services.sh smoke"
