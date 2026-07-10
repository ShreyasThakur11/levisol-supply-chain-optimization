# -*- coding: utf-8 -*-
"""
LEVISOL MONTHLY PLANNING TOOL
=============================
A supply-chain planner's tool: pick the month's data workbook, press RUN,
get a complete production + distribution + inventory-norms plan workbook.

HOW TO USE (no technical knowledge needed)
------------------------------------------
1. Update the input workbook in Excel (same layout as the case data file):
     - Sheet J : the month's demand forecast per SKU per CFA
     - Sheet I : opening inventory (CFA rows + 'Mother Hub West/East' rows)
     - Sheet A : plant line capacities / production costs
     - Sheets B, C : transport rates
     - Sheets D, E, F, G, H : SKU master, lead times, service levels, history
2. Double-click this file (or run:  python src/planning_tool.py)
3. Pick the workbook, press RUN PLAN.
4. The tool writes  Levisol_Plan_Output.xlsx  next to the input file and
   shows a summary. All shortfalls appear on the 'Exceptions' sheet -
   the tool never crashes on capacity shortage or zero demand.

Command-line use:   python src/planning_tool.py <input.xlsx> [output.xlsx]
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEFAULT_DATA = os.path.normpath(os.path.join(HERE, "..", "data", "case_data.xlsx"))

from levisol_engine import load_inputs, compute_norms, build_plan
from levisol_output import write_output


def run(input_path, output_path=None, working_days=30, hub_sl=0.98,
        buffer_weight=0.25, contractual_multiplier=3.0, log=print):
    """Full monthly run. Returns (output_path, summary_text)."""
    log(f"Reading  {os.path.basename(input_path)} ...")
    data = load_inputs(input_path)
    log(f"  {len(data['sku'])} SKUs, {len(data['jan'])} SKU x CFA demand rows")

    log("Computing inventory norms ...")
    norms, hub_norms, tier, vol6 = compute_norms(
        data, working_days=working_days, hub_service_level=hub_sl)

    log("Optimising production & distribution plan (this can take a few minutes) ...")
    plan = build_plan(data, norms, hub_norms, buffer_weight=buffer_weight,
                      contractual_multiplier=contractual_multiplier, msg=log)

    if output_path is None:
        output_path = os.path.join(os.path.dirname(input_path),
                                   "Levisol_Plan_Output.xlsx")
    write_output(output_path, data, norms, hub_norms, plan,
                 params=dict(working_days=working_days, hub_sl=hub_sl,
                             buffer_weight=buffer_weight,
                             contractual_multiplier=contractual_multiplier))

    c = plan["costs"]
    cash = (c["production"] + c["transport_plant_hub"] +
            c["transport_hub_cfa"] + c["penalty_unmet"])
    n_unmet = len(plan["unmet"]); kl_unmet = sum(plan["unmet"].values())
    kl_bufc = sum(plan["buf_cfa"].values()); kl_bufh = sum(plan["buf_hub"].values())
    summary = (
        f"PLAN COMPLETE  →  {output_path}\n\n"
        f"Total production        : {sum(plan['prod'].values()):>12,.0f} kL\n"
        f"Production cost         : ₹ {c['production']:>15,.0f}\n"
        f"Transport plant→hub     : ₹ {c['transport_plant_hub']:>15,.0f}\n"
        f"Transport hub→CFA       : ₹ {c['transport_hub_cfa']:>15,.0f}\n"
        f"Penalty (unmet demand)  : ₹ {c['penalty_unmet']:>15,.0f}\n"
        f"TOTAL CASH COST         : ₹ {cash:>15,.0f}\n\n"
        f"Unmet demand lines      : {n_unmet}  ({kl_unmet:,.1f} kL)\n"
        f"CFA safety-stock top-ups short : {kl_bufc:,.1f} kL\n"
        f"Hub safety-stock short  : {kl_bufh:,.1f} kL\n"
        + ("\n⚠ See the 'Exceptions' sheet for what was shorted, where and why."
           if (n_unmet or kl_bufc > 0.05 or kl_bufh > 0.05) else
           "\n✓ All demand and all safety-stock targets fully met.")
    )
    log(summary)
    return output_path, summary


# ----------------------------- GUI ------------------------------------------
def gui():
    import tkinter as tk
    from tkinter import filedialog, scrolledtext, messagebox

    root = tk.Tk()
    root.title("Levisol Monthly Planning Tool")
    root.geometry("860x560")

    frm = tk.Frame(root, padx=10, pady=8); frm.pack(fill="x")
    tk.Label(frm, text="Input data workbook:").grid(row=0, column=0, sticky="w")
    path_var = tk.StringVar(value=DEFAULT_DATA)
    tk.Entry(frm, textvariable=path_var, width=80).grid(row=0, column=1, padx=6)
    tk.Button(frm, text="Browse…",
              command=lambda: path_var.set(
                  filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
                  or path_var.get())).grid(row=0, column=2)

    opt = tk.Frame(root, padx=10); opt.pack(fill="x")
    tk.Label(opt, text="Working days/month").grid(row=0, column=0, sticky="w")
    wd = tk.StringVar(value="30"); tk.Entry(opt, textvariable=wd, width=6).grid(row=0, column=1)
    tk.Label(opt, text="   Hub service level").grid(row=0, column=2)
    hs = tk.StringVar(value="0.98"); tk.Entry(opt, textvariable=hs, width=6).grid(row=0, column=3)

    txt = scrolledtext.ScrolledText(root, font=("Consolas", 10))
    txt.pack(fill="both", expand=True, padx=10, pady=8)

    def log(msg):
        txt.insert("end", str(msg) + "\n"); txt.see("end"); root.update()

    def go():
        txt.delete("1.0", "end")
        try:
            run(path_var.get(), working_days=float(wd.get()),
                hub_sl=float(hs.get()), log=log)
            messagebox.showinfo("Done", "Plan generated - see output workbook.")
        except Exception as exc:
            log("\nERROR: " + str(exc))
            log(traceback.format_exc())
            messagebox.showerror("Planning tool", f"Run failed:\n{exc}")

    tk.Button(root, text="RUN PLAN", font=("Segoe UI", 12, "bold"),
              bg="#1a7f37", fg="white", command=go).pack(pady=6)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        gui()
