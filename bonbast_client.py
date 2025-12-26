import re
import time
from typing import Any, Dict, Optional

import httpx


class BonbastClient:
    """
    Bonbast exposes data via POST /json with a changing 'param' value embedded in homepage HTML/JS.
    We fetch the homepage, extract param, then fetch /json.
    """

    BASE_URL = "https://bonbast.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) bonbast-bot/1.0",
                "Accept": "*/*",
                "Referer": self.BASE_URL + "/",
            },
            follow_redirects=True,
        )
        self._param: Optional[str] = None
        self._param_ts: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_param(self, force: bool = False) -> str:
        # Cache param for a while; refresh on failure.
        if not force and self._param and (time.time() - self._param_ts) < 3600:
            return self._param

        r = await self._client.get("/")
        r.raise_for_status()
        html = r.text

        # Typical patterns in the page:
        # $.post('/json', {param:"...."}, ...
        m = re.search(r"\.post\(\s*['\"]\/json['\"]\s*,\s*\{\s*param\s*:\s*['\"]([^'\"]+)['\"]", html)
        if not m:
            # Alternate pattern fallback
            m = re.search(r"param\s*:\s*['\"]([^'\"]+)['\"]", html)
        if not m:
            raise RuntimeError("Could not extract bonbast param from homepage")

        self._param = m.group(1)
        self._param_ts = time.time()
        return self._param

    async def fetch(self) -> Dict[str, Any]:
        param = await self._get_param(force=False)

        # Bonbast expects form-encoded POST
        r = await self._client.post("/json", data={"param": param})
        if r.status_code >= 400:
            # refresh param once and retry
            param = await self._get_param(force=True)
            r = await self._client.post("/json", data={"param": param})

        r.raise_for_status()

        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected bonbast /json response")
        return data
