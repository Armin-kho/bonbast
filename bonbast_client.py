import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx


BONBAST_BASE = "https://bonbast.com"


def _add_commas_int(n: int) -> str:
    s = f"{n:,}"
    return s


def _format_number(value: Any) -> str:
    """
    Bonbast JSON values are usually numeric or numeric strings.
    We format ints with commas; floats keep decimals.
    """
    if value is None:
        return ""
    if isinstance(value, (int,)):
        return _add_commas_int(value)
    if isinstance(value, float):
        # keep up to 2 decimals if needed
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    s = str(value).strip()
    if s == "":
        return ""
    # strip commas if any then parse
    s2 = s.replace(",", "")
    try:
        if "." in s2:
            f = float(s2)
            return f"{f:,.2f}".rstrip("0").rstrip(".")
        i = int(float(s2))
        return _add_commas_int(i)
    except Exception:
        return s


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


@dataclass(frozen=True)
class ItemDef:
    key: str                 # internal code (e.g. USD, EMAMI, GOL18)
    kind: str                # currency | coin | metal
    name_fa: str
    emoji: str
    sell_key: Optional[str] = None
    buy_key: Optional[str] = None
    single_key: Optional[str] = None


# All items visible in the provided HTML (tables + gold/bitcoin panels)
ITEMS: Dict[str, ItemDef] = {
    # Currencies
    "USD": ItemDef("USD", "currency", "دلار آمریکا", "💵", sell_key="usd1", buy_key="usd2"),
    "EUR": ItemDef("EUR", "currency", "یورو", "💶", sell_key="eur1", buy_key="eur2"),
    "GBP": ItemDef("GBP", "currency", "پوند انگلیس", "💷", sell_key="gbp1", buy_key="gbp2"),
    "CHF": ItemDef("CHF", "currency", "فرانک سوئیس", "🇨🇭", sell_key="chf1", buy_key="chf2"),
    "CAD": ItemDef("CAD", "currency", "دلار کانادا", "🇨🇦", sell_key="cad1", buy_key="cad2"),
    "AUD": ItemDef("AUD", "currency", "دلار استرالیا", "🇦🇺", sell_key="aud1", buy_key="aud2"),
    "SEK": ItemDef("SEK", "currency", "کرون سوئد", "🇸🇪", sell_key="sek1", buy_key="sek2"),
    "NOK": ItemDef("NOK", "currency", "کرون نروژ", "🇳🇴", sell_key="nok1", buy_key="nok2"),
    "RUB": ItemDef("RUB", "currency", "روبل روسیه", "🇷🇺", sell_key="rub1", buy_key="rub2"),
    "THB": ItemDef("THB", "currency", "بات تایلند", "🇹🇭", sell_key="thb1", buy_key="thb2"),
    "SGD": ItemDef("SGD", "currency", "دلار سنگاپور", "🇸🇬", sell_key="sgd1", buy_key="sgd2"),
    "HKD": ItemDef("HKD", "currency", "دلار هنگ‌کنگ", "🇭🇰", sell_key="hkd1", buy_key="hkd2"),
    "AZN": ItemDef("AZN", "currency", "منات آذربایجان", "🇦🇿", sell_key="azn1", buy_key="azn2"),
    "AMD": ItemDef("AMD", "currency", "درام ارمنستان", "🇦🇲", sell_key="amd1", buy_key="amd2"),
    "DKK": ItemDef("DKK", "currency", "کرون دانمارک", "🇩🇰", sell_key="dkk1", buy_key="dkk2"),
    "AED": ItemDef("AED", "currency", "درهم امارات", "🇦🇪", sell_key="aed1", buy_key="aed2"),
    "JPY": ItemDef("JPY", "currency", "ین ژاپن", "🇯🇵", sell_key="jpy1", buy_key="jpy2"),
    "TRY": ItemDef("TRY", "currency", "لیر ترکیه", "🇹🇷", sell_key="try1", buy_key="try2"),
    "CNY": ItemDef("CNY", "currency", "یوان چین", "🇨🇳", sell_key="cny1", buy_key="cny2"),
    "SAR": ItemDef("SAR", "currency", "ریال عربستان", "🇸🇦", sell_key="sar1", buy_key="sar2"),
    "INR": ItemDef("INR", "currency", "روپیه هند", "🇮🇳", sell_key="inr1", buy_key="inr2"),
    "MYR": ItemDef("MYR", "currency", "رینگیت مالزی", "🇲🇾", sell_key="myr1", buy_key="myr2"),
    "AFN": ItemDef("AFN", "currency", "افغانی افغانستان", "🇦🇫", sell_key="afn1", buy_key="afn2"),
    "KWD": ItemDef("KWD", "currency", "دینار کویت", "🇰🇼", sell_key="kwd1", buy_key="kwd2"),
    "IQD": ItemDef("IQD", "currency", "دینار عراق", "🇮🇶", sell_key="iqd1", buy_key="iqd2"),
    "BHD": ItemDef("BHD", "currency", "دینار بحرین", "🇧🇭", sell_key="bhd1", buy_key="bhd2"),
    "OMR": ItemDef("OMR", "currency", "ریال عمان", "🇴🇲", sell_key="omr1", buy_key="omr2"),
    "QAR": ItemDef("QAR", "currency", "ریال قطر", "🇶🇦", sell_key="qar1", buy_key="qar2"),

    # Coins
    "AZADI": ItemDef("AZADI", "coin", "آزادی", "🪙", sell_key="azadi1", buy_key="azadi12"),
    "EMAMI": ItemDef("EMAMI", "coin", "امامی", "🪙", sell_key="emami1", buy_key="emami12"),
    "NIM": ItemDef("NIM", "coin", "نیم", "🪙", sell_key="azadi1_2", buy_key="azadi1_22"),
    "ROB": ItemDef("ROB", "coin", "ربع", "🪙", sell_key="azadi1_4", buy_key="azadi1_42"),
    "GERAMI": ItemDef("GERAMI", "coin", "گرمی", "🪙", sell_key="azadi1g", buy_key="azadi1g2"),

    # Gold / BTC
    "MITHQAL": ItemDef("MITHQAL", "metal", "طلا مثقال", "⚜️", single_key="mithqal"),
    "GOL18": ItemDef("GOL18", "metal", "طلا گرمی", "⚜️", single_key="gol18"),
    "OUNCE": ItemDef("OUNCE", "metal", "طلا اونس", "🌍", single_key="ounce"),
    "BTC": ItemDef("BTC", "metal", "بیت‌کوین", "₿", single_key="bitcoin"),
}

CURRENCY_KEYS_ORDER = [k for k, v in ITEMS.items() if v.kind == "currency"]
COIN_KEYS_ORDER = [k for k, v in ITEMS.items() if v.kind == "coin"]
METAL_KEYS_ORDER = [k for k, v in ITEMS.items() if v.kind == "metal"]


class BonbastClient:
    def __init__(self, base_url: str = BONBAST_BASE, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; bonbast-bot/1.0; +https://example.invalid)",
                "Accept": "text/html,application/json",
            },
        )
        self._param: Optional[str] = None
        self._param_ts: float = 0.0  # when extracted

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_param(self) -> str:
        r = await self._client.get(f"{self.base_url}/")
        r.raise_for_status()
        html = r.text

        m = re.search(r"\$\.post\('/json'\s*,\s*\{param:\s*\"([^\"]+)\"\}", html)
        if not m:
            # tolerate single quotes
            m = re.search(r"\$\.post\('/json'\s*,\s*\{param:\s*'([^']+)'\}", html)
        if not m:
            raise RuntimeError("Could not find Bonbast JSON param in homepage HTML.")
        self._param = m.group(1)
        self._param_ts = time.time()
        return self._param

    async def fetch_json(self) -> Dict[str, Any]:
        # refresh param every ~10 minutes
        if not self._param or (time.time() - self._param_ts) > 600:
            await self._fetch_param()

        assert self._param is not None
        try:
            r = await self._client.post(f"{self.base_url}/json", data={"param": self._param})
            r.raise_for_status()
            return r.json()
        except Exception:
            # retry once with fresh param
            await self._fetch_param()
            r = await self._client.post(f"{self.base_url}/json", data={"param": self._param})
            r.raise_for_status()
            return r.json()

    @staticmethod
    def extract_datetime(json_data: Dict[str, Any]) -> Tuple[str, str]:
        y = str(json_data.get("year", "")).strip()
        m = str(json_data.get("month", "")).strip()
        d = str(json_data.get("day", "")).strip()
        hh = str(json_data.get("hour", "")).strip()
        mm = str(json_data.get("minute", "")).strip()
        # Normalize zero padding where possible
        def z2(s: str) -> str:
            try:
                return f"{int(s):02d}"
            except Exception:
                return s

        date = f"{y}/{z2(m)}/{z2(d)}"
        t = f"{z2(hh)}:{z2(mm)}"
        return date, t

    @staticmethod
    def get_value(item: ItemDef, json_data: Dict[str, Any], mode: str) -> Any:
        if item.single_key:
            return json_data.get(item.single_key)
        if mode == "buy":
            return json_data.get(item.buy_key or "")
        return json_data.get(item.sell_key or "")

    @staticmethod
    def get_value_float(item: ItemDef, json_data: Dict[str, Any], mode: str) -> Optional[float]:
        return _to_float(BonbastClient.get_value(item, json_data, mode))

    @staticmethod
    def fmt_value(item: ItemDef, json_data: Dict[str, Any], mode: str) -> str:
        return _format_number(BonbastClient.get_value(item, json_data, mode))
