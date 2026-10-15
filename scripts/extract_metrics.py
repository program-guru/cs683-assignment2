import csv
import re
from pathlib import Path



SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

RAW_DIR = PROJECT_ROOT / "results" / "raw"
FILTERED_DIR = PROJECT_ROOT / "results" / "filtered_raw"
CSV_PATH = PROJECT_ROOT / "results" / "metrics.csv"


def normalize(text):
    return re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_").lower()


def infer_metadata(path):
    name = str(path).lower()
    filename = path.stem.lower()

    trace_match = re.search(r"trace[_-]?(\d+)", name)
    trace = f"trace{trace_match.group(1)}" if trace_match else ""

    if re.search(r"dyn[_-]?irrip|dynamic[_-]?irrip", name):
        policy = "Dynamic I-RRIP"
    elif re.search(r"irrip", name):
        policy = "I-RRIP"
    elif re.search(r"\blru\b|(^|[_-])lru([_.-]|$)", name):
        policy = "LRU"
    else:
        policy = ""

    threshold_match = re.search(
        r"(?:threshold|thresh|thr)[_-]?(\d+(?:[._p]\d+)?)",
        filename,
    )

    threshold = ""
    if threshold_match:
        threshold = threshold_match.group(1).replace("p", ".")
        if "_" in threshold:
            threshold = threshold.replace("_", ".")

    return {
        "experiment": path.stem,
        "trace": trace,
        "policy": policy,
        "threshold": threshold,
    }


def extract_ipc(line):
    if not re.search(r"\bIPC\b", line, re.IGNORECASE):
        return None

    match = re.search(
        r"\bIPC\b\s*(?::|=)?\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?\d+)?)",
        line,
        re.IGNORECASE,
    )

    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

    return None


def is_l2_line(line):
    return bool(
        re.search(r"\bL2C\b|\bL2\b", line, re.IGNORECASE)
    )


def parse_l2_line(line):

    match = re.search(
        r"\b(L2C[A-Z0-9_ ]*|L2[A-Z0-9_ ]*)\b",
        line,
        re.IGNORECASE,
    )

    if not match:
        return {}

    label = normalize(match.group(1))
    if not label:
        label = "l2"


    field_pattern = re.compile(
        r"\b([A-Za-z][A-Za-z0-9_ /-]*?)\s*[:=]\s*"
        r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    )

    stats = {}

    for field_match in field_pattern.finditer(line):
        field = normalize(field_match.group(1))

        if not field:
            continue

        try:
            value = float(field_match.group(2))
        except ValueError:
            continue

        column = f"l2_{label}_{field}"
        stats[column] = value


    if "mpki" not in line.lower():
        mpki_match = re.search(
            r"\bMPKI\b\s+([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?\d+)?)",
            line,
            re.IGNORECASE,
        )
        if mpki_match:
            stats[f"l2_{label}_mpki"] = float(mpki_match.group(1))

    return stats


def main():
    if not RAW_DIR.exists():
        raise SystemExit(
            f"Raw log directory not found: {RAW_DIR}\n"
            "Check that results/raw contains your ChampSim logs."
        )

    log_files = sorted(
        path for path in RAW_DIR.rglob("*")
        if path.is_file()
    )

    if not log_files:
        raise SystemExit(f"No files found in {RAW_DIR}")

    FILTERED_DIR.mkdir(parents=True, exist_ok=True)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    all_metric_columns = set()
    total_kept_lines = 0

    for log_path in log_files:
        try:
            content = log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            print(f"Skipping unreadable file {log_path}: {error}")
            continue

        metadata = infer_metadata(log_path)
        row = {
            **metadata,
            "source_file": str(log_path.relative_to(RAW_DIR)),
            "ipc": "",
        }

        filtered_lines = []

        for line in content.splitlines():
            ipc = extract_ipc(line)
            keep = False

            if ipc is not None:
                row["ipc"] = ipc
                filtered_lines.append(line)
                keep = True

            if is_l2_line(line):
                filtered_lines.append(line)
                row.update(parse_l2_line(line))
                keep = True

            if keep:
                total_kept_lines += 1

        relative_path = log_path.relative_to(RAW_DIR)
        filtered_path = FILTERED_DIR / relative_path
        filtered_path.parent.mkdir(parents=True, exist_ok=True)

        filtered_path.write_text(
            "\n".join(filtered_lines) + ("\n" if filtered_lines else ""),
            encoding="utf-8",
        )

        all_metric_columns.update(
            key for key in row
            if key.startswith("l2_")
        )
        rows.append(row)

        print(
            f"Processed: {relative_path} | "
            f"IPC: {row['ipc'] if row['ipc'] != '' else 'not found'} | "
            f"L2 fields: {sum(k.startswith('l2_') for k in row)}"
        )

    base_columns = [
        "experiment",
        "source_file",
        "trace",
        "policy",
        "threshold",
        "ipc",
    ]

    l2_columns = sorted(all_metric_columns)
    columns = base_columns + l2_columns

    with CSV_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(row)

    print("\nExtraction complete.")
    print(f"Raw files processed: {len(rows)}")
    print(f"IPC/L2 lines retained: {total_kept_lines}")
    print(f"Filtered logs: {FILTERED_DIR}")
    print(f"Metrics CSV: {CSV_PATH}")
    print(f"L2 metric columns found: {len(l2_columns)}")

    missing_ipc = sum(row["ipc"] == "" for row in rows)
    if missing_ipc:
        print(
            f"Warning: IPC was not found in {missing_ipc} file(s). "
            "Inspect their filtered logs and check the IPC output format."
        )

    missing_metadata = sum(
        not row["trace"] or not row["policy"]
        for row in rows
    )
    if missing_metadata:
        print(
            f"Warning: {missing_metadata} file(s) had an unknown trace "
            "or policy. Rename files to include names such as "
            "'trace1', 'lru', 'irrip', or 'dyn_irrip'."
        )


if __name__ == "__main__":
    main()
