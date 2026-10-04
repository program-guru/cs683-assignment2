#!/bin/bash

WARMUP=10000000
SIM=25000000

# keep L1D stat lines + final IPC line(s); drop the prefetcher's own debug prints
filter() {
    grep -E '^(L1D|Finished CPU|CPU [0-9]+ cumulative IPC)' | grep -v 'L1D Prefetch Accuracy'
}

run_one() {   # $1 = binary, $2 = trace, $3 = label, $4 = output file
    {
        echo "========================================"
        echo "$3"
        echo "========================================"
    } >> "$4"

    "$1" \
        -warmup_instructions $WARMUP \
        -simulation_instructions $SIM \
        -traces "$2" | filter >> "$4"

    echo "" >> "$4"
}

for i in 1 2 3
do
    trace="../traces/trace${i}.gz"
    output="trace${i}-data.txt"
    : > "$output"     # start with a clean file each run

    run_one ./bin/baseline "$trace" "TRACE ${i} - BASELINE" "$output"
    run_one ./bin/ipstride "$trace" "TRACE ${i} - IPSTRIDE" "$output"
    run_one ./bin/mixed    "$trace" "TRACE ${i} - NU+U"     "$output"
done

# IPC summary
echo ""
echo "IPC summary:"
for i in 1 2 3
do
    echo "--- trace${i} ---"
    grep -E 'TRACE|Finished CPU' "trace${i}-data.txt"
done