package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/markusmobius/go-py3langid/benchmarks/language-detection/internal/workerprotocol"
	"github.com/pemistahl/lingua-go"
)

type languageInput struct {
	Text string
}

type languageOutput struct {
	Iso639      string
	Probability float32
	IsReliable  bool
}

func supportedLanguages() []string {
	languages := make([]string, 0, len(lingua.AllLanguages()))
	for _, language := range lingua.AllLanguages() {
		languages = append(languages, strings.ToLower(language.IsoCode639_1().String()))
	}
	sort.Strings(languages)
	return languages
}

func classify(detector lingua.LanguageDetector, command string) ([]byte, error) {
	var input languageInput
	if err := json.Unmarshal([]byte(command), &input); err != nil {
		return nil, err
	}
	values := detector.ComputeLanguageConfidenceValues(input.Text)
	var result languageOutput
	if len(values) > 0 {
		result.Probability = float32(values[0].Value())
		result.IsReliable = len(values) == 1 || values[0].Value() > values[1].Value()
		if result.IsReliable {
			result.Iso639 = strings.ToLower(values[0].Language().IsoCode639_1().String())
		}
	}
	return json.Marshal(result)
}

func run(arguments []string) error {
	if len(arguments) == 2 && arguments[1] == "--languages" {
		return json.NewEncoder(os.Stdout).Encode(supportedLanguages())
	}
	if err := workerprotocol.ValidateArguments(arguments); err != nil {
		return err
	}
	detector := lingua.NewLanguageDetectorBuilder().FromAllLanguages().Build()
	return workerprotocol.Serve(arguments, func(command string) ([]byte, error) {
		return classify(detector, command)
	})
}

func main() {
	if err := run(os.Args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
