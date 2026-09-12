# Language Detector Comparison

Run: 2026-09-12T15:07:02.216107+00:00

1000 cases; 8 measured passes per engine; 1 full warm-up pass(es) discarded.

Selection: corpus languages supported by every engine. Source corpus: 1000 cases.
Included languages: ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh.
Excluded corpus languages: none.

| Engine | Pure Go | Supported languages | Accuracy | Startup (s) | Warm-up total (s) | Median pass wall (s) | Texts/s | Median RTT (ms) | p95 RTT (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cld3 | No (CGO) | 103 | 98.40% | 0.0474 | 0.6154 | 0.4546 | 2199.9 | 0.3994 | 0.5659 |
| langid | Yes | 139 | 99.20% | 0.4007 | 0.6579 | 0.4982 | 2007.0 | 0.4539 | 0.6523 |
| whatlanggo | Yes | 84 | 98.40% | 0.0394 | 0.7823 | 0.5724 | 1746.9 | 0.5286 | 0.8067 |
| lingua | Yes | 75 | 98.70% | 0.0634 | 11.1091 | 2.3587 | 424.0 | 2.0583 | 6.3081 |

## Pass Wall Times

Startup is launch-to-ready. Warm-up passes are timed separately and discarded from steady-state statistics. Each pass processes the full selected corpus.

| Phase | Pass | cld3 (s) | langid (s) | whatlanggo (s) | lingua (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Warm-up (discarded) | 1 | 0.6154 | 0.6579 | 0.7823 | 11.1091 |
| Measured | 1 | 0.4995 | 0.5053 | 0.5928 | 2.2629 |
| Measured | 2 | 0.4497 | 0.4861 | 0.5799 | 2.2500 |
| Measured | 3 | 0.4773 | 0.4593 | 0.5649 | 2.4361 |
| Measured | 4 | 0.4327 | 0.5320 | 0.5547 | 2.2319 |
| Measured | 5 | 0.4361 | 0.4912 | 0.6038 | 2.4267 |
| Measured | 6 | 0.4137 | 0.5548 | 0.7051 | 2.3982 |
| Measured | 7 | 0.4594 | 0.4848 | 0.5496 | 2.3793 |
| Measured | 8 | 0.4925 | 0.6200 | 0.5649 | 2.3381 |

## Confidence

| Engine | Macro accuracy | Reliable coverage | Accuracy when reliable |
| --- | ---: | ---: | ---: |
| cld3 | 98.40% | 98.80% | 98.68% |
| langid | 99.20% | 99.90% | 99.30% |
| whatlanggo | 98.40% | 80.40% | 100.00% |
| lingua | 98.70% | 100.00% | 98.70% |

## Short And Medium Text

| Group | Cases | cld3 accuracy | langid accuracy | whatlanggo accuracy | lingua accuracy | cld3 median RTT (ms) | langid median RTT (ms) | whatlanggo median RTT (ms) | lingua median RTT (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| short | 500 | 98.00% | 99.40% | 97.80% | 98.40% | 0.3874 | 0.4324 | 0.5140 | 1.9116 |
| medium | 500 | 98.80% | 99.00% | 99.00% | 99.00% | 0.4107 | 0.4757 | 0.5502 | 2.2125 |

## Per-Language Accuracy

| Language | Cases | cld3 | langid | whatlanggo | lingua |
| --- | ---: | ---: | ---: | ---: | ---: |
| ar | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| de | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| en | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| es | 50 | 100.00% | 100.00% | 98.00% | 100.00% |
| fr | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| hi | 50 | 100.00% | 98.00% | 92.00% | 96.00% |
| id | 50 | 84.00% | 96.00% | 100.00% | 80.00% |
| it | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| ja | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| ko | 50 | 96.00% | 100.00% | 100.00% | 100.00% |
| nl | 50 | 100.00% | 100.00% | 100.00% | 98.00% |
| pl | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| pt | 50 | 98.00% | 98.00% | 100.00% | 100.00% |
| ru | 50 | 98.00% | 100.00% | 92.00% | 100.00% |
| sv | 50 | 100.00% | 100.00% | 94.00% | 100.00% |
| th | 50 | 94.00% | 100.00% | 100.00% | 100.00% |
| tr | 50 | 100.00% | 100.00% | 96.00% | 100.00% |
| uk | 50 | 98.00% | 100.00% | 98.00% | 100.00% |
| vi | 50 | 100.00% | 100.00% | 100.00% | 100.00% |
| zh | 50 | 100.00% | 92.00% | 98.00% | 100.00% |

## Interpretation

- All worker processes stay alive for the entire run. Full warm-up passes are excluded, and engine order rotates each measured pass.
- Warm-up total is the sum of separately timed discarded full passes, not startup time. Warm-up and startup are excluded from measured pass times, throughput, and RTT statistics.
- Pass wall time includes the Python loop, JSON handling, TCP transport, and worker execution; texts/s uses its median. This is not isolated model CPU time.
- RTT runs from sending a pre-encoded request to receiving the complete response frame; Python response decoding is outside the RTT timer. p95 uses nearest rank.
- Startup is one observed launch-to-ready measurement, not a cold-cache benchmark. Lingua loads models lazily during warm-up. Startup is excluded from steady-state throughput.
- Accuracy counts each unique case once, including unreliable predictions. Every detector retains its full language set. No label aliases are applied by the harness. Changed predictions across measured passes fail the run.
- Supported-language counts count script variants once and exclude und/zxx non-language labels. Raw output-label lists and counts remain in summary.json; evaluation still compares exact labels.
- Pure Go reflects CGO_ENABLED in the matching build metadata: CLD3 uses native C++ through CGO; the other bundled adapters disable CGO. Custom binaries without that metadata are marked Unknown.
- The probability scales and reliability rules differ between engines. Reliable coverage and conditional accuracy are not calibration-equivalent metrics.
- This is a small, balanced FLORES-200 sentence subset, not a universal accuracy or production-throughput claim. Training-set overlap has not been audited.

Source corpus SHA256: `ec0ea263e5cdea41005cb2bc9fff1612e2b5a937c319a7c25162d2ad8b00e7a5`

See summary.json for environment, binary hashes, parameters, paired outcomes, and individual pass times; predictions.csv for every prediction; timings.csv for every measured request.
