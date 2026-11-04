# -*- coding: utf-8 -*-
"""热股榜聚合: 东方财富人气榜 / 同花顺热股榜 / 淘股吧热门研股
- 东方财富: emappdata 人气榜(POST), 按人气值排名
- 同花顺: dq.10jqka 热股日榜(讨论/关注度热度值)
- 淘股吧: 首页"热门研股"栏(游资讨论)
- 通达信: 无公开HTTP接口, 未纳入
名称与涨跌幅统一用东财全A快照补全。
用法: python hot_stocks.py   输出: data/hot_stocks.json
"""
import datetime
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TOPN = 50
OUT = os.path.join(C.DATA, "hot_stocks.json")


def post_json(url, body, headers=None, timeout=20):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json",
         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def fetch_names():
    """全A股快照: {code: [名称, 涨跌幅, 最新价]}"""
    out, pn = {}, 1
    while True:
        j = C.get_json("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f12,f14,f2,f3",
        })
        rows = (j.get("data") or {}).get("diff") or []
        if not rows:
            break
        for r in rows:
            out[str(r["f12"])] = [
                r.get("f14") or "",
                round(float(r["f3"]), 2) if isinstance(r.get("f3"), (int, float)) else None,
                float(r["f2"]) if isinstance(r.get("f2"), (int, float)) else None,
            ]
        total = int((j.get("data") or {}).get("total") or 0)
        if pn * 100 >= total:
            break
        pn += 1
        if pn > 90:
            break
    return out


def fetch_em():
    """东方财富人气榜 TOPN"""
    j = post_json("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                  {"appId": "appId01",
                   "globalId": "786e4c21-70dc-4442-a2b5-fc661904fbf8",
                   "marketType": "", "pageNo": 1, "pageSize": TOPN})
    rows = (j.get("data") or [])[:TOPN]
    out = []
    for r in rows:
        sc = r.get("sc") or ""
        if len(sc) >= 8:
            out.append({"code": sc[2:], "rank": r.get("rk"),
                        "rk_chg": r.get("hisRc") or 0})
    return out


def fetch_ths():
    """同花顺热股榜(日榜, 热度值)"""
    j = C.get_json(
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
        params={"stock_type": "a", "type": "day", "list_type": "normal"},
        headers={"User-Agent": "Mozilla/5.0",
                 "Referer": "https://eq.10jqka.com.cn/"})
    lst = ((j.get("data") or {}).get("stock_list")) or []
    out = []
    for r in lst[:TOPN]:
        code = str(r.get("code") or "")
        if code:
            out.append({"code": code, "name": r.get("name") or "",
                        "rank": r.get("order"),
                        "rate": float(r.get("rate") or 0),
                        "pct": (round(float(r["rise_and_fall"]), 2)
                                if isinstance(r.get("rise_and_fall"), (int, float)) else None)})
    return out


def fetch_tgb():
    """淘股吧首页"热门研股"栏"""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request("https://www.tgb.cn/", headers=h)
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(
            r'defaultContainerRight-stock-item"\s+id="[a-z]{2}(\d{6})"', html):
        code = m.group(1)
        seg = html[m.start():m.start() + 700]
        nm = re.search(r"<p>([^<]+)</p>", seg)
        rt = re.search(r'<span class="(?:rise|fall)">([\d.]+) / ([+\-\d.]+)%</span>', seg)
        if nm and rt:
            out.append({"code": code, "name": nm.group(1).strip(),
                        "price": float(rt.group(1)), "pct": float(rt.group(2))})
    return out


def run():
    em, ths, tgb, err = [], [], [], []
    for name, fn in [("em", fetch_em), ("ths", fetch_ths), ("tgb", fetch_tgb)]:
        try:
            rows = fn()
            if name == "em":
                em = rows
            elif name == "ths":
                ths = rows
            else:
                tgb = rows
        except Exception as e:  # noqa: BLE001
            err.append("%s:%s" % (name, str(e)[:100]))
    if not (em or ths or tgb):
        raise RuntimeError("全部热股源失败: %s" % err)

    names = {}
    if em or ths:
        try:
            names = fetch_names()
        except Exception as e:  # noqa: BLE001
            err.append("names:%s" % str(e)[:100])

    def fill(rows):
        for r in rows:
            info = names.get(r["code"])
            if info:
                if not r.get("name"):
                    r["name"] = info[0]
                if r.get("pct") is None:
                    r["pct"] = info[1]
                if r.get("price") is None:
                    r["price"] = info[2]
            r.setdefault("name", "")
            r.setdefault("pct", None)
            r.setdefault("price", None)
        return rows

    em, ths, tgb = fill(em), fill(ths), fill(tgb)
    out = {
        "updated": C.now_str(),
        "em": em, "ths": ths, "tgb": tgb,
        "err": err,
        "method": (
            "东方财富=人气榜TOP50(关注+成交人气); 同花顺=热股日榜TOP50(讨论热度值); "
            "淘股吧=首页'热门研股'栏(游资/潜伏讨论); 通达信无公开接口未纳入。"
            "出现在≥2个榜单的个股金色高亮(共热=资金与情绪共振)。"
        ),
    }
    C.save_json(OUT, out)
    C.log("hot: em=%d ths=%d tgb=%d err=%s"
          % (len(em), len(ths), len(tgb), err or "-"))
    return out


if __name__ == "__main__":
    run()
