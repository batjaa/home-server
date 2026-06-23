package auth

import (
	"crypto/subtle"
	"net/http"
	"strings"
)

// Middleware returns an http middleware that requires a bearer token matching
// the configured value. Requests whose path equals or is a child of any entry
// in skipPaths bypass the check (used for /healthz and /openapi.json).
func Middleware(token string, skipPaths []string) func(http.Handler) http.Handler {
	tokenBytes := []byte(token)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if shouldSkip(r.URL.Path, skipPaths) {
				next.ServeHTTP(w, r)
				return
			}
			h := r.Header.Get("Authorization")
			const prefix = "Bearer "
			if !strings.HasPrefix(h, prefix) {
				http.Error(w, "missing bearer token", http.StatusUnauthorized)
				return
			}
			provided := []byte(h[len(prefix):])
			if subtle.ConstantTimeCompare(provided, tokenBytes) != 1 {
				http.Error(w, "invalid bearer token", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func shouldSkip(path string, skipPaths []string) bool {
	for _, p := range skipPaths {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}
