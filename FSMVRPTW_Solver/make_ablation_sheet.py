#!/usr/bin/env python3
"""Three-version ablation sheet: what each operator contributes.

    python make_ablation_sheet.py      ->  outputs/sheet_ablation.csv

  v1 = FSMVRPTW objective + fleet-aware move deltas (steps 1-8)
  v2 = v1 + route elimination by redistribution     (step 9)
  v3 = v2 + parallel vehicle-type downgrade

Reads outputs/<size>_v<N>/results.csv for size in {1000, 10000}. All three
versions of a size come from ONE binary and ONE job, at the same sweep angle,
so differences are attributable to the operators alone.

NOISE FLOOR. Our solver is nondeterministic under OpenMP (ties in the
thread-local-best merge resolve by thread order). Three identical runs at 1k,
angle 180, differed by 0.113% on the 60-instance total and by up to 5.77% on a
single instance. A version difference smaller than that is not a result.
"""

import csv
import os
import re
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "sheet_ablation.csv")
NOISE_TOTAL_PCT = 0.113          # measured: identical runs, 1k, 60-instance total
NOISE_MAX_INST_PCT = 5.77        # measured: worst single-instance spread
LABEL = {1: "v1 base (steps 1-8)", 2: "v2 + route elimination",
         3: "v3 + vehicle downgrade"}
ANGLE = {"1000": 180, "10000": 30}

pct = lambda a, b: (a - b) / b * 100 if b else 0.0
fam = lambda n: n.split("_")[0]


def load(size, v):
    p = os.path.join(HERE, "outputs", f"{size}_v{v}", "results.csv")
    if not os.path.exists(p):
        return None
    rows = list(csv.DictReader(open(p)))
    # Guard against the stale-binary failure: no version column, or the wrong one.
    if rows and ("version" not in rows[0] or rows[0]["version"] != str(v)):
        raise SystemExit(f"{p}: version column missing or not {v} -- this was "
                         f"produced by an old binary. Rebuild and rerun.")
    return {os.path.basename(r["instance"]): r for r in rows}


def downgrade_stats(size):
    p = os.path.join(HERE, "outputs", f"{size}_v3", "results.txt")
    if not os.path.exists(p):
        return None
    keys = ["routes", "above_threshold", "shed_set_found", "all_placed",
            "profitable", "applied", "tried", "tw_infeasible", "feasible"]
    tot = defaultdict(int)
    lines = 0
    for ln in open(p):
        if not ln.startswith("[downgrade]"):
            continue
        lines += 1
        for k in keys:
            m = re.search(rf"\b{k}=(\d+)", ln)
            if m:
                tot[k] += int(m.group(1))
    return lines, tot


def main():
    rows = []
    add = rows.append
    add(["FSMVRPTW three-version ablation"])
    add(["v1 = objective + fleet-aware moves | v2 = v1 + route elimination | "
         "v3 = v2 + vehicle downgrade"])
    add(["Each size: all three versions from one binary, one job, one angle -- "
         "so differences are the operators alone."])
    add([f"NOISE FLOOR: identical runs differ by {NOISE_TOTAL_PCT}% on the "
         f"60-instance total, up to {NOISE_MAX_INST_PCT}% on one instance. "
         "Smaller differences are not results."])
    add([])

    for size in ("1000", "10000"):
        data = {v: load(size, v) for v in (1, 2, 3)}
        if not all(data.values()):
            add([f"{size} customers: results not all present yet"])
            add([])
            continue
        names = sorted(set.intersection(*(set(d) for d in data.values())))
        tot = {v: sum(float(data[v][n]["total_cost"]) for n in names)
               for v in (1, 2, 3)}
        veh = {v: sum(int(data[v][n]["vehicles_used"]) for n in names)
               for v in (1, 2, 3)}
        tim = {v: st.mean(float(data[v][n]["solve_time_s"]) for n in names)
               for v in (1, 2, 3)}
        ok = {v: sum(1 for n in names if data[v][n]["feasible"] == "OK")
              for v in (1, 2, 3)}

        add([f"{size} CUSTOMERS  (angle {ANGLE[size]}, {len(names)} instances)"])
        add(["version", "total cost", "vs v1 %", "vs previous %", "vehicles",
             "mean time (s)", "valid", "beyond noise floor?"])
        for v in (1, 2, 3):
            vs1 = pct(tot[v], tot[1])
            prev = pct(tot[v], tot[v - 1]) if v > 1 else 0.0
            verdict = ("--" if v == 1 else
                       ("yes" if abs(prev) > NOISE_TOTAL_PCT else "NO - within noise"))
            add([LABEL[v], f"{tot[v]:.0f}", f"{vs1:+.3f}",
                 f"{prev:+.3f}" if v > 1 else "--", veh[v], f"{tim[v]:.2f}",
                 f"{ok[v]}/{len(names)}", verdict])
        add([])

        add(["by family", "v1 mean", "v2 mean", "v3 mean", "v2 vs v1 %",
             "v3 vs v2 %", "v1 veh", "v2 veh", "v3 veh"])
        for f in sorted({fam(n) for n in names}):
            ns = [n for n in names if fam(n) == f]
            m = {v: st.mean(float(data[v][n]["total_cost"]) for n in ns)
                 for v in (1, 2, 3)}
            vv = {v: st.mean(int(data[v][n]["vehicles_used"]) for n in ns)
                  for v in (1, 2, 3)}
            add([f, f"{m[1]:.0f}", f"{m[2]:.0f}", f"{m[3]:.0f}",
                 f"{pct(m[2], m[1]):+.2f}", f"{pct(m[3], m[2]):+.2f}",
                 f"{vv[1]:.1f}", f"{vv[2]:.1f}", f"{vv[3]:.1f}"])
        add([])

        ds = downgrade_stats(size)
        if ds:
            lines, t = ds
            add(["v3 downgrade diagnostics (summed over all passes)"])
            add(["metric", "value"])
            for k, lbl in [("routes", "routes examined"),
                           ("above_threshold", "above a capacity threshold"),
                           ("shed_set_found", "shed set found"),
                           ("tried", "insertions tried"),
                           ("tw_infeasible", "  ...time-window infeasible"),
                           ("feasible", "  ...feasible"),
                           ("all_placed", "candidates fully placed"),
                           ("profitable", "profitable (gain > cost)"),
                           ("applied", "applied")]:
                add([lbl, t[k]])
            add([])

        add([f"every instance, {size} customers"])
        add(["instance", "v1 cost", "v2 cost", "v3 cost", "v2 vs v1 %",
             "v3 vs v2 %", "v1 veh", "v2 veh", "v3 veh"])
        for n in names:
            c = {v: float(data[v][n]["total_cost"]) for v in (1, 2, 3)}
            add([n.replace(".txt", ""), f"{c[1]:.1f}", f"{c[2]:.1f}",
                 f"{c[3]:.1f}", f"{pct(c[2], c[1]):+.2f}",
                 f"{pct(c[3], c[2]):+.2f}"] +
                [data[v][n]["vehicles_used"] for v in (1, 2, 3)])
        add([])

    with open(OUT, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
