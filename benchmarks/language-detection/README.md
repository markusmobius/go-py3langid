# Language Detector Comparison

An opt-in, source-built Linux benchmark of **CLD3**, **go-py3langid**, **Whatlanggo**, and **Lingua**. The Python harness uses persistent workers and measures accuracy and wall-clock performance on labeled FLORES-200 sentences.

The command runs all four engines together on corpus languages supported by every engine and produces one comparison table. All 20 languages in the included corpus qualify, so every detector is evaluated on the same 1,000 sentences.

All detectors retain their full candidate language sets. Coverage filtering applies to input cases, not detector configuration. No executables are bundled.

## Build And Run

Requires native Linux Go 1.25+, Python 3.10+, GCC/G++, Make, tar, and pkg-config. No third-party Python packages are needed. There is no AMD64 restriction: Go and the C/C++ compilers must target the same native host architecture.

On Ubuntu/Debian, install the prerequisites yourself and check that the distribution's Go version is new enough:

```sh
sudo apt-get update
sudo apt-get install build-essential pkg-config golang-go python3 ca-certificates
go version
```

From this benchmark directory inside Linux or WSL:

```sh
bash run.sh
```

From the repository root, use `bash benchmarks/language-detection/run.sh`. From PowerShell, with prerequisites installed inside WSL:

```powershell
wsl --distribution Ubuntu --cd /mnt/c/github/go-py3langid --exec bash benchmarks/language-detection/run.sh
```

Adjust the checkout path if necessary. The script reports missing tools and an example package-install command; it never runs `sudo` or installs system packages itself.

The script downloads and SHA256-verifies **Protobuf 3.17.3 source**, builds it privately under `.cache/`, builds all four workers, and runs the Python comparison. CLD3 statically links that Protobuf build but still needs the system C/C++ runtime libraries. No precompiled runtime is downloaded and no `LD_LIBRARY_PATH` override is used. An arbitrary newer system Protobuf is not a substitute for the version matching CLD3's generated C++ files.

The initial native dependency build can take several minutes; subsequent runs reuse it. `JOBS` controls Make parallelism (default 2). `GO`, `PYTHON`, `CC`, `CXX`, and `PKG_CONFIG` select executables. After changing architecture, compilers, flags, or the checkout location, remove `.cache/protobuf-3.17.3` and `.cache/protobuf-3.17.3-install` to rebuild the native dependency. If Go's linker still refers to an old native-library path, refresh its cache once with `GOFLAGS=-a bash run.sh --build-only`; omit that override on subsequent runs.

Allow several GB of free RAM for the four-engine run. Lingua documents approximately **1.8 GB** when all high-accuracy models are loaded. Its default lazy loading occurs during discarded warm-up requests; launch-to-ready timing does not include that model-loading work.

From this directory, build only, change the pass count, or choose an output directory:

```sh
bash run.sh --build-only
bash run.sh --rounds 12
bash run.sh --output results/local/another-run
```

`--help` lists comparison options. The default is eight measured passes per engine. `--rounds` overrides this; use a multiple of four for balanced engine ordering. The default startup/request timeout is 120 seconds.

Normal results go directly to ignored `results/local/latest/`. Assets resolve relative to the scripts; explicit relative input/output paths resolve from the caller's directory. Windows cross-compilation is not used for CGO.

## Workers And Builds

All four adapters have the same layout and share one isolated [worker module](workers/go.mod):

```text
run.sh                   Linux source builds followed by the four-engine comparison
prepare.py               Builds, provenance, checked source download, corpus preparation
compare.py               Persistent-process benchmark and report generation
test_compare.py          Harness unit tests and real-worker integration tests
workers/go.mod           Pinned dependencies for every adapter
workers/cld3/            CLD3 adapter
workers/langid/          go-py3langid adapter
workers/whatlanggo/       Whatlanggo adapter
workers/lingua/          Lingua adapter
internal/workerprotocol/ Shared TCP and legacy file event loops
bin/                     Generated binaries and sidecars; entire directory ignored
suite/                   Labeled sentences, manifest, and corpus attribution
results/2026-09-12/       Published four-engine report, summary, predictions, and timings
PROTOCOL.md              Framing, handshake, payloads, and coverage queries
```

| Engine | Source/version | Configuration |
| --- | --- | --- |
| `cld3` | [jmhodges/gocld3 v1.0.0](https://github.com/jmhodges/gocld3/tree/v1.0.0) | CGO; native probability/reliability; 0-4,000 input bytes |
| `langid` | [go-py3langid from this checkout](../../README.md) | Pure Go; calibrated probabilities; reliability at score >= 0.5; low-confidence labels retained |
| `whatlanggo` | [RadhiFadlillah/whatlanggo aac1f0f737fc](https://github.com/RadhiFadlillah/whatlanggo/tree/aac1f0f737fc3dbfb7606fb1b9c457220c685dcc) | Pure Go; default detection, native confidence and reliability |
| `lingua` | [pemistahl/lingua-go v1.4.0](https://github.com/pemistahl/lingua-go/tree/v1.4.0) | Pure Go; all languages, default high accuracy, lazy loading, zero minimum relative distance |

The outputs are `bin/goCld3`, `bin/goLangId`, `bin/goWhatlanggo`, and `bin/goLingua`. All are built with `go build -mod=readonly -trimpath`, `GOOS=linux`, `GOARCH` from the selected toolchain's `GOHOSTARCH`, and `GOWORK=off`. Only CLD3 uses `CGO_ENABLED=1`; the others use `CGO_ENABLED=0`.

Each binary has a `.build.json` sidecar recording architecture, toolchain/dependencies, binary SHA256, and worker/module-file hashes. CLD3 also records its C++ compiler and Protobuf source/static-library hashes. The harness verifies every selected sidecar and embeds it in the run summary.

The entire `bin/`, `.cache/`, and `results/local/` directories are ignored. A leftover `.runtime/` from the old setup is ignored but unused. Corpus files and published results are ordinary Git files; no Git LFS setup is needed.

## Corpus And Coverage

The [suite](suite/README.md) is a deterministic FLORES-200 `devtest` subset: 20 languages, each with 25 short (20-200 UTF-8 bytes) and 25 medium (201-1,000 bytes) sentences. Complete sentences, original line numbers, labels, source checksums, and CC-BY-SA 4.0 attribution are retained. Every input fits below CLD3's 4,000-byte cap.

Before the run, the harness queries each worker with `--languages` and intersects their supported output labels with the corpus labels. Selected sentences are unchanged. Full worker coverage and included/excluded corpus languages are recorded in the summary. Selection never depends on predictions, confidence, or observed accuracy. Exact output labels are used; aliases and script variants are not silently merged for evaluation.

The comparison table counts **103 CLD3 languages, 139 py3langid languages, 84 Whatlanggo languages, and 75 Lingua languages**. Counts group script variants of the same language and exclude non-language classes (`und` and `zxx`). The raw coverage lists contain 109 CLD3 output labels and 140 py3langid classes; all raw labels and both counts remain in the summary for audit. This counting rule does not change predictions or restrict the detectors.

**This is a selected 20-language FLORES-200 subset, not the full FLORES-200 language inventory.** All 20 corpus languages are supported by all four engines, so the run includes all 1,000 cases and excludes none. The supported-language column describes detector coverage, not how many languages were tested.

The included corpus is read offline. Optional regeneration, from this directory:

```sh
python3 prepare.py suite
```

`--archive PATH` accepts an existing download and still verifies its SHA256. Selection is independent of model predictions; no sentences were truncated or joined to fill length groups.

## Measurement

- One long-lived process per engine; one outstanding request at a time over IPv4 loopback TCP. All adapters share the same [protocol](PROTOCOL.md).
- Startup (launch-to-ready), warm-up total (wall time of all discarded full passes), and measured pass wall time are recorded separately. The default is one full warm-up pass followed by eight measured passes, rotating the starting engine so each occupies every position twice. Each engine sees the same seeded sentence shuffle in each measured pass.
- `GOMAXPROCS=1` is set equally for every worker. This is a sequential workload, not a saturation or concurrent-throughput test.
- Primary steady-state timing is full-pass wall time, including the Python loop, JSON, transport, and worker execution. Texts/second uses the median measured pass time. Startup and warm-up are excluded from pass, throughput, and RTT statistics. This is not isolated model CPU time.
- Request RTT starts after Python encodes the request and ends when the complete response frame arrives. Response decoding is outside RTT but inside pass wall time. p95 uses nearest rank.
- Startup is one launch-to-ready observation, not a controlled cold-cache measurement. Lingua's lazy model loading occurs in warm-up, so startup values do not represent equivalent initialization work.
- Accuracy counts each case once, including unreliable predictions. Labels are compared exactly, and predictions must remain unchanged across measured passes. Reports include macro, per-language, and per-length scores plus pairwise outcomes.
- Confidence and reliability are engine-specific. Lingua computes confidence once and uses its default unique-winner rule; a tie returns an empty label and false reliability. No common confidence threshold is imposed across engines. See [PROTOCOL.md](PROTOCOL.md) for exact adapter semantics.

## Results: 2026-09-12

Fresh measurements on **WSL2 Linux/AMD64, AMD Ryzen AI 7 PRO 350, Go 1.26.0, Python 3.14.4**, with `GOMAXPROCS=1`. One run uses 1,000 cases across 20 languages, with eight measured passes after one full warm-up. Predictions stayed identical across measured passes. [Full comparison report](results/2026-09-12/report.md).

| Engine | Pure Go | Supported languages | Accuracy | Startup (s) | Warm-up total (s) | Median pass (s) | Texts/s | Median RTT (ms) | p95 RTT (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CLD3 | No (CGO) | 103 | 98.4% | 0.0474 | 0.6154 | 0.4546 | 2,199.9 | 0.3994 | 0.5659 |
| go-py3langid | Yes | 139 | 99.2% | 0.4007 | 0.6579 | 0.4982 | 2,007.0 | 0.4539 | 0.6523 |
| Whatlanggo | Yes | 84 | 98.4% | 0.0394 | 0.7823 | 0.5724 | 1,746.9 | 0.5286 | 0.8067 |
| Lingua | Yes | 75 | 98.7% | 0.0634 | 11.1091 | 2.3587 | 424.0 | 2.0583 | 6.3081 |

**Startup** ends at readiness. **Warm-up total** is the separately timed discarded 1,000-text pass in this run, including Lingua's lazy model loading. **Median pass** is the median of the eight subsequent 1,000-text passes and excludes startup and warm-up. The [per-pass breakdown](results/2026-09-12/report.md#pass-wall-times) lists the warm-up and all eight measured durations for every engine.

**Pure Go** reflects the recorded build: CLD3 uses native C++ through CGO; the other three adapters are built with `CGO_ENABLED=0`. Custom binaries without matching build metadata for this setting are reported as `Unknown`.

Supported-language counts use the rule in [Corpus And Coverage](#corpus-and-coverage); only the same 20 corpus languages are evaluated for every engine.

On this corpus, langid has the highest overall accuracy and CLD3 the lowest median pass time. This is not a universal ranking: Chinese favors CLD3 and Lingua (50/50 each versus langid's 46/50), while Indonesian favors Whatlanggo (50/50 versus langid's 48/50, CLD3's 42/50, and Lingua's 40/50).

Small timing differences require caution; individual passes vary. The sample contains clean, edited sentences; translations share source sentences, training overlap has not been audited, and noisy text, code-switching, and very short messages are not covered. No statistical significance or universal speedup is claimed.

The [recorded artifacts](results/README.md) contain one report, a structured summary with separate startup, warm-up, and per-pass wall times, 1,000 per-case prediction rows, and **32,000 measured request timings**. Warm-up requests are excluded from that timing CSV. Older result snapshots have been removed.

After building, rerun without rebuilding or check worker startup and repeated requests:

```sh
python3 compare.py run
python3 compare.py run --rounds 12 --output results/local/twelve-passes
python3 compare.py smoke bin/goCld3 bin/goLangId bin/goWhatlanggo bin/goLingua
```

Custom `--cld3`, `--langid`, `--whatlanggo`, `--lingua`, `--suite`, `--seed`, `--warmup-rounds`, `--gomaxprocs`, and `--timeout` options are available. With multiple warm-up passes, the table reports their total; each duration is retained separately in the report and summary. To publish another observation, use `bash run.sh --output results/NAME`; the report and data files are written directly into that directory.

## Tests

Normal `go test ./...` covers the library and shared protocol without requiring Python, CGO, Protobuf, or the benchmark detector dependencies. All four adapters live in the separate worker module.

Run Python tests without integration from the repository root:

```sh
python3 -m unittest discover -s benchmarks/language-detection -p test_compare.py -v
```

After `run.sh --build-only`, run all adapter contract tests and real-worker integration tests on Linux, also from the repository root:

```sh
PKG_CONFIG_PATH= PKG_CONFIG_LIBDIR="$PWD/benchmarks/language-detection/.cache/protobuf-3.17.3-install/lib/pkgconfig" \
    CGO_ENABLED=1 GOWORK=off go -C benchmarks/language-detection/workers test -mod=readonly ./...
LANGENGINE_INTEGRATION=1 python3 -m unittest discover -s benchmarks/language-detection -p test_compare.py -v
```

Performance thresholds and corpus-wide accuracy numbers are deliberately not CI assertions. Workers are intended for a trusted local experiment, not exposure as a public service.

## Licensing

Benchmark code follows the [repository license](../../LICENSE). FLORES-200 sentences and text-containing result CSVs retain their [CC-BY-SA 4.0 terms and attribution](suite/README.md).

Dependencies are downloaded during setup, not vendored, and generated binaries are not distributed with this repository. When redistributing builds, include the applicable license and notice files for [CLD3](https://github.com/google/cld3/blob/master/LICENSE), [gocld3](https://github.com/jmhodges/gocld3/blob/cc40e88f75052db19488738560c837a06fad0162/LICENSE), [Whatlanggo](https://github.com/RadhiFadlillah/whatlanggo/blob/aac1f0f737fc3dbfb7606fb1b9c457220c685dcc/LICENSE), [Lingua](https://github.com/pemistahl/lingua-go/blob/v1.4.0/LICENSE), [Protobuf](https://github.com/protocolbuffers/protobuf/blob/v3.17.3/LICENSE), Go, and other libraries you ship.

The Protobuf license is retained under `.cache/protobuf-3.17.3/LICENSE`, Go dependency licenses are in their module downloads, and the selected Go toolchain's license is in `GOROOT`. Each published summary retains its binaries' build provenance.