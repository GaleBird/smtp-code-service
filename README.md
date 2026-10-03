# 📬 FlashMail (smtp-code-service)

一个现代、轻量、高颜值的自建临时邮箱与 SMTP 接码服务（SMTP + Web UI + HTTP API）。

内置专为开发者与接码场景设计的专业桌面客户端级 Web 查看器，单进程同时提供 SMTP 接收信件、SQLite 持久化与 Web 前端渲染，内存占用仅 **~25 MB**。

---

## ✨ 核心特性

- 🖥️ **现代邮件阅读界面（Modern Mail Client UI）**
  - **Reader Canvas**：沉浸式阅读画布，白底邮件居中排版（最大宽度 920px），超宽屏阅读舒适不拉伸。
  - **沙箱隔离与原生平滑滚动**：采用安全沙箱 iframe 隔离第三方复杂 HTML 邮件，注入专属优化样式，杜绝高度锁死与滚动截断。
  - **纯文本 / HTML 视图一键切换**。
  - **外部链接折叠菜单**：所有邮件内外部链接自动识别并折叠收纳在 Toolbar 浮层中，支持一键安全打开。
- 🌓 **克制深浅主题（Light & Dark Theme）**
  - 严格遵循现代工具系统 Token，深浅模式无缝切换并记住偏好。
  - 拒绝粗暴反色：深色客户端外壳包裹原生白底邮件，浅色模式拒绝全屏惨白。
- ⚡ **极致轻量高效**
  - **单进程纯异步架构**：基于 Python `aiosmtpd` 与 `aiohttp`，无 Celery、无 Redis、无复杂依赖。
  - **极低资源开销**：常驻内存仅 **~25 MB**，待机 CPU **接近 0%**。
  - **零外部前端依赖**：无 Vue/React/Tailwind 等打包产物，完整前端内嵌单个文档，Gzip 传输仅 **~9 KB**，秒级秒开。
- 🔑 **自动化验证码提取**
  - 收到邮件自动智能提取 4~8 位数字/字母验证码。
  - 在列表与详情页中高亮展示，支持一键点击复制（附带丝滑复制状态反馈）。
- 🔌 **RESTful API 驱动**
  - 支持通过 API 动态生成随机临时邮箱。
  - 支持通过 API 轮询最新验证码（配合自动化脚本/测试框架使用）。

---

## 🚀 快速开始

### 方式一：直接运行 (Python)

#### 1. 准备环境
```bash
git clone https://github.com/<your-username>/smtp-code-service.git
cd smtp-code-service

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. 启动服务
```bash
python smtp_code_service.py \
  --domain example.com \
  --api-token your-secret-token \
  --http-port 8081 \
  --smtp-port 25
```

> **注意**：监听标准 SMTP 25 端口需要 root 权限，或使用 `setcap 'cap_net_bind_service=+ep' $(which python3)` 授权。

---

### 方式二：Docker 快速部署

```bash
docker compose up -d
```

---

### 方式三：Systemd 系统服务运行

1. 创建配置文件 `/etc/smtp-code-service.env`：
```ini
DOMAIN=example.com
API_TOKEN=your-secret-token-here
HTTP_HOST=0.0.0.0
HTTP_PORT=8081
SMTP_HOST=0.0.0.0
SMTP_PORT=25
DB_PATH=/var/lib/smtp-code-service/messages.db
```

2. 创建服务单元文件 `/etc/systemd/system/smtp-code-service.service`：
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
RestartSec=3

[Install]
WantedBy=multi-user.target
```

3. 启动并启用开机自启：
```bash
systemctl daemon-reload
systemctl enable --now smtp-code-service
```

---

## 🌐 域名 DNS 配置指南

为了使您的域名能够正常接收外部邮件，需在 DNS 提供商处添加以下解析记录：

| 记录类型 | 主机记录 | 记录值 | 优先级 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| **A** | `mail` | `你的服务器公网IP` | - | 邮件服务器主机名解析 |
| **MX** | `@` | `mail.example.com` | `10` | 指定接收域名的邮件路由 |

---

## 📚 HTTP API 参考

所有受保护接口通过 URL 参数 `?token=YOUR_API_TOKEN` 进行鉴权。

### 1. 健康检查
- **请求**：`GET /health`
- **响应**：
  ```json
  {"ok": true, "time": "2026-10-03T10:00:00Z"}
  ```

### 2. 生成临时邮箱
- **请求**：`POST /api/emails/generate?token=YOUR_API_TOKEN`
- **请求体（可选）**：
  ```json
  {"domain": "example.com", "name": "custom-prefix"}
  ```
- **响应**：
  ```json
  {
    "email": "custom-prefix.a1b2c@example.com",
    "mailbox": "custom-prefix.a1b2c@example.com",
    "domain": "example.com",
    "created_at": "2026-10-03T10:00:00Z"
  }
  ```

### 3. 获取最新验证码
- **请求**：`GET /get-code?token=YOUR_API_TOKEN&email=user@example.com`
- **响应**：
  ```json
  {
    "email": "user@example.com",
    "code": "718808",
    "subject": "验证您的电子邮箱地址",
    "received_at": "2026-10-03T10:05:00Z"
  }
  ```

### 4. 邮件 Web 查看器端点
- `GET /?email=user@example.com`：直接进入 Web 邮件阅读器。
- `GET /api/ui/messages?mailbox=user@example.com`：获取指定邮箱的消息列表。
- `GET /api/ui/message/{id}`：获取单封邮件完整详情（包含 HTML 富文本）。

---

## 📄 License

MIT License
