"""Standard-library tests; enable real WSL workers with LANGENGINE_INTEGRATION=1."""

from collections import Counter
from contextlib import redirect_stdout
import csv
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import compare
import prepare


class FramingTests(unittest.TestCase):
    def connection_pair(self):
        reader, writer = socket.socketpair()
        reader.settimeout(2)
        writer.settimeout(2)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        return reader, writer

    def test_fragmented_unicode_frame(self):
        reader, writer = self.connection_pair()
        payload = json.dumps({"text": "\u65e5\u672c\u8a9e\n\t\""}, ensure_ascii=False).encode("utf-8")
        packet = compare.HEADER.pack(len(payload), 5) + payload

        def send_fragments():
            for offset in range(0, len(packet), 3):
                writer.sendall(packet[offset:offset + 3])

        sender = threading.Thread(target=send_fragments, daemon=True)
        sender.start()
        self.assertEqual(compare.receive_message(reader), payload)
        sender.join(timeout=2)
        self.assertFalse(sender.is_alive())

    def test_incomplete_frame_fails(self):
        reader, writer = self.connection_pair()
        writer.sendall(b"abc")
        writer.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(RuntimeError, "closed the connection"):
            compare.receive_exact(reader, 4)

    def test_invalid_headers_fail(self):
        for size, chunk_size in ((0, 1), (compare.MAX_MESSAGE + 1, 1), (10, 0)):
            with self.subTest(size=size, chunk_size=chunk_size):
                reader, writer = self.connection_pair()
                writer.sendall(compare.HEADER.pack(size, chunk_size))
                with self.assertRaisesRegex(ValueError, "header"):
                    compare.receive_message(reader)


class OutputContractTests(unittest.TestCase):
    def worker_with_response(self, result, guid="1"):
        worker = compare.Worker("unused")
        worker.connection = mock.Mock()
        response = json.dumps({"MType": 1, "GUID": guid, "Content": json.dumps(result)}).encode()
        return worker, response

    def test_preserves_text_and_json_types(self):
        expected = {"Iso639": "ja", "Probability": 0.75, "IsReliable": True}
        worker, response = self.worker_with_response(expected)
        text = "\u65e5\u672c\u8a9e\n\t\"quoted\""
        with mock.patch.object(compare, "receive_message", return_value=response):
            result, elapsed = worker.classify(text)
        self.assertEqual(result, expected)
        self.assertGreaterEqual(elapsed, 0)
        request = worker.connection.sendall.call_args.args[0]
        size, chunk_size = compare.HEADER.unpack(request[:8])
        self.assertEqual(size, len(request[8:]))
        self.assertGreater(chunk_size, 0)
        envelope = json.loads(request[8:])
        self.assertEqual(envelope["GUID"], "1")
        self.assertEqual(json.loads(envelope["Command"]), {"Text": text})

    def test_mismatched_guid_fails(self):
        worker, response = self.worker_with_response({}, guid="wrong")
        with mock.patch.object(compare, "receive_message", return_value=response):
            with self.assertRaisesRegex(ValueError, "mismatched"):
                worker.classify("test")

    def test_invalid_output_fails(self):
        invalid_results = [
            [], {}, {"Iso639": "en", "Probability": 1.1, "IsReliable": True},
            {"Iso639": "en", "Probability": float("nan"), "IsReliable": True},
            {"Iso639": "en", "Probability": True, "IsReliable": True},
            {"Iso639": "en", "Probability": 0.9, "IsReliable": "true"},
        ]
        for result in invalid_results:
            with self.subTest(result=result):
                worker, response = self.worker_with_response(result)
                with mock.patch.object(compare, "receive_message", return_value=response):
                    with self.assertRaises(ValueError):
                        worker.classify("test")


class StatisticsTests(unittest.TestCase):
    def records(self):
        return [
            {"case": {"language": "en", "text": "a" * 30},
             "result": {"Iso639": "en", "IsReliable": False}, "seconds": [0.001, 0.002]},
            {"case": {"language": "en", "text": "b" * 30},
             "result": {"Iso639": "de", "IsReliable": True}, "seconds": [0.003, 0.004]},
            {"case": {"language": "de", "text": "c" * 30},
             "result": {"Iso639": "de", "IsReliable": True}, "seconds": [0.005, 0.006]},
        ]

    def test_accuracy_counts_cases_not_repeated_measurements(self):
        stats = compare.summarize(self.records())
        self.assertEqual(stats["samples"], 3)
        self.assertEqual(stats["correct"], 2)
        self.assertEqual(stats["measurements"], 6)
        self.assertAlmostEqual(stats["accuracy_pct"], 200 / 3)
        self.assertEqual(stats["macro_accuracy_pct"], 75)
        self.assertAlmostEqual(stats["reliable_coverage_pct"], 200 / 3)
        self.assertEqual(stats["reliable_accuracy_pct"], 50)
        self.assertEqual(stats["median_roundtrip_ms"], 3.5)
        self.assertEqual(stats["p95_roundtrip_ms"], 6)

    def test_no_reliable_predictions_has_no_conditional_accuracy(self):
        records = self.records()
        for record in records:
            record["result"]["IsReliable"] = False
        stats = compare.summarize(records)
        self.assertEqual(stats["reliable_coverage_pct"], 0)
        self.assertIsNone(stats["reliable_accuracy_pct"])


class SuiteTests(unittest.TestCase):
    def test_builds_all_workers_without_changing_parent_environment(self):
        original_environment = os.environ.copy()
        for architecture, machine in (("amd64", "x86_64"), ("arm64", "aarch64"), ("arm", "armv7l")):
            def run_command(command, **kwargs):
                return SimpleNamespace(stdout=json.dumps({"GOHOSTOS": "linux", "GOHOSTARCH": architecture})
                                       if command[1] == "env" else "test build info")

            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as directory:
                with (
                    mock.patch.object(prepare, "BIN", Path(directory)),
                    mock.patch.object(prepare.subprocess, "run", side_effect=run_command) as run,
                    mock.patch.object(prepare, "sha256_file", return_value="test-hash"),
                    mock.patch.object(prepare.platform, "system", return_value="Linux"),
                    mock.patch.object(prepare.platform, "machine", return_value=machine),
                    redirect_stdout(io.StringIO()),
                ):
                    prepare.build_workers("custom-go")
                for engine, name, cgo in (
                    ("cld3", "goCld3", "1"), ("langid", "goLangId", "0"),
                    ("whatlanggo", "goWhatlanggo", "0"), ("lingua", "goLingua", "0"),
                ):
                    metadata = json.loads((Path(directory) / f"{name}.build.json").read_text(encoding="utf-8"))
                    self.assertEqual(metadata["engine"], engine)
                    self.assertEqual(metadata["sha256"], "test-hash")
                    self.assertEqual(metadata["go_build_info"], "test build info")
                    self.assertIn("go.mod", metadata["source_sha256"])
                    self.assertIn("benchmarks/language-detection/internal/workerprotocol/worker.go", metadata["source_sha256"])
                    self.assertIn(f"benchmarks/language-detection/workers/{engine}/main.go", metadata["source_sha256"])
                    self.assertIn("benchmarks/language-detection/workers/go.mod", metadata["source_sha256"])
                    self.assertIn("benchmarks/language-detection/workers/go.sum", metadata["source_sha256"])
                    self.assertEqual(metadata["environment"]["CGO_ENABLED"], cgo)
                    self.assertEqual(metadata["environment"]["GOARCH"], architecture)
                    if cgo == "1":
                        self.assertEqual(metadata["protobuf"]["version"], "3.17.3")
                build_calls = [call for call in run.call_args_list if call.args[0][1] == "build"]
                self.assertEqual(len(build_calls), 4)
                for call, name, cgo, package in zip(
                    build_calls, ("goCld3", "goLangId", "goWhatlanggo", "goLingua"), ("1", "0", "0", "0"),
                    ("./cld3", "./langid", "./whatlanggo", "./lingua"),
                ):
                    self.assertEqual(call.args[0][0], "custom-go")
                    self.assertIn("-mod=readonly", call.args[0])
                    self.assertEqual(call.args[0][-2:], [str(Path(directory) / name), package])
                    self.assertEqual(call.kwargs["cwd"], prepare.ROOT / "workers")
                    self.assertEqual({key: call.kwargs["env"][key] for key in ("CGO_ENABLED", "GOOS", "GOARCH", "GOWORK")},
                                     {"CGO_ENABLED": cgo, "GOOS": "linux", "GOARCH": architecture, "GOWORK": "off"})
                    self.assertEqual(call.kwargs["env"]["PKG_CONFIG_PATH"], "")
                    self.assertEqual(call.kwargs["env"]["PKG_CONFIG_LIBDIR"], str(prepare.PROTOBUF_PREFIX / "lib" / "pkgconfig"))
        self.assertEqual(dict(os.environ), original_environment)

    def test_build_rejects_non_linux_hosts(self):
        with mock.patch.object(prepare.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "Linux"):
                prepare.build_workers("go")

    def test_build_rejects_non_linux_go_toolchain(self):
        with (
            mock.patch.object(prepare.platform, "system", return_value="Linux"),
            mock.patch.object(prepare.subprocess, "run", return_value=SimpleNamespace(
                stdout=json.dumps({"GOHOSTOS": "windows", "GOHOSTARCH": "amd64"}),
            )) as run,
        ):
            with self.assertRaisesRegex(ValueError, "native Linux"):
                prepare.build_workers("go.exe")
        self.assertEqual(run.call_count, 1)

    def test_vendored_suite_integrity_and_balance(self):
        cases, manifest = compare.load_suite(compare.ROOT / "suite" / "flores200.jsonl")
        self.assertEqual(len(cases), 1000)
        self.assertEqual(manifest["source_sha256"], prepare.FLORES_SHA256)
        self.assertEqual(manifest["license"], "CC-BY-SA-4.0")
        self.assertEqual(Counter((case["language"], case["length_group"]) for case in cases),
                         Counter({(language, group): 25 for language in prepare.LANGUAGES for group in prepare.LENGTH_GROUPS}))
        self.assertTrue(all(1 <= case["source_line"] <= 1012 for case in cases))

    def test_selection_is_deterministic_and_uses_utf8_bytes(self):
        sentences = ["\u3042" * 50, "\u3044" * 50, "\u3042" * 50, "\u3046" * 100, "\u3048" * 100]
        selected, counts = prepare.select_sentences(sentences, "ja", "jpn_Jpan", 2, 1234)
        self.assertEqual((selected, counts), prepare.select_sentences(sentences, "ja", "jpn_Jpan", 2, 1234))
        self.assertEqual(counts, {"short": 2, "medium": 2})
        self.assertEqual(len(selected), 4)
        for case in selected:
            minimum, maximum = prepare.LENGTH_GROUPS[case["length_group"]]
            self.assertLessEqual(minimum, len(case["text"].encode("utf-8")))
            self.assertGreaterEqual(maximum, len(case["text"].encode("utf-8")))
            self.assertEqual(case["text"], sentences[case["source_line"] - 1])

    def test_insufficient_candidates_fail(self):
        with self.assertRaisesRegex(ValueError, "eligible"):
            prepare.select_sentences(["a" * 30], "en", "eng_Latn", 2, 1234)

    def test_invalid_suites_fail(self):
        case = {"id": "one", "language": "en", "length_group": "short", "text": "a" * 30}
        for contents in ("", "[]\n", json.dumps(case) + "\n" + json.dumps(case) + "\n",
                         json.dumps({**case, "text": "too short"}) + "\n"):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cases.jsonl"
                path.write_text(contents, encoding="utf-8")
                with self.assertRaises(ValueError):
                    compare.load_suite(path)

    def test_archive_checksum_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive"
            path.write_bytes(b"not the original archive")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                prepare.checked_archive(prepare.FLORES_URL, prepare.FLORES_SHA256, path)


class BuildMetadataTests(unittest.TestCase):
    def test_generated_workers_require_matching_build_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with mock.patch.object(compare, "BIN", directory):
                for name in ("goCld3", "goLangId", "goWhatlanggo", "goLingua"):
                    with self.subTest(engine=name):
                        executable = directory / name
                        executable.write_bytes(b"freshly built worker")
                        with self.assertRaisesRegex(ValueError, "no build metadata"):
                            compare.binary_metadata(executable)
                        build = {"sha256": prepare.sha256_file(executable), "go_build_info": "pinned dependencies",
                                 "environment": {"CGO_ENABLED": "1" if name == "goCld3" else "0"}}
                        executable.with_name(name + ".build.json").write_text(json.dumps(build), encoding="utf-8")
                        metadata = compare.binary_metadata(executable)
                        self.assertEqual(metadata["build"], build)
                        self.assertIs(metadata["pure_go"], name != "goCld3")
                        executable.write_bytes(b"different executable")
                        with self.assertRaisesRegex(ValueError, "does not match"):
                            compare.binary_metadata(executable)

    def test_custom_binary_without_build_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "custom-worker"
            executable.write_bytes(b"custom worker")
            metadata = compare.binary_metadata(executable)
            self.assertEqual(metadata["sha256"], prepare.sha256_file(executable))
            self.assertNotIn("build", metadata)
            self.assertIsNone(metadata["pure_go"])
            self.assertEqual(compare.format_pure_go(metadata["pure_go"]), "Unknown")


class BenchmarkTests(unittest.TestCase):
    def test_cli_uses_native_worker_names_on_linux_arm64(self):
        with (
            mock.patch.object(compare.sys, "argv", ["compare.py", "run"]),
            mock.patch.object(compare.platform, "system", return_value="Linux"),
            mock.patch.object(compare.platform, "machine", return_value="aarch64"),
            mock.patch.object(compare, "run_benchmark") as run,
        ):
            self.assertEqual(compare.main(), 0)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments.cld3, compare.BIN / "goCld3")
        self.assertEqual(arguments.langid, compare.BIN / "goLangId")
        self.assertEqual(arguments.whatlanggo, compare.BIN / "goWhatlanggo")
        self.assertEqual(arguments.lingua, compare.BIN / "goLingua")
        self.assertEqual(arguments.rounds, 8)
        self.assertEqual(arguments.output, compare.ROOT / "results" / "local" / "latest")
        self.assertEqual(run.call_count, 1)

    def test_cli_runs_one_comparison_in_the_requested_directory(self):
        with (
            mock.patch.object(compare.sys, "argv", ["compare.py", "run", "--output", "reports"]),
            mock.patch.object(compare.platform, "system", return_value="Linux"),
            mock.patch.object(compare, "run_benchmark") as run,
        ):
            self.assertEqual(compare.main(), 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0].output, Path("reports"))
        self.assertEqual(run.call_args.args[0].rounds, 8)

    def test_cli_accepts_custom_rounds(self):
        with (
            mock.patch.object(compare.sys, "argv", ["compare.py", "run", "--rounds", "4"]),
            mock.patch.object(compare.platform, "system", return_value="Linux"),
            mock.patch.object(compare, "run_benchmark") as run,
        ):
            self.assertEqual(compare.main(), 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0].rounds, 4)

    def test_warmup_exclusion_order_reuse_and_reports(self):
        self.check_benchmark(8, warmup_rounds=2)

    def test_unsupported_corpus_languages_are_excluded(self):
        self.check_benchmark(4, exclude_unsupported=True)

    def check_benchmark(self, rounds, exclude_unsupported=False, warmup_rounds=1):
        engine_names = tuple(compare.WORKERS)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cases = [
                {"id": "short", "language": "en", "length_group": "short", "text": "English text. " * 3},
                {"id": "medium", "language": "en", "length_group": "medium", "text": "English text. " * 20},
            ]
            source_cases = list(cases)
            if exclude_unsupported:
                source_cases.extend({**case, "id": "unsupported-" + case["id"], "language": "fr"} for case in cases)
            suite = directory / "cases.jsonl"
            suite.write_text("".join(json.dumps(case) + "\n" for case in source_cases), encoding="utf-8")
            executables = {engine: directory / engine for engine in engine_names}
            for engine, path in executables.items():
                path.write_bytes(b"mock executable")
                build = {"sha256": prepare.sha256_file(path),
                         "environment": {"CGO_ENABLED": compare.WORKERS[engine][1]}}
                path.with_name(path.name + ".build.json").write_text(json.dumps(build), encoding="utf-8")
            seen = {engine: [] for engine in executables}
            wall_clock = 0.0

            def create_worker(executable, timeout, gomaxprocs):
                worker = mock.MagicMock()
                worker.__enter__.return_value = worker
                worker.startup_seconds = 0.1
                worker.process.pid = 1234
                worker.sequence = 0

                def classify(text):
                    nonlocal wall_clock
                    worker.sequence += 1
                    seen[executable.name].append(text)
                    is_warmup = worker.sequence <= len(cases) * warmup_rounds
                    wall_clock += 5.0 if is_warmup else 0.002
                    elapsed = 500.0 if is_warmup else 0.001
                    return {"Iso639": "en", "Probability": 0.75, "IsReliable": True}, elapsed

                worker.classify.side_effect = classify
                return worker

            args = SimpleNamespace(suite=suite, **executables,
                                   timeout=1, gomaxprocs=1, seed=42, rounds=rounds, warmup_rounds=warmup_rounds,
                                   output=directory / "results")
            with (
                mock.patch.object(compare, "Worker", side_effect=create_worker) as factory,
                mock.patch.object(compare.time, "perf_counter", side_effect=lambda: wall_clock),
                mock.patch.object(compare, "supported_languages", side_effect=lambda executable, timeout:
                                  ["en"] if executable.name == "whatlanggo" else ["en", "fr"]) as coverage,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                compare.run_benchmark(args)
            self.assertEqual(factory.call_count, len(engine_names))
            self.assertEqual(coverage.call_count, len(engine_names))
            for texts in seen.values():
                self.assertEqual(texts, seen["cld3"])
            summary = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["suite"]["source_samples"], 4 if exclude_unsupported else 2)
            self.assertEqual(summary["suite"]["samples"], 2)
            self.assertEqual(summary["suite"]["languages"], ["en"])
            self.assertEqual(summary["suite"]["excluded_languages"], ["fr"] if exclude_unsupported else [])
            self.assertEqual(summary["engines"]["lingua"]["supported_languages"], ["en", "fr"])
            self.assertEqual(summary["engines"]["lingua"]["supported_language_count"], 2)
            self.assertEqual(summary["engines"]["whatlanggo"]["supported_language_count"], 1)
            self.assertEqual(summary["engines"]["whatlanggo"]["supported_label_count"], 1)
            self.assertEqual([entry["engine_order"] for entry in summary["passes"]],
                             [list(engine_names[offset:] + engine_names[:offset])
                              for offset in (index % len(engine_names) for index in range(rounds))])
            self.assertEqual(list(summary["engines"]), list(engine_names))
            self.assertEqual(len(summary["pairwise_outcomes"]), len(engine_names) * (len(engine_names) - 1) // 2)
            self.assertEqual(summary["paired_outcomes"]["both_correct"], 2)
            for engine, stats in summary["engines"].items():
                self.assertIs(stats["pure_go"], engine != "cld3")
                self.assertEqual(stats["startup_seconds"], 0.1)
                self.assertEqual(stats["warmup_batch_wall_seconds"], [10.0] * warmup_rounds)
                self.assertEqual(stats["warmup_wall_seconds"], 10.0 * warmup_rounds)
                self.assertEqual(len(stats["batch_wall_seconds"]), rounds)
                self.assertAlmostEqual(stats["median_batch_wall_seconds"], 0.004)
                self.assertAlmostEqual(stats["texts_per_second"], 500.0)
                self.assertEqual(stats["requests_per_process"], 2 * (rounds + warmup_rounds))
                self.assertEqual(stats["samples"], 2)
                self.assertEqual(stats["measurements"], 2 * rounds)
                self.assertEqual(stats["median_roundtrip_ms"], 1)
                self.assertEqual(stats["accuracy_pct"], 100)
            with (args.output / "timings.csv").open(encoding="utf-8", newline="") as source:
                self.assertEqual(len(list(csv.DictReader(source))), 2 * rounds * len(engine_names))
            with (args.output / "predictions.csv").open(encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                self.assertEqual(len(list(reader)), 2)
                for engine in engine_names:
                    self.assertIn(f"{engine}_language", reader.fieldnames)
            report = (args.output / "report.md").read_text(encoding="utf-8")
            self.assertIn("| Engine | Pure Go | Supported languages | Accuracy | Startup (s) | Warm-up total (s) | Median pass wall (s) |", report)
            self.assertIn(f"| cld3 | No (CGO) | 2 | 100.00% | 0.1000 | {10.0 * warmup_rounds:.4f} | 0.0040 |", report)
            self.assertIn("| whatlanggo | Yes | 1 | 100.00% |", report)
            self.assertIn("| lingua | Yes | 2 | 100.00% |", report)
            self.assertIn(f"| Warm-up (discarded) | {warmup_rounds} | 10.0000 |", report)
            self.assertIn(f"| Measured | {rounds} | 0.0040 |", report)
            self.assertEqual(sum(line.startswith("| Warm-up (discarded) |") for line in report.splitlines()), warmup_rounds)
            self.assertEqual(sum(line.startswith("| Measured |") for line in report.splitlines()), rounds)
            self.assertIn("| Language | Cases | " + " | ".join(engine_names) + " |", report)
            for heading in ("Pure Go", "Startup(s)", "Warm-up(s)", "Median pass(s)"):
                self.assertIn(heading, stdout.getvalue())
            for engine in engine_names:
                self.assertIn(f"{engine} median RTT (ms)", report)


class LanguageCoverageTests(unittest.TestCase):
    def test_language_counts_collapse_scripts_and_exclude_non_languages(self):
        self.assertEqual(compare.supported_language_count(
            ["en", "ja", "ja-Latn", "zh", "zh-Latn", "und", "zxx"],
        ), 3)
        self.assertEqual(compare.supported_language_count(["und", "zxx"]), 0)

    def test_language_coverage_query(self):
        with mock.patch.object(compare.subprocess, "run", return_value=SimpleNamespace(stdout='["fr", "en"]')) as run:
            self.assertEqual(compare.supported_languages(Path("worker"), 10), ["en", "fr"])
        self.assertEqual(run.call_args.args[0], [str(Path("worker").resolve()), "--languages"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_invalid_coverage_fails(self):
        for languages in ([], {}, ["en", "en"], [""], ["en", 1], [" en"]):
            with self.subTest(languages=languages), mock.patch.object(
                compare.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(languages)),
            ):
                with self.assertRaisesRegex(ValueError, "invalid language coverage"):
                    compare.supported_languages(Path("worker"), 10)

    def test_shared_selection_is_an_intersection_without_changing_cases(self):
        cases = [{"id": language, "language": language, "text": language} for language in ("en", "fr", "de")]
        coverage = {"cld3": ["en", "fr", "de"], "langid": ["en", "fr"],
                    "whatlanggo": ["en", "de"], "lingua": ["en", "fr", "de"]}
        selected = compare.select_shared_cases(cases, coverage)
        self.assertEqual(selected, [cases[0]])
        self.assertIs(selected[0], cases[0])
        self.assertEqual(len(cases), 3)

    def test_no_shared_corpus_language_fails(self):
        with self.assertRaisesRegex(ValueError, "no languages shared"):
            compare.select_shared_cases([{"language": "de"}], {"cld3": ["en"], "langid": ["fr"]})
        with self.assertRaisesRegex(ValueError, "coverage is required"):
            compare.select_shared_cases([{"language": "en"}], {})


@unittest.skipUnless(os.environ.get("LANGENGINE_INTEGRATION") == "1", "set LANGENGINE_INTEGRATION=1 to run real Linux workers")
class RealWorkerTests(unittest.TestCase):
    def test_workers_reuse_process_and_cleanup(self):
        for name in ("goCld3", "goLangId", "goWhatlanggo", "goLingua"):
            with self.subTest(engine=name):
                with compare.Worker(compare.BIN / name, timeout=120, gomaxprocs=1) as worker:
                    process = worker.process
                    for text, language in (("This text is in English.", "en"), ("Guten Tag, wie geht es?", "de")):
                        result, elapsed = worker.classify(text)
                        self.assertEqual(result["Iso639"], language)
                        self.assertGreater(elapsed, 0)
                        self.assertIs(worker.process, process)
                        self.assertIsNone(process.poll())
                    self.assertEqual(worker.sequence, 2)
                self.assertIsNotNone(process.poll())
                self.assertIsNone(worker.connection)

    def test_all_workers_cover_the_vendored_corpus(self):
        cases, _ = compare.load_suite(compare.ROOT / "suite" / "flores200.jsonl")
        coverage = {engine: compare.supported_languages(compare.BIN / name, 120)
                    for engine, (name, _) in compare.WORKERS.items()}
        self.assertEqual(compare.select_shared_cases(cases, coverage), cases)

    def test_failed_startup_cleans_up(self):
        worker = compare.Worker("/bin/false", timeout=1)
        with self.assertRaisesRegex(RuntimeError, "exited before ready"):
            with worker:
                self.fail("worker should not start")
        self.assertIsNone(worker.process)
        self.assertIsNone(worker.connection)
        self.assertIsNone(worker.log)


if __name__ == "__main__":
    unittest.main()