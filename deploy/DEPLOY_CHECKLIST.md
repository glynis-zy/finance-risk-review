# 腾讯云部署检查清单（财务单据智能风险审核系统）

> 配套文件：`deploy.sh`（一键脚本）/ `finance-risk.service`（systemd 单元模板）/ `nginx-finance-risk.conf`（反代模板）

## 1. 服务器选购

| 项 | 建议 |
|---|---|
| 机型 | 轻量应用服务器或 CVM，**2 核 4G 即可**（CPU 轻量、无 GPU 需求） |
| 镜像 | **Ubuntu 22.04 LTS**（脚本按此验证）；TencentOS/CentOS 需自行适配 apt 包名 |
| 系统盘 | 40G 起；附件存储建议挂独立数据盘并软链到 `backend/data/uploads` |
| 带宽 | 3-5Mbps 起步；演示/小规模足够 |
| 备注 | 若已购 GPU 算力机也不影响，按同一流程部署即可 |

## 2. 安全组放行（腾讯云控制台 → 安全组）

| 端口 | 协议 | 放行对象 | 说明 |
|---|---|---|---|
| 22 | TCP | 你的 IP（或 0.0.0.0/0 临时） | SSH 登录 |
| 80 | TCP | 0.0.0.0/0 | HTTP（Nginx） |
| 443 | TCP | 0.0.0.0/0 | HTTPS（后续证书） |
| 3306 | TCP | **不要放行公网** | 数据库只服务内网/本机 |

> 应用端口 8000 只监听 `127.0.0.1`，无需放行。

## 3. 数据库二选一

- **自装 MySQL（默认）**：脚本自动安装并建 `finance_risk` 库 + `atguigu` 账号。
- **腾讯云云数据库 MySQL**：控制台创建实例后，把 `DB_HOST` 环境变量设为**内网 IP** 传给脚本：
  ```bash
  DB_HOST=10.0.x.x sudo bash deploy.sh
  ```
  （库与账号需先在控制台创建好，密码以 `DB_PASS` 传入保持一致。）

## 4. 部署步骤（上传代码后执行）

```bash
# 1) 上传代码（二选一）
git clone <你的仓库地址> /opt/finance-risk-review          # 私有仓库需配置 token
# 或本地打包：tar czf frr.tgz 后端目录 frontend docs，再 scp 解压到 /opt/finance-risk-review

# 2) 一键部署（约 3-5 分钟）
cd /opt/finance-risk-review/deploy
sudo bash deploy.sh

# 3) 可选：补填 LLM / OCR 密钥后重启
sudo vim /opt/finance-risk-review/backend/.env
sudo systemctl restart finance-risk
```

> 若脚本报 `bad interpreter` 或 `\r` 错误：Windows 上传导致 CRLF，先执行 `sed -i 's/\r$//' deploy.sh`。

## 5. 部署后验证

```bash
curl -s http://127.0.0.1:8000/health          # {"status":"ok"}
curl -s http://127.0.0.1/health               # Nginx 转发 OK
curl -s http://<公网IP>/ | head -3             # 外网可打开前端
sudo systemctl status finance-risk             # active (running)
# 上线回归（会消耗审批待办，先重新 seed 再跑）：
cd /opt/finance-risk-review/backend
.venv/bin/python scripts/seed.py && .venv/bin/python scripts/integration_test.py
```

## 6. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 附件上传 413 | Nginx `client_max_body_size` 过小 → 模板已设 20m |
| 外网打不开页面 | 安全组未放行 80；或域名未备案（腾讯云拦截 80 端口需备案） |
| 500 + `pymysql` 连接失败 | `.env` 的 `DATABASE_URL` 主机/密码不对；云数据库需检查白名单 |
| 服务自动重启循环 | `systemctl status finance-risk` 看日志；多为 `.env` 语法或依赖缺失 |
| 部署脚本连不上公网 | 服务器安全组/网络需能访问公网（装包、调 DeepSeek/百度 OCR 都需要） |

## 7. 生产化提醒（本期刻意不做）

- 进程内异步队列 → 多进程/多实例前需先替换 Celery/RQ（见 DEVLOG 第三轮记录）
- HTTPS：域名备案后可用 certbot 一键签发
- 备份：`mysqldump finance_risk > backup.sql` + 附件目录快照，配 crontab
