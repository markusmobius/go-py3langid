package workerprotocol

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/iotest"
	"time"
)

func TestTCPRepeatedRequests(t *testing.T) {
	controller, process := net.Pipe()
	t.Cleanup(func() { controller.Close() })
	t.Cleanup(func() { process.Close() })
	deadline := time.Now().Add(5 * time.Second)
	if err := controller.SetDeadline(deadline); err != nil {
		t.Fatal(err)
	}
	if err := process.SetDeadline(deadline); err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() {
		finished <- tcpLoop(process, "ready-guid", func(command string) ([]byte, error) {
			return []byte(command), nil
		})
	}()
	readResponse := func() message {
		t.Helper()
		payload, err := readMessage(controller)
		if err != nil {
			t.Fatal(err)
		}
		var response message
		if err := json.Unmarshal(payload, &response); err != nil {
			t.Fatal(err)
		}
		return response
	}
	if ready := readResponse(); ready.MType != 0 || ready.GUID != "ready-guid" {
		t.Fatalf("unexpected handshake: %+v", ready)
	}
	for _, request := range []inputMessage{
		{GUID: "first", Command: `{"Text":"English text."}`},
		{GUID: "second", Command: `{"Text":"escaped\nline\ttext"}`},
	} {
		payload, err := json.Marshal(request)
		if err != nil {
			t.Fatal(err)
		}
		var header [8]byte
		binary.BigEndian.PutUint32(header[:4], uint32(len(payload)))
		binary.BigEndian.PutUint32(header[4:], 5)
		if _, err := controller.Write(append(header[:], payload...)); err != nil {
			t.Fatal(err)
		}
		response := readResponse()
		if response.MType != 1 || response.GUID != request.GUID || response.Content != request.Command {
			t.Fatalf("unexpected reply: %+v", response)
		}
	}
	controller.Close()
	select {
	case err := <-finished:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not exit after EOF")
	}
}

func TestFileLoop(t *testing.T) {
	directory := t.TempDir()
	paths := []string{filepath.Join(directory, "first.json"), filepath.Join(directory, "second.json")}
	command := `{"Text":"This text is in English."}`
	input := strings.NewReader(command + "\t" + paths[0] + "\n" + command + "\t" + paths[1] + "\n")
	var output bytes.Buffer
	if err := fileLoop(input, &output, func(command string) ([]byte, error) {
		return []byte(command), nil
	}); err != nil {
		t.Fatal(err)
	}
	if output.String() != "ready\nok\nok\n" {
		t.Fatalf("unexpected stdout: %q", output.String())
	}
	for _, path := range paths {
		payload, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for index := range payload {
			payload[index] ^= 0xFF
		}
		if string(payload) != command {
			t.Fatalf("unexpected XOR-decoded output: %s", payload)
		}
	}
}

func TestMessageFraming(t *testing.T) {
	var output bytes.Buffer
	if err := sendMessage(&output, message{MType: 0, GUID: "ready"}); err != nil {
		t.Fatal(err)
	}
	frame := output.Bytes()
	if binary.BigEndian.Uint32(frame[:4]) != uint32(len(frame)-8) || binary.BigEndian.Uint32(frame[4:8]) != messageChunkBytes {
		t.Fatalf("unexpected big-endian header: %x", frame[:8])
	}
	payload, err := readMessage(iotest.OneByteReader(bytes.NewReader(frame)))
	if err != nil || string(payload) != `{"MType":0,"GUID":"ready","Content":""}` {
		t.Fatalf("fragmented read: %s, %v", payload, err)
	}
	for _, header := range [][2]uint32{{0, 1}, {maxMessageBytes + 1, 1}, {10, 0}} {
		var frame [8]byte
		binary.BigEndian.PutUint32(frame[:4], header[0])
		binary.BigEndian.PutUint32(frame[4:], header[1])
		if _, err := readMessage(bytes.NewReader(frame[:])); err == nil {
			t.Fatalf("accepted invalid header: %v", header)
		}
	}
}

func TestValidateArguments(t *testing.T) {
	if err := ValidateArguments([]string{"worker"}); err != nil {
		t.Fatal(err)
	}
	if err := ValidateArguments([]string{"worker", "1", "2", "ready"}); err != nil {
		t.Fatal(err)
	}
	if err := ValidateArguments([]string{"worker", "extra"}); err == nil {
		t.Fatal("accepted invalid arguments")
	}
}
