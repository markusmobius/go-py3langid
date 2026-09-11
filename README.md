# go-py3langid

A pure-Go port of the [py3langid](https://github.com/adbar/py3langid) inference
runtime, with its current model embedded in the library and CLI. No Python, NumPy,
C compiler, or model download is needed at runtime.

The initial Go release, **v0.4.0**, tracks **0.4.0** of
[adbar/py3langid](https://github.com/adbar/py3langid).
The reference implementation is pinned to commit
[`3b99caf`](https://github.com/adbar/py3langid/tree/3b99caf00d0dcbc9416a9d06f8e5b690e919a74e).
The model recognizes **139 languages plus `zxx`** (non-linguistic content).
Its 142 internal score columns produce 140 unique output labels; Serbian and
Uzbek each have two script-specific columns that are merged during inference.
Only this model is included. The older langid.py and acquis models are not supported.

## Install

Requires **Go 1.25 or newer**.

```sh
go get github.com/markusmobius/go-py3langid
go install github.com/markusmobius/go-py3langid/cmd/langid@latest
```

From a checkout, use `go run ./cmd/langid` or `go build ./cmd/langid`.
The package name is `py3langid`; the command name is `langid`.

## Library

```go
package main

import (
	"fmt"
	"log"

	"github.com/markusmobius/go-py3langid"
)

func main() {
	identifier, err := py3langid.NewDefaultIdentifier(
		py3langid.WithNormalizedProbabilities(),
	)
	if err != nil {
		log.Fatal(err)
	}
	result, err := identifier.IdentifyString("This text is in English.")
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("%s %.6f\n", result.Language, result.Score)
}
```

Output: `en 0.720258`.

Without `WithNormalizedProbabilities()`, scores are raw log scores, as in Python's
default mode. Probabilities use upstream's byte-length calibration, not a plain
softmax of the returned raw ranking. A high score is not proof that a prediction
is correct, particularly for very short or out-of-domain text.

| Operation | Go API |
| --- | --- |
| Shared, lazily loaded default model | `py3langid.Classify(text)`, `py3langid.Rank(text)` |
| Restrict the shared default | `py3langid.SetLanguages("en", "de")`; no arguments resets it |
| Independent configuration | `py3langid.NewDefaultIdentifier(options...)` |
| Classify strings or bytes | `identifier.IdentifyString(text)`, `identifier.IdentifyBytes(data)` |
| Full sorted ranking | `identifier.RankString(text)`, `identifier.RankBytes(data)` |
| Restrict an identifier | `identifier.SetLanguages("en", "fr")`, `identifier.ResetLanguages()` |
| List active, unique labels | `identifier.Classes()` |
| Classify or rank a file | `identifier.IdentifyFile(path)`, `identifier.RankFile(path)` |
| Load a converted custom model | `py3langid.LoadModel(path, options...)` |

Use `WithMinConfidence(0.5)` together with normalized probabilities to return
`und` when confidence is below the threshold. `und` means abstention; it is not
the model's `zxx` label. Rankings still contain the model's language labels.
The `IdentifyNormalizedString` / `IdentifyNormalizedBytes` and corresponding
`RankNormalized...` methods request calibrated probabilities for individual calls.

Construct an identifier once and reuse it. Inference and language-set changes
are concurrency-safe; each call sees one complete configuration. An invalid
language restriction returns an error without changing the active set. Separate
identifiers have independent settings and model storage: the embedded file is
8.55 MiB and the decoded model arrays occupy about 92 MiB per identifier, before
working buffers and other runtime overhead. Language subsets allocate a reduced
scoring matrix while retaining the original model for reset.

## CLI And HTTP

```sh
echo "This text is in English." | langid -n
echo "This text is in English." | langid -n -d -l en,de,fr
langid -b english.txt french.txt
langid -b -d -n english.txt french.txt
langid -n -u https://example.org/
langid -s -n --host 127.0.0.1 --port 9008
```

`-m` loads a converted `.lidg` model. `-l` always selects languages; `--line`
classifies one input line at a time. With a terminal on stdin the command offers
an interactive prompt. `-b` reads paths from arguments, or one path per stdin line.
Default batch output is CSV: `path,language,score`, without a header. With `-d`
the header is `path,language,<labels...>` and each row includes all label scores.
Missing paths and non-files are skipped in default batch mode.

Go extensions include `--format jsonl`, `--format classic`, `--ignore-missing`,
`--line`, and `--demo`. Explicit `--format` enables batch mode and reports file
errors as rows unless `--ignore-missing` is given. Text and CSV scores are rounded
for display; JSON and the library retain the numerical results. CLI output is
not intended to be byte-for-byte identical to Python output.

The HTTP service supports `/detect` and `/rank` with `GET ?q=...`, `POST` form
data or a raw body, and `PUT` with a raw body. POST/PUT require a positive content
length. Responses use `application/json` and upstream's
`responseData` / `responseStatus` / `responseDetails` envelope. Scores follow the
identifier configuration: raw by default, normalized with `-n`. Missing input
returns HTTP 400; unsupported methods return 405 and unknown routes return 404.

Applications can embed `service.NewServer(identifier).NewHandler()` in a Go HTTP
server. `urlclass.NewClient(identifier)` provides `ClassifyURL` and `RankURL`,
including timeouts and a configurable HTTP client. URL mode classifies response
bytes, including any HTML; it does not extract article text.

The CLI binds to loopback unless `--host` or `-r` / `--remote` is specified.
CLI input, HTTP bodies, and URL responses default to a 4 MiB limit, adjustable
with `--max-bytes` (`0` disables it). Library string/byte/file methods have no
automatic input-size limit. Configure service/client limits before serving
concurrent requests; do not expose the service to untrusted traffic without
appropriate access controls.

## Comparison With py3langid

| Area | py3langid | go-py3langid |
| --- | --- | --- |
| Inference | Python + NumPy | Pure Go, same model parameters |
| Preprocessing and scoring | Unicode normalization, uppercase handling, `log1p` feature counts | Ported and checked against Python |
| Probabilities, aliases, restrictions, abstention | Supported | Supported, full vectors tested within floating-point tolerances |
| CLI and HTTP | Python CLI and WSGI | Native CLI and `net/http`; upstream-derived contract tests |
| Batch concurrency | Python worker processes | Sequential CLI; concurrent Go library calls supported |
| Custom models | `.npz.xz` | Convert once to `.lidg` using the development script |
| Training and corpus preparation | Included upstream | Remain in Python; not reimplemented in Go |

### Performance

Measured on **2026-09-11**, Windows 11 amd64, AMD Ryzen AI 7 PRO 350,
Go 1.27.1, Python 3.14.6, NumPy 2.5.1, with the pinned model and revision above.
Values are medians of five alternating Go/Python samples after a discarded
warm-up round; both engines use one thread.

| Input | Go, us/op | Python, us/op | Python time / Go time | Go allocations/op |
| --- | ---: | ---: | ---: | ---: |
| 23-byte English sentence | 1.72 | 11.51 | 6.70x | 1 |
| 1 KiB repeated English text | 6.47 | 84.91 | 13.13x | 2 |
| 100 KiB repeated English text | 420.53 | 7,499.74 | 17.83x | 2 |

These are warm, sequential **raw classification** microbenchmarks, not end-to-end
application throughput. Model loading, startup, I/O, ranking, probability
normalization, and concurrent workloads are excluded. Text is generated from
the same repeated English sentence for both engines. Results depend on the
machine, corpus, and language subset; no general speedup is guaranteed.
See [the reproducibility notes](docs/upstream-sync.md#performance-measurement)
for sample ranges and the command to rerun the comparison.

### Accuracy And Real Sentences

**Yes, upstream includes real multilingual sentence tests.** They include English,
French, German, and Russian examples, alongside shorter Slavic/Uzbek phrases,
Unicode regressions, restricted language sets, and empty or ambiguous inputs.
They are regression tests, not a representative held-out accuracy dataset.

| Check | Result |
| --- | --- |
| Agreement with live Python, raw predictions | 42/42 shared inputs |
| Agreement with live Python, normalized predictions including abstention | 42/42 shared inputs |
| Full raw and normalized label-score vectors | All shared cases pass the documented tolerances |
| Explicit expected-language assertions | 19/19 in both implementations |
| Unmodified upstream tests, executed under Python | 28 inference/CLI + 14 HTTP test instances pass |
| Native Go tests | Inference, CLI, HTTP, URL, model decoding, and concurrency checks |
| Representative held-out accuracy | Not measured |

The 19 assertions include duplicates, language restrictions, and `zxx`; only four
unrestricted natural-language labels are represented. **42/42 agreement is not
100% language-identification accuracy.** Go uses the same learned model, so this
port claims tested behavioral agreement, not better language accuracy. For
example, upstream classifies `hello world` as `fuv` with low confidence; Go
intentionally preserves that result.

## Development And Upstream Sync

```sh
go test ./...
go vet ./...
go test -race ./...
python scripts/compare_py3langid.py --check-upstream
```

Ordinary Go tests are offline and need no Python installation. The race detector
requires a supported platform and C toolchain. CI runs Windows/Linux Go tests,
Linux race checks, and a live comparison with the pinned Python checkout.

[testdata/py3langid_cases.json](testdata/py3langid_cases.json) records the upstream
revision, dependency baseline, model fingerprints, test inventory, and shared
inputs. Expected numerical results in
[testdata/py3langid_reference.json](testdata/py3langid_reference.json) are generated
by Python, never by the Go implementation.

A weekly and manually runnable workflow checks upstream `master` and fails when
it differs from the pin. **It detects drift; it does not silently update the
model or claim automatic compatibility.** Actions must be enabled in the hosted
repository for scheduled checks to run. The reviewed upgrade and model-conversion
procedure is in [docs/upstream-sync.md](docs/upstream-sync.md).

## License And Credits

The Go port, bundled model, and adapted regression tests are distributed under
the single [BSD 3-Clause LICENSE](LICENSE), retaining credits for **Marco Lui**,
**Ilya Pyshkin**, and **Adrien Barbaresi** (adbar). The original langid research
was by Marco Lui and Tim Baldwin. This project builds on the earlier Go port by
Ilya Pyshkin and tracks Adrien Barbaresi's py3langid project. Go module dependencies
retain their own licenses.