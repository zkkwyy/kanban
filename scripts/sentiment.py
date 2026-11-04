# -*- coding: utf-8 -*-
"""全市场情绪: 全A股站上5/10/20日均线的比例(%)
- backfill: 全A股 -> 个股日K线收盘价(全史) -> 逐日统计 -> data/raw/sent_days/
            + 增量状态(每股最近20个收盘价) data/state/sent_state.json
- update:   收盘后全市场快照(最新价) + 状态 -> 当日比例(首跑追加)
- 输出: data/sentiment.json
口径: 个股按自身交易日计算均线(停牌股当日不计入分母); 次新股不足窗口天数不计入
      对应窗口分母; 沪深A股(不含北交所); 历史回填仅含当前上市公司。
用法: python sentiment.py backfill|update
"""
import datetime
import os
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import concentration  # noqa: E402  (复用fetch_universe)

DAYS_DIR = os.path.join(C.RAW, "sent_days")
CKPT = os.path.join(C.RAW, "sent_ckpt.pkl")
STATE = os.path.join(C.STATE, "sent_state.json")
OUT = os.path.join(C.DATA, "sentiment.json")
WINDOWS = (5, 10, 20)
START = "2005-01-01"


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
    """收盘后全市场最新价: 返回[(code, price), ...] 仅当日有交易"""
    out, pn = [], 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": concentration.FS, "fields": "f12,f2",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            v = r.get("f2")
            if isinstance(v, (int, float)) and v > 0:
                out.append((str(r["f12"]), float(v)))
        total = int((j.get("data") or {}).get("total") or 0)
        if pn * 100 >= total:
            break
        pn += 1
        if pn > 80:
            break
    return out


def load_days():
    """{date: [t5,a5,t10,a10,t20,a20]}"""
    days = {}
    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                d = fn[:-5]
                v = C.load_json(os.path.join(DAYS_DIR, fn))
                if v and len(v) == 6:
                    days[d] = list(v)
    return days


def build_series(days):
    dates = sorted(days)
    a5, a10, a20, n5 = [], [], [], []
    for d in dates:
        v = days[d]
        n5.append(v[0])
        a5.append(round(v[1] / v[0] * 100, 1) if v[0] else None)
        a10.append(round(v[3] / v[2] * 100, 1) if v[2] else None)
        a20.append(round(v[5] / v[4] * 100, 1) if v[4] else None)
    return {
        "updated": C.now_str(),
        "dates": dates,
        "above5": a5,
        "above10": a10,
        "above20": a20,
        "n_stocks": n5,
        "cur5": a5[-1] if a5 else None,
        "cur10": a10[-1] if a10 else None,
        "cur20": a20[-1] if a20 else None,
        "method": (
            "全A股(沪深, 剔除当日停牌)收盘价站上其自身5/10/20日均线(按个股自身交易日)的比例; "
            "次新股不足窗口天数不计入对应分母; 沪深A股不含北交所; 历史回填仅含当前上市公司。"
            ">80%通常视为超买(普涨过热), <20%为超卖(普跌冰点)。"
        ),
    }


def save_series(days):
    s = build_series(days)
    C.save_json(OUT, s)
    C.log("sentiment: %d days, 5日=%.1f%% 10日=%.1f%% 20日=%.1f%%"
          % (len(s["dates"]), s["cur5"] or -1, s["cur10"] or -1, s["cur20"] or -1))
    return s


# ---------------- 回填 ----------------

def backfill():
    uni = concentration.fetch_universe()
    codes = sorted(uni.keys())
    days = {}
    state = {}
    done = set()
    if os.path.exists(CKPT):
        try:
            with open(CKPT, "rb") as f:
                st = pickle.load(f)
            days = st.get("days") or {}
            state = st.get("state") or {}
            done = set(st.get("done") or [])
            C.log("ckpt resume: %d days, %d stocks in state, %d/%d done"
                  % (len(days), len(state), len(done), len(codes)))
        except Exception as e:  # noqa: BLE001
            C.log("ckpt load fail: %s" % e)
    todo = [c for c in codes if c not in done]
    # 盘中跑回填时, 今日bar未定型 -> 状态里剔除今日, 今晚update会补收盘价
    market_open = datetime.datetime.now().hour < 15

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
                flags = _stock_flags(rows)
                for d, fl in flags:
                    if d < START:
                        continue
                    c = days.get(d)
                    if c is None:
                        c = days[d] = [0, 0, 0, 0, 0, 0]
                    for wi in range(3):
                        if fl[wi] is not None:
                            c[wi * 2] += 1
                            if fl[wi]:
                                c[wi * 2 + 1] += 1
                cl = [r[1] for r in rows]
                if market_open and cl and rows[-1][0] == datetime.date.today().isoformat():
                    cl = cl[:-1]
                state[code] = cl[-20:]
            done.add(code)
            if cnt % 500 == 0:
                C.log("sent kline %d/%d (days=%d)" % (cnt, len(todo), len(days)))
                with open(CKPT, "wb") as f:
                    pickle.dump({"days": days, "state": state, "done": sorted(done)},
                                f, protocol=4)
    with open(CKPT, "wb") as f:
        pickle.dump({"days": days, "state": state, "done": sorted(done)}, f, protocol=4)

    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(DAYS_DIR, fn))
    os.makedirs(DAYS_DIR, exist_ok=True)
    for d in sorted(days):
        C.save_json(os.path.join(DAYS_DIR, d + ".json"), days[d])
    C.log("sent days written: %d" % len(days))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    C.save_json(STATE, state)
    C.log("sent state saved: %d stocks" % len(state))
    save_series(days)
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

    counters = [0, 0, 0, 0, 0, 0]
    seen = set()
    for code, price in rows:
        cl = state.get(code)
        if cl is None:
            continue          # 新股: 稍后用K线补种
        seen.add(code)
        newc = cl + [price]
        for wi, w in enumerate(WINDOWS):
            if len(newc) >= w:
                ma = sum(newc[-w:]) / float(w)
                counters[wi * 2] += 1
                if price > ma:
                    counters[wi * 2 + 1] += 1
        state[code] = newc[-20:]

    # 快照里没状态的新股/缺失股: 拉近30根K线补种再算
    missing = [c for c, _ in rows if c not in seen]

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
                for wi in range(3):
                    if fl[wi] is not None:
                        counters[wi * 2] += 1
                        if fl[wi]:
                            counters[wi * 2 + 1] += 1
                state[code] = [r[1] for r in kl][-20:]
        C.log("sent update: seeded %d missing" % len(missing))

    if not any(counters[wi * 2] for wi in range(3)):
        C.log("sent update: 无有效计数")
        return None
    days[today] = counters
    C.save_json(os.path.join(DAYS_DIR, today + ".json"), counters)
    C.save_json(STATE, state)
    C.log("sent update: %s 5日=%d/%d 20日=%d/%d"
          % (today, counters[1], counters[0], counters[5], counters[4]))
    return save_series(days)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "update"
    if mode == "backfill":
        backfill()
    else:
        update()
