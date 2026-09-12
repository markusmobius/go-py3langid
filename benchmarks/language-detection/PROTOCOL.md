# Worker Protocol

This is the application worker protocol implemented by all four source-built adapters: [CLD3](workers/cld3/main.go), [go-py3langid](workers/langid/main.go), [Whatlanggo](workers/whatlanggo/main.go), and [Lingua](workers/lingua/main.go). They share one [Go module](workers/go.mod) and the same [event-loop implementation](internal/workerprotocol/worker.go). It is not LSP, HTTP, JSON-RPC, or newline-delimited JSON over TCP. The benchmark uses TCP; all workers also implement the legacy file mode for compatibility.

## Classification Payload

The command is a UTF-8 JSON object:

```json
{"Text":"This text is in English."}
```

The classification result is a UTF-8 JSON object with exactly these fields:

```json
{"Iso639":"en","Probability":0.7202585,"IsReliable":true}
```

`Iso639` is the detector's label. `Probability` is a number in [0, 1], serialized from a Go `float32`; `IsReliable` is a Boolean. CLD3 returns its native probability and reliability. The langid worker requests calibrated py3langid probabilities, converts the score to `float32` for serialization, and tests the original score against 0.5 for reliability. It keeps the predicted label below the threshold rather than changing it to `und`.

Whatlanggo returns its native confidence and `IsReliable()`, using its ISO 639-1 code where available and ISO 639-3 otherwise. Lingua uses all languages, default high accuracy, and zero minimum relative distance. It computes confidence once, returns the uniquely leading language's lowercase ISO 639-1 code, and sets `IsReliable` according to that default winner rule. A tie returns an empty label and false reliability. No additional confidence threshold is applied. Probability scales and reliability rules are not equivalent across engines.

The benchmark does not rewrite text or normalize labels. Newlines, tabs, quotes, and Unicode must be encoded through JSON, not inserted as unescaped delimiters.

## Language Coverage

`<executable> --languages` writes a JSON array of supported output labels to stdout and exits without opening a protocol connection. It does not restrict the detector. The harness intersects these arrays with the corpus's expected labels, preserves selected cases unchanged, and records included/excluded languages before classification.

CLD3's binding has no coverage API, so its adapter records the 109 labels from the pinned [TaskContextParams::kLanguageNames](https://github.com/jmhodges/gocld3/blob/cc40e88f75052db19488738560c837a06fad0162/cld3/task_context_params.cc). Update that list when changing the CLD3 model. The other adapters enumerate their model classes or upstream language enums. Legacy aliases such as CLD3's `iw` and script variants such as `zh-Latn` remain unchanged for evaluation.

The report's supported-language count groups script variants and excludes `und` and `zxx`. Raw output-label lists and counts are retained separately in the summary. This counting rule never changes the labels used for corpus selection or accuracy scoring.

## TCP Connection

The controller first binds a listening socket on IPv4 loopback and launches:

```text
<executable> <port> <parent_pid> <ready_guid>
```

The **worker connects to the controller**, not the other way around. `parent_pid` is retained for command-line compatibility but is not used for parent-liveness monitoring. Each detector is initialized once before readiness and reused for all requests. Lingua retains its default lazy model loading, so model loading occurs during discarded warm-up requests rather than launch-to-ready timing. No authentication or encryption is provided; keep the listener on loopback and use trusted local processes.

Every message in either direction has this frame:

| Offset | Size | Value |
| --- | --- | --- |
| 0 | 4 bytes | Unsigned big-endian UTF-8 JSON payload length, excluding the header |
| 4 | 4 bytes | Unsigned big-endian sender chunk-size field |
| 8 | Declared length | UTF-8 JSON payload |

The usual chunk-size value is `1048576` (1 MiB). It is **not** an extra payload length and does not introduce per-chunk headers, separators, or padding. TCP may split or combine writes arbitrarily; read exactly 8 header bytes, then exactly the declared payload length. All workers and the Python harness reject zero-length messages, zero chunk sizes, and messages larger than 8 MiB.

### Ready Handshake

The worker sends this envelope, with the command-line ready token echoed:

```json
{"MType":0,"GUID":"ready-token","Content":""}
```

### Request

The controller sends a unique correlation ID and a **JSON string containing the classification command**:

```json
{"GUID":"1","Command":"{\"Text\":\"This text is in English.\"}"}
```

`Command` is not an embedded JSON object. Serialize the inner object and then serialize the outer envelope to preserve escaping.

### Response

The worker echoes the request ID and returns a **JSON string containing the classification result**:

```json
{"MType":1,"GUID":"1","Content":"{\"Iso639\":\"en\",\"Probability\":0.7202585,\"IsReliable\":true}"}
```

Parse the envelope, check `MType` and `GUID`, then parse `Content`. The harness uses one outstanding request per worker and reuses the same connection for all warm-up and measured passes. There is no delimiter after the frame and no connection restart per request.

Closing the connection ends a session. There is no shutdown message, heartbeat, or cancellation opcode. The controller applies socket/startup timeouts, terminates its child processes on cleanup, and captures stdout/stderr separately from protocol traffic.

## File Mode

With no arguments, a worker prints `ready` followed by LF to stdout. Each stdin line is:

```text
<classification JSON><TAB><output filename><LF>
```

`<TAB>` and `<LF>` mean literal delimiter bytes, not those printed strings. The file contains classification-result JSON whose **UTF-8 bytes have each been XORed with `0xFF`**. XOR the bytes with `0xFF` again, then decode UTF-8 and parse JSON. This is obfuscation, not encryption.

The worker prints `ok` after writing the file, or `error` if writing fails. Requests can be repeated through the same process. EOF or a line without the two tab-separated fields ends the loop. Filenames cannot contain tabs or newlines; whitespace at their edges is trimmed. File mode is not used in performance measurements, so per-request filesystem I/O is excluded.

## Error Behavior And Scope

All workers return errors for malformed JSON or invalid frames. A protocol failure aborts the benchmark rather than counting as an incorrect language prediction.

The CLD3 worker creates one `NewLanguageIdentifier(0, 4000)` per process, reuses it for every request, and frees it on exit. The other workers do not impose that same input cap, but every included sentence is at most 1,000 UTF-8 bytes. Every engine's full language set stays enabled throughout the comparison.