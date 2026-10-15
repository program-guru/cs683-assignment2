#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                      
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

FLOAT = r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"

TASK_2A = "task2a"
TASK_2B = "task2b"


# ====================================================================== config
@dataclass(frozen=True)
class Paths:
    raw_dir: Path
    filtered_dir: Path
    csv_path: Path
    fig_dir: Path

    @classmethod
    def default(cls, raw_dir: Path | None = None) -> "Paths":
        root = Path(__file__).resolve().parent.parent
        res = root / "results"
        return cls(raw_dir=raw_dir or res / "raw",
                   filtered_dir=res / "filtered_raw",
                   csv_path=res / "metrics.csv",
                   fig_dir=res / "figures")


@dataclass(frozen=True)
class MetricSpec:
    name: str          
    pattern: str       
    label: str         


METRICS = [
    MetricSpec("ipc",           r"\bIPC\b\s*[:=]?\s*" + FLOAT,                "IPC"),
    MetricSpec("l2c_load_mpki", r"L2C LOAD\s+ACCESS:.*?MPKI:\s*" + FLOAT,     "L2C Load MPKI"),
    MetricSpec("l2_instr_mpki", r"L2C INSTRUCTION LOAD MPKI:\s*" + FLOAT,     "L2 Instruction Load MPKI"),
    MetricSpec("l2_data_mpki",  r"L2C DATA LOAD MPKI:\s*" + FLOAT,            "L2 Data Load MPKI"),
]
MPKI_KEYS = ["l2c_load_mpki", "l2_instr_mpki", "l2_data_mpki"]
PLOT_KEYS = ["ipc", "speedup", "speedup_rrip"] + MPKI_KEYS
LABELS = {m.name: m.label for m in METRICS} | {
    "speedup": "Speedup over LRU",
    "speedup_rrip": "Speedup over RRIP",
}
DECIMALS = {"ipc": 4, "speedup": 4, "speedup_rrip": 4,
            "l2c_load_mpki": 3, "l2_instr_mpki": 3, "l2_data_mpki": 3}
HIGHER_IS_BETTER = {"ipc", "speedup", "speedup_rrip"}

FIXED_COLORS = {"LRU": "#7f7f7f", "RRIP": "#ff7f0e", "I-RRIP": "#1f77b4"}


# ================================================================== extraction
class MetadataInferer:

    POLICY_FROM_TEXT = {"lru": "LRU",
                        "rrip": "RRIP", "srrip": "RRIP",
                        "irrip": "I-RRIP", "i-rrip": "I-RRIP",
                        "dynamic i-rrip": "Dynamic I-RRIP"}
    # Order matters: check "irrip" before "rrip" so irrip_* files are not read as RRIP.
    POLICY_FROM_NAME = [(r"dyn[_-]?irrip|dynamic[_-]?irrip", "Dynamic I-RRIP"),
                        (r"irrip", "I-RRIP"),
                        (r"(^|[_\-/.])s?rrip([_\-/.]|$)", "RRIP"),
                        (r"(^|[_\-/.])lru([_\-/.]|$)", "LRU")]

    def infer(self, path: Path, text: str) -> dict:
        name = str(path).lower()
        return {"experiment": path.stem,
                "trace": self._trace(name),
                "policy": self._policy(name, text),
                "threshold": self._threshold(path.stem.lower(), text)}

    @staticmethod
    def _trace(name: str) -> str:
        m = re.search(r"trace[_-]?(\d+)", name)
        return f"trace{m.group(1)}" if m else ""

    def _policy(self, name: str, text: str) -> str:
        m = re.search(r"L2C has (.+?) replacement policy", text, re.I)
        if m and m.group(1).strip().lower() in self.POLICY_FROM_TEXT:
            return self.POLICY_FROM_TEXT[m.group(1).strip().lower()]
        for pat, label in self.POLICY_FROM_NAME:
            if re.search(pat, name):
                return label
        return ""

    @staticmethod
    def _threshold(stem: str, text: str):
        m = re.search(r"Configured IRRIP threshold:\s*" + FLOAT, text)
        if m:
            return float(m.group(1))
        m = re.search(r"(?:threshold|thresh|thr)[_-]?(\d+(?:[._p]\d+)?)", stem)
        if m:
            return float(m.group(1).replace("p", ".").replace("_", "."))
        m = re.search(r"(?:^|_)t(\d{2,3})(?:_|$)", stem)       # t035 -> 0.35
        if m:
            return int(m.group(1)) / 100
        return np.nan


class LogParser:

    KEEP = re.compile(r"\bIPC\b|\bL2C?\b|Configured IRRIP threshold", re.I)

    def __init__(self, metrics=METRICS, inferer: MetadataInferer | None = None):
        self.metrics = metrics
        self.inferer = inferer or MetadataInferer()

    @staticmethod
    def _last_match(pattern: str, text: str) -> float:
        hits = re.findall(pattern, text)
        return float(hits[-1]) if hits else np.nan

    def parse(self, path: Path) -> tuple[dict, list[str]]:
        text = path.read_text(encoding="utf-8", errors="replace")
        row = self.inferer.infer(path, text)
        row.update({m.name: self._last_match(m.pattern, text) for m in self.metrics})
        kept = [ln for ln in text.splitlines() if self.KEEP.search(ln)]
        return row, kept


class MetricsExtractor:

    COLUMNS = ["experiment", "source_file", "trace", "policy", "threshold"] + [m.name for m in METRICS]

    def __init__(self, paths: Paths, parser: LogParser | None = None):
        self.paths = paths
        self.parser = parser or LogParser()

    def _log_files(self) -> list[Path]:
        if not self.paths.raw_dir.exists():
            raise SystemExit(f"Raw log directory not found: {self.paths.raw_dir}")
        files = sorted(p for p in self.paths.raw_dir.rglob("*") if p.is_file())
        if not files:
            raise SystemExit(f"No files found in {self.paths.raw_dir}")
        return files

    def _write_filtered(self, rel: Path, lines: list[str]) -> None:
        out = self.paths.filtered_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def run(self) -> pd.DataFrame:
        rows = []
        for path in self._log_files():
            rel = path.relative_to(self.paths.raw_dir)
            row, kept = self.parser.parse(path)
            row["source_file"] = str(rel)
            self._write_filtered(rel, kept)
            rows.append(row)
            print(f"Processed {rel} | IPC={row['ipc']} | policy={row['policy'] or '?'} "
                  f"| trace={row['trace'] or '?'} | thr={row['threshold']}")
        df = pd.DataFrame(rows)[self.COLUMNS]
        self.paths.csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.paths.csv_path, index=False)
        self._warn(df)
        print(f"\nMetrics CSV: {self.paths.csv_path}")
        return df

    @staticmethod
    def _warn(df: pd.DataFrame) -> None:
        for m in METRICS:
            n = int(df[m.name].isna().sum())
            if n:
                print(f"WARNING: '{m.name}' missing in {n} file(s); check its regex.")
        if (df["trace"].eq("") | df["policy"].eq("")).any():
            print("WARNING: some files have unknown trace/policy; "
                  "include trace1 and lru/rrip/irrip/dyn_irrip in file names.")
        if not df["policy"].eq("RRIP").any():
            print("WARNING: no RRIP rows found; run the rrip experiments first.")


# ================================================================ data / tables
class ResultsTable:

    BASE_KEYS = ["ipc"] + MPKI_KEYS

    def __init__(self, df: pd.DataFrame):
        self.df = self._prepare(df)

    @classmethod
    def from_csv(cls, path: Path) -> "ResultsTable":
        return cls(pd.read_csv(path))

    @classmethod
    def _prepare(cls, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["trace"] = df["trace"].astype(str)
        for c in cls.BASE_KEYS + ["threshold"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.drop_duplicates(["policy", "trace", "threshold"], keep="last")

        def ipc_of(policy: str) -> pd.Series:
            sub = df[df.policy == policy].drop_duplicates("trace", keep="last")
            return sub.set_index("trace")["ipc"]

        lru = ipc_of("LRU")
        if lru.empty:
            raise SystemExit("No LRU rows found; cannot compute speedup.")
        rrip = ipc_of("RRIP")                       # may be empty -> speedup_rrip is NaN
        df["speedup"] = df["ipc"] / df["trace"].map(lru)
        df["speedup_rrip"] = df["ipc"] / df["trace"].map(rrip) if not rrip.empty else np.nan
        df["config"] = df.apply(cls._config_name, axis=1)
        return df

    @staticmethod
    def _config_name(r) -> str:
        if r.policy == "Dynamic I-RRIP":
            return f"Dyn thr={r.threshold:g}"
        return r.policy

    @property
    def traces(self) -> list[str]:
        return sorted(self.df.trace.unique())

    @property
    def thresholds(self) -> list[float]:
        return sorted(self.df[self.df.policy == "Dynamic I-RRIP"].threshold.dropna().unique())

    def dyn_configs(self) -> list[str]:
        return [f"Dyn thr={t:g}" for t in self.thresholds]

    def value(self, config: str, trace: str, key: str) -> float:
        s = self.df[(self.df.config == config) & (self.df.trace == trace)][key]
        return float(s.iloc[0]) if len(s) else np.nan

    # ---- report tables (AVG = arithmetic mean over traces)
    def report(self, configs: list[str]) -> pd.DataFrame:
        rows = []
        for c in configs:
            per_trace = []
            for t in self.traces:
                per_trace.append({"config": c, "trace": t,
                                  **{k: self.value(c, t, k) for k in PLOT_KEYS}})
            rows += per_trace
            avg = {}
            for k in PLOT_KEYS:
                vals = [r[k] for r in per_trace]
                avg[k] = np.nan if np.all(np.isnan(vals)) else np.nanmean(vals)
            rows.append({"config": c, "trace": "AVG", **avg})
        return pd.DataFrame(rows).rename(columns=LABELS)

    def task2a_configs(self) -> list[str]:
        return ["LRU", "RRIP", "I-RRIP"]

    def task2b_configs(self) -> list[str]:
        return ["LRU", "RRIP", "I-RRIP"] + self.dyn_configs()


# ==================================================================== plotting
class BasePlotter:
    PALETTE = plt.cm.tab10.colors

    def __init__(self, table: ResultsTable, out_dir: Path):
        self.t = table
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)

    def _save(self, fig, name: str) -> None:
        fig.tight_layout()
        path = self.out / name
        fig.savefig(path, dpi=180)
        plt.close(fig)
        print(f"Saved {path}")

    @staticmethod
    def _zoom(ax, values, frac: float = 0.35) -> None:
        v = np.asarray([x for x in values if not np.isnan(x)], dtype=float)
        if not len(v):
            return
        lo, hi = v.min(), v.max()
        pad = (hi - lo) * frac or abs(hi) * 0.01 or 0.01
        ax.set_ylim(lo - pad, hi + pad * 1.6)      # extra headroom for labels


class BarPlotter(BasePlotter):

    DYN_COLORS = plt.cm.Reds(np.linspace(0.35, 0.95, 8))

    def plot_all(self, configs: list[str], title_prefix: str, file_prefix: str) -> None:
        for key in PLOT_KEYS:
            if all(np.isnan(self.t.value(c, tr, key))
                   for c in configs for tr in self.t.traces):
                print(f"Skipping {key}: no data (is the RRIP baseline missing?)")
                continue
            self._plot_one(configs, key, title_prefix, file_prefix)

    def _color(self, cfg: str, dyn_idx: int):
        if cfg in FIXED_COLORS:
            return FIXED_COLORS[cfg]
        return self.DYN_COLORS[dyn_idx % len(self.DYN_COLORS)]

    def _plot_one(self, configs, key, title_prefix, file_prefix) -> None:
        traces = self.t.traces
        x = np.arange(len(traces))
        w = 0.8 / len(configs)
        dec = DECIMALS[key]
        rot = 90 if len(configs) > 4 else 0
        fig, ax = plt.subplots(figsize=(max(7, 2.4 * len(traces) + len(configs)), 5.5))
        all_vals = []
        dyn_idx = 0
        for i, c in enumerate(configs):
            vals = [self.t.value(c, tr, key) for tr in traces]
            all_vals += vals
            color = self._color(c, dyn_idx)
            if c not in FIXED_COLORS:
                dyn_idx += 1
            bars = ax.bar(x + (i - (len(configs) - 1) / 2) * w, vals, w,
                          label=c, color=color, edgecolor="black", linewidth=.4)
            for b, v in zip(bars, vals):
                if np.isnan(v):
                    continue
                ax.annotate(f"{v:.{dec}f}", (b.get_x() + b.get_width() / 2, v),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, rotation=rot)
        self._zoom(ax, all_vals)
        if key in ("speedup", "speedup_rrip"):
            ax.axhline(1.0, color="k", lw=.8, ls="--")
        ax.set_xticks(x)
        ax.set_xticklabels(traces)
        ax.set_ylabel(f"{LABELS[key]}")
        better = "higher is better" if key in HIGHER_IS_BETTER else "lower is better"
        ax.set_title(f"{title_prefix}: {LABELS[key]}  ({better})")
        ax.grid(axis="y", alpha=.3)
        ax.legend(fontsize=8, ncol=min(len(configs), 4))
        self._save(fig, f"{file_prefix}_{key}.png")


class SensitivityPlotter(BasePlotter):

    BASELINES = (("I-RRIP", "--"), ("RRIP", "-."), ("LRU", ":"))

    def plot_all(self) -> None:
        thr = self.t.thresholds
        if not thr:
            print("No Dynamic I-RRIP rows; skipping sensitivity plots.")
            return
        if len(thr) < 5:
            print(f"WARNING: only {len(thr)} threshold(s) found; the assignment asks for 5.")
        for key in PLOT_KEYS:
            self._plot_one(thr, key)

    def _plot_one(self, thr, key) -> None:
        dec = DECIMALS[key]
        tcol = dict(zip(self.t.traces, self.PALETTE))
        fig, ax = plt.subplots(figsize=(9, 5.8))
        all_vals = []
        for tr in self.t.traces:
            ys = [self.t.value(f"Dyn thr={v:g}", tr, key) for v in thr]
            all_vals += ys
            ax.plot(thr, ys, "o-", color=tcol[tr], label=f"{tr} (Dynamic)")
            for xv, yv in zip(thr, ys):
                if not np.isnan(yv):
                    ax.annotate(f"{yv:.{dec}f}", (xv, yv), xytext=(0, 6),
                                textcoords="offset points", ha="center",
                                fontsize=7, color=tcol[tr])
            for cfg, ls in self.BASELINES:
                b = self.t.value(cfg, tr, key)
                if not np.isnan(b):
                    all_vals.append(b)
                    ax.axhline(b, color=tcol[tr], ls=ls, lw=1.1, alpha=.7)
        self._zoom(ax, all_vals, frac=0.25)
        ax.set_xticks(thr)
        ax.set_xlabel("Threshold")
        ax.set_ylabel(f"{LABELS[key]}")
        ax.set_title(f"Dynamic I-RRIP threshold sensitivity: {LABELS[key]}")
        ax.grid(alpha=.3)
        h, l = ax.get_legend_handles_labels()
        for cfg, ls in self.BASELINES:
            h.append(plt.Line2D([], [], color="k", ls=ls))
            l.append(f"{cfg} baseline")
        ax.legend(h, l, fontsize=8)
        self._save(fig, f"sensitivity_{key}.png")


# ======================================================================== main
def build_reports(paths: Paths) -> None:
    table = ResultsTable.from_csv(paths.csv_path)
    dir_a = paths.fig_dir / TASK_2A
    dir_b = paths.fig_dir / TASK_2B
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)

    # ---- Task 2a: LRU vs RRIP vs I-RRIP
    rep = table.report(table.task2a_configs())
    rep.round(5).to_csv(dir_a / "table.csv", index=False)
    print(f"\n=== {TASK_2A.upper()} table ===")
    print(rep.round(4).to_string(index=False))
    BarPlotter(table, dir_a).plot_all(table.task2a_configs(),
                                      "Task 2a (LRU / RRIP / I-RRIP)", "task2a")

    # ---- Task 2b: LRU / RRIP / I-RRIP / Dynamic I-RRIP (+ threshold sensitivity)
    rep = table.report(table.task2b_configs())
    rep.round(5).to_csv(dir_b / "table.csv", index=False)
    print(f"\n=== {TASK_2B.upper()} table ===")
    print(rep.round(4).to_string(index=False))
    BarPlotter(table, dir_b).plot_all(table.task2b_configs(),
                                      "Task 2b (LRU / RRIP / I-RRIP / Dynamic I-RRIP)",
                                      "task2b_comparison")
    SensitivityPlotter(table, dir_b).plot_all()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["extract", "plot", "all"], nargs="?", default="all")
    ap.add_argument("--raw-dir", type=Path, help="override results/raw")
    args = ap.parse_args()
    paths = Paths.default(args.raw_dir)

    if args.command in ("extract", "all"):
        MetricsExtractor(paths).run()
    if args.command in ("plot", "all"):
        build_reports(paths)

if __name__ == "__main__":
    main()