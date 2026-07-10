# Levisol Monthly Planning Tool - User Guide

*A tool for the Levisol supply-chain planning team. No data-science or coding
knowledge is required to run it.*

---

## What it does

Every month, the tool takes **one Excel workbook of inputs** (the same layout
as the case data file) and produces **one Excel workbook of outputs**:

| Output sheet | Contents |
|---|---|
| Cost Summary | Production + transport + penalty cost of the plan |
| Production Plan | kL of each SKU at each plant, in 25 kL batches |
| Plant-Hub Flows | Volume and cost on each plant → hub lane |
| Hub-CFA Dispatch | Volume and cost on each hub → CFA lane |
| Hub Stock Position | Month-end hub stock vs the 98% safety-stock target |
| Norms - CFA | Safety stock, reorder point, days of cover per SKU × CFA |
| Norms - Hub | The same norms at hub level |
| Exceptions | Every unmet demand line and safety-stock shortfall, with reasons |

## How to run it

**Option A - point and click**
1. Double-click `src/planning_tool.py` (needs Python 3 with
   `pandas`, `scipy`, `openpyxl`, `xlsxwriter` - a one-time IT setup).
2. Browse to the month's input workbook.
3. Press **RUN PLAN**. Results appear in `Levisol_Plan_Output.xlsx`
   next to your input file.

**Option B - command line**
```
python src/planning_tool.py  <input.xlsx>  [output.xlsx]
```

## Changing inputs for a new month / scenario

Edit the input workbook in Excel - nothing else changes:

| To change… | Edit sheet |
|---|---|
| Demand forecast per SKU per CFA | `J - Jan Forecast` (values column) |
| Opening stock at CFAs and hubs | `I - Expected opening Inventory` |
| Plant line capacities, production cost | `A - Plants & Production` |
| Plant → hub freight rates | `B - Plant-Hub Transport` |
| Hub → CFA freight rates | `C - Hub-CFA Transport` |
| Penalty cost / contractual flags | `D - SKU Portfolio+Penalty matrix` |
| Lead times & variability | `E - Source + LT data` |
| Service-level targets per tier | `F - Service Levels` |
| Sales / forecast history (norms basis) | `G`, `H` |

The tool re-reads everything each run; formulas are never hard-coded to cells.

## What happens when things go wrong (by design, it never just crashes)

- **Demand exceeds capacity** → the tool serves the highest-priority demand
  first (contractual SKUs, then highest penalty cost) and lists every
  shorted line on the **Exceptions** sheet with volume and penalty cost.
- **Zero demand for an SKU/CFA** → norms are computed as zero and flagged;
  the optimiser simply ships nothing there.
- **An SKU with no sales history** → its norm is suppressed and flagged
  `NO-SALES-6M` for manual review rather than inventing a number.
- **A capacity cell set to 0** → that plant line is excluded automatically.

## The two dials a planner may adjust (defaults are the recommended values)

| Dial | Default | Meaning |
|---|---|---|
| Working days / month | 30 | Per the case document |
| Hub service level | 0.98 | Case rule: 98% for all grades at hubs |

Advanced (function parameters, documented in the methodology report):

| Parameter | Default | Meaning |
|---|---|---|
| `cfa_lt_mode` | `decoupled` | CFA norms buffer the hub→CFA leg (hub holds its own 98% buffer). `end_to_end` = conservative variant |
| `fill_rate_method` | `tiered` | A/B conservative (CSL), C/D exact fill-rate. Also `csl` / `fillrate` for all tiers |
| `buffer_weight` | 0.25 | Priority of safety-stock top-ups vs real demand |
| `contractual_multiplier` | 3 | Extra protection for contractual SKUs (unmet demand and buffers) |
| `holding_cost_per_kl` | 0 | Optional working-capital cost on month-end hub stock |

## Norm flags a planner should review monthly (Exceptions sheet)

- `NO-SALES-6M` - no sales history; norm suppressed, review the listing
- `FORECAST-BIAS` - systematic forecast error dominates randomness; fix the
  forecast rather than buffering it with stock
- `HIGH-DOC` - reorder point exceeds 90 days of average demand; slow mover
  with erratic signal, review manually
