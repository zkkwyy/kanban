/* 金融数据看板 — 前端逻辑 (纯静态, 数据来自 data/*.json) */
(function () {
  "use strict";

  var UPC = "#e5484d", DOWN = "#2fbf71";   // A股习惯: 涨红跌绿
  var C_INST = "#4f8cff", C_INDIV = "#d4a94f", C_GJS = "#b07cff";
  var charts = {};
  var state = { flowsMode: "cum", usTab: "regular", triCoef: null,
                triCoefTouched: false, us: null, flows: null, tri: null };

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
    var st = $("flows-stats");
    st.innerHTML = ""
      + stat("机构累计净申购(含国家队)", fmt(inst[inst.length - 1], 0) + " 亿", C_INST)
      + stat("个人累计净申购", fmt(indiv[indiv.length - 1], 0) + " 亿", C_INDIV)
      + stat("国家队持有ETF市值·估算", fmt(gjsAligned[gjsAligned.length - 1], 0) + " 亿", C_GJS)
      + stat("统计范围", "A股宽基ETF " + (flows.universe_count || 0) + " 只");

    var chart = charts.flows || echarts.init($("flows-chart"));
    charts.flows = chart;
    var mode = state.flowsMode;
    var series;
    if (mode === "cum") {
      series = [
        line("机构累计净申购(含国家队)", inst, C_INST),
        line("个人累计净申购", indiv, C_INDIV),
        line("国家队(汇金)持有ETF市值·估算", gjsAligned, C_GJS, true)
      ];
    } else {
      series = [
        { name: "机构日净申购", type: "bar", stack: "f", data: flows.daily_inst,
          itemStyle: { color: C_INST } },
        { name: "个人日净申购", type: "bar", stack: "f", data: flows.daily_indiv,
          itemStyle: { color: C_INDIV } }
      ];
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
      grid: { left: 60, right: 20, top: 30, bottom: 70 },
      tooltip: deepMerge({ trigger: "axis" }, TIP),
      legend: { textStyle: { color: "#7b8496" }, top: 0 },
      xAxis: deepMerge({ type: "category", data: dates }, AXIS),
      yAxis: deepMerge({ name: "亿元", scale: true }, AXIS),
      dataZoom: [
        { type: "inside" },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" } }
      ],
      series: series
    }, true);
    $("flows-note").textContent = "口径: " + (flows.method || "") +
      " 国家队曲线自" + (g.est_from || "—") +
      "起为估算延伸; 数据滞后于交易所披露, 每日收盘后更新。";
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
    $("margin-note").textContent = "口径: " + (m.method || "");
  }

  /* ---------------- 3. A股成交集中度 ---------------- */
  function renderConc(c) {
    var cur = c.cur, ma = c.ma20 ? c.ma20[c.ma20.length - 1] : null;
    $("conc-stats").innerHTML = ""
      + stat("当前集中度", fmt(cur, 1) + "%", "#39c5cf")
      + stat("MA20", fmt(ma, 1) + "%", "#d4a94f")
      + stat("历史中位数", fmt(c.median, 1) + "%")
      + stat("历史分位", fmt(c.pctile, 0) + "%",
             (c.pctile >= 80 || c.pctile <= 20) ? UPC : "#d7dde8")
      + stat("活跃个股", fmt(c.n_stocks[c.n_stocks.length - 1], 0) + " 只");
    var chart = charts.conc || echarts.init($("conc-chart"));
    charts.conc = chart;
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
        axisLabel: { formatter: function (v) { return v + "%"; } }
      }, AXIS),
      dataZoom: [
        { type: "inside" },
        { type: "slider", height: 18, bottom: 8, borderColor: "#2a3346",
          textStyle: { color: "#7b8496" } }
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
          }
        },
        {
          name: "MA20", type: "line", data: c.ma20, showSymbol: false,
          lineStyle: { color: "#d4a94f", width: 2 },
          itemStyle: { color: "#d4a94f" }
        }
      ]
    }, true);
    $("conc-note").textContent = "口径: " + (c.method || "") +
      " 集中度越高代表成交越向少数个股(龙头/抱团股)集中, 市场宽度越窄; 反之为普涨普跌的宽市场。";
  }

  /* ---------------- 4. 沪深300全收益 vs 隐形支撑线(K×二级债基) ---------------- */
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
    var bn = (sup.bottoms || []).map(function (b) {
      return b.label + "(" + b.date + (b.dev != null ? " " + (b.dev > 0 ? "+" : "") + b.dev + "%" : "") + ")";
    }).join("、");
    $("tri-note").textContent = "口径: " + (t.method || "") +
      " 已确认大底: " + (bn || "--") + "。" +
      " 说明: 原文0.43对应中证非纯债债基指数点位口径; 本看板为自建等权指数(基期1000), " +
      "经大底校准的等效系数 K=" + kLabel + "。" +
      " 大底平均偏离 " + (avgDev != null ? fmt(avgDev, 1) : "--") +
      "%, 当前偏离 " + (curDev === null ? "--" : (curDev > 0 ? "+" : "") + fmt(curDev, 1)) + "%。";
  }

  /* ---------------- 4. 美股板块 ---------------- */
  var PHASE_CN = { pre: "盘前", regular: "盘中", post: "盘后", closed: "休市" };
  function renderUS(us) {
    state.us = us;
    $("us-updated").textContent = "更新时间: " + (us.updated_ny || us.updated) +
      " (纽约时间, 每10分钟自动更新)";
    var chips = $("us-chips");
    chips.innerHTML = (us.rows || []).filter(function (r) { return r.kind === "index"; })
      .map(function (r) {
        return "<span class='chip'><span class='sym'>" + r.sym + "</span>" + r.cn +
          " " + pctHTML(r[state.usTab === "pre" ? "pre" : (state.usTab === "regular" ? "regular" : "post")]) + "</span>";
      }).join("");
    renderUSTable(us);
    $("us-note").textContent = "口径: " + (us.method || "");
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
