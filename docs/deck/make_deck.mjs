// Generates attendance-forecast-roi.pptx — a short, ROI-first deck for a VP of
// Business Intelligence. Numbers use THIS park's actuals: avg attendance 3,285,
// measured incumbent MAE 947 guests/day, model target MAE 450, $15 base wage
// (~$18 loaded), ~300 employees, ticketing history 10/2017-7/2026. Estimated
// inputs (guests/staff-hour, capture rate) are labeled as such on the slides.
// Rebuild: node make_deck.mjs  (from this directory).
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";
const require = createRequire(import.meta.url);
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const icons = require("react-icons/fi");

const HERE = path.dirname(fileURLToPath(import.meta.url));

// ---- palette: matches the live dashboard's Dali landscape brand ----
const INK = "2B2013";      // deep umber (dark slides)
const CREAM = "FFFDF6";
const WHITE = "FFFFFF";
const COBALT = "1268C8";   // dominant accent
const AMBER = "B3790A";    // secondary
const FLAME = "D24E1E";    // sharp accent, sparing
const SAND = "F1E8D2";     // tint for cards
const MUTED = "75674E";
const BODY = "3A2F1E";

const HEAD = "Cambria";
const SANS = "Calibri";

async function iconPng(name, hex) {
  const el = React.createElement(icons[name], { color: `#${hex}`, size: 512, strokeWidth: 2 });
  const svg = ReactDOMServer.renderToStaticMarkup(el);
  const buf = await sharp(Buffer.from(svg)).resize(512, 512).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5

const W = 13.33, H = 7.5, M = 0.7;

function kicker(slide, text, color = AMBER, y = 0.55) {
  slide.addText(text.toUpperCase(), {
    x: M, y, w: W - 2 * M, h: 0.3, fontFace: SANS, fontSize: 12, bold: true,
    color, charSpacing: 3, margin: 0,
  });
}
function title(slide, text, color = INK, y = 0.85, size = 34, w = W - 2 * M) {
  slide.addText(text, {
    x: M, y, w, h: 1.0, fontFace: HEAD, fontSize: size, bold: true,
    color, margin: 0,
  });
}
function footer(slide, n) {
  slide.addText(
    [{ text: "Attendance Forecasting — BI review", options: { color: MUTED } },
     { text: `   ·   ${n}`, options: { color: MUTED } }],
    { x: M, y: H - 0.42, w: 6, h: 0.3, fontFace: SANS, fontSize: 9, margin: 0 });
}
function iconCircle(slide, data, x, y, d = 0.52, fill = COBALT) {
  slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color: fill } });
  const pad = d * 0.24;
  slide.addImage({ data, x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad });
}

const main = async () => {
  const ic = {};
  for (const [k, hex] of [
    ["FiLock", WHITE], ["FiVolume2", WHITE], ["FiHelpCircle", WHITE],
    ["FiDatabase", WHITE], ["FiRefreshCw", WHITE], ["FiShield", WHITE],
    ["FiServer", WHITE], ["FiClock", WHITE], ["FiGitBranch", WHITE],
    ["FiCheckCircle", WHITE], ["FiBarChart2", WHITE], ["FiEye", WHITE],
  ]) ic[k] = await iconPng(k, hex);

  // ============ 1 · TITLE (dark) ============
  {
    const s = pres.addSlide();
    s.background = { color: INK };
    s.addText("PROPOSAL · FOR REVIEW — VP, BUSINESS INTELLIGENCE", {
      x: M, y: 1.5, w: W - 2 * M, h: 0.35, fontFace: SANS, fontSize: 13,
      bold: true, color: AMBER, charSpacing: 3, margin: 0,
    });
    s.addText("A forecast that pays for the schedule it writes", {
      x: M, y: 2.0, w: 11.6, h: 1.9, fontFace: HEAD, fontSize: 48, bold: true,
      color: CREAM, margin: 0,
    });
    s.addText(
      "Month-ahead attendance forecasting for the labor lock, week-ahead for " +
      "adjustments and ad spend — phase-gated, measured against our own " +
      "incumbent before operations changes anything.",
      { x: M, y: 4.0, w: 9.6, h: 0.9, fontFace: SANS, fontSize: 17,
        color: "D8CDB4", margin: 0 });
    s.addText(
      [{ text: "Working demo + full production guide in repo:  ", options: { color: MUTED } },
       { text: "github.com/lmansf/Attendance-Projections", options: { color: "D8CDB4" } }],
      { x: M, y: 6.6, w: 10, h: 0.35, fontFace: SANS, fontSize: 12, margin: 0 });
  }

  // ============ 2 · THE PROBLEM ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "The decision problem");
    title(s, "Labor locks a month out — on a 947 miss.");
    const rows = [
      ["FiLock", "Labor budgets commit ~30 days out", "The zoo plans rosters and labor for ~300 employees a month ahead — so the model's headline horizon is month-ahead, matching that lock."],
      ["FiVolume2", "Adjustments and ad spend at ~7 days", "Weekly schedule tweaks and media buys key on a week-ahead forecast; promos need soft days flagged early enough to act."],
      ["FiHelpCircle", "Today's miss is measured, and it is large", "Against our own 10/2017–7/2026 history, current projections average a 947-guest daily miss (MAE) — a 29% error on a 3,285-guest average day."],
    ];
    let y = 2.15;
    for (const [icn, h, b] of rows) {
      iconCircle(s, ic[icn], M, y + 0.05, 0.52, COBALT);
      s.addText(h, { x: M + 0.75, y, w: 6.1, h: 0.35, fontFace: SANS, fontSize: 16, bold: true, color: INK, margin: 0 });
      s.addText(b, { x: M + 0.75, y: y + 0.36, w: 6.1, h: 1.05, fontFace: SANS, fontSize: 13, color: BODY, margin: 0 });
      y += 1.55;
    }
    // right: what ±400 means, as stats
    s.addShape("roundRect", { x: 8.1, y: 2.1, w: 4.5, h: 4.6, rectRadius: 0.12, fill: { color: SAND } });
    s.addText("What a 947-guest average miss costs, daily", {
      x: 8.45, y: 2.4, w: 3.9, h: 0.6, fontFace: SANS, fontSize: 13, bold: true, color: MUTED, margin: 0 });
    s.addText("±29%", { x: 8.45, y: 3.0, w: 3.9, h: 0.85, fontFace: SANS, fontSize: 54, bold: true, color: FLAME, margin: 0 });
    s.addText("average error rate — measured, not guessed", {
      x: 8.45, y: 3.85, w: 3.9, h: 0.4, fontFace: SANS, fontSize: 12, color: BODY, margin: 0 });
    s.addText("~95 staff-hours", { x: 8.45, y: 4.5, w: 3.9, h: 0.6, fontFace: SANS, fontSize: 28, bold: true, color: INK, margin: 0 });
    s.addText("planned against the wrong number every day — roughly twelve 8-hour shifts (at ~10 guests served per scalable staff-hour, our one estimated input)", {
      x: 8.45, y: 5.1, w: 3.9, h: 1.2, fontFace: SANS, fontSize: 12, color: BODY, margin: 0 });
    footer(s, 2);
  }

  // ============ 3 · THE ROI MATH ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "The value, in our numbers");
    title(s, "Halving the miss: ~$86k–$143k a year.");
    // left: assumptions table
    const tbl = [
      [{ text: "Assumption", options: { bold: true, color: MUTED } }, { text: "Value", options: { bold: true, color: MUTED } }, { text: "Status", options: { bold: true, color: MUTED } }],
      ["Average daily attendance", "3,285", "measured, from ticketing"],
      ["Incumbent MAE", "947 guests", "measured, 10/2017–7/2026"],
      ["Model MAE (target)", "450 guests", { text: "target — Phase 0 validates", options: { color: FLAME } }],
      ["Guests / scalable staff-hour", "10", { text: "estimate — to confirm", options: { color: FLAME } }],
      ["Loaded cost / staff-hour", "$18", "$15 wage + ~20% load"],
      ["Operating days / year", "320", "our calendar"],
      ["Capture rate", "30–50%", "min shifts, fixed posts"],
      ["Forecast horizon", "month-ahead", "matches the labor lock"],
    ];
    s.addTable(tbl.map(r => r.map(c => typeof c === "string" ? { text: c, options: { color: BODY } } : c)), {
      x: M, y: 2.15, w: 5.9, colW: [2.7, 1.35, 1.85], fontFace: SANS, fontSize: 11.5,
      border: { type: "solid", color: "E6DCC2", pt: 0.75 }, rowH: 0.34,
      fill: { color: WHITE }, valign: "middle", margin: 0.06,
    });
    // right: derivation stack
    const steps = [
      ["497", "fewer mis-forecast guests per day (947 → 450)"],
      ["~50 hrs", "staff-hours re-aimed daily (÷ 10 guests/staff-hour)"],
      ["$895 / day", "at the $18 loaded rate"],
      ["~$286k / yr", "gross misallocation removed (× 320 days)"],
    ];
    let y = 2.1;
    for (const [big, small] of steps) {
      s.addText(big, { x: 7.3, y, w: 2.6, h: 0.5, fontFace: SANS, fontSize: 22, bold: true, color: COBALT, align: "right", margin: 0 });
      s.addText(small, { x: 10.05, y: y + 0.05, w: 2.6, h: 0.62, fontFace: SANS, fontSize: 11.5, color: BODY, margin: 0 });
      y += 0.68;
    }
    s.addShape("roundRect", { x: 7.3, y: 5.0, w: 5.35, h: 1.5, rectRadius: 0.12, fill: { color: INK } });
    s.addText("$86k–$143k / yr", { x: 7.6, y: 5.2, w: 4.8, h: 0.6, fontFace: SANS, fontSize: 30, bold: true, color: CREAM, margin: 0 });
    s.addText("captured at a 30–50% scheduling capture rate — every row above is a live slider in the Power BI what-if.",
      { x: 7.6, y: 5.8, w: 4.8, h: 0.6, fontFace: SANS, fontSize: 11.5, color: "D8CDB4", margin: 0 });
    s.addText("A range, never a point: the incumbent side is measured; Phase 0 validates the model side on nine seasons of our own data.",
      { x: M, y: 6.65, w: 5.9, h: 0.5, fontFace: SANS, fontSize: 10.5, italic: true, color: MUTED, margin: 0 });
    footer(s, 3);
  }

  // ============ 4 · PAYBACK ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "Net of costs");
    title(s, "Break-even ~month 15 in the mid case.");
    s.addChart("line", [
      { name: "Mid case (450 MAE, 40% capture)", labels: ["0", "6", "12", "18", "24", "30", "36"], values: [0, -50, -25, 24, 73, 122, 172] },
      { name: "Conservative (model only reaches 650 MAE, 30%)", labels: ["0", "6", "12", "18", "24", "30", "36"], values: [0, -50, -41, -24, -6, 12, 29] },
    ], {
      x: M, y: 2.0, w: 7.3, h: 4.6,
      chartColors: [COBALT, AMBER],
      lineSize: 3, lineSmooth: false,
      showLegend: true, legendPos: "b", legendFontSize: 11, legendColor: BODY,
      catAxisTitle: "Months from funding", showCatAxisTitle: true,
      catAxisTitleColor: MUTED, catAxisTitleFontSize: 11,
      valAxisTitle: "Cumulative net, $k", showValAxisTitle: true,
      valAxisTitleColor: MUTED, valAxisTitleFontSize: 11,
      catAxisLabelColor: MUTED, valAxisLabelColor: MUTED,
      catAxisLabelFontSize: 11, valAxisLabelFontSize: 11,
      valGridLine: { color: "EDE6D2", size: 0.75 }, catGridLine: { style: "none" },
      showTitle: false, showValue: false,
    });
    const chips = [
      ["~$50k build", "8–12 person-weeks, existing team; DuckDB path, one small VM"],
      ["~$16k / yr run", "0.1 FTE + Power BI Pro. US zoo: NWS weather is $0; no flight-schedule feed unless the backtest earns it"],
      ["Break-even ~month 15", "mid case (~$114k/yr captured, net of run cost)"],
      ["Even the conservative case pays back", "~month 26 if the model only closes a third of the gap — and if it can't beat 947 at all, Phase 0 kills it for ~$8k, not $50k"],
    ];
    let y = 2.0;
    for (const [h, b] of chips) {
      s.addShape("roundRect", { x: 8.5, y, w: 4.15, h: 1.02, rectRadius: 0.1, fill: { color: SAND } });
      s.addText(h, { x: 8.75, y: y + 0.1, w: 3.7, h: 0.32, fontFace: SANS, fontSize: 14, bold: true, color: INK, margin: 0 });
      s.addText(b, { x: 8.75, y: y + 0.42, w: 3.7, h: 0.56, fontFace: SANS, fontSize: 10.5, color: BODY, margin: 0 });
      y += 1.17;
    }
    footer(s, 4);
  }

  // ============ 5 · THE ASK & GATES ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "The ask");
    title(s, "Fund Phase 0 and shadow. Nothing else, yet.");
    const phases = [
      ["0 · POC", "wks 1–4", "Backtest on our own ticketing extract. Go / no-go for ~$8k of time.", COBALT],
      ["1 · SHADOW", "mo 2–5", "Daily forecasts logged and scored. Ops changes nothing. Gates signed before it starts.", COBALT],
      ["2 · ASSISTED", "mo 5–8", "Planners see the report at schedule lock, adjust freely, overrides logged.", AMBER],
      ["3 · INTEGRATED", "mo 9+", "Template seeded from the forecast; manager keeps override; reversion gates active.", AMBER],
    ];
    let x = M;
    const bw = 2.85, gap = 0.25;
    for (const [ph, when, body, color] of phases) {
      s.addShape("roundRect", { x, y: 2.15, w: bw, h: 2.5, rectRadius: 0.1, fill: { color: SAND } });
      s.addText(ph, { x: x + 0.22, y: 2.35, w: bw - 0.44, h: 0.35, fontFace: SANS, fontSize: 15, bold: true, color, margin: 0 });
      s.addText(when, { x: x + 0.22, y: 2.7, w: bw - 0.44, h: 0.3, fontFace: SANS, fontSize: 11, bold: true, color: MUTED, margin: 0 });
      s.addText(body, { x: x + 0.22, y: 3.05, w: bw - 0.44, h: 1.5, fontFace: SANS, fontSize: 11.5, color: BODY, margin: 0 });
      x += bw + gap;
    }
    s.addShape("roundRect", { x: M, y: 5.1, w: W - 2 * M, h: 1.35, rectRadius: 0.12, fill: { color: INK } });
    s.addText(
      [{ text: "The kill point is priced.  ", options: { bold: true, color: CREAM } },
       { text: "Gates missed in shadow → stop; sunk cost is engineering time only, and operations never changed a thing. " +
               "Funding today covers Phase 0 + shadow; the schedule template is not touched until the system beats our incumbent on our own data, at margins we sign in advance.",
         options: { color: "D8CDB4" } }],
      { x: M + 0.35, y: 5.3, w: W - 2 * M - 0.7, h: 1.0, fontFace: SANS, fontSize: 13.5, margin: 0 });
    footer(s, 5);
  }

  // ============ 6 · WHAT EXISTS TODAY ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "De-risked");
    title(s, "This is not a cold start.");
    s.addImage({
      path: path.join(HERE, "assets-dashboard.png"),
      x: M, y: 2.1, w: 5.9, h: 4.3,
      shadow: { type: "outer", blur: 12, offset: 5, angle: 90, color: "6B5B3E", opacity: 0.4 },
    });
    const facts = [
      ["FiEye", "A working demo is live", "The dashboard exists end-to-end today: month-, week-, and day-ahead forecasts vs actual, an 80% planning band, and a labor-budget panel priced at the month-ahead lock."],
      ["FiGitBranch", "The model earned its seat", "An equal-weight LightGBM + Ridge + random-forest blend, picked by a reproducible 17-candidate backtest (single command; a neural net and 5 other families lost)."],
      ["FiCheckCircle", "The production path is written", "A 6-chapter guide in the repo: warehouse DDL, leakage-firewall feature contract, monitoring thresholds, and the Power BI DAX for every panel — reviewed for accuracy, coherence, and exec scrutiny."],
    ];
    let y = 2.15;
    for (const [icn, h, b] of facts) {
      iconCircle(s, ic[icn], 7.1, y + 0.05, 0.52, AMBER);
      s.addText(h, { x: 7.85, y, w: 4.8, h: 0.35, fontFace: SANS, fontSize: 15, bold: true, color: INK, margin: 0 });
      s.addText(b, { x: 7.85, y: y + 0.35, w: 4.8, h: 1.1, fontFace: SANS, fontSize: 12, color: BODY, margin: 0 });
      y += 1.5;
    }
    footer(s, 6);
  }

  // ============ 7 · BUILT FOR BI ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "For the BI team specifically");
    title(s, "It lands in our stack, not beside it.");
    const cells = [
      ["FiBarChart2", "Power BI native", "Star schema + written DAX, incl. what-if sliders for guests/staff-hour and loaded rate"],
      ["FiRefreshCw", "Import mode, 1–2 refreshes/day", "Pro licensing is enough; no DirectQuery, no gateway on the DuckDB/parquet path"],
      ["FiDatabase", "PostgreSQL or DuckDB", "Single-digit MB per park-year; raw → staging → marts, idempotent loads"],
      ["FiShield", "No guest PII, by design", "Aggregate counts only — fast security review, no DPIA, low vendor-risk tier"],
      ["FiServer", "One small VM", "The entire pipeline; scheduled jobs with quality gates and a fallback forecast"],
      ["FiClock", "Append-only forecast history", "“What did we tell ops last Tuesday?” is a query, not an argument"],
    ];
    let i = 0;
    for (const [icn, h, b] of cells) {
      const cx = M + (i % 3) * 4.05, cy = 2.25 + Math.floor(i / 3) * 2.25;
      iconCircle(s, ic[icn], cx, cy, 0.52, COBALT);
      s.addText(h, { x: cx, y: cy + 0.62, w: 3.7, h: 0.55, fontFace: SANS, fontSize: 14.5, bold: true, color: INK, margin: 0 });
      s.addText(b, { x: cx, y: cy + 1.12, w: 3.7, h: 0.95, fontFace: SANS, fontSize: 11.5, color: BODY, margin: 0 });
      i++;
    }
    footer(s, 7);
  }

  // ============ 8 · HONEST CAVEATS ============
  {
    const s = pres.addSlide();
    s.background = { color: WHITE };
    kicker(s, "What we are not claiming", FLAME);
    title(s, "The caveats, before anyone else finds them.");
    const rows = [
      ["The model's side is provisional", "The incumbent MAE (947) is measured on our own history; the 450 target is not yet — the demo runs on synthetic data. Phase 0 validates the model on our nine seasons before gates are set."],
      ["Year 1 is still cash-negative", "Benefits start ~month 9; mid-case break-even lands mid-year-2. If Phase 0 says the 450 target is fantasy, we stop at the kill point having spent engineering time only."],
      ["The best feature arrives late", "Advance-booking snapshots start accumulating on day one and mature around month 10; shadow is judged without them, and the model only improves after the gates pass."],
      ["Month-ahead is the hardest horizon", "No weather is knowable 30 days out, so the month-ahead model runs on calendar, seasonality, bookings, and flight schedules alone — its MAE will sit above the week-ahead model's. The 450 target is judged at this horizon because that is where labor locks."],
      ["Regime breaks happen", "A closure or a viral season breaks any model. Built in: drift monitoring, a same-weekday fallback that always publishes, and band logic that discards broken-regime history."],
    ];
    let y = 2.15;
    for (const [h, b] of rows) {
      s.addText(h, { x: M, y, w: 3.9, h: 1.0, fontFace: SANS, fontSize: 14.5, bold: true, color: FLAME, margin: 0 });
      s.addText(b, { x: 4.9, y, w: 7.7, h: 1.0, fontFace: SANS, fontSize: 12.5, color: BODY, margin: 0 });
      y += 1.15;
    }
    footer(s, 8);
  }

  // ============ 9 · CLOSE (dark) ============
  {
    const s = pres.addSlide();
    s.background = { color: INK };
    s.addText("THE DECISION TODAY", {
      x: M, y: 1.7, w: W - 2 * M, h: 0.35, fontFace: SANS, fontSize: 13, bold: true,
      color: AMBER, charSpacing: 3, margin: 0 });
    s.addText("Approve Phase 0: four weeks, our own data, a go/no-go with evidence.", {
      x: M, y: 2.2, w: 11.9, h: 1.6, fontFace: HEAD, fontSize: 38, bold: true, color: CREAM, margin: 0 });
    const bullets = [
      "One ticketing extract — 10/2017 to 7/2026, nearly nine seasons of daily admissions; no integrations, no vendors",
      "The existing backtest re-run on our numbers: model vs our rule of thumb, out-of-fold",
      "Output: a measured accuracy delta and a re-priced ROI table — then, and only then, the shadow decision",
    ];
    s.addText(bullets.map((t, i) => ({
      text: t, options: { bullet: { code: "2022" }, color: "D8CDB4", breakLine: i < bullets.length - 1, paraSpaceAfter: 10 },
    })), { x: M, y: 4.15, w: 10.6, h: 1.7, fontFace: SANS, fontSize: 15, margin: 0 });
    s.addText(
      [{ text: "Demo, guide, and this deck:  ", options: { color: MUTED } },
       { text: "github.com/lmansf/Attendance-Projections  ·  docs/production-guide", options: { color: "D8CDB4" } }],
      { x: M, y: 6.55, w: 11.9, h: 0.35, fontFace: SANS, fontSize: 12, margin: 0 });
  }

  await pres.writeFile({ fileName: path.join(HERE, "attendance-forecast-roi.pptx") });
  console.log("written attendance-forecast-roi.pptx");
};

main().catch(e => { console.error(e); process.exit(1); });
