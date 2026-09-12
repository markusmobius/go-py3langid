module github.com/markusmobius/go-py3langid/benchmarks/language-detection/workers

go 1.25.0

require (
	github.com/RadhiFadlillah/whatlanggo v0.0.0-20240916001553-aac1f0f737fc
	github.com/jmhodges/gocld3 v1.0.0
	github.com/markusmobius/go-py3langid v0.0.0
	github.com/pemistahl/lingua-go v1.4.0
)

require (
	github.com/shopspring/decimal v1.3.1 // indirect
	golang.org/x/exp v0.0.0-20221106115401-f9659909a136 // indirect
	golang.org/x/text v0.37.0 // indirect
	google.golang.org/protobuf v1.31.0 // indirect
)

replace github.com/markusmobius/go-py3langid => ../../..
