# -*- coding: utf-8 -*-
"""自建二级债基等权指数
- rebuild: fundcode_search取"债券型-混合二级"全量基金 -> pingzhongdata全史净值 ->
           日收益率(equityReturn)等权合成, 基期2005-01-04=1000
- update:  用天天基金债券型排行的当日日增长率做日度增量
用法: python bond_index.py rebuild|update
"""
import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

STATE_FP = os.path.join(C.STATE, "bond_state.json")
BOND_TYPE = "债券型-混合二级"
BASE_DATE = "2005-01-04"
BASE_VALUE = 1000.0


def get_universe():
    fc = C.fetch_fundcode_search()
    uni = {r[0]: r[2] for r in fc if len(r) > 3 and r[3] == BOND_TYPE}
    C.log("bond universe: %d funds" % len(uni))
    return uni


def _fetch_returns(code):
    """返回该基金的[[date, ret%], ...](跳过首个点) 或 None"""
    try:
        trend = C.fetch_pingzhong(code)
    except Exception:  # noqa: BLE001
        return None
    if not trend:
        return None
    out = []
    for i, p in enumerate(trend):
        if i == 0:
            continue
        r = p.get("equityReturn")
        if r is None:
            continue
        d = datetime.datetime.fromtimestamp(p["x"] / 1000).strftime("%Y-%m-%d")
        out.append([d, float(r)])
    return out


def rebuild():
    uni = get_universe()
    codes = sorted(uni.keys())
    agg = {}  # date -> [sum_ret, count]
    done = [0]
    lock_sum = {}

    def work(code):
        rets = _fetch_returns(code)
        local = {}
        if rets:
            for d, r in rets:
                if d < BASE_DATE:
                    continue
                if d not in local:
                    local[d] = [0.0, 0]
                local[d][0] += r
                local[d][1] += 1
        done[0] += 1
        if done[0] % 200 == 0:
            C.log("pingzhong %d/%d" % (done[0], len(codes)))
        return local

    with ThreadPoolExecutor(max_workers=6) as ex:
        for local in ex.map(work, codes):
            for d, (s, n) in local.items():
                if d not in agg:
                    agg[d] = [0.0, 0]
                agg[d][0] += s
                agg[d][1] += n

    dates = sorted(agg.keys())
    values, idx = [], BASE_VALUE
    for d in dates:
        s, n = agg[d]
        if n < 3:  # 样本太少不生效(基期前后)
            values.append(round(idx, 2))
            continue
        idx *= (1.0 + s / n / 100.0)
        values.append(round(idx, 2))
    # 基期对齐: 第一天强制为基点
    if dates:
        values[0] = BASE_VALUE
        # 归一: 用第二天重新起算, 保证首日=1000
        if len(values) > 1:
            pass
    C.save_json(STATE_FP, {
        "codes": codes,
        "universe": uni,
        "last_date": dates[-1] if dates else None,
        "index_value": values[-1] if values else BASE_VALUE,
        "rebuilt": C.now_str(),
    })
    merge_into_tri(dates, values)
    C.log("bond index rebuilt: %d points, %s -> %s (%.1f)"
          % (len(dates), dates[0] if dates else "-", dates[-1] if dates else "-",
             values[-1] if values else BASE_VALUE))
    return dates, values


def update():
    state = C.load_json(STATE_FP)
    if not state:
        C.log("no bond state, need rebuild first")
        return rebuild()
    codes = set(state.get("codes") or [])
    rows = C.fetch_fund_rank(ft="zq", dt="kf", pn=10000)
    # 按净值日期分组(各基金披露进度不同, 只取当日已披露基金的等权均值)
    by_date = {}
    for row in rows:
        p = row.split(",")
        if len(p) < 7 or p[0] not in codes:
            continue
        try:
            d, r = p[3], float(p[6])
        except (ValueError, IndexError):
            continue
        if r == r:  # NaN过滤
            by_date.setdefault(d, []).append(r)
    if not by_date:
        C.log("bond update: no nav data from rank")
        return None
    tri = C.load_json(os.path.join(C.DATA, "tri_bond.json")) or {}
    dates = tri.get("bond_dates") or []
    values = tri.get("bond") or []
    last = dates[-1] if dates else None
    new_dates = sorted(d for d in by_date if d > (last or "2000-01-01"))
    if not new_dates:
        C.log("bond update: already up to date (%s)" % last)
        return dates, values
    idx = state.get("index_value") or (values[-1] if values else BASE_VALUE)
    added = 0
    for d in new_dates:
        rets = by_date[d]
        if len(rets) < 3:  # 当日披露样本太少, 跳过避免噪声
            C.log("bond update: skip %s (only %d funds)" % (d, len(rets)))
            continue
        mean_r = sum(rets) / len(rets)
        idx *= (1.0 + mean_r / 100.0)
        dates = dates + [d]
        values = values + [round(idx, 2)]
        state["last_date"] = d
        added += 1
        C.log("bond update: %s mean=%.3f%% n=%d -> %.1f"
              % (d, mean_r, len(rets), idx))
    if not added:
        return dates, values
    state["index_value"] = idx
    C.save_json(STATE_FP, state)
    merge_into_tri(dates, values)
    return dates, values


def merge_into_tri(dates, values):
    tri = C.load_json(os.path.join(C.DATA, "tri_bond.json")) or {}
    if not tri.get("dates"):
        raw = C.load_json(os.path.join(C.RAW, "h00300.json")) or []
        tri = {"dates": [r[0] for r in raw], "tri": [r[1] for r in raw]}
    tri["bond_dates"] = dates
    tri["bond"] = values
    tri["updated"] = C.now_str()
    tri["method"] = (
        "沪深300全收益指数(H00300, 中证指数官网, 含分红再投资); "
        "二级债基指数为自建等权指数(全市场混合债券型二级基金日净值增长率等权合成, "
        "基期2005-01-04=1000, 存在幸存者偏差等近似); "
        "对比系数可在页面调整, 默认1即不缩放, 两曲线同以约1000点起步。"
    )
    C.save_json(os.path.join(C.DATA, "tri_bond.json"), tri)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "update"
    if mode == "rebuild":
        rebuild()
    else:
        update()
