#!/usr/bin/env bash
# 财务单据智能风险审核系统 - 腾讯云 Ubuntu 一键部署脚本
# 用法: sudo bash deploy.sh [APP_DIR]
#   APP_DIR 默认 /opt/finance-risk-review；代码需已放置在该目录（git clone 或 scp 上传）
# 环境变量可覆盖:
#   DB_PASS    MySQL 应用账号密码（默认 Atguigu.123）
#   DB_HOST    MySQL 主机（默认 127.0.0.1 = 本机自装；腾讯云数据库填内网 IP）
#   SERVER_NAME Nginx server_name（默认 _，即用 IP 访问）
set -euo pipefail

APP_DIR="${1:-/opt/finance-risk-review}"
DB_PASS="${DB_PASS:-Atguigu.123}"
DB_HOST="${DB_HOST:-127.0.0.1}"
SERVER_NAME="${SERVER_NAME:-_}"
BACKEND_DIR="$APP_DIR/backend"
PORT=8000

if [ ! -f "$BACKEND_DIR/requirements.txt" ]; then
    echo "[ERR] 未在 $APP_DIR 找到项目代码，请先 git clone 或 scp 上传。" >&2
    exit 1
fi

echo "==> [1/8] 安装系统依赖 (python3-venv / mysql-server / nginx / git)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip mysql-server nginx git openssl

if [ "$DB_HOST" = "127.0.0.1" ]; then
    echo "==> [2/8] 启动本机 MySQL 并创建库/账号"
    systemctl enable --now mysql
    mysql <<SQL
CREATE DATABASE IF NOT EXISTS finance_risk CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'atguigu'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON finance_risk.* TO 'atguigu'@'localhost';
FLUSH PRIVILEGES;
SQL
else
    echo "==> [2/8] 使用外部 MySQL（$DB_HOST），请确认 finance_risk 库与账号已由控制台创建"
fi

echo "==> [3/8] 创建 Python 虚拟环境并安装依赖"
cd "$BACKEND_DIR"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip -q
./.venv/bin/pip install -r requirements.txt -q

echo "==> [4/8] 生成 .env（若不存在）"
if [ ! -f "$BACKEND_DIR/.env" ]; then
    JWT_SECRET="$(openssl rand -hex 32)"
    cat > "$BACKEND_DIR/.env" <<EOF
DATABASE_URL=mysql+pymysql://atguigu:${DB_PASS}@${DB_HOST}:3306/finance_risk?charset=utf8mb4
JWT_SECRET=${JWT_SECRET}
JWT_EXPIRE_MINUTES=120
# LLM：AutoDL 算力云 Qwen3-8B（OpenAI 兼容，已接入）；如需换 DeepSeek/其他，改下面三行
# 密钥留空 = auto 模式解析失败回退预制；部署后 vim 填 LLM_API_KEY 再 restart
# AutoDL 公网映射为自签证书；演示环境 LLM_INSECURE_SSL=true 跳过校验（生产对外 API 建议关闭）
LLM_BASE_URL=https://u1132348-8415-715a8889.bjb1.seetacloud.com:8443/v1
LLM_API_KEY=
LLM_MODEL=qwen3-8b
LLM_INSECURE_SSL=true
OCR_API_KEY=
OCR_SECRET_KEY=
OCR_BASE_URL=https://aip.baidubce.com
FILE_STORAGE_PATH=data/uploads
PRESET_PARSE_DIR=demo/preset_parse
EOF
    echo "    已生成 .env（LLM/OCR key 留空 = auto 模式回退预制，稍后可用 vim 编辑补填）"
else
    echo "    .env 已存在，跳过"
fi

echo "==> [5/8] 初始化演示数据（首次执行；会清空并重建 finance_risk 库）"
./.venv/bin/python scripts/seed.py
./.venv/bin/python scripts/verify.py

echo "==> [6/8] 安装 systemd 服务（单进程约束: --workers 1）"
cat > /etc/systemd/system/finance-risk.service <<EOF
[Unit]
Description=Finance Risk Review
After=network.target mysql.service

[Service]
WorkingDirectory=${BACKEND_DIR}
ExecStart=${BACKEND_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${PORT} --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now finance-risk
sleep 3
systemctl status finance-risk --no-pager | head -8 || true

echo "==> [7/8] 配置 Nginx 反向代理（80 端口）"
cat > /etc/nginx/sites-available/finance-risk <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/finance-risk /etc/nginx/sites-enabled/finance-risk
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> [8/8] 健康检查"
sleep 1
curl -sf http://127.0.0.1:${PORT}/health && echo " <- 应用 OK" || echo "[WARN] 应用未响应，请查看 systemctl status finance-risk"
curl -sf http://127.0.0.1/health && echo " <- Nginx OK" || echo "[WARN] Nginx 未响应，请检查 nginx -t"

echo
echo "==== 部署完成 ===="
echo "  应用直连:  http://127.0.0.1:${PORT}"
echo "  外网访问:  http://<服务器公网IP>   （安全组需放行 80/443；域名需 ICP 备案）"
echo "  演示账号:  zhangsan / wangwu / zhaoliu / admin ，密码均 123456"
echo "  服务管理:  sudo systemctl {start,stop,restart,status} finance-risk"
echo "  补填密钥:  vim $BACKEND_DIR/.env 后 sudo systemctl restart finance-risk"
echo "  重放数据:  sudo $BACKEND_DIR/.venv/bin/python $BACKEND_DIR/scripts/seed.py"
