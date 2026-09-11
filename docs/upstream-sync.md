# Upstream Synchronization

This repository ports the py3langid inference runtime, CLI, and HTTP contracts to
Go. Training, corpus acquisition, feature selection, and Python model generation
remain upstream. A Go test passing does not establish compatibility with an
unreviewed upstream revision.

## Reference Lock

[../testdata/py3langid_cases.json](../testdata/py3langid_cases.json) is the source
of truth for the Python commit, version, NumPy baseline, model SHA-256 hashes,
upstream inference/HTTP test-function inventory, and shared inputs.
[../scripts/requirements-parity.txt](../scripts/requirements-parity.txt) pins
the development dependencies. CI installs py3langid from the checkout selected
by the lock, rather than from a floating package release.

The comparison runner rejects a different commit, modified upstream library or
tests, mismatched model fingerprints, mismatched NumPy/version, or a changed
inventory of test-function names. It then runs the unmodified upstream suites
and generates fresh Python results before running the Go comparisons. Go's JSON
test events must confirm that every named shared case and the CLI/HTTP mirrors
actually passed; a successful command that ran no tests is not sufficient.

The inventory is a review guard, not proof of complete automatic test translation.
Changed assertions, parameters, or new test modules still require human review.

## Run The Live Comparison

From the repository root, create a development environment:

```sh
python -m venv .venv
```

Activate it with `.\.venv\Scripts\Activate.ps1` in PowerShell, or
`source .venv/bin/activate` on macOS/Linux. Then:

```sh
python -m pip install -r scripts/requirements-parity.txt
git clone https://github.com/adbar/py3langid .reference/py3langid
python -c "import json,subprocess; pin=json.load(open('testdata/py3langid_cases.json'))['commit']; subprocess.run(['git','-C','.reference/py3langid','checkout','--detach',pin],check=True)"
python -m pip install --no-deps ./.reference/py3langid
python scripts/compare_py3langid.py --reference .reference/py3langid
```

The installation step supplies Python's `langid` console entry point, which the
unmodified upstream CLI tests execute. Python is used only in this development
environment; the Go library and command remain self-contained. The runner's
`--go` option accepts an explicit Go executable if needed. Go must also be on
`PATH` for native CLI tests that build or run the command.

The current checks include 28 upstream inference/CLI and 14 upstream HTTP test
instances, 42 shared inference inputs, and native Go CLI/HTTP mirrors. The full
Go suite additionally checks files, concurrent language-set mutation, URL errors
and limits, and invalid converted models. Upstream training tests are outside the
Go runtime port's scope.

## Numerical Contract

For every shared input, Go checks preprocessed bytes, raw and normalized top
labels, full per-label score vectors, unique labels, sorted rankings, and
normalization sums. The snapshot also records model dimensions and provenance.

| Values | Allowed absolute error |
| --- | --- |
| Raw score | `1e-5 + 1e-5 * abs(reference)` |
| Normalized probability | `2e-6 + 1e-4 * abs(reference)` |
| Predicted label and preprocessed bytes | Exact match |

The scalar Go and NumPy implementations may round floating-point operations
differently. Do not change expected labels or loosen tolerances merely to hide a
port regression. Test changed behavior under the exact reference dependency
versions first.

The model currently has 100,053 features, 104,583 automaton states, 38,346 shared
transition rows, 142 internal score columns, and 140 public labels. Raw alias
columns merge by maximum; normalized columns merge by summed probability. Scores
use float32 `log1p` feature counts and upstream's byte-length temperature.
Featureless input, NFC, all-uppercase lowercasing, truncated UTF-8, and abstention
are part of the contract, not preprocessing options to change independently.

## Update Procedure

1. Run `python scripts/compare_py3langid.py --check-upstream`. A nonzero exit
   identifies drift and prints the upstream comparison URL. The scheduled workflow
   performs the same check weekly; Actions notifications depend on repository and
   account settings.
2. Review the upstream diff, including inference, model format, CLI, server, tests,
   dependencies, licenses, and any new test modules. Decide which training-only
   changes are outside the runtime port. Update the reference checkout to the
   reviewed commit and install its package in the isolated environment.
3. Update the commit/version/dependency metadata in the lock and development
   requirements where needed. Port changed inference behavior and native Go tests.
   Copy relevant new inputs or assertions into the shared corpus and update the
   test inventory after reviewing actual coverage, not just to make it pass.
4. If the model changes, convert it with the command below. Update both model
   fingerprints in the lock. Do not retain obsolete models as silent fallbacks.
5. Generate a new independent Python snapshot with `--update-reference`. Inspect
   changed predictions, scores, and preprocessing; do not generate it from Go or
   accept changed expectations without understanding why upstream changed.
6. Run the complete Go suite, vet, Linux race checks, and the live comparison.
   Rerun performance measurements when runtime, model, or dependency changes affect
   the recorded comparison. Update the README's reference and measurements.
7. Review the changes before committing or publishing. A drift notification alone
   does not mean the port has been updated, tested, or released.

```sh
python scripts/convert_model.py .reference/py3langid/py3langid/data/model.npz.xz model/py3langid.lidg
python scripts/compare_py3langid.py --reference .reference/py3langid --update-reference
go test ./...
go vet ./...
go test -race ./...
python scripts/compare_py3langid.py --reference .reference/py3langid
```

Use `Get-FileHash -Algorithm SHA256` in PowerShell or `sha256sum` on Linux to
record both model files' fingerprints. The converter reads NumPy data with
`allow_pickle=False` and emits deterministic gzip (`mtime=0`) with LIDG2 array
storage. Running it twice on the pinned input must produce the same hash.
Custom models must have the supported upstream schema; Python `.npz.xz` files
are not read directly by the Go library, and legacy LIDG1 files are rejected.

## Performance Measurement

```sh
python scripts/compare_py3langid.py --reference .reference/py3langid --benchmark --samples 5
```

Both engines classify the same strings in raw-score mode with the complete model.
The workloads are exactly 23, 1,024, and 102,400 bytes generated by repeating
`This text is in English. `. The runner pins BLAS threads to one, uses Go
`-cpu=1 -benchtime=200ms -benchmem`, and uses Python `timeit.autorange` to select
iteration counts. One complete warm-up round is discarded. Engine order
alternates each sample; each engine's median is computed independently. The
printed ratio is Python median divided by Go median, and raw samples are printed
as JSON for inspection.

The README records the 2026-09-11 Windows 11 run on an AMD Ryzen AI 7 PRO 350,
Go 1.27.1, Python 3.14.6, and NumPy 2.5.1. The five-sample ranges were:

| Workload | Go min/max, us/op | Python min/max, us/op |
| --- | ---: | ---: |
| Sentence | 1.605 / 2.248 | 11.017 / 12.294 |
| 1 KiB | 6.183 / 7.790 | 82.639 / 88.751 |
| 100 KiB | 409.692 / 478.277 | 7,344.404 / 7,648.254 |

These exclude model loading, process startup, I/O, rank sorting, probability
normalization, and concurrent processing. Repeated English input is intentionally
a reproducible microbenchmark, not a real multilingual workload or a latency SLA.
There is no speed threshold in CI: shared runners are unsuitable for stable
performance regression percentages.

## Deliberate Differences

The Go API returns errors rather than Python exceptions and uses explicit
constructors/options. `SetLanguages()` with no arguments resets the selection;
Python uses `set_languages(None)`. Go exposes only unique public labels through
`Classes()` rather than its internal duplicate score columns.

The Go CLI includes extra JSONL/classic/line/demo modes and readable score
rounding. Batch classification is sequential and ordered, not a Python worker
pool. Default batch mode skips missing paths and non-files; explicitly selecting
a Go output format enables error rows. The library retains full numeric scores.

HTTP uses Go's request parser and exact `/detect` and `/rank` routes, not a WSGI
environ or Python's first-path-segment routing. Malformed wire headers are handled
by `net/http`. CLI service binding defaults to loopback, input bodies are bounded,
and URL fetching has timeouts and response-size checks. These safety defaults
are intentional; the service/client limit setters are configuration-time APIs,
not concurrent mutation APIs.

## Model And License Provenance

The single root [LICENSE](../LICENSE) retains Marco Lui, Adrien Barbaresi, and
Ilya Pyshkin's credits, plus the original research attribution to Marco Lui and
Tim Baldwin. It covers this port's Go implementation, embedded upstream model,
and adapted tests. Preserve upstream attribution when updating those artifacts.
Third-party Go/Python dependencies retain their respective licenses and are not
relicensed by this repository.