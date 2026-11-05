# -*- coding: utf-8 -*-
"""美股板块/行业ETF 盘前·盘中·盘后涨跌幅 — Yahoo财经免费数据
用法: python us_sectors.py
输出: data/us_sectors.json
"""
import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

NY = ZoneInfo("America/New_York")

ETFS = [
    # 11大板块
    ("XLK", "科技", "sector"), ("XLF", "金融", "sector"),
    ("XLE", "能源", "sector"), ("XLV", "医疗保健", "sector"),
    ("XLY", "可选消费", "sector"), ("XLP", "必需消费", "sector"),
    ("XLI", "工业", "sector"), ("XLB", "原材料", "sector"),
    ("XLRE", "房地产", "sector"), ("XLU", "公用事业", "sector"),
    ("XLC", "通信服务", "sector"),
    # 细分行业
    ("SMH", "半导体", "industry"), ("SOXX", "费城半导体", "industry"),
    ("IGV", "软件", "industry"), ("XBI", "生物科技", "industry"),
    ("IHI", "医疗器械", "industry"), ("KWEB", "中概互联网", "industry"),
    ("XHB", "住宅建筑", "industry"), ("ITB", "房屋建筑", "industry"),
    ("XME", "金属与采矿", "industry"), ("MOO", "农业", "industry"),
    ("TAN", "太阳能", "industry"), ("ICLN", "清洁能源", "industry"),
    ("XOP", "油气开采", "industry"), ("OIH", "油井服务", "industry"),
    ("AMLP", "能源管道", "industry"), ("KRE", "区域银行", "industry"),
    ("KIE", "保险", "industry"), ("XRT", "零售", "industry"),
    ("JETS", "航空", "industry"), ("ITA", "航天与国防", "industry"),
    ("URA", "铀", "industry"), ("LIT", "锂电池", "industry"),
    # 大盘参照
    ("SPY", "标普500", "index"), ("QQQ", "纳斯达克100", "index"),
    ("DIA", "道琼斯", "index"),
]


def market_phase(now=None):
    now = now or datetime.datetime.now(NY)
    if now.weekday() >= 5:
        return "closed"
    t = now.hour * 60 + now.minute
    if 4 * 60 <= t < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= t < 16 * 60:
        return "regular"
    if 16 * 60 <= t < 20 * 60:
        return "post"
    return "closed"


def fetch_one(sym, cn, kind):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s" % sym
    j = C.get_json(url, params={"range": "1d", "interval": "5m",
                                "includePrePost": "true"},
                   headers={"Accept": "application/json"})
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        return {"sym": sym, "cn": cn, "kind": kind, "err": "no data"}
    meta = res[0].get("meta") or {}
    ts = (res[0].get("timestamp") or [])
    quotes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    pre_p = reg_p = post_p = None
    for t, c in zip(ts, closes):
        if c is None:
            continue
        dt = datetime.datetime.fromtimestamp(t, NY)
        m = dt.hour * 60 + dt.minute
        if 4 * 60 <= m < 9 * 60 + 30:
            pre_p = c
        elif 9 * 60 + 30 <= m < 16 * 60:
            reg_p = c
        elif 16 * 60 <= m < 20 * 60:
            post_p = c
    if reg_p is None:
        reg_p = prev_close
    row = {"sym": sym, "cn": cn, "kind": kind, "prev_close": prev_close}
    if pre_p is not None and prev_close:
        row["pre"] = round((pre_p / prev_close - 1) * 100, 2)
        row["pre_price"] = round(pre_p, 2)
    if reg_p is not None and prev_close:
        row["regular"] = round((reg_p / prev_close - 1) * 100, 2)
        row["regular_price"] = round(reg_p, 2)
    if post_p is not None and reg_p:
        row["post"] = round((post_p / reg_p - 1) * 100, 2)
        row["post_price"] = round(post_p, 2)
    return row


def run():
    phase = market_phase()
    if phase == "closed":
        C.log("us_sectors: skip (休市, phase=closed)")
        return None
    rows = []

    def work(t):
        try:
            return fetch_one(*t)
        except Exception as e:  # noqa: BLE001
            return {"sym": t[0], "cn": t[1], "kind": t[2], "err": str(e)[:80]}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(work, ETFS):
            rows.append(r)
    ok = [r for r in rows if "err" not in r]
    out_path = os.path.join(C.DATA, "us_sectors.json")
    if not ok:
        # 全部失败(如国内服务器连不上Yahoo): 保留上次数据, 不写空
        C.log("us_sectors: 0/%d ok, 保留旧数据" % len(rows))
        return C.load_json(out_path)
    now_ny = datetime.datetime.now(NY)
    out = {
        "updated": C.now_str(),
        "updated_ny": now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "phase": phase,
        "rows": rows,
        "method": ("Yahoo财经免费行情; 以行业ETF代理板块涨跌幅; "
                   "盘前=相对昨收, 盘中=最新价相对昨收, 盘后=盘后价相对收盘价; "
                   "流动性较低的ETF盘前盘后可能无成交"),
    }
    C.save_json(out_path, out)
    meta = C.load_json(os.path.join(C.DATA, "meta.json")) or {}
    meta["us_updated"] = out["updated"]
    meta["us_phase"] = phase
    C.save_json(os.path.join(C.DATA, "meta.json"), meta)
    C.log("us_sectors: %d/%d ok, phase=%s"
          % (len(ok), len(rows), phase))
    return out


if __name__ == "__main__":
    run()
