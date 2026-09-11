package modelio

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"os"
)

var py3Magic = [6]byte{'L', 'I', 'D', 'G', '2', 0}

const (
	maxNumLangs  = 10000
	maxNumFeats  = 1000000
	maxNumStates = 500000
	maxNbPTCLen  = 50000000
)

func Load(path string) (*Model, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open model: %w", err)
	}
	defer f.Close()

	return loadConvertedModel(f)
}

// LoadBytes decodes a model from in-memory bytes.
func LoadBytes(data []byte) (*Model, error) {
	return loadConvertedModel(bytes.NewReader(data))
}

func loadConvertedModel(source io.Reader) (*Model, error) {
	var signature [6]byte
	if _, err := io.ReadFull(source, signature[:]); err != nil {
		return nil, fmt.Errorf("read model signature: %w", err)
	}
	if signature != py3Magic {
		return nil, fmt.Errorf("unsupported model format: expected converted py3langid LIDG2 model")
	}
	return loadPy3LangID(source)
}

func loadPy3LangID(source io.Reader) (*Model, error) {
	reader, err := gzip.NewReader(source)
	if err != nil {
		return nil, fmt.Errorf("open compressed model: %w", err)
	}
	defer reader.Close()

	var dimensions [4]uint32
	if err := binary.Read(reader, binary.LittleEndian, &dimensions); err != nil {
		return nil, fmt.Errorf("read model dimensions: %w", err)
	}
	limits := [4]uint32{maxNumFeats, maxNumLangs, maxNumStates, maxNumStates}
	for index, value := range dimensions {
		if value == 0 || value > limits[index] {
			return nil, fmt.Errorf("invalid model dimension %d: %d (limit %d)", index, value, limits[index])
		}
	}
	if dimensions[3] > dimensions[2] {
		return nil, fmt.Errorf("invalid model: more transition rows than states")
	}
	model := &Model{
		NumFeats: int(dimensions[0]), NumLangs: int(dimensions[1]),
		NumStates: int(dimensions[2]),
	}
	weightCount, err := checkedProduct(model.NumFeats, model.NumLangs)
	if err != nil || weightCount > maxNbPTCLen {
		return nil, fmt.Errorf("invalid model: likelihood dimensions exceed limit %d", maxNbPTCLen)
	}
	model.TkNextmove32 = make([]uint32, int(dimensions[3])*256)
	model.TkRow = make([]uint32, model.NumStates)
	model.TkOutputF = make([]int32, model.NumStates)
	model.NbPC = make([]float32, model.NumLangs)
	model.NbPTC = make([]float32, weightCount)
	for _, values := range []any{model.TkNextmove32, model.TkRow, model.TkOutputF, model.NbPC, model.NbPTC} {
		if err := binary.Read(reader, binary.LittleEndian, values); err != nil {
			return nil, fmt.Errorf("read model arrays: %w", err)
		}
	}
	model.Classes = make([]string, model.NumLangs)
	for index := range model.Classes {
		var length uint16
		if err := binary.Read(reader, binary.LittleEndian, &length); err != nil {
			return nil, fmt.Errorf("read class length: %w", err)
		}
		if length == 0 || length > 256 {
			return nil, fmt.Errorf("invalid class label length: %d", length)
		}
		label := make([]byte, length)
		if _, err := io.ReadFull(reader, label); err != nil {
			return nil, fmt.Errorf("read class: %w", err)
		}
		model.Classes[index] = string(label)
	}
	var extra [1]byte
	if _, err := io.ReadFull(reader, extra[:]); err != io.EOF {
		return nil, fmt.Errorf("invalid compressed model end: %v", err)
	}
	rowCount := len(model.TkNextmove32) / 256
	for index, state := range model.TkNextmove32 {
		if state >= uint32(model.NumStates) {
			return nil, fmt.Errorf("invalid transition %d: state %d", index, state)
		}
	}
	for state, row := range model.TkRow {
		if row >= uint32(rowCount) {
			return nil, fmt.Errorf("invalid row %d for state %d", row, state)
		}
		feature := model.TkOutputF[state]
		if feature < -1 || feature >= int32(model.NumFeats) {
			return nil, fmt.Errorf("invalid output feature %d for state %d", feature, state)
		}
	}
	for _, values := range [][]float32{model.NbPC, model.NbPTC} {
		for _, value := range values {
			if math.IsNaN(float64(value)) || math.IsInf(float64(value), 0) {
				return nil, fmt.Errorf("invalid non-finite model score")
			}
		}
	}
	return model, nil
}

func checkedProduct(a, b int) (int, error) {
	if a < 0 || b < 0 {
		return 0, fmt.Errorf("negative dimension")
	}
	if a == 0 || b == 0 {
		return 0, nil
	}
	if a > math.MaxInt/b {
		return 0, fmt.Errorf("dimension overflow: %d * %d", a, b)
	}
	return a * b, nil
}
