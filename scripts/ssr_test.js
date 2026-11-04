/* SSR渲染验证: 与app.js完全一致的配置, 验证竖排标注无重叠、在图内 */
const path = require("path");
const fs = require("fs");
const ROOT = path.resolve(__dirname, "..");
const echarts = require(path.join(ROOT, "assets/echarts.min.js"));

const flows = JSON.parse(fs.readFileSync(path.join(ROOT, "data/etf_flows.json"), "utf8"));
const tri = JSON.parse(fs.readFileSync(path.join(ROOT, "data/tri_bond.json"), "utf8"));

function render(width, height, option) {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  chart.setOption(option);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return svg;
}

/* 与 app.js verticalMark 完全一致 */
function verticalMark(dateStr, text) {
  const t = (text || "").replace(/\s+/g, "");
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

function analyzeLabels(svg) {
  const re = /<text\b([^>]*)>([^<]*)<\/text>/g;
  let m;
  const lines = [];
  while ((m = re.exec(svg)) !== null) {
    const c = m[2].trim();
    if (!c || c.length !== 1 || !/[\u4e00-\u9fff0-9A-Z]/.test(c)) continue;
    const attrs = m[1];
    const tm = attrs.match(/translate\(([\d.\-]+)[ ,]+([\d.\-]+)\)/);
    const ym = attrs.match(/\by="([\d.\-]+)"/);
    const xm = attrs.match(/\bx="([\d.\-]+)"/);
    if (!tm) continue;
    const lx = xm ? parseFloat(xm[1]) : 0, ly = ym ? parseFloat(ym[1]) : 0;
    const px = parseFloat(tm[1]) + lx;
    if (px < 70) continue;   // 跳过左侧y轴刻度文字(非标注)
    lines.push({ c, x: px, y: parseFloat(tm[2]) + ly });
  }
  // 按x聚类成标注列
  const cols = {};
  lines.forEach(l => {
    const key = Math.round(l.x / 6) * 6;
    (cols[key] = cols[key] || []).push(l);
  });
  return Object.keys(cols).map(Number).sort((a, b) => a - b).map(x => {
    const g = cols[x].sort((a, b) => a.y - b.y);
    return { x, chars: g.map(l => l.c).join(""), y0: g[0].y, y1: g[g.length - 1].y, n: g.length };
  });
}

function check(name, svg, yMin, yMax) {
  const cols = analyzeLabels(svg);
  let fail = 0;
  console.log(`\n[${name}] ${cols.length}列:`);
  cols.forEach(c => {
    const inb = c.y0 >= yMin - 5 && c.y1 <= yMax + 5;
    console.log(`  x=${c.x} "${c.chars}" y[${c.y0.toFixed(0)},${c.y1.toFixed(0)}] ${inb ? "ok" : "!!越界"}`);
    if (!inb) fail++;
  });
  for (let i = 1; i < cols.length; i++) {
    if (cols[i].x - cols[i - 1].x < 12) {
      console.log(`  !!列重叠 x=${cols[i - 1].x}与${cols[i].x} 间距${cols[i].x - cols[i - 1].x}px`);
      fail++;
    }
  }
  if (!fail) console.log("  全部通过");
  return fail;
}

/* 模块① option(与app.js一致的关键部分) */
function flowsOption() {
  const dates = flows.dates;
  const marks = (flows.events || []).map(e => {
    let idx = dates.indexOf(e.date);
    if (idx < 0) { for (let i = 0; i < dates.length; i++) if (dates[i] > e.date) { idx = i; break; } }
    return verticalMark(dates[Math.max(0, idx)], e.text);
  });
  return {
    grid: { left: 60, right: 20, top: 30, bottom: 70 },
    xAxis: { type: "category", data: dates },
    yAxis: { scale: true },
    series: [{ type: "line", data: flows.inst_cum, showSymbol: false,
      markLine: { symbol: "none", data: marks, silent: true } }]
  };
}

/* 模块③ option(与app.js一致: 双grid) */
function triOption() {
  const dates = tri.dates, triV = tri.tri;
  const sup = tri.support || {};
  const k = sup.k || 1.52;
  const bmap = {};
  (tri.bond_dates || []).forEach((d, i) => { bmap[d] = tri.bond[i]; });
  const bondAligned = []; let last = null;
  dates.forEach(d => { if (bmap[d] !== undefined) last = bmap[d]; bondAligned.push(last); });
  const dev = dates.map((d, i) => bondAligned[i] ? (triV[i] / (k * bondAligned[i]) - 1) * 100 : null);
  const bottomMarks = (sup.bottoms || []).map(b => verticalMark(b.date, b.label));
  const bottomPlain = (sup.bottoms || []).map(b => ({
    xAxis: b.date, lineStyle: { color: "#39425a", type: "dashed" }, label: { show: false }
  }));
  return {
    grid: [
      { left: 60, right: 20, top: 45, height: 175 },
      { left: 60, right: 20, top: 250, height: 105 }
    ],
    xAxis: [
      { type: "category", gridIndex: 0, data: dates, axisLabel: { show: false }, axisTick: { show: false } },
      { type: "category", gridIndex: 1, data: dates }
    ],
    yAxis: [{ gridIndex: 0, scale: true }, { gridIndex: 1 }],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", height: 18, bottom: 8, xAxisIndex: [0, 1] }
    ],
    series: [
      { type: "line", data: triV, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0,
        markLine: { symbol: "none", data: bottomMarks, silent: true } },
      { type: "line", data: bondAligned.map(v => v == null ? null : v * k), showSymbol: false, xAxisIndex: 0, yAxisIndex: 0 },
      { type: "line", data: dev, showSymbol: false, xAxisIndex: 1, yAxisIndex: 1,
        markLine: { symbol: "none", silent: true, data: bottomPlain } }
    ]
  };
}

/* 模块③ 集中度: 历次大顶竖标+过热线(与app.js renderConc一致) */
function concOption(zoom30) {
  const c = JSON.parse(fs.readFileSync(path.join(ROOT, "data/concentration.json"), "utf8"));
  const oh = c.overheat;
  const mk = (c.peaks || []).map(p => verticalMark(p.date, p.label));
  if (oh != null) {
    mk.push({
      yAxis: oh,
      lineStyle: { color: "#e5484d", type: "dashed", width: 1.5 },
      label: { formatter: "过热线(历次大顶均值) " + oh.toFixed(1) + "%",
               color: "#e5484d", fontSize: 10, position: "insideEndTop" }
    });
  }
  const n = c.dates.length;
  const zs = zoom30 && n > 30 ? c.dates[n - 30] : undefined;
  const zoom = zs ? { startValue: zs, endValue: c.dates[n - 1] } : {};
  return {
    grid: { left: 60, right: 20, top: 30, bottom: 70 },
    xAxis: { type: "category", data: c.dates },
    yAxis: { scale: true,
      min: v => Math.max(0, (v.min || 0) - 3),
      max: v => Math.max((v.max || 0) + 3, (oh || 0) + 3) },
    dataZoom: [
      Object.assign({ type: "inside" }, zoom),
      Object.assign({ type: "slider", height: 18, bottom: 8 }, zoom)
    ],
    series: [{ type: "line", data: c.conc, showSymbol: false,
      markLine: { symbol: "none", data: mk, silent: true } }]
  };
}

let fail = 0;
const W = 1100, H = 420;

fail += check("模块①-ETF事件标注(全视图)", render(W, H, flowsOption()), 30, 350);
fail += check("模块④-大底标注(全视图)", render(W, H, triOption()), 45, 220);
fail += check("模块③-集中度历顶标注(全视图)", render(W, H, concOption(false)), 30, 350);

// 30天默认窗口: 过热线标签必须可见(检查SVG中含"过热线"文字)
const svg30 = render(W, H, concOption(true));
if (/过热线/.test(svg30)) {
  console.log("[模块③-30天窗口] 过热线可见 ✓");
} else {
  console.log("[模块③-30天窗口] !! 过热线不可见");
  fail++;
}
const cols30 = analyzeLabels(svg30);
if (cols30.length === 0) {
  console.log("[模块③-30天窗口] 窗口外竖标已隐藏 ✓");
} else {
  console.log("[模块③-30天窗口] !! 窗口外竖标残留: " + cols30.map(x => x.chars).join(","));
  fail++;
}

console.log(fail ? `\nRESULT: ${fail} 项问题` : "\nRESULT: 全部通过");
process.exit(fail ? 1 : 0);
