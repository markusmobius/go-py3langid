//go:build linux && cgo

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/jmhodges/gocld3/cld3"
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

func supportedLanguages() []string {
	languages := strings.Fields(
		"eo co eu ta de mt ps te su uz zh-Latn ne " +
			"nl sw sq hmn ja no mn so ko kk sl ig " +
			"mr th zu ml hr bs lo sd cy hy uk pt " +
			"lv iw cs vi jv be km mk tr fy am zh " +
			"da sv fi ht af la id fil sm ca el ka " +
			"sr it sk ru ru-Latn bg ny fa haw gl et " +
			"ms gd bg-Latn ha is ur mi hi bn hi-Latn fr " +
			"yi hu xh my tg ro ar lb el-Latn st ceb " +
			"kn az si ky mg en gu es pl ja-Latn ga lt sn yo pa ku",
	)
	sort.Strings(languages)
	return languages
}

func classify(identifier cld3.LanguageIdentifier, command string) ([]byte, error) {
	var input languageInput
	if err := json.Unmarshal([]byte(command), &input); err != nil {
		return nil, err
	}
	result := identifier.FindLanguage(input.Text)
	return json.Marshal(languageOutput{
		Iso639:      result.Language,
		Probability: result.Probability,
		IsReliable:  result.IsReliable,
	})
}

func run(arguments []string) error {
	if len(arguments) == 2 && arguments[1] == "--languages" {
		return json.NewEncoder(os.Stdout).Encode(supportedLanguages())
	}
	if err := workerprotocol.ValidateArguments(arguments); err != nil {
		return err
	}
	identifier, err := cld3.NewLanguageIdentifier(0, 4000)
	if err != nil {
		return err
	}
	defer cld3.FreeLanguageIdentifier(identifier)
	return workerprotocol.Serve(arguments, func(command string) ([]byte, error) {
		return classify(identifier, command)
	})
}

func main() {
	if err := run(os.Args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
