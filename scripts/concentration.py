# -*- coding: utf-8 -*-
"""A股成交集中度: 每日成交额前5%个股合计占全市场成交额的百分比
- backfill: clist全A股 -> 个股日K线成交额(全史) -> 逐日横截面 -> data/raw/conc_days/
- update:   收盘后全市场行情快照(clist f6) -> 当日横截面 -> 覆盖/追加
- 输出: data/concentration.json
口径: 剔除当日停牌(无成交额); 前ceil(5%×当日活跃个股数)只个股成交额合计/全市场;
      沪深A股(不含北交所); 历史回填仅含当前上市股票(退市股缺失, 轻微偏差)。
用法: python concentration.py backfill|update
"""
import datetime
import math
import os
import pickle
import sys
from array import array
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

DAYS_DIR = os.path.join(C.RAW, "conc_days")
CKPT = os.path.join(C.RAW, "conc_ckpt.pkl")
OUT = os.path.join(C.DATA, "concentration.json")
SHARE = 0.05
START = "2005-01-01"
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"   # 沪主板A+科创板+深主板A+创业板(不含北交所)


def fetch_universe():
    """全部A股代码: 返回{code: name}"""
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
            out[str(r["f12"])] = r.get("f14") or ""
        if len(out) >= int((j.get("data") or {}).get("total") or 0):
            break
        pn += 1
        if pn > 80:
            break
    C.log("conc universe: %d stocks" % len(out))
    return out


def fetch_snapshot_amounts():
    """收盘后全市场当日成交额(元): 返回[(code, amount元), ...] 仅活跃"""
    out, pn = [], 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": FS, "fields": "f12,f6",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            v = r.get("f6")
            if isinstance(v, (int, float)) and v > 0:
                out.append((str(r["f12"]), float(v)))
        total = int((j.get("data") or {}).get("total") or 0)
        if pn * 100 >= total:
            break
        pn += 1
        if pn > 80:
            break
    return out


def conc_of(amts_wan):
    """万元列表 -> (活跃数, 总成交万元, 前5%合计万元, 集中度%)"""
    lst = sorted(amts_wan, reverse=True)
    n = len(lst)
    if not n:
        return 0, 0, 0, None
    top_n = max(1, math.ceil(SHARE * n))
    tot = sum(lst)
    if tot <= 0:
        return n, 0, 0, None
    tp = sum(lst[:top_n])
    return n, tot, tp, tp / tot * 100.0


def load_days():
    """从conc_days读取历史: 返回{date: (n, tot万, top万)}"""
    days = {}
    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                d = fn[:-5]
                v = C.load_json(os.path.join(DAYS_DIR, fn))
                if v and len(v) == 3:
                    days[d] = tuple(v)
    return days


def build_series(days):
    """{date:(n,tot,top)} -> 排序序列 + ma20 + 统计"""
    dates = sorted(days)
    conc, ma20, ns = [], [], []
    run = []
    for d in dates:
        n, tot, tp = days[d]
        v = round(tp / tot * 100, 2) if tot > 0 else None
        conc.append(v)
        ns.append(n)
        run.append(v)
        if len(run) > 20:
            run.pop(0)
        ma20.append(round(sum(run) / len(run), 2))
    # 统计
    valid = [v for v in conc if v is not None]
    cur = valid[-1] if valid else None
    med = None
    pctl = None
    overheat = None
    if valid and cur is not None:
        vs = sorted(valid)
        med = round(vs[len(vs) // 2], 1)
        pctl = round(sum(1 for v in valid if v <= cur) / len(valid) * 100, 1)
        overheat = round(vs[int(0.8 * (len(vs) - 1))], 1)   # 历史80%分位=过热线
    return {
        "updated": C.now_str(),
        "dates": dates,
        "conc": conc,
        "ma20": ma20,
        "n_stocks": ns,
        "cur": cur,
        "median": med,
        "pctile": pctl,
        "overheat": overheat,
        "share": SHARE,
        "method": (
            "成交集中度=当日成交额前5%个股(按数量, 剔除停牌)合计成交额占全市场成交额的百分比; "
            "数据为东财全A股日度成交额(沪深主板/创业板/科创板, 不含北交所); "
            "历史回填仅含当前上市公司(存在幸存者偏差), 含MA20均线; "
            "过热线=2005年以来80%历史分位, 高于该线代表成交过度向少数个股集中。"
        ),
    }


def save_series(days):
    s = build_series(days)
    C.save_json(OUT, s)
    C.log("concentration: %d days, cur=%.1f%%, median=%s, pctile=%s%%"
          % (len(s["dates"]), s["cur"] or -1, s["median"], s["pctile"]))
    return s


# ---------------- 回填 ----------------

def backfill():
    uni = fetch_universe()
    codes = sorted(uni.keys())
    days = {}          # date -> array('q') 万元
    done = set()
    if os.path.exists(CKPT):
        try:
            with open(CKPT, "rb") as f:
                st = pickle.load(f)
            days = st.get("days") or {}
            done = set(st.get("done") or [])
            C.log("ckpt resume: %d days, %d/%d stocks done"
                  % (len(days), len(done), len(codes)))
        except Exception as e:  # noqa: BLE001
            C.log("ckpt load fail: %s" % e)
    todo = [c for c in codes if c not in done]

    def work(code):
        try:
            rows = C.fetch_kline(code)
        except Exception:  # noqa: BLE001
            return code, None
        return code, rows

    cnt = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, rows in ex.map(work, todo):
            cnt += 1
            if rows:
                for d, close, amt in rows:
                    if d < START or amt <= 0:
                        continue
                    a = days.get(d)
                    if a is None:
                        a = days[d] = array("q")
                    a.append(int(round(amt / 10000.0)))   # 万元
            done.add(code)
            if cnt % 500 == 0:
                C.log("conc kline %d/%d (days=%d)" % (cnt, len(todo), len(days)))
                with open(CKPT, "wb") as f:
                    pickle.dump({"days": days, "done": sorted(done)}, f, protocol=4)
    with open(CKPT, "wb") as f:
        pickle.dump({"days": days, "done": sorted(done)}, f, protocol=4)

    # 写出逐日文件与序列
    if os.path.isdir(DAYS_DIR):
        for fn in os.listdir(DAYS_DIR):
            if fn.endswith(".json"):
                os.remove(os.path.join(DAYS_DIR, fn))
    os.makedirs(DAYS_DIR, exist_ok=True)
    for d in sorted(days):
        n, tot, tp, cv = conc_of(days[d])
        C.save_json(os.path.join(DAYS_DIR, d + ".json"), [n, tot, tp])
    C.log("conc days written: %d" % len(days))
    s = save_series({d: (v[0], v[1], v[2]) for d, v in
                     ((d, conc_of(days[d])) for d in days)})
    try:
        os.remove(CKPT)
    except OSError:
        pass
    return s


# ---------------- 增量 ----------------

def update(tdays=None):
    now = datetime.datetime.now()
    today = now.date().isoformat()
    if now.hour < 15:
        C.log("conc update: skip (未到收盘, hour=%d)" % now.hour)
        return None
    if tdays is not None and today not in tdays:
        C.log("conc update: skip (今日非交易日)")
        return None
    rows = fetch_snapshot_amounts()
    if len(rows) < 1000:
        C.log("conc update: snapshot rows=%d, 可能未收盘或接口异常" % len(rows))
        return None
    amts = [int(round(a / 10000.0)) for _, a in rows]
    n, tot, tp, cv = conc_of(amts)
    if cv is None:
        C.log("conc update: 无有效成交额")
        return None
    days = load_days()
    days[today] = (n, tot, tp)
    C.save_json(os.path.join(DAYS_DIR, today + ".json"), [n, tot, tp])
    C.log("conc update: %s n=%d conc=%.1f%% (upsert)" % (today, n, cv))
    return save_series(days)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "update"
    if mode == "backfill":
        backfill()
    else:
        update()
