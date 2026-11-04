# -*- coding: utf-8 -*-
"""国家队(中央汇金)持有ETF市值 — 锚点插值 + 机构净申购归因延伸

锚点(2023-10~2025-06)来自ETF定期报告"前十大持有人"中汇金系持仓汇总
(券商研报与财经媒体统计披露的公开数据):
- 2023-10-23 中央汇金公告: 买入ETF并将继续增持
- 2025-04-08 汇金公告: 明确"国家队/类平准基金"角色
2020-2023年中的锚点为估算(2015救市后汇金长期持有的存量仓位)。

2025-06-30之后无公开汇总, 采用"机构净申购归因法"动态延伸:
    GJS(t) = 13000 + 0.8 × (机构累计净申购(t) − 机构累计净申购(锚点日))
即假定宽基ETF机构净申赎中约80%为汇金行为(救市与退出期汇金占机构申赎主导)。
该段为估算(图中虚线), 会随每日数据自动更新。
"""

ATTRIB = 0.8  # 机构净申购归因于汇金的比例

# (日期, 汇金系持有ETF市值 亿元, 说明)
ANCHORS = [
    ("2020-01-02", 550, "估算: 2015救市后汇金长期持有的存量ETF仓位"),
    ("2021-06-30", 560, "估算: 存量仓位小幅变化"),
    ("2022-12-30", 590, "估算: 2022年市场调整期间小幅增持"),
    ("2023-06-30", 640, "2023基金中报汇总(约数)"),
    ("2023-10-23", 700, "汇金公告开始买入ETF(估算起点)"),
    ("2023-12-29", 1177, "2023基金年报汇总: 汇金系持有ETF市值约1177亿"),
    ("2024-06-28", 5838, "2024基金中报汇总: 约5838亿"),
    ("2024-12-31", 10500, "2024基金年报汇总: 首破万亿, 约1.05万亿"),
    ("2025-04-08", 11500, "汇金'类平准基金'公告前后继续增持(估算)"),
    ("2025-06-30", 13000, "2025基金中报汇总口径(约数, 估算)"),
]

ESTIMATE_FROM = "2025-06-30"   # 该日期之后为归因估算段

# 汇金公告事件(图上标注)
EVENTS = [
    {"date": "2023-10-23", "text": "汇金公告买入ETF并持续增持"},
    {"date": "2024-02-05", "text": "汇金加大增持力度(市场底部)"},
    {"date": "2025-04-08", "text": "汇金明确'类平准基金'角色"},
]


def month_grid(start, end):
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _mi(s):
    return int(s[:4]) * 12 + int(s[5:7])


def build_gjs_series(inst_dates=None, inst_cum=None, start="2020-01", end=None):
    """返回 (months, values)
    锚点期: 线性插值; 估算期: 锚点值 + ATTRIB×机构累计净申购增量
    inst_dates/inst_cum: 机构累计净申购日度序列(用于估算段)
    """
    import bisect
    import datetime
    if end is None:
        end = datetime.date.today().strftime("%Y-%m")
    months = month_grid(start, end)
    ad = [a[0][:7] for a in ANCHORS]
    av = [a[1] for a in ANCHORS]
    last_anchor_val = av[-1]
    last_anchor_month = ad[-1]
    # 机构累计净申购在锚点日的值
    inst_at_anchor = None
    if inst_dates and inst_cum:
        for d, v in zip(inst_dates, inst_cum):
            if d[:7] <= last_anchor_month:
                inst_at_anchor = v
            else:
                break
    values = []
    for m in months:
        if m <= ad[0]:
            values.append(av[0])
            continue
        if m <= last_anchor_month:
            if m >= ad[-1]:
                values.append(last_anchor_val)
                continue
            i = bisect.bisect_right(ad, m) - 1
            d0, d1, v0, v1 = ad[i], ad[i + 1], av[i], av[i + 1]
            t = (_mi(m) - _mi(d0)) / max(1, _mi(d1) - _mi(d0))
            values.append(round(v0 + (v1 - v0) * t, 1))
            continue
        # 估算段: 归因法
        if inst_dates and inst_cum and inst_at_anchor is not None:
            cur = inst_at_anchor
            for d, v in zip(inst_dates, inst_cum):
                if d[:7] <= m:
                    cur = v
                else:
                    break
            values.append(max(0.0, round(last_anchor_val + ATTRIB * (cur - inst_at_anchor), 1)))
        else:
            values.append(last_anchor_val)
    return months, values


def refresh_gjs(flows):
    """根据flows(inst累计序列)刷新其gjs字段(月度)"""
    months, values = build_gjs_series(flows.get("dates") or [],
                                       flows.get("inst_cum") or [])
    flows["gjs"] = {
        "months": months,
        "values": values,
        "est_from": ESTIMATE_FROM,
        "attrib": ATTRIB,
        "anchors": [{"date": a[0], "value": a[1], "note": a[2]}
                    for a in ANCHORS],
    }
    return flows["gjs"]
