from pathlib import Path
import re
import matplotlib.pyplot as plt
import numpy as np


def parse_filename(filepath: Path) -> dict:
    """Extracts prefetcher, replacement policy, and trace name from filename."""
    stem = filepath.stem
    parts = stem.split("_")

    if len(parts) >= 3:
        l1d_prefetcher = parts[0]
        l2_policy = parts[1]
        trace_name = "_".join(parts[2:])
    else:
        l1d_prefetcher = parts[0] if len(parts) > 0 else "unknown"
        l2_policy = parts[1] if len(parts) > 1 else "unknown"
        trace_name = stem

    return {
        "file_name": filepath.name,
        "l1d_prefetcher": l1d_prefetcher,
        "l2_policy": l2_policy,
        "trace_name": trace_name,
    }


def parse_champsim_log(filepath: Path) -> dict:
    """Parses execution statistics from ChampSim output log files."""
    content = filepath.read_text(encoding="utf-8", errors="ignore")
    data = parse_filename(filepath)

    # 1. Extract IPC
    ipc_match = re.search(r"CPU \d+ cumulative IPC:\s+([\d.]+)", content)
    data["ipc"] = float(ipc_match.group(1)) if ipc_match else 0.0

    # 2. Extract L1D Load Misses & Load MPKI
    l1d_load_match = re.search(
        r"L1D LOAD\s+ACCESS:\s+\d+\s+HIT:\s+\d+\s+MISS:\s+(\d+)\s+HIT %:\s+[\d.]+\s+MISS %:\s+[\d.]+\s+MPKI:\s+([\d.]+)",
        content,
    )
    if l1d_load_match:
        data["l1d_load_miss"] = int(l1d_load_match.group(1))
        data["l1d_load_mpki"] = float(l1d_load_match.group(2))
    else:
        data["l1d_load_miss"] = 0
        data["l1d_load_mpki"] = 0.0

    # Parse the counters used by ChampSim's accuracy formula instead of
    # relying on its formatted floating-point output.
    prefetch_stats_match = re.search(
        r"L1D USEFUL LOAD PREFETCHES:\s+(\d+)"
        r"\s+PREFETCH ISSUED TO LOWER LEVEL:\s+(\d+)"
        r"\s+ACCURACY:\s+\S+",
        content,
    )
    if prefetch_stats_match:
        data["l1d_useful_prefetches"] = int(prefetch_stats_match.group(1))
        data["l1d_prefetches_to_lower_level"] = int(
            prefetch_stats_match.group(2)
        )
    else:
        data["l1d_useful_prefetches"] = 0
        data["l1d_prefetches_to_lower_level"] = 0

    issued = data["l1d_prefetches_to_lower_level"]
    data["l1d_accuracy"] = (
        data["l1d_useful_prefetches"] / issued * 100.0
        if issued > 0
        else 0.0
    )

    return data


def calculate_task1_metrics(records: list) -> list:
    """Compute speedup and L1D coverage relative to the no-prefetch baseline."""
    baselines = {}
    for r in records:
        if r.get("l1d_prefetcher") == "no":
            baselines[r["trace_name"]] = {
                "ipc": r.get("ipc", 1.0),
                "load_misses": r.get("l1d_load_miss", 1),
            }

    summary = []
    for r in records:
        trace = r["trace_name"]
        base_info = baselines.get(trace, {"ipc": 1.0, "load_misses": 1})

        ipc = r.get("ipc", 0.0)
        speedup = ipc / base_info["ipc"] if base_info["ipc"] > 0 else 1.0

        load_misses = r.get("l1d_load_miss", 0)
        baseline_misses = base_info["load_misses"]
        coverage = (
            (baseline_misses - load_misses) / baseline_misses * 100.0
            if baseline_misses > 0
            else 0.0
        )

        summary.append({
            "trace_name": trace,
            "l1d_prefetcher": r.get("l1d_prefetcher"),
            "ipc": ipc,
            "speedup": speedup,
            "l1d_load_mpki": r.get("l1d_load_mpki", 0.0),
            "l1d_accuracy": r.get("l1d_accuracy", 0.0),
            "l1d_coverage": coverage,
        })

    return summary


def generate_png_plots(summary: list, output_dir: Path):
    """Generates the 4 required PNG plot files."""
    traces = sorted(list(set(r["trace_name"] for r in summary)))
    x = np.arange(len(traces))
    w = 0.25
    w2 = 0.35

    color_base = "#7570B3"  # Muted Slate Purple
    color_ip = "#006699"    # Deep Navy Blue
    color_spp = "#D95F02"   # Vibrant Coral Orange

    # 1. Speedup Plot
    plt.figure(figsize=(7, 4.5))
    w_sp = 0.3
    sp_ip = [next(r["speedup"] for r in summary if r["trace_name"] == t and r["l1d_prefetcher"] == "ipstride") for t in traces]
    sp_spp = [next(r["speedup"] for r in summary if r["trace_name"] == t and r["l1d_prefetcher"] == "spp") for t in traces]

    plt.bar(x - w_sp / 2, sp_ip, w_sp, label="IP-stride", color=color_ip)
    plt.bar(x + w_sp / 2, sp_spp, w_sp, label="SPP", color=color_spp)
    plt.axhline(1.0, color="red", linestyle="--", linewidth=1.2, label="Baseline (1.0)")

    max_sp = max(sp_ip + sp_spp)
    plt.ylim(0.9990, max_sp + 0.0010)

    plt.xticks(x, [t.capitalize() for t in traces])
    plt.ylabel("Speedup")
    plt.title("Speedup of L1D Prefetchers over Baseline (no prefetching)")
    plt.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(output_dir / "speedup.png", dpi=300)
    plt.close()

    # 2. MPKI Plot
    plt.figure(figsize=(7, 4.5))
    m_base = [
        next(
            r["l1d_load_mpki"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "no"
        )
        for t in traces
    ]
    m_ip = [
        next(
            r["l1d_load_mpki"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "ipstride"
        )
        for t in traces
    ]
    m_spp = [
        next(
            r["l1d_load_mpki"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "spp"
        )
        for t in traces
    ]

    plt.bar(x - w, m_base, w, label="Baseline", color=color_base)
    plt.bar(x, m_ip, w, label="IP-stride", color=color_ip)
    plt.bar(x + w, m_spp, w, label="SPP", color=color_spp)
    plt.xticks(x, [t.capitalize() for t in traces])
    plt.ylabel("L1D Load MPKI")
    plt.title("L1D Load MPKI across Prefetchers")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "mpki.png", dpi=300)
    plt.close()

    # 3. Accuracy Plot
    plt.figure(figsize=(7, 4.5))
    a_ip = [
        next(
            r["l1d_accuracy"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "ipstride"
        )
        for t in traces
    ]
    a_spp = [
        next(
            r["l1d_accuracy"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "spp"
        )
        for t in traces
    ]

    plt.bar(x - w2 / 2, a_ip, w2, label="IP-stride", color=color_ip)
    plt.bar(x + w2 / 2, a_spp, w2, label="SPP", color=color_spp)
    plt.xticks(x, [t.capitalize() for t in traces])
    plt.ylabel("L1D Prefetch Accuracy (%)")
    plt.title("L1D Prefetch Accuracy (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "accuracy.png", dpi=300)
    plt.close()

    # 4. Coverage Plot
    plt.figure(figsize=(7, 4.5))
    c_ip = [
        next(
            r["l1d_coverage"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "ipstride"
        )
        for t in traces
    ]
    c_spp = [
        next(
            r["l1d_coverage"]
            for r in summary
            if r["trace_name"] == t and r["l1d_prefetcher"] == "spp"
        )
        for t in traces
    ]

    plt.bar(x - w2 / 2, c_ip, w2, label="IP-stride", color=color_ip)
    plt.bar(x + w2 / 2, c_spp, w2, label="SPP", color=color_spp)
    plt.xticks(x, [t.capitalize() for t in traces])
    plt.ylabel("L1D Prefetch Coverage (%)")
    plt.title("L1D Prefetch Coverage (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "coverage.png", dpi=300)
    plt.close()


if __name__ == "__main__":
    results_dir = (
        Path("../results") if Path("../results").exists() else Path("results")
    )
    plot_dir = (
        Path("../charts") if Path("../charts").exists() else Path("charts")
    )
    txt_files = list(results_dir.glob("*.txt"))

    if not txt_files:
        print(f"No result files found in {results_dir.resolve()}")
    else:
        raw_records = [parse_champsim_log(f) for f in txt_files]
        summary = calculate_task1_metrics(raw_records)

        # Generate ONLY PNG plots
        generate_png_plots(summary, plot_dir)
        print(
            f"Successfully generated PNG plots (speedup.png, mpki.png, accuracy.png, coverage.png) in: {plot_dir.resolve()}\n"
        )

        # Print Terminal Summary Table
        header = f"{'Trace':<10} {'Prefetcher':<12} {'IPC':<10} {'Speedup':<10} {'L1D MPKI':<10} {'Accuracy (%)':<14} {'Coverage (%)':<12}"
        print(header)
        print("-" * len(header))
        for s in sorted(
            summary, key=lambda x: (x["trace_name"], x["l1d_prefetcher"])
        ):
            print(
                f"{s['trace_name']:<10} {s['l1d_prefetcher']:<12} {s['ipc']:<10.6f} {s['speedup']:<10.4f} {s['l1d_load_mpki']:<10.4f} {s['l1d_accuracy']:<14.2f} {s['l1d_coverage']:<12.2f}"
            )