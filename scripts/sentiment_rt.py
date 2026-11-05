# -*- coding: utf-8 -*-
"""全市场情绪·盘中实时: 全市场快照 -> 当日站上5/10/20日线比例 + 涨跌家数 + 成交额
与 sentiment.py 的关系: 本脚本只读状态(昨日收盘的20日收盘序列)做盘中估算,
绝不推进状态、绝不写历史 —— 收盘后仍由 sentiment.py update 锁定当日终值。
输出: data/sentiment_rt.json (points 仅保留当日, 同时刻覆盖, 最多60点)
用法: python sentiment_rt.py   (每10分钟跑一次, 非交易时段自动跳过)
"""
import datetime
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import sentiment as S  # noqa: E402

BJ = ZoneInfo("Asia/Shanghai")
OUT = os.path.join(C.DATA, "sentiment_rt.json")
BOARDS = ("main", "chinext", "star", "bse")


def fetch_rt():
    """[(code, price, 当日成交额, 涨跌幅%)] 全市场实时快照(含北交所)"""
    out, pn = [], 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": S.FS, "fields": "f12,f2,f6,f3",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            v, p = r.get("f2"), r.get("f3")
            if isinstance(v, (int, float)) and v > 0:
                out.append((str(r["f12"]), float(v),
                            float(r["f6"]) if isinstance(r.get("f6"), (int, float)) else 0.0,
                            float(p) if isinstance(p, (int, float)) else None))
        total = int((j.get("data") or {}).get("total") or 0)
        if pn * 100 >= total:
            break
        pn += 1
        if pn > 90:
            break
    return out


def run():
    now = datetime.datetime.now(BJ)
    hhmm = now.strftime("%H:%M")
    if now.weekday() >= 5 or not ("09:20" <= hhmm <= "15:05"):
        C.log("sent_rt: skip (非交易时段 %s)" % hhmm)
        return None
    state = C.load_json(S.STATE) or {}
    rows = fetch_rt()
    if len(rows) < 1000:
        C.log("sent_rt: snapshot rows=%d 异常, 保留旧数据" % len(rows))
        return C.load_json(OUT)

    counters = {b: [0] * 6 for b in BOARDS}
    up = down = flat = 0
    amt = 0.0
    for code, price, ramt, pct in rows:
        bd = S.board_of(code)
        if not bd:
            continue
        amt += ramt
        if pct is not None:
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
        cl = state.get(code)
        if cl is None:            # 新股盘中无状态: 计入涨跌家数, 不计均线
            continue
        newc = cl + [price]
        c = counters[bd]
        for wi, w in enumerate(S.WINDOWS):
            if len(newc) >= w:
                ma = sum(newc[-w:]) / float(w)
                c[wi * 2] += 1
                if price > ma:
                    c[wi * 2 + 1] += 1

    tot = [sum(counters[b][i] for b in BOARDS) for i in range(6)]

    def ratio(c, i):
        return round(c[i + 1] / c[i] * 100, 1) if c[i] else None

    boards = {}
    for name in ("all",) + BOARDS:
        c = tot if name == "all" else counters[name]
        boards[name] = {"n_stocks": c[0], "above5": ratio(c, 0),
                        "above10": ratio(c, 2), "above20": ratio(c, 4)}
    cur = {
        "t": hhmm, "up": up, "down": down, "flat": flat,
        "amt": round(amt / 1e8, 0),
        "a5": boards["all"]["above5"], "a10": boards["all"]["above10"],
        "a20": boards["all"]["above20"],
    }
    old = C.load_json(OUT) or {}
    today = now.date().isoformat()
    if old.get("date") == today:
        pts = [p for p in (old.get("points") or []) if p.get("t") != hhmm]
    else:
        pts = []
    pts.append(cur)
    pts = pts[-60:]
    out = {
        "updated": C.now_str(),
        "date": today,
        "boards": boards,
        "current": cur,
        "points": pts,
        "method": ("盘中实时情绪: 全市场快照(东财)逐股用昨收20日序列+当前价估算"
                   "站上5/10/20日均线比例; 涨/跌/平为当日有成交个股家数; "
                   "成交额为当日累计。收盘终值仍以每日20:30的正式更新为准。"),
    }
    C.save_json(OUT, out)
    C.log("sent_rt: %s up=%d down=%d a20=%s%% amt=%.0f亿"
          % (hhmm, up, down, cur["a20"], cur["amt"]))
    return out


if __name__ == "__main__":
    run()
