#ifndef DOWNGRADE_H
#define DOWNGRADE_H

#include <vector>

#include "../vrp.h"

// Parallel vehicle-type downgrade -- the operator that makes a route's vehicle
// CHEAPER, as opposed to consolidation, which removes the route entirely.
//
// F(z) is a step function: capacity comes in discrete classes, so a route one
// unit over a class limit pays for the whole next class up. A route carrying 170
// under the Braysy R1 table needs the 170-capacity vehicle at 460; shedding 30
// units drops it to the 140-capacity vehicle at 405, saving 55.
//
// The existing inter-route moves cannot find this even though they are now
// fleet-aware. Shedding 30 may take three customers, and moving the first two
// costs distance while saving NOTHING -- the load is still above the threshold,
// so F has not moved. A first-improvement search rejects step one and never
// reaches the reward. The intermediate states form a valley. This operator
// evaluates the whole shed as one atomic move, so those states never exist.
//
// Accepts a move only when gain > cost, where
//
//   gain = [F(L) - F(L-D)]            the cheaper vehicle class
//        + sum of removalGain(c)      travel the donor no longer makes
//
//   cost = sum of detour(c)           detours forced in the receivers
//        + sum of [F(load(r)+d(c)) - F(load(r))]   receivers' own upgrades
//
// Every term is non-negative: F is non-decreasing, and detours are non-negative
// by the triangle inequality.
//
// `knn` is the neighbour list built by build_neighbour_lists() for the
// consolidation operator -- shared rather than rebuilt, since it depends only on
// the instance. It bounds the insertion search to positions adjacent to a
// customer's nearest neighbours, which is what keeps the operator affordable.
//
// Returns true if at least one downgrade was applied.
//
// ORDERING: run this AFTER consolidation. A downgrade fills receiving routes,
// and if it fills them past the point where some other route's customers could
// have been redistributed, it spends a ~55 gain to block a ~1500 one. Eliminate
// while routes still have slack; downgrade the survivors afterwards.
bool downgrade_vehicle_types(const VRP &vrp,
                             std::vector<std::vector<node_t>> &routes,
                             const std::vector<std::vector<node_t>> &knn);

#endif
