#!/usr/bin/env python3
"""Compare four persistent language detectors on a shared corpus inside Linux/WSL."""

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
from itertools import combinations
import json
import math
import os
from pathlib import Path
import platform
import random
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import uuid

from prepare import BIN, DEFAULT_SEED, LENGTH_GROUPS, WORKERS, sha256_file


HEADER = struct.Struct("!II")
MAX_MESSAGE = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parent


def receive_exact(connection, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError("worker closed the connection before completing a message")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_message(connection):
    size, chunk_size = HEADER.unpack(receive_exact(connection, HEADER.size))
    if not 0 < size <= MAX_MESSAGE or chunk_size == 0:
        raise ValueError("invalid worker message header")
    return receive_exact(connection, size)


class Worker:
    def __init__(self, executable, timeout=30.0, gomaxprocs=None):
        self.executable = Path(executable).resolve()
        self.timeout = timeout
        self.gomaxprocs = gomaxprocs
        self.process = None
        self.connection = None
        self.log = None
        self.startup_seconds = None
        self.sequence = 0

    def __enter__(self):
        self.log = tempfile.TemporaryFile()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(0.1)
                ready_guid = uuid.uuid4().hex
                environment = os.environ.copy()
                if self.gomaxprocs is not None:
                    environment["GOMAXPROCS"] = str(self.gomaxprocs)
                started = time.perf_counter()
                self.process = subprocess.Popen(
                    [str(self.executable), str(listener.getsockname()[1]),
                     str(os.getpid()), ready_guid],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=self.log,
                    stderr=subprocess.STDOUT,
                )
                deadline = started + self.timeout
                while self.connection is None:
                    if self.process.poll() is not None:
                        self.log.seek(0)
                        detail = self.log.read().decode("utf-8", errors="replace").strip()
                        raise RuntimeError(
                            f"{self.executable.name} exited before ready "
                            f"(exit {self.process.returncode}): {detail}"
                        )
                    if time.perf_counter() >= deadline:
                        raise TimeoutError(f"{self.executable.name} did not connect in time")
                    try:
                        self.connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                self.connection.settimeout(max(0.001, deadline - time.perf_counter()))
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                ready = json.loads(receive_message(self.connection))
                if not isinstance(ready, dict) or ready.get("MType") != 0 or ready.get("GUID") != ready_guid:
                    raise ValueError(f"{self.executable.name} sent an invalid ready message")
                self.startup_seconds = time.perf_counter() - started
                self.connection.settimeout(self.timeout)
            return self
        except BaseException:
            self.close()
            raise

    def classify(self, text):
        self.sequence += 1
        guid = str(self.sequence)
        command = json.dumps({"Text": text}, ensure_ascii=False, separators=(",", ":"))
        message = json.dumps(
            {"GUID": guid, "Command": command},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        request = HEADER.pack(len(message), 1024 * 1024) + message
        started = time.perf_counter_ns()
        self.connection.sendall(request)
        response_bytes = receive_message(self.connection)
        elapsed_seconds = (time.perf_counter_ns() - started) / 1e9
        response = json.loads(response_bytes)
        if not isinstance(response, dict) or response.get("MType") != 1 or response.get("GUID") != guid:
            raise ValueError(f"{self.executable.name} sent a mismatched response")
        if not isinstance(response.get("Content"), str):
            raise ValueError(f"{self.executable.name} sent invalid response content")
        result = json.loads(response["Content"])
        if not isinstance(result, dict) or set(result) != {"Iso639", "Probability", "IsReliable"}:
            raise ValueError(f"{self.executable.name} sent unexpected output fields")
        probability = result["Probability"]
        if not isinstance(result["Iso639"], str) or type(result["IsReliable"]) is not bool:
            raise ValueError(f"{self.executable.name} sent invalid output types")
        if (type(probability) not in (int, float) or not math.isfinite(probability)
                or not 0 <= probability <= 1):
            raise ValueError(f"{self.executable.name} sent an invalid probability")
        return result, elapsed_seconds

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def load_suite(path):
    cases = []
    identifiers = set()
    texts = set()
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            case = json.loads(line)
            if not isinstance(case, dict) or any(
                not isinstance(case.get(field), str) or not case[field].strip()
                for field in ("id", "language", "text")
            ):
                raise ValueError(f"invalid suite fields on line {line_number}")
            if case.get("length_group") not in LENGTH_GROUPS:
                raise ValueError(f"invalid length group on line {line_number}")
            minimum, maximum = LENGTH_GROUPS[case["length_group"]]
            if not minimum <= len(case["text"].encode("utf-8")) <= maximum:
                raise ValueError(f"incorrect length group on line {line_number}")
            text_key = (case["language"], case["text"])
            if case["id"] in identifiers or text_key in texts:
                raise ValueError(f"duplicate case on line {line_number}")
            identifiers.add(case["id"])
            texts.add(text_key)
            cases.append(case)
    if not cases:
        raise ValueError("the test suite is empty")
    manifest_path = path.parent / "manifest.json"
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["suite_sha256"] != sha256_file(path):
            raise ValueError("suite SHA256 does not match its manifest")
        if manifest["sample_count"] != len(cases):
            raise ValueError("suite sample count does not match its manifest")
        counts = Counter((case["language"], case["length_group"]) for case in cases)
        expected = Counter({
            (language, group): manifest["per_language_per_group"]
            for language in manifest["languages"] for group in LENGTH_GROUPS
        })
        if counts != expected:
            raise ValueError("suite is not balanced as specified by its manifest")
    return cases, manifest


def summarize(records):
    if not records:
        raise ValueError("cannot summarize an empty set of predictions")
    correct = sum(record["result"]["Iso639"] == record["case"]["language"] for record in records)
    reliable = [record for record in records if record["result"]["IsReliable"]]
    reliable_correct = sum(record["result"]["Iso639"] == record["case"]["language"] for record in reliable)
    language_counts = Counter(record["case"]["language"] for record in records)
    language_correct = Counter(
        record["case"]["language"] for record in records
        if record["result"]["Iso639"] == record["case"]["language"]
    )
    latencies_ms = sorted(seconds * 1000 for record in records for seconds in record["seconds"])
    if not latencies_ms:
        raise ValueError("cannot summarize predictions without measurements")
    return {
        "samples": len(records),
        "correct": correct,
        "accuracy_pct": 100 * correct / len(records),
        "macro_accuracy_pct": statistics.fmean(
            100 * language_correct[language] / count for language, count in language_counts.items()
        ),
        "reliable_samples": len(reliable),
        "reliable_coverage_pct": 100 * len(reliable) / len(records),
        "reliable_accuracy_pct": 100 * reliable_correct / len(reliable) if reliable else None,
        "measurements": len(latencies_ms),
        "mean_roundtrip_ms": statistics.fmean(latencies_ms),
        "median_roundtrip_ms": statistics.median(latencies_ms),
        "p95_roundtrip_ms": latencies_ms[math.ceil(0.95 * len(latencies_ms)) - 1],
        "median_utf8_bytes": statistics.median(len(record["case"]["text"].encode("utf-8")) for record in records),
    }


def supported_languages(executable, timeout):
    try:
        result = subprocess.run(
            [str(executable.resolve()), "--languages"],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        languages = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError(f"{executable.name} could not report language coverage; rebuild the workers: {error}") from error
    if (not isinstance(languages, list) or not languages
            or any(not isinstance(language, str) or not language or language != language.strip() for language in languages)
            or len(set(languages)) != len(languages)):
        raise ValueError(f"{executable.name} reported invalid language coverage")
    return sorted(languages)


def supported_language_count(languages):
    return len({language.split("-", 1)[0] for language in languages} - {"und", "zxx"})


def select_shared_cases(cases, coverage):
    if not coverage:
        raise ValueError("language coverage is required for the shared comparison")
    languages = set.intersection(*(set(supported) for supported in coverage.values()))
    selected = [case for case in cases if case["language"] in languages]
    if not selected:
        raise ValueError("the corpus has no languages shared by every engine")
    return selected


def environment_info(gomaxprocs):
    information = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "go_environment": {name: os.environ.get(name) for name in ("GOGC", "GOMEMLIMIT", "GODEBUG")},
    }
    information["go_environment"]["GOMAXPROCS"] = str(gomaxprocs)
    cpu_information = Path("/proc/cpuinfo")
    if cpu_information.is_file():
        for line in cpu_information.read_text().splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() == "model name":
                information["cpu_model"] = value.strip()
                break
    return information


def format_percentage(value):
    return "n/a" if value is None else f"{value:.2f}%"


def format_pure_go(value):
    if value is None:
        return "Unknown"
    return "Yes" if value else "No (CGO)"


def report_path(path):
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def write_reports(directory, summary, records, measurements):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    fields = ["case_id", "expected", "length_group", "utf8_bytes"]
    for engine in records:
        fields.extend(f"{engine}_{field}" for field in ("language", "probability", "reliable", "correct", "median_ms"))
    fields.append("text")
    with (directory / "predictions.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for index, record in enumerate(next(iter(records.values()))):
            case = record["case"]
            row = {
                "case_id": case["id"], "expected": case["language"],
                "length_group": case["length_group"], "utf8_bytes": len(case["text"].encode("utf-8")),
                "text": case["text"],
            }
            for engine, engine_records in records.items():
                entry = engine_records[index]
                result = entry["result"]
                row.update({
                    f"{engine}_language": result["Iso639"],
                    f"{engine}_probability": result["Probability"],
                    f"{engine}_reliable": result["IsReliable"],
                    f"{engine}_correct": result["Iso639"] == case["language"],
                    f"{engine}_median_ms": statistics.median(entry["seconds"]) * 1000,
                })
            writer.writerow(row)
    with (directory / "timings.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("engine", "round", "position", "case_id", "roundtrip_ms"))
        writer.writerows(measurements)
    engine_names = list(summary["engines"])
    lines = [
        f"# {summary['title']}", "", f"Run: {summary['run_utc']}", "",
        f"{summary['suite']['samples']} cases; {summary['parameters']['rounds']} measured passes per engine; "
        f"{summary['parameters']['warmup_rounds']} full warm-up pass(es) discarded.", "",
        f"Selection: {summary['suite']['selection']}. Source corpus: {summary['suite']['source_samples']} cases.",
        "Included languages: " + ", ".join(summary["suite"]["languages"]) + ".",
        "Excluded corpus languages: " + (", ".join(summary["suite"]["excluded_languages"]) or "none") + ".", "",
        "| Engine | Pure Go | Supported languages | Accuracy | Startup (s) | Warm-up total (s) | Median pass wall (s) | Texts/s | Median RTT (ms) | p95 RTT (ms) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for engine, stats in summary["engines"].items():
        lines.append(f"| {engine} | {format_pure_go(stats['pure_go'])} | {stats['supported_language_count']} | "
                     f"{format_percentage(stats['accuracy_pct'])} | {stats['startup_seconds']:.4f} | "
                     f"{stats['warmup_wall_seconds']:.4f} | "
                     f"{stats['median_batch_wall_seconds']:.4f} | {stats['texts_per_second']:.1f} | "
                     f"{stats['median_roundtrip_ms']:.4f} | {stats['p95_roundtrip_ms']:.4f} |")
    lines.extend(["", "## Pass Wall Times", "",
                  "Startup is launch-to-ready. Warm-up passes are timed separately and discarded from steady-state statistics. Each pass processes the full selected corpus.", "",
                  "| Phase | Pass | " + " | ".join(f"{engine} (s)" for engine in engine_names) + " |",
                  "| --- | ---: |" + " ---: |" * len(engine_names)])
    for phase, count_key, times_key in (
        ("Warm-up (discarded)", "warmup_rounds", "warmup_batch_wall_seconds"),
        ("Measured", "rounds", "batch_wall_seconds"),
    ):
        for pass_index in range(summary["parameters"][count_key]):
            row = [phase, str(pass_index + 1), *(f"{summary['engines'][engine][times_key][pass_index]:.4f}"
                                               for engine in engine_names)]
            lines.append("| " + " | ".join(row) + " |")
    lines.extend(["", "## Confidence", "",
                  "| Engine | Macro accuracy | Reliable coverage | Accuracy when reliable |",
                  "| --- | ---: | ---: | ---: |"])
    for engine, stats in summary["engines"].items():
        lines.append(f"| {engine} | {format_percentage(stats['macro_accuracy_pct'])} | "
                     f"{format_percentage(stats['reliable_coverage_pct'])} | "
                     f"{format_percentage(stats['reliable_accuracy_pct'])} |")
    first_engine = summary["engines"][engine_names[0]]
    columns = ["Group", "Cases", *(f"{engine} accuracy" for engine in engine_names),
               *(f"{engine} median RTT (ms)" for engine in engine_names)]
    lines.extend(["", "## Short And Medium Text", "", "| " + " | ".join(columns) + " |",
                  "| --- |" + " ---: |" * (len(columns) - 1)])
    for group in LENGTH_GROUPS:
        stats = [summary["engines"][engine]["by_length"][group] for engine in engine_names]
        row = [group, str(stats[0]["samples"]), *(format_percentage(entry["accuracy_pct"]) for entry in stats),
               *(f"{entry['median_roundtrip_ms']:.4f}" for entry in stats)]
        lines.append("| " + " | ".join(row) + " |")
    lines.extend(["", "## Per-Language Accuracy", "",
                  "| Language | Cases | " + " | ".join(engine_names) + " |",
                  "| --- | ---: |" + " ---: |" * len(engine_names)])
    for language, stats in first_engine["by_language"].items():
        row = [language, str(stats["samples"]), *(format_percentage(
            summary["engines"][engine]["by_language"][language]["accuracy_pct"],
        ) for engine in engine_names)]
        lines.append("| " + " | ".join(row) + " |")
    lines.extend([
        "", "## Interpretation", "",
        "- All worker processes stay alive for the entire run. Full warm-up passes are excluded, and engine order rotates each measured pass.",
        "- Warm-up total is the sum of separately timed discarded full passes, not startup time. Warm-up and startup are excluded from measured pass times, throughput, and RTT statistics.",
        "- Pass wall time includes the Python loop, JSON handling, TCP transport, and worker execution; texts/s uses its median. This is not isolated model CPU time.",
        "- RTT runs from sending a pre-encoded request to receiving the complete response frame; Python response decoding is outside the RTT timer. p95 uses nearest rank.",
        "- Startup is one observed launch-to-ready measurement, not a cold-cache benchmark. Lingua loads models lazily during warm-up. Startup is excluded from steady-state throughput.",
        "- Accuracy counts each unique case once, including unreliable predictions. Every detector retains its full language set. No label aliases are applied by the harness. Changed predictions across measured passes fail the run.",
        "- Supported-language counts count script variants once and exclude und/zxx non-language labels. Raw output-label lists and counts remain in summary.json; evaluation still compares exact labels.",
        "- Pure Go reflects CGO_ENABLED in the matching build metadata: CLD3 uses native C++ through CGO; the other bundled adapters disable CGO. Custom binaries without that metadata are marked Unknown.",
        "- The probability scales and reliability rules differ between engines. Reliable coverage and conditional accuracy are not calibration-equivalent metrics.",
        "- This is a small, balanced FLORES-200 sentence subset, not a universal accuracy or production-throughput claim. Training-set overlap has not been audited.",
        "", f"Source corpus SHA256: `{summary['suite']['sha256']}`", "",
        "See summary.json for environment, binary hashes, parameters, paired outcomes, and individual pass times; predictions.csv for every prediction; timings.csv for every measured request.",
    ])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def binary_metadata(executable):
    executable = executable.resolve()
    metadata = {"executable": report_path(executable), "sha256": sha256_file(executable), "pure_go": None}
    build_manifest = executable.with_name(executable.name + ".build.json")
    if build_manifest.is_file():
        build = json.loads(build_manifest.read_text(encoding="utf-8"))
        if metadata["sha256"] != build["sha256"]:
            raise ValueError(f"{executable.name} binary does not match its build metadata; rebuild it")
        metadata["build"] = build
        metadata["pure_go"] = {"0": True, "1": False}.get(build.get("environment", {}).get("CGO_ENABLED"))
    elif executable in {BIN / name for name, _ in WORKERS.values()}:
        raise ValueError(f"{executable.name} has no build metadata; run bash benchmarks/language-detection/run.sh --build-only")
    return metadata


def run_benchmark(args):
    cases, manifest = load_suite(args.suite)
    source_samples = len(cases)
    source_languages = {case["language"] for case in cases}
    engines = {engine: getattr(args, engine).resolve() for engine in WORKERS}
    if len(set(engines.values())) != len(engines):
        raise ValueError("the engine paths must be different")
    engine_metadata = {engine: binary_metadata(executable) for engine, executable in engines.items()}
    coverage = {engine: supported_languages(executable, args.timeout) for engine, executable in engines.items()}
    cases = select_shared_cases(cases, coverage)
    for engine, languages in coverage.items():
        engine_metadata[engine].update({
            "supported_languages": languages,
            "supported_language_count": supported_language_count(languages),
            "supported_label_count": len(languages),
        })
    if set(case["length_group"] for case in cases) != set(LENGTH_GROUPS):
        raise ValueError("the benchmark requires both short and medium cases")
    languages = sorted({case["language"] for case in cases})
    title = "Language Detector Comparison"
    print(f"\n{title}: {len(cases)} cases across {len(languages)} languages", flush=True)
    records = {engine: [{"case": case, "result": None, "seconds": []} for case in cases] for engine in engines}
    measurements = []
    batch_times = {engine: [] for engine in engines}
    warmup_times = {engine: [] for engine in engines}
    summary = {
        "title": title,
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment_info(args.gomaxprocs),
        "harness_sha256": sha256_file(Path(__file__)),
        "preparation_sha256": sha256_file(ROOT / "prepare.py"),
        "suite": {"path": report_path(args.suite), "sha256": sha256_file(args.suite),
                  "samples": len(cases), "source_samples": source_samples, "manifest": manifest,
                  "languages": languages, "excluded_languages": sorted(source_languages - set(languages)),
                  "selection": "corpus languages supported by every engine"},
        "parameters": {"rounds": args.rounds, "warmup_rounds": args.warmup_rounds,
                       "seed": args.seed, "timeout_seconds": args.timeout, "gomaxprocs": args.gomaxprocs,
                       "candidate_languages": "all supported languages for every engine",
                       "engine_order": "rotate starting engine each measured pass",
                       "transport": "TCP IPv4 loopback, one outstanding request per worker"},
        "passes": [],
        "engines": engine_metadata,
    }
    started = time.perf_counter()
    with ExitStack() as stack:
        workers = {}
        for engine, executable in engines.items():
            worker = stack.enter_context(Worker(executable, args.timeout, args.gomaxprocs))
            workers[engine] = worker
            summary["engines"][engine].update({"startup_seconds": worker.startup_seconds, "pid": worker.process.pid})
            print(f"{engine}: ready in {worker.startup_seconds * 1000:.2f} ms", flush=True)
        for warmup in range(args.warmup_rounds):
            for engine in engines:
                warmup_started = time.perf_counter()
                for case in cases:
                    workers[engine].classify(case["text"])
                wall_seconds = time.perf_counter() - warmup_started
                warmup_times[engine].append(wall_seconds)
                print(f"Warm-up {warmup + 1}/{args.warmup_rounds} {engine}: {wall_seconds:.3f} s (discarded)", flush=True)
        for round_index in range(args.rounds):
            order = list(range(len(cases)))
            random.Random(args.seed + round_index).shuffle(order)
            engine_order = list(engines)
            offset = round_index % len(engine_order)
            engine_order = engine_order[offset:] + engine_order[:offset]
            summary["passes"].append({"round": round_index + 1, "engine_order": engine_order})
            for engine in engine_order:
                batch_started = time.perf_counter()
                for position, index in enumerate(order, start=1):
                    case = cases[index]
                    try:
                        result, elapsed = workers[engine].classify(case["text"])
                    except (OSError, ValueError, RuntimeError, KeyError) as error:
                        raise RuntimeError(f"{engine}, round {round_index + 1}, case {case['id']}: {error}") from error
                    record = records[engine][index]
                    if record["result"] is not None and record["result"] != result:
                        raise RuntimeError(f"{engine}: prediction changed across passes for {case['id']}")
                    record["result"] = result
                    record["seconds"].append(elapsed)
                    measurements.append((engine, round_index + 1, position, case["id"], elapsed * 1000))
                wall_seconds = time.perf_counter() - batch_started
                batch_times[engine].append(wall_seconds)
                print(f"Pass {round_index + 1}/{args.rounds} {engine}: {wall_seconds:.3f} s", flush=True)
        for engine, worker in workers.items():
            summary["engines"][engine]["requests_per_process"] = worker.sequence
    summary["experiment_wall_seconds"] = time.perf_counter() - started
    for engine, entries in records.items():
        stats = summary["engines"][engine]
        stats.update(summarize(entries))
        stats["warmup_batch_wall_seconds"] = warmup_times[engine]
        stats["warmup_wall_seconds"] = sum(warmup_times[engine])
        stats["batch_wall_seconds"] = batch_times[engine]
        stats["median_batch_wall_seconds"] = statistics.median(batch_times[engine])
        stats["texts_per_second"] = len(cases) / stats["median_batch_wall_seconds"]
        stats["by_length"] = {
            group: summarize([entry for entry in entries if entry["case"]["length_group"] == group])
            for group in LENGTH_GROUPS
        }
        stats["by_language"] = {
            language: summarize([entry for entry in entries if entry["case"]["language"] == language])
            for language in sorted(set(case["language"] for case in cases))
        }
    summary["pairwise_outcomes"] = {}
    for left_name, right_name in combinations(engines, 2):
        paired = Counter()
        for left, right in zip(records[left_name], records[right_name]):
            left_correct = left["result"]["Iso639"] == left["case"]["language"]
            right_correct = right["result"]["Iso639"] == right["case"]["language"]
            paired["both_correct" if left_correct and right_correct else
                   f"only_{left_name}_correct" if left_correct else
                   f"only_{right_name}_correct" if right_correct else "both_wrong"] += 1
            paired["disagreements"] += left["result"]["Iso639"] != right["result"]["Iso639"]
        summary["pairwise_outcomes"][f"{left_name}_vs_{right_name}"] = {name: paired[name] for name in (
            "both_correct", f"only_{left_name}_correct", f"only_{right_name}_correct", "both_wrong", "disagreements",
        )}
    summary["paired_outcomes"] = summary["pairwise_outcomes"]["cld3_vs_langid"]
    write_reports(args.output, summary, records, measurements)
    engine_width = max(8, *(len(engine) for engine in engines))
    print(f"\n{'Engine':{engine_width}} {'Pure Go':8} Languages  Accuracy  Startup(s)  Warm-up(s)  Median pass(s)   Texts/s   Median RTT   p95 RTT")
    for engine, stats in summary["engines"].items():
        print(f"{engine:{engine_width}} {format_pure_go(stats['pure_go']):8} {stats['supported_language_count']:9d}  "
              f"{stats['accuracy_pct']:6.2f}%  {stats['startup_seconds']:10.4f}  {stats['warmup_wall_seconds']:10.4f}  "
              f"{stats['median_batch_wall_seconds']:14.4f}  "
              f"{stats['texts_per_second']:8.1f}   {stats['median_roundtrip_ms']:8.4f} ms  {stats['p95_roundtrip_ms']:8.4f} ms")
    print(f"Reports: {args.output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke", help="send two requests to each persistent worker")
    smoke.add_argument("executables", nargs="+", type=Path)
    smoke.add_argument("--timeout", type=float, default=120.0)
    benchmark = commands.add_parser("run", help="compare accuracy and warm wall-clock performance")
    for engine, (name, _) in WORKERS.items():
        benchmark.add_argument(f"--{engine}", type=Path, default=BIN / name)
    benchmark.add_argument("--suite", type=Path, default=ROOT / "suite" / "flores200.jsonl")
    benchmark.add_argument("--output", type=Path, default=ROOT / "results" / "local" / "latest",
                           help="directory for the four-engine report and data files")
    benchmark.add_argument("--rounds", type=int, default=8, help="measured passes per engine; default: 8")
    benchmark.add_argument("--warmup-rounds", type=int, default=1)
    benchmark.add_argument("--seed", type=int, default=DEFAULT_SEED)
    benchmark.add_argument("--timeout", type=float, default=120.0)
    benchmark.add_argument("--gomaxprocs", type=int, default=1)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if platform.system() != "Linux":
        parser.error("run this script inside Linux (for example, WSL Ubuntu)")
    if args.command == "run":
        if args.rounds < 1 or args.warmup_rounds < 1 or args.gomaxprocs < 1:
            parser.error("rounds, warmup-rounds, and gomaxprocs must be positive")
        run_benchmark(args)
        return 0
    for executable in args.executables:
        with Worker(executable, args.timeout) as worker:
            print(f"{executable.name}: startup {worker.startup_seconds * 1000:.2f} ms")
            for text in ("This text is in English.", "Guten Tag, wie geht es?"):
                result, elapsed = worker.classify(text)
                print(f"  {elapsed * 1000:.3f} ms  {json.dumps(result)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)