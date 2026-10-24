#!/bin/bash
#SBATCH --job-name=fsm_solver
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --partition=small
#SBATCH --time=24:00:00
#SBATCH --output=fsm_solver.%J.out
#SBATCH --error=fsm_solver.%J.err

# ---------------------------------------------------------------------------
# EXECUTION ONLY. Build first, on the login node:
#
#     bash build_solver.sh          # then:
#     sbatch submit_solver.sh 1000
#
# PARAM Rudra User Manual, p.39: "Compilations are performed on the login node.
# Only the execution is scheduled via SLURM on the compute nodes." The compute
# nodes carry no compiler at all -- jobs 613144/613150 both died on
# `g++: command not found` while trying to build here.
#
# This is a CPU / OpenMP solver, so it belongs on the 'small' partition, NOT on
# sgpu. Per the PARAM Rudra login banner:
#
#   small*  1 - 48 cores    2-00:00:00   3 running / 7 queued per user
#   sgpu    1 core / 1 GPU  2-00:00:00   1 running / 2 queued per user
#
# small is the cluster default, allows up to 48 cores, and permits three
# concurrent jobs rather than one -- so solver runs need not queue behind the
# cuOpt jobs.
#
# If an account is required, uncomment and fill in:
#   sacctmgr show assoc user=$USER
##SBATCH -A <account-name>
# ---------------------------------------------------------------------------

SET="${1:-1000}"

# Sweep angle, passed through to run_solver.sh.
#
# 180 is best on 1,000 customers, but it does NOT scale. Clarke-Wright merges
# only within a cluster and its merge loop is ~O(R^3) in the customers per
# cluster, so halving the cluster count multiplies the work eightfold:
#
#   customers  angle  clusters  per cluster   relative work
#      1,000    180       2          500          1x   (measured ~65 s seq)
#     10,000     30      12          833         28x
#     10,000    180       2        5,000      1,000x
#    100,000     30      12        8,333     28,000x
#
# So pass a SMALLER angle as the instance size grows:
#     sbatch submit_solver.sh 1000            # angle 180
#     sbatch submit_solver.sh 10000 30        # angle 30
ANGLE="${2:-180}"

# Which algorithm version(s) to run. "all" runs 1, 2 and 3 in sequence
# inside a single job, so the whole ablation comes from one queue slot and
# one binary.
#   sbatch submit_solver.sh 1000  180 all
#   sbatch submit_solver.sh 10000  30 3
VERSIONS="${3:-2}"
if [ "${VERSIONS}" = "all" ]; then VERSIONS="1 2 3"; fi

cd "$SLURM_SUBMIT_DIR"

# No `module purge`: in the batch environment the module function points at
# /opt/ohpc/admin/lmod/lmod/libexec/lmod, which does not exist. It prints an
# error, returns 0 regardless, and leaves PATH without a compiler. No sample
# script in the manual purges either.
#
# Spack is still loaded here even though nothing is compiled: the binary was
# linked against this GCC's libstdc++ and libgomp, and the manual is explicit
# that "compilation and execution must be done with the same libraries and
# matching version" (p.48). Without it the run dies on a missing GLIBCXX.
export SPACK_ROOT=/home/apps/spack
. "$SPACK_ROOT/share/spack/setup-env.sh"
spack load gcc/wnu2dj5

# OpenMP threads = the cores we asked SLURM for.
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-48}"

echo "=== run environment ==="
echo "host             : $(hostname)"
echo "OMP_NUM_THREADS  : $OMP_NUM_THREADS"
echo "instance set     : $SET"
echo "sweep angle      : $ANGLE"
echo "versions         : $VERSIONS"
echo "libstdc++        : $(ldd ./solve_cvrptw 2>/dev/null | grep -o '/[^ ]*libstdc++[^ ]*' | head -1)"
echo

if [ ! -x ./solve_cvrptw ]; then
    echo "No solve_cvrptw binary. Build it on the LOGIN node first:" >&2
    echo "    bash build_solver.sh" >&2
    exit 1
fi

# Fail before burning the allocation if the binary cannot run on this node --
# an -march mismatch shows up as SIGILL, which is otherwise cryptic. Run it with
# no arguments: it prints usage and exits 1, which proves it loads and executes.
#
# The status must be captured from a plain call, not from `if ! cmd; then rc=$?`
# -- inside that branch $? is the status of the negation (0), not of the command.
./solve_cvrptw >/dev/null 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 132 ]; then
        echo "Binary died with SIGILL on $(hostname): built for a newer" >&2
        echo "microarchitecture than this node provides. Rebuild with a lower" >&2
        echo "ARCH, e.g.  make clean && make ARCH=-march=x86-64-v2 par" >&2
        exit 1
    fi
    # Any other code is just the usage message from running with no arguments,
    # which is what we want: the binary loads and executes.
fi

status=0
for V in ${VERSIONS}; do
    echo
    echo "################ VERSION ${V} ################"
    bash run_solver.sh "${SET}" "outputs/${SET}_v${V}" "${ANGLE}" "${V}"
    rc=$?
    if [ "${rc}" -ne 0 ]; then status=${rc}; fi
done
status=$?

if [ "${status}" -ne 0 ]; then
    # run_solver.sh exits 1 when instances failed verification, and non-1 when it
    # died for some other reason. Do not report the second as the first -- an
    # inaccurate error message costs more time than no message at all.
    if [ "${status}" -eq 1 ]; then
        echo "run_solver.sh: at least one instance failed verification." >&2
    else
        echo "run_solver.sh aborted with exit ${status} -- see the errors above." >&2
    fi
fi
exit "${status}"
