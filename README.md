# Site Monitor

把 **Umami**（用户来了之后做了什么）和 **Google Search Console**（用户是怎么搜到你的）拼在一起，
按落地页对齐，自动生成「下一步该优化什么」的机会清单。网页给人看，同一套 API 给 AI 用。

- 非品牌搜索单独统计：区分「搜你名字的老用户」和「SEO 带来的新用户」
- 落地页漏斗：搜索展示 → 排名 → 点击 → 访问 → 站内事件 → 失败
- 机会清单：冲前三、冲首页、多页抢词、有需求没竞争力、未收录、失败事件、统计污染
- 每周逐 URL 收录巡检、PageSpeed 测试；变更日志用来对照改动效果
- Pocket ID 单点登录；API 用 Bearer Token

## 快速开始

```bash
cp .env.example .env      # 填 Pocket ID 和 SM_SECRET_KEY
make setup                # 依赖 + 测试浏览器 + git hooks
make dev                  # http://localhost:8700
```

首次登录后会进入引导流程，依次配置并**实时校验**：

1. **Umami**：地址 + API Key
2. **Google Service Account**：JSON 密钥；需在 Search Console → 设置 → 用户和权限里把它的邮箱加为用户
3. **Google API Key**：需启用 PageSpeed Insights API 和 Chrome UX Report API
4. **监控站点**：自动按域名配对 Umami 网站与 Search Console 资源

### Pocket ID

新建一个 OIDC 客户端，回调地址填 `${SM_BASE_URL}/auth/callback`。谁能登录由该客户端的「允许的用户组」决定。

## API

在「设置 → API Token」创建 Token：

```bash
curl -H "Authorization: Bearer smk_..." https://monitor.example.com/api/v1/sites/example.com/opportunities
```

| 接口 | 用途 |
|---|---|
| `GET /api/v1/sites` | 站点列表与同步状态 |
| `GET /api/v1/sites/{site}/summary?range=7d\|28d\|8w\|90d` | 核心指标与环比 |
| `GET /api/v1/sites/{site}/trend?weeks=8` | 按周的品牌/非品牌/匿名展示与排名 |
| `GET /api/v1/sites/{site}/opportunities` | 机会清单；`PATCH` 改状态 |
| `GET /api/v1/sites/{site}/queries?min_impressions=10&brand=false` | 搜索词明细（含所有落地页） |
| `GET /api/v1/sites/{site}/pages` | 落地页漏斗 |
| `GET /api/v1/sites/{site}/index-status` | 逐 URL 收录状态 |
| `GET /api/v1/sites/{site}/health` | PageSpeed 与失败事件 |
| `GET /api/v1/sites/{site}/events?name=convert_fail&props=error_code` | 事件属性分布 |
| `GET/POST /api/v1/sites/{site}/changelog` | 变更日志 |
| `POST /api/v1/sites/{site}/sync?kind=all\|gsc\|inspections\|pagespeed` | 手动同步 |

完整文档：`/api/docs`。Token 只能读数据、触发同步、写变更日志，不能读写凭证或管理 Token。

## 开发与发布

```bash
make ci        # 防泄密扫描 → ruff → 接口测试 + Playwright 端到端 → docker build
```

`git push` 前 pre-push hook 自动执行 `make ci`；通过后 GitHub Actions 再跑一遍，
main 分支构建多架构镜像推送到 `ghcr.io/cherrylover/site-monitor`（公开，无需登录即可拉取）。

## 部署

```bash
# 服务器上（首次）
mkdir -p /root/docker-container/site-monitor && cd $_
curl -O https://raw.githubusercontent.com/CherryLover/site-monitor/main/deploy/docker-compose.yml
curl -O https://raw.githubusercontent.com/CherryLover/site-monitor/main/deploy/update.sh && chmod +x update.sh
vi .env && chmod 600 .env    # 参照 .env.example
./update.sh                  # 之后每次更新也只跑这一句
```

- 服务只监听 `127.0.0.1:8700`，由反向代理（nginx-proxy-manager 等）提供 HTTPS。
- 数据在 `./data/site-monitor.db`；容器以 uid 10001 运行，`update.sh` 会处理目录权限。
- `update.sh` 用独立的空 `DOCKER_CONFIG` 匿名拉取公开镜像，不受本机其他 ghcr.io 登录凭证影响。
