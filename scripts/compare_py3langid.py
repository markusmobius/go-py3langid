#!/usr/bin/env python3
"""Run pinned py3langid tests and compare the shared corpus with the Go port."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "testdata" / "py3langid_cases.json"
SNAPSHOT = ROOT / "testdata" / "py3langid_reference.json"


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def load_reference(checkout, corpus):
    revision = run(["git", "rev-parse", "HEAD"], cwd=checkout,
                   capture_output=True, text=True).stdout.strip()
    if revision != corpus["commit"]:
        raise ValueError(f"expected upstream commit {corpus['commit']}, got {revision}")
    run(["git", "diff", "--exit-code", "HEAD", "--", "py3langid", "tests"],
        cwd=checkout, capture_output=True)
    for path, expected in corpus["upstream_tests"].items():
        tree = ast.parse((checkout / path).read_text(encoding="utf-8"))
        actual = {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
        if actual != set(expected):
            raise ValueError(f"upstream test inventory changed in {path}: added={actual-set(expected)}, removed={set(expected)-actual}; port the changed tests before updating the lock")
    sys.path.insert(0, str(checkout))
    import numpy as np
    import py3langid
    import py3langid.langid as library

    if py3langid.__version__ != corpus["version"] or np.__version__ != corpus["numpy"]:
        raise ValueError("reference dependency versions differ from requirements-parity.txt")
    model = library.MODEL_DIR / library.MODEL_FILE
    if hashlib.sha256(model.read_bytes()).hexdigest() != corpus["python_model_sha256"]:
        raise ValueError("Python model fingerprint differs from the pinned model")
    go_model = ROOT / "model" / "py3langid.lidg"
    if hashlib.sha256(go_model.read_bytes()).hexdigest() != corpus["go_model_sha256"]:
        raise ValueError("Go model fingerprint differs from the converted pinned model")
    return library


def sample_input(sample):
    if "hex" in sample:
        return bytes.fromhex(sample["hex"])
    text = sample["text"]
    if sample.get("as_bytes"):
        data = text.encode("utf-8")
        trim = sample.get("trim_bytes", 0)
        return data[:-trim] if trim else data
    return text


def prediction(result):
    return {"language": result[0], "score": float(result[1])}


def reference_results(library, corpus):
    raw = library.LanguageIdentifier.from_model_file(library.MODEL_FILE)
    normalized = library.LanguageIdentifier(
        raw.nb_ptc, raw.nb_pc, raw.nb_classes, raw.tk_nextmove, raw.tk_output,
        tk_row=raw.tk_row, norm_probs=True)
    result = {
        "commit": corpus["commit"], "version": corpus["version"],
        "numpy": corpus["numpy"], "python": platform.python_version(),
        "go_model_sha256": corpus["go_model_sha256"],
        "python_model_sha256": corpus["python_model_sha256"],
        "classes": raw.nb_classes, "num_features": len(raw.nb_ptc),
        "num_states": len(raw.tk_row), "samples": [],
    }
    for sample in corpus["cases"]:
        text = sample_input(sample)
        raw.set_languages(sample.get("languages"))
        normalized.set_languages(sample.get("languages"))
        normalized.min_confidence = sample.get("min_confidence")
        raw_result = prediction(raw.classify(text))
        normalized_result = prediction(normalized.classify(text))
        if "language" in sample and raw_result["language"] != sample["language"]:
            raise AssertionError(f"upstream label assertion failed: {sample['name']}: {raw_result}")
        if "normalized_language" in sample and normalized_result["language"] != sample["normalized_language"]:
            raise AssertionError(f"upstream abstention assertion failed: {sample['name']}: {normalized_result}")
        result["samples"].append({
            "name": sample["name"], "raw": raw_result, "normalized": normalized_result,
            "raw_scores": dict(raw.rank(text)), "normalized_scores": dict(normalized.rank(text)),
            "encoded": base64.b64encode(library.LanguageIdentifier._encode(text)).decode("ascii"),
        })
    return result


def write_results(path, result):
    with path.open("w", encoding="utf-8", newline="\n") as target:
        json.dump(result, target, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")


def run_upstream_tests(checkout, corpus):
    environment = os.environ.copy()
    python_dir = Path(sys.executable).parent
    environment["PATH"] = os.pathsep.join((str(python_dir), str(python_dir / "Scripts"), environment.get("PATH", "")))
    environment["PYTHONPATH"] = os.pathsep.join((str(checkout), environment.get("PYTHONPATH", "")))
    print("Running unmodified upstream inference and HTTP tests", flush=True)
    run([sys.executable, "-m", "pytest", *(str(checkout / path) for path in corpus["upstream_tests"]), "-q"],
        cwd=checkout, env=environment)


def run_go_parity(go_command, reference, corpus):
    environment = os.environ.copy()
    environment["LANGID_PY3_REFERENCE"] = str(reference)
    completed = subprocess.run(
        [go_command, "test", "./...", "-run", "^(TestPy3LangIDParity|TestUpstreamCLI|TestUpstreamService)$", "-count=1", "-json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode:
        print("".join(event.get("Output", "") for event in events), end="")
        print(completed.stderr, file=sys.stderr, end="")
        completed.check_returncode()
    passed = {event.get("Test") for event in events if event["Action"] == "pass"}
    required = {"TestPy3LangIDParity", "TestUpstreamCLI", "TestUpstreamService"}
    required.update("TestPy3LangIDParity/" + case["name"] for case in corpus["cases"])
    if missing := required - passed:
        raise RuntimeError(f"Go parity cases did not run successfully: {sorted(missing)}")
    print(f"Go matched all {len(corpus['cases'])} live Python reference cases; upstream-derived CLI and HTTP tests passed.", flush=True)


def check_upstream(corpus):
    output = run(["git", "ls-remote", corpus["repository"], "refs/heads/master"],
                 capture_output=True, text=True).stdout.strip()
    if not output:
        raise RuntimeError("could not resolve upstream master")
    revision = output.split()[0]
    print(f"Pinned upstream: {corpus['commit']}\nCurrent upstream: {revision}", flush=True)
    if revision != corpus["commit"]:
        print(f"Upstream changed: {corpus['repository']}/compare/{corpus['commit']}...{revision}\nFollow docs/upstream-sync.md before advancing the pin.", file=sys.stderr)
        return 1
    print("Pinned port is at the current upstream head.")
    return 0


def benchmark(library, corpus, go_command, samples):
    identifier = library.LanguageIdentifier.from_model_file(library.MODEL_FILE)
    timers = {}
    counts = {}
    measurements = {}
    for case in corpus["benchmarks"]:
        unit = corpus["benchmark_text"]
        text = (unit * ((case["bytes"] + len(unit) - 1) // len(unit)))[:case["bytes"]]
        timer = timeit.Timer("identifier.classify(text)", globals={"identifier": identifier, "text": text})
        counts[case["name"]], _ = timer.autorange()
        timers[case["name"]] = timer
        measurements[case["name"]] = {"go_ns": [], "python_ns": [], "go_allocs": []}
    def measure_go(record):
        output = run([go_command, "test", ".", "-run", "^$", "-bench", "^BenchmarkPy3LangID$",
                      "-benchmem", "-benchtime=200ms", "-count=1", "-cpu=1"],
                     cwd=ROOT, capture_output=True, text=True).stdout
        seen = set()
        for line in output.splitlines():
            match = re.match(r"BenchmarkPy3LangID/([\w]+)(?:-\d+)?\s", line)
            if not match:
                continue
            name = match.group(1)
            fields = line.split()
            if record:
                measurements[name]["go_ns"].append(float(fields[fields.index("ns/op") - 1]))
                measurements[name]["go_allocs"].append(int(fields[fields.index("allocs/op") - 1]))
            seen.add(name)
        if seen != set(measurements):
            raise RuntimeError(f"missing Go benchmark results:\n{output}")

    def measure_python(record):
        for name, timer in timers.items():
            elapsed = timer.timeit(counts[name])
            if record:
                measurements[name]["python_ns"].append(elapsed * 1e9 / counts[name])

    measure_go(False)
    measure_python(False)
    for sample in range(samples):
        engines = (measure_go, measure_python) if sample % 2 == 0 else (measure_python, measure_go)
        for measure in engines:
            measure(True)
    print("Warm single-thread classification; model load, process startup, and I/O excluded.")
    print("One warm-up round discarded; engine order alternates; five samples by default.")
    print("Workload                  Go us/op   Python us/op   Python/Go   Go allocs/op")
    for name, values in measurements.items():
        go_ns = statistics.median(values["go_ns"])
        python_ns = statistics.median(values["python_ns"])
        print(f"{name:24} {go_ns / 1000:9.2f} {python_ns / 1000:14.2f} {python_ns / go_ns:11.2f} {max(values['go_allocs']):14}")
    print(json.dumps({"samples": samples, "measurements": measurements}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="checkout of the pinned py3langid commit")
    parser.add_argument("--check-upstream", action="store_true", help="fail if upstream master differs from the pinned revision")
    parser.add_argument("--go", default="go", help="Go executable")
    parser.add_argument("--update-reference", action="store_true", help="regenerate the offline Python snapshot and exit")
    parser.add_argument("--benchmark", action="store_true", help="also compare warm sequential classification timings")
    parser.add_argument("--samples", type=int, default=5, help="interleaved benchmark samples per engine (at least 3)")
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples must be at least 3")
    for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ[variable] = "1"
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    if args.check_upstream:
        return check_upstream(corpus)
    if args.reference is None:
        parser.error("--reference is required unless --check-upstream is used")
    checkout = args.reference.resolve()
    library = load_reference(checkout, corpus)
    print(f"Reference: py3langid {corpus['version']} @ {corpus['commit']}; NumPy {corpus['numpy']}; Python {platform.python_version()}", flush=True)
    print(f"Platform: {platform.platform()}; CPU: {platform.processor()}", flush=True)
    results = reference_results(library, corpus)
    if args.update_reference:
        write_results(SNAPSHOT, results)
        print(f"Wrote {len(results['samples'])} reference cases to {SNAPSHOT}")
        return
    print(run([args.go, "version"], capture_output=True, text=True).stdout.strip(), flush=True)
    run_upstream_tests(checkout, corpus)
    with tempfile.TemporaryDirectory(prefix="go-py3langid-parity-") as temporary:
        reference = Path(temporary) / "reference.json"
        write_results(reference, results)
        run_go_parity(args.go, reference, corpus)
    if args.benchmark:
        benchmark(library, corpus, args.go, args.samples)


if __name__ == "__main__":
    sys.exit(main())