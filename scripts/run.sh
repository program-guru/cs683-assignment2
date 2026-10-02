#!/bin/bash

# Dynamically find the project root (one directory up from where this script lives)
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

L1D_PREFETCHERS=("no" "ipstride" "spp")
L2C_REPLACEMENTS=("lru")

# Define all paths relative to the project root
RESULTS_DIR="${PROJECT_ROOT}/results"
TRACE_DIR="${PROJECT_ROOT}/traces"
BIN_DIR="${PROJECT_ROOT}/bin"
VISUALIZE_SCRIPT="${PROJECT_ROOT}/scripts/visualize.py"

mkdir -p "${RESULTS_DIR}"

# Navigate to project root. This ensures ChampSim's internal paths don't break during compilation.
cd "${PROJECT_ROOT}" || { echo "Failed to cd to project root"; exit 1; }

echo "--- Build and Run Binaries ---"
shopt -s nullglob
for pref in "${L1D_PREFETCHERS[@]}"; do
    for repl in "${L2C_REPLACEMENTS[@]}"; do
        bin_name="${pref}_${repl}"
        ./build_champsim.sh "${pref}" "${repl}" "${bin_name}"

        for trace_file in "${TRACE_DIR}"/*; do
            trace_name=$(basename -- "${trace_file}" | cut -f 1 -d '.')
            result_file="${RESULTS_DIR}/${bin_name}_${trace_name}.txt"
            
            # Launch all runs in the background
            "${BIN_DIR}/${bin_name}" \
                -warmup_instructions 250000 \
                -simulation_instructions 250000 \
                -traces "${trace_file}" > "${result_file}" 2>&1 &
        done
    done
done
shopt -u nullglob

# Wait for all background simulations to finish
wait 

echo "--- Generating Visualizations ---"
python3 "${VISUALIZE_SCRIPT}"