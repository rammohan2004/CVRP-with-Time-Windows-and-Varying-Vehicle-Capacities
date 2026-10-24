#!/usr/bin/env python3
"""Report sheets for the CURRENT results: ours (v1/v2/v3) and cuOpt.

    python make_current_sheet.py

Writes up to three paste-ready CSVs in outputs/:

  sheet_current_ours.csv        our v1/v2/v3 at 1k and 10k, full detail
  sheet_current_cuopt.csv       cuOpt at 1k and 10k, full detail
  sheet_current_comparison.csv  ours (v2) vs cuOpt, instance by instance

The cuOpt sheets are written only once outputs_cuopt/current_<size>.csv exists
(produced by cuOpt_FSMVRPTW/submit_cuopt_current.sh). That run gives cuOpt, per
instance, OUR v2 runtime rounded up to a whole second, and at most 500
vehicles of each type.

No text cell starts with "=", "+" or "-": Google Sheets parses those as
formulas and shows #ERROR!.
"""

import csv
import math
import os
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CU = os.path.join(ROOT, "cuOpt_FSMVRPTW", "outputs_cuopt")
OUTDIR = os.path.join(HERE, "outputs")

SIZES = ("1000", "10000")
LABEL_SIZE = {"1000": "1,000", "10000": "10,000"}
ANGLE = {"1000": 180, "10000": 30}
VNAME = {1: "v1 base (objective + fleet-aware moves)",
         2: "v2 = v1 + route elimination",
         3: "v3 = v2 + vehicle-type downgrade"}
TYPES = "ABCDEFGH"
PER_TYPE_CAP = 500

# Measured, not assumed: three identical runs of our solver at 1k, angle 180.
NOISE_TOTAL_PCT = 0.113
NOISE_MEAN_INST_PCT = 1.63
NOISE_MAX_INST_PCT = 5.77

base = os.path.basename
fam = lambda n: n.split("_")[0]
pct = lambda a, b: (a - b) / b * 100 if b else 0.0
budget = lambda t: math.ceil(float(t) - 1e-9)      # same rule as --match-ceil


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_ours(size, v):
    p = os.path.join(OUTDIR, f"{size}_v{v}", "results.csv")
    if not os.path.exists(p):
        return {}
    rows = list(csv.DictReader(open(p)))
    if rows and rows[0].get("version") != str(v):
        raise SystemExit(f"{p}: version column missing or not {v} -- produced "
                         f"by an old binary.")
    return {base(r["instance"]): r for r in rows}


def load_cuopt(size):
    """instance -> the SUCCESS row of the current run (one budget each)."""
    p = os.path.join(CU, f"current_{size}.csv")
    if not os.path.exists(p):
        return {}, []
    good, bad = {}, []
    for r in csv.DictReader(open(p)):
        if r.get("status_name") == "SUCCESS":
            good[base(r["instance"])] = r
        else:
            bad.append((base(r["instance"]), r.get("status_name", "")))
    return good, bad


_demand_cache = {}


def total_demand(size, name):
    key = (size, name)
    if key in _demand_cache:
        return _demand_cache[key]
    tot, seen, hdr = 0.0, False, False
    for ln in open(os.path.join(HERE, "instances", size, name)):
        s = ln.split()
        if not s:
            continue
        if s[0] == "CUSTOMER":
            seen, hdr = True, False
            continue
        if not seen:
            continue
        if not hdr:
            hdr = True
            continue
        if len(s) >= 7 and s[0].isdigit() and int(s[0]) != 0:
            tot += float(s[3])
    _demand_cache[key] = tot
    return tot


# --------------------------------------------------------------------------
# Row helpers shared by both solvers (same column names in both CSVs)
# --------------------------------------------------------------------------

def caps_of(r):
    return [float(x) for x in r["capacities"].split("/")]


def used(r, c):
    x = r.get(f"used_{c}", "")
    return int(float(x)) if x not in ("", None) else 0


def capacity_bought(r):
    return sum(q * used(r, c) for q, c in zip(caps_of(r), TYPES))


def lower_bound(size, name, r):
    return math.ceil(total_demand(size, name) / max(caps_of(r)))


def checks_ok(r, cols):
    return all(r.get(c, "") == "OK" for c in cols)


OUR_CHECKS = ("routing_cost_check", "cost_split_check", "customers_covered",
              "feasible")
CU_CHECKS = ("routing_cost_check", "cost_split_check")


def summary(rows):
    """Totals over a dict of rows."""
    if not rows:
        return None
    rs = list(rows.values())
    tot = sum(float(r["total_cost"]) for r in rs)
    rt = sum(float(r["routing_cost"]) for r in rs)
    fx = sum(float(r["fixed_cost"]) for r in rs)
    vh = sum(int(r["vehicles_used"]) for r in rs)
    return dict(n=len(rs), total=tot, routing=rt, fixed=fx, veh=vh,
                mean_cost=tot / len(rs), mean_veh=vh / len(rs),
                fixed_share=fx / tot * 100,
                mean_t=st.mean(float(r["solve_time_s"]) for r in rs),
                max_t=max(float(r["solve_time_s"]) for r in rs),
                per_type={c: sum(used(r, c) for r in rs) for c in TYPES})


def detail_header(prefix=""):
    return ([f"{prefix}total cost", f"{prefix}routing cost",
             f"{prefix}fixed cost", f"{prefix}fixed share %",
             f"{prefix}vehicles", f"{prefix}mix"]
            + [f"{prefix}used_{c}" for c in TYPES]
            + [f"{prefix}capacity use %"])


def detail_cells(size, name, r):
    tot = float(r["total_cost"])
    cb = capacity_bought(r)
    return ([f"{tot:.1f}", f"{float(r['routing_cost']):.1f}",
             f"{float(r['fixed_cost']):.1f}",
             f"{float(r['fixed_cost']) / tot * 100:.1f}",
             r["vehicles_used"], r["mix"]]
            + [r.get(f"used_{c}", "") for c in TYPES]
            + [f"{total_demand(size, name) / cb * 100:.1f}" if cb else ""])


def type_totals_rows(add, label, s):
    add([label] + [s["per_type"][c] for c in TYPES] + [s["veh"]])


def write(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Wrote {path}  ({len(rows)} rows)")


# --------------------------------------------------------------------------
# Sheet 1: ours
# --------------------------------------------------------------------------

def sheet_ours(ours):
    rows = []
    add = rows.append
    add(["FSMVRPTW - our solver, current results (v1 / v2 / v3)"])
    add(["Objective: total cost = distance travelled + fixed cost of vehicles used"])
    add(["Vehicle costs: Braysy et al. (2009) Table 1, cost structure (a)"])
    add(["Hardware: PARAM Rudra 'small', 48 OpenMP threads, GCC 13.3 -O3 "
         "-march=haswell"])
    add(["Sweep angle: 180 at 1,000 customers, 30 at 10,000"])
    add(["v1 = objective + fleet-aware moves | v2 = v1 + route elimination | "
         "v3 = v2 + vehicle-type downgrade"])
    add(["All three versions of a size come from one binary and one job, so "
         "differences are the operators alone."])
    add(["v2 is the version compared against cuOpt."])
    add([])

    # 1. headline
    add(["1. HEADLINE (v2)"])
    add(["metric", "1,000 customers", "10,000 customers"])
    s = {z: summary(ours[z][2]) for z in SIZES}
    get = lambda z, f: f(s[z]) if s[z] else "-"
    add(["instances", get("1000", lambda x: x["n"]), get("10000", lambda x: x["n"])])
    add(["sweep angle", ANGLE["1000"], ANGLE["10000"]])
    add(["total cost (sum over instances)",
         get("1000", lambda x: f"{x['total']:.0f}"),
         get("10000", lambda x: f"{x['total']:.0f}")])
    add(["routing cost (sum)", get("1000", lambda x: f"{x['routing']:.0f}"),
         get("10000", lambda x: f"{x['routing']:.0f}")])
    add(["fixed cost (sum)", get("1000", lambda x: f"{x['fixed']:.0f}"),
         get("10000", lambda x: f"{x['fixed']:.0f}")])
    add(["fixed cost share %", get("1000", lambda x: f"{x['fixed_share']:.1f}"),
         get("10000", lambda x: f"{x['fixed_share']:.1f}")])
    add(["mean cost per instance", get("1000", lambda x: f"{x['mean_cost']:.1f}"),
         get("10000", lambda x: f"{x['mean_cost']:.1f}")])
    add(["vehicles (sum)", get("1000", lambda x: x["veh"]),
         get("10000", lambda x: x["veh"])])
    add(["mean vehicles per instance", get("1000", lambda x: f"{x['mean_veh']:.1f}"),
         get("10000", lambda x: f"{x['mean_veh']:.1f}")])
    add(["mean runtime (s)", get("1000", lambda x: f"{x['mean_t']:.2f}"),
         get("10000", lambda x: f"{x['mean_t']:.1f}")])
    add(["max runtime (s)", get("1000", lambda x: f"{x['max_t']:.2f}"),
         get("10000", lambda x: f"{x['max_t']:.1f}")])
    add(["all verification checks OK"] +
        [(f"{sum(1 for r in ours[z][2].values() if checks_ok(r, OUR_CHECKS))}"
          f"/{len(ours[z][2])}") if ours[z][2] else "-" for z in SIZES])
    add([])

    # 2. precision
    add(["2. HOW PRECISE ARE OUR NUMBERS?"])
    add(["Our solver is nondeterministic under OpenMP: ties in the parallel "
         "best-move merge break by thread order."])
    add(["Measured on three identical runs at 1k, angle 180:"])
    add(["metric", "value"])
    add(["identical per-instance results", "0/60"])
    add(["mean per-instance spread %", NOISE_MEAN_INST_PCT])
    add(["largest per-instance spread %", NOISE_MAX_INST_PCT])
    add(["spread of the 60-instance total %", NOISE_TOTAL_PCT])
    add(["Rule used below", "a version difference in the total smaller than "
         f"{NOISE_TOTAL_PCT}% is not a result"])
    add([])

    # 3. ablation
    add(["3. ABLATION - what each version contributes"])
    for z in SIZES:
        if not all(ours[z].values()):
            add([f"{LABEL_SIZE[z]} customers: not all versions present"])
            continue
        add([f"{LABEL_SIZE[z]} customers (angle {ANGLE[z]})"])
        add(["version", "total cost", "routing", "fixed", "fixed share %",
             "vs v1 %", "vs previous %", "vehicles", "mean time (s)", "valid",
             "beyond noise?"])
        sv = {v: summary(ours[z][v]) for v in (1, 2, 3)}
        for v in (1, 2, 3):
            x = sv[v]
            prev = pct(x["total"], sv[v - 1]["total"]) if v > 1 else None
            verdict = ("" if v == 1 else
                       "yes" if abs(prev) > NOISE_TOTAL_PCT else "no, within noise")
            ok = sum(1 for r in ours[z][v].values() if checks_ok(r, OUR_CHECKS))
            add([VNAME[v], f"{x['total']:.0f}", f"{x['routing']:.0f}",
                 f"{x['fixed']:.0f}", f"{x['fixed_share']:.1f}",
                 f"{pct(x['total'], sv[1]['total']):.3f}",
                 f"{prev:.3f}" if prev is not None else "",
                 x["veh"], f"{x['mean_t']:.2f}", f"{ok}/{x['n']}", verdict])
        add([])
        add(["vehicles by type", *[f"used_{c}" for c in TYPES], "total"])
        for v in (1, 2, 3):
            type_totals_rows(add, VNAME[v], sv[v])
        add([])
        add(["by family", "v1 mean cost", "v2 mean cost", "v3 mean cost",
             "v2 vs v1 %", "v3 vs v2 %", "v1 mean veh", "v2 mean veh",
             "v3 mean veh", "v2 mean routing", "v2 mean fixed"])
        names = sorted(ours[z][2])
        for f in sorted({fam(n) for n in names}):
            ns = [n for n in names if fam(n) == f]
            m = {v: st.mean(float(ours[z][v][n]["total_cost"]) for n in ns)
                 for v in (1, 2, 3)}
            vv = {v: st.mean(int(ours[z][v][n]["vehicles_used"]) for n in ns)
                  for v in (1, 2, 3)}
            add([f, f"{m[1]:.0f}", f"{m[2]:.0f}", f"{m[3]:.0f}",
                 f"{pct(m[2], m[1]):.2f}", f"{pct(m[3], m[2]):.2f}",
                 f"{vv[1]:.1f}", f"{vv[2]:.1f}", f"{vv[3]:.1f}",
                 f"{st.mean(float(ours[z][2][n]['routing_cost']) for n in ns):.0f}",
                 f"{st.mean(float(ours[z][2][n]['fixed_cost']) for n in ns):.0f}"])
        add([])

    # 4. fleet
    add(["4. FLEET (v2) - vehicles against the capacity lower bound"])
    add(["LB = ceil(total demand / largest capacity); no feasible solution can "
         "use fewer vehicles."])
    add(["capacity use % = total demand / total capacity of the vehicles used."])
    add(["size", "family", "instances", "mean LB", "mean vehicles", "vehicles/LB",
         "capacity use %"])
    for z in SIZES:
        o = ours[z][2]
        for f in sorted({fam(n) for n in o}):
            ns = [n for n in o if fam(n) == f]
            lb = sum(lower_bound(z, n, o[n]) for n in ns)
            vh = sum(int(o[n]["vehicles_used"]) for n in ns)
            dem = sum(total_demand(z, n) for n in ns)
            cb = sum(capacity_bought(o[n]) for n in ns)
            add([LABEL_SIZE[z], f, len(ns), f"{lb / len(ns):.1f}",
                 f"{vh / len(ns):.1f}", f"{vh / lb:.2f}", f"{dem / cb * 100:.1f}"])
    add([])

    # 5.. every instance, every version
    sec = 5
    for z in SIZES:
        for v in (2, 1, 3):
            o = ours[z][v]
            if not o:
                continue
            add([f"{sec}. EVERY INSTANCE, {LABEL_SIZE[z]} CUSTOMERS, {VNAME[v]}"])
            add(["instance", "family", *detail_header(), "time (s)",
                 "cuOpt budget (s) = ceil(time)" if v == 2 else "", "checks"])
            for n in sorted(o):
                r = o[n]
                add([n.replace(".txt", ""), fam(n), *detail_cells(z, n, r),
                     f"{float(r['solve_time_s']):.2f}",
                     budget(r["solve_time_s"]) if v == 2 else "",
                     "OK" if checks_ok(r, OUR_CHECKS) else "FAIL"])
            add([])
            sec += 1
    return rows


# --------------------------------------------------------------------------
# Sheet 2: cuOpt
# --------------------------------------------------------------------------

def sheet_cuopt(cu, bad, ours):
    rows = []
    add = rows.append
    add(["FSMVRPTW - NVIDIA cuOpt, current run"])
    add(["Objective: total cost = distance travelled + fixed cost of vehicles used"])
    add(["Vehicle costs: Braysy et al. (2009) Table 1, cost structure (a)"])
    add(["Hardware: 1x NVIDIA A100 80GB, PARAM Rudra 'sgpu'"])
    add([f"Fleet: at most {PER_TYPE_CAP} vehicles of each type"])
    add(["Time limit per instance: our v2 runtime on that instance, rounded up "
         "to a whole second (140.2 s gives 141 s)"])
    add([])

    add(["1. HEADLINE"])
    add(["metric", "1,000 customers", "10,000 customers"])
    s = {z: summary(cu[z]) for z in SIZES}
    get = lambda z, f: (f(s[z]) if s[z] else "not run")
    add(["instances solved", get("1000", lambda x: x["n"]),
         get("10000", lambda x: x["n"])])
    add(["instances NOT solved"] + [len(bad[z]) for z in SIZES])
    add(["total cost (sum)", get("1000", lambda x: f"{x['total']:.0f}"),
         get("10000", lambda x: f"{x['total']:.0f}")])
    add(["routing cost (sum)", get("1000", lambda x: f"{x['routing']:.0f}"),
         get("10000", lambda x: f"{x['routing']:.0f}")])
    add(["fixed cost (sum)", get("1000", lambda x: f"{x['fixed']:.0f}"),
         get("10000", lambda x: f"{x['fixed']:.0f}")])
    add(["fixed cost share %", get("1000", lambda x: f"{x['fixed_share']:.1f}"),
         get("10000", lambda x: f"{x['fixed_share']:.1f}")])
    add(["mean cost per instance", get("1000", lambda x: f"{x['mean_cost']:.1f}"),
         get("10000", lambda x: f"{x['mean_cost']:.1f}")])
    add(["vehicles (sum)", get("1000", lambda x: x["veh"]),
         get("10000", lambda x: x["veh"])])
    add(["mean vehicles per instance", get("1000", lambda x: f"{x['mean_veh']:.1f}"),
         get("10000", lambda x: f"{x['mean_veh']:.1f}")])
    add(["mean time budget (s)"] +
        [f"{st.mean(float(r['timeout_s']) for r in cu[z].values()):.2f}"
         if cu[z] else "not run" for z in SIZES])
    add(["mean wall time incl. setup (s)", get("1000", lambda x: f"{x['mean_t']:.2f}"),
         get("10000", lambda x: f"{x['mean_t']:.1f}")])
    add(["instances where a type hit the 500 cap"] +
        [sum(1 for r in cu[z].values() if r.get("fleet_cap_binding"))
         if cu[z] else "not run" for z in SIZES])
    add(["all verification checks OK"] +
        [f"{sum(1 for r in cu[z].values() if checks_ok(r, CU_CHECKS))}/{len(cu[z])}"
         if cu[z] else "not run" for z in SIZES])
    add([])
    for z in SIZES:
        for n, status in bad[z]:
            add(["NOT SOLVED", LABEL_SIZE[z], n, status])
    if any(bad.values()):
        add([])

    add(["2. VEHICLES BY TYPE (sum over instances)"])
    add(["size", *[f"used_{c}" for c in TYPES], "total"])
    for z in SIZES:
        if s[z]:
            type_totals_rows(add, LABEL_SIZE[z], s[z])
    add([])

    add(["3. BY FAMILY"])
    add(["size", "family", "instances", "mean cost", "mean routing", "mean fixed",
         "mean vehicles", "mean LB", "vehicles/LB", "capacity use %"])
    for z in SIZES:
        c = cu[z]
        for f in sorted({fam(n) for n in c}):
            ns = [n for n in c if fam(n) == f]
            lb = sum(lower_bound(z, n, c[n]) for n in ns)
            vh = sum(int(c[n]["vehicles_used"]) for n in ns)
            dem = sum(total_demand(z, n) for n in ns)
            cb = sum(capacity_bought(c[n]) for n in ns)
            add([LABEL_SIZE[z], f, len(ns),
                 f"{st.mean(float(c[n]['total_cost']) for n in ns):.0f}",
                 f"{st.mean(float(c[n]['routing_cost']) for n in ns):.0f}",
                 f"{st.mean(float(c[n]['fixed_cost']) for n in ns):.0f}",
                 f"{vh / len(ns):.1f}", f"{lb / len(ns):.1f}", f"{vh / lb:.2f}",
                 f"{dem / cb * 100:.1f}"])
    add([])

    sec = 4
    for z in SIZES:
        c = cu[z]
        if not c:
            continue
        add([f"{sec}. EVERY INSTANCE, {LABEL_SIZE[z]} CUSTOMERS"])
        add(["instance", "family", *detail_header(), "time budget (s)",
             "our v2 time (s)", "wall time (s)", "type at cap", "checks"])
        for n in sorted(c):
            r = c[n]
            o = ours[z][2].get(n)
            add([n.replace(".txt", ""), fam(n), *detail_cells(z, n, r),
                 f"{float(r['timeout_s']):g}",
                 f"{float(o['solve_time_s']):.2f}" if o else "",
                 f"{float(r['solve_time_s']):.2f}",
                 r.get("fleet_cap_binding") or "none",
                 "OK" if checks_ok(r, CU_CHECKS) else "FAIL"])
        add([])
        sec += 1
    return rows


# --------------------------------------------------------------------------
# Sheet 3: comparison
# --------------------------------------------------------------------------

def sheet_comparison(cu, ours):
    rows = []
    add = rows.append
    add(["FSMVRPTW - our solver (v2) vs NVIDIA cuOpt, current results"])
    add(["Equal time per instance: cuOpt gets our v2 runtime rounded UP to a "
         "whole second, so never less time than we used."])
    add([f"cuOpt fleet: at most {PER_TYPE_CAP} per type. Ours: no fleet limit."])
    add(["gap % = (ours - cuOpt) / cuOpt x 100. NEGATIVE means we are cheaper."])
    add([])

    pairs = {}
    for z in SIZES:
        pairs[z] = {n: (ours[z][2][n], cu[z][n], ours[z][3].get(n))
                    for n in sorted(ours[z][2]) if n in cu[z]}

    def gaps(z, key="total_cost", ver=0):
        return [pct(float(p[ver][key]), float(p[1][key]))
                for p in pairs[z].values() if p[ver] is not None]

    add(["1. HEADLINE"])
    add(["metric", "1,000 customers", "10,000 customers"])
    row = lambda lbl, f: add([lbl] + [f(z) if pairs[z] else "pending"
                                      for z in SIZES])
    row("instances compared", lambda z: len(pairs[z]))
    row("our mean runtime (s)", lambda z: f"{st.mean(float(p[0]['solve_time_s']) for p in pairs[z].values()):.2f}")
    row("cuOpt mean budget (s)", lambda z: f"{st.mean(float(p[1]['timeout_s']) for p in pairs[z].values()):.2f}")
    row("mean gap % per instance (headline)", lambda z: f"{st.mean(gaps(z)):.2f}")
    row("aggregate gap % (sum of costs)", lambda z: f"{pct(sum(float(p[0]['total_cost']) for p in pairs[z].values()), sum(float(p[1]['total_cost']) for p in pairs[z].values())):.2f}")
    row("instances we beat", lambda z: f"{sum(1 for g in gaps(z) if g < 0)}/{len(pairs[z])}")
    row("best gap % (most in our favour)", lambda z: f"{min(gaps(z)):.2f}")
    row("worst gap %", lambda z: f"{max(gaps(z)):.2f}")
    row("v3 mean gap % per instance (for reference)", lambda z: f"{st.mean(gaps(z, ver=2)):.2f}")
    add([])

    add(["2. WHERE THE DIFFERENCE COMES FROM (sums over instances)"])
    add(["size", "solver", "total cost", "routing cost", "fixed cost",
         "fixed share %", "vehicles", "mean vehicles"])
    for z in SIZES:
        if not pairs[z]:
            continue
        for lbl, i in (("ours v2", 0), ("cuOpt", 1)):
            tot = sum(float(p[i]["total_cost"]) for p in pairs[z].values())
            rt = sum(float(p[i]["routing_cost"]) for p in pairs[z].values())
            fx = sum(float(p[i]["fixed_cost"]) for p in pairs[z].values())
            vh = sum(int(p[i]["vehicles_used"]) for p in pairs[z].values())
            add([LABEL_SIZE[z], lbl, f"{tot:.0f}", f"{rt:.0f}", f"{fx:.0f}",
                 f"{fx / tot * 100:.1f}", vh, f"{vh / len(pairs[z]):.1f}"])
        add([LABEL_SIZE[z], "gap % (ours vs cuOpt)",
             f"{pct(*[sum(float(p[i]['total_cost']) for p in pairs[z].values()) for i in (0, 1)]):.2f}",
             f"{pct(*[sum(float(p[i]['routing_cost']) for p in pairs[z].values()) for i in (0, 1)]):.2f}",
             f"{pct(*[sum(float(p[i]['fixed_cost']) for p in pairs[z].values()) for i in (0, 1)]):.2f}",
             "",
             f"{pct(*[sum(int(p[i]['vehicles_used']) for p in pairs[z].values()) for i in (0, 1)]):.2f}",
             ""])
    add([])

    add(["3. VEHICLES BY TYPE (sum over instances)"])
    add(["size", "solver", *[f"used_{c}" for c in TYPES], "total"])
    for z in SIZES:
        if not pairs[z]:
            continue
        for lbl, i in (("ours v2", 0), ("cuOpt", 1)):
            add([LABEL_SIZE[z], lbl] +
                [sum(used(p[i], c) for p in pairs[z].values()) for c in TYPES] +
                [sum(int(p[i]["vehicles_used"]) for p in pairs[z].values())])
    add([])

    add(["4. GAP BY FAMILY"])
    add(["size", "family", "instances", "mean gap %", "won", "ours mean cost",
         "cuOpt mean cost", "ours mean veh", "cuOpt mean veh", "mean LB",
         "ours capacity use %", "cuOpt capacity use %"])
    for z in SIZES:
        pz = pairs[z]
        for f in sorted({fam(n) for n in pz}):
            ns = [n for n in pz if fam(n) == f]
            g = [pct(float(pz[n][0]["total_cost"]), float(pz[n][1]["total_cost"]))
                 for n in ns]
            dem = sum(total_demand(z, n) for n in ns)
            add([LABEL_SIZE[z], f, len(ns), f"{st.mean(g):.2f}",
                 f"{sum(1 for x in g if x < 0)}/{len(ns)}",
                 f"{st.mean(float(pz[n][0]['total_cost']) for n in ns):.0f}",
                 f"{st.mean(float(pz[n][1]['total_cost']) for n in ns):.0f}",
                 f"{st.mean(int(pz[n][0]['vehicles_used']) for n in ns):.1f}",
                 f"{st.mean(int(pz[n][1]['vehicles_used']) for n in ns):.1f}",
                 f"{st.mean(lower_bound(z, n, pz[n][0]) for n in ns):.1f}",
                 f"{dem / sum(capacity_bought(pz[n][0]) for n in ns) * 100:.1f}",
                 f"{dem / sum(capacity_bought(pz[n][1]) for n in ns) * 100:.1f}"])
    add([])

    add(["5. PRECISION"])
    add(["Both solvers are nondeterministic. Ours: identical runs differ by "
         f"{NOISE_MEAN_INST_PCT}% per instance on average, up to "
         f"{NOISE_MAX_INST_PCT}%."])
    add(["A single-instance gap smaller than a few % is therefore not "
         "meaningful on its own; the per-instance MEAN is."])
    for z in SIZES:
        g = gaps(z) if pairs[z] else []
        if len(g) > 1:
            se = st.stdev(g) / math.sqrt(len(g))
            add([f"{LABEL_SIZE[z]}: mean gap {st.mean(g):.2f}%, standard error "
                 f"{se:.2f} pp, so roughly {st.mean(g) - 2 * se:.2f}% to "
                 f"{st.mean(g) + 2 * se:.2f}%"])
    add([])

    sec = 6
    for z in SIZES:
        pz = pairs[z]
        if not pz:
            continue
        add([f"{sec}. EVERY INSTANCE, {LABEL_SIZE[z]} CUSTOMERS"])
        add(["instance", "family", "our time (s)", "cuOpt budget (s)",
             *detail_header("ours "), *detail_header("cuOpt "),
             "gap % total", "gap % routing", "gap % fixed", "vehicle diff",
             "cuOpt type at cap", "checks"])
        for n, (o, c, _v3) in pz.items():
            ok = checks_ok(o, OUR_CHECKS) and checks_ok(c, CU_CHECKS)
            add([n.replace(".txt", ""), fam(n),
                 f"{float(o['solve_time_s']):.2f}", f"{float(c['timeout_s']):g}",
                 *detail_cells(z, n, o), *detail_cells(z, n, c),
                 f"{pct(float(o['total_cost']), float(c['total_cost'])):.2f}",
                 f"{pct(float(o['routing_cost']), float(c['routing_cost'])):.2f}",
                 f"{pct(float(o['fixed_cost']), float(c['fixed_cost'])):.2f}",
                 int(o["vehicles_used"]) - int(c["vehicles_used"]),
                 c.get("fleet_cap_binding") or "none",
                 "OK" if ok else "FAIL"])
        add([])
        sec += 1
    return rows


def main():
    ours = {z: {v: load_ours(z, v) for v in (1, 2, 3)} for z in SIZES}
    cu, bad = {}, {}
    for z in SIZES:
        cu[z], bad[z] = load_cuopt(z)

    write(os.path.join(OUTDIR, "sheet_current_ours.csv"), sheet_ours(ours))
    if any(cu.values()) or any(bad.values()):
        write(os.path.join(OUTDIR, "sheet_current_cuopt.csv"),
              sheet_cuopt(cu, bad, ours))
        write(os.path.join(OUTDIR, "sheet_current_comparison.csv"),
              sheet_comparison(cu, ours))
    else:
        print(f"No {CU}/current_<size>.csv yet -- cuOpt and comparison sheets "
              "skipped. Rerun this once the cuOpt jobs finish.")


if __name__ == "__main__":
    main()
