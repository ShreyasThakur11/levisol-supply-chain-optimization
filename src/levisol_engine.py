# -*- coding: utf-8 -*-
"""
LEVISOL SUPPLY CHAIN PLANNING ENGINE  (Project 2)
=================================================
Reusable calculation core:
  1. load_inputs()    - reads the case data workbook (Exhibits A-J layout)
  2. compute_norms()  - inventory norms per SKU x CFA and per SKU x Hub
  3. build_plan()     - cost-minimising production & distribution MILP (HiGHS)
  4. write_output()   - writes a formatted output workbook

All quantities are kL. All costs are INR (Rs.).
Month = 30 working days (per case document).
"""

import re
import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import csr_matrix

# -----------------------------------------------------------------------------
# Canonical entity names
# -----------------------------------------------------------------------------
PLANTS = ["BOM", "AHM", "KOL"]
PLANT_CITY = {"Mumbai": "BOM", "Ahmedabad": "AHM", "Kolkata": "KOL"}
HUBS = ["MHW", "MHE"]
LINES = ["<=1.5LT", "3-5LT", "7-20LT", "50LT", "180-210LT"]
SOURCE_TO_HUB = {"East": "MHE", "Rest of India": "MHW"}
BATCH_KL = 25.0

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _find_header_row(df, must_contain="Product Name"):
    """Locate the header row of an exhibit sheet by scanning for a key label."""
    for i in range(min(8, len(df))):
        if any(str(v).strip() == must_contain for v in df.iloc[i].values):
            return i
    raise ValueError(f"Header row containing '{must_contain}' not found")


def _read_exhibit(xls, sheet, key="Product Name"):
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)
    hr = _find_header_row(raw, key)
    df = raw.iloc[hr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hr].values]
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.loc[:, [c for c in df.columns if c != "nan"]]
    return df


def pack_to_line(pack: str) -> str:
    """Map a pack size string like '20 X 900 ML' to its production line."""
    m = re.match(r"\s*(\d+)\s*X\s*([\d.]+)\s*(ML|LT|KG)\s*$", str(pack).strip(), re.I)
    if not m:
        raise ValueError(f"Unrecognised pack size: {pack!r}")
    v, unit = float(m.group(2)), m.group(3).upper()
    if unit == "ML":
        v /= 1000.0
    if v <= 1.5:
        return "<=1.5LT"
    if 3.0 <= v <= 5.0:
        return "3-5LT"
    if 7.0 <= v <= 20.0:
        return "7-20LT"
    if v == 50.0:
        return "50LT"
    if v >= 180.0:
        return "180-210LT"
    raise ValueError(f"Pack unit volume {v} LT does not map to any line ({pack!r})")


# -----------------------------------------------------------------------------
# 1. INPUT LOADING
# -----------------------------------------------------------------------------
def load_inputs(path):
    """Read all exhibits from the data workbook. Returns dict of DataFrames."""
    xls = pd.ExcelFile(path)

    def sheet_like(*words):
        for s in xls.sheet_names:
            if all(w.lower() in s.lower() for w in words):
                return s
        raise ValueError(f"No sheet matching {words}; sheets = {xls.sheet_names}")

    # --- Exhibit A: plants ---------------------------------------------------
    a = _read_exhibit(xls, sheet_like("Plants"), key="Plant Code")
    a = a[a["Plant Code"].isin(PLANTS)].set_index("Plant Code")
    capcols = {}
    for c in a.columns:
        cc = str(c).replace("\n", " ")
        if "<=1.5" in cc:
            capcols[c] = "<=1.5LT"
        elif "3- 5" in cc or "3-5" in cc:
            capcols[c] = "3-5LT"
        elif "7- 20" in cc or "7-20" in cc:
            capcols[c] = "7-20LT"
        elif "180" in cc:
            capcols[c] = "180-210LT"
        elif "50" in cc:
            capcols[c] = "50LT"
    cap = a[list(capcols)].rename(columns=capcols).astype(float)      # plant x line
    prod_cost = a["Production Cost (₹/kl)" if "Production Cost (₹/kl)" in a.columns
                  else [c for c in a.columns if "Cost" in str(c)][0]].astype(float)

    # --- Exhibit B: plant->hub transport --------------------------------------
    b = _read_exhibit(xls, sheet_like("Plant-Hub"), key="From Plant")
    b = b[b["From Plant"].isin(PLANT_CITY)].copy()
    b["Plant"] = b["From Plant"].map(PLANT_CITY)
    tc_ph = {}
    for _, r in b.iterrows():
        for c in b.columns:
            if "MHW" in str(c):
                tc_ph[(r["Plant"], "MHW")] = float(r[c])
            elif "MHE" in str(c):
                tc_ph[(r["Plant"], "MHE")] = float(r[c])

    # --- Exhibit C: hub->CFA transport ----------------------------------------
    c_ = _read_exhibit(xls, sheet_like("Hub-CFA"), key="CFA")
    c_ = c_[~c_["CFA"].isna() & (c_["CFA"] != "CFA")]
    c_ = c_[c_[[col for col in c_.columns if "MHW" in str(col)][0]].notna()]
    tc_hc = {}
    cfa_region = {}
    for _, r in c_.iterrows():
        cfa = str(r["CFA"]).strip()
        if not cfa or cfa.lower() == "nan":
            continue
        cfa_full = cfa if cfa.endswith("CFA") else cfa + " CFA"
        cfa_region[cfa_full] = str(r.get("Region", "")).strip()
        for col in c_.columns:
            if "MHW" in str(col):
                tc_hc[("MHW", cfa_full)] = float(r[col])
            elif "MHE" in str(col):
                tc_hc[("MHE", cfa_full)] = float(r[col])

    # --- Exhibit D: SKU portfolio ---------------------------------------------
    d = _read_exhibit(xls, sheet_like("Portfolio"))
    d = d[d["Product Name"].astype(str).str.startswith("SKU")].copy()
    d["Penalty cost (per kL)"] = d["Penalty cost (per kL)"].astype(float)
    d["Contractual"] = d["Contractual?"].astype(str).str.upper().str.contains("YES")
    d["Line"] = d["Pack size"].map(pack_to_line)
    d = d.set_index("Product Name")

    # --- Exhibit E: source + lead times ----------------------------------------
    e = _read_exhibit(xls, sheet_like("Source"))
    e = e[e["Product Name"].astype(str).str.startswith("SKU")].copy()
    ltcols = {}
    for c in e.columns:
        cc = re.sub(r"\s+", " ", str(c))
        if "Plant to Hub" in cc:
            ltcols[c] = "lt_plant_hub"
        elif "Hub to CFA" in cc:
            ltcols[c] = "lt_hub_cfa"
        elif "Production lead" in cc:
            ltcols[c] = "lt_prod"
        elif "Production variability" in cc:
            ltcols[c] = "sd_prod"
        elif "Transit lead variability" in cc:
            ltcols[c] = "sd_transit"
    e = e.rename(columns=ltcols)
    for c in ["lt_plant_hub", "lt_hub_cfa", "lt_prod", "sd_prod", "sd_transit"]:
        e[c] = e[c].astype(float)
    e["Hub"] = e["Source"].map(SOURCE_TO_HUB)

    # --- Exhibits G/H: sales & forecast history ---------------------------------
    g = _read_exhibit(xls, sheet_like("Sales History"))
    h = _read_exhibit(xls, sheet_like("Forecast History"))
    mcols = [c for c in g.columns if "kL" in str(c)]
    for c in mcols:
        g[c] = g[c].astype(float)
        h[c] = h[c].astype(float)

    # --- Exhibit I: opening inventory (CFA rows + hub rows) ----------------------
    i_ = _read_exhibit(xls, sheet_like("opening Inventory"))
    icol = [c for c in i_.columns if "kL" in str(c)][0]
    i_[icol] = i_[icol].astype(float)
    i_ = i_.rename(columns={icol: "open_kl"})
    hub_mask = i_["CFA"].astype(str).str.contains("Mother", case=False)
    inv_cfa = i_[~hub_mask].copy()
    inv_hub = i_[hub_mask].copy()
    inv_hub["Hub"] = np.where(inv_hub["CFA"].str.contains("West"), "MHW", "MHE")

    # --- Exhibit J: January forecast ----------------------------------------------
    j = _read_exhibit(xls, sheet_like("Jan Forecast"))
    jcol = [c for c in j.columns if "kL" in str(c)][0]
    j[jcol] = j[jcol].astype(float)
    j = j.rename(columns={jcol: "jan_fcst"})

    # --- Exhibit F: service levels ---------------------------------------------
    f = _read_exhibit(xls, sheet_like("Service"), key="Tier")
    f = f[f["Tier"].astype(str).str.strip().isin(list("ABCD"))].copy()
    fr_col = [c for c in f.columns if "Fill Rate" in str(c)][0]
    def _pct(v):
        s = str(v).replace("%", "").strip()
        x = float(s)
        return x / 100.0 if x > 1 else x
    fill_rate = {str(r["Tier"]).strip(): _pct(r[fr_col]) for _, r in f.iterrows()}
    slab_col = [c for c in f.columns if "Volume" in str(c)][0]
    vol_slab = {str(r["Tier"]).strip(): _pct(r[slab_col]) for _, r in f.iterrows()}

    return dict(cap=cap, prod_cost=prod_cost, tc_ph=tc_ph, tc_hc=tc_hc,
                cfa_region=cfa_region, sku=d, lt=e, sales=g, fcst=h,
                inv_cfa=inv_cfa, inv_hub=inv_hub, jan=j, month_cols=mcols,
                fill_rate=fill_rate, vol_slab=vol_slab)


# -----------------------------------------------------------------------------
# 2. INVENTORY NORMS
# -----------------------------------------------------------------------------
def classify_tiers(data):
    """ABC-D classification on total 6-month sales volume using Exhibit F slabs
    (A = SKUs supplying top 50% of volume, B next 30%, C next 15%, D last 5%)."""
    g, m = data["sales"], data["month_cols"]
    vol = g.groupby("Product Name")[m].sum().sum(axis=1).sort_values(ascending=False)
    cum = vol.cumsum() / vol.sum()
    slabs = data["vol_slab"]
    b1 = slabs.get("A", .5)
    b2 = b1 + slabs.get("B", .3)
    b3 = b2 + slabs.get("C", .15)
    tier = pd.Series(np.where(cum <= b1, "A",
                     np.where(cum <= b2, "B",
                     np.where(cum <= b3, "C", "D"))), index=vol.index, name="Tier")
    return tier, vol.rename("vol_6m")


def _loss(z):
    """Standard normal loss function G(z) = phi(z) - z*(1 - Phi(z))."""
    return norm.pdf(z) - z * (1.0 - norm.cdf(z))


def _z_for_fill_rate(beta, q, sigma_ltd, z_cap):
    """z achieving expected fill rate beta with cycle quantity q and LTD sigma
    sigma_ltd:  G(z) = (1-beta)*q/sigma_ltd. Floored at 0, capped at z_cap."""
    if sigma_ltd <= 1e-12 or q <= 1e-12:
        return 0.0
    target = (1.0 - beta) * q / sigma_ltd
    if target >= _loss(0.0):               # cycle stock alone achieves the target
        return 0.0
    lo, hi = 0.0, z_cap
    if _loss(hi) > target:                 # even z_cap can't reach it -> cap
        return z_cap
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _loss(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def compute_norms(data, working_days=30, hub_service_level=0.98,
                  cfa_lt_mode="decoupled", fill_rate_method="tiered",
                  z_cap=None, doc_review_days=90):
    """
    CFA norms per SKU x CFA (957 rows) and hub norms per SKU x Hub.

    Demand & uncertainty (per SKU x CFA):
      mu_d = mean(last-6-month sales) / working_days
      s_d  = std(forecast errors, bias removed, ddof=1) / sqrt(working_days)
             Safety stock buffers the RANDOM part of forecast error; systematic
             bias is a demand-planning correction and is flagged, not stocked.

    Lead time (multi-echelon, each step buffered exactly once):
      cfa_lt_mode='decoupled' (default): the hub holds its own 98% safety
        stock, so it is the decoupling point. CFA replenishment lead time is
        the hub->CFA transit only; its variability is the transit variability.
        The production + plant->hub legs are buffered AT THE HUB.
      cfa_lt_mode='end_to_end': conservative variant - CFA lead time spans
        production + plant->hub + hub->CFA (reported as alt columns anyway).

    Service level -> z (fill_rate_method='tiered', default):
      Tier A/B (critical, 98/97% targets): z = PPF(target) - cycle-service-
        level reading, deliberately conservative to protect service.
      Tier C/D (92% targets): exact fill-rate translation via the normal loss
        function G(z) = (1-beta)*Q/sigma_LTD with Q = one month's demand,
        z floored at 0 and capped at PPF(0.98) - releases cash where service
        matters least.  'csl' / 'fillrate' apply one method to all tiers.

      SS  = z * sqrt(LT*s_d^2 + mu_d^2*s_LT^2);  ROP = mu_d*LT + SS
      DOC = ROP / mu_d  (days of average last-6-months demand; 0 if mu_d=0)

    Hubs (98% service level, case rule): demand = sum of served CFA rows
    (independent, case instruction), LT = production + plant->hub transit,
    LT variability = production variability.
    Zero-sales rows -> zero norms + flag. High forecast bias and DOC above
    doc_review_days are flagged for planner review.
    """
    g, h, e, m = data["sales"], data["fcst"], data["lt"], data["month_cols"]
    tier, vol6 = classify_tiers(data)
    fr = data["fill_rate"]
    zt_csl = {t: float(norm.ppf(fr[t])) for t in fr}
    z_hub = float(norm.ppf(hub_service_level))
    if z_cap is None:
        z_cap = float(norm.ppf(0.98))

    df = e.merge(g[["Product Name", "CFA"] + m], on=["Product Name", "CFA"], how="left")
    hcols = [c + "_f" for c in m]
    hh = h[["Product Name", "CFA"] + m].copy()
    hh.columns = ["Product Name", "CFA"] + hcols
    df = df.merge(hh, on=["Product Name", "CFA"], how="left")

    A = df[m].to_numpy(float)              # actuals
    F = df[hcols].to_numpy(float)          # forecasts
    mu_m = A.mean(axis=1)
    err = A - F
    bias_m = err.mean(axis=1)                          # systematic error (kL/month)
    sd_err_m = err.std(axis=1, ddof=1)                 # random error (kL/month)
    rmse_m = np.sqrt((err ** 2).mean(axis=1))          # reported for transparency
    sd_sales_m = A.std(axis=1, ddof=1)

    mu_d = mu_m / working_days
    s_d = sd_err_m / np.sqrt(working_days)

    lt_e2e = (df["lt_prod"] + df["lt_plant_hub"] + df["lt_hub_cfa"]).to_numpy(float)
    slt_e2e = np.sqrt(df["sd_prod"] ** 2 + df["sd_transit"] ** 2).to_numpy(float)
    if cfa_lt_mode == "decoupled":
        LT = df["lt_hub_cfa"].to_numpy(float)
        s_LT = df["sd_transit"].to_numpy(float)
    else:
        LT, s_LT = lt_e2e, slt_e2e

    df["Tier"] = df["Product Name"].map(tier)
    tiers_arr = df["Tier"].to_numpy()
    beta = df["Tier"].map(fr).to_numpy(float)

    dead = A.sum(axis=1) <= 0              # no sales in 6 months -> suppress norms
    var_ltd = LT * s_d ** 2 + (mu_d ** 2) * (s_LT ** 2)
    sig_ltd = np.sqrt(np.maximum(var_ltd, 0.0))

    z = np.empty(len(df))
    method = np.empty(len(df), dtype=object)
    for i in range(len(df)):
        t = tiers_arr[i]
        if fill_rate_method == "csl" or (fill_rate_method == "tiered" and t in ("A", "B")):
            z[i] = zt_csl[t]
            method[i] = "CSL"
        else:                               # exact fill-rate (tiered C/D, or all)
            z[i] = _z_for_fill_rate(beta[i], mu_m[i], sig_ltd[i], z_cap)
            method[i] = "FILL-RATE"

    ss = z * sig_ltd
    ss[dead] = 0.0
    rop = mu_d * LT + ss
    rop[dead] = 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        doc = np.where(mu_d > 0, rop / mu_d, 0.0)

    # conservative end-to-end alternative (same z), for comparison/reporting
    sig_e2e = np.sqrt(np.maximum(lt_e2e * s_d ** 2 + (mu_d ** 2) * (slt_e2e ** 2), 0.0))
    ss_e2e = z * sig_e2e
    ss_e2e[dead] = 0.0

    flags = []
    for i in range(len(df)):
        f_ = []
        if dead[i]:
            f_.append("NO-SALES-6M: norm suppressed")
        else:
            if abs(bias_m[i]) > max(sd_err_m[i], 1e-9):
                f_.append("FORECAST-BIAS: |bias| exceeds error sigma - review demand plan")
            if doc[i] > doc_review_days:
                f_.append(f"HIGH-DOC: cover > {doc_review_days}d - review")
        flags.append("; ".join(f_))

    norms = df[["Product Name", "Pack size", "CFA region", "CFA", "Source", "Hub",
                "Tier"]].copy()
    norms["fill_rate_target"] = beta
    norms["z"] = z
    norms["z_method"] = method
    norms["mu_month_kl"] = mu_m
    norms["mu_day_kl"] = mu_d
    norms["fcst_bias_month_kl"] = bias_m
    norms["sd_fcst_err_month_kl"] = sd_err_m
    norms["rmse_fcst_month_kl"] = rmse_m
    norms["sd_sales_month_kl"] = sd_sales_m
    norms["sigma_day_kl"] = s_d
    norms["LT_days"] = LT
    norms["sigma_LT_days"] = s_LT
    norms["safety_stock_kl"] = ss
    norms["reorder_point_kl"] = rop
    norms["days_of_cover"] = doc
    norms["ss_end_to_end_alt_kl"] = ss_e2e
    norms["flag"] = flags

    # ---------------- hub norms ------------------------------------------------
    # Hub demand = sum of daily demand of the CFA rows it historically serves
    # (Source column); CFA demands independent => variances add.
    # Hub replenishment LT = production LT + plant->hub transit (1 day);
    # its variability = production variability (transit variability in the data
    # is a property of the hub->CFA leg -- see methodology).
    nb = norms.assign(var_d=norms["sigma_day_kl"] ** 2,
                      w=norms["mu_month_kl"].clip(lower=1e-9))
    nb["lt_prod"] = df["lt_prod"]; nb["sd_prod"] = df["sd_prod"]
    nb["lt_plant_hub"] = df["lt_plant_hub"]

    rows = []
    for (skuu, hub), grp in nb.groupby(["Product Name", "Hub"]):
        mu_hub = grp["mu_day_kl"].sum()
        var_hub = grp["var_d"].sum()
        w = grp["w"] / grp["w"].sum()
        lt_hub = float((grp["lt_prod"] + grp["lt_plant_hub"]) @ w)
        sd_lt_hub = float(grp["sd_prod"] @ w)
        ss_h = z_hub * np.sqrt(max(lt_hub * var_hub + mu_hub ** 2 * sd_lt_hub ** 2, 0))
        if grp["mu_month_kl"].sum() <= 0:
            ss_h = 0.0
        rop_h = mu_hub * lt_hub + ss_h
        rows.append(dict(**{"Product Name": skuu, "Hub": hub},
                         mu_day_kl=mu_hub, sigma_day_kl=np.sqrt(var_hub),
                         LT_days=lt_hub, sigma_LT_days=sd_lt_hub,
                         service_level=hub_service_level, z=z_hub,
                         safety_stock_kl=ss_h, reorder_point_kl=rop_h,
                         days_of_cover=(rop_h / mu_hub if mu_hub > 0 else 0.0),
                         n_cfas=len(grp)))
    hub_norms = pd.DataFrame(rows)
    return norms, hub_norms, tier, vol6


# -----------------------------------------------------------------------------
# 3. PRODUCTION & DISTRIBUTION OPTIMISATION (MILP, HiGHS via scipy)
# -----------------------------------------------------------------------------
def build_plan(data, norms, hub_norms,
               buffer_weight=0.25, contractual_multiplier=3.0,
               holding_cost_per_kl=0.0,
               batch_kl=BATCH_KL, mip_rel_gap=1e-6, time_limit=300, msg=print):
    """
    Single-period (January-2026) cost-minimising plan.

    Decision variables
      n[s,p]    integer  batches of 25 kL of SKU s at plant p (line-eligible only)
      y[s,p,h]  >=0      kL shipped plant p -> hub h
      z[s,c,h]  >=0      kL shipped hub h -> CFA c
      u[s,c]    >=0      unmet January demand (lost sales) at CFA
      v[s,c]    >=0      CFA safety-stock top-up shortfall
      w[s,h]    >=0      hub safety-stock shortfall

    Objective  min  production + transport
                    + penalty * contractual_multiplier(if contractual) * u
                    + buffer_weight * penalty * (v + w)
    (Only penalty*u is a real cash cost; the buffer terms are prioritisation
    weights so that buffers are filled whenever capacity allows, and shorted
    before real demand when it does not.)

    EXACT DECOMPOSITION: SKUs interact only through plant line capacities,
    and every SKU belongs to exactly one line, so the model separates into
    one independent MILP per production line. Each is solved to (near-)
    optimality and the union is the global optimum.
    """
    sku = data["sku"]
    line = sku["Line"]
    merged = None
    for ln in LINES:
        skus_l = [s for s in sku.index if line[s] == ln]
        if not skus_l:
            continue
        msg(f"--- line {ln}: {len(skus_l)} SKUs")
        part = _solve_group(data, norms, hub_norms, skus_l,
                            buffer_weight, contractual_multiplier,
                            holding_cost_per_kl,
                            batch_kl, mip_rel_gap, time_limit, msg)
        if merged is None:
            merged = part
        else:
            for k in ("prod", "flow_ph", "flow_hc", "unmet",
                      "buf_cfa", "buf_hub", "closing", "D1", "R"):
                merged[k].update(part[k])
            for k in merged["costs"]:
                merged["costs"][k] += part["costs"][k]
            merged["mip_gap"] = max(merged["mip_gap"], part["mip_gap"])
    return merged


def _solve_group(data, norms, hub_norms, skus,
                 buffer_weight, contractual_multiplier, holding_cost_per_kl,
                 batch_kl, mip_rel_gap, time_limit, msg):
    sku = data["sku"]; cap = data["cap"]; pc = data["prod_cost"]
    tc_ph, tc_hc = data["tc_ph"], data["tc_hc"]
    jan = data["jan"].set_index(["Product Name", "CFA"])["jan_fcst"]
    open_cfa = data["inv_cfa"].set_index(["Product Name", "CFA"])["open_kl"]
    open_hub = data["inv_hub"].set_index(["Product Name", "Hub"])["open_kl"]

    ss_cfa = norms.set_index(["Product Name", "CFA"])["safety_stock_kl"]
    ss_hub = hub_norms.set_index(["Product Name", "Hub"])["safety_stock_kl"]

    sset = set(skus)
    pairs = [(s, c) for (s, c) in jan.index if s in sset]

    pen = sku["Penalty cost (per kL)"]
    contr = sku["Contractual"]
    line = sku["Line"]

    elig = [(s, p) for s in skus for p in PLANTS if cap.loc[p, line[s]] > 0]
    elig_set = set(elig)

    # tight upper bounds on batch counts: total possible requirement per SKU
    req_tot = {}
    for (s, c) in pairs:
        op = float(open_cfa.get((s, c), 0.0))
        f = float(jan.loc[(s, c)])
        ssn = float(ss_cfa.get((s, c), 0.0))
        req_tot[s] = req_tot.get(s, 0.0) + max(0.0, f + ssn - op)
    for s in skus:
        for hb in HUBS:
            req_tot[s] = req_tot.get(s, 0.0) + max(
                0.0, float(ss_hub.get((s, hb), 0.0)) - float(open_hub.get((s, hb), 0.0)))
    n_ub = {}
    for (s, p) in elig:
        cap_b = int(np.floor(float(cap.loc[p, line[s]]) / batch_kl))
        need_b = int(np.ceil(req_tot.get(s, 0.0) / batch_kl))
        n_ub[(s, p)] = max(0, min(cap_b, need_b))

    # ---- variable index maps -------------------------------------------------
    idx = {}
    nv = 0
    for sp in elig:
        idx[("n",) + sp] = nv; nv += 1
    n_int_end = nv
    for (s, p) in elig:
        for hb in HUBS:
            idx[("y", s, p, hb)] = nv; nv += 1
    for (s, c) in pairs:
        for hb in HUBS:
            idx[("z", s, c, hb)] = nv; nv += 1
    for (s, c) in pairs:
        idx[("u", s, c)] = nv; nv += 1
        idx[("v", s, c)] = nv; nv += 1
    hub_keys = [(s, hb) for s in skus for hb in HUBS
                if ss_hub.get((s, hb), 0.0) > 0 or open_hub.get((s, hb), 0.0) > 0]
    for (s, hb) in hub_keys:
        idx[("w", s, hb)] = nv; nv += 1

    # ---- objective -------------------------------------------------------------
    # holding_cost_per_kl (default 0: case prices no holding cost) applies to
    # month-end HUB stock = open + sum(y) - sum(z):  +h on y, -h on z
    # (constant h*open dropped from the LP, irrelevant to the argmin).
    h_ = float(holding_cost_per_kl)
    obj = np.zeros(nv)
    for (s, p) in elig:
        obj[idx[("n", s, p)]] = batch_kl * pc[p]
        for hb in HUBS:
            obj[idx[("y", s, p, hb)]] = tc_ph[(p, hb)] + h_
    for (s, c) in pairs:
        mult = contractual_multiplier if contr[s] else 1.0
        for hb in HUBS:
            obj[idx[("z", s, c, hb)]] = tc_hc[(hb, c)] - h_
        obj[idx[("u", s, c)]] = pen[s] * mult
        obj[idx[("v", s, c)]] = pen[s] * buffer_weight * mult
    for (s, hb) in hub_keys:
        mult = contractual_multiplier if contr[s] else 1.0
        obj[idx[("w", s, hb)]] = pen[s] * buffer_weight * mult

    rowsA, colsA, valsA, lo, hi = [], [], [], [], []
    def add_row(entries, lb, ub):
        r = len(lo)
        for cix, val in entries:
            rowsA.append(r); colsA.append(cix); valsA.append(val)
        lo.append(lb); hi.append(ub)

    # (1) plant balance: sum_h y - 25*n = 0
    for (s, p) in elig:
        ent = [(idx[("y", s, p, hb)], 1.0) for hb in HUBS]
        ent.append((idx[("n", s, p)], -batch_kl))
        add_row(ent, 0.0, 0.0)

    # (2) line capacity: sum_{s in line} 25*n[s,p] <= cap[p,line]
    for p in PLANTS:
        for ln in LINES:
            members = [s for s in skus if line[s] == ln and (s, p) in elig_set]
            if not members or cap.loc[p, ln] <= 0:
                continue
            ent = [(idx[("n", s, p)], batch_kl) for s in members]
            add_row(ent, -np.inf, float(cap.loc[p, ln]))

    # (3) hub availability (closing stock >= 0): sum_c z <= open_hub + sum_p y
    pairs_by_sku = {}
    for (s, c) in pairs:
        pairs_by_sku.setdefault(s, []).append(c)
    for s in skus:
        for hb in HUBS:
            zc = [(idx[("z", s, c, hb)], 1.0) for c in pairs_by_sku.get(s, [])]
            yc = [(idx[("y", s, p, hb)], -1.0) for p in PLANTS if (s, p) in elig_set]
            if not zc and not yc:
                continue
            add_row(zc + yc, -np.inf, float(open_hub.get((s, hb), 0.0)))

    # (4) u >= D1 - r   (D1 = sales at risk)  ->  r + u >= D1
    # (5) v >= R - r - u                       ->  r + u + v >= R
    D1s, Rs = {}, {}
    for (s, c) in pairs:
        op = float(open_cfa.get((s, c), 0.0))
        f = float(jan.loc[(s, c)])
        ssn = float(ss_cfa.get((s, c), 0.0))
        D1 = max(0.0, f - op)
        R = max(0.0, f + ssn - op)
        D1s[(s, c)], Rs[(s, c)] = D1, R
        rvars = [(idx[("z", s, c, hb)], 1.0) for hb in HUBS]
        add_row(rvars + [(idx[("u", s, c)], 1.0)], D1, np.inf)
        add_row(rvars + [(idx[("u", s, c)], 1.0), (idx[("v", s, c)], 1.0)], R, np.inf)

    # (6) hub safety-stock shortfall: closing + w >= SS_hub
    #     i.e.  sum_p y - sum_c z + w >= SS_hub - open_hub
    hub_key_set = set(hub_keys)
    for (s, hb) in hub_keys:
        tgt = float(ss_hub.get((s, hb), 0.0))
        if tgt <= 0:
            continue
        oh = float(open_hub.get((s, hb), 0.0))
        zc = [(idx[("z", s, c, hb)], -1.0) for c in pairs_by_sku.get(s, [])]
        yc = [(idx[("y", s, p, hb)], 1.0) for p in PLANTS if (s, p) in elig_set]
        add_row(yc + zc + [(idx[("w", s, hb)], 1.0)], tgt - oh, np.inf)

    A = csr_matrix((valsA, (rowsA, colsA)), shape=(len(lo), nv))
    con = LinearConstraint(A, lo, hi)
    integrality = np.zeros(nv)
    integrality[:n_int_end] = 1
    ub = np.full(nv, np.inf)
    for (s, p) in elig:                    # tight bounds help the B&B enormously
        ub[idx[("n", s, p)]] = n_ub[(s, p)]
    bounds = Bounds(np.zeros(nv), ub)

    msg(f"  MILP: {nv} vars ({n_int_end} int), {len(lo)} cons ... solving")
    res = milp(c=obj, constraints=con, integrality=integrality, bounds=bounds,
               options={"mip_rel_gap": mip_rel_gap, "time_limit": time_limit})
    # scipy milp status: 0 optimal, 1 iteration/time limit, 2 infeasible, 3 unbounded
    if res.x is None or res.status in (2, 3):
        raise RuntimeError(f"Solver failed: {res.message}")
    gap = float(getattr(res, "mip_gap", 0.0) or 0.0)
    if res.status != 0:
        msg(f"  NOTE: stopped at limit; best feasible kept (rel gap = {gap:.2e})")
    else:
        msg(f"  optimal (rel gap = {gap:.2e})")
    x = res.x

    # ---- extract solution -------------------------------------------------------
    get = lambda k: float(x[idx[k]]) if k in idx else 0.0
    prod = {(s, p): batch_kl * round(get(("n", s, p))) for (s, p) in elig
            if round(get(("n", s, p))) > 0}
    flow_ph = {(s, p, hb): get(("y", s, p, hb)) for (s, p) in elig for hb in HUBS
               if get(("y", s, p, hb)) > 1e-6}
    flow_hc = {(s, c, hb): get(("z", s, c, hb)) for (s, c) in pairs for hb in HUBS
               if get(("z", s, c, hb)) > 1e-6}
    unmet = {(s, c): get(("u", s, c)) for (s, c) in pairs if get(("u", s, c)) > 1e-6}
    buf_cfa = {(s, c): get(("v", s, c)) for (s, c) in pairs if get(("v", s, c)) > 1e-6}
    buf_hub = {(s, hb): get(("w", s, hb)) for (s, hb) in hub_keys
               if get(("w", s, hb)) > 1e-6}

    cost_prod = sum(q * pc[p] for (s, p), q in prod.items())
    cost_ph = sum(q * tc_ph[(p, hb)] for (s, p, hb), q in flow_ph.items())
    cost_hc = sum(q * tc_hc[(hb, c)] for (s, c, hb), q in flow_hc.items())
    cost_pen = sum(q * pen[s] for (s, c), q in unmet.items())          # cash penalty
    risk_cfa = sum(q * pen[s] * buffer_weight for (s, c), q in buf_cfa.items())
    risk_hub = sum(q * pen[s] * buffer_weight for (s, hb), q in buf_hub.items())

    # hub closing stock
    closing = {}
    for s in skus:
        for hb in HUBS:
            oh = float(open_hub.get((s, hb), 0.0))
            inn = sum(q for (s2, p, h2), q in flow_ph.items() if s2 == s and h2 == hb)
            out = sum(q for (s2, c, h2), q in flow_hc.items() if s2 == s and h2 == hb)
            cl = oh + inn - out
            if abs(cl) > 1e-6 or ss_hub.get((s, hb), 0) > 0:
                closing[(s, hb)] = cl

    return dict(prod=prod, flow_ph=flow_ph, flow_hc=flow_hc,
                unmet=unmet, buf_cfa=buf_cfa, buf_hub=buf_hub, closing=closing,
                D1=D1s, R=Rs, mip_gap=gap,
                costs=dict(production=cost_prod, transport_plant_hub=cost_ph,
                           transport_hub_cfa=cost_hc, penalty_unmet=cost_pen,
                           buffer_risk_cfa=risk_cfa, buffer_risk_hub=risk_hub,
                           buffer_risk_weighted=risk_cfa + risk_hub,
                           objective=float(res.fun)))
