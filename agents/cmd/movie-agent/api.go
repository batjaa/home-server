package main

import (
	"context"
	"fmt"
	"net/http"

	"github.com/danielgtaylor/huma/v2"
)

type pingOutput struct {
	Body struct {
		OK      bool   `json:"ok"`
		Service string `json:"service"`
	}
}

type searchInput struct {
	Query string `query:"query" doc:"Movie title or keyword to search for"`
	Limit int    `query:"limit" doc:"Maximum number of results to return" default:"10"`
}

type searchOutput struct {
	Body struct {
		Results []movieSummary `json:"results"`
	}
}

type queueInput struct {
	Limit int `query:"limit" doc:"Maximum number of queue items to return" default:"10"`
}

type queueOutput struct {
	Body struct {
		Results []queueItemSummary `json:"results"`
	}
}

type recentInput struct {
	Limit       int  `query:"limit" doc:"Maximum number of movies to return" default:"10"`
	RequireFile bool `query:"require_file" doc:"Only return movies with an imported file" default:"true"`
}

type tmdbInput struct {
	TMDBID int `path:"tmdb_id" doc:"TMDB movie identifier"`
}

type getMovieOutput struct {
	Body movieSummary
}

type requestMovieInput struct {
	Body struct {
		TMDBID int  `json:"tmdb_id" doc:"TMDB movie identifier"`
		Is4K   bool `json:"is_4k,omitempty" doc:"Request the 4K profile in Seerr if available"`
	}
}

type requestMovieOutput struct {
	Body requestResult
}

func registerMovieAPI(api huma.API, service *movieService) {
	huma.Register(api, huma.Operation{
		OperationID: "ping",
		Method:      http.MethodGet,
		Path:        "/v1/ping",
		Summary:     "Authenticated liveness check",
		Description: "Returns 200 when the bearer token is valid. Used to verify auth wiring.",
		Tags:        []string{"meta"},
	}, func(_ context.Context, _ *struct{}) (*pingOutput, error) {
		out := &pingOutput{}
		out.Body.OK = true
		out.Body.Service = "movie-agent"
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "searchLibraryMovies",
		Method:      http.MethodGet,
		Path:        "/v1/library/search",
		Summary:     "Search movies already tracked in Radarr",
		Description: "Returns movies already in the Radarr library, filtered by title, overview, studio, or IMDb ID.",
		Tags:        []string{"library"},
	}, func(ctx context.Context, input *searchInput) (*searchOutput, error) {
		if input.Query == "" {
			return nil, huma.Error400BadRequest("query is required")
		}
		results, err := service.searchLibrary(ctx, input.Query, clampLimit(input.Limit))
		if err != nil {
			return nil, huma.Error503ServiceUnavailable("radarr library search failed", err)
		}
		out := &searchOutput{}
		out.Body.Results = results
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "listRecentLibraryMovies",
		Method:      http.MethodGet,
		Path:        "/v1/library/recent",
		Summary:     "List recently added or recently downloaded movies from Radarr",
		Description: "Returns the newest movies in the Radarr library. By default it only includes movies that already have a file, which makes it suitable for questions like 'what did I recently download?'",
		Tags:        []string{"library"},
	}, func(ctx context.Context, input *recentInput) (*searchOutput, error) {
		results, err := service.recentLibrary(ctx, clampLimit(input.Limit), input.RequireFile)
		if err != nil {
			return nil, huma.Error503ServiceUnavailable("recent library query failed", err)
		}
		out := &searchOutput{}
		out.Body.Results = results
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "listMovieQueue",
		Method:      http.MethodGet,
		Path:        "/v1/queue",
		Summary:     "List movies currently queued, downloading, importing, or failing in Radarr",
		Description: "Returns the current Radarr queue. Use this for questions like 'what is still downloading?' or 'which movie imports are failing?'.",
		Tags:        []string{"queue"},
	}, func(ctx context.Context, input *queueInput) (*queueOutput, error) {
		results, err := service.movieQueue(ctx, clampLimit(input.Limit))
		if err != nil {
			return nil, huma.Error503ServiceUnavailable("radarr queue query failed", err)
		}
		out := &queueOutput{}
		out.Body.Results = results
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "lookupMovies",
		Method:      http.MethodGet,
		Path:        "/v1/movies/lookup",
		Summary:     "Look up movies by title via Radarr and enrich them with Seerr request state",
		Description: "Useful when the chat wants to know if a movie exists, whether it is already in Radarr, and whether Seerr thinks it is requested or available.",
		Tags:        []string{"discovery"},
	}, func(ctx context.Context, input *searchInput) (*searchOutput, error) {
		if input.Query == "" {
			return nil, huma.Error400BadRequest("query is required")
		}
		results, err := service.lookupMovies(ctx, input.Query, clampLimit(input.Limit))
		if err != nil {
			return nil, huma.Error503ServiceUnavailable("movie lookup failed", err)
		}
		out := &searchOutput{}
		out.Body.Results = results
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "getMovie",
		Method:      http.MethodGet,
		Path:        "/v1/movies/{tmdb_id}",
		Summary:     "Get combined movie status from Radarr and Seerr",
		Description: "Returns a normalized view of one movie, merging Radarr library state with Seerr request and availability state when possible.",
		Tags:        []string{"status"},
	}, func(ctx context.Context, input *tmdbInput) (*getMovieOutput, error) {
		movie, err := service.getMovie(ctx, input.TMDBID)
		if err != nil {
			return nil, huma.Error503ServiceUnavailable(fmt.Sprintf("failed to get movie %d", input.TMDBID), err)
		}
		out := &getMovieOutput{}
		out.Body = movie
		return out, nil
	})

	huma.Register(api, huma.Operation{
		OperationID: "requestMovie",
		Method:      http.MethodPost,
		Path:        "/v1/requests",
		Summary:     "Request a movie in Seerr by TMDB ID",
		Description: "Creates a Seerr movie request. Use lookup first to confirm the TMDB ID.",
		Tags:        []string{"requests"},
	}, func(ctx context.Context, input *requestMovieInput) (*requestMovieOutput, error) {
		if input.Body.TMDBID <= 0 {
			return nil, huma.Error400BadRequest("tmdb_id must be a positive integer")
		}
		result, err := service.requestMovie(ctx, input.Body.TMDBID, input.Body.Is4K)
		if err != nil {
			return nil, huma.Error503ServiceUnavailable(fmt.Sprintf("failed to request movie %d", input.Body.TMDBID), err)
		}
		out := &requestMovieOutput{}
		out.Body = result
		return out, nil
	})
}

func clampLimit(limit int) int {
	if limit <= 0 {
		return 10
	}
	if limit > 25 {
		return 25
	}
	return limit
}
