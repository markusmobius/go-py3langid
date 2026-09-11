package py3langid

import (
	_ "embed"
)

//go:embed model/py3langid.lidg
var defaultModel []byte
