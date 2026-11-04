# -*- coding: utf-8 -*-
"""从raw数据推导最终看板数据: ETF资金流 / 两融 / H00300
用法: python compute_cn.py   (backfill完成后本地运行, 或全量重建时运行)
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import huijin  # noqa: E402

SSE_DIR = os.path.join(C.RAW, "sse")
SZSE_DIR = os.path.join(C.RAW, "szse")
KLINE_DIR = os.path.join(C.RAW, "kline")
CYRJG_DIR = os.path.join(C.RAW, "cyrjg")

SUSPICION_RATIO = 0.20   # 单日净申购超过前一日市值的20%视为份额折算/异常
DELIST_STREAK = 8        # 连续缺失份额的天数达到即视为退市/终止上市


def load_all_shares():
    shares = {}
    if os.path.isdir(SSE_DIR):
        for fn in os.listdir(SSE_DIR):
            if fn.endswith(".json"):
                d = fn[:-5]
                shares[d] = C.load_json(os.path.join(SSE_DIR, fn)) or {}
    if os.path.isdir(SZSE_DIR):
        for fn in os.listdir(SZSE_DIR):
            if not fn.endswith(".json"):
                continue
            blob = C.load_json(os.path.join(SZSE_DIR, fn))
            if isinstance(blob, dict) and "shares" in blob:
                for d, m in blob["shares"].items():
                    shares.setdefault(d, {}).update(m)
            elif isinstance(blob, dict):
                for d, m in blob.items():
                    shares.setdefault(d, {}).update(m)
    return shares


def compute_flows():
    uni = C.load_json(os.path.join(C.RAW, "universe.json")) or {}
    broad = uni.get("broad") or {}
    shares = load_all_shares()
    dates = sorted(d for d in shares if d >= "2019-12-20")
    # K线: {code: {date: close}}
    kline_map, kline_last = {}, {}
    for code in broad:
        rows = C.load_json(os.path.join(KLINE_DIR, code + ".json")) or []
        m = {r[0]: r[1] for r in rows}
        kline_map[code] = m
        if rows:
            kline_last[code] = rows[-1][:2]
    # 持有人结构: {code: [[date, inst, indiv],...]} 升序
    cyrjg = {}
    for code in broad:
        rows = C.load_json(os.path.join(CYRJG_DIR, code + ".json")) or []
        rows.sort(key=lambda r: r[0])
        cyrjg[code] = rows

    def ratio_asof(code, d):
        rows = cyrjg.get(code) or []
        if not rows:
            return 50.0, 45.0
        r = rows[0]
        for row in rows:
            if row[0] <= d:
                r = row
            else:
                break
        return r[1], r[2]

    def price_asof(code, d):
        m = kline_map.get(code) or {}
        p = m.get(d)
        if p:
            return p
        # 回看最近一次价格
        best = None
        for k in m:
            if k <= d and (best is None or k > best):
                best = k
        return m.get(best) if best else None

    out_dates, daily_inst, daily_indiv, daily_total = [], [], [], []
    active = {}          # code -> 上次份额
    missing_streak = {}  # code -> 连续缺失天数
    suspicious, delisted, listed = [], [], []
    inst_cum = indiv_cum = total_cum = 0.0

    for i in range(1, len(dates)):
        d, dprev = dates[i], dates[i - 1]
        if d < "2020-01-02":
            # 基线日(2019年末): 初始化份额
            for code, v in shares[d].items():
                if code in broad:
                    active[code] = v
            continue
        today_shares = shares[d]
        # 新出现的ETF -> 建立基线(上市首日不计净申购)
        for code in broad:
            if code not in active and code in today_shares:
                active[code] = today_shares[code]
                if d > "2020-01-06":
                    listed.append((d, code))
        inst_amt = indiv_amt = tot_amt = 0.0
        gone = []
        for code, prev_share in active.items():
            cur = today_shares.get(code)
            if cur is None:
                missing_streak[code] = missing_streak.get(code, 0) + 1
                if missing_streak[code] >= DELIST_STREAK:
                    delisted.append((d, code))
                    gone.append(code)
                continue
            missing_streak[code] = 0
            delta = cur - prev_share
            if abs(delta) < 1e-9:
                active[code] = cur
                continue
            price = price_asof(code, d)
            if not price:
                active[code] = cur
                continue
            amt = delta * price
            prev_value = prev_share * price
            if (abs(amt) > SUSPICION_RATIO * max(prev_value, 0.5)
                    and abs(amt) > 5.0):
                suspicious.append((d, code, round(amt, 1)))
                active[code] = cur
                continue
            inst_r, indiv_r = ratio_asof(code, d)
            inst_amt += amt * inst_r / 100.0
            indiv_amt += amt * indiv_r / 100.0
            tot_amt += amt
            active[code] = cur
        for code in gone:
            active.pop(code, None)
            missing_streak.pop(code, None)
        inst_cum += inst_amt
        indiv_cum += indiv_amt
        total_cum += tot_amt
        out_dates.append(d)
        daily_inst.append(round(inst_amt, 1))
        daily_indiv.append(round(indiv_amt, 1))
        daily_total.append(round(tot_amt, 1))

    months, gjs_values = huijin.build_gjs_series(
        out_dates, _cum(daily_inst))
    flows = {
        "updated": C.now_str(),
        "dates": out_dates,
        "inst_cum": [round(x, 1) for x in _cum(daily_inst)],
        "indiv_cum": [round(x, 1) for x in _cum(daily_indiv)],
        "total_cum": [round(x, 1) for x in _cum(daily_total)],
        "daily_inst": daily_inst,
        "daily_indiv": daily_indiv,
        "daily_total": daily_total,
        "gjs": {
            "months": months,
            "values": gjs_values,
            "est_from": huijin.ESTIMATE_FROM,
            "attrib": huijin.ATTRIB,
            "anchors": [{"date": a[0], "value": a[1], "note": a[2]}
                        for a in huijin.ANCHORS],
        },
        "events": huijin.EVENTS,
        "universe_count": len(broad),
        "method": (
            "净申购金额=ETF份额变动(沪深交易所官方日度数据)×当日收盘价; "
            "机构/个人按各ETF定期报告持有人结构比例拆分; "
            "国家队(汇金)为持有ETF市值口径, 由基金定期报告披露数据插值; "
            "机构线已包含国家队; 统计范围为A股宽基指数股票ETF。"
        ),
        "suspicious_sample": suspicious[:20],
        "delisted_sample": delisted[:20],
        "listed_sample": listed[:20],
    }
    C.save_json(os.path.join(C.DATA, "etf_flows.json"), flows)
    # 增量更新所需状态
    C.save_json(os.path.join(C.STATE, "etf_state.json"), {
        "last_date": out_dates[-1] if out_dates else None,
        "active": active,
        "kline_last": kline_last,
        "cyrjg": cyrjg,
        "universe": broad,
        "missing_streak": missing_streak,
    })
    C.log("flows: %d days, universe=%d, inst_cum=%.0f indiv_cum=%.0f total=%.0f"
          % (len(out_dates), len(broad), inst_cum, indiv_cum, total_cum))
    C.log("suspicious=%d delisted=%d listed=%d"
          % (len(suspicious), len(delisted), len(listed)))


def _cum(daily):
    out, s = [], 0.0
    for v in daily:
        s += v
        out.append(s)
    return out


def compute_margin():
    raw = C.load_json(os.path.join(C.RAW, "margin.json")) or {}
    dates, rzye, rqye = [], [], []
    for d, rz, rq in zip(raw.get("dates", []), raw.get("rzye", []),
                         raw.get("rqye", [])):
        if d >= "2019-12-01":
            dates.append(d)
            rzye.append(rz)
            rqye.append(rq)
    C.save_json(os.path.join(C.DATA, "margin.json"), {
        "updated": C.now_str(), "dates": dates, "rzye": rzye, "rqye": rqye,
        "method": "融资余额(亿元), 东方财富数据中心沪深两融汇总, 日频(收盘后披露)",
    })
    C.log("margin: %d rows, last=%s" % (len(dates), dates[-1] if dates else "-"))


def compute_tri():
    rows = C.load_json(os.path.join(C.RAW, "h00300.json")) or []
    # 债基指数若已生成则合并
    bond = C.load_json(os.path.join(C.DATA, "tri_bond.json")) or {}
    out = {
        "updated": C.now_str(),
        "dates": [r[0] for r in rows],
        "tri": [r[1] for r in rows],
        "bond": bond.get("bond") or [],
        "bond_dates": bond.get("bond_dates") or [],
        "method": (
            "沪深300全收益指数(H00300, 中证指数官网, 含分红再投资); "
            "二级债基指数为自建等权指数(全市场混合债券型二级基金日净值增长率等权合成, "
            "基期2005-01-04=1000, 存在幸存者偏差等近似); "
            "对比系数可在页面调整, 默认1即不缩放, 两曲线同以约1000点起步。"
        ),
    }
    C.save_json(os.path.join(C.DATA, "tri_bond.json"), out)
    C.log("tri: %d rows" % len(rows))


def update_meta():
    meta = C.load_json(os.path.join(C.DATA, "meta.json")) or {}
    meta["cn_updated"] = C.now_str()
    C.save_json(os.path.join(C.DATA, "meta.json"), meta)


if __name__ == "__main__":
    compute_flows()
    compute_margin()
    compute_tri()
    update_meta()
    C.log("COMPUTE DONE")
