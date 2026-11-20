# site-monitor

Umami（站内行为）+ Google Search Console（搜索表现）+ PageSpeed 的站点监控服务。
一个 Python 进程、一个 SQLite 文件、一个无构建步骤的单页前端。网页和 AI skill 读同一套 `/api/v1`。

## 骨架

```
site-monitor/
├── app/                 应用本体（见 app/CLAUDE.md）
│   ├── main.py          装配：配置 → 存储 → 客户端 → 调度器 → 路由
│   ├── config.py        环境变量（只放登录前必须知道的：OIDC、密钥、DB 路径、上游地址）
│   ├── db.py            SQLite schema + 短连接助手
│   ├── auth/            Pocket ID 登录、会话、Bearer Token
│   ├── upstream/        Umami / Google 的薄客户端，错误统一成 UpstreamError
│   ├── core/            业务：凭证存储、引导校验、同步、调度、报表口径、机会规则
│   ├── api/             HTTP 路由：setup（只认会话）/ sites / data（会话或 Token）
│   └── web/             单页前端（ES modules，无框架无构建）
├── tests/               假上游 + 接口测试 + Playwright 端到端（见 tests/CLAUDE.md）
├── scripts/ci.sh        本地 CI = GitHub Actions 跑的同一个脚本
├── .githooks/pre-push   push 前自动跑 scripts/ci.sh
├── deploy/              服务器上的 docker-compose + update.sh（匿名拉镜像、重建、健康检查）
├── Dockerfile           运行时镜像（uv，非 root，/data 卷）
└── .github/workflows/   测试 → main 推送 ghcr.io/cherrylover/site-monitor
```

## 架构决策

- **配置分两层**：OIDC / 会话密钥走环境变量（鸡生蛋问题：没登录进不了设置页）；Umami、Google 凭证走页面引导流程，校验通过才落库，Fernet 加密存储。
- **单一真相源**：页面不自己算任何数，所有口径（品牌词、时间窗、主机名过滤）都在 `core/report.py`。
- **GSC 落库、Umami 实时**：GSC 有 2–3 天延迟且有配额，每天同步一次；Umami 自建、无配额，查询时实时取（60 秒缓存）。
- **只信逐 URL 巡检**：sitemap 接口的 `indexed` 字段 Google 已不维护，收录状态一律来自 URL Inspection。
- **调度器在进程内**：单进程单文件，不需要 cron / 队列。
- **已处理 = 观察，不是关闭**：问题只在数据证明解决后才算解决；规则窗口仍含修复前数据时机会照常生成，标 watching。

## 开发规范

- `make setup` 一次；`make dev` 开发；`make ci` = push 前必过的全部检查。
- 仓库公开：任何真实凭证都不能进 git，`scripts/ci.sh` 第一步就扫描。
- 新的上游行为先加到 `tests/fakes/upstream.py`，再写代码。
- 每个文件不超过 800 行，每个目录不超过 8 个文件。

## 变更日志

- 2026-11-20 静态资源按内容哈希加版本 + import map：修复 Cloudflare 给 JS/CSS 补 4 小时浏览器缓存导致上线后刷新看不到新界面。
- 2026-11-20 「已处理」改为观察期：每条观察每天用修复前 28 天做基线自动对比，给出 等待数据 / 见效中 / 已验证 / 未见改善 / 复发，永不删除；收录机会 key 改为稳定的 index:sitemap 并快照 URL 范围。
- 2026-11-20 概览局部刷新；设置页二级菜单 + 站点多维表格 + 拖拽排序（sites.position）；国家维度同步 + 市场缺口规则。
- 2026-11-20 部署到 docker-test（/root/docker-container/site-monitor），nginx-proxy-manager 反代 gdata.flyooo.uk。
- 2026-11-19 初版：Pocket ID 登录、四步引导、同步与调度、概览/设置页、API Token、本地 CI + GHCR。
