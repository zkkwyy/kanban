# -*- coding: utf-8 -*-
"""韩股涨跌榜: KRX主要个股 + KOSPI/KOSDAQ指数当日涨跌幅 — Yahoo财经免费数据
用法: python kr_stocks.py
输出: data/kr_stocks.json
"""
import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

KST = ZoneInfo("Asia/Seoul")

KR = [
    # 指数
    ("^KS11", "KOSPI指数", "index"),
    ("^KQ11", "KOSDAQ指数", "index"),
    # 大盘股(按板块近似分组)
    ("005930.KS", "三星电子", "stock"),
    ("000660.KS", "SK海力士", "stock"),
    ("373220.KS", "LG新能源", "stock"),
    ("006400.KS", "三星SDI", "stock"),
    ("051910.KS", "LG化学", "stock"),
    ("207940.KS", "三星生物", "stock"),
    ("068270.KS", "Celltrion", "stock"),
    ("005380.KS", "现代汽车", "stock"),
    ("000270.KS", "起亚汽车", "stock"),
    ("035420.KS", "NAVER", "stock"),
    ("035720.KS", "Kakao", "stock"),
    ("034730.KS", "SK集团", "stock"),
    ("005490.KS", "浦项制铁", "stock"),
    ("105560.KS", "KB金融", "stock"),
    ("055550.KS", "新韩金融", "stock"),
    ("086790.KS", "韩亚金融", "stock"),
    ("017670.KS", "SK电讯", "stock"),
    ("015760.KS", "韩国电力", "stock"),
    ("010950.KS", "S-Oil", "stock"),
    ("329180.KS", "HD现代重工", "stock"),
    ("241560.KS", "斗山山猫", "stock"),
    ("090430.KS", "爱茉莉太平洋", "stock"),
    ("066570.KS", "LG电子", "stock"),
    ("028260.KS", "三星物产", "stock"),
]


def market_phase(now=None):
    """KRX: 9:00-15:30(首尔)盘中, 其余休市(周末休)"""
    now = now or datetime.datetime.now(KST)
    if now.weekday() >= 5:
        return "closed"
    t = now.hour * 60 + now.minute
    return "regular" if 9 * 60 <= t < 15 * 60 + 30 else "closed"


def fetch_one(sym, cn, kind):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s" % sym
    j = C.get_json(url, params={"range": "1d", "interval": "1d"},
                   headers={"Accept": "application/json"})
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        return {"sym": sym, "cn": cn, "kind": kind, "pct": None, "price": None}
    m = res[0].get("meta") or {}
    p = m.get("regularMarketPrice")
    pc = m.get("chartPreviousClose") or m.get("previousClose")
    pct = round((p / pc - 1) * 100, 2) if p and pc else None
    return {"sym": sym, "cn": cn, "kind": kind,
            "pct": pct, "price": p}


def run():
    out_path = os.path.join(C.DATA, "kr_stocks.json")
    phase = market_phase()
    if phase == "closed":
        C.log("kr: skip (休市, phase=closed)")
        return None
    rows = []

    def work(a):
        try:
            return fetch_one(*a)
        except Exception:  # noqa: BLE001
            return {"sym": a[0], "cn": a[1], "kind": a[2],
                    "pct": None, "price": None}

    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(work, KR):
            rows.append(r)
    ok = sum(1 for r in rows if r["pct"] is not None)
    if ok == 0:
        # 全部失败(如国内服务器连不上Yahoo): 保留上次数据, 不写空
        C.log("kr: 0/%d ok, 保留旧数据" % len(rows))
        return C.load_json(out_path)
    now = datetime.datetime.now(KST)
    out = {
        "updated": C.now_str(),
        "updated_kst": now.strftime("%Y-%m-%d %H:%M") + " KST",
        "phase": phase,
        "rows": rows,
        "method": (
            "韩股为KRX主要个股与KOSPI/KOSDAQ指数的当日涨跌幅(Yahoo财经免费数据, "
            "价格为韩元); KRX交易时间9:00-15:30(首尔=北京时间8:00-14:30), 无盘前盘后时段。"
        ),
    }
    C.save_json(out_path, out)
    C.log("kr: %d/%d rows ok, phase=%s" % (ok, len(rows), phase))
    return out


if __name__ == "__main__":
    run()
