#!/usr/bin/env python3
"""Prepare reproducible local inputs for the language engine comparison."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
BIN = ROOT / "bin"
WORKERS = {
    "cld3": ("goCld3", "1"),
    "langid": ("goLangId", "0"),
    "whatlanggo": ("goWhatlanggo", "0"),
    "lingua": ("goLingua", "0"),
}
PROTOBUF_VERSION = "3.17.3"
PROTOBUF_URL = (
    f"https://github.com/protocolbuffers/protobuf/releases/download/v{PROTOBUF_VERSION}/"
    f"protobuf-all-{PROTOBUF_VERSION}.tar.gz"
)
PROTOBUF_SHA256 = "77ad26d3f65222fd96ccc18b055632b0bfedf295cb748b712a98ba1ac0b704b2"
PROTOBUF_PREFIX = ROOT / ".cache" / f"protobuf-{PROTOBUF_VERSION}-install"
FLORES_URL = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"
FLORES_SHA256 = "b8b0b76783024b85797e5cc75064eb83fc5288b41e9654dabc7be6ae944011f6"
LANGUAGES = {
    "ar": "arb_Arab", "de": "deu_Latn", "en": "eng_Latn", "es": "spa_Latn",
    "fr": "fra_Latn", "hi": "hin_Deva", "id": "ind_Latn", "it": "ita_Latn",
    "ja": "jpn_Jpan", "ko": "kor_Hang", "nl": "nld_Latn", "pl": "pol_Latn",
    "pt": "por_Latn", "ru": "rus_Cyrl", "sv": "swe_Latn", "th": "tha_Thai",
    "tr": "tur_Latn", "uk": "ukr_Cyrl", "vi": "vie_Latn", "zh": "zho_Hans",
}
LENGTH_GROUPS = {"short": (20, 200), "medium": (201, 1000)}
DEFAULT_SEED = 20260912


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_archive(url, expected_sha256, provided=None):
    archive = provided or ROOT / ".cache" / Path(urlsplit(url).path).name
    if provided is None and not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(archive.suffix + ".part")
        try:
            print(f"Downloading {url}", flush=True)
            with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if sha256_file(temporary) != expected_sha256:
                raise ValueError(f"SHA256 mismatch for {url}")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256_file(archive) != expected_sha256:
        raise ValueError(f"SHA256 mismatch for {archive}")
    return archive


def build_workers(go_executable):
    if platform.system() != "Linux":
        raise ValueError("build the workers on Linux using bash benchmarks/language-detection/run.sh")
    go_host = json.loads(subprocess.run(
        [go_executable, "env", "-json", "GOHOSTOS", "GOHOSTARCH"],
        capture_output=True, text=True, check=True,
    ).stdout)
    if go_host["GOHOSTOS"] != "linux":
        raise ValueError("Go must use a native Linux toolchain")
    BIN.mkdir(parents=True, exist_ok=True)
    directory = ROOT / "workers"
    common_sources = [
        REPOSITORY / "go.mod", REPOSITORY / "go.sum", ROOT / "run.sh", ROOT / "prepare.py",
        directory / "go.mod", directory / "go.sum",
        *sorted((ROOT / "internal" / "workerprotocol").glob("*.go")),
    ]
    for engine, (name, cgo) in WORKERS.items():
        worker_directory = directory / engine
        executable = BIN / name
        environment = os.environ.copy()
        environment.update(CGO_ENABLED=cgo, GOOS="linux", GOARCH=go_host["GOHOSTARCH"], GOWORK="off")
        environment.update(PKG_CONFIG_PATH="", PKG_CONFIG_LIBDIR=str(PROTOBUF_PREFIX / "lib" / "pkgconfig"))
        command = [go_executable, "build", "-mod=readonly", "-trimpath", "-o", str(executable), f"./{engine}"]
        subprocess.run(command, cwd=directory, env=environment, check=True)
        build_info = subprocess.run(
            [go_executable, "version", "-m", str(executable)],
            cwd=directory, env=environment, capture_output=True, text=True, check=True,
        ).stdout
        sources = [*common_sources, *sorted(worker_directory.glob("*.go"))]
        metadata = {
            "engine": engine,
            "sha256": sha256_file(executable),
            "command": command,
            "environment": {key: environment[key] for key in (
                "CGO_ENABLED", "GOOS", "GOARCH", "GOWORK", "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR",
            )},
            "go_build_info": build_info,
            "source_sha256": {path.relative_to(REPOSITORY).as_posix(): sha256_file(path) for path in sources},
        }
        if cgo == "1":
            metadata["protobuf"] = {
                "version": PROTOBUF_VERSION, "source": PROTOBUF_URL, "archive_sha256": PROTOBUF_SHA256,
                "static_library_sha256": sha256_file(PROTOBUF_PREFIX / "lib" / "libprotobuf.a"),
            }
            metadata["cxx_compiler"] = subprocess.run(
                [environment.get("CXX", "g++"), "--version"],
                env=environment, capture_output=True, text=True, check=True,
            ).stdout.strip()
        executable.with_name(executable.name + ".build.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8",
        )
        print(build_info.rstrip())
        print(f"Built {executable}")
        print(f"SHA256: {metadata['sha256']}")


def select_sentences(sentences, language, source_language, per_group, seed):
    selected = []
    eligible_counts = {}
    for group, (minimum, maximum) in LENGTH_GROUPS.items():
        candidates = []
        seen = set()
        for line_number, text in enumerate(sentences, start=1):
            byte_length = len(text.encode("utf-8"))
            if not minimum <= byte_length <= maximum or byte_length > 4000 or text in seen:
                continue
            seen.add(text)
            candidates.append({
                "id": f"flores200-devtest-{source_language}-{line_number:04d}",
                "language": language,
                "length_group": group,
                "source_line": line_number,
                "text": text,
            })
        eligible_counts[group] = len(candidates)
        if len(candidates) < per_group:
            raise ValueError(f"{language}/{group}: only {len(candidates)} eligible sentences")
        candidates.sort(key=lambda case: hashlib.sha256(
            f"{seed}:{case['id']}".encode("utf-8")
        ).digest())
        selected.extend(candidates[:per_group])
    return selected, eligible_counts


def prepare_suite(archive_path, output_directory, per_group, seed):
    archive_path = checked_archive(FLORES_URL, FLORES_SHA256, archive_path)
    cases = []
    eligible_counts = {}
    prefix = "./flores200_dataset"
    with tarfile.open(archive_path) as archive:
        for language, source_language in LANGUAGES.items():
            member = archive.getmember(f"{prefix}/devtest/{source_language}.devtest")
            with archive.extractfile(member) as source:
                sentences = source.read().decode("utf-8").splitlines()
            if len(sentences) != 1012:
                raise ValueError(f"{source_language}: expected 1012 devtest sentences")
            selected, counts = select_sentences(sentences, language, source_language, per_group, seed)
            cases.extend(selected)
            eligible_counts[language] = counts
        with archive.extractfile(f"{prefix}/README") as source:
            source_readme = source.read()
    output_directory.mkdir(parents=True, exist_ok=True)
    suite_path = output_directory / "flores200.jsonl"
    with suite_path.open("w", encoding="utf-8", newline="\n") as output:
        for case in cases:
            output.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_directory / "SOURCE_README.txt").write_bytes(source_readme)
    manifest = {
        "name": "FLORES-200 short/medium language identification subset",
        "source": FLORES_URL,
        "source_sha256": FLORES_SHA256,
        "source_project": "https://github.com/facebookresearch/flores/tree/main/flores200",
        "split": "devtest",
        "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": "NLLB Team et al. (2022), No Language Left Behind: Scaling Human-Centered Machine Translation",
        "selection": "First N unique full sentences per language/group ordered by SHA256(seed:case_id); text unchanged",
        "seed": seed,
        "per_language_per_group": per_group,
        "length_groups_utf8_bytes": LENGTH_GROUPS,
        "maximum_utf8_bytes": 4000,
        "languages": LANGUAGES,
        "eligible_counts": eligible_counts,
        "sample_count": len(cases),
        "suite_sha256": sha256_file(suite_path),
    }
    (output_directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(cases)} sentences: {len(LANGUAGES)} languages x 2 groups x {per_group}")
    print(f"Suite SHA256: {manifest['suite_sha256']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build all four native Linux workers after run.sh prepares Protobuf")
    build.add_argument("--go", default="go", help="Go executable (Go 1.25 or newer)")
    commands.add_parser("protobuf", help="download and verify the pinned Protobuf source archive")
    suite = commands.add_parser("suite", help="select the labeled FLORES-200 devtest sentences")
    suite.add_argument("--archive", type=Path, help="use an already downloaded archive")
    suite.add_argument("--output-directory", type=Path, default=ROOT / "suite")
    suite.add_argument("--per-group", type=int, default=25)
    suite.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    if args.command == "build":
        build_workers(args.go)
    elif args.command == "protobuf":
        print(f"Verified Protobuf source: {checked_archive(PROTOBUF_URL, PROTOBUF_SHA256)}")
    else:
        if args.per_group < 1:
            parser.error("--per-group must be positive")
        prepare_suite(args.archive, args.output_directory, args.per_group, args.seed)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)