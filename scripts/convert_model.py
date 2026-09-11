#!/usr/bin/env python3
"""Convert a py3langid NumPy/LZMA model into the Go runtime format."""

from __future__ import annotations

import argparse
import gzip
import io
import lzma
import struct
from pathlib import Path

PY3_MAGIC = b"LIDG2\x00"


def write_py3langid_model(input_path: Path, out_path: Path):
    import numpy as np

    with np.load(io.BytesIO(lzma.decompress(input_path.read_bytes())), allow_pickle=False) as data:
        required = {"ptc", "pc", "classes", "nextmove", "nextmove_row", "out_feat"}
        if not required.issubset(data.files):
            raise ValueError("unsupported py3langid model layout")
        priors = data["pc"].astype("<f4")
        weights = data["ptc"].astype("<f4")
        classes = data["classes"].tolist()
        transitions = data["nextmove"].astype("<u4").ravel()
        rows = data["nextmove_row"].astype("<u4").ravel()
        outputs = data["out_feat"].astype("<i4").ravel()

    num_feats, num_langs = weights.shape
    if len(priors) != num_langs or len(classes) != num_langs:
        raise ValueError("inconsistent language dimensions")
    if len(transitions) % 256 or len(outputs) != len(rows):
        raise ValueError("inconsistent DFA dimensions")
    num_rows = len(transitions) // 256
    if transitions.max() >= len(rows) or rows.max() >= num_rows:
        raise ValueError("invalid DFA transition or row")
    if outputs.min() < -1 or outputs.max() >= num_feats:
        raise ValueError("invalid DFA output")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as target:
        target.write(PY3_MAGIC)
        with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as stream:
            stream.write(struct.pack("<IIII", num_feats, num_langs, len(rows), num_rows))
            for values in (transitions, rows, outputs, priors, weights):
                stream.write(values.tobytes(order="C"))
            for label in classes:
                encoded = label.encode("utf-8")
                if not encoded or len(encoded) > 256:
                    raise ValueError("invalid class label length")
                stream.write(struct.pack("<H", len(encoded)))
                stream.write(encoded)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path, help="py3langid .npz.xz model")
    p.add_argument("output", type=Path, help="output .lidg file")
    args = p.parse_args()

    write_py3langid_model(args.input, args.output)


if __name__ == "__main__":
    main()
