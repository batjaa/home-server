package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMiddleware(t *testing.T) {
	const token = "s3cret"
	skipped := []string{"/healthz", "/openapi.json"}

	ok := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := Middleware(token, skipped)(ok)

	cases := []struct {
		name       string
		path       string
		authHeader string
		wantStatus int
	}{
		{"valid token", "/v1/ping", "Bearer s3cret", http.StatusOK},
		{"wrong token", "/v1/ping", "Bearer nope", http.StatusUnauthorized},
		{"missing header", "/v1/ping", "", http.StatusUnauthorized},
		{"wrong scheme", "/v1/ping", "Basic s3cret", http.StatusUnauthorized},
		{"skip exact path", "/healthz", "", http.StatusOK},
		{"skip openapi", "/openapi.json", "", http.StatusOK},
		{"skip subpath", "/healthz/extra", "", http.StatusOK},
		{"not actually a skip prefix", "/healthznope", "", http.StatusUnauthorized},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, tc.path, nil)
			if tc.authHeader != "" {
				req.Header.Set("Authorization", tc.authHeader)
			}
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, req)
			if rr.Code != tc.wantStatus {
				t.Errorf("got %d, want %d", rr.Code, tc.wantStatus)
			}
		})
	}
}
