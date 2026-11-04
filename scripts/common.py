# -*- coding: utf-8 -*-
"""公共工具: HTTP / 存储 / 日志 — 仅标准库, 本地与GitHub Actions通用"""
import datetime
import gzip
import io
import json
import os
import random
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
STATE = os.path.join(DATA, "state")


def http_get(url, params=None, referer=None, headers=None, timeout=40,
             retries=4, backoff=1.6, as_bytes=False):
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    hdrs = {"User-Agent": UA, "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate"}
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    last = None
    for i in range(retries):
        try:
            req = Request(url, headers=hdrs)
            with urlopen(req, timeout=timeout) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                if as_bytes:
                    return raw
                charset = r.headers.get_content_charset() or "utf-8"
                try:
                    return raw.decode(charset, errors="replace")
                except LookupError:
                    return raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(backoff * (i + 1) + random.random() * 0.5)
    raise RuntimeError("GET failed: %s (%s)" % (url, last))


def get_json(url, **kw):
    return json.loads(http_get(url, **kw))


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------- 东财 / 交易所 / 中证 各接口封装 ----------------

EM_RANK = "https://fund.eastmoney.com/data/rankhandler.aspx"


def fetch_h00300(start="20050101", end="20991231"):
    """中证指数官网: 沪深300全收益指数 H00300 日度收盘. 返回[(date, close)]"""
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    j = get_json(url, params={"indexCode": "H00300", "startDate": start,
                              "endDate": end},
                 headers={"Referer": "https://www.csindex.com.cn/"})
    out = []
    for row in j.get("data", []):
        d, c = row.get("tradeDate"), row.get("close")
        if d and c is not None:
            out.append((d[:4] + "-" + d[4:6] + "-" + d[6:], float(c)))
    return out


def fetch_sse_etf_shares(date):
    """上交所: 指定日期全部ETF份额(万份). 返回{code: 亿份}"""
    url = "https://query.sse.com.cn/commonQuery.do"
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": date,
    }
    j = get_json(url, params=params, referer="https://www.sse.com.cn/")
    rows = j.get("result") or []
    out = {}
    for r in rows:
        try:
            out[r["SEC_CODE"]] = float(r["TOT_VOL"]) / 10000.0  # 万份 -> 亿份
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_szse_etf_shares(start, end):
    """深交所: 区间(<=6个月)ETF日度份额xlsx. 返回({date:{code:亿份}}, {code:简称})"""
    import openpyxl
    url = "https://www.szse.cn/api/report/ShowReport"
    params = {
        "SHOWTYPE": "xlsx", "CATALOGID": "scsj_fund_jjgm", "TABKEY": "tab1",
        "txtStart": start, "txtEnd": end, "jjlb": "ETF",
        "random": "%.6f" % random.random(),
    }
    raw = http_get(url, params=params, timeout=90, retries=5,
                   referer="https://www.szse.cn/market/fund/volume/etf/index.html",
                   as_bytes=True)
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    ws = wb.active
    out, names = {}, {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0] or not row[1]:
            continue
        d = str(row[0])[:10]
        code = str(row[1]).zfill(6)
        try:
            share = float(str(row[3]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        out.setdefault(d, {})[code] = share / 1e8  # 份 -> 亿份
        if row[2]:
            names[code] = str(row[2])
    return out, names


def em_secid(code):
    """基金代码 -> 东财secid市场前缀: 5开头沪市(1), 其余深市(0)"""
    return ("1." if code.startswith("5") else "0.") + code


def fetch_kline(code, beg="19900101", end="20991231"):
    """东财日K线: 返回[[date, close, amount], ...]"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": em_secid(code), "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101", "fqt": "0", "beg": beg, "end": end,
    }
    j = get_json(url, params=params)
    data = (j.get("data") or {}).get("klines") or []
    out = []
    for line in data:
        p = line.split(",")
        if len(p) >= 7:
            out.append([p[0], float(p[2]), float(p[6])])
    return out


def fetch_quotes_ulist(codes):
    """东财单次批量行情: 返回{code: [最新价, 涨跌幅, 昨收]}"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    out = {}
    for i in range(0, len(codes), 80):
        chunk = codes[i:i + 80]
        params = {
            "secids": ",".join(em_secid(c) for c in chunk),
            "fields": "f2,f3,f12,f18", "fltt": "2", "pn": "1", "np": "1",
        }
        j = get_json(url, params=params)
        for r in ((j.get("data") or {}).get("diff") or []):
            try:
                out[str(r["f12"])] = [float(r["f2"]), float(r["f3"]),
                                      float(r["f18"])]
            except (KeyError, TypeError, ValueError):
                continue
    return out


def fetch_cyrjg(code):
    """天天基金F10持有人结构: 返回[[date, 机构%, 个人%], ...] 降序"""
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    txt = http_get(url, params={"code": code, "type": "cyrjg"},
                   referer="https://fundf10.eastmoney.com/")
    import re
    rows = re.findall(
        r"<tr><td>(\d{4}-\d{2}-\d{2})</td><td[^>]*>([\d.]+)%</td>"
        r"<td[^>]*>([\d.]+)%</td>", txt)
    return [[d, float(a), float(b)] for d, a, b in rows]


def fetch_margin_all(from_date="2019-12-01"):
    """东财两融余额历史: 返回[(date, 融资余额元, 融券余额元)] 升序"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    out = []
    page = 1
    while True:
        params = {
            "reportName": "RPTA_RZRQ_LSHJ", "columns": "DIM_DATE,RZYE,RQYE",
            "sortColumns": "dim_date", "sortTypes": "-1",
            "pageNumber": str(page), "pageSize": "800",
        }
        j = get_json(url, params=params)
        rows = ((j.get("result") or {}).get("data")) or []
        if not rows:
            break
        stop = False
        for r in rows:
            d = str(r["DIM_DATE"])[:10]
            if d < from_date:
                stop = True
                break
            out.append((d, float(r["RZYE"]), float(r["RQYE"])))
        if stop or len(rows) < 800:
            break
        page += 1
        time.sleep(0.3)
    out.reverse()
    return out


def fetch_fund_rank(ft="zq", dt="kf", pn=10000):
    """天天基金排行: 返回原始csv行列表"""
    sd = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    ed = datetime.date.today().isoformat()
    txt = http_get(EM_RANK, params={
        "op": "ph", "dt": dt, "ft": ft, "rs": "", "gs": "0",
        "sc": "zzf", "st": "desc", "sd": sd, "ed": ed, "qdii": "",
        "tabSubtype": ",,,,,", "pi": "1", "pn": str(pn), "dx": "1",
        "v": "%.7f" % random.random(),
    }, referer="https://fund.eastmoney.com/data/fundranking.html")
    # 响应为JS字面量: var rankData = {datas:["row1","row2",...],allRecords:...};
    i = txt.find("datas:[")
    if i < 0:
        return []
    j = txt.find("]", i)
    if j < 0:
        return []
    try:
        return json.loads(txt[i + 6:j + 1])
    except ValueError:
        return []


def fetch_fundcode_search():
    """全部基金代码与类型: 返回[[code, pinyin, name, type, pinyinFull], ...]"""
    txt = http_get("https://fund.eastmoney.com/js/fundcode_search.js",
                   referer="https://fund.eastmoney.com/")
    i = txt.find("[")
    arr = txt[i:txt.rfind("]") + 1]
    return json.loads(arr)


def fetch_pingzhong(code):
    """天天基金pingzhongdata: 返回 Data_netWorthTrend 列表"""
    txt = http_get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                   referer="https://fund.eastmoney.com/%s.html" % code,
                   timeout=60)
    key = "Data_netWorthTrend = "
    i = txt.find(key)
    if i < 0:
        return None
    j = txt.find("]", i)
    return json.loads(txt[i + len(key):j + 1])
