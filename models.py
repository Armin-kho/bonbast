from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

Section = Literal["fx", "coins", "markets"]
PriceSide = Literal["sell", "buy"]

RLM = "\u200F"

@dataclass(frozen=True)
class Item:
    item_id: str
    section: Section
    code: str
    name_fa: str
    emoji: str
    sell_key: str
    buy_key: Optional[str]
    kind: Literal["int", "float"]  # formatting
    # Notes like AMD x10, IQD x100 are already reflected by Bonbast keys; we just label name.
    # You can rename freely later.

def _fx(item_id: str, code: str, name_fa: str, emoji: str, sell_key: str, buy_key: str) -> Item:
    return Item(item_id=item_id, section="fx", code=code, name_fa=name_fa, emoji=emoji, sell_key=sell_key, buy_key=buy_key, kind="int")

def _coin(item_id: str, code: str, name_fa: str, sell_key: str, buy_key: str) -> Item:
    return Item(item_id=item_id, section="coins", code=code, name_fa=name_fa, emoji="🪙", sell_key=sell_key, buy_key=buy_key, kind="int")

def _mkt(item_id: str, code: str, name_fa: str, emoji: str, key: str, kind: str) -> Item:
    return Item(item_id=item_id, section="markets", code=code, name_fa=name_fa, emoji=emoji, sell_key=key, buy_key=None, kind=kind)  # buy_key unused

ITEMS: List[Item] = [
    # --- FX (28) ---
    _fx("usd", "USD", "دلار آمریکا", "💵", "usd1", "usd2"),
    _fx("eur", "EUR", "یورو", "💶", "eur1", "eur2"),
    _fx("gbp", "GBP", "پوند انگلیس", "💷", "gbp1", "gbp2"),

    _fx("chf", "CHF", "فرانک سوئیس", "🇨🇭", "chf1", "chf2"),
    _fx("cad", "CAD", "دلار کانادا", "🇨🇦", "cad1", "cad2"),
    _fx("aud", "AUD", "دلار استرالیا", "🇦🇺", "aud1", "aud2"),
    _fx("sek", "SEK", "کرون سوئد", "🇸🇪", "sek1", "sek2"),
    _fx("nok", "NOK", "کرون نروژ", "🇳🇴", "nok1", "nok2"),
    _fx("rub", "RUB", "روبل روسیه", "🇷🇺", "rub1", "rub2"),
    _fx("thb", "THB", "بات تایلند", "🇹🇭", "thb1", "thb2"),
    _fx("sgd", "SGD", "دلار سنگاپور", "🇸🇬", "sgd1", "sgd2"),
    _fx("hkd", "HKD", "دلار هنگ‌کنگ", "🇭🇰", "hkd1", "hkd2"),
    _fx("azn", "AZN", "منات آذربایجان", "🇦🇿", "azn1", "azn2"),
    _fx("amd", "AMD", "درام ارمنستان (۱۰)", "🇦🇲", "amd1", "amd2"),

    _fx("dkk", "DKK", "کرون دانمارک", "🇩🇰", "dkk1", "dkk2"),
    _fx("aed", "AED", "درهم امارات", "🇦🇪", "aed1", "aed2"),
    _fx("jpy", "JPY", "ین ژاپن (۱۰)", "🇯🇵", "jpy1", "jpy2"),
    _fx("try", "TRY", "لیر ترکیه", "🇹🇷", "try1", "try2"),
    _fx("cny", "CNY", "یوان چین", "🇨🇳", "cny1", "cny2"),
    _fx("sar", "SAR", "ریال عربستان", "🇸🇦", "sar1", "sar2"),
    _fx("inr", "INR", "روپیه هند", "🇮🇳", "inr1", "inr2"),
    _fx("myr", "MYR", "رینگیت مالزی", "🇲🇾", "myr1", "myr2"),
    _fx("afn", "AFN", "افغانی افغانستان", "🇦🇫", "afn1", "afn2"),
    _fx("kwd", "KWD", "دینار کویت", "🇰🇼", "kwd1", "kwd2"),
    _fx("iqd", "IQD", "دینار عراق (۱۰۰)", "🇮🇶", "iqd1", "iqd2"),
    _fx("bhd", "BHD", "دینار بحرین", "🇧🇭", "bhd1", "bhd2"),
    _fx("omr", "OMR", "ریال عمان", "🇴🇲", "omr1", "omr2"),
    _fx("qar", "QAR", "ریال قطر", "🇶🇦", "qar1", "qar2"),

    # --- COINS (5) ---
    _coin("coin_azadi", "AZADI", "آزادی", "azadi1", "azadi12"),
    _coin("coin_emami", "EMAMI", "امامی", "emami1", "emami12"),
    _coin("coin_half", "HALF", "نیم", "azadi1_2", "azadi1_22"),
    _coin("coin_quarter", "QUARTER", "ربع", "azadi1_4", "azadi1_42"),
    _coin("coin_gerami", "GERAMI", "گرمی", "azadi1g", "azadi1g2"),

    # --- MARKETS ---
    _mkt("gold_mithqal", "MITHQAL", "طلا مثقال", "⚜️", "mithqal", "int"),
    _mkt("gold_gram", "GOLD18", "طلا گرمی", "⚜️", "gol18", "int"),
    _mkt("gold_ounce", "OUNCE", "طلا اونس", "🌍", "ounce", "float"),
    _mkt("btc", "BTC", "بیت‌کوین", "₿", "bitcoin", "float"),
]

ITEM_BY_ID: Dict[str, Item] = {i.item_id: i for i in ITEMS}
ITEMS_BY_SECTION: Dict[Section, List[Item]] = {
    "fx": [i for i in ITEMS if i.section == "fx"],
    "coins": [i for i in ITEMS if i.section == "coins"],
    "markets": [i for i in ITEMS if i.section == "markets"],
}

SEP = "_______________________"

def format_number(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "float":
        try:
            return f"{float(value):,.2f}"
        except Exception:
            return str(value)
    # int
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)

def arrow(prev, cur) -> str:
    if prev is None or cur is None:
        return ""
    try:
        p = float(prev)
        c = float(cur)
        if c > p:
            return " ▲"
        if c < p:
            return " 🔻"
        return ""
    except Exception:
        return ""

def build_message(
    data: dict,
    selected_fx: List[str],
    selected_coins: List[str],
    selected_markets: List[str],
    price_side: PriceSide,
    last_seen_by_key: Dict[str, float | int | None],
) -> str:
    lines: List[str] = []

    # FX
    for item_id in selected_fx:
        it = ITEM_BY_ID[item_id]
        key = it.sell_key if price_side == "sell" else (it.buy_key or it.sell_key)
        cur = data.get(key)
        prev = last_seen_by_key.get(key)
        a = arrow(prev, cur)
        price = format_number(cur, it.kind)
        # RTL line
        lines.append(RLM + f"{it.emoji} {it.name_fa} {price}{a}")

    if selected_coins:
        lines.append(RLM + SEP)
        for item_id in selected_coins:
            it = ITEM_BY_ID[item_id]
            key = it.sell_key if price_side == "sell" else (it.buy_key or it.sell_key)
            cur = data.get(key)
            prev = last_seen_by_key.get(key)
            a = arrow(prev, cur)
            price = format_number(cur, it.kind)
            lines.append(RLM + f"{it.emoji} {it.name_fa} {price}{a}")

    if selected_markets:
        lines.append(RLM + SEP)
        for item_id in selected_markets:
            it = ITEM_BY_ID[item_id]
            key = it.sell_key
            cur = data.get(key)
            prev = last_seen_by_key.get(key)
            a = arrow(prev, cur)
            price = format_number(cur, it.kind)
            lines.append(RLM + f"{it.emoji} {it.name_fa} {price}{a}")

    lines.append(RLM + SEP)

    # Use Bonbast's own Jalali date/time fields when available
    y = data.get("year")
    m = data.get("month")
    d = data.get("day")
    hh = data.get("hour")
    mm = data.get("minute")
    if all(v is not None for v in (y, m, d, hh, mm)):
        try:
            ts = f"{int(y):04d}/{int(m):02d}/{int(d):02d} - {int(hh):02d}:{int(mm):02d}"
        except Exception:
            ts = f"{y}/{m}/{d} - {hh}:{mm}"
    else:
        ts = "—"
    lines.append(RLM + ts)

    # Update last_seen map
    for it_id in (selected_fx + selected_coins + selected_markets):
        it = ITEM_BY_ID[it_id]
        keys = [it.sell_key]
        if it.buy_key:
            keys.append(it.buy_key)
        for k in keys:
            if k in data:
                last_seen_by_key[k] = data.get(k)

    return "\n".join(lines)
