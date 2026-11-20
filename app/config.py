# ============================================================================
#  config —— 只读的进程级配置，全部来自环境变量
#
#  这里只放「登录之前就必须知道」的东西：OIDC、会话密钥、数据库路径、上游地址。
#  Umami / Google 凭证属于业务配置，存在数据库里，由引导流程写入。
# ============================================================================
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载：已存在的环境变量优先，不覆盖。"""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Config:
    base_url: str
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    secret_key: str
    db_path: str
    # ---- 上游地址：生产用默认值，测试指向本地假服务 ----
    gsc_base: str = "https://www.googleapis.com/webmasters/v3"
    inspect_base: str = "https://searchconsole.googleapis.com/v1"
    psi_base: str = "https://www.googleapis.com/pagespeedonline/v5"
    crux_base: str = "https://chromeuxreport.googleapis.com/v1"
    scheduler: bool = True

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @classmethod
    def from_env(cls) -> Config:
        _load_dotenv(Path(os.environ.get("SM_ENV_FILE", ".env")))
        env = os.environ
        missing = [
            k
            for k in ("SM_OIDC_ISSUER", "SM_OIDC_CLIENT_ID", "SM_OIDC_CLIENT_SECRET", "SM_SECRET_KEY")
            if not env.get(k)
        ]
        if missing:
            raise RuntimeError(f"缺少环境变量: {', '.join(missing)}（见 .env.example）")
        defaults = cls.__dataclass_fields__
        return cls(
            base_url=env.get("SM_BASE_URL", "http://localhost:8700").rstrip("/"),
            oidc_issuer=env["SM_OIDC_ISSUER"].rstrip("/"),
            oidc_client_id=env["SM_OIDC_CLIENT_ID"],
            oidc_client_secret=env["SM_OIDC_CLIENT_SECRET"],
            secret_key=env["SM_SECRET_KEY"],
            db_path=env.get("SM_DB_PATH", "./data/site-monitor.db"),
            gsc_base=env.get("SM_GSC_BASE", defaults["gsc_base"].default),
            inspect_base=env.get("SM_INSPECT_BASE", defaults["inspect_base"].default),
            psi_base=env.get("SM_PSI_BASE", defaults["psi_base"].default),
            crux_base=env.get("SM_CRUX_BASE", defaults["crux_base"].default),
            scheduler=env.get("SM_SCHEDULER", "1") != "0",
        )
