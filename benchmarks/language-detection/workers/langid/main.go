package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/markusmobius/go-py3langid"
	"github.com/markusmobius/go-py3langid/benchmarks/language-detection/internal/workerprotocol"
)

const reliableProbability = 0.5

type languageInput struct {
	Text string
}

type languageOutput struct {
	Iso639      string
	Probability float32
	IsReliable  bool
}

func classify(identifier *py3langid.Identifier, command string) ([]byte, error) {
	var input languageInput
	if err := json.Unmarshal([]byte(command), &input); err != nil {
		return nil, err
	}
	result, err := identifier.IdentifyString(input.Text)
	if err != nil {
		return nil, err
	}
	return json.Marshal(languageOutput{
		Iso639:      result.Language,
		Probability: float32(result.Score),
		IsReliable:  result.Score >= reliableProbability,
	})
}

func run(arguments []string) error {
	languagesOnly := len(arguments) == 2 && arguments[1] == "--languages"
	if !languagesOnly {
		if err := workerprotocol.ValidateArguments(arguments); err != nil {
			return err
		}
	}
	identifier, err := py3langid.NewDefaultIdentifier(py3langid.WithNormalizedProbabilities())
	if err != nil {
		return err
	}
	if languagesOnly {
		return json.NewEncoder(os.Stdout).Encode(identifier.Classes())
	}
	handle := func(command string) ([]byte, error) {
		return classify(identifier, command)
	}
	return workerprotocol.Serve(arguments, handle)
}

func main() {
	if err := run(os.Args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
