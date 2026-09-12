# Recorded Comparisons

The [2026-09-12 comparison](2026-09-12/report.md) is one fresh run of CLD3, go-py3langid, Whatlanggo, and Lingua, built from the unified [worker module](../workers/go.mod). It replaces the older snapshots and separate two-/four-engine runs, which have been removed.

All four engines classify the same **1,000 sentences across 20 selected FLORES-200 languages**. Every corpus language is supported by every engine, so coverage filtering excludes none. Each detector retains its full candidate language set. Supported-language counts in the comparison table describe model coverage, not the number of evaluated languages.

Eight measured passes per engine follow one discarded full warm-up, with rotating engine order. Startup, warm-up wall time, and each measured pass are recorded separately; startup and warm-up never contribute to steady-state throughput or RTT statistics. The recorded files are:

- [report.md](2026-09-12/report.md): comparison table with Pure Go status and separate startup/warm-up/measured timing, the individual pass breakdown, per-language and per-length results, and interpretation.
- [summary.json](2026-09-12/summary.json): binary/build/source hashes, pinned dependencies, environment, language coverage, all six pairwise outcomes, startup time, and separate warm-up and measured pass times.
- [predictions.csv](2026-09-12/predictions.csv): 1,000 per-case rows with all four predictions.
- [timings.csv](2026-09-12/timings.csv): 32,000 measured request timings, 8,000 per engine.

Prediction CSVs contain FLORES-200 sentences and retain the corpus's [CC-BY-SA 4.0 terms and attribution](../suite/README.md).

## Reproduce

From the benchmark directory, build and run the comparison:

```sh
bash run.sh
```

Normal output goes directly to ignored `results/local/latest/`. To publish another observation, choose a new directory:

```sh
bash run.sh --output results/NAME
```

After building, `python3 compare.py run` reruns without rebuilding. See the [benchmark README](../README.md) for prerequisites, configurations, counting rules, and the measured table.

These files are observations, not accuracy or performance gates. Small timing differences should not be generalized. Startup is launch-to-ready rather than a cold-cache measurement; Lingua loads models lazily during warm-up. The corpus covers only 20 languages, and training-set overlap has not been audited.