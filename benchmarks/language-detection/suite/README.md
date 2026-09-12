# FLORES-200 Short And Medium Sentences

This is a reproducible subset of the established [FLORES-200 evaluation benchmark](https://github.com/facebookresearch/flores/tree/main/flores200), using its public `devtest` split. The benchmark consists of professionally translated sentences from Wikipedia, Wikinews, and Wikivoyage. It was developed for translation evaluation; here its language labels provide language-identification ground truth.

## Selection

- 20 selected languages supported by all four engines: Arabic, Chinese (Simplified), Dutch, English, French, German, Hindi, Indonesian, Italian, Japanese, Korean, Polish, Portuguese, Russian, Spanish, Swedish, Thai, Turkish, Ukrainian, and Vietnamese. This is not the full FLORES-200 language inventory.
- 25 short sentences (20-200 UTF-8 bytes) and 25 medium sentences (201-1,000 bytes) per language: 1,000 cases in total. Byte-based bins provide enough complete sentences across scripts without language-specific cutoffs; they are not word-count bins.
- Maximum 4,000 UTF-8 bytes, matching the CLD3 wrapper's input limit. No selected text is truncated, joined, translated, or otherwise rewritten.
- Exact duplicate text within a language/group is excluded. Candidates are sorted by `SHA256("20260912:<case_id>")`, taking the first 25. Predictions never influence selection.
- Original 1-based line numbers and language identifiers are retained. The same source sentence can occur in more than one translated language. Length groups and selections are determined separately per language, so this is not a fully sentence-aligned translation subset.

`flores200.jsonl` contains the input cases. `manifest.json` records source and suite SHA256s, eligibility counts, label mappings, selection rules, and attribution. `SOURCE_README.txt` is copied unchanged from the upstream archive.

Regenerate from the comparison directory:

```sh
python3 prepare.py suite
```

An already downloaded archive can be supplied with `--archive PATH`; its pinned SHA256 is still verified. Generation uses only the Python standard library. Corpus creation requires internet access unless the archive is cached or supplied; running the benchmark uses the checked-in subset offline.

## License And Attribution

The sentence data and this selection are distributed under [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/), the original FLORES-200 license. Retain attribution and the same license when redistributing or adapting the data. Selection, grouping, and metadata have been added; sentence text is unchanged. No endorsement by the original authors is implied.

Attribution: **NLLB Team et al. (2022), _No Language Left Behind: Scaling Human-Centered Machine Translation_.** See the upstream project's [citation and credits](https://github.com/facebookresearch/flores/tree/main/flores200#citation). The copied source README describes the original archive layout.

This small balanced sample measures performance on clean, edited sentences in these 20 languages. It does not establish accuracy on every supported language, very short chat messages, code-switching, HTML, or noisy text. Possible overlap with any detector's training data has not been audited. Translations of the same source sentences are correlated; they are not 1,000 independent semantic examples.