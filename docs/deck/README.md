# ROI deck — VP of Business Intelligence review

**[`attendance-forecast-roi.pptx`](attendance-forecast-roi.pptx)** — 9 slides,
ROI-first, built for a short review: the ask (fund Phase 0 + shadow only), the
problem in our park's numbers, the value math, netted payback, what already
exists, why it fits the BI stack, and the caveats stated before anyone else
finds them.

## The numbers in the deck

Park actuals used throughout: average attendance **3,285/day** (range
600–6,000), **open 364 days/year**, **~300 employees**, incumbent projections
at a **measured MAE of 947 guests/day** (28.8% of an average day; ticketing
history 10/2017–7/2026), model target **MAE 450** at the **month-ahead**
horizon (labor locks ~30 days out), wage **$15/hr** (≈ $18 loaded with ~20%
employer taxes/benefits). Derived mid-case: closing the 947→450 gap ≈
**$326k/yr gross**, **$98k–$163k/yr captured** at a 30–50% scheduling capture
rate, against ~$50k build + ~$16k/yr run → **break-even ~month 14** (mid) /
~month 23 (conservative: model only reaches MAE 650 at 30% capture). Still
estimated and flagged on the slides: guests-per-scalable-staff-hour (10) and
the capture rate; the model target is validated in Phase 0 on the nine
seasons of history. Full derivation and sensitivity:
[production guide, chapter 6](../production-guide/06-rollout.md).

## Rebuilding

```bash
cd docs/deck
npm install --no-save pptxgenjs react-icons react react-dom sharp
node make_deck.mjs      # writes attendance-forecast-roi.pptx
```

`make_deck.mjs` holds every number and word on the slides — edit it and
re-run rather than editing the .pptx, so the deck stays reproducible.
`assets-dashboard.png` is a screenshot of the live demo dashboard used on
slide 6; refresh it after a visual redesign of the dashboard.
