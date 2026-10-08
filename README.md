# smtp-code-service (FlashMail)

一个轻量的临时邮箱与 SMTP 接码服务，使用 Python 编写，集成 SMTP 接收、SQLite 存储、Web 邮件查看页面与 HTTP API。

---

## 目录结构

```text
smtp-code-service/
├── app/
│   ├── __init__.py
│   ├── main.py              # 服务启动入口
│   ├── config.py            # 配置管理与参数解析
│   ├── smtp_receiver.py     # SMTP 邮件接收处理
│   ├── mail_parser.py       # 邮件解析与验证码提取
│   ├── storage.py           # SQLite 数据存储
│   └── api.py               # HTTP API 与 Web 路由
├── web/
│   ├── index.html           # 前端页面结构
│   ├── style.css            # 页面样式（支持暗色/浅色主题）
│   └── app.js               # 前端交互逻辑
├── tests/
│   ├── test_mail_parser.py  # 邮件解析单元测试
│   └── test_api.py          # HTTP API 测试
├── .env.example             # 环境变量示例
├── .gitignore
├── Dockerfile
├── compose.yaml             # Docker Compose 配置文件
├── pyproject.toml           # 项目依赖与打包配置
├── README.md
└── LICENSE                  # MIT 开源协议
```

---

## 功能

- **SMTP 接收**：监听 25 端口接收邮件并持久化到 SQLite 数据库。
- **Web 查看界面**：
  - 邮件列表与正文阅读器。
  - 支持 HTML 渲染与纯文本模式切换。
  - 支持深色 / 浅色模式切换。
  - 自动提取邮件中的 6 位验证码并支持一键复制。
  - 提取正文中的外部链接并折叠展示。
  - 自动轮询刷新与邮箱前缀快速检索。
- **HTTP API**：
  - 生成随机临时邮箱地址。
  - 查询指定邮箱的最新验证码。

---

## 快速运行与部署

### 1. 本地运行

```bash
git clone https://github.com/GaleBird/smtp-code-service.git
cd smtp-code-service

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动服务（需要 root 权限以监听 25 端口）
python -m app.main \
  --domain example.com \
  --api-token your-secret-token \
  --http-port 8081 \
  --smtp-port 25
```

### 2. Docker Compose 运行

```bash
docker compose up -d
```

### 3. Systemd 服务配置

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
ExecStart=/opt/smtp-code-service/.venv/bin/python -m app.main --domain ${DOMAIN} --api-token ${API_TOKEN} --http-host ${HTTP_HOST} --http-port ${HTTP_PORT} --smtp-host ${SMTP_HOST} --smtp-port ${SMTP_PORT} --db-path ${DB_PATH}
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now smtp-code-service
```

---

## 运行测试

```bash
python -m unittest discover -s tests
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

所有受保护 API 通过 `?token=YOUR_API_TOKEN` 或 `Authorization: Bearer YOUR_API_TOKEN` 鉴权。

### 1. 健康检查
- `GET /health`
```json
{"ok": true, "time": "2026-10-08T10:00:00Z"}
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
  "email": "test169600000012345@example.com",
  "name": "test169600000012345",
  "domain": "example.com"
}
```

### 3. 获取最新验证码
- `GET /get-code?token=YOUR_API_TOKEN&email=user@example.com`
- 支持省略后缀：`GET /get-code?token=YOUR_API_TOKEN&email=user`
- 返回：
```json
{
  "mailbox": "user@example.com",
  "code": "718808",
  "subject": "验证码邮件",
  "sender": "service@example.com",
  "received_at": "2026-10-08T10:05:00Z"
}
```

### 4. Web 查看页面
- `GET /?email=user@example.com`：直接打开指定邮箱收件箱。
- `GET /api/ui/messages?mailbox=user@example.com`：获取指定邮箱的消息列表。
- `GET /api/ui/message/{id}`：获取单封邮件完整详情。

---

## License

MIT License
