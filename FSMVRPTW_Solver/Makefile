CXX := g++

# ARCH: NOT -march=native. The PARAM Rudra manual (p.39) requires compiling on
# the login node and executing on compute nodes, so -march=native would bake in
# the LOGIN node's microarchitecture and the binary could hit an illegal
# instruction (SIGILL) on a compute node.
#
# haswell means the AVX2 / FMA / BMI2 level -- the same ISA level as the newer
# spelling -march=x86-64-v3, but understood by every GCC since 4.9 rather than
# only GCC 11+, so the same Makefile builds on the cluster and on a developer
# machine with an older compiler. Both microarchitectures spack reports here,
# cascadelake and skylake_avx512, are well above this level.
#
# Raise it if a measurement justifies it, e.g.:  make ARCH=-march=cascadelake par
ARCH ?= -march=haswell
CXXFLAGS := -std=c++17 -Wall -Wextra -pedantic -MMD -MP -O3 $(ARCH)
LDFLAGS :=
TARGET := solve_cvrptw
ANGLE ?= 45
MODE := sequential
BUILD_MODE_FILE := .build_mode

ifeq ($(PARALLEL),1)
MODE := parallel
CXXFLAGS += -fopenmp -DUSE_PARALLEL
LDFLAGS += -fopenmp
endif

SRCS := \
	solve_cvrptw.cpp \
	lib/vrp.cpp \
	lib/route_utils.cpp \
	lib/cluster/clustering.cpp \
	lib/clark/clarke_wright.cpp \
	lib/optim/intra_route_optimization.cpp \
	lib/optim/inter_route_optimization.cpp \
	lib/optim/consolidation.cpp \
	lib/optim/downgrade.cpp

OBJS := $(SRCS:.cpp=.o)
DEPS := $(OBJS:.o=.d)

.PHONY: all clean clean-outputs run par FORCE

all: $(BUILD_MODE_FILE) $(TARGET)

par:
	$(MAKE) PARALLEL=1 all

$(BUILD_MODE_FILE): FORCE
	@if [ ! -f $@ ] || [ "$$(cat $@)" != "$(MODE)" ]; then echo "$(MODE)" > $@; fi

$(TARGET): $(OBJS)
	$(CXX) $(OBJS) $(LDFLAGS) -o $@

%.o: %.cpp $(BUILD_MODE_FILE)
	$(CXX) $(CXXFLAGS) -c $< -o $@

run: $(TARGET)
	./$(TARGET) r102.txt $(ANGLE)

# Build artefacts only. `rm -rf outputs` used to live here, which meant a single
# `make clean` destroyed every measurement collected so far -- and submit_solver.sh
# called it on each submit. Results are the one thing here that cannot be
# regenerated cheaply, so cleaning them is never automatic.
clean:
	rm -f $(TARGET) $(OBJS) $(DEPS) $(BUILD_MODE_FILE)

# Explicit, and deliberately not part of `clean`.
clean-outputs:
	rm -rf outputs

-include $(DEPS)
