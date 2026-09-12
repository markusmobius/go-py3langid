package main

import (
	"encoding/json"
	"testing"

	"github.com/markusmobius/go-py3langid"
)

func TestClassifyContract(test *testing.T) {
	identifier, err := py3langid.NewDefaultIdentifier(py3langid.WithNormalizedProbabilities())
	if err != nil {
		test.Fatal(err)
	}
	for _, entry := range []struct {
		text     string
		language string
		reliable bool
	}{
		{"This text is in English.", "en", true},
		{"Guten Tag, wie geht es?", "de", true},
		{"hello world", "fuv", false},
	} {
		test.Run(entry.text, func(test *testing.T) {
			command, err := json.Marshal(languageInput{Text: entry.text})
			if err != nil {
				test.Fatal(err)
			}
			payload, err := classify(identifier, string(command))
			if err != nil {
				test.Fatal(err)
			}
			var result languageOutput
			if err := json.Unmarshal(payload, &result); err != nil {
				test.Fatal(err)
			}
			if result.Iso639 != entry.language || result.IsReliable != entry.reliable {
				test.Fatalf("unexpected prediction: %+v", result)
			}
			if result.Probability < 0 || result.Probability > 1 {
				test.Fatalf("probability outside [0, 1]: %v", result.Probability)
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(payload, &fields); err != nil {
				test.Fatal(err)
			}
			if len(fields) != 3 || fields["Iso639"] == nil || fields["Probability"] == nil || fields["IsReliable"] == nil {
				test.Fatalf("unexpected output fields: %s", payload)
			}
		})
	}
}
