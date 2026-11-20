# ============================================================================
#  clients —— 按当前已保存的凭证组装上游客户端
#
#  凭证可以在运行中被设置页修改，所以客户端不在启动时固定，
#  而是每次按需从 SecretStore 读取；Service Account 按 updated_at 缓存 token。
# ============================================================================
from __future__ import annotations

import httpx

from app.config import Config
from app.core import secrets as S
from app.core.secrets import SecretStore
from app.upstream.google import PageSpeed, SearchConsole, ServiceAccount, parse_service_account
from app.upstream.umami import Umami


class NotConfigured(Exception):
    def __init__(self, what: str):
        super().__init__(f"尚未配置：{what}")
        self.what = what


class Clients:
    def __init__(self, cfg: Config, secrets: SecretStore, http: httpx.AsyncClient):
        self.cfg, self.secrets, self.http = cfg, secrets, http
        self._sa: tuple[str, ServiceAccount] | None = None

    def umami(self) -> Umami:
        url, key = self.secrets.get(S.UMAMI_URL), self.secrets.get(S.UMAMI_KEY)
        if not url or not key:
            raise NotConfigured("Umami")
        return Umami(url, key, self.http)

    def gsc(self) -> SearchConsole:
        raw, stamp = self.secrets.get(S.GOOGLE_SA), self.secrets.updated_at(S.GOOGLE_SA)
        if not raw:
            raise NotConfigured("Google Service Account")
        if not self._sa or self._sa[0] != stamp:
            self._sa = (stamp or "", ServiceAccount(parse_service_account(raw), self.http))
        return SearchConsole(self._sa[1], self.cfg.gsc_base, self.cfg.inspect_base)

    def pagespeed(self) -> PageSpeed:
        key = self.secrets.get(S.GOOGLE_KEY)
        if not key:
            raise NotConfigured("Google API Key")
        return PageSpeed(key, self.http, self.cfg.psi_base, self.cfg.crux_base)
