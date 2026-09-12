package workerprotocol

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
)

const maxMessageBytes = 8 * 1024 * 1024
const messageChunkBytes = 1024 * 1024

type message struct {
	MType   int
	GUID    string
	Content string
}

type inputMessage struct {
	GUID    string
	Command string
}

type Handler func(string) ([]byte, error)

func ValidateArguments(arguments []string) error {
	if len(arguments) != 1 && len(arguments) != 4 {
		return fmt.Errorf("usage: %s [port parent_pid ready_guid]", arguments[0])
	}
	return nil
}

func Serve(arguments []string, handle Handler) error {
	if err := ValidateArguments(arguments); err != nil {
		return err
	}
	if len(arguments) == 1 {
		return fileLoop(os.Stdin, os.Stdout, handle)
	}
	port, err := strconv.Atoi(arguments[1])
	if err != nil || port < 1 || port > 65535 {
		return fmt.Errorf("invalid TCP port: %q", arguments[1])
	}
	connection, err := net.Dial("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		return err
	}
	defer connection.Close()
	return tcpLoop(connection, arguments[3], handle)
}

func fileLoop(input io.Reader, output io.Writer, handle Handler) error {
	reader := bufio.NewReader(input)
	if _, err := fmt.Fprintln(output, "ready"); err != nil {
		return err
	}
	for {
		line, err := reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return err
		}
		parts := strings.Split(line, "\t")
		if len(parts) != 2 {
			return nil
		}
		result, err := handle(parts[0])
		if err == nil {
			for index := range result {
				result[index] ^= 0xFF
			}
			err = os.WriteFile(strings.TrimSpace(parts[1]), result, 0644)
		}
		status := "ok"
		if err != nil {
			status = "error"
		}
		if _, err := fmt.Fprintln(output, status); err != nil {
			return err
		}
	}
}

func tcpLoop(connection io.ReadWriter, readyGUID string, handle Handler) error {
	if err := sendMessage(connection, message{MType: 0, GUID: readyGUID}); err != nil {
		return err
	}
	for {
		payload, err := readMessage(connection)
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		var input inputMessage
		if err := json.Unmarshal(payload, &input); err != nil {
			return err
		}
		result, err := handle(input.Command)
		if err != nil {
			return err
		}
		if err := sendMessage(connection, message{MType: 1, GUID: input.GUID, Content: string(result)}); err != nil {
			return err
		}
	}
}

func readMessage(reader io.Reader) ([]byte, error) {
	var header [8]byte
	if _, err := io.ReadFull(reader, header[:]); err != nil {
		return nil, err
	}
	length := binary.BigEndian.Uint32(header[:4])
	chunkBytes := binary.BigEndian.Uint32(header[4:])
	if length == 0 || length > maxMessageBytes || chunkBytes == 0 {
		return nil, fmt.Errorf("invalid message header: length=%d chunk_bytes=%d", length, chunkBytes)
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(reader, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func sendMessage(writer io.Writer, value message) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return err
	}
	var header [8]byte
	binary.BigEndian.PutUint32(header[:4], uint32(len(payload)))
	binary.BigEndian.PutUint32(header[4:], messageChunkBytes)
	for _, part := range [][]byte{header[:4], header[4:], payload} {
		written, err := writer.Write(part)
		if err != nil {
			return err
		}
		if written != len(part) {
			return io.ErrShortWrite
		}
	}
	return nil
}
