#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>

#include "lib/clark/clarke_wright.h"
#include "lib/cluster/clustering.h"
#include "lib/optim/consolidation.h"
#include "lib/optim/downgrade.h"
#include "lib/optim/inter_route_optimization.h"
#include "lib/optim/intra_route_optimization.h"
#include "lib/route_utils.h"
#include "lib/vrp.h"

using namespace std;

int main(int argc, char *argv[]) {
  VRP vrp;
  if (argc < 3) {
    cout << "seqFSMVRPTW" << '\n';
    cout << "Usage: " << argv[0]
         << " instance.txt angle_range [results.csv] [version]" << '\n';
    cout << "  version 1 = base: FSMVRPTW objective + fleet-aware moves only"
         << '\n';
    cout << "  version 2 = 1 + route elimination        (default)" << '\n';
    cout << "  version 3 = 2 + vehicle-type downgrade" << '\n';
    exit(1);
  }
  const string csv_path = (argc > 3) ? argv[3] : "solver_results.csv";

  // Three versions from ONE build, rather than three copies of the tree. Copies
  // drift -- a fix lands in one and not the others, and the ablation then
  // compares different code instead of different operators.
  const int version = (argc > 4) ? atoi(argv[4]) : 2;
  if (version < 1 || version > 3) {
    cerr << "version must be 1, 2 or 3" << endl;
    exit(1);
  }

  vrp.read(argv[1]);
  vrp.cal_dist();

  chrono::steady_clock::time_point total_start = chrono::steady_clock::now();
  chrono::steady_clock::time_point pre_start = chrono::steady_clock::now();


  
  double angle_range = stod(argv[2]);

  // vector<vector<node_t>> clusters =
  //     clustering_angle_sweep_parallel(vrp, angle_range, 1000);
  vector<vector<node_t>> clusters = clustering_angle_sweep(vrp, angle_range);
  // vector<vector<node_t>> clusters = clustering_hierarchical(vrp, n_clusters);
  // vector<vector<node_t>> clusters = clustering_kmedoid(vrp, n_clusters);
  // vector<vector<node_t>> clusters = clustering_kmeans_plus_plus(vrp, n_clusters);
  // vector<vector<node_t>> clusters = clustering_k_far(vrp, n_clusters);
  


  // Cluster sizes only. Dumping every cluster's full membership printed 10,000
  // customer ids per instance at 10k and 100,000 at 100k, which buries the
  // result line and costs real I/O time on a shared filesystem. Set
  // FSMVRPTW_DUMP_CLUSTERS=1 to get the full listing back for debugging.
  const bool dump_clusters = getenv("FSMVRPTW_DUMP_CLUSTERS") != nullptr;
  cout << "Clusters: " << clusters.size() << " [";
  for (size_t i = 0; i < clusters.size(); i++) {
    cout << (i ? " " : "") << clusters[i].size();
  }
  cout << "]" << endl;
  if (dump_clusters) {
    for (size_t i = 0; i < clusters.size(); i++) {
      cout << "Cluster " << i << ": ";
      for (auto node : clusters[i]) {
        cout << node << " ";
      }
      cout << endl;
    }
  }

  chrono::steady_clock::time_point pre_end = chrono::steady_clock::now();
  chrono::steady_clock::time_point mid_start = chrono::steady_clock::now();

#ifdef USE_PARALLEL
  auto routes = clarke_wright_cvrptw_parallel(vrp, clusters);
#else
  auto routes = clarke_wright_cvrptw(vrp, clusters);
#endif
  
  // Below approach is giving more average distance compared to other clark & wright.....
  // auto routes = clarke_wright_cvrptw_distance(vrp, clusters);

  chrono::steady_clock::time_point mid_end = chrono::steady_clock::now();

  for (auto &route : routes) {
    route.insert(route.begin(), DEPOT);
    route.push_back(DEPOT);
  }

  weight_t min_cost = calculate_total_cost(vrp, routes);
  weight_t min_cost1 = min_cost;
  cout << "Total Distance: " << min_cost << endl;

  chrono::steady_clock::time_point post_start = chrono::steady_clock::now();

#ifdef USE_PARALLEL
  inter_route_relocate_parallel(vrp, routes);
#else
  inter_route_relocate(vrp, routes);
#endif
  weight_t post_relocate_cost = calculate_total_cost(vrp, routes);

#ifdef USE_PARALLEL
  inter_route_swap_parallel(vrp, routes);
#else
  inter_route_swap(vrp, routes);
#endif
  weight_t post_swap_cost = calculate_total_cost(vrp, routes);

#ifdef USE_PARALLEL
  inter_route_2opt_star_parallel(vrp, routes);
#else
  inter_route_2opt_star(vrp, routes);
#endif
  weight_t post_2opt_star_cost = calculate_total_cost(vrp, routes);

  auto best_routes = routes;
  weight_t post_optimized_cost = min_cost;
 
#ifdef USE_PARALLEL
  best_routes = postProcessIt_parallel(vrp, best_routes, post_optimized_cost);
#else
  best_routes = postProcessIt(vrp, best_routes, post_optimized_cost);
#endif
  
  weight_t post_process_it_cost = calculate_total_cost(vrp, best_routes);

  // Consolidation: eliminate whole routes by redistributing their customers.
  // Runs LAST, on routes the rest of the local search has already tidied, so
  // every remaining route is a genuine candidate rather than a construction
  // artefact. This is the only move in the pipeline that attacks the fixed-cost
  // term directly -- step 6 measured fixed cost at ~64% of the objective.
  //
  // Interleaved with the inter-route moves rather than run once. Eliminating a
  // route reshapes the ones that absorbed its customers, and the relocate/swap
  // moves then tidy those routes and free up slack that lets the NEXT route be
  // emptied. Run alone the move stalls as soon as no single route can be emptied
  // whole; alternated, each pass creates the conditions for the next. It costs
  // ~0.014 s per call against a budget measured in seconds, so the loop is
  // effectively free.
  chrono::steady_clock::time_point consol_start = chrono::steady_clock::now();

  // Neighbour lists are shared by BOTH consolidation and downgrade: they depend
  // only on the instance, and building them is O(n^2), so once per instance.
  // Skipped entirely for version 1, which uses neither operator.
  vector<vector<node_t>> knn;
  if (version >= 2) {
    knn = build_neighbour_lists(vrp, 30);
  }

  if (version >= 2) {
    for (int round = 0; round < 10; ++round) {
      if (!consolidate_routes(vrp, best_routes, knn)) {
        break;  // nothing left to eliminate; tidying will not change that
      }
#ifdef USE_PARALLEL
      inter_route_relocate_parallel(vrp, best_routes);
      inter_route_2opt_star_parallel(vrp, best_routes);
#else
      inter_route_relocate(vrp, best_routes);
      inter_route_2opt_star(vrp, best_routes);
#endif
    }
  }
  weight_t post_elimination_cost = calculate_total_cost(vrp, best_routes);

  // Downgrade runs AFTER elimination, deliberately. A downgrade fills receiving
  // routes; done first it can fill them past the point where another route's
  // customers could have been redistributed, spending a ~55 gain to block a
  // ~1500 one. Eliminate while routes still have slack, then downgrade the
  // survivors.
  if (version >= 3) {
    for (int round = 0; round < 10; ++round) {
      if (!downgrade_vehicle_types(vrp, best_routes, knn)) break;
#ifdef USE_PARALLEL
      inter_route_relocate_parallel(vrp, best_routes);
#else
      inter_route_relocate(vrp, best_routes);
#endif
    }
  }

  chrono::steady_clock::time_point consol_end = chrono::steady_clock::now();
  weight_t post_downgrade_cost = calculate_total_cost(vrp, best_routes);

  chrono::steady_clock::time_point post_end = chrono::steady_clock::now();
  chrono::steady_clock::time_point total_end = chrono::steady_clock::now();

  // auto best_routes=routes;

  min_cost = calculate_total_cost(vrp, best_routes);
  // The full route listing is the same scale problem as the cluster dump: every
  // customer printed once, so 100,000 ids at the largest size. It is genuinely
  // useful for inspecting a solution (it is how the depot-wrapping bug was
  // found), so it stays -- behind the same switch.
  if (dump_clusters) {
    print_routes(best_routes);
  }

  const double routing = total_routing_cost(vrp, best_routes);
  const double fixed = total_fixed_cost(vrp, best_routes);
  const double total = calculate_total_cost(vrp, best_routes);
  const int used = count_vehicles_used(best_routes);
  const double recomputed = recompute_routing_cost(vrp, best_routes);

  const double total_seconds = static_cast<double>(
      chrono::duration_cast<chrono::nanoseconds>(total_end - total_start)
          .count() *
      1.E-9);

  // Verification. Our own solver gets more scrutiny than cuOpt's, not less:
  // a bug that flatters our own result is the worst outcome available.
  const bool feasible = verify_route(vrp, best_routes);
  const bool visited = all_customers_visited_once(vrp, best_routes);
  const bool dist_ok =
      fabs(routing - recomputed) <= 1e-6 * max(1.0, fabs(routing));
  const bool split_ok =
      fabs(routing + fixed - total) <= 1e-6 * max(1.0, fabs(total));
  const bool all_ok = feasible && visited && dist_ok && split_ok;

  // Per-phase detail, for tracking where cost is won.
  cerr << "File: " << argv[1] << " ";
  cerr << "Preprocessing_Time: "
       << static_cast<double>(
              chrono::duration_cast<chrono::nanoseconds>(pre_end - pre_start)
                  .count() *
              1.E-9)
       << " s ";
  cerr << "Route_Construction_Time: "
       << static_cast<double>(
              chrono::duration_cast<chrono::nanoseconds>(mid_end - mid_start)
                  .count() *
              1.E-9)
       << " s ";
  cerr << "Post_Optimization_Time: "
       << static_cast<double>(
              chrono::duration_cast<chrono::nanoseconds>(post_end - post_start)
                  .count() *
              1.E-9)
       << " s ";
  cerr << "Initial_Cost: " << min_cost1 << " ";
  cerr << "Post_Relocate_Cost: " << post_relocate_cost << " ";
  cerr << "Post_Swap_Cost: " << post_swap_cost << " ";
  cerr << "Post_2opt_star_Cost: " << post_2opt_star_cost << " ";
  cerr << "Intra_Route_Optimization_Cost: " << post_process_it_cost << " ";
  cerr << "Version: " << version << " ";
  cerr << "Post_Elimination_Cost: " << post_elimination_cost << " ";
  cerr << "Post_Downgrade_Cost: " << post_downgrade_cost << " ";
  cerr << "Consolidation_Time: "
       << static_cast<double>(
              chrono::duration_cast<chrono::nanoseconds>(consol_end -
                                                         consol_start)
                  .count() *
              1.E-9)
       << " s ";
  cerr << "Total_Cost: " << total << " ";
  cerr << "Routing_Cost: " << routing << " ";
  cerr << "Fixed_Cost: " << fixed << " ";
  cerr << "Total_Time: " << total_seconds << " s ";
  cerr << "Vehicle_Used: " << used << " ";
  cerr << "Mix: " << mix_string(vrp, best_routes) << " ";
  cerr << "route_length: " << max_length_of_route(best_routes) << " ";
  // Printed whether or not the solution is valid. The original only printed on
  // success, so an invalid answer produced no output at all and looked like a
  // crash.
  cerr << (all_ok ? "VALID" : "INVALID") << endl;

  if (!all_ok) {
    cerr << "  CHECK FAILED:";
    if (!feasible) cerr << " capacity/time-window";
    if (!visited) cerr << " customer-coverage";
    if (!dist_ok) cerr << " routing-cost-recompute";
    if (!split_ok) cerr << " cost-split";
    cerr << endl;
  }

  // One CSV row, same column names as the cuOpt harness so both solvers'
  // results can sit in a single sheet.
  {
    ostringstream caps, fixes;
    for (size_t k = 0; k < vrp.numTypes(); ++k) {
      if (k) {
        caps << '/';
        fixes << '/';
      }
      caps << vrp.types()[k].capacity;
      fixes << vrp.types()[k].fixedCost;
    }
    vector<int> per_type = fleet_mix(vrp, best_routes);

    // ate as well as app: app alone leaves the initial get/put position
    // unspecified, so tellp() could report 0 on a file that already has rows and
    // the header would be written again mid-file.
    ofstream csv(csv_path, ios::app | ios::ate);
    if (!csv) {
      cerr << "  WARNING: could not open " << csv_path
           << " -- no CSV row written" << endl;
      return all_ok ? 0 : 1;
    }
    if (csv.tellp() == 0) {
      csv << "instance,version,n_customers,n_types,capacities,fixed_costs,"
             "total_cost,routing_cost,fixed_cost,vehicles_used,mix,";
      for (size_t k = 0; k < vrp.numTypes(); ++k) {
        csv << "used_" << vrp.types()[k].letter << ',';
      }
      csv << "solve_time_s,routing_cost_check,cost_split_check,"
             "customers_covered,feasible\n";
    }
    csv << argv[1] << ',' << version << ',' << vrp.getSize() - 1 << ','
        << vrp.numTypes() << ','
        << caps.str() << ',' << fixes.str() << ',' << total << ',' << routing
        << ',' << fixed << ',' << used << ',' << mix_string(vrp, best_routes)
        << ',';
    for (size_t k = 0; k < vrp.numTypes(); ++k) {
      csv << per_type[k] << ',';
    }
    csv << total_seconds << ',' << (dist_ok ? "OK" : "MISMATCH") << ','
        << (split_ok ? "OK" : "MISMATCH") << ',' << (visited ? "OK" : "MISSING")
        << ',' << (feasible ? "OK" : "VIOLATED") << '\n';
  }

  // Non-zero exit on a failed check, so a batch run cannot quietly produce a
  // sheet full of invalid solutions.
  return all_ok ? 0 : 1;
}
