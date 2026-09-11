package py3langid

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"

	"path/filepath"

	"slices"
	"strings"
	"testing"
)

type py3Corpus struct {
	Commit        string `json:"commit"`
	Version       string `json:"version"`
	NumPy         string `json:"numpy"`
	ModelSHA256   string `json:"go_model_sha256"`
	BenchmarkText string `json:"benchmark_text"`
	Benchmarks    []struct {
		Name  string `json:"name"`
		Bytes int    `json:"bytes"`
	} `json:"benchmarks"`
	Cases []struct {
		Name          string   `json:"name"`
		Text          string   `json:"text"`
		Hex           string   `json:"hex"`
		AsBytes       bool     `json:"as_bytes"`
		TrimBytes     int      `json:"trim_bytes"`
		Languages     []string `json:"languages"`
		MinConfidence *float64 `json:"min_confidence"`
	} `json:"cases"`
}

type py3Reference struct {
	Commit      string   `json:"commit"`
	Version     string   `json:"version"`
	NumPy       string   `json:"numpy"`
	ModelSHA256 string   `json:"go_model_sha256"`
	Classes     []string `json:"classes"`
	NumFeatures int      `json:"num_features"`
	NumStates   int      `json:"num_states"`
	Samples     []struct {
		Name             string             `json:"name"`
		Encoded          []byte             `json:"encoded"`
		Raw              Result             `json:"raw"`
		Normalized       Result             `json:"normalized"`
		RawScores        map[string]float64 `json:"raw_scores"`
		NormalizedScores map[string]float64 `json:"normalized_scores"`
	} `json:"samples"`
}

func readPy3JSON(tb testing.TB, path string, target any) {
	tb.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		tb.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		tb.Fatal(err)
	}
}

func TestPy3LangIDParity(t *testing.T) {
	var corpus py3Corpus
	readPy3JSON(t, filepath.Join("testdata", "py3langid_cases.json"), &corpus)
	referencePath := os.Getenv("LANGID_PY3_REFERENCE")
	if referencePath == "" {
		referencePath = filepath.Join("testdata", "py3langid_reference.json")
	}
	var reference py3Reference
	readPy3JSON(t, referencePath, &reference)
	digest := sha256.Sum256(defaultModel)
	if reference.Commit != corpus.Commit || reference.Version != corpus.Version || reference.NumPy != corpus.NumPy ||
		reference.ModelSHA256 != corpus.ModelSHA256 || hex.EncodeToString(digest[:]) != corpus.ModelSHA256 {
		t.Fatal("Python reference, dependency baseline, or embedded model fingerprint is stale")
	}
	base, err := getDefaultIdentifier()
	if err != nil {
		t.Fatal(err)
	}
	if reference.NumFeatures != base.model.NumFeats || reference.NumStates != base.model.NumStates ||
		!slices.Equal(reference.Classes, base.model.Classes) {
		t.Fatal("Python and Go model metadata differ")
	}
	if len(corpus.Cases) == 0 || len(corpus.Cases) != len(reference.Samples) {
		t.Fatal("reference does not cover the shared corpus")
	}
	confidence := make(map[string]float64)
	for index, sample := range corpus.Cases {
		expected := reference.Samples[index]
		if expected.Name != sample.Name {
			t.Fatalf("reference case %d: expected %q, got %q", index, sample.Name, expected.Name)
		}
		t.Run(sample.Name, func(t *testing.T) {
			data := []byte(sample.Text)
			if sample.Hex != "" {
				data, err = hex.DecodeString(sample.Hex)
				if err != nil {
					t.Fatal(err)
				}
			}
			if sample.TrimBytes < 0 || sample.TrimBytes > len(data) {
				t.Fatal("invalid corpus truncation")
			}
			data = data[:len(data)-sample.TrimBytes]
			if encoded := encodePy3LangID(data); !bytes.Equal(encoded, expected.Encoded) {
				t.Fatalf("preprocessing differs: go=%x python=%x", encoded, expected.Encoded)
			}
			for _, normalized := range []bool{false, true} {
				options := []Option{}
				prediction, scores := expected.Raw, expected.RawScores
				absoluteTolerance, relativeTolerance := 1e-5, 1e-5
				if normalized {
					options = append(options, WithNormalizedProbabilities())
					if sample.MinConfidence != nil {
						options = append(options, WithMinConfidence(*sample.MinConfidence))
					}
					prediction, scores = expected.Normalized, expected.NormalizedScores
					absoluteTolerance, relativeTolerance = 2e-6, 1e-4
				}
				id, err := applyIdentifierOptions(newIdentifier(base.model), options)
				if err != nil {
					t.Fatal(err)
				}
				if err := id.SetLanguages(sample.Languages...); err != nil {
					t.Fatal(err)
				}
				var result Result
				var ranked []Result
				if sample.AsBytes {
					result, err = id.IdentifyBytes(data)
				} else {
					result, err = id.IdentifyString(string(data))
				}
				if err != nil {
					t.Fatal(err)
				}
				if result.Language != prediction.Language {
					t.Errorf("normalized=%t: label go=%s python=%s", normalized, result.Language, prediction.Language)
				}
				assertPy3Score(t, result.Score, prediction.Score, absoluteTolerance, relativeTolerance)
				if sample.AsBytes {
					ranked, err = id.RankBytes(data)
				} else {
					ranked, err = id.RankString(string(data))
				}
				if err != nil || len(ranked) != len(scores) || len(ranked) != len(id.Classes()) {
					t.Fatalf("ranking dimensions differ: %d vs %d (%v)", len(ranked), len(scores), err)
				}
				seen := make(map[string]bool)
				total := 0.0
				for position, entry := range ranked {
					want, exists := scores[entry.Language]
					if !exists || seen[entry.Language] {
						t.Fatalf("unexpected or duplicate label %q", entry.Language)
					}
					seen[entry.Language] = true
					assertPy3Score(t, entry.Score, want, absoluteTolerance, relativeTolerance)
					if position > 0 && ranked[position-1].Score < entry.Score {
						t.Fatal("ranking is not descending")
					}
					if normalized && (entry.Score < 0 || entry.Score > 1+1e-6) {
						t.Fatalf("invalid probability: %v", entry)
					}
					total += entry.Score
				}
				if result.Score != ranked[0].Score || (result.Language != "und" && result.Language != ranked[0].Language) {
					t.Fatal("classify and rank disagree")
				}
				if normalized {
					confidence[sample.Name] = result.Score
					if math.Abs(total-1) > 1e-6 {
						t.Fatalf("probabilities sum to %f", total)
					}
				}
			}
		})
	}
	if confidence["calibration_sqrt/ambiguous"] >= confidence["calibration_sqrt/clear"] ||
		confidence["calibration_sqrt/ambiguous"] >= 0.9 {
		t.Fatal("calibrated confidence does not reflect the upstream ambiguity assertion")
	}
	t.Run("set_languages_error_and_reset", func(t *testing.T) {
		id := newIdentifier(base.model)
		if err := id.SetLanguages("en", "de"); err != nil {
			t.Fatal(err)
		}
		if err := id.SetLanguages("xx_invalid"); err == nil {
			t.Fatal("accepted unknown language code")
		}
		if !slices.Equal(id.Classes(), []string{"de", "en"}) {
			t.Fatal("invalid restriction modified the active languages")
		}
		id.ResetLanguages()
		if !slices.Equal(id.Classes(), base.Classes()) {
			t.Fatal("reset did not restore the model labels")
		}
	})
	t.Run("min_confidence_validation", func(t *testing.T) {
		for _, threshold := range []float64{-0.1, 1.1, math.NaN(), math.Inf(1)} {
			if _, err := applyIdentifierOptions(newIdentifier(base.model), []Option{WithNormalizedProbabilities(), WithMinConfidence(threshold)}); err == nil {
				t.Fatalf("accepted invalid confidence threshold %f", threshold)
			}
		}
	})
}

func assertPy3Score(t *testing.T, got, want, absoluteTolerance, relativeTolerance float64) {
	t.Helper()
	if math.IsNaN(got) || math.IsInf(got, 0) || math.Abs(got-want) > absoluteTolerance+relativeTolerance*math.Abs(want) {
		t.Fatalf("score differs: go=%.10g python=%.10g difference=%.10g", got, want, got-want)
	}
}

func BenchmarkPy3LangID(b *testing.B) {
	var corpus py3Corpus
	readPy3JSON(b, filepath.Join("testdata", "py3langid_cases.json"), &corpus)
	id, err := NewDefaultIdentifier()
	if err != nil {
		b.Fatal(err)
	}
	for _, sample := range corpus.Benchmarks {
		b.Run(sample.Name, func(b *testing.B) {
			unit := corpus.BenchmarkText
			text := strings.Repeat(unit, (sample.Bytes+len(unit)-1)/len(unit))[:sample.Bytes]
			for range 3 {
				if _, err := id.IdentifyString(text); err != nil {
					b.Fatal(err)
				}
			}
			b.ReportAllocs()
			b.SetBytes(int64(len(text)))
			for b.Loop() {
				if _, err := id.IdentifyString(text); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

func TestPy3LangIDInference(t *testing.T) {
	id, err := LoadModel(filepath.Join("model", "py3langid.lidg"))
	if err != nil {
		t.Fatal(err)
	}
	if len(id.Classes()) != 140 {
		t.Fatalf("expected 140 distinct labels, got %d", len(id.Classes()))
	}
	samples := []struct {
		text string
		lang string
	}{
		{"This text is in English.", "en"},
		{"Test Unicode sur du texte en fran\u00e7ais", "fr"},
		{"CECI EST UN TEST EN FRAN\u00c7AIS", "fr"},
		{"DIES IST EIN DEUTSCHER TEXT", "de"},
		{"\u042d\u0422\u041e \u0420\u0423\u0421\u0421\u041a\u0418\u0419 \u0422\u0415\u041a\u0421\u0422 \u0414\u041b\u042f \u0422\u0415\u0421\u0422\u0410", "ru"},
		{"This is normal English text", "en"},
		{"NASA launched a SpaceX rocket", "en"},
		{"12345", "zxx"},
	}
	for _, sample := range samples {
		t.Run(sample.text, func(t *testing.T) {
			result, err := id.IdentifyString(sample.text)
			if err != nil || result.Language != sample.lang {
				t.Fatalf("expected %s, got %+v (%v)", sample.lang, result, err)
			}
			ranked, err := id.RankString(sample.text)
			if err != nil || len(ranked) != 140 || ranked[0] != result {
				t.Fatalf("rank does not agree with classify: %v", err)
			}
		})
	}
	result, err := id.IdentifyString(samples[0].text)
	if err != nil || math.Abs(result.Score-(-68.56228637695312)) > 0.001 {
		t.Fatalf("pinned Python raw score mismatch: %+v (%v)", result, err)
	}
	for _, text := range []string{"", "hi", "a"} {
		result, err := id.IdentifyString(text)
		if err != nil || result.Score != -math.MaxFloat32 {
			t.Fatalf("featureless input %q: %+v (%v)", text, result, err)
		}
	}
	normalized, err := applyIdentifierOptions(newIdentifier(id.model), []Option{WithNormalizedProbabilities()})
	if err != nil {
		t.Fatal(err)
	}
	ranked, err := normalized.RankString("")
	if err != nil {
		t.Fatal(err)
	}
	total := 0.0
	for _, result := range ranked {
		total += result.Score
	}
	if math.Abs(total-1) > 1e-6 || math.Abs(ranked[0].Score-2.0/142) > 1e-6 {
		t.Fatalf("featureless probability distribution: top=%+v total=%f", ranked[0], total)
	}
	if err := normalized.SetLanguages("sr"); err != nil {
		t.Fatal(err)
	}
	ranked, err = normalized.RankString("ovo je tekst za probu")
	if err != nil || len(ranked) != 1 || ranked[0].Language != "sr" || math.Abs(ranked[0].Score-1) > 1e-6 {
		t.Fatalf("aliased subset: %+v (%v)", ranked, err)
	}
	if _, err := applyIdentifierOptions(newIdentifier(id.model), []Option{WithMinConfidence(0.5)}); err == nil {
		t.Fatal("accepted min confidence without normalization")
	}
}
