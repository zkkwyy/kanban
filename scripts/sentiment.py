# -*- coding: utf-8 -*-
"""全市场情绪: 全A股站上5/10/20日均线的比例(%) — 分板块
板块: 全市场(all=四板块合计) / 主板(main) / 创业板(chinext) / 科创板(star) / 北交所(bse)
- backfill: 全A股+北交所 -> 个股日K线收盘价 -> 逐日分板块统计 -> data/raw/sent_days/
            + 增量状态(每股最近20个收盘价) data/state/sent_state.json
- update:   收盘后全市场快照(最新价) + 状态 -> 当日分板块统计
- 输出: data/sentiment.json
口径: 个股按自身交易日计算均线(停牌股当日不计入分母); 次新股不足窗口天数不计入
      对应窗口分母; 老三板/两网(810xxx等)剔除。
用法: python sentiment.py backfill|update
"""
import datetime
import os
import pickle
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

DAYS_DIR = os.path.join(C.RAW, "sent_days")
TURNDIR = os.path.join(C.RAW, "turnover_days")
CKPT = os.path.join(C.RAW, "sent_ckpt.pkl")
STATE = os.path.join(C.STATE, "sent_state.json")
OUT = os.path.join(C.DATA, "sentiment.json")
TURN_OUT = os.path.join(C.DATA, "turnover.json")
WINDOWS = (5, 10, 20)
START = "2005-01-01"
BOARDS = ("main", "chinext", "star", "bse")
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"   # 沪深A股+北交所(含老三板需过滤)


def board_of(code):
    """main主板(60/00) / chinext创业板(30) / star科创板(68) / bse北交所(43/82/83/87/92)
    返回None=剔除(老三板810xxx等)"""
    if re.match(r"^(43|82|83|87|92)", code):
        return "bse"
    if code.startswith("60") or code.startswith("00"):
        return "main"
    if code.startswith("30"):
        return "chinext"
    if code.startswith("68"):
        return "star"
    return None


def fetch_universe():
    """{code: name} 全A股+北交所(剔除老三板)"""
    out, pn = {}, 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": FS, "fields": "f12,f14",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            c = str(r["f12"])
            if board_of(c):
                out[c] = r.get("f14") or ""
        if len(out) >= int((j.get("data") or {}).get("total") or 0):
            break
        pn += 1
        if pn > 90:
            break
    C.log("sent universe: %d stocks" % len(out))
    return out


def _stock_flags(rows):
    """[[date, close, amount],...] -> [(date, [f5, f10, f20]), ...]
    f=None表示该股当日bars不足对应窗口"""
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    out = []
    s5 = s10 = s20 = 0.0
    n = len(closes)
    for i in range(n):
        c = closes[i]
        s5 += c
        s10 += c
        s20 += c
        if i >= 5:
            s5 -= closes[i - 5]
        if i >= 10:
            s10 -= closes[i - 10]
        if i >= 20:
            s20 -= closes[i - 20]
        fl = [
            (c > s5 / 5.0) if i >= 4 else None,
            (c > s10 / 10.0) if i >= 9 else None,
            (c > s20 / 20.0) if i >= 19 else None,
        ]
        out.append((dates[i], fl))
    return out


def fetch_snapshot_prices():
    """收盘后全市场(含北交所)最新价: [(code, price, 当日成交额元), ...] 仅当日有交易"""
    out, pn = [], 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": FS, "fields": "f12,f2,f6",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            v = r.get("f2")
            if isinstance(v, (int, float)) and v > 0:
                out.append((str(r["f12"]), float(v),
                            float(r["f6"]) if isinstance(r.get("f6"), (int, float)) else 0.0))
        total = int((j.get("data") or {}).get("total") or 0)
        if pn * 100 >= total:
            break
        pn += 1
        if pn > 90:
            break
    return out


def load_days():
    """{date: {board: [t5,a5,t10,a10,t20,a20]}}"""
    days = {}
    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                d = fn[:-5]
                v = C.load_json(os.path.join(DAYS_DIR, fn))
                if v and isinstance(v, dict):
                    days[d] = {b: (list(v[b]) if v.get(b) else [0] * 6)
                               for b in BOARDS}
    return days


def build_series(days):
    dates = sorted(days)
    names = ("all",) + BOARDS
    agg = {b: {"above5": [], "above10": [], "above20": [], "n_stocks": []}
           for b in names}
    for d in dates:
        v = days[d]
        tot = [sum((v.get(b) or [0] * 6)[i] for b in BOARDS) for i in range(6)]
        for name in names:
            c = tot if name == "all" else (v.get(name) or [0] * 6)
            agg[name]["n_stocks"].append(c[0])
            agg[name]["above5"].append(round(c[1] / c[0] * 100, 1) if c[0] else None)
            agg[name]["above10"].append(round(c[3] / c[2] * 100, 1) if c[2] else None)
            agg[name]["above20"].append(round(c[5] / c[4] * 100, 1) if c[4] else None)
    return {
        "updated": C.now_str(),
        "dates": dates,
        "boards": agg,
        "method": (
            "全A股(沪深主板/创业板/科创板/北交所, 剔除当日停牌)收盘价站上其自身"
            "5/10/20日均线(按个股自身交易日)的比例, 分板块统计; 次新股不足窗口天数"
            "不计入对应分母; 北交所2021年前无数据; >80%通常视为超买, <20%为超卖。"
        ),
    }


def save_series(days):
    s = build_series(days)
    C.save_json(OUT, s)
    last = s["dates"][-1] if s["dates"] else "-"
    a = s["boards"]["all"]
    C.log("sentiment: %d days, all 5日=%s%% 20日=%s%% (%s)"
          % (len(s["dates"]),
             a["above5"][-1] if a["above5"] else None,
             a["above20"][-1] if a["above20"] else None, last))
    return s


# ---------------- 成交金额(与情绪共用同一次个股遍历) ----------------

def load_turnover():
    """{date: {board: 当日成交额(元)}}"""
    days = {}
    if os.path.isdir(TURNDIR):
        for fn in os.listdir(TURNDIR):
            if fn.endswith(".json"):
                v = C.load_json(os.path.join(TURNDIR, fn))
                if v and isinstance(v, dict):
                    days[fn[:-5]] = {b: float(v.get(b) or 0) for b in BOARDS}
    return days


def build_turnover(days):
    """{dates, boards: {all/main/chinext/star/bse: [亿元...]}}"""
    dates = sorted(days)
    names = ("all",) + BOARDS
    agg = {b: [] for b in names}
    for d in dates:
        v = days[d]
        tot = sum((v.get(b) or 0.0) for b in BOARDS)
        for name in names:
            amt = tot if name == "all" else (v.get(name) or 0.0)
            agg[name].append(round(amt / 1e8, 1))     # 亿
    return {
        "updated": C.now_str(),
        "dates": dates,
        "boards": agg,
        "method": ("全A股(含北交所)当日总成交额, 按板块汇总, 单位亿元; "
                   "全市场=主板+创业板+科创板+北交所。"),
    }


def save_turnover(days):
    t = build_turnover(days)
    C.save_json(TURN_OUT, t)
    if t["dates"]:
        i = len(t["dates"]) - 1
        C.log("turnover: %d days, all=%.0f亿 main=%.0f亿 (%s)"
              % (len(t["dates"]), t["boards"]["all"][i],
                 t["boards"]["main"][i], t["dates"][i]))
    return t


# ---------------- 回填 ----------------

def backfill():
    uni = fetch_universe()
    codes = sorted(uni.keys())
    days = {}       # date -> {board: [6]}
    amt = {}        # date -> {board: 当日成交额(元)}
    state = {}      # code -> [last 20 closes]
    done = set()
    if os.path.exists(CKPT):
        try:
            with open(CKPT, "rb") as f:
                st = pickle.load(f)
            days = st.get("days") or {}
            amt = st.get("amt") or {}
            state = st.get("state") or {}
            done = set(st.get("done") or [])
            C.log("ckpt resume: %d days, %d in state, %d/%d done"
                  % (len(days), len(state), len(done), len(codes)))
        except Exception as e:  # noqa: BLE001
            C.log("ckpt load fail: %s" % e)
    todo = [c for c in codes if c not in done]
    market_open = datetime.datetime.now().hour < 15
    today = datetime.date.today().isoformat()

    def work(code):
        try:
            return code, C.fetch_kline(code)
        except Exception:  # noqa: BLE001
            return code, None

    cnt = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, rows in ex.map(work, todo):
            cnt += 1
            if rows:
                bd = board_of(code)
                for (d, _close, ramt), (_d2, fl) in zip(rows, _stock_flags(rows)):
                    if d < START:
                        continue
                    b = days.get(d)
                    if b is None:
                        b = days[d] = {x: [0] * 6 for x in BOARDS}
                        amt[d] = {x: 0.0 for x in BOARDS}
                    c = b[bd]
                    for wi in range(3):
                        if fl[wi] is not None:
                            c[wi * 2] += 1
                            if fl[wi]:
                                c[wi * 2 + 1] += 1
                    amt[d][bd] += ramt
                cl = [r[1] for r in rows]
                if market_open and cl and rows[-1][0] == today:
                    cl = cl[:-1]
                state[code] = cl[-20:]
            done.add(code)
            if cnt % 500 == 0:
                C.log("sent kline %d/%d (days=%d)" % (cnt, len(todo), len(days)))
                with open(CKPT, "wb") as f:
                    pickle.dump({"days": days, "amt": amt, "state": state,
                                 "done": sorted(done)}, f, protocol=4)
    with open(CKPT, "wb") as f:
        pickle.dump({"days": days, "amt": amt, "state": state,
                     "done": sorted(done)}, f, protocol=4)

    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(DAYS_DIR, fn))
    os.makedirs(DAYS_DIR, exist_ok=True)
    for d in sorted(days):
        C.save_json(os.path.join(DAYS_DIR, d + ".json"), days[d])
    if os.path.isdir(TURNDIR):
        for fn in os.listdir(TURNDIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(TURNDIR, fn))
    os.makedirs(TURNDIR, exist_ok=True)
    for d in sorted(amt):
        C.save_json(os.path.join(TURNDIR, d + ".json"), amt[d])
    C.log("sent days written: %d (turnover %d)" % (len(days), len(amt)))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    C.save_json(STATE, state)
    C.log("sent state saved: %d stocks" % len(state))
    save_series(days)
    save_turnover(amt)
    try:
        os.remove(CKPT)
    except OSError:
        pass


# ---------------- 增量 ----------------

def update(tdays=None):
    now = datetime.datetime.now()
    today = now.date().isoformat()
    if now.hour < 15:
        C.log("sent update: skip (未到收盘, hour=%d)" % now.hour)
        return None
    if tdays is not None and today not in tdays:
        C.log("sent update: skip (今日非交易日)")
        return None
    days = load_days()
    if today in days:
        C.log("sent update: skip (%s 已处理, 避免状态重复推进)" % today)
        return None
    state = C.load_json(STATE) or {}
    rows = fetch_snapshot_prices()
    if len(rows) < 1000:
        C.log("sent update: snapshot rows=%d 异常" % len(rows))
        return None

    counters = {b: [0] * 6 for b in BOARDS}
    amt_today = {b: 0.0 for b in BOARDS}
    seen = set()
    for code, price, ramt in rows:
        bd = board_of(code)
        if not bd:
            continue
        amt_today[bd] += ramt
        cl = state.get(code)
        if cl is None:
            continue          # 新股: 稍后用K线补种
        seen.add(code)
        newc = cl + [price]
        c = counters[bd]
        for wi, w in enumerate(WINDOWS):
            if len(newc) >= w:
                ma = sum(newc[-w:]) / float(w)
                c[wi * 2] += 1
                if price > ma:
                    c[wi * 2 + 1] += 1
        state[code] = newc[-20:]

    missing = [c for c, _p, _a in rows if board_of(c) and c not in seen]

    def seed(code):
        try:
            beg = (datetime.date.today() - datetime.timedelta(days=60)).strftime("%Y%m%d")
            return code, C.fetch_kline(code, beg=beg)
        except Exception:  # noqa: BLE001
            return code, None

    if missing:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for code, kl in ex.map(seed, missing):
                if not kl:
                    continue
                # 末根bar即今日(收盘后): 计数并直接用K线末端收盘价入状态
                fl = _stock_flags(kl)[-1][1]
                bd = board_of(code)
                c = counters[bd]
                for wi in range(3):
                    if fl[wi] is not None:
                        c[wi * 2] += 1
                        if fl[wi]:
                            c[wi * 2 + 1] += 1
                state[code] = [r[1] for r in kl][-20:]
        C.log("sent update: seeded %d missing" % len(missing))

    if not any(counters[b][0] for b in BOARDS):
        C.log("sent update: 无有效计数")
        return None
    days[today] = counters
    C.save_json(os.path.join(DAYS_DIR, today + ".json"), counters)
    tdays_amt = load_turnover()
    tdays_amt[today] = amt_today
    C.save_json(os.path.join(TURNDIR, today + ".json"), amt_today)
    C.save_json(STATE, state)
    C.log("sent update: %s main 5日=%d/%d bse=%d只 成交=%.0f亿"
          % (today, counters["main"][1], counters["main"][0], counters["bse"][0],
             sum(amt_today.values()) / 1e8))
    save_turnover(tdays_amt)
    return save_series(days)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "update"
    if mode == "backfill":
        backfill()
    else:
        update()
