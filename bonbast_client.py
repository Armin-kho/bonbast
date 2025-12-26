import re
import time
from typing import Any, Dict, Optional

import httpx


class BonbastClient:
    """
    Fast scraper:
      1) GET https://bonbast.com/ and extract the dynamic param used in $.post('/json', {param: "..."} )
      2) POST https://bonbast.com/json with param=...
    """

    HOME_URL = "https://bonbast.com/"
    JSON_URL = "https://bonbast.com/json"

    def __init__(self, timeout_s: float = 12.0) -> None:
        self._token: Optional[str] = None
        self._token_ts: float = 0.0
        self._timeout = timeout_s
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; BonbastBot/1.0; +https://bonbast.com/)",
                "Accept": "text/html,application/json",
            },
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _refresh_token(self) -> str:
        r = await self._client.get(self.HOME_URL)
        r.raise_for_status()
        html = r.text

        # Extract: $.post('/json', {param: "...."}, function(json) { ... })
        m = re.search(r"\.post\(\s*['\"]\/json['\"]\s*,\s*\{param:\s*['\"]([^'\"]+)['\"]", html)
        if not m:
            raise RuntimeError("Could not extract Bonbast /json param token from homepage HTML.")
        self._token = m.group(1)
        self._token_ts = time.time()
        return self._token

    async def _get_token(self) -> str:
        # Token seems to rotate; refresh if older than 10 minutes or missing
        if (not self._token) or (time.time() - self._token_ts > 600):
            return await self._refresh_token()
        return self._token

    @staticmethod
    def _parse_number(v: Any) -> Any:
        # Keep date fields as-is; parse numeric strings with commas
        if isinstance(v, str):
            s = v.strip()
            s2 = s.replace(",", "")
            # float?
            if re.fullmatch(r"-?\d+\.\d+", s2):
                try:
                    return float(s2)
                except ValueError:
                    return v
            # int?
            if re.fullmatch(r"-?\d+", s2):
                try:
                    return int(s2)
                except ValueError:
                    return v
        return v

    async def fetch(self) -> Dict[str, Any]:
        token = await self._get_token()

        for attempt in (1, 2):
            try:
                r = await self._client.post(
                    self.JSON_URL,
                    data={"param": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                )
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Bonbast returned non-object JSON.")
                # Site may ask for reset
                if data.get("reset"):
                    # refresh token and retry
                    token = await self._refresh_token()
                    continue

                # parse numbers
                parsed: Dict[str, Any] = {}
                for k, v in data.items():
                    parsed[k] = self._parse_number(v)
                return parsed
            except Exception:
                if attempt == 1:
                    token = await self._refresh_token()
                    continue
                raise

        raise RuntimeError("Unreachable")
