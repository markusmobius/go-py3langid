package modelio

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"math"
	"strings"
	"testing"
)

func TestLoadPy3LangID(t *testing.T) {
	wire := newPy3TestModel()
	model, err := LoadBytes(encodePy3TestModel(t, wire))
	if err != nil {
		t.Fatal(err)
	}
	if model.NumStates != 65537 || model.TkNextmove32['a'] != 65536 {
		t.Fatal("32-bit shared-row model did not survive decoding")
	}
	if model.TkOutputF[65536] != 0 || model.TkOutputF[0] != -1 || model.Classes[2] != "sr" {
		t.Fatal("outputs or aliased labels did not survive decoding")
	}
}

func TestLoadBundledPy3LangID(t *testing.T) {
	model, err := Load("../../model/py3langid.lidg")
	if err != nil {
		t.Fatal(err)
	}
	if model.NumFeats != 100053 || model.NumLangs != 142 || model.NumStates != 104583 {
		t.Fatal("unexpected pinned py3langid model dimensions")
	}
}

func TestLoadRejectsInvalidPy3Models(t *testing.T) {
	if _, err := LoadBytes([]byte{'L', 'I', 'D', 'G', '1', 0}); err == nil || !strings.Contains(err.Error(), "unsupported model format") {
		t.Fatalf("expected unsupported legacy format error, got %v", err)
	}
	tests := []struct {
		name   string
		mutate func(*Model)
		want   string
	}{
		{"zero dimension", func(model *Model) { model.NumFeats = 0 }, "invalid model dimension"},
		{"excessive states", func(model *Model) { model.NumStates = maxNumStates + 1 }, "invalid model dimension"},
		{"excessive weights", func(model *Model) { model.NumFeats = maxNumFeats; model.NumLangs = 100 }, "likelihood dimensions"},
		{"transition", func(model *Model) { model.TkNextmove32[0] = 65537 }, "invalid transition"},
		{"row", func(model *Model) { model.TkRow[0] = 1 }, "invalid row"},
		{"feature", func(model *Model) { model.TkOutputF[0] = 1 }, "invalid output feature"},
		{"negative feature", func(model *Model) { model.TkOutputF[0] = -2 }, "invalid output feature"},
		{"label", func(model *Model) { model.Classes[0] = strings.Repeat("x", 257) }, "invalid class label length"},
		{"score", func(model *Model) { model.NbPC[0] = float32(math.Inf(1)) }, "non-finite"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			model := newPy3TestModel()
			test.mutate(model)
			if _, err := LoadBytes(encodePy3TestModel(t, model)); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("expected %q, got %v", test.want, err)
			}
		})
	}
	encoded := encodePy3TestModel(t, newPy3TestModel())
	for _, length := range []int{0, 6, 16, len(encoded) - 1} {
		if _, err := LoadBytes(encoded[:length]); err == nil {
			t.Fatalf("accepted truncated model of length %d", length)
		}
	}
	encoded[len(encoded)-1] ^= 1
	if _, err := LoadBytes(encoded); err == nil {
		t.Fatal("accepted corrupt gzip checksum")
	}
}

func newPy3TestModel() *Model {
	model := &Model{
		NumFeats: 1, NumLangs: 3, NumStates: 65537,
		TkNextmove32: make([]uint32, 256), TkRow: make([]uint32, 65537),
		TkOutputF: make([]int32, 65537), NbPC: []float32{-1, -2, -3},
		NbPTC: []float32{-2, -3, -4}, Classes: []string{"en", "sr", "sr"},
	}
	model.TkNextmove32['a'] = 65536
	for state := range model.TkOutputF {
		model.TkOutputF[state] = -1
	}
	model.TkOutputF[65536] = 0
	return model
}

func encodePy3TestModel(t *testing.T, model *Model) []byte {
	t.Helper()
	var buffer bytes.Buffer
	buffer.Write(py3Magic[:])
	compressed := gzip.NewWriter(&buffer)
	values := []any{
		[4]uint32{uint32(model.NumFeats), uint32(model.NumLangs), uint32(model.NumStates), uint32(len(model.TkNextmove32) / 256)},
		model.TkNextmove32, model.TkRow, model.TkOutputF, model.NbPC, model.NbPTC,
	}
	for _, value := range values {
		if err := binary.Write(compressed, binary.LittleEndian, value); err != nil {
			t.Fatal(err)
		}
	}
	for _, label := range model.Classes {
		if err := binary.Write(compressed, binary.LittleEndian, uint16(len(label))); err != nil {
			t.Fatal(err)
		}
		if _, err := compressed.Write([]byte(label)); err != nil {
			t.Fatal(err)
		}
	}
	if err := compressed.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}
