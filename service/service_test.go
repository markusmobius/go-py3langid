package service_test

import (
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/markusmobius/go-py3langid"
	"github.com/markusmobius/go-py3langid/service"
)

func TestServiceMinConfidence(t *testing.T) {
	id, err := py3langid.NewDefaultIdentifier(py3langid.WithNormalizedProbabilities(), py3langid.WithMinConfidence(0.5))
	if err != nil {
		t.Fatal(err)
	}
	handler := service.NewServer(id).NewHandler()
	for _, sample := range []struct{ text, language string }{
		{"Hi", "und"},
		{"This should be enough text.", "en"},
	} {
		request := httptest.NewRequest(http.MethodGet, "/detect?q="+url.QueryEscape(sample.text), nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		var envelope struct {
			Data struct {
				Language   string  `json:"language"`
				Confidence float64 `json:"confidence"`
			} `json:"responseData"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
			t.Fatal(err)
		}
		if response.Code != http.StatusOK || envelope.Data.Language != sample.language {
			t.Fatalf("detect %q: status=%d data=%+v", sample.text, response.Code, envelope.Data)
		}
		if (envelope.Data.Confidence < 0.5) != (sample.language == "und") {
			t.Fatalf("threshold not honored for %q: %+v", sample.text, envelope.Data)
		}
	}
}

func TestServiceHandlers(t *testing.T) {
	id, err := py3langid.NewDefaultIdentifier(py3langid.WithNormalizedProbabilities())
	if err != nil {
		t.Fatalf("failed to load default identifier: %v", err)
	}

	srv := service.NewServer(id)
	handler := srv.NewHandler()

	type responseEnvelope struct {
		ResponseData    any     `json:"responseData"`
		ResponseDetails *string `json:"responseDetails"`
		ResponseStatus  int     `json:"responseStatus"`
	}

	tests := []struct {
		name           string
		method         string
		path           string
		body           string
		contentType    string
		expectedStatus int
		verify         func(t *testing.T, resp responseEnvelope)
	}{
		{
			name:           "Detect GET with q",
			method:         "GET",
			path:           "/detect?q=hello+world",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				if resp.ResponseData == nil {
					t.Errorf("expected non-nil responseData")
					return
				}
				dataMap, ok := resp.ResponseData.(map[string]any)
				if !ok {
					t.Errorf("expected responseData to be a map, got %T", resp.ResponseData)
					return
				}
				lang, ok := dataMap["language"].(string)
				if !ok || lang != "fuv" {
					t.Errorf("expected pinned py3langid label 'fuv', got %v", dataMap["language"])
				}
				conf, ok := dataMap["confidence"].(float64)
				if !ok || math.Abs(conf-0.1421690732240677) > 1e-6 {
					t.Errorf("expected pinned py3langid confidence 0.142169, got %v", dataMap["confidence"])
				}
			},
		},
		{
			name:           "Detect GET with missing q",
			method:         "GET",
			path:           "/detect",
			expectedStatus: http.StatusBadRequest,
			verify: func(t *testing.T, resp responseEnvelope) {
				if resp.ResponseData != nil {
					t.Errorf("expected nil responseData for missing q, got %v", resp.ResponseData)
				}
			},
		},
		{
			name:           "Detect POST form with q",
			method:         "POST",
			path:           "/detect",
			body:           "q=Guten+Tag,+wie+geht+es+Ihnen%3F",
			contentType:    "application/x-www-form-urlencoded",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				dataMap, ok := resp.ResponseData.(map[string]any)
				if !ok {
					t.Fatalf("expected map response")
				}
				if dataMap["language"] != "de" {
					t.Errorf("expected language 'de', got %v", dataMap["language"])
				}
			},
		},
		{
			name:           "Detect POST raw body",
			method:         "POST",
			path:           "/detect",
			body:           "Bonjour tout le monde",
			contentType:    "text/plain",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				dataMap, ok := resp.ResponseData.(map[string]any)
				if !ok {
					t.Fatalf("expected map response")
				}
				if dataMap["language"] != "fr" {
					t.Errorf("expected language 'fr', got %v", dataMap["language"])
				}
			},
		},
		{
			name:           "Detect PUT raw body",
			method:         "PUT",
			path:           "/detect",
			body:           "This is an english test sentence.",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				dataMap, ok := resp.ResponseData.(map[string]any)
				if !ok {
					t.Fatalf("expected map response")
				}
				if dataMap["language"] != "en" {
					t.Errorf("expected language 'en', got %v", dataMap["language"])
				}
			},
		},
		{
			name:           "Rank GET with q",
			method:         "GET",
			path:           "/rank?q=hello+world",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				slice, ok := resp.ResponseData.([]any)
				if !ok {
					t.Fatalf("expected slice response for /rank, got %T", resp.ResponseData)
				}
				if len(slice) == 0 {
					t.Fatalf("expected non-empty rank list")
				}
				first, ok := slice[0].([]any)
				if !ok || len(first) < 2 {
					t.Fatalf("expected list of pairs, got %v", slice[0])
				}
				if first[0] != "fuv" {
					t.Errorf("expected pinned py3langid top label 'fuv', got %v", first[0])
				}
			},
		},
		{
			name:           "Rank GET with missing q",
			method:         "GET",
			path:           "/rank",
			expectedStatus: http.StatusBadRequest,
			verify: func(t *testing.T, resp responseEnvelope) {
				if resp.ResponseData != nil {
					t.Errorf("expected nil responseData for missing q, got %v", resp.ResponseData)
				}
			},
		},
		{
			name:           "Rank POST form with q",
			method:         "POST",
			path:           "/rank",
			body:           "q=Guten+Tag,+wie+geht+es+Ihnen%3F",
			contentType:    "application/x-www-form-urlencoded",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				slice, ok := resp.ResponseData.([]any)
				if !ok {
					t.Fatalf("expected slice")
				}
				first := slice[0].([]any)
				if first[0] != "de" {
					t.Errorf("expected top language 'de', got %v", first[0])
				}
			},
		},
		{
			name:           "Rank POST raw body",
			method:         "POST",
			path:           "/rank",
			body:           "Bonjour tout le monde",
			contentType:    "text/plain",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				slice, ok := resp.ResponseData.([]any)
				if !ok {
					t.Fatalf("expected slice")
				}
				first := slice[0].([]any)
				if first[0] != "fr" {
					t.Errorf("expected top language 'fr', got %v", first[0])
				}
			},
		},
		{
			name:           "Rank PUT raw body",
			method:         "PUT",
			path:           "/rank",
			body:           "This is an english test sentence.",
			expectedStatus: http.StatusOK,
			verify: func(t *testing.T, resp responseEnvelope) {
				slice, ok := resp.ResponseData.([]any)
				if !ok {
					t.Fatalf("expected slice")
				}
				first := slice[0].([]any)
				if first[0] != "en" {
					t.Errorf("expected top language 'en', got %v", first[0])
				}
			},
		},
		{
			name:           "Unsupported HTTP Method (DELETE)",
			method:         "DELETE",
			path:           "/detect",
			expectedStatus: http.StatusMethodNotAllowed,
			verify: func(t *testing.T, resp responseEnvelope) {
				if resp.ResponseDetails == nil || !strings.Contains(*resp.ResponseDetails, "DELETE not allowed") {
					t.Errorf("expected 'DELETE not allowed' in responseDetails, got %v", resp.ResponseDetails)
				}
			},
		},
		{
			name:           "Not Found Endpoint",
			method:         "GET",
			path:           "/unsupported_endpoint",
			expectedStatus: http.StatusNotFound,
			verify: func(t *testing.T, resp responseEnvelope) {
				if resp.ResponseDetails == nil || *resp.ResponseDetails != "Not found" {
					t.Errorf("expected 'Not found' in responseDetails, got %v", resp.ResponseDetails)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var req *http.Request
			if tt.body != "" {
				req = httptest.NewRequest(tt.method, tt.path, strings.NewReader(tt.body))
			} else {
				req = httptest.NewRequest(tt.method, tt.path, nil)
			}

			if tt.contentType != "" {
				req.Header.Set("Content-Type", tt.contentType)
			}

			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, req)

			if rr.Code != tt.expectedStatus {
				t.Fatalf("expected status %d, got %d", tt.expectedStatus, rr.Code)
			}

			// Validate Content-Type
			contentType := rr.Header().Get("Content-Type")
			if contentType != "application/json; charset=utf-8" {
				t.Errorf("expected JSON Content-Type, got %q", contentType)
			}

			var envelope responseEnvelope
			if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
				t.Fatalf("failed to decode JSON response: %v", err)
			}

			if envelope.ResponseStatus != tt.expectedStatus {
				t.Errorf("envelope status %d does not match HTTP status %d", envelope.ResponseStatus, tt.expectedStatus)
			}

			tt.verify(t, envelope)
		})
	}
}

func TestUpstreamService(t *testing.T) {
	id, err := py3langid.NewDefaultIdentifier()
	if err != nil {
		t.Fatal(err)
	}
	handler := service.NewServer(id).NewHandler()
	for _, path := range []string{"/detect", "/rank"} {
		for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodPut} {
			t.Run(path+"/"+method, func(t *testing.T) {
				target, body := path, "This is a test"
				if method == http.MethodGet {
					target, body = path+"?q=This+is+a+test", ""
				} else if method == http.MethodPost {
					body = "q=This+is+a+test"
				}
				request := httptest.NewRequest(method, target, strings.NewReader(body))
				response := httptest.NewRecorder()
				handler.ServeHTTP(response, request)
				if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "application/json; charset=utf-8" {
					t.Fatalf("status=%d headers=%v body=%s", response.Code, response.Header(), response.Body.String())
				}
				var envelope struct {
					Data json.RawMessage `json:"responseData"`
				}
				if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
					t.Fatal(err)
				}
				if path == "/detect" {
					var result struct {
						Language   string  `json:"language"`
						Confidence float64 `json:"confidence"`
					}
					if err := json.Unmarshal(envelope.Data, &result); err != nil {
						t.Fatal(err)
					}
					if result.Language != "en" || result.Confidence >= 0 {
						t.Fatalf("expected raw English result: %+v", result)
					}
				} else {
					var ranking [][2]any
					if err := json.Unmarshal(envelope.Data, &ranking); err != nil {
						t.Fatal(err)
					}
					if len(ranking) != 140 || ranking[0][0] != "en" || ranking[0][1].(float64) >= 0 {
						t.Fatalf("unexpected raw ranking: %v", ranking)
					}
				}
			})
		}
	}
	for _, sample := range []struct {
		name, method, path, body, length string
		status                           int
	}{
		{"invalid_method", "DELETE", "/detect", "", "", 405},
		{"invalid_path", "GET", "/invalid", "", "", 404},
		{"empty_path", "GET", "/", "", "", 404},
		{"no_query", "GET", "/detect", "", "", 400},
		{"missing_q", "GET", "/detect?x=hello", "", "", 400},
		{"empty_q", "GET", "/detect?q=", "", "", 400},
		{"missing_content_length", "POST", "/detect", "q=test", "missing", 400},
		{"invalid_content_length", "POST", "/detect", "q=test", "abc", 400},
	} {
		t.Run(sample.name, func(t *testing.T) {
			request := httptest.NewRequest(sample.method, sample.path, strings.NewReader(sample.body))
			if sample.length == "missing" {
				request.ContentLength = 0
			} else if sample.length != "" {
				request.Header.Set("Content-Length", sample.length)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			var envelope struct {
				Data   any `json:"responseData"`
				Status int `json:"responseStatus"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
				t.Fatal(err)
			}
			if response.Code != sample.status || envelope.Status != sample.status || envelope.Data != nil {
				t.Fatalf("status=%d envelope=%+v", response.Code, envelope)
			}
		})
	}
}

func TestDemoHandler(t *testing.T) {
	id, _ := py3langid.NewDefaultIdentifier()
	srv := service.NewServer(id)
	handler := srv.NewHandler()

	req := httptest.NewRequest("GET", "/demo", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rr.Code)
	}

	contentType := rr.Header().Get("Content-Type")
	if !strings.Contains(contentType, "text/html") {
		t.Errorf("expected Content-Type to contain text/html, got %q", contentType)
	}

	body := rr.Body.String()
	if !strings.Contains(body, "<html>") || !strings.Contains(body, "typerArea") {
		t.Errorf("returned body does not look like the demo form HTML")
	}
}

func TestServiceRequestSizeLimit(t *testing.T) {
	id, err := py3langid.NewDefaultIdentifier()
	if err != nil {
		t.Fatalf("failed to load default identifier: %v", err)
	}

	srv := service.NewServer(id)
	srv.SetMaxRequestBytes(4)
	handler := srv.NewHandler()

	req := httptest.NewRequest(http.MethodPost, "/detect", strings.NewReader("hello"))
	req.Header.Set("Content-Type", "text/plain")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected status %d, got %d", http.StatusRequestEntityTooLarge, rr.Code)
	}

	var envelope struct {
		ResponseDetails *string `json:"responseDetails"`
		ResponseStatus  int     `json:"responseStatus"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
		t.Fatalf("failed to decode JSON response: %v", err)
	}
	if envelope.ResponseStatus != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected envelope status %d, got %d", http.StatusRequestEntityTooLarge, envelope.ResponseStatus)
	}
	if envelope.ResponseDetails == nil || !strings.Contains(*envelope.ResponseDetails, "exceeds 4 bytes") {
		t.Fatalf("expected response details to mention the 4-byte limit, got %v", envelope.ResponseDetails)
	}
}
