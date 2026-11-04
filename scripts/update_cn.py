# -*- coding: utf-8 -*-
"""A股增量更新(GitHub Actions / 本地计划任务通用)
更新: 宽基ETF净申购曲线 / 融资余额 / H00300 / 二级债基指数 / 持有人结构(周一)
用法: python update_cn.py [--full-cyrjg]
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import bond_index  # noqa: E402
import concentration  # noqa: E402
import huijin  # noqa: E402
from width_etfs import is_broad_etf  # noqa: E402

SUSPICION_RATIO = 0.20
DELIST_STREAK = 8


def trading_days():
    rows = C.fetch_h00300()
    if not rows:
        return []
    C.save_json(os.path.join(C.RAW, "h00300.json"), rows)
    return [r[0] for r in rows]


def refresh_universe(state):
    """返回 universe, 并把新增宽基ETF补进state"""
    uni = state.get("universe") or {}
    try:
        for row in C.fetch_fund_rank(ft="ct", dt="fb", pn=20000):
            p = row.split(",")
            if len(p) > 2 and p[0] not in uni:
                if is_broad_etf(p[0], p[1]):
                    uni[p[0]] = p[1]
        state["universe"] = uni
    except Exception as e:  # noqa: BLE001
        C.log("universe refresh fail: %s" % e)
    return uni


def ratio_asof(rows, d):
    if not rows:
        return 50.0, 45.0
    r = rows[0]
    for row in rows:
        if row[0] <= d:
            r = row
        else:
            break
    return r[1], r[2]


def run():
    state = C.load_json(os.path.join(C.STATE, "etf_state.json"))
    flows = C.load_json(os.path.join(C.DATA, "etf_flows.json"))
    if not state or not flows:
        C.log("missing state, cannot increment")
        return
    tdays = trading_days()
    today = datetime.date.today().isoformat()
    last = state.get("last_date")
    need = [d for d in tdays if d > (last or "2000-01-01") and d <= today]
    if not need:
        C.log("no new trading day (last=%s)" % last)
    else:
        uni = refresh_universe(state)
        # 拉取缺失日期的份额
        share_days = {}
        try:
            sz = C.fetch_szse_etf_shares(need[0], need[-1])
        except Exception as e:  # noqa: BLE001
            C.log("szse fail: %s" % e)
            sz = ({}, {})
        szse_shares = sz[0] if isinstance(sz, tuple) else sz
        for d in need:
            day_map = {}
            try:
                day_map.update(C.fetch_sse_etf_shares(d) or {})
            except Exception as e:  # noqa: BLE001
                C.log("sse %s fail: %s" % (d, e))
            day_map.update(szse_shares.get(d) or {})
            share_days[d] = day_map
        # 价格: 单日用批量行情, 多日用K线
        prices = {}
        codes = sorted(uni.keys())
        if len(need) == 1:
            try:
                q = C.fetch_quotes_ulist(codes)
                for c, v in q.items():
                    if v and v[0] and v[0] > 0:
                        prices.setdefault(need[0], {})[c] = v[0]
            except Exception as e:  # noqa: BLE001
                C.log("ulist fail: %s" % e)
        if not prices:
            for c in codes:
                try:
                    rows = C.fetch_kline(c, beg=need[0].replace("-", ""),
                                         end=need[-1].replace("-", ""))
                except Exception:  # noqa: BLE001
                    rows = []
                for r in rows or []:
                    prices.setdefault(r[0], {}).setdefault(c, r[1])
        # 逐日计算增量
        active = state.get("active") or {}
        missing = state.get("missing_streak") or {}
        cyrjg = state.get("cyrjg") or {}
        kl_last = state.get("kline_last") or {}
        for d in need:
            day_map = share_days.get(d) or {}
            if not day_map:
                C.log("no share data for %s (未发布?)" % d)
                continue
            for code in uni:
                if code not in active and code in day_map:
                    active[code] = day_map[code]  # 新上市, 建基线
            pmap = prices.get(d) or {}
            inst_amt = indiv_amt = tot_amt = 0.0
            gone = []
            for code, prev_share in list(active.items()):
                cur = day_map.get(code)
                if cur is None:
                    missing[code] = missing.get(code, 0) + 1
                    if missing[code] >= DELIST_STREAK:
                        gone.append(code)
                    continue
                missing[code] = 0
                delta = cur - prev_share
                price = pmap.get(code)
                if price is None:
                    lk = kl_last.get(code)
                    price = lk[1] if lk else None
                if abs(delta) < 1e-9 or not price:
                    active[code] = cur
                    continue
                amt = delta * price
                if abs(amt) > SUSPICION_RATIO * max(prev_share * price, 0.5):
                    C.log("skip suspicious %s %s %.0f" % (d, code, amt))
                    active[code] = cur
                    continue
                ir, dr = ratio_asof(cyrjg.get(code) or [], d)
                inst_amt += amt * ir / 100.0
                indiv_amt += amt * dr / 100.0
                tot_amt += amt
                active[code] = cur
                kl_last[code] = [d, price]
            for code in gone:
                active.pop(code, None)
                missing.pop(code, None)
            flows["dates"].append(d)
            flows["daily_inst"].append(round(inst_amt, 1))
            flows["daily_indiv"].append(round(indiv_amt, 1))
            flows["daily_total"].append(round(tot_amt, 1))
            def cum(key, dkey):
                prev = flows[key][-1] if flows[key] else 0.0
                flows[key].append(round(prev + flows[dkey][-1], 1))
            cum("inst_cum", "daily_inst")
            cum("indiv_cum", "daily_indiv")
            cum("total_cum", "daily_total")
            state["last_date"] = d
            C.log("flows %s: inst=%+.0f indiv=%+.0f total=%+.0f"
                  % (d, inst_amt, indiv_amt, tot_amt))
        state["active"] = active
        state["missing_streak"] = missing
        state["kline_last"] = kl_last
        state["cyrjg"] = cyrjg
        flows["updated"] = C.now_str()
        flows["universe_count"] = len(uni)
        C.save_json(os.path.join(C.DATA, "etf_flows.json"), flows)
        C.save_json(os.path.join(C.STATE, "etf_state.json"), state)

    # 两融增量
    try:
        m = C.load_json(os.path.join(C.DATA, "margin.json")) or {}
        last_m = m["dates"][-1] if m.get("dates") else "2000-01-01"
        rows = C.fetch_margin_all((datetime.date.today() -
                                   datetime.timedelta(days=20)).isoformat())
        added = 0
        for d, rz, rq in rows:
            if d > last_m:
                m["dates"].append(d)
                m["rzye"].append(round(rz / 1e8, 2))
                m["rqye"].append(round(rq / 1e8, 2))
                added += 1
        if added:
            m["updated"] = C.now_str()
            C.save_json(os.path.join(C.DATA, "margin.json"), m)
        C.log("margin +%d (last=%s)" % (added, m["dates"][-1]))
    except Exception as e:  # noqa: BLE001
        C.log("margin fail: %s" % e)

    # H00300 (trading_days里已经全量抓过并保存raw, 这里同步到tri_bond)
    try:
        raw = C.load_json(os.path.join(C.RAW, "h00300.json")) or []
        tri = C.load_json(os.path.join(C.DATA, "tri_bond.json")) or {}
        tri["dates"] = [r[0] for r in raw]
        tri["tri"] = [r[1] for r in raw]
        tri["updated"] = C.now_str()
        C.save_json(os.path.join(C.DATA, "tri_bond.json"), tri)
        C.log("tri synced: %d rows" % len(raw))
    except Exception as e:  # noqa: BLE001
        C.log("tri fail: %s" % e)

    # 持有人结构: 周一或 --full-cyrjg
    if "--full-cyrjg" in sys.argv or datetime.date.today().weekday() == 0:
        uni = (state.get("universe") or {})
        cyrjg = state.get("cyrjg") or {}
        n = 0
        for code in sorted(uni.keys()):
            try:
                rows = C.fetch_cyrjg(code)
            except Exception:  # noqa: BLE001
                continue
            if rows:
                cyrjg[code] = rows
                n += 1
        state["cyrjg"] = cyrjg
        C.save_json(os.path.join(C.STATE, "etf_state.json"), state)
        C.log("cyrjg refreshed: %d" % n)

    # 成交集中度(收盘后快照, 非交易日/未收盘自动跳过)
    try:
        concentration.update(tdays)
    except Exception as e:  # noqa: BLE001
        C.log("conc fail: %s" % e)

    # 二级债基指数
    try:
        bond_index.update()
    except Exception as e:  # noqa: BLE001
        C.log("bond fail: %s" % e)

    # 国家队(汇金)估算序列: 无论是否有新交易日, 月度网格都延伸到当月
    try:
        huijin.refresh_gjs(flows)
        C.save_json(os.path.join(C.DATA, "etf_flows.json"), flows)
        C.log("gjs refreshed: %d months, last=%.0f"
              % (len(flows["gjs"]["months"]), flows["gjs"]["values"][-1]))
    except Exception as e:  # noqa: BLE001
        C.log("gjs fail: %s" % e)

    meta = C.load_json(os.path.join(C.DATA, "meta.json")) or {}
    meta["cn_updated"] = C.now_str()
    C.save_json(os.path.join(C.DATA, "meta.json"), meta)
    C.log("UPDATE CN DONE")


if __name__ == "__main__":
    run()
