# -*- coding: utf-8 -*-
"""部署前连通性探测: 从当前机器测试全部数据源可达性
用法: python server_probe.py
输出: 每个源 OK/FAIL, 决定服务器上开哪些定时任务
"""
import http.cookiejar
import io
import json
import ssl
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="gbk", errors="replace")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def t_get(url, referer=None):
    h = {"User-Agent": UA}
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    urllib.request.urlopen(req, timeout=12, context=CTX).read(2048)
    return True


def t_post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "User-Agent": UA})
    urllib.request.urlopen(req, timeout=12, context=CTX).read(2048)
    return True


def t_xq():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=CTX))
    op.addheaders = [("User-Agent", UA)]
    op.open("https://xueqiu.com/", timeout=12)
    if not any(c.name == "xq_a_token" for c in cj):
        raise RuntimeError("no token")
    return True


TESTS = [
    ("东财人气榜(emappdata)", lambda: t_post(
        "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
        {"appId": "appId01", "globalId":
         "786e4c21-70dc-4442-a2b5-fc661904fbf8", "marketType": "",
         "pageNo": 1, "pageSize": 10})),
    ("东财行情(clist)", lambda: t_get(
        "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=0&np=1"
        "&fltt=2&invt=2&fs=m:1+t:2&fields=f12,f14")),
    ("东财K线(push2his)", lambda: t_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519"
        "&fields1=f1&fields2=f51&klt=101&fqt=1&beg=20261001&end=20261101")),
    ("同花顺热榜(dq.10jqka)", lambda: t_get(
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
        "?stock_type=a&type=day&list_type=normal",
        referer="https://eq.10jqka.com.cn/")),
    ("淘股吧(tgb.cn)", lambda: t_get("https://www.tgb.cn/")),
    ("雪球(xueqiu)", t_xq),
    ("Yahoo美股(query1)", lambda: t_get(
        "https://query1.finance.yahoo.com/v8/finance/chart/XLK?range=1d")),
    ("中证指数(csindex)", lambda: t_get(
        "https://www.csindex.com.cn/csindex-home/perf/index-perf"
        "?indexCode=H00300&startDate=20261101&endDate=20261105")),
    ("上交所ETF份额", lambda: t_get(
        "https://query.sse.com.cn/commonQuery.do?jsonCallBack=x&sqlId="
        "COMMON_SSE_FUND_ETF_SHARE_GATEWAY_DAILY")),
    ("深交所(szsse)", lambda: t_get(
        "https://www.szse.cn/api/report/ShowReport/data?"
        "SHOWTYPE=JSON&CATALOGID=1945&TABKEY=tab1&PAGENO=1&PAGESIZE=25",
        referer="https://www.szse.cn/market/fund/volume/etf/index.html")),
]

ok, bad = [], []
for name, fn in TESTS:
    try:
        fn()
        ok.append(name)
        print("OK   %s" % name)
    except Exception as e:  # noqa: BLE001
        bad.append(name)
        print("FAIL %s (%s)" % (name, str(e)[:70]))
print("\n可用: %d  不可用: %d" % (len(ok), len(bad)))
if bad:
    print("不可用列表: %s" % "; ".join(bad))
