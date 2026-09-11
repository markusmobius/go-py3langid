package py3langid

import (
	"fmt"
	"testing"
)

func TestClassifyWithEmbeddedDefaultModel(t *testing.T) {
	id, err := NewDefaultIdentifier()
	if err != nil {
		t.Fatal(err)
	}
	if len(id.Classes()) != 140 {
		t.Fatal("default model is not the pinned py3langid 0.4 model")
	}
	res, err := Classify("This is a short sentence in English.")
	if err != nil {
		t.Fatalf("classify: %v", err)
	}
	if res.Language != "en" {
		t.Fatalf("expected English, got %q", res.Language)
	}
}

func TestDefaultLanguageRestriction(t *testing.T) {
	t.Cleanup(func() {
		if err := SetLanguages(); err != nil {
			t.Error(err)
		}
	})
	if err := SetLanguages("en", "de"); err != nil {
		t.Fatal(err)
	}
	if err := SetLanguages("en", "invalid"); err == nil {
		t.Fatal("expected unsupported language error")
	}
	text := "This should be enough text."
	result, err := Classify(text)
	if err != nil {
		t.Fatal(err)
	}
	ranking, err := Rank(text)
	if err != nil {
		t.Fatal(err)
	}
	if len(ranking) != 2 || ranking[0] != result || result.Language != "en" || result.Score >= 0 {
		t.Fatalf("inconsistent shared default: result=%+v ranking=%v", result, ranking)
	}
	if err := SetLanguages(); err != nil {
		t.Fatal(err)
	}
	ranking, err = Rank(text)
	if err != nil || len(ranking) != 140 {
		t.Fatalf("reset: %d labels, error=%v", len(ranking), err)
	}
}

func ExampleIdentifier() {
	identifier, err := NewDefaultIdentifier(WithNormalizedProbabilities())
	if err != nil {
		panic(err)
	}
	result, err := identifier.IdentifyString("This text is in English.")
	if err != nil {
		panic(err)
	}
	fmt.Printf("%s %.6f\n", result.Language, result.Score)
	// Output: en 0.720258
}
