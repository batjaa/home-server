package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/batjaa/home-server/agents/internal/auth"
	"github.com/danielgtaylor/huma/v2"
	"github.com/danielgtaylor/huma/v2/adapters/humago"
)

func TestMovieAPIContract(t *testing.T) {
	requests := make(chan seerrCreateRequestInput, 1)
	upstreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Api-Key") != "upstream-secret" {
			t.Error("upstream authentication missing")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.Method + " " + r.URL.Path {
		case "GET /api/v3/movie", "GET /api/v3/movie/lookup":
			_, _ = w.Write([]byte(`[{"id":7,"tmdbId":42,"title":"Test Movie","year":2026,"hasFile":true,"monitored":true}]`))
		case "GET /api/v3/queue":
			_, _ = w.Write([]byte(`{"records":[{"movie":{"title":"Test Movie","tmdbId":42},"status":"downloading","sizeleft":1024}]}`))
		case "GET /api/v1/movie/42":
			_, _ = w.Write([]byte(`{"id":42,"title":"Test Movie","mediaInfo":{"status":5,"isAvailable":true}}`))
		case "POST /api/v1/request":
			var body seerrCreateRequestInput
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			requests <- body
			_, _ = w.Write([]byte(`{"id":99,"status":2,"media":{"tmdbId":42}}`))
		default:
			t.Errorf("unexpected upstream request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer upstreamServer.Close()
	service := &movieService{
		httpClient: upstreamServer.Client(),
		radarr:     upstream{name: "radarr", url: upstreamServer.URL, apiKey: "upstream-secret"},
		seerr:      upstream{name: "seerr", url: upstreamServer.URL, apiKey: "upstream-secret"},
	}
	handler := testMovieHandler(service)
	cases := []struct {
		name, method, path, body, token string
		status                          int
		contains                        string
	}{
		{"ping", "GET", "/v1/ping", "", "test-token", 200, `"service":"movie-agent"`},
		{"library search", "GET", "/v1/library/search?query=Test&limit=1", "", "test-token", 200, `"tmdb_id":42`},
		{"recent library", "GET", "/v1/library/recent", "", "test-token", 200, `"has_file":true`},
		{"queue", "GET", "/v1/queue", "", "test-token", 200, `"status":"downloading"`},
		{"lookup", "GET", "/v1/movies/lookup?query=Test", "", "test-token", 200, `"is_available":true`},
		{"movie status", "GET", "/v1/movies/42", "", "test-token", 200, `"in_library":true`},
		{"request", "POST", "/v1/requests", `{"tmdb_id":42,"is_4k":true}`, "test-token", 200, `"request_id":99`},
		{"missing auth", "GET", "/v1/ping", "", "", 401, "missing bearer token"},
		{"wrong auth", "POST", "/v1/requests", `{"tmdb_id":42}`, "incorrect", 401, "invalid bearer token"},
		{"missing query", "GET", "/v1/library/search", "", "test-token", 400, "query is required"},
		{"invalid path integer", "GET", "/v1/movies/not-an-id", "", "test-token", 422, ""},
		{"invalid limit", "GET", "/v1/queue?limit=no", "", "test-token", 422, ""},
		{"invalid request ID", "POST", "/v1/requests", `{"tmdb_id":0}`, "test-token", 400, "positive integer"},
		{"malformed request", "POST", "/v1/requests", `{`, "test-token", 400, ""},
		{"public schema", "GET", "/openapi.json", "", "", 200, `"requestMovie"`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			if tc.token != "" {
				req.Header.Set("Authorization", "Bearer "+tc.token)
			}
			if tc.body != "" {
				req.Header.Set("Content-Type", "application/json")
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, req)
			if response.Code != tc.status || !strings.Contains(response.Body.String(), tc.contains) {
				t.Fatalf("got HTTP %d: %s; want HTTP %d containing %q", response.Code, response.Body.String(), tc.status, tc.contains)
			}
		})
	}
	if len(requests) != 1 {
		t.Fatalf("want exactly one authorized request sent upstream, got %d", len(requests))
	}
	got := <-requests
	if got.MediaID != 42 || got.MediaType != "movie" || !got.Is4K {
		t.Fatalf("request payload changed: %+v", got)
	}
}

func TestMovieAPIUpstreamFailure(t *testing.T) {
	upstreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer upstreamServer.Close()
	service := &movieService{httpClient: upstreamServer.Client(), radarr: upstream{name: "radarr", url: upstreamServer.URL}}
	req := httptest.NewRequest("GET", "/v1/library/search?query=test", nil)
	req.Header.Set("Authorization", "Bearer test-token")
	response := httptest.NewRecorder()
	testMovieHandler(service).ServeHTTP(response, req)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("got HTTP %d; want 503", response.Code)
	}
}

func testMovieHandler(service *movieService) http.Handler {
	mux := http.NewServeMux()
	api := humago.New(mux, huma.DefaultConfig("Movie Agent", "0.2.0"))
	registerMovieAPI(api, service)
	return auth.Middleware("test-token", []string{"/openapi.json"})(mux)
}
