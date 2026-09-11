package modelio

// Model holds the shared-row DFA and scores of a converted py3langid model.
type Model struct {
	NumFeats  int
	NumLangs  int
	NumStates int

	TkNextmove32 []uint32
	TkRow        []uint32
	TkOutputF    []int32

	NbPC    []float32
	NbPTC   []float32
	Classes []string
}
