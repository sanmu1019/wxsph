# wxsph-api

一个用于解析微信视频号分享链接的轻量 HTTP API。

腾讯元宝 Cookie 仅保存在服务端。调用方只需要提供分享链接，以及在启用鉴权后提供 API Key。

## 功能

- 解析 `https://weixin.qq.com/sph/...` 和 `https://mp.weixin.qq.com/sph/...` 分享链接
- 支持 JSON、表单和查询参数提交链接
- 支持 API Key 鉴权
- 支持带 TTL 的本地缓存
- 提供 Docker 和 Nginx 部署示例

## 公开仓库与公网部署检查

在公开上传或部署前，请确认以下事项：

1. `.env` 仅保存在本机或服务器上，从 `.env.example` 复制生成，不要提交到 git。
2. `WXSPH_COOKIE` 或 `WXSPH_COOKIES` 只能配置在服务端，不能出现在代码、README、日志或仓库提交记录中。
3. 服务对公网开放前必须设置 `WXSPH_API_KEYS`。
4. 建议使用 `X-API-Key` 或 `Authorization: Bearer ...` 传递密钥。`?key=...` 虽然可用，但密钥可能进入访问日志或浏览器历史记录。
5. 仅在服务位于可信反向代理之后时设置 `WXSPH_TRUST_PROXY=true`。启用后，限流会信任 `X-Forwarded-For`。
6. 生产环境保持 `WXSPH_DEBUG=false`。
7. 使用 HTTPS，并通过 Nginx 或其他反向代理对外提供服务。
8. 不要提交 `wxsph_cache.json`、`*.log` 和 `__pycache__/` 等运行文件。
9. 如果希望他人可以复用代码，请在公开前补充 `LICENSE` 文件。

## 接口

- `GET /health`
- `GET /api/wxsph?url=...`
- `POST /api/wxsph`，JSON 请求体示例：`{"url":"https://weixin.qq.com/sph/..."}`

配置 `WXSPH_API_KEYS` 后，可通过以下任一请求头传递密钥：

```text
X-API-Key: your_key
Authorization: Bearer your_key
```

也支持 `?key=...`，但不建议在公网环境使用。

## 本地运行

项目只使用 Python 标准库，不需要安装第三方依赖。

```powershell
Copy-Item .env.example .env
notepad .env
```

至少配置以下内容：

```text
WXSPH_COOKIE=your_yuanbao_cookie
WXSPH_API_KEYS=change_me_to_a_long_random_key
```

不启动服务，直接测试解析：

```powershell
python .\wxsph_api.py --test "https://weixin.qq.com/sph/AdrlNzDbUa"
```

启动服务：

```powershell
python .\wxsph_api.py
```

调用示例：

```powershell
curl.exe -H "X-API-Key: change_me_to_a_long_random_key" "http://127.0.0.1:8787/api/wxsph?url=https%3A%2F%2Fweixin.qq.com%2Fsph%2FAdrlNzDbUa"
```

## Docker 部署

在服务器上执行：

```bash
git clone <你的仓库地址> wxsph-api
cd wxsph-api
cp .env.example .env
nano .env
docker compose up -d --build
```

健康检查：

```bash
curl http://127.0.0.1:8787/health
```

Docker Compose 会将缓存文件保存到 `/app/data/wxsph_cache.json`，对应宿主机项目目录下的 `data/`。
容器端口仅绑定到 VPS 的 `127.0.0.1:8787`，不直接对公网开放；请通过 Nginx 和 HTTPS 访问服务。

## Nginx 反向代理

将 `deploy/nginx.conf.example` 复制到 Nginx 站点配置目录，并修改：

```text
server_name api.example.com;
```

随后重载 Nginx，并使用 Certbot 或服务器面板配置 HTTPS 证书。

公网调用示例：

```bash
curl -H "X-API-Key: your_key" "https://api.example.com/api/wxsph?url=https%3A%2F%2Fweixin.qq.com%2Fsph%2FAdrlNzDbUa"
```

## 环境变量

```text
WXSPH_HOST=0.0.0.0
WXSPH_PORT=8787
WXSPH_TIMEOUT=18
WXSPH_COOKIE=server_side_cookie
WXSPH_COOKIES=cookie_a||cookie_b
WXSPH_API_KEYS=key1,key2
WXSPH_CACHE_TTL=600
WXSPH_CACHE_FILE=wxsph_cache.json
WXSPH_RATE_LIMIT_PER_MIN=30
WXSPH_TRUST_PROXY=false
WXSPH_DEBUG=false
```

`WXSPH_COOKIES` 可使用 `||` 分隔多个 Cookie，服务会按顺序尝试。

## 说明

- 默认缓存文件为 `wxsph_cache.json`。
- Docker 部署时缓存写入 `/app/data`，避免容器重建后丢失缓存。
- 对公网提供服务时，请始终启用 API Key、限流和 HTTPS。
