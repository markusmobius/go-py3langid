package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"

	"github.com/RadhiFadlillah/whatlanggo"
	"github.com/markusmobius/go-py3langid/benchmarks/language-detection/internal/workerprotocol"
)

type languageInput struct {
	Text string
}

type languageOutput struct {
	Iso639      string
	Probability float32
	IsReliable  bool
}

func languageCode(language whatlanggo.Lang) string {
	if code := language.Iso6391(); code != "" {
		return code
	}
	return language.Iso6393()
}

func supportedLanguages() []string {
	languages := make([]string, 0, int(whatlanggo.Zul)+1)
	for language := whatlanggo.Afr; language <= whatlanggo.Zul; language++ {
		languages = append(languages, languageCode(language))
	}
	sort.Strings(languages)
	return languages
}

func classify(command string) ([]byte, error) {
	var input languageInput
	if err := json.Unmarshal([]byte(command), &input); err != nil {
		return nil, err
	}
	result := whatlanggo.Detect(input.Text)
	return json.Marshal(languageOutput{
		Iso639:      languageCode(result.Lang),
		Probability: float32(result.Confidence),
		IsReliable:  result.IsReliable(),
	})
}

func run(arguments []string) error {
	if len(arguments) == 2 && arguments[1] == "--languages" {
		return json.NewEncoder(os.Stdout).Encode(supportedLanguages())
	}
	if err := workerprotocol.ValidateArguments(arguments); err != nil {
		return err
	}
	return workerprotocol.Serve(arguments, classify)
}

func main() {
	if err := run(os.Args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
