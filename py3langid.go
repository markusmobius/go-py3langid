// Package py3langid implements the py3langid language identification runtime
// in pure Go. Its embedded model recognizes 139 languages plus zxx, with
// preprocessing, scoring, and confidence calibration tested against Python.
// Model training remains in the upstream Python project.
package py3langid

import (
	"fmt"
	"math"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"unicode"
	"unicode/utf8"

	"github.com/markusmobius/go-py3langid/internal/modelio"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

// Identifier classifies text by language using a pre-trained model.
type Identifier struct {
	model         *modelio.Model
	runtime       atomic.Pointer[identifierRuntime]
	normProbs     bool
	minConfidence *float64
}

// Option configures an identifier at construction time.
type Option func(*Identifier) error

// WithNormalizedProbabilities returns probabilities instead of raw log scores.
// Py3langid models use their byte-length temperature calibration.
func WithNormalizedProbabilities() Option {
	return func(id *Identifier) error {
		id.normProbs = true
		return nil
	}
}

// WithMinConfidence returns "und" for predictions below the threshold.
// It requires WithNormalizedProbabilities.
func WithMinConfidence(threshold float64) Option {
	return func(id *Identifier) error {
		if math.IsNaN(threshold) || threshold < 0 || threshold > 1 {
			return fmt.Errorf("minimum confidence must be between 0 and 1")
		}
		id.minConfidence = &threshold
		return nil
	}
}

func applyIdentifierOptions(id *Identifier, options []Option) (*Identifier, error) {
	for _, option := range options {
		if option == nil {
			return nil, fmt.Errorf("identifier option is nil")
		}
		if err := option(id); err != nil {
			return nil, err
		}
	}
	if id.minConfidence != nil && !id.normProbs {
		return nil, fmt.Errorf("minimum confidence requires normalized probabilities")
	}
	return id, nil
}

// Result contains a predicted class and its raw log score or configured probability.
type Result struct {
	Language string
	Score    float64
}

type workBuffer struct {
	featCounts  []uint32
	activeFeats []uint32
	scores      []float64
	scores32    []float32
}

type identifierRuntime struct {
	numLangs     int
	classes      []string
	nbPC         []float32
	nbPTC        []float32
	pool         *sync.Pool
	labelIndices []int
	aliasPairs   [][2]int
}

func newWorkBuffer(numFeats, numLangs int) *workBuffer {
	return &workBuffer{
		featCounts:  make([]uint32, numFeats),
		activeFeats: make([]uint32, 0, 256),
		scores:      make([]float64, numLangs),
		scores32:    make([]float32, numLangs),
	}
}

var (
	defaultOnce sync.Once
	defaultID   *Identifier
	defaultErr  error
)

// LoadModel reads a .lidg model file.
func LoadModel(path string, options ...Option) (*Identifier, error) {
	m, err := modelio.Load(path)
	if err != nil {
		return nil, err
	}
	return applyIdentifierOptions(newIdentifier(m), options)
}

// NewDefaultIdentifier loads the embedded py3langid 0.4 model.
func NewDefaultIdentifier(options ...Option) (*Identifier, error) {
	m, err := modelio.LoadBytes(defaultModel)
	if err != nil {
		return nil, err
	}
	return applyIdentifierOptions(newIdentifier(m), options)
}

func newIdentifier(m *modelio.Model) *Identifier {
	id := &Identifier{model: m}
	id.runtime.Store(newIdentifierRuntime(m, m.Classes, m.NbPC, m.NbPTC))
	return id
}

func getDefaultIdentifier() (*Identifier, error) {
	defaultOnce.Do(func() {
		defaultID, defaultErr = NewDefaultIdentifier()
	})
	return defaultID, defaultErr
}

// Classify uses a lazily-initialized embedded default model.
func Classify(text string) (Result, error) {
	id, err := getDefaultIdentifier()
	if err != nil {
		return Result{}, err
	}
	return id.IdentifyString(text)
}

// Rank uses the lazily initialized embedded model to rank all output labels.
func Rank(text string) ([]Result, error) {
	id, err := getDefaultIdentifier()
	if err != nil {
		return nil, err
	}
	return id.RankString(text)
}

// SetLanguages restricts the shared default identifier used by package functions.
// Call with no languages to reset it. Use a separate Identifier for independent settings.
func SetLanguages(langs ...string) error {
	id, err := getDefaultIdentifier()
	if err != nil {
		return err
	}
	return id.SetLanguages(langs...)
}

// IdentifyFile reads the file at the specified path and predicts its language using the default identifier.
func IdentifyFile(path string) (Result, error) {
	id, err := getDefaultIdentifier()
	if err != nil {
		return Result{}, err
	}
	return id.IdentifyFile(path)
}

// RankFile reads the file at the specified path and ranks all supported languages by likelihood using the default identifier.
func RankFile(path string) ([]Result, error) {
	id, err := getDefaultIdentifier()
	if err != nil {
		return nil, err
	}
	return id.RankFile(path)
}

// Classes returns the active language classes supported by the identifier.
func (id *Identifier) Classes() []string {
	rt := id.activeRuntime()
	if rt == nil {
		return nil
	}
	res := make([]string, len(rt.labelIndices))
	for index, column := range rt.labelIndices {
		res[index] = rt.classes[column]
	}
	return res
}

// ResetLanguages restores the active language set of the identifier to include
// all languages present in the original loaded model.
func (id *Identifier) ResetLanguages() {
	if id == nil || id.model == nil {
		return
	}
	id.runtime.Store(newIdentifierRuntime(id.model, id.model.Classes, id.model.NbPC, id.model.NbPTC))
}

// SetLanguages restricts the active language set of the identifier to the specified subset.
// If langs is empty or nil, it resets the active languages to the original model languages.
// If any requested language is not supported by the model, it returns an error and leaves
// the active language set unmodified (atomic operation).
func (id *Identifier) SetLanguages(langs ...string) error {
	if id == nil || id.model == nil {
		return fmt.Errorf("identifier is nil")
	}
	if len(langs) == 0 {
		id.ResetLanguages()
		return nil
	}

	modelLangs := make(map[string]bool, len(id.model.Classes))
	for _, c := range id.model.Classes {
		modelLangs[c] = true
	}

	for _, l := range langs {
		if !modelLangs[l] {
			return fmt.Errorf("language %q is not supported by this model", l)
		}
	}

	validLangs := make(map[string]bool, len(langs))
	for _, l := range langs {
		validLangs[l] = true
	}

	var newClasses []string
	var newPC []float32
	keepIndices := make([]int, 0, len(validLangs))

	for i, c := range id.model.Classes {
		if validLangs[c] {
			keepIndices = append(keepIndices, i)
			newClasses = append(newClasses, c)
			newPC = append(newPC, id.model.NbPC[i])
		}
	}

	newPTC := make([]float32, id.model.NumFeats*len(keepIndices))
	for feat := 0; feat < id.model.NumFeats; feat++ {
		baseOrig := feat * id.model.NumLangs
		baseNew := feat * len(keepIndices)
		for j, origIdx := range keepIndices {
			newPTC[baseNew+j] = id.model.NbPTC[baseOrig+origIdx]
		}
	}

	id.runtime.Store(newIdentifierRuntime(id.model, newClasses, newPC, newPTC))
	return nil
}

// IdentifyString predicts a language label for text.
func (id *Identifier) IdentifyString(text string) (Result, error) {
	return id.IdentifyBytes([]byte(text))
}

// IdentifyBytes predicts a language label for bytes.
func (id *Identifier) IdentifyBytes(text []byte) (Result, error) {
	return id.identifyBytes(text, id != nil && id.normProbs)
}

// IdentifyNormalizedString returns calibrated confidence and honors abstention.
func (id *Identifier) IdentifyNormalizedString(text string) (Result, error) {
	return id.IdentifyNormalizedBytes([]byte(text))
}

// IdentifyNormalizedBytes returns calibrated confidence and honors abstention.
func (id *Identifier) IdentifyNormalizedBytes(text []byte) (Result, error) {
	return id.identifyBytes(text, true)
}

func (id *Identifier) identifyBytes(text []byte, normalized bool) (Result, error) {
	rt := id.activeRuntime()
	buf, err := id.getLogProbs(rt, text, normalized)
	if err != nil {
		return Result{}, err
	}
	defer rt.pool.Put(buf)

	best := rt.labelIndices[0]
	for _, column := range rt.labelIndices[1:] {
		if buf.scores[column] > buf.scores[best] {
			best = column
		}
	}

	label := rt.classes[best]
	if id.minConfidence != nil && buf.scores[best] < *id.minConfidence {
		label = "und"
	}
	return Result{Language: label, Score: buf.scores[best]}, nil
}

// RankString returns all labels sorted by their configured scores.
func (id *Identifier) RankString(text string) ([]Result, error) {
	return id.RankBytes([]byte(text))
}

// RankBytes returns all labels sorted by their configured scores.
func (id *Identifier) RankBytes(text []byte) ([]Result, error) {
	return id.rankBytes(text, id != nil && id.normProbs)
}

// RankNormalizedString returns model-calibrated probabilities regardless of options.
func (id *Identifier) RankNormalizedString(text string) ([]Result, error) {
	return id.RankNormalizedBytes([]byte(text))
}

// RankNormalizedBytes returns model-calibrated probabilities regardless of options.
func (id *Identifier) RankNormalizedBytes(text []byte) ([]Result, error) {
	return id.rankBytes(text, true)
}

func (id *Identifier) rankBytes(text []byte, normalized bool) ([]Result, error) {
	rt := id.activeRuntime()
	buf, err := id.getLogProbs(rt, text, normalized)
	if err != nil {
		return nil, err
	}
	defer rt.pool.Put(buf)

	results := make([]Result, len(rt.labelIndices))
	for i, column := range rt.labelIndices {
		results[i] = Result{
			Language: rt.classes[column],
			Score:    buf.scores[column],
		}
	}

	sort.SliceStable(results, func(i, j int) bool {
		return results[i].Score > results[j].Score
	})

	return results, nil
}

// IdentifyFile reads the file at the specified path and predicts its language.
// If reading the file fails, it returns the wrapped filesystem error without swallowing context.
func (id *Identifier) IdentifyFile(path string) (Result, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return Result{}, fmt.Errorf("failed to read file: %w", err)
	}
	return id.IdentifyBytes(content)
}

// RankFile reads the file at the specified path and ranks all supported languages by likelihood.
// If reading the file fails, it returns the wrapped filesystem error without swallowing context.
func (id *Identifier) RankFile(path string) ([]Result, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read file: %w", err)
	}
	return id.RankBytes(content)
}

func (id *Identifier) getLogProbs(rt *identifierRuntime, text []byte, normalized bool) (*workBuffer, error) {
	if id == nil || id.model == nil || rt == nil {
		return nil, fmt.Errorf("identifier is nil")
	}

	m := id.model
	buf, ok := rt.pool.Get().(*workBuffer)
	if !ok {
		buf = newWorkBuffer(m.NumFeats, rt.numLangs)
	}
	id.scorePy3LangID(rt, buf, text, normalized)
	return buf, nil
}

func encodePy3LangID(text []byte) []byte {
	if !utf8.Valid(text) {
		decoded := false
		for trim := 1; trim <= 3 && trim <= len(text); trim++ {
			if candidate := text[:len(text)-trim]; utf8.Valid(candidate) {
				text = candidate
				decoded = true
				break
			}
		}
		if !decoded {
			return text
		}
	}
	allUpper := false
	for _, letter := range string(text) {
		if unicode.IsLower(letter) || unicode.IsTitle(letter) || unicode.Is(unicode.Other_Lowercase, letter) {
			allUpper = false
			break
		}
		if unicode.IsUpper(letter) || unicode.Is(unicode.Other_Uppercase, letter) {
			allUpper = true
		}
	}
	if allUpper {
		text = cases.Lower(language.Und).Bytes(text)
	}
	return norm.NFC.Bytes(text)
}

func (id *Identifier) scorePy3LangID(rt *identifierRuntime, buf *workBuffer, text []byte, normalized bool) {
	text = encodePy3LangID(text)
	model := id.model
	state := uint32(0)
	for _, letter := range text {
		state = model.TkNextmove32[int(model.TkRow[state])*256+int(letter)]
		feature := model.TkOutputF[state]
		if feature >= 0 {
			if buf.featCounts[feature] == 0 {
				buf.activeFeats = append(buf.activeFeats, uint32(feature))
			}
			buf.featCounts[feature]++
		}
	}
	clear(buf.scores32)
	if len(buf.activeFeats) == 0 {
		if !normalized {
			for index := range buf.scores32 {
				buf.scores32[index] = -math.MaxFloat32
			}
		}
	} else {
		for _, feature := range buf.activeFeats {
			count := float32(math.Log1p(float64(buf.featCounts[feature])))
			buf.featCounts[feature] = 0
			base := int(feature) * rt.numLangs
			for column := range buf.scores32 {
				buf.scores32[column] += count * rt.nbPTC[base+column]
			}
		}
		for column := range buf.scores32 {
			buf.scores32[column] += rt.nbPC[column]
		}
	}
	buf.activeFeats = buf.activeFeats[:0]
	if normalized {
		scale := float32(1 / math.Sqrt(float64(max(1, len(text)))))
		maximum := float32(-math.MaxFloat32)
		for column := range buf.scores32 {
			buf.scores32[column] *= scale
			maximum = max(maximum, buf.scores32[column])
		}
		total := float32(0)
		for column, score := range buf.scores32 {
			buf.scores32[column] = float32(math.Exp(float64(score - maximum)))
			total += buf.scores32[column]
		}
		for column := range buf.scores32 {
			buf.scores32[column] /= total
		}
	}
	for _, pair := range rt.aliasPairs {
		if normalized {
			buf.scores32[pair[0]] += buf.scores32[pair[1]]
			buf.scores32[pair[1]] = 0
		} else {
			buf.scores32[pair[0]] = max(buf.scores32[pair[0]], buf.scores32[pair[1]])
			buf.scores32[pair[1]] = -math.MaxFloat32
		}
	}
	for column, score := range buf.scores32 {
		buf.scores[column] = float64(score)
	}
}

func (id *Identifier) activeRuntime() *identifierRuntime {
	if id == nil {
		return nil
	}
	return id.runtime.Load()
}

func newIdentifierRuntime(m *modelio.Model, classes []string, nbPC []float32, nbPTC []float32) *identifierRuntime {
	rt := &identifierRuntime{
		numLangs: len(classes),
		classes:  classes,
		nbPC:     nbPC,
		nbPTC:    nbPTC,
	}
	first := make(map[string]int, len(classes))
	for column, label := range classes {
		if previous, exists := first[label]; exists {
			rt.aliasPairs = append(rt.aliasPairs, [2]int{previous, column})
		} else {
			first[label] = column
			rt.labelIndices = append(rt.labelIndices, column)
		}
	}
	rt.pool = &sync.Pool{
		New: func() any {
			return newWorkBuffer(m.NumFeats, rt.numLangs)
		},
	}
	return rt
}
