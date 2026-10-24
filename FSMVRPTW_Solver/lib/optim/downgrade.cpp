#include "downgrade.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <map>
#include <numeric>
#include <utility>

#include "../route_utils.h"

using namespace std;

namespace {

// One planned downgrade, produced by a single thread and applied later.
//
// The new route contents are carried WHOLESALE rather than as a list of
// (customer, position) placements. Positions were computed against successive
// states of a working copy, so replaying them by index against the live routes
// would misplace every customer after the first insertion.
struct Proposal {
  bool valid = false;
  int donor = -1;
  double benefit = 0.0;
  vector<node_t> donor_route;                      // donor after the removals
  vector<pair<int, vector<node_t>>> receivers;     // (index, new contents)
};

// Distance freed by taking `c` out from between its current neighbours, and the
// distance added by putting it between `u` and `v`. Same formula read in
// opposite directions; both non-negative by the triangle inequality.
double removal_gain(const VRP &vrp, const vector<node_t> &route, size_t pos) {
  return vrp.get_dist(route[pos - 1], route[pos]) +
         vrp.get_dist(route[pos], route[pos + 1]) -
         vrp.get_dist(route[pos - 1], route[pos + 1]);
}

double insertion_detour(const VRP &vrp, const vector<node_t> &route, size_t pos,
                        node_t c) {
  return vrp.get_dist(route[pos - 1], c) + vrp.get_dist(c, route[pos]) -
         vrp.get_dist(route[pos - 1], route[pos]);
}

// The smallest set of customers whose demand reaches `gap`.
//
// Exact minimisation is subset-sum, so: prefer a single customer, and among
// those that suffice take the SMALLEST. Naive demand-descending overshoots --
// with gap 5 and a 30-demand customer available it sheds 30 to save a class that
// needed 5, and a 30-demand customer is far harder to rehome. Only when no
// single customer suffices does it accumulate largest-first, to move the fewest.
vector<node_t> choose_shed_set(const VRP &vrp, const vector<node_t> &route,
                               demand_t gap) {
  vector<node_t> best_single;
  demand_t best_demand = 0;
  for (node_t v : route) {
    if (v == DEPOT) continue;
    const demand_t d = vrp.node[v].demand;
    if (d + 1e-9 >= gap && (best_single.empty() || d < best_demand)) {
      best_single.assign(1, v);  // not "= {v}": GCC 13 -Warray-bounds false positive
      best_demand = d;
    }
  }
  if (!best_single.empty()) return best_single;

  vector<node_t> custs;
  for (node_t v : route) {
    if (v != DEPOT) custs.push_back(v);
  }
  sort(custs.begin(), custs.end(), [&](node_t a, node_t b) {
    if (vrp.node[a].demand != vrp.node[b].demand)
      return vrp.node[a].demand > vrp.node[b].demand;
    return a < b;                                  // deterministic tie-break
  });

  vector<node_t> s;
  demand_t total = 0;
  for (node_t v : custs) {
    s.push_back(v);
    total += vrp.node[v].demand;
    if (total + 1e-9 >= gap) return s;
  }
  return {};                                       // cannot reach the threshold
}

// Hardest customers first, so the scarce resource -- headroom inside a
// receiver's current vehicle class -- is spent on the customer that needs it
// while it is still available. Place an easy customer first and it can consume
// the slack the hard one required, failing the whole all-or-nothing attempt.
//
// Lexicographic on the three things that make a customer hard to place, so there
// are no magic weights to tune: high demand, narrow time window, far from depot.
void order_by_criticality(const VRP &vrp, vector<node_t> &s) {
  sort(s.begin(), s.end(), [&](node_t a, node_t b) {
    if (vrp.node[a].demand != vrp.node[b].demand)
      return vrp.node[a].demand > vrp.node[b].demand;
    const double wa = static_cast<double>(vrp.node[a].latestTime) -
                      static_cast<double>(vrp.node[a].earlyTime);
    const double wb = static_cast<double>(vrp.node[b].latestTime) -
                      static_cast<double>(vrp.node[b].earlyTime);
    if (wa != wb) return wa < wb;
    const weight_t da = vrp.get_dist(DEPOT, a), db = vrp.get_dist(DEPOT, b);
    if (da != db) return da > db;
    return a < b;                                  // deterministic tie-break
  });
}

}  // namespace

bool downgrade_vehicle_types(const VRP &vrp, vector<vector<node_t>> &routes,
                             const vector<vector<node_t>> &knn) {
  if (routes.size() < 2) return false;

  const demand_t cap = vrp.maxCapacity();
  const vector<VehicleType> &types = vrp.types();
  bool applied_any = false;

  // Why a move was or was not made. Set FSMVRPTW_DOWNGRADE_STATS=1 to print it.
  // Without this it is impossible to tell "no candidates" from "candidates but
  // none profitable" from "the operator is broken", and those need different
  // responses.
  const bool stats = getenv("FSMVRPTW_DOWNGRADE_STATS") != nullptr;
  long n_routes = 0, n_cand = 0, n_shed = 0, n_placed = 0, n_profit = 0,
       n_applied = 0, n_tried = 0, n_capblock = 0, n_twfail = 0, n_ok = 0;
  double best_seen = -1e18;

  bool progress = true;
  while (progress) {
    progress = false;

    const int R = static_cast<int>(routes.size());
    vector<double> load(R, 0.0);
    vector<int> route_of(vrp.getSize(), -1);
    for (int r = 0; r < R; ++r) {
      load[r] = vrp.get_route_load(routes[r]);
      for (node_t v : routes[r]) {
        if (v != DEPOT) route_of[v] = r;
      }
    }

    // ---- PHASE 1: score and plan, in one parallel pass over routes --------
    //
    // Scoring and planning are not split. An earlier design ranked candidates
    // by gain/gap and planned them in that order, but the rank was never used:
    // every candidate is planned, each plan is independent of the others, and
    // Phase 2 re-sorts by actual benefit. The split only pays if the candidate
    // list is truncated, which it is not.
    //
    // proposal[] is pre-sized and each thread writes only proposal[r] for its
    // own r -- distinct indices, so no shared append, no critical section and no
    // allocation in the loop. schedule(dynamic) because the per-route cost is
    // wildly uneven: most routes are skipped in a few operations, a candidate
    // costs a full ejection plan.
    vector<Proposal> proposal(R);

#ifdef USE_PARALLEL
#pragma omp parallel for schedule(dynamic)
#endif
    for (int r = 0; r < R; ++r) {
      if (route_is_empty(routes[r])) continue;
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
      ++n_routes;

      const demand_t L = load[r];
      const int k = vrp.typeFor(L);
      if (k <= 0) continue;                        // already on the cheapest type

      const demand_t gap = L - types[k - 1].capacity;
      if (gap <= 0) continue;
      if (vrp.F(L) - vrp.F(types[k - 1].capacity) <= 0) continue;

#ifdef USE_PARALLEL
#pragma omp atomic
#endif
      ++n_cand;

      vector<node_t> s = choose_shed_set(vrp, routes[r], gap);
      if (s.empty()) continue;
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
      ++n_shed;

      demand_t shed = 0;
      for (node_t c : s) shed += vrp.node[c].demand;

      // Shedding everything would EMPTY the route, which is consolidation's job
      // -- and F(0) returns the cheapest vehicle's fixed cost rather than zero,
      // so the gain would be understated by a whole vehicle. The test must be on
      // the CHOSEN set's demand, not on gap: gap = L - capacity[k-1] is always
      // strictly less than L, so a gap-based guard could never fire, while a
      // lumpy demand distribution can still make choose_shed_set return
      // everything.
      if (shed + 1e-9 >= L) continue;

      // ---- what we GAIN. Removals are evaluated sequentially on a local copy,
      // because two customers adjacent in the donor change each other's
      // neighbours -- computing both against the original route overstates it.
      vector<node_t> donor = routes[r];
      double freed = 0.0;
      bool removal_ok = true;
      for (node_t c : s) {
        size_t pos = 0;
        for (size_t i = 1; i + 1 < donor.size(); ++i) {
          if (donor[i] == c) { pos = i; break; }
        }
        if (pos == 0) { removal_ok = false; break; }
        freed += removal_gain(vrp, donor, pos);
        donor.erase(donor.begin() + pos);
      }
      if (!removal_ok) continue;
      const double gain = freed + (vrp.F(L) - vrp.F(L - shed));

      // ---- what we PAY. working[] holds a local copy of each route this thread
      // inserts into, created on first touch and updated immediately: customers
      // in s often land in the SAME route, and each insertion changes the
      // detour, the time-window feasibility and the positions the next one sees.
      // Lazy rather than copying all routes, since |s| is small and copying the
      // whole solution per thread would be gigabytes at scale.
      map<int, vector<node_t>> working;
      vector<double> work_load = load;
      double cost = 0.0;
      bool all_placed = true;

      order_by_criticality(vrp, s);

      for (node_t c : s) {
        const demand_t dc = vrp.node[c].demand;
        double best_delta = 0.0;
        int best_t = -1;
        size_t best_pos = 0;
        bool found = false;

        for (node_t anchor : knn[c]) {
          const int t = route_of[anchor];
          if (t < 0 || t == r) continue;
          if (work_load[t] + dc > cap) {
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
            ++n_capblock;
            continue;
          }

          const double fixed_delta =
              vrp.F(work_load[t] + dc) - vrp.F(work_load[t]);

          auto it = working.find(t);
          const vector<node_t> &view =
              (it != working.end()) ? it->second : routes[t];

          for (size_t p = 1; p < view.size(); ++p) {
            if (view[p - 1] != anchor && view[p] != anchor) continue;

            const double delta = insertion_detour(vrp, view, p, c) + fixed_delta;
            if (found && delta >= best_delta) continue;   // prune before the
                                                          // expensive check
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
            ++n_tried;
            vector<node_t> trial = view;
            trial.insert(trial.begin() + p, c);
            if (!verify_single_route(vrp, trial)) {
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
              ++n_twfail;
              continue;
            }
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
            ++n_ok;

            best_delta = delta;
            best_t = t;
            best_pos = p;
            found = true;
          }
        }

        if (!found) { all_placed = false; break; }   // ALL-OR-NOTHING

        if (working.find(best_t) == working.end()) working[best_t] = routes[best_t];
        working[best_t].insert(working[best_t].begin() + best_pos, c);
        work_load[best_t] += dc;
        cost += best_delta;
      }

      if (all_placed) {
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
        ++n_placed;
#ifdef USE_PARALLEL
#pragma omp critical
#endif
        if (gain - cost > best_seen) best_seen = gain - cost;
      }
      if (!all_placed || gain <= cost) continue;
#ifdef USE_PARALLEL
#pragma omp atomic
#endif
      ++n_profit;

      proposal[r].valid = true;
      proposal[r].donor = r;
      proposal[r].benefit = gain - cost;
      proposal[r].donor_route = std::move(donor);
      for (auto &kv : working) {
        proposal[r].receivers.emplace_back(kv.first, std::move(kv.second));
      }
    }

    // ---- PHASE 2: apply, sequentially -- the only mutation point ----------
    // Compacted in route order and sorted by (benefit desc, donor asc), so the
    // result is identical on every run regardless of thread scheduling.
    vector<int> order;
    for (int r = 0; r < R; ++r) {
      if (proposal[r].valid) order.push_back(r);
    }
    sort(order.begin(), order.end(), [&](int a, int b) {
      if (proposal[a].benefit != proposal[b].benefit)
        return proposal[a].benefit > proposal[b].benefit;
      return a < b;
    });

    vector<char> touched(R, 0);
    for (int r : order) {
      Proposal &p = proposal[r];
      if (touched[p.donor]) continue;
      bool clash = false;
      for (auto &kv : p.receivers) {
        if (touched[kv.first]) { clash = true; break; }
      }
      if (clash) continue;

      // Disjoint in donor AND receivers, so nothing already applied can have
      // invalidated this plan: the loads and contents it was costed against are
      // untouched, and route indices do not shift because a downgrade keeps
      // every route alive.
      routes[p.donor] = p.donor_route;
      touched[p.donor] = 1;
      for (auto &kv : p.receivers) {
        routes[kv.first] = kv.second;
        touched[kv.first] = 1;
      }
      applied_any = true;
      progress = true;
      ++n_applied;
    }
    if (stats) {
      cerr << "[downgrade] routes=" << n_routes
           << " above_threshold=" << n_cand
           << " shed_set_found=" << n_shed
           << " all_placed=" << n_placed
           << " profitable=" << n_profit
           << " applied=" << n_applied
           << " best_benefit=" << best_seen
           << " | insertions tried=" << n_tried
           << " tw_infeasible=" << n_twfail
           << " feasible=" << n_ok
           << " cap_blocked_anchors=" << n_capblock << endl;
      n_routes = n_cand = n_shed = n_placed = n_profit = n_applied = 0;
      n_tried = n_capblock = n_twfail = n_ok = 0;
      best_seen = -1e18;
    }
  }

  return applied_any;
}
