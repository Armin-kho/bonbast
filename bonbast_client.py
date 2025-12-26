import re
import httpx
from typing import Any, Dict, Optional


class BonbastClient:
    """
    Fast + lightweight:
    - fetch homepage
    - extract /json param token from embedded JS
    - POST /json to get values
    """

    HOME_URL = "https://bonbast.com/"
    JSON_URL = "https://bonbast.com/json"

    _param_re = re.compile(r"""\.post\('/json'\s*,\s*\{param:\s*"([^"]+)"\}""")

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            headers={
                "User-Agent": "bonbast-bot/1.0 (+https://bonbast.com/)",
                "Accept": "text/html,application/json",
            },
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=10),
            follow_redirects=True,
        )
        self._cached_param: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_param(self) -> str:
        # cache for speed; refresh if needed
        if self._cached_param:
            return self._cached_param

        r = await self._client.get(self.HOME_URL)
        r.raise_for_status()
        m = self._param_re.search(r.text)
        if not m:
            raise RuntimeError("Could not extract /json param token from bonbast homepage.")
        self._cached_param = m.group(1)
        return self._cached_param

    async def fetch_json(self) -> Dict[str, Any]:
        # param can rotate; if POST fails, refresh token once
        param = await self._get_param()
        try:
            r = await self._client.post(self.JSON_URL, data={"param": param})
            r.raise_for_status()
            return r.json()
        except Exception:
            self._cached_param = None
            param = await self._get_param()
            r = await self._client.post(self.JSON_URL, data={"param": param})
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _as_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None

    @staticmethod
    def get_value_float(item: "Item", json_data: Dict[str, Any], mode: str) -> Optional[float]:
        key = item.sell_key if mode == "sell" else item.buy_key
        return BonbastClient._as_float(json_data.get(key))
