//go:build linux && cgo

package main

import (
	"encoding/json"
	"slices"
	"testing"

	"github.com/jmhodges/gocld3/cld3"
)

func TestClassifyContract(test *testing.T) {
	identifier, err := cld3.NewLanguageIdentifier(0, 4000)
	if err != nil {
		test.Fatal(err)
	}
	defer cld3.FreeLanguageIdentifier(identifier)
	for _, text := range []string{"This text is in English.", "Guten Tag, wie geht es?", "", "\u65e5\u672c\u8a9e\n\t\"quoted\""} {
		test.Run(text, func(test *testing.T) {
			native := identifier.FindLanguage(text)
			command, err := json.Marshal(languageInput{Text: text})
			if err != nil {
				test.Fatal(err)
			}
			for range 2 {
				payload, err := classify(identifier, string(command))
				if err != nil {
					test.Fatal(err)
				}
				var result languageOutput
				if err := json.Unmarshal(payload, &result); err != nil {
					test.Fatal(err)
				}
				expected := languageOutput{native.Language, native.Probability, native.IsReliable}
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
		if _, err := classify(identifier, command); err == nil {
			test.Fatalf("accepted malformed command: %s", command)
		}
	}
}

func TestSupportedLanguages(test *testing.T) {
	languages := supportedLanguages()
	if len(languages) != 109 || !slices.IsSorted(languages) || slices.Contains(languages, "") {
		test.Fatalf("unexpected language coverage: %v", languages)
	}
	for _, code := range []string{"en", "de", "ja", "ko", "th", "zh", "iw", "zh-Latn"} {
		if !slices.Contains(languages, code) {
			test.Errorf("missing supported language %q", code)
		}
	}
}
