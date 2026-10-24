#!/usr/bin/env python3
"""Compare our 1,000-customer results against Braysy et al. (2009) as published.

    python make_braysy_sheet.py   ->  outputs/sheet_vs_braysy.csv

Source: Braysy, Porkka, Dullaert, Repoussis & Tarantilis (2009), "A well-scalable
metaheuristic for the fleet size and mix vehicle routing problem with time
windows", Expert Systems with Applications. Per-family averages for 1,000
customers, cost structure A, read from the paper's results table.

Their three reported variants:
  Normal  their full-effort setting            38.25 s mean CPU at this size
  Quick   their reduced-effort setting          6.35 s
  MSDAL   the earlier Braysy et al. (2008) multistart deterministic annealing

Cross-check performed: averaging the six per-family Normal values gives
89165.95, which matches the paper's own size-level summary (Table 3) exactly, so
the figures below are being read from the right columns.
"""

import csv
import os
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OURS = os.path.join(HERE, "outputs", "1000", "results_after(8+9).csv")
OUT = os.path.join(HERE, "outputs", "sheet_vs_braysy.csv")

# family -> (Normal, Quick, MSDAL), 1000 customers, cost structure A
BRAYSY = {
    "C1":  (115023.57, 117816.15, 116020.70),
    "C2":  (98576.46,  99600.16,  102138.68),
    "R1":  (82919.62,  88660.99,  85609.66),
    "R2":  (86196.78,  92323.20,  89037.40),
    "RC1": (73488.49,  78563.27,  76032.60),
    "RC2": (78790.75,  87002.82,  82336.65),
}
ORDER = ["C1", "C2", "R1", "R2", "RC1", "RC2"]
BRAYSY_CPU = {"Normal": 38.25, "Quick": 6.35}

pct = lambda a, b: (a - b) / b * 100


def main():
    rows = [r for r in csv.DictReader(open(OURS))]
    byfam = defaultdict(list)
    times = []
    for r in rows:
        byfam[os.path.basename(r["instance"]).split("_")[0]].append(
            float(r["total_cost"]))
        times.append(float(r["solve_time_s"]))

    out = []
    add = out.append

    add(["Our FSMVRPTW solver vs Braysy et al. (2009), as published"])
    add(["1,000 customers, cost structure A, per-family averages over 10 "
         "instances each"])
    add([])
    add(["Source: Braysy, Porkka, Dullaert, Repoussis & Tarantilis (2009), "
         "Expert Systems with Applications."])
    add(["Their variants: Normal = full effort; Quick = reduced effort; "
         "MSDAL = their earlier 2008 method."])
    add(["Cross-check: the six Normal values average to 89165.95, matching the "
         "paper's own Table 3 exactly,"])
    add(["so these are read from the right columns."])
    add([])

    add(["1. COST BY FAMILY (positive % = we cost more)"])
    add(["family", "ours", "Braysy Normal", "vs Normal %",
         "Braysy Quick", "vs Quick %", "Braysy MSDAL", "vs MSDAL %"])
    to = tn = tq = tm = 0.0
    for k in ORDER:
        o = st.mean(byfam[k])
        n, q, m = BRAYSY[k]
        to += o; tn += n; tq += q; tm += m
        add([k, f"{o:.0f}", f"{n:.0f}", f"{pct(o,n):+.1f}",
             f"{q:.0f}", f"{pct(o,q):+.1f}", f"{m:.0f}", f"{pct(o,m):+.1f}"])
    add(["MEAN", f"{to/6:.0f}", f"{tn/6:.0f}", f"{pct(to,tn):+.1f}",
         f"{tq/6:.0f}", f"{pct(to,tq):+.1f}", f"{tm/6:.0f}", f"{pct(to,tm):+.1f}"])
    add([])

    add(["2. SUMMARY"])
    add(["comparison", "mean gap %", "reading"])
    add(["vs Normal (their best)", f"{pct(to,tn):+.1f}",
         "we are behind their full-effort setting"])
    add(["vs Quick", f"{pct(to,tq):+.1f}",
         "we beat their reduced-effort setting"])
    add(["vs MSDAL (their 2008 method)", f"{pct(to,tm):+.1f}",
         "essentially level"])
    add(["families where we beat Normal",
         f"{sum(1 for k in ORDER if st.mean(byfam[k]) < BRAYSY[k][0])}/6",
         "R2 and RC2 - the wide-time-window families"])
    add([])

    add(["3. RUNTIME - NOT DIRECTLY COMPARABLE"])
    add(["Quoting a speedup here would be meaningless: the hardware is 17 years "
         "apart and the core counts differ."])
    add(["what", "value", "hardware"])
    add(["our mean runtime", f"{st.mean(times):.2f} s",
         "PARAM Rudra, 48 OpenMP threads, Xeon G-6240R, 2026"])
    add(["their Normal", f"{BRAYSY_CPU['Normal']:.2f} s",
         "Intel Core Duo T7700 2.4 GHz laptop, ~2007"])
    add(["their Quick", f"{BRAYSY_CPU['Quick']:.2f} s", "same"])
    add(["", "", ""])
    add(["The honest statement is about SOLUTION QUALITY, not speed: we land "
         "between their Quick and Normal"])
    add(["settings, and level with their earlier MSDAL method."])
    add([])

    add(["4. CAVEATS - read before quoting any of this"])
    add(["1. INSTANCE PROVENANCE IS UNVERIFIED. Our instances were generated "
         "from Gehring-Homberger sources by"])
    add(["   instance-generator/solomon_to_fsmvrptw.py using the paper's Table 1 "
         "vehicle costs (structure a)."])
    add(["   They should match the paper's setup, but we do not hold the "
         "authors' own instance files, so we"])
    add(["   cannot confirm they are identical. Treat this as INDICATIVE, not a "
         "like-for-like benchmark."])
    add(["2. Their figures are the BEST of several runs; ours is a single run "
         "per instance."])
    add(["3. Their per-family numbers are averages over 10 instances, as are "
         "ours, so the granularity matches."])
    add(["4. This comparison is on COST ONLY. The paper does not report the "
         "routing/fixed split or fleet mix"])
    add(["   per family at this size, so those columns cannot be compared."])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        csv.writer(f).writerows(out)
    print(f"Wrote {OUT}")
    print(f"  ours {to/6:.0f} vs Normal {tn/6:.0f} ({pct(to,tn):+.1f}%), "
          f"Quick {tq/6:.0f} ({pct(to,tq):+.1f}%), MSDAL {tm/6:.0f} "
          f"({pct(to,tm):+.1f}%)")


if __name__ == "__main__":
    main()
