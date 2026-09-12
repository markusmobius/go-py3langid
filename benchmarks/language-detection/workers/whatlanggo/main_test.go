package main

import (
	"encoding/json"
	"slices"
	"testing"

	"github.com/RadhiFadlillah/whatlanggo"
)

func TestClassifyContract(test *testing.T) {
	for _, text := range []string{"This text is in English.", "Guten Tag, wie geht es?", "", "12345", "\u65e5\u672c\u8a9e\n\t\"quoted\""} {
		test.Run(text, func(test *testing.T) {
			native := whatlanggo.Detect(text)
			command, err := json.Marshal(languageInput{Text: text})
			if err != nil {
				test.Fatal(err)
			}
			for range 2 {
				payload, err := classify(string(command))
				if err != nil {
					test.Fatal(err)
				}
				var result languageOutput
				if err := json.Unmarshal(payload, &result); err != nil {
					test.Fatal(err)
				}
				expected := languageOutput{languageCode(native.Lang), float32(native.Confidence), native.IsReliable()}
				if result != expected {
					test.Fatalf("prediction = %+v, want %+v", result, expected)
				}
				var fields map[string]json.RawMessage
				if err := json.Unmarshal(payload, &fields); err != nil {
					test.Fatal(err)
				}
				if len(fields) != 3 || fields["Iso639"] == nil || fields["Probability"] == nil || fields["IsReliable"] == nil {
					test.Fatalf("unexpected output fields: %s", payload)
				}
			}
		})
	}
	for _, command := range []string{"{", `{"Text":3}`} {
		if _, err := classify(command); err == nil {
			test.Fatalf("accepted malformed command: %s", command)
		}
	}
}

func TestSupportedLanguages(test *testing.T) {
	languages := supportedLanguages()
	if len(languages) != 84 || !slices.IsSorted(languages) || slices.Contains(languages, "") {
		test.Fatalf("unexpected language coverage: %v", languages)
	}
	for _, code := range []string{"en", "de", "ja", "ko", "th", "zh", "ceb"} {
		if !slices.Contains(languages, code) {
			test.Errorf("missing supported language %q", code)
		}
	}
}
