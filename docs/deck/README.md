# ROI deck — VP of Business Intelligence review

**[`attendance-forecast-roi.pptx`](attendance-forecast-roi.pptx)** — 9 slides,
ROI-first, built for a short review: the ask (fund Phase 0 + shadow only), the
problem in our park's numbers, the value math, netted payback, what already
exists, why it fits the BI stack, and the caveats stated before anyone else
finds them.

## The numbers in the deck

Park profile used throughout: attendance **600–6,000/day, ~1,500 average**,
**~300 employees**, current projections missing by **~±400 guests/day
(internal estimate)**. Derived mid-case: halving the miss ≈ **$37k–$62k/yr
captured** (at 10 guests/scalable-staff-hour, $22 loaded, 320 operating days,
30–50% capture), against ~$50k build + ~$16k/yr run → **break-even ~month
30** (mid) / ~month 21 (upside). Every estimated input is flagged on its
slide and is replaced by measurement in Phase 0 (model, on our data) and
Phase 1 (incumbent, logged in shadow). Full derivation and sensitivity:
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
