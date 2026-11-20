# app/

依赖方向单向：`api → core → upstream`，`auth` 只被 `api` 和 `main` 使用，`web` 只通过 HTTP 访问 `api`。

## auth/
- `oidc.py`   Pocket ID 授权码 + PKCE；从非 SM_BASE_URL 主机进来时先跳转，保证 state cookie 对得上。
- `tokens.py` Bearer Token：`smk_` 前缀，库里只存 SHA-256，明文只在创建时返回一次。
- `deps.py`   `require_user`（只认会话：凭证、Token 管理）与 `require_principal`（会话或 Token：数据接口）。

## upstream/
- `__init__.py` `UpstreamError` + 把 HTTP 状态翻译成给人看的中文原因。
- `umami.py`    Umami REST（Bearer key）。`_get` 的路径参数是位置参数，因为 `path` 也是 Umami 的过滤参数名。
- `google.py`   Service Account → access token（只申请 readonly）、Search Analytics、Sitemaps、URL Inspection、PageSpeed、CrUX。

## core/
- `secrets.py`       凭证 Fernet 加密存储 + 每类凭证最近一次校验结果。
- `clients.py`       按当前已保存凭证组装上游客户端；未配置抛 `NotConfigured`（→ HTTP 409）。
- `setup.py`         引导四步的校验、进度、Umami 网站 × GSC 资源按域名配对。
- `sync.py`          三类作业：gsc（每天，首次 16 个月）、inspections（每周）、pagespeed（每周）；结果写 jobs 表。
- `scheduler.py`     进程内调度，每 10 分钟检查一次到期作业；同一 (站点, 作业) 同时只跑一份。
- `report.py`        所有口径：品牌/非品牌/匿名拆分、按周趋势、搜索词、落地页、Umami 实时概览。
- `opportunities.py` 信号 → 动作的规则表；状态按稳定 key 存 opp_state。
- `markets.py`       市场缺口：国家 → 语言汇总非品牌长尾，对比 sitemap 里已有的语言目录。

## api/
- `setup_routes.py` `/api/v1/me`、`/setup/*`、`/tokens`（只认会话）。
- `site_routes.py`  站点增删改、手动同步、作业记录。
- `data_routes.py`  summary / trend / opportunities / queries / pages / index-status / health / events / changelog。

## web/
- `index.html` 应用外壳，`login.html` 登录页（未登录只展示它，用户点按钮才跳 Pocket ID）；`style.css` 设计令牌与组件。
- `ui.js`   请求、转义、格式化、图表；所有数据字符串必须经 `esc()`。
- `app.js`  启动与路由：未完成引导时强制进入 /setup。
- `views/`  `setup.js`（引导 + 可复用的凭证表单）、`dashboard.js`（概览：骨架只搭一次，区块按 JSON 局部刷新，同步时轮询作业状态只重绘受影响的区块）、`settings.js`（二级菜单：数据源 / 站点多维表格 + 拖拽排序 / Token 表格）。
