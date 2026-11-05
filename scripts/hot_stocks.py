# -*- coding: utf-8 -*-
"""热股榜聚合: 东方财富人气榜 / 同花顺热股榜 / 淘股吧热门研股 / 雪球热股榜
- 各榜只取前10名; "共热"判定也仅按各榜前10(超出前10不参与)
- 东方财富: emappdata 人气榜(POST), 按人气值排名
- 同花顺: dq.10jqka 热股日榜(讨论/关注度热度值)
- 淘股吧: 首页"热门研股"栏(游资讨论)
- 雪球: 热股榜(用户关注度值, 与通达信"当前关注度"同口径; 先访问首页取Cookie)
- 通达信: 关注度数据锁于其私有云(TQLEX网关拒绝外部连接), 无法接入
名称与涨跌幅统一用东财全A快照补全。
用法: python hot_stocks.py   输出: data/hot_stocks.json
"""
import datetime
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TOPN = 10
OUT = os.path.join(C.DATA, "hot_stocks.json")
NAME_CACHE = os.path.join(C.STATE, "names.json")
XQ_URL = ("https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
          "?size=50&_type=10&type=10")
_NOVERIFY = ssl.create_default_context()
_NOVERIFY.check_hostname = False
_NOVERIFY.verify_mode = ssl.CERT_NONE


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
    """淘股吧首页"热门研股"栏(盘前/停牌价格显示为0, span类不固定: rise/fall/c999)"""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request("https://www.tgb.cn/", headers=h)
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(
            r'defaultContainerRight-stock-item"\s+id="[a-z]{2}(\d{6})"', html):
        code = m.group(1)
        seg = html[m.start():m.start() + 700]
        nm = re.search(r"<p>([^<]+)</p>", seg)
        rt = re.search(r"<span[^>]*>\s*([\d.]+)\s*/\s*([+\-\d.]+)%\s*</span>", seg)
        if nm and rt:
            out.append({"code": code, "name": nm.group(1).strip(),
                        "price": float(rt.group(1)), "pct": float(rt.group(2))})
    return out


def fetch_xq():
    """雪球热股榜: 用户关注度值(需先访问首页获取Cookie; 海外IP可能被风控, 带重试)"""
    import time
    last = None
    for attempt in range(3):
        try:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cj),
                urllib.request.HTTPSHandler(context=_NOVERIFY))
            opener.addheaders = [("User-Agent",
                                  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36"),
                                 ("Accept-Language", "zh-CN,zh;q=0.9")]
            opener.open("https://xueqiu.com/", timeout=20)
            if not any(c.name == "xq_a_token" for c in cj):
                raise RuntimeError("xueqiu未下发token(可能被风控)")
            req = urllib.request.Request(
                XQ_URL, headers={"Referer": "https://xueqiu.com/"})
            j = json.loads(opener.open(req, timeout=20).read()
                           .decode("utf-8", "replace"))
            items = ((j.get("data") or {}).get("items")) or []
            out = []
            for i, r in enumerate(items[:TOPN]):
                code = str(r.get("code") or "")[2:]
                if not code:
                    continue
                out.append({
                    "code": code, "name": r.get("name") or "", "rank": i + 1,
                    "value": round(float(r.get("value") or 0)),
                    "rank_change": r.get("rank_change"),
                    "pct": (round(float(r["percent"]), 2)
                            if isinstance(r.get("percent"), (int, float)) else None),
                    "price": (float(r["current"])
                              if isinstance(r.get("current"), (int, float)) else None),
                })
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3)
    raise last


def run():
    old = C.load_json(OUT, {})
    em, ths, tgb, xq, err = [], [], [], [], []
    for name, fn in [("em", fetch_em), ("ths", fetch_ths),
                     ("tgb", fetch_tgb), ("xq", fetch_xq)]:
        try:
            rows = fn()
            if not rows:
                raise RuntimeError("空结果")
        except Exception as e:  # noqa: BLE001
            err.append("%s:%s" % (name, str(e)[:100]))
            # 抓取失败/空结果时沿用上次数据(避免偶发风控或页面改版清空榜单)
            rows = old.get(name) or []
            if rows:
                err[-1] += "(沿用上次)"
        if name == "em":
            em = rows
        elif name == "ths":
            ths = rows
        elif name == "tgb":
            tgb = rows
        else:
            xq = rows
    if not (em or ths or tgb or xq):
        raise RuntimeError("全部热股源失败: %s" % err)

    names = {}
    cache = C.load_json(NAME_CACHE, {}) or {}
    if em or ths:
        try:
            names = fetch_names()
        except Exception as e:  # noqa: BLE001
            err.append("names:%s(改用名称缓存)" % str(e)[:80])
    if names:
        # 名称稳定, 成功一次就沉淀进仓库缓存(runner上clist失败时兜底)
        n_new = 0
        for code, info in names.items():
            nm = (info or [""])[0]
            if nm and cache.get(code) != nm:
                cache[code] = nm
                n_new += 1
        if n_new:
            try:
                C.save_json(NAME_CACHE, cache)
            except Exception:  # noqa: BLE001
                pass

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
            if not r.get("name"):
                nm = cache.get(r["code"])
                if nm:
                    r["name"] = nm
            r.setdefault("name", "")
            r.setdefault("pct", None)
            r.setdefault("price", None)
        return rows

    em, ths, tgb, xq = fill(em), fill(ths), fill(tgb), fill(xq)
    out = {
        "updated": C.now_str(),
        "em": em, "ths": ths, "tgb": tgb, "xq": xq,
        "err": err,
        "method": (
            "东方财富=人气榜前10(关注+成交人气); 同花顺=热股日榜前10(讨论热度值); "
            "淘股吧=首页'热门研股'栏(游资/潜伏讨论); 雪球=热股榜前10(用户关注度值, "
            "与通达信'当前关注度'同口径, 附榜内热度占比); 通达信私有云无法接入。"
            "共热仅按各榜前10判定: 出现在≥2个榜单的个股金色高亮(资金与情绪共振)。"
        ),
    }
    C.save_json(OUT, out)
    C.log("hot: em=%d ths=%d tgb=%d xq=%d err=%s"
          % (len(em), len(ths), len(tgb), len(xq), err or "-"))
    return out


if __name__ == "__main__":
    run()
