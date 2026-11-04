/* 金融数据看板 — 前端逻辑 (纯静态, 数据来自 data/*.json) */
(function () {
  "use strict";

  var UPC = "#e5484d", DOWN = "#2fbf71";   // A股习惯: 涨红跌绿
  var C_INST = "#4f8cff", C_INDIV = "#d4a94f", C_GJS = "#b07cff";
  var charts = {};
  var state = { flowsMode: "cum", usTab: "regular", usPhase: null, usTabTouched: false,
                triCoef: null, triCoefTouched: false, us: null, flows: null, tri: null };

  function $(id) { return document.getElementById(id); }
  function fmt(n, d) {
    if (n === null || n === undefined || isNaN(n)) return "--";
    d = d === undefined ? 1 : d;
    return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fetchJSON(url) {
    return fetch(url + "?t=" + Date.now()).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }
  function pctClass(v) { return v > 0 ? "up" : (v < 0 ? "down" : ""); }
  function sign(n) { return n > 0 ? "+" : ""; }

  /* ---------------- 通用图表主题 ---------------- */
  var AXIS = {
    axisLine: { lineStyle: { color: "#2a3346" } },
    axisLabel: { color: "#7b8496" },
    splitLine: { lineStyle: { color: "#212937" } }
  };
  var TIP = {
    backgroundColor: "#1c2330",
    borderColor: "#2a3346",
    textStyle: { color: "#d7dde8", fontSize: 12 },
    confine: true
  };

  /* ---------------- 1. ETF资金流 ---------------- */
  // 事件标注(竖排文字: 多行formatter+offset, 避免相邻事件标签水平重叠)
  function verticalMark(dateStr, text) {
    var t = (text || "").replace(/\s+/g, "");
    return {
      xAxis: dateStr,
      label: {
        formatter: t.split("").join("\n"),
        color: "#9aa4b8", fontSize: 11, lineHeight: 13,
        offset: [8, t.length * 13]
      },
      lineStyle: { color: "#5a6478", type: "dashed" }
    };
  }

  function renderFlows(flows) {
    state.flows = flows;
    var dates = flows.dates;
    var gjsMap = {};
    var g = flows.gjs || {};
    (g.months || []).forEach(function (m, i) { gjsMap[m] = (g.values || [])[i]; });
    var gjsAligned = dates.map(function (d) { return gjsMap[d.slice(0, 7)]; });
    var inst = flows.inst_cum, indiv = flows.indiv_cum;
    // 对比线: 中证全指(万得全A的免费等价口径), 前向填充对齐到flows日期
    var idxAligned = null, idxLast = null, idxChg = null;
    var fi = flows.index;
    if (fi && fi.dates && fi.dates.length) {
      var imap = {};
      fi.dates.forEach(function (d, i) { imap[d] = fi.values[i]; });
      idxLast = null;
      idxAligned = dates.map(function (d) {
        if (imap[d] !== undefined) idxLast = imap[d];
        return idxLast;
      });
      if (idxLast && fi.values[0]) {
        idxChg = (idxLast / fi.values[0] - 1) * 100;
      }
    }
    var st = $("flows-stats");
    st.innerHTML = ""
      + stat("机构累计净申购(含国家队)", fmt(inst[inst.length - 1], 0) + " 亿", C_INST)
      + stat("个人累计净申购", fmt(indiv[indiv.length - 1], 0) + " 亿", C_INDIV)
      + stat("国家队持有ETF市值·估算", fmt(gjsAligned[gjsAligned.length - 1], 0) + " 亿", C_GJS)
      + (idxLast ? stat("中证全指(万得全A口径)", fmt(idxLast, 0) + " 点" +
          (idxChg !== null ? " (" + sign(idxChg) + fmt(idxChg, 1) + "%)" : ""), "#aab3c5") : "")
      + stat("统计范围", "A股宽基ETF " + (flows.universe_count || 0) + " 只");

    var chart = charts.flows || echarts.init($("flows-chart"));
    charts.flows = chart;
    var mode = state.flowsMode;
    var idxSeries = null;
    if (idxAligned) {
      idxSeries = {
        name: "中证全指(右轴)", type: "line", yAxisIndex: 1, data: idxAligned,
        showSymbol: false, connectNulls: true,
        lineStyle: { color: "#8a93a6", width: 1.5, opacity: 0.9 },
        itemStyle: { color: "#8a93a6" },
        emphasis: { focus: "series" }
      };
    }
    var series;
    if (mode === "cum") {
      series = [
        line("机构累计净申购(含国家队)", inst, C_INST),
        line("个人累计净申购", indiv, C_INDIV),
        line("国家队(汇金)持有ETF市值·估算", gjsAligned, C_GJS, true)
      ];
      if (idxSeries) series.push(idxSeries);
    } else {
      series = [
        { name: "机构日净申购", type: "bar", stack: "f", data: flows.daily_inst,
          itemStyle: { color: C_INST } },
        { name: "个人日净申购", type: "bar", stack: "f", data: flows.daily_indiv,
          itemStyle: { color: C_INDIV } }
      ];
      if (idxSeries) series.push(idxSeries);
    }
    // 事件标注(竖排文字, 避免相邻事件标签重叠)
    var markLines = (flows.events || []).map(function (e) {
      var idx = dates.indexOf(e.date);
      if (idx < 0) { // 找最近的日期
        for (var i = 0; i < dates.length; i++) if (dates[i] > e.date) { idx = i; break; }
      }
      return verticalMark(idx >= 0 ? dates[idx] : e.date, e.text);
    });
    if (markLines.length && series[0]) {
      series[0].markLine = { symbol: "none", data: markLines, silent: true };
    }
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 60, right: idxSeries ? 56 : 20, top: 30, bottom: 70 },
      tooltip: deepMerge({
        trigger: "axis",
        formatter: function (ps) {
          var h = ps[0].name + "<br>";
          for (var i = 0; i < ps.length; i++) {
            var p = ps[i];
            var isIdx = p.seriesName.indexOf("中证全指") >= 0;
            h += p.marker + " " + p.seriesName + ": <b>" +
                 (p.value === null || p.value === undefined ? "--" : fmt(p.value, isIdx ? 0 : 1)) +
                 (isIdx ? " 点" : " 亿") + "</b><br>";
          }
          return h;
        }
      }, TIP),
      legend: { textStyle: { color: "#7b8496" }, top: 0 },
      xAxis: deepMerge({ type: "category", data: dates }, AXIS),
      yAxis: [
        deepMerge({ name: "亿元", scale: true }, AXIS),
        deepMerge({ name: "点位", scale: true, position: "right",
                    splitLine: { show: false } }, AXIS)
      ],
      dataZoom: [
        { type: "inside" },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" } }
      ],
      series: series
    }, true);
  }

  function line(name, data, color, dashed) {
    return {
      name: name, type: "line", data: data, showSymbol: false,
      lineStyle: { color: color, width: 2, type: dashed ? "dashed" : "solid" },
      itemStyle: { color: color },
      emphasis: { focus: "series" }
    };
  }

  /* ---------------- 2. 融资余额 ---------------- */
  function renderMargin(m) {
    var dates = m.dates, rzye = m.rzye;
    var last = rzye[rzye.length - 1];
    var firstThisYear = null;
    for (var i = 0; i < dates.length; i++) {
      if (dates[i].slice(0, 4) === dates[dates.length - 1].slice(0, 4)) { firstThisYear = i; break; }
    }
    var ytd = firstThisYear !== null ? last - rzye[firstThisYear] : null;
    $("margin-stats").innerHTML = ""
      + stat("最新融资余额", fmt(last, 0) + " 亿元")
      + stat("较年初", sign(ytd) + fmt(ytd, 0) + " 亿", ytd > 0 ? UPC : DOWN)
      + stat("数据日期", dates[dates.length - 1]);
    var chart = charts.margin || echarts.init($("margin-chart"));
    charts.margin = chart;
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 60, right: 20, top: 30, bottom: 70 },
      tooltip: deepMerge({
        trigger: "axis",
        formatter: function (ps) {
          var p = ps[0];
          return p.name + "<br>融资余额: <b>" + fmt(p.value, 0) + "</b> 亿元";
        }
      }, TIP),
      xAxis: deepMerge({ type: "category", data: dates }, AXIS),
      yAxis: deepMerge({ name: "亿元", scale: true }, AXIS),
      dataZoom: [
        { type: "inside" },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" } }
      ],
      series: [{
        name: "融资余额", type: "line", data: rzye, showSymbol: false,
        lineStyle: { color: "#4f8cff", width: 2 },
        itemStyle: { color: "#4f8cff" },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(79,140,255,0.35)" },
            { offset: 1, color: "rgba(79,140,255,0.02)" }
          ])
        }
      }]
    }, true);
  }

  /* ---------------- 3. A股成交集中度 ---------------- */
  function renderConc(c) {
    var cur = c.cur, ma = c.ma20 ? c.ma20[c.ma20.length - 1] : null;
    var oh = c.overheat;
    var overheated = oh != null && ma != null && ma > oh;
    $("conc-stats").innerHTML = ""
      + stat("当前集中度", fmt(cur, 1) + "%", "#39c5cf")
      + stat("MA20", fmt(ma, 1) + "%", "#d4a94f")
      + stat("过热判定", overheated ? "过热 ↑" : "未过热",
             overheated ? UPC : "#d7dde8")
      + stat("历次大顶均值", oh != null ? fmt(oh, 1) + "%" : "--", "#e5484d")
      + stat("活跃个股", fmt(c.n_stocks[c.n_stocks.length - 1], 0) + " 只");
    var chart = charts.conc || echarts.init($("conc-chart"));
    charts.conc = chart;
    var mk = (c.peaks || []).map(function (p) {
      return verticalMark(p.date, p.label);
    });
    if (oh != null) {
      mk.push({
        yAxis: oh,
        lineStyle: { color: "#e5484d", type: "dashed", width: 1.5 },
        label: { formatter: "过热线(历次大顶均值) " + fmt(oh, 1) + "%",
                 color: "#e5484d", fontSize: 10, position: "insideEndTop" }
      });
    }
    var n = c.dates.length;
    var zs = n > 30 ? c.dates[n - 30] : c.dates[0];
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 60, right: 20, top: 30, bottom: 70 },
      tooltip: deepMerge({
        trigger: "axis",
        formatter: function (ps) {
          var h = ps[0].name + "<br>";
          for (var i = 0; i < ps.length; i++) {
            var p = ps[i];
            h += p.marker + " " + p.seriesName + ": <b>" +
                 (p.value === null || p.value === undefined ? "--" : fmt(p.value, 2)) +
                 "%</b><br>";
          }
          return h;
        }
      }, TIP),
      legend: { textStyle: { color: "#7b8496" }, top: 0 },
      xAxis: deepMerge({ type: "category", data: c.dates }, AXIS),
      yAxis: deepMerge({
        name: "%", scale: true,
        min: function (v) { return Math.max(0, (v.min || 0) - 3); },
        max: function (v) { return Math.max((v.max || 0) + 3, (oh || 0) + 3); },
        axisLabel: { formatter: function (v) { return v + "%"; } }
      }, AXIS),
      dataZoom: [
        { type: "inside", startValue: zs, endValue: c.dates[n - 1] },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" }, startValue: zs, endValue: c.dates[n - 1] }
      ],
      series: [
        {
          name: "日度集中度", type: "line", data: c.conc, showSymbol: false,
          lineStyle: { color: "#39c5cf", width: 1.5 },
          itemStyle: { color: "#39c5cf" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(57,197,207,0.25)" },
              { offset: 1, color: "rgba(57,197,207,0.02)" }
            ])
          },
          markLine: mk.length ? { symbol: "none", data: mk, silent: true } : undefined
        },
        {
          name: "MA20", type: "line", data: c.ma20, showSymbol: false,
          lineStyle: { color: "#d4a94f", width: 2 },
          itemStyle: { color: "#d4a94f" }
        }
      ]
    }, true);
  }

  /* ---------------- 4. 全市场情绪(站上均线比例) ---------------- */
  function renderSent(s) {
    var cur5 = s.cur5, cur10 = s.cur10, cur20 = s.cur20;
    var verdict, vColor;
    if (cur20 >= 80) { verdict = "超买"; vColor = UPC; }
    else if (cur20 <= 20) { verdict = "超卖"; vColor = DOWN; }
    else { verdict = "中性"; vColor = "#d7dde8"; }
    $("sent-stats").innerHTML = ""
      + stat("站上5日均线", fmt(cur5, 1) + "%", "#e5484d")
      + stat("站上10日均线", fmt(cur10, 1) + "%", "#d4a94f")
      + stat("站上20日均线", fmt(cur20, 1) + "%", "#4f8cff")
      + stat("情绪判读", verdict, vColor)
      + stat("活跃个股", fmt(s.n_stocks[s.n_stocks.length - 1], 0) + " 只");
    var chart = charts.sent || echarts.init($("sent-chart"));
    charts.sent = chart;
    var sn = s.dates.length;
    var szs = sn > 30 ? s.dates[sn - 30] : s.dates[0];
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 60, right: 20, top: 30, bottom: 70 },
      tooltip: deepMerge({
        trigger: "axis",
        formatter: function (ps) {
          var h = ps[0].name + "<br>";
          for (var i = 0; i < ps.length; i++) {
            var p = ps[i];
            h += p.marker + " " + p.seriesName + ": <b>" +
                 (p.value === null || p.value === undefined ? "--" : fmt(p.value, 1)) +
                 "%</b><br>";
          }
          return h;
        }
      }, TIP),
      legend: { textStyle: { color: "#7b8496" }, top: 0 },
      xAxis: deepMerge({ type: "category", data: s.dates }, AXIS),
      yAxis: deepMerge({
        name: "%", min: 0, max: 100,
        axisLabel: { formatter: function (v) { return v + "%"; } }
      }, AXIS),
      dataZoom: [
        { type: "inside", startValue: szs, endValue: s.dates[sn - 1] },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" }, startValue: szs, endValue: s.dates[sn - 1] }
      ],
      series: [
        {
          name: "站上5日线", type: "line", data: s.above5, showSymbol: false,
          lineStyle: { color: "#e5484d", width: 1.5 },
          itemStyle: { color: "#e5484d" },
          emphasis: { focus: "series" },
          markLine: {
            symbol: "none", silent: true,
            data: [
              { yAxis: 80, lineStyle: { color: "#5a6478", type: "dashed" },
                label: { formatter: "超买 80%", color: "#7b8496", fontSize: 10,
                         position: "insideEndTop" } },
              { yAxis: 50, lineStyle: { color: "#39425a", type: "solid", width: 1 },
                label: { formatter: "中位 50%", color: "#7b8496", fontSize: 10,
                         position: "insideEndTop" } },
              { yAxis: 20, lineStyle: { color: "#5a6478", type: "dashed" },
                label: { formatter: "超卖 20%", color: "#7b8496", fontSize: 10,
                         position: "insideStartTop" } }
            ]
          }
        },
        {
          name: "站上10日线", type: "line", data: s.above10, showSymbol: false,
          lineStyle: { color: "#d4a94f", width: 1.5 },
          itemStyle: { color: "#d4a94f" },
          emphasis: { focus: "series" }
        },
        {
          name: "站上20日线", type: "line", data: s.above20, showSymbol: false,
          lineStyle: { color: "#4f8cff", width: 2 },
          itemStyle: { color: "#4f8cff" },
          emphasis: { focus: "series" }
        }
      ]
    }, true);
  }

  /* ---------------- 5. 沪深300全收益 vs 隐形支撑线(K×二级债基) ---------------- */
  function renderTri(t) {
    state.tri = t;
    var dates = t.dates, tri = t.tri;
    var bd = t.bond_dates || [], bv = t.bond || [];
    var bmap = {};
    for (var i = 0; i < bd.length; i++) bmap[bd[i]] = bv[i];
    var bondAligned = [], last = null;
    for (var j = 0; j < dates.length; j++) {
      if (bmap[dates[j]] !== undefined) last = bmap[dates[j]];
      bondAligned.push(last);
    }
    var sup = t.support || {};
    if (!state.triCoefTouched) {
      state.triCoef = sup.k || 1;
      var ci = $("coef-input");
      if (ci) ci.value = state.triCoef;
    }
    var k = state.triCoef || sup.k || 1;
    // 偏离度序列: H00300 / (k×债基) - 1
    var dev = [];
    for (var m = 0; m < dates.length; m++) {
      dev.push(bondAligned[m] ? (tri[m] / (k * bondAligned[m]) - 1) * 100 : null);
    }
    var curDev = dev[dev.length - 1];
    var avgDev = sup.avg_abs_dev;
    var tLast = tri[tri.length - 1];
    var lineLast = k * (bondAligned[bondAligned.length - 1] || 0);
    var bottomMarks = (sup.bottoms || []).map(function (b) {
      return verticalMark(b.date, b.label);
    });
    var bottomMarksPlain = (sup.bottoms || []).map(function (b) {
      return { xAxis: b.date, lineStyle: { color: "#39425a", type: "dashed" },
               label: { show: false } };
    });
    var stats = ""
      + stat("当前偏离隐形线", (curDev === null ? "--" :
          (curDev > 0 ? "+" : "") + fmt(curDev, 1) + "%"),
          curDev > 0 ? UPC : DOWN)
      + stat("隐形线 K×债基指数", fmt(lineLast, 0))
      + stat("沪深300全收益", fmt(tLast, 0) + " 点", "#4f8cff")
      + stat("大底平均偏离", avgDev != null ? "±" + fmt(avgDev, 1) + "%" : "--")
      + stat("系统性大底", (sup.bottom_count || 0) + " 个")
      + stat("年化(2005至今)", fmt(sup.cagr_tri, 1) + "% vs " + fmt(sup.cagr_bond, 1) + "%");
    $("tri-stats").innerHTML = stats;

    var chart = charts.tri || echarts.init($("tri-chart"));
    charts.tri = chart;
    var kLabel = (Math.round(k * 10000) / 10000);
    var kTxt = k === 1 ? "" : kLabel + " × ";
    chart.setOption({
      backgroundColor: "transparent",
      grid: [
        { left: 60, right: 20, top: 45, height: 175 },
        { left: 60, right: 20, top: 250, height: 105 }
      ],
      tooltip: deepMerge({
        trigger: "axis",
        formatter: function (ps) {
          var d = ps[0].name, h = "";
          for (var i = 0; i < ps.length; i++) {
            var p = ps[i];
            h += p.marker + " " + p.seriesName + ": <b>" +
              (p.value === null || p.value === undefined ? "--" : fmt(p.value, 2)) +
              (p.seriesName.indexOf("偏离") >= 0 ? "%" : "") + "</b><br>";
          }
          return d + "<br>" + h;
        }
      }, TIP),
      legend: { textStyle: { color: "#7b8496" }, top: 4,
                data: ["沪深300全收益指数", kTxt + "二级债基指数 · 隐形支撑线", "偏离度"] },
      xAxis: [
        deepMerge({ type: "category", gridIndex: 0, data: dates,
                    axisLabel: { show: false }, axisTick: { show: false } }, AXIS),
        deepMerge({ type: "category", gridIndex: 1, data: dates }, AXIS)
      ],
      yAxis: [
        deepMerge({ gridIndex: 0, name: "点", scale: true }, AXIS),
        deepMerge({ gridIndex: 1, name: "偏离%", scale: false,
                    axisLabel: {
                      formatter: function (v) { return (v > 0 ? "+" : "") + v + "%"; }
                    } }, AXIS)
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", height: 18, bottom: 8, xAxisIndex: [0, 1],
          borderColor: "#2a3346", textStyle: { color: "#7b8496" } }
      ],
      series: [
        {
          name: "沪深300全收益指数", type: "line", data: tri,
          showSymbol: false, xAxisIndex: 0, yAxisIndex: 0,
          lineStyle: { color: "#4f8cff", width: 2 },
          itemStyle: { color: "#4f8cff" },
          emphasis: { focus: "series" },
          markLine: { symbol: "none", data: bottomMarks, silent: true }
        },
        {
          name: kTxt + "二级债基指数 · 隐形支撑线", type: "line",
          data: bondAligned.map(function (v) { return v === null ? null : v * k; }),
          showSymbol: false, xAxisIndex: 0, yAxisIndex: 0,
          lineStyle: { color: "#d4a94f", width: 2, type: "solid" },
          itemStyle: { color: "#d4a94f" },
          emphasis: { focus: "series" }
        },
        {
          name: "偏离度", type: "line", data: dev, showSymbol: false,
          xAxisIndex: 1, yAxisIndex: 1,
          lineStyle: { color: "#b07cff", width: 1.5 },
          itemStyle: { color: "#b07cff" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(176,124,255,0.30)" },
              { offset: 1, color: "rgba(176,124,255,0.02)" }
            ])
          },
          markLine: {
            symbol: "none", silent: true,
            data: (function () {
              var md = (bottomMarksPlain || []).concat([
                { yAxis: 0, lineStyle: { color: "#5a6478", type: "solid", width: 1 },
                  label: { show: false } }
              ]);
              if (avgDev != null) {
                md.push({ yAxis: avgDev,
                  lineStyle: { color: "#d4a94f", type: "dashed" },
                  label: { formatter: "大底平均偏离 +" + fmt(avgDev, 1) + "%",
                           color: "#d4a94f", fontSize: 10, position: "insideEndTop" } });
                md.push({ yAxis: -avgDev,
                  lineStyle: { color: "#d4a94f", type: "dashed" },
                  label: { show: false } });
              }
              return md;
            })()
          }
        }
      ]
    }, true);
  }


  /* ---------------- 6. 美股板块 ---------------- */
  var PHASE_CN = { pre: "盘前", regular: "盘中", post: "盘后", closed: "休市" };
  var PHASE_TAB = { pre: "pre", regular: "regular", post: "post", closed: "post" };
  function syncUsToggle() {
    var box = $("us-toggle");
    if (!box) return;
    Array.prototype.forEach.call(box.children, function (b) {
      if (b.tagName !== "BUTTON") return;
      b.classList.toggle("on", b.getAttribute("data-tab") === state.usTab);
    });
  }
  function renderUS(us) {
    state.us = us;
    // 默认跟随最新阶段: 阶段变化时自动切到对应tab(手动选择保留到下次阶段切换)
    if (state.usPhase !== us.phase) {
      state.usPhase = us.phase;
      state.usTabTouched = false;
      if (PHASE_TAB[us.phase]) state.usTab = PHASE_TAB[us.phase];
      // 休市且无盘后数据时回退到盘中
      if (us.phase === "closed") {
        var anyPost = (us.rows || []).some(function (r) {
          return r.post !== null && r.post !== undefined;
        });
        if (!anyPost) state.usTab = "regular";
      }
    }
    $("us-updated").textContent = "更新时间: " + (us.updated_ny || us.updated) +
      " (纽约时间, 每10分钟自动更新)";
    var chips = $("us-chips");
    chips.innerHTML = (us.rows || []).filter(function (r) { return r.kind === "index"; })
      .map(function (r) {
        return "<span class='chip'><span class='sym'>" + r.sym + "</span>" + r.cn +
          " " + pctHTML(r[state.usTab === "pre" ? "pre" : (state.usTab === "regular" ? "regular" : "post")]) + "</span>";
      }).join("");
    renderUSTable(us);
    syncUsToggle();
    var b = $("badge-us-phase");
    b.textContent = "美股" + (PHASE_CN[us.phase] || us.phase);
    b.className = "badge" + (us.phase === "closed" ? "" : " live");
  }

  function pctHTML(v) {
    if (v === null || v === undefined) return "<span class='pct na'>--</span>";
    return "<span class='pct " + pctClass(v) + "'>" + sign(v) + fmt(v, 2) + "%</span>";
  }

  function renderUSTable(us) {
    var tab = state.usTab;
    var key = tab;
    var rows = (us.rows || []).filter(function (r) { return r.kind !== "index"; });
    rows.sort(function (a, b) {
      var av = a[key], bv = b[key];
      if (av === null || av === undefined) av = -1e9;
      if (bv === null || bv === undefined) bv = -1e9;
      return bv - av;
    });
    var maxAbs = 1;
    rows.forEach(function (r) {
      if (r[key] !== undefined && r[key] !== null) maxAbs = Math.max(maxAbs, Math.abs(r[key]));
    });
    var priceKey = { pre: "pre_price", regular: "regular_price", post: "post_price" }[tab];
    var html = "<table class='us'><thead><tr><th class='rank'>#</th><th class='sym'>代码</th>" +
      "<th class='name'>名称</th><th class='pct'>" + PHASE_CN[tab] + "涨跌幅</th>" +
      "<th class='price'>最新价</th><th class='bar-cell'>幅度</th></tr></thead><tbody>";
    rows.forEach(function (r, i) {
      var v = r[key];
      var bar = "";
      if (v !== undefined && v !== null) {
        var w = Math.min(50, Math.abs(v) / maxAbs * 50);
        bar = "<div class='bar-bg'><div class='bar-zero'></div><div class='bar " +
          pctClass(v) + "' style='width:" + w + "%'></div></div>";
      }
      html += "<tr><td class='rank'>" + (i + 1) + "</td><td class='sym'>" + r.sym +
        "</td><td class='name'>" + r.cn + (r.kind === "sector" ? "" : "") +
        "</td><td class='pct " + (v === undefined || v === null ? "na" : pctClass(v)) + "'>" +
        (v === undefined || v === null ? "无数据" : sign(v) + fmt(v, 2) + "%") +
        "</td><td class='price'>" + (r[priceKey] !== undefined ? fmt(r[priceKey], 2) : "--") +
        "</td><td class='bar-cell'>" + bar + "</td></tr>";
    });
    html += "</tbody></table>";
    $("us-table").innerHTML = html;
  }

  function stat(k, v, color) {
    return "<div class='stat'><div class='k'>" + k + "</div><div class='v'" +
      (color ? " style='color:" + color + "'" : "") + ">" + v + "</div></div>";
  }

  function deepMerge(base, extra) {
    for (var k in extra) {
      if (extra[k] && typeof extra[k] === "object" && base[k] && typeof base[k] === "object") {
        deepMerge(base[k], extra[k]);
      } else { base[k] = extra[k]; }
    }
    return base;
  }

  /* ---------------- 元信息 & 刷新 ---------------- */
  function renderMeta(meta) {
    $("badge-cn").textContent = "A股数据 " + (meta.cn_updated || "--");
    $("badge-us").textContent = "美股数据 " + (meta.us_updated || "--");
    var link = $("repo-link");
    if (meta.repo_url) { link.href = meta.repo_url; link.textContent = "源码/更新日志"; }
  }

  function loadAll() {
    fetchJSON("data/etf_flows.json").then(renderFlows).catch(showErr("flows"));
    fetchJSON("data/margin.json").then(renderMargin).catch(showErr("margin"));
    fetchJSON("data/concentration.json").then(renderConc).catch(showErr("conc"));
    fetchJSON("data/sentiment.json").then(renderSent).catch(showErr("sent"));
    fetchJSON("data/tri_bond.json").then(renderTri).catch(showErr("tri"));
    fetchJSON("data/us_sectors.json").then(renderUS).catch(showErr("us"));
    fetchJSON("data/meta.json").then(renderMeta).catch(function () {});
  }

  function showErr(which) {
    return function (e) {
      console.error(which, e);
    };
  }

  /* 交互绑定 */
  $("flows-toggle").addEventListener("click", function (e) {
    if (e.target.tagName !== "BUTTON") return;
    Array.prototype.forEach.call(this.children, function (b) { b.classList.remove("on"); });
    e.target.classList.add("on");
    state.flowsMode = e.target.getAttribute("data-mode");
    if (state.flows) renderFlows(state.flows);
  });

  $("us-toggle").addEventListener("click", function (e) {
    if (e.target.tagName !== "BUTTON") return;
    Array.prototype.forEach.call(this.children, function (b) { b.classList.remove("on"); });
    e.target.classList.add("on");
    state.usTab = e.target.getAttribute("data-tab");
    state.usTabTouched = true;   // 手动选择: 保留直到下次阶段切换
    if (state.us) { renderUS(state.us); }
  });

  $("us-refresh").addEventListener("click", function () {
    fetchJSON("data/us_sectors.json").then(renderUS).catch(function () {});
    fetchJSON("data/meta.json").then(renderMeta).catch(function () {});
  });

  var coefTimer = null;
  $("coef-input").addEventListener("input", function () {
    var v = parseFloat(this.value);
    if (isNaN(v) || v <= 0) return;
    state.triCoef = v;
    state.triCoefTouched = true;
    clearTimeout(coefTimer);
    coefTimer = setTimeout(function () {
      if (state.tri) renderTri(state.tri);
    }, 300);
  });

  window.addEventListener("resize", function () {
    Object.keys(charts).forEach(function (k) { charts[k].resize(); });
  });

  loadAll();
  setInterval(function () {  // 美股每60秒自动刷新
    fetchJSON("data/us_sectors.json").then(renderUS).catch(function () {});
    fetchJSON("data/meta.json").then(renderMeta).catch(function () {});
  }, 60000);
  setInterval(loadAll, 30 * 60 * 1000);  // A股图表每30分钟刷新
})();
