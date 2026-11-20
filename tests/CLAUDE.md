# tests/

测试从不访问真实网络：一个本地假服务模拟所有上游，应用通过 Config 把上游地址指过去。

- `fakes/upstream.py` 假 Pocket ID（真实 RS256 id_token + JWKS + PKCE 校验）、假 Umami、假 Google 全家、假 sitemap。
  数据是确定性的，覆盖每一条机会规则。新增上游行为先改这里。
- `fakes/__init__.py` 在后台线程起真实 HTTP 服务（随机端口）。
- `conftest.py`       夹具：`anon`（未登录）、`user`（走完 OIDC 流程）、`ready`（凭证已配、站点已同步）。
- `unit/`             接口级测试：登录与 Token 边界、引导每一步的失败/成功、数据链路与口径。
- `e2e/test_flow.py`  Playwright 真浏览器：首次登录 → 引导（每步先错后对）→ 同步 → 概览交互 → Token 调 API → 吊销 → 退出。
