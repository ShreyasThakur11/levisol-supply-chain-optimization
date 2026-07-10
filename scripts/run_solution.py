# -*- coding: utf-8 -*-
"""Reproduce the full January 2026 solution from the case data workbook.

Usage:
    python scripts/run_solution.py [--data data/case_data.xlsx] [--out output]

Writes the plan workbook plus norm and plan tables to the output directory
and prints a validation summary.
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

import pandas as pd

from levisol_engine import BATCH_KL, LINES, PLANTS, build_plan, compute_norms, load_inputs
from levisol_output import write_output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO, "data", "case_data.xlsx"))
    ap.add_argument("--out", default=os.path.join(REPO, "output"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print(f"Reading {args.data}")
    data = load_inputs(args.data)
    print(f"  {len(data['sku'])} SKUs, {len(data['jan'])} SKU x CFA demand rows")

    print("Computing inventory norms")
    norms, hub_norms, tier, vol6 = compute_norms(data)
    print(f"  CFA safety stock {norms['safety_stock_kl'].sum():,.1f} kL, "
          f"hub safety stock {hub_norms['safety_stock_kl'].sum():,.1f} kL")

    print("Solving the production and distribution plan")
    plan = build_plan(data, norms, hub_norms)

    c = plan["costs"]
    cash = (c["production"] + c["transport_plant_hub"]
            + c["transport_hub_cfa"] + c["penalty_unmet"])
    print("\nCosts (Rs)")
    print(f"  production            {c['production']:>15,.0f}")
    print(f"  plant to hub freight  {c['transport_plant_hub']:>15,.0f}")
    print(f"  hub to CFA freight    {c['transport_hub_cfa']:>15,.0f}")
    print(f"  penalty (unmet)       {c['penalty_unmet']:>15,.0f}")
    print(f"  TOTAL CASH COST       {cash:>15,.0f}")

    # ---- validation -----------------------------------------------------------
    sku = data["sku"]
    line = sku["Line"]
    cap = data["cap"]
    bad_batch = [k for k, q in plan["prod"].items()
                 if abs(q / BATCH_KL - round(q / BATCH_KL)) > 1e-9]
    viol = 0
    for p in PLANTS:
        for ln in LINES:
            used = sum(q for (s, pp), q in plan["prod"].items()
                       if pp == p and line[s] == ln)
            if used > float(cap.loc[p, ln]) + 1e-6:
                viol += 1
    unmet = sum(plan["unmet"].values())
    print("\nValidation")
    print(f"  non-25 kL batches      {len(bad_batch)}")
    print(f"  capacity violations    {viol}")
    print(f"  unmet demand           {unmet:,.2f} kL")
    print(f"  solver gap             {plan['mip_gap']:.2e}")

    # ---- outputs --------------------------------------------------------------
    out_xlsx = os.path.join(args.out, "plan-output.xlsx")
    write_output(out_xlsx, data, norms, hub_norms, plan)
    norms.to_csv(os.path.join(args.out, "norms_cfa.csv"), index=False)
    hub_norms.to_csv(os.path.join(args.out, "norms_hub.csv"), index=False)
    pd.DataFrame([(s, p, q) for (s, p), q in plan["prod"].items()],
                 columns=["SKU", "Plant", "kl"]) \
        .to_csv(os.path.join(args.out, "plan_production.csv"), index=False)
    with open(os.path.join(args.out, "plan_costs.json"), "w") as fp:
        json.dump({k: float(v) for k, v in c.items()}, fp, indent=1)
    print(f"\nWrote {out_xlsx}")


if __name__ == "__main__":
    main()
