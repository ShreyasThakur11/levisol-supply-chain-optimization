# Levisol Supply Chain Case - Methodology & Assumptions
*Project 2 - Inventory Norms, Production & Distribution Optimisation, Planning Tool*

---

## 0. Data used (traceability)

Single source of truth: **`ab2f19f3f7454b1aa0f33186bcd07af5.xlsx`** (the case's
machine-readable data file - per its READ ME sheet it takes precedence over the
PDF wherever they differ).

| Sheet | Used for |
|---|---|
| A - Plants & Production | Line capacities (kL/mo) per plant × 5 lines; production cost ₹/kL (BOM 12 000, AHM 12 500, KOL 9 000) |
| B - Plant-Hub Transport | Primary freight ₹/kL (3 plants × 2 hubs) |
| C - Hub-CFA Transport | Secondary freight ₹/kL (2 hubs × 10 CFAs) |
| D - SKU Portfolio | 100 SKUs; pack size → production line; penalty ₹/kL; contractual flag (18 SKUs) |
| E - Source + LT data | 957 SKU×CFA rows; historical source (hub) + production/transit lead times & variability (days) |
| F - Service Levels | Tier fill-rate targets: A 98%, B 97%, C 92%, D 92%; volume slabs 50/30/15/5% |
| G - Sales History | Jul-Dec 2025 actual sales (kL) - demand levels, tier classification, days-of-cover base |
| H - Forecast History | Jul-Dec 2025 forecasts - forecast-error measurement |
| I - Opening Inventory | Jan-26 opening stock: 957 CFA rows + hub rows (MHW 100 SKUs / 608.5 kL, MHE 51 SKUs / 347.6 kL) |
| J - Jan Forecast | Planning-period demand: 8 109.7 kL total across 957 SKU×CFA |

**PDF↔Excel discrepancy found and resolved:** the PDF's Exhibit A table is
garbled by its layout (it reads as if production cost at KOL were ₹200/kL and
the 180-210LT capacity 12 000 kL). The data file's clean table (costs 12 000 /
12 500 / 9 000 ₹/kL; 180-210LT capacities 2 450 / 2 200 / 200 kL) is used, as
the case instructs. The workbook's Service-Level sheet is titled "Exhibit G"
inside the file (off-by-one exhibit lettering); content matches PDF Exhibit F.

**Data quality notes:** 2 small negative monthly sales cells (returns netting,
kept as-is, −0.13 kL total); 16 SKU×CFA rows with zero sales across all 6
months (norms suppressed and flagged; 4 of them have positive Jan forecast
which the plan still supplies); 29 rows have zero Jan forecast (handled
naturally); AHM's 50LT capacity of 220 kL is not a 25 kL multiple → effective
usable capacity 200 kL in whole batches; pack sizes quoted in KG (e.g.
1 X 180 KG) are classed into the 180-210LT line (drum class; volumes are
already in kL everywhere, so no unit conversion is involved).

---

## 1. Component 1 - Inventory norms

### 1.1 Tier classification (Exhibit F volume slabs)
SKUs ranked by total 6-month sales volume; cumulative-share slabs:

| Tier | Slab | SKUs | Actual share | Fill-rate target |
|---|---|---|---|---|
| A | top 50% of volume | 15 | 50.0% | 98% |
| B | next 30% | 34 | 30.0% | 97% |
| C | next 15% | 33 | 15.0% | 92% |
| D | last 5% | 18 | 5.0% | 92% |

### 1.2 Demand and demand-uncertainty per SKU×CFA
- Mean daily demand **μ_d = (mean monthly sales Jul-Dec) / 30** (case: 30
  working days/month).
- Demand uncertainty **σ_d = stdev(forecast errors, bias removed, n−1) / √30**.

*Why bias-removed forecast error:* replenishment is planned against the
forecast (Exhibits H/J), so the risk safety stock must absorb is **forecast
error** - this jointly captures demand variability and forecast quality, the
two effects the case asks us to account for. But safety stock should buffer
only the **random** part of that error: systematic bias is a demand-planning
defect to be corrected, not warehoused. Sizing stock on raw RMSE would, for
example, give one 8-litre-a-month SKU-CFA a 5.4 kL safety stock (56 years of
cover) purely because its forecast is broken. Instead, the 211 SKU×CFA rows
where |bias| exceeds the error σ are flagged **FORECAST-BIAS** on the
Exceptions sheet for demand-planning review, and the 7 rows whose days of
cover still exceed 90 days are flagged **HIGH-DOC**. (Portfolio-level bias is
≈0: −0.04 kL per row-month.) The √30 scaling converts a monthly error σ to a
daily one under independent daily demand.

### 1.3 Lead times - each step buffered exactly once (multi-echelon)
The network holds stock at two echelons, and the case requires hubs to keep
their own 98% safety stock. The hub is therefore the **decoupling point**:

- **CFA norms** buffer the CFA's replenishment loop from its hub:
  **LT = hub→CFA transit** (1-9 days, Exhibit E, historical source
  predefined), **σ_LT = transit variability** (±1 to ±3 days; in the data it
  scales with transit length).
- **Hub norms** buffer the hub's replenishment loop from the plants:
  **LT = production lead time + plant→hub transit (1 day)**,
  **σ_LT = production variability** (the data's transit-variability column
  belongs to the hub→CFA leg).

Every step of the case's "production, dispatch, transit - each carries its own
variability" chain is thus buffered exactly once, at the echelon that owns it.
Sizing CFA stock on the full end-to-end lead time *while also* holding hub
safety stock would double-buffer the upstream legs and tie up cash against the
case's explicit mandate to release it. (That conservative end-to-end variant
is still computed and reported per row as `ss_end_to_end_alt_kl`: it would add
+981 kL of CFA safety stock - the measured price of ignoring the hub echelon.)

### 1.4 Fill-rate targets → stock (the case leaves the translation to us)
**Tiered translation, mirroring the dual mandate "protect service on critical
SKUs while rapidly releasing cash":**
- **Tiers A and B (49 SKUs, 80% of volume):** cycle-service-level reading,
  z = Φ⁻¹(target) → 2.054 / 1.881. Deliberately conservative - with a
  base-oil shock running, the flagship portfolio is protected against demand
  *and* lead-time tails.
- **Tiers C and D (51 SKUs, 20% of volume):** exact fill-rate translation via
  the normal loss function: solve **G(z) = (1−β)·Q/σ_LTD** with Q = one
  month's demand (monthly replenishment cycle), z floored at 0 and capped at
  2.054. Because monthly cycle stock alone already covers most of a 92%
  fill-rate target, this releases nearly all C/D safety stock while still
  meeting the stated target.

```
SS  = z × √( LT·σ_d² + μ_d²·σ_LT² )      (variable demand + variable LT)
ROP = μ_d × LT + SS
DOC = ROP / μ_d        (days of average last-6-months demand; 0 if μ_d = 0)
```

### 1.5 Results
| Level | Safety stock | Reorder point | Notes |
|---|---|---|---|
| CFA total (957 rows) | **1 465.1 kL** | **2 442.0 kL** | A 811.3 / B 621.5 / C 22.1 / D 10.2 kL |
| Mother Hub West | **1 156.2 kL** | 2 718.5 kL | 98% SL, z=2.054, 100 SKUs |
| Mother Hub East | **332.0 kL** | 737.8 kL | 98% SL, 51 East-sourced SKUs |

Median CFA days of cover: **6 days**. Hub norms use hub demand = sum of the
CFA rows each hub historically serves (Source column: East→MHE, others→MHW),
with variances added across CFAs (independence per case instruction), so risk
pooling keeps hub buffers far below the sum of CFA-level equivalents.

**Sensitivity (methodology choices, total CFA SS):**
raw-RMSE σ + end-to-end LT + all-CSL z (legacy conservative read): **3 304 kL**
→ bias-corrected σ + end-to-end LT + tiered z: **2 446 kL**
→ bias-corrected σ + decoupled LT + all-CSL z: **1 750 kL**
→ **recommended (bias-corrected σ + decoupled LT + tiered z): 1 465 kL**.
The recommended norm releases ≈1 839 kL (~56%) of CFA buffer stock vs the
legacy conservative reading while holding A/B protection at cycle-service
level - this is the quantified "protect service, release cash" trade.

---

## 2. Component 2 - Production & distribution plan (Jan 2026)

### 2.1 Requirements derived from the norms
Per SKU×CFA:
- **Sales-at-risk D₁ = max(0, JanForecast − CFA opening stock)** = 5 136.3 kL.
- **Full requirement R = max(0, JanForecast + SS_CFA − CFA opening stock)** =
  6 523.1 kL (serve January and end the month at the safety-stock norm).
Per SKU×hub: end January at **≥ SS_hub** (soft constraint).

### 2.2 The optimisation model (mixed-integer linear program)
Decisions: batches n(SKU, plant) ∈ ℤ₊ (25 kL each, only where the SKU's line
capacity > 0); flows plant→hub and hub→CFA in kL; shortfall variables.

Objective (minimise):
```
  Σ production cost + Σ primary freight + Σ secondary freight
+ Σ penalty × (3× if contractual) × unmet-demand
+ 0.25 × Σ penalty × (3× if contractual) × safety-stock shortfall (CFA & hub)
```
Constraints in plain language:
1. A plant ships exactly what it produces; production in whole 25 kL batches.
2. Per plant per line: production ≤ Exhibit A line capacity.
3. A hub cannot ship more than opening stock + inbound (hub stock never negative).
4. Unmet demand = undelivered sales-at-risk; buffer shortfall = undelivered
   remainder of R; hub shortfall = month-end gap to hub SS.

*Why these weights:* unmet demand costs the full Exhibit-D penalty - real
cash. A missed **safety-stock top-up** is not a lost sale, it is increased
*risk* of a future one, so it carries a 0.25 weight: high enough that buffers
are always filled when capacity economically allows (0.25 × minimum penalty =
₹32.5k/kL exceeds the costliest make-and-ship route ≈ ₹22.5k/kL), low enough
that real demand always wins under scarcity. Contractual SKUs carry a 3×
multiplier on both terms - the case says their under-supply is "commercially
damaging beyond the immediate lost sales" but gives no number, so they are
protected lexicographically ahead of comparable non-contractual volume.
(Neither dial changed this month's answer: the optimum serves **all** demand,
so no penalty and no multiplier was ever paid.) No inventory-holding cost is
priced because the case provides none; the tool has a `holding_cost_per_kl`
dial (default 0) if management later wants working-capital pressure inside
the objective.

### 2.3 Exact solution by line decomposition
SKUs interact **only** through plant line capacities and each SKU belongs to
exactly one line ⇒ the MILP separates into 5 independent problems, each solved
to **proven optimality (gap 0.0)** with HiGHS. Verification: a monolithic
120-second solve finds nothing better (₹119.64M incumbent vs ₹119.38M
decomposed optimum); the LP relaxation bounds the theoretical
fractional-batch ideal at ₹105.09M - the 13.6% difference is the unavoidable,
correctly-priced cost of the 25 kL batch rule, not sub-optimality.

### 2.4 Result headlines
- **All January demand served - zero lost sales, zero penalty cost.**
- Production **7 725 kL** = 309 batches: BOM 4 325, KOL 2 550, AHM 850.
  KOL (₹9 000/kL) runs every line at 100%; BOM's cheap <=1.5LT line is full;
  AHM (₹12 500/kL) is the swing plant with slack for the day-of scenario.
- **Total cash cost ₹114 411 784** = production 85 475 000 + plant→hub freight
  10 719 136 + hub→CFA freight 18 217 648 + penalty 0.
- Safety-stock shortfalls left: 68.1 kL at CFAs + 31.5 kL at hubs (≈1.2% of
  buffer requirement; risk-weighted memo value ₹3.16M + ₹1.41M). Every one is
  batch-quantisation economics: covering a small residual would need an extra
  25 kL batch whose cash cost exceeds the buffer's risk value - verified
  case-by-case (zero profitable batch opportunities missed).
- Hubs end January **above** aggregate safety-stock targets (MHW 1 636 vs
  1 156; MHE 590 vs 332) because 25 kL batch remainders are parked at hubs.

### 2.5 Flow economics (why the network routes this way)
Landed production+freight per kL: to MHW - BOM ₹13 000 < AHM ₹16 500 < KOL
₹19 000; to MHE - KOL ₹10 100 < AHM ₹17 500 < BOM ₹20 000. So East demand
pulls from KOL/MHE, West/South from BOM/MHW, and AHM tops up whatever the
cheap plants' line capacities cannot cover. Kanpur (North) is served mainly
via MHE (₹3 100 < ₹3 800 from MHW) - the data's historical sourcing agrees.
Small counter-intuitive flows (e.g. KOL→MHW ~11 kL) are batch remainders
already paid for at the cheap plant being shipped to where they are useful.

---

## 3. Component 3 - Planning tool

`Levisol_Planning_Tool.py` (+ engine `levisol_engine.py`, writer
`levisol_output.py`). GUI (double-click → browse → RUN PLAN) or CLI
(`python Levisol_Planning_Tool.py input.xlsx [output.xlsx]`). Reads any
workbook in the case-file layout - a planner changes demand, capacities,
costs, rates, lead times or service levels in Excel only; nothing is
hard-coded. Output: one workbook (plan, flows, norms, hub stock positions,
cost summary, Exceptions). Robustness by construction: shortfall variables
make every instance feasible, so capacity shortages produce a ranked shortage
report instead of a crash; zero-demand and zero-history rows are flagged;
empty lines are excluded. Tested with three assessment-day style modified
input sets (capacity shock, +40% demand, freight shock) - see final report.

---

## 4. Assumptions register

| # | Assumption | Why unavoidable | Impact / risk |
|---|---|---|---|
| 1 | Tiered fill-rate translation (A/B: CSL z; C/D: exact fill-rate, z∈[0, 2.054]) | Case explicitly delegates the translation | A/B conservative; C/D lean. All-CSL variant = +981 kL SS (quantified §1.5) |
| 2 | σ_d = bias-removed forecast-error std (n−1, 6 obs), scaled by √30 | Only monthly data; forecasts drive replenishment | Small-sample noise; bias rows flagged instead of stocked; re-estimated monthly by the tool |
| 3 | Hub = decoupling point (CFA LT = hub→CFA leg only) | Hubs hold mandatory 98% SS by case design | If a hub stocks out (≤2%), CFA replenishment stretches; end-to-end variant reported per row |
| 4 | Buffer-shortfall weight 0.25×penalty; contractual multiplier 3× (on unmet **and** buffers) | Case prices unmet demand only | Zero effect on this month's optimum (no unmet demand); dials documented in the tool |
| 5 | Hub replenishment variability = production variability only | Data ties transit variability to the hub→CFA leg; plant→hub is a fixed 1 day | Slightly understates hub SS if plant→hub transit varies in reality |
| 6 | No inventory holding cost in the objective | None provided | Batch remainders parked at hubs; `holding_cost_per_kl` dial available (default 0) |
| 7 | Plants hold no month-end stock (produce = ship) | Hubs are the network's buffer points by design | Matches case intent |
| 8 | Zero-sales-history rows get zero norms (flagged) | Norms "must be calculated basis historical data" | 4 such rows have Jan demand - still supplied; norms held for manual review |
| 9 | CFA demands independent across CFAs (hub variance = Σ variances) | Explicit case instruction | If demand is positively correlated in reality, hub SS understates the 98% target |
| 10 | Negative sales cells kept as-is (net returns, −0.13 kL) | Data as given | Negligible |
| 11 | KG drum packs (180 KG) classed in the 180-210LT line | No density data; volumes already in kL | Line assignment only; no volume conversion involved |

## 5. Limitations
- Single-period (January) optimisation; no multi-month smoothing of the
  ~698 kL hub stock build from batch rounding.
- σ estimated from 6 monthly observations; norms should be re-estimated every
  month as data accrues (the tool recomputes them on every run).
- Variability inputs are single ±day figures with unstated distributions;
  normality is assumed at both echelons.
- Fill-rate method for C/D assumes one replenishment cycle per month
  (Q = monthly demand).

## 6. Verification performed (all reproducible)
1. **Independent recomputation** of all 957 CFA and 151 hub norms from raw
   data via a second code path: max deviation ≤ 1.4×10⁻¹⁴ kL.
2. **Feasibility audit** (independent): 25 kL batching, line capacities, plant
   and hub mass balances, CFA demand accounting, non-negative hub stock - zero
   violations; all cost components re-derived to < 10⁻⁶ relative error.
3. **Optimality audit:** per-line MILP gaps 0.0; monolithic cross-check cannot
   beat the decomposed optimum; LP lower bound computed; zero profitable
   missed-batch opportunities; zero mispriced lanes among the largest flows.
4. **Scenario robustness:** capacity-shock / demand-spike / freight-shock
   modified inputs run end-to-end without failure, shortages reported ranked
   by commercial priority.
