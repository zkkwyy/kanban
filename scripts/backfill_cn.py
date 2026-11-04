# -*- coding: utf-8 -*-
"""一次性全量回填(本地运行): 交易所ETF份额 / K线 / 持有人结构 / 两融 / H00300
用法: python backfill_cn.py [h00300|sse|szse|universe|kline|cyrjg|margin|all]
已下载的数据自动跳过, 可断点续跑。
"""
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from width_etfs import is_broad_etf  # noqa: E402

SSE_DIR = os.path.join(C.RAW, "sse")
SZSE_DIR = os.path.join(C.RAW, "szse")
KLINE_DIR = os.path.join(C.RAW, "kline")
CYRJG_DIR = os.path.join(C.RAW, "cyrjg")


def step_h00300():
    rows = C.fetch_h00300()
    C.save_json(os.path.join(C.RAW, "h00300.json"), rows)
    C.log("H00300 rows=%d first=%s last=%s" % (len(rows), rows[0][0], rows[-1][0]))


def trading_dates():
    rows = C.load_json(os.path.join(C.RAW, "h00300.json"))
    if not rows:
        step_h00300()
        rows = C.load_json(os.path.join(C.RAW, "h00300.json"))
    return [r[0] for r in rows]


def step_sse():
    os.makedirs(SSE_DIR, exist_ok=True)
    dates = [d for d in trading_dates() if d >= "2019-12-20"]
    done = 0
    for i, d in enumerate(dates):
        fp = os.path.join(SSE_DIR, d + ".json")
        if os.path.exists(fp):
            continue
        try:
            shares = C.fetch_sse_etf_shares(d)
        except Exception as e:  # noqa: BLE001
            C.log("sse %s ERR %s" % (d, e))
            time.sleep(2)
            continue
        C.save_json(fp, shares)
        done += 1
        if done % 50 == 0:
            C.log("sse %d/%d (%s)" % (i + 1, len(dates), d))
        time.sleep(0.12)
    C.log("sse done, total dates=%d" % len(dates))


def _szse_chunks():
    start = datetime.date(2019, 12, 1)
    today = datetime.date.today()
    out = []
    cur = start
    while cur < today:
        end = min(cur + datetime.timedelta(days=175), today)
        out.append((cur.isoformat(), end.isoformat()))
        cur = end + datetime.timedelta(days=1)
    return out


def step_szse():
    os.makedirs(SZSE_DIR, exist_ok=True)
    for s, e in _szse_chunks():
        fp = os.path.join(SZSE_DIR, "%s_%s.json" % (s, e))
        if os.path.exists(fp):
            continue
        try:
            data, names = C.fetch_szse_etf_shares(s, e)
        except Exception as ex:  # noqa: BLE001
            C.log("szse %s..%s ERR %s" % (s, e, ex))
            time.sleep(3)
            continue
        C.save_json(fp, {"shares": data, "names": names})
        C.log("szse %s..%s dates=%d" % (s, e, len(data)))
        time.sleep(0.5)
    C.log("szse done")


def _merge_shares():
    """{date: {code: 亿份}}, {code: 简称}"""
    shares, names = {}, {}
    if os.path.isdir(SSE_DIR):
        for fn in sorted(os.listdir(SSE_DIR)):
            if not fn.endswith(".json"):
                continue
            d = fn[:-5]
            data = C.load_json(os.path.join(SSE_DIR, fn)) or {}
            shares[d] = data
    if os.path.isdir(SZSE_DIR):
        for fn in sorted(os.listdir(SZSE_DIR)):
            if not fn.endswith(".json"):
                continue
            blob = C.load_json(os.path.join(SZSE_DIR, fn))
            if isinstance(blob, dict) and "shares" in blob:
                data, nm = blob["shares"], blob.get("names") or {}
                for d, m in data.items():
                    shares.setdefault(d, {}).update(m)
                names.update(nm)
            elif isinstance(blob, dict):
                for d, m in blob.items():
                    shares.setdefault(d, {}).update(m)
    return shares, names


def step_universe():
    """汇总ETF清单, 补全名称, 生成宽基分类review文件"""
    shares, names = _merge_shares()
    codes = set()
    for d, m in shares.items():
        if d >= "2019-12-20":
            codes.update(m.keys())
    # 名称: 优先东财场内基金排行
    for row in C.fetch_fund_rank(ft="ct", dt="fb", pn=20000):
        p = row.split(",")
        if len(p) > 2:
            names[p[0]] = p[1]
    # 兜底: fundcode_search
    fc = C.fetch_fundcode_search()
    fcmap = {r[0]: r[2] for r in fc if len(r) > 2}
    for code in list(codes):
        if not names.get(code):
            names[code] = fcmap.get(code, "")
    broad = {c: names.get(c, "") for c in sorted(codes)
             if is_broad_etf(c, names.get(c, ""))}
    C.save_json(os.path.join(C.RAW, "universe.json"),
                {"all": {c: names.get(c, "") for c in sorted(codes)},
                 "broad": broad})
    with open(os.path.join(C.RAW, "universe_review.txt"), "w", encoding="utf-8") as f:
        f.write("=== 判定为宽基的ETF (%d只) ===\n" % len(broad))
        for c, n in broad.items():
            f.write("%s  %s\n" % (c, n))
        f.write("\n=== 判定为非宽基的ETF (%d只, 供核对误伤) ===\n"
                % (len(codes) - len(broad)))
        for c in sorted(codes):
            if c not in broad:
                f.write("%s  %s\n" % (c, names.get(c, "")))
    C.log("universe all=%d broad=%d -> universe_review.txt" % (len(codes), len(broad)))


def step_kline():
    os.makedirs(KLINE_DIR, exist_ok=True)
    uni = C.load_json(os.path.join(C.RAW, "universe.json")) or {}
    broad = uni.get("broad") or {}
    for i, (code, name) in enumerate(sorted(broad.items())):
        fp = os.path.join(KLINE_DIR, code + ".json")
        if os.path.exists(fp):
            continue
        try:
            rows = C.fetch_kline(code)
        except Exception as e:  # noqa: BLE001
            C.log("kline %s ERR %s" % (code, e))
            time.sleep(1)
            continue
        C.save_json(fp, rows)
        if (i + 1) % 30 == 0:
            C.log("kline %d/%d" % (i + 1, len(broad)))
        time.sleep(0.12)
    C.log("kline done %d" % len(broad))


def step_cyrjg():
    os.makedirs(CYRJG_DIR, exist_ok=True)
    uni = C.load_json(os.path.join(C.RAW, "universe.json")) or {}
    broad = uni.get("broad") or {}
    ok = 0
    for i, (code, name) in enumerate(sorted(broad.items())):
        fp = os.path.join(CYRJG_DIR, code + ".json")
        if os.path.exists(fp):
            ok += 1
            continue
        try:
            rows = C.fetch_cyrjg(code)
        except Exception as e:  # noqa: BLE001
            C.log("cyrjg %s ERR %s" % (code, e))
            time.sleep(1)
            continue
        C.save_json(fp, rows)
        ok += len(rows) and 1 or 0
        if (i + 1) % 30 == 0:
            C.log("cyrjg %d/%d" % (i + 1, len(broad)))
        time.sleep(0.15)
    C.log("cyrjg done ok=%d/%d" % (ok, len(broad)))


def step_margin():
    rows = C.fetch_margin_all("2019-12-01")
    C.save_json(os.path.join(C.RAW, "margin.json"),
                {"dates": [r[0] for r in rows],
                 "rzye": [round(r[1] / 1e8, 2) for r in rows],
                 "rqye": [round(r[2] / 1e8, 2) for r in rows]})
    C.log("margin rows=%d last=%s" % (len(rows), rows[-1][0] if rows else "-"))


STEPS = {"h00300": step_h00300, "sse": step_sse, "szse": step_szse,
         "universe": step_universe, "kline": step_kline, "cyrjg": step_cyrjg,
         "margin": step_margin}

if __name__ == "__main__":
    os.makedirs(C.RAW, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        for name in ["h00300", "sse", "szse", "universe", "kline", "cyrjg", "margin"]:
            C.log("=== step %s ===" % name)
            STEPS[name]()
    else:
        STEPS[which]()
    C.log("ALL DONE")
