#!/bin/bash
# run_solver.sh
#
# Runs the solver over every instance in one directory.
#
#   bash run_solver.sh 1000              # -> outputs/1000/
#   bash run_solver.sh 10000
#
# Per-instance stdout (routes, per-phase costs) goes to outputs/<set>/<name>.out
# The solver's summary line goes to stderr, collected in outputs/<set>/results.txt
# One CSV row per instance is appended to outputs/<set>/results.csv, using the
# same column names as the cuOpt harness so one sheet can hold both solvers.

set -euo pipefail

SET="${1:?usage: run_solver.sh <1000|10000|path> [outdir] [angle]}"
if [ -d "instances/${SET}" ]; then
    IN="instances/${SET}"
    TAG="${SET}"
else
    IN="${SET}"
    TAG="$(basename "${SET}")"
fi
OUTDIR="${2:-outputs/${TAG}_v${4:-2}}"
# 180, per Dinesh's observation that it beats every other angle -- confirmed here
# on R2_10_1_a, where cost and vehicle count improve monotonically with angle:
#   30 deg (12 clusters) 40 veh / 103568   120 deg (3) 29 veh /  99710
#   60 deg  (6 clusters) 37 veh / 102695   180 deg (2) 28 veh /  99525
# Clarke-Wright merges only WITHIN a cluster, so the cluster count is a hard floor
# on route count; a wider angle means fewer, bigger clusters and more room to
# consolidate. The price is runtime -- the merge loop is ~O(n^3) per cluster.
# NB: results at different angles are not comparable, so changing this
# invalidates earlier baselines.
ANGLE="${3:-180}"

# Algorithm version: 1 = base, 2 = + route elimination, 3 = + vehicle
# downgrade. One build serves all three (see solve_cvrptw.cpp), so the
# ablation compares operators rather than three drifting copies of the tree.
VERSION="${4:-2}"

if [ ! -d "${IN}" ]; then
    echo "ERROR: ${IN} not found" >&2
    exit 1
fi

# No build here. On PARAM Rudra the compute nodes have no compiler at all --
# compilation happens on the login node via build_solver.sh (manual, p.39) -- so
# a `make` in this script cannot work under sbatch. It also used to be an active
# hazard: a bare `make` after `make par` flipped .build_mode back to
# "sequential" and silently rebuilt, so the job measured the wrong binary.
if [ ! -x ./solve_cvrptw ]; then
    echo "ERROR: no solve_cvrptw binary." >&2
    echo "       Build it on the login node first:  bash build_solver.sh" >&2
    exit 2
fi

echo "Binary    : $(ls -la solve_cvrptw | awk '{print $6, $7, $8}') ($(stat -c%s solve_cvrptw) bytes)"
if [ -f .build_mode ]; then
    echo "Build mode: $(cat .build_mode)"
fi

mkdir -p "${OUTDIR}"
RESULTS="${OUTDIR}/results.txt"
CSV="${OUTDIR}/results.csv"
: > "${RESULTS}"
# Started fresh each run: the solver appends, so a stale file would mix runs.
: > "${CSV}"

echo "Instances : ${IN}  ($(ls "${IN}" | wc -l) files)"
echo "Angle     : ${ANGLE}"
echo "Version   : ${VERSION}"
echo "Output    : ${OUTDIR}"
echo

# The solver exits non-zero when a verification check fails. Under `set -e` that
# would abort the batch, so failures are counted and reported at the end instead
# -- one bad instance should not cost us the other 59.
failed=0
for infile in "${IN}"/*.txt; do
    name="$(basename "${infile}" .txt)"
    echo -n "  ${name}"
    if ./solve_cvrptw "${infile}" "${ANGLE}" "${CSV}" "${VERSION}" \
            > "${OUTDIR}/${name}.out" 2>> "${RESULTS}"; then
        echo ""
    else
        echo "   *** CHECK FAILED ***"
        failed=$((failed + 1))
    fi
done

echo
echo "Done. Summary lines in ${RESULTS}"
echo "      CSV rows      in ${CSV}"
if [ "${failed}" -gt 0 ]; then
    echo "WARNING: ${failed} instance(s) failed verification -- see ${RESULTS}" >&2
    exit 1
fi
