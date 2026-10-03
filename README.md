# smtp-code-service (FlashMail)

一个轻量的临时邮箱与 SMTP 接码服务，使用 Python 编写，集成 SMTP 接收、SQLite 存储、Web 邮件查看页面与 HTTP API。

---

## 功能

- **SMTP 接收**：监听 25 端口接收邮件并持久化到 SQLite 数据库。
- **Web 查看界面**：
  - 提供简洁的邮件列表与正文阅读器。
  - 支持 HTML 渲染与纯文本切换。
  - 支持深色 / 浅色模式切换。
  - 自动提取邮件中的验证码并支持一键复制。
  - 提取正文中的外部链接并折叠展示。
  - 支持自动轮询刷新。
- **HTTP API**：
  - 生成随机临时邮箱地址。
  - 查询指定邮箱的最新验证码。
- **单文件实现**：核心逻辑集中在 `smtp_code_service.py`，无前端打包步骤，资源占用小。

---

## 运行与部署

### 1. 本地运行

```bash
git clone https://github.com/GaleBird/smtp-code-service.git
cd smtp-code-service

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动服务
python smtp_code_service.py \
  --domain example.com \
  --api-token your-secret-token \
  --http-port 8081 \
  --smtp-port 25
```

> 注：监听标准 SMTP 25 端口需要 root 权限。

### 2. Docker 运行

```bash
docker compose up -d
```

### 3. Systemd 服务

1. 配置文件 `/etc/smtp-code-service.env`：
```ini
DOMAIN=example.com
API_TOKEN=your-secret-token
HTTP_HOST=0.0.0.0
HTTP_PORT=8081
SMTP_HOST=0.0.0.0
SMTP_PORT=25
DB_PATH=/var/lib/smtp-code-service/messages.db
```

2. 服务文件 `/etc/systemd/system/smtp-code-service.service`：
```ini
[Unit]
Description=SMTP Code Service
After=network.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/smtp-code-service.env
ExecStart=/opt/smtp-code-service/.venv/bin/python /opt/smtp-code-service/smtp_code_service.py --domain ${DOMAIN} --api-token ${API_TOKEN} --http-host ${HTTP_HOST} --http-port ${HTTP_PORT} --smtp-host ${SMTP_HOST} --smtp-port ${SMTP_PORT} --db-path ${DB_PATH}
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now smtp-code-service
```

---

## DNS 配置

在域名服务商处添加以下解析记录：

| 类型 | 主机记录 | 记录值 | 优先级 |
| :--- | :--- | :--- | :--- |
| **A** | `mail` | `服务器公网IP` | - |
| **MX** | `@` | `mail.example.com` | `10` |

---

## API 说明

所有 API 通过 `?token=YOUR_API_TOKEN` 传参鉴权。

### 1. 健康检查
- `GET /health`
```json
{"ok": true, "time": "2026-10-03T10:00:00Z"}
```

### 2. 生成临时邮箱
- `POST /api/emails/generate?token=YOUR_API_TOKEN`
- 请求体（可选）：
```json
{"domain": "example.com", "name": "test"}
```
- 返回：
```json
{
  "email": "test.x1y2z@example.com",
  "mailbox": "test.x1y2z@example.com",
  "domain": "example.com"
}
```

### 3. 获取最新验证码
- `GET /get-code?token=YOUR_API_TOKEN&email=user@example.com`
- 返回：
```json
{
  "email": "user@example.com",
  "code": "718808",
  "subject": "验证码邮件",
  "received_at": "2026-10-03T10:05:00Z"
}
```

### 4. Web 查看页面
- `GET /?email=user@example.com`：直接打开该邮箱的查看界面。
- `GET /api/ui/messages?mailbox=user@example.com`：获取指定邮箱的消息列表。
- `GET /api/ui/message/{id}`：获取单封邮件完整详情。

---

## License

MIT License
