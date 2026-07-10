# -*- coding: utf-8 -*-
"""Fast end-to-end sanity check used by CI.

Loads the case workbook, recomputes the inventory norms, checks the headline
totals against the verified reference values, and solves one small production
line through the MILP path to exercise the optimizer.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from levisol_engine import _solve_group, compute_norms, load_inputs

DATA = os.path.join(REPO, "data", "case_data.xlsx")


def close(a, b, tol):
    assert abs(a - b) <= tol, f"expected {b} +/- {tol}, got {a}"


def main():
    data = load_inputs(DATA)
    assert len(data["sku"]) == 100, "expected 100 SKUs"
    assert len(data["jan"]) == 957, "expected 957 SKU x CFA rows"

    norms, hub_norms, tier, vol6 = compute_norms(data)
    assert len(norms) == 957
    assert len(hub_norms) == 151
    close(norms["safety_stock_kl"].sum(), 1465.1, 0.5)
    close(norms["reorder_point_kl"].sum(), 2442.0, 0.5)
    close(hub_norms["safety_stock_kl"].sum(), 1488.2, 0.5)
    assert tier.value_counts().to_dict() == {"B": 34, "C": 33, "D": 18, "A": 15}

    # solve the smallest line (2 SKUs on the 50 LT line) end to end
    line = data["sku"]["Line"]
    skus_50 = [s for s in data["sku"].index if line[s] == "50LT"]
    assert len(skus_50) == 2
    part = _solve_group(data, norms, hub_norms, skus_50,
                        buffer_weight=0.25, contractual_multiplier=3.0,
                        holding_cost_per_kl=0.0, batch_kl=25.0,
                        mip_rel_gap=1e-6, time_limit=120,
                        msg=lambda *a: None)
    total = sum(part["prod"].values())
    assert total > 0 and total % 25 == 0, "50LT production must be in 25 kL batches"
    assert sum(part["unmet"].values()) < 1e-6, "50LT demand must be fully served"

    print("smoke test passed")
    print(f"  norms: CFA SS {norms['safety_stock_kl'].sum():,.1f} kL, "
          f"hub SS {hub_norms['safety_stock_kl'].sum():,.1f} kL")
    print(f"  50LT line: {total:,.0f} kL produced, 0 kL unmet")


if __name__ == "__main__":
    main()
