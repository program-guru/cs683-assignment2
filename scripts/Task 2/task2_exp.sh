#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${PROJECT_ROOT}/bin"
RESULTS_DIR="${PROJECT_ROOT}/results"
RAW_DIR="${RESULTS_DIR}/raw"
TRACE_DIR="${PROJECT_ROOT}/../traces"

WARMUP=25000000
SIMULATION=25000000

# ---------------------------------------------------------------- defaults
ALL_POLICIES=(lru rrip irrip dyn_irrip)
POLICIES=("${ALL_POLICIES[@]}")
THRESHOLDS=(0.15 0.20 0.25 0.30 0.35)
TRACES=(trace1.gz trace2.gz trace3.gz)
DO_BUILD=1
DO_CSV=1

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

  -p, --policy LIST      Comma-separated: lru,rrip,irrip,dyn_irrip or all (default: all)
  -t, --traces LIST      Comma-separated, e.g. trace1,trace3 (default: trace1,trace2,trace3)
  -T, --thresholds LIST  Comma-separated, dyn_irrip only, e.g. 0.25,0.30 (default: 0.15,0.20,0.25,0.30,0.35)
      --no-build         Skip building, use existing binaries in bin/
      --no-csv           Skip regenerating metrics.csv / extracted_l2_lines.txt
  -h, --help             Show this help

Examples:
  $(basename "$0") -p rrip                       # only RRIP, all traces
  $(basename "$0") -p irrip -t trace2            # only I-RRIP on trace2
  $(basename "$0") -p dyn_irrip -T 0.25,0.30     # only two thresholds
  $(basename "$0") -p lru,rrip --no-csv          # LRU and RRIP, no CSV rewrite
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--policy)     IFS=',' read -r -a POLICIES   <<< "${2:?missing value}"; shift 2 ;;
        -t|--traces)     IFS=',' read -r -a TRACES     <<< "${2:?missing value}"; shift 2 ;;
        -T|--thresholds) IFS=',' read -r -a THRESHOLDS <<< "${2:?missing value}"; shift 2 ;;
        --no-build)      DO_BUILD=0; shift ;;
        --no-csv)        DO_CSV=0; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

# "all" expands to every policy
for p in "${POLICIES[@]}"; do
    if [[ "$p" == "all" ]]; then POLICIES=("${ALL_POLICIES[@]}"); break; fi
done

# validate policies
for p in "${POLICIES[@]}"; do
    [[ " ${ALL_POLICIES[*]} " == *" ${p} "* ]] || { echo "ERROR: unknown policy '${p}'"; usage; exit 1; }
done

# normalise trace names (allow trace1 or trace1.gz)
for i in "${!TRACES[@]}"; do
    [[ "${TRACES[$i]}" == *.gz ]] || TRACES[$i]="${TRACES[$i]}.gz"
done

want() { [[ " ${POLICIES[*]} " == *" $1 "* ]]; }

mkdir -p "${RAW_DIR}"
cd "${PROJECT_ROOT}" || exit 1

echo "Policies:   ${POLICIES[*]}"
echo "Traces:     ${TRACES[*]}"
want dyn_irrip && echo "Thresholds: ${THRESHOLDS[*]}"

# ---------------------------------------------------------------- build
if [[ "${DO_BUILD}" -eq 1 ]]; then
    echo "Building ChampSim configurations..."
    for p in "${POLICIES[@]}"; do
        ./build_champsim.sh no "$p" "$p" || exit 1
    done
fi

for p in "${POLICIES[@]}"; do
    if [[ ! -x "${BIN_DIR}/${p}" ]]; then
        echo "ERROR: Missing executable ${BIN_DIR}/${p}"
        exit 1
    fi
done

missing=0
for trace in "${TRACES[@]}"; do
    if [[ ! -f "${TRACE_DIR}/${trace}" ]]; then
        echo "ERROR: Trace not found: ${TRACE_DIR}/${trace}"
        missing=1
    fi
done
if [[ "${missing}" -ne 0 ]]; then
    echo "Update TRACE_DIR/TRACES in this script to match your actual trace locations."
    exit 1
fi

# ---------------------------------------------------------------- run
run_experiment() {
    local policy="$1" trace="$2" threshold="$3" binary="$4" label="$5"
    local trace_path="${TRACE_DIR}/${trace}"
    local output_file="${RAW_DIR}/${label}_${trace%.gz}.txt"

    echo "Running ${label} | ${trace} | threshold=${threshold}"
    {
        echo "POLICY=${policy}"
        echo "TRACE=${trace}"
        echo "THRESHOLD=${threshold}"
        echo "WARMUP=${WARMUP}"
        echo "SIMULATION=${SIMULATION}"
        echo "STARTED=$(date -Iseconds)"
        echo
    } > "${output_file}"

    local status=0
    if [[ "${policy}" == "dyn_irrip" ]]; then
        DYN_IRRIP_THRESHOLD="${threshold}" "${BIN_DIR}/${binary}" \
            -warmup_instructions "${WARMUP}" \
            -simulation_instructions "${SIMULATION}" \
            -traces "${trace_path}" >> "${output_file}" 2>&1 || status=$?
    else
        "${BIN_DIR}/${binary}" \
            -warmup_instructions "${WARMUP}" \
            -simulation_instructions "${SIMULATION}" \
            -traces "${trace_path}" >> "${output_file}" 2>&1 || status=$?
    fi

    {
        echo
        echo "EXIT_STATUS=${status}"
        echo "FINISHED=$(date -Iseconds)"
    } >> "${output_file}"

    if [[ "${status}" -ne 0 ]]; then
        echo "FAILED: ${label} on ${trace}; inspect ${output_file}"
        return 1
    fi

    if [[ "${policy}" == "dyn_irrip" ]] &&
       ! grep -Fq "IRRIP threshold:" "${output_file}"; then
        echo "WARNING: ${output_file} does not print the runtime threshold."
        echo "Check that dyn_irrip.l2c_repl reads DYN_IRRIP_THRESHOLD."
    fi
}

failed=0
for trace in "${TRACES[@]}"; do
    echo "===== ${trace} ====="
    want lru   && { run_experiment lru   "${trace}" NA lru   lru   || failed=1; }
    want rrip  && { run_experiment rrip  "${trace}" NA rrip  rrip  || failed=1; }
    want irrip && { run_experiment irrip "${trace}" NA irrip irrip || failed=1; }

    if want dyn_irrip; then
        for threshold in "${THRESHOLDS[@]}"; do
            threshold_tag="${threshold/./}"
            label="dyn_irrip_t${threshold_tag}"
            run_experiment dyn_irrip "${trace}" "${threshold}" dyn_irrip "${label}" || failed=1
        done
    fi
done

# ---------------------------------------------------------------- metrics.csv
if [[ "${DO_CSV}" -eq 1 ]]; then

grep -Eih 'IPC|MPKI|L2C|L2 cache|instruction.*(access|miss)|data.*(access|miss)|IRRIP threshold|Instruction score|Final replacement mode' \
    "${RAW_DIR}"/*.txt > "${RESULTS_DIR}/extracted_l2_lines.txt" || true

python3 - "${RAW_DIR}" "${RESULTS_DIR}/metrics.csv" <<'PYTHON'
import csv, re, sys
from pathlib import Path

raw_dir = Path(sys.argv[1])
csv_path = Path(sys.argv[2])
rows = []

def get_ipc(text):
    patterns = [
        r"CPU\s*0\s+cumulative IPC:\s*([0-9.eE+-]+)",
        r"cumulative IPC:\s*([0-9.eE+-]+)",
        r"\bIPC\s*[:=]\s*([0-9.eE+-]+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            try:
                return float(matches[-1])
            except ValueError:
                pass
    return ""

for path in sorted(raw_dir.glob("*.txt")):
    text = path.read_text(errors="replace")
    def metadata(key, default=""):
        m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else default

    policy = metadata("POLICY")
    trace = metadata("TRACE")
    threshold = metadata("THRESHOLD", "NA")
    ipc = get_ipc(text)
    l2_lines = [
        line.strip() for line in text.splitlines()
        if re.search(r"L2C|L2 cache|MPKI|instruction.*(access|miss)|data.*(access|miss)|IRRIP threshold|Instruction score|Final replacement mode", line, re.I)
    ]
    row = {
        "trace": trace, "policy": policy, "threshold": threshold,
        "runtime_threshold_verified": "", "instruction_score": "",
        "final_replacement_mode": "", "ipc": ipc, "speedup_vs_lru": "",
        "l2_load_mpki": "", "l2_instruction_load_mpki": "",
        "l2_data_load_mpki": "", "raw_log": path.name,
        "l2_stat_lines": " || ".join(l2_lines),
    }
    if policy == "dyn_irrip":
        m = re.search(r"IRRIP threshold:\s*([0-9.eE+-]+)", text)
        if m: row["runtime_threshold_verified"] = m.group(1)
        m = re.search(r"Instruction score:\s*([0-9.eE+-]+)", text)
        if m: row["instruction_score"] = m.group(1)
        m = re.search(r"Final replacement mode:\s*(\S+)", text)
        if m: row["final_replacement_mode"] = m.group(1)
    rows.append(row)

baseline = {
    row["trace"]: float(row["ipc"])
    for row in rows
    if row["policy"] == "lru" and row["ipc"] != ""
}
for row in rows:
    if row["ipc"] != "" and row["trace"] in baseline and baseline[row["trace"]] > 0:
        row["speedup_vs_lru"] = round(float(row["ipc"]) / baseline[row["trace"]], 6)

columns = [
    "trace", "policy", "threshold", "runtime_threshold_verified",
    "instruction_score", "final_replacement_mode", "ipc", "speedup_vs_lru",
    "l2_load_mpki", "l2_instruction_load_mpki", "l2_data_load_mpki",
    "raw_log", "l2_stat_lines",
]
with csv_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to {csv_path}")
print("Check runtime_threshold_verified against threshold for every dyn_irrip row.")
print("MPKI columns remain blank until matched to your exact ChampSim output labels.")
PYTHON

    echo "Metrics CSV: ${RESULTS_DIR}/metrics.csv"
    echo "Extracted L2 lines: ${RESULTS_DIR}/extracted_l2_lines.txt"
fi

echo "Done."
echo "Raw logs: ${RAW_DIR}"

if [[ "${failed}" -ne 0 ]]; then
    echo "WARNING: One or more runs failed. Check raw logs."
    exit 1
fi