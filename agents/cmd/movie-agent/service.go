package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

type movieService struct {
	httpClient *http.Client
	radarr     upstream
	seerr      upstream
}

type upstream struct {
	name   string
	url    string
	apiKey string
}

type movieSummary struct {
	TMDBID        int     `json:"tmdb_id"`
	Title         string  `json:"title"`
	Year          int     `json:"year,omitempty"`
	Overview      string  `json:"overview,omitempty"`
	Studio        string  `json:"studio,omitempty"`
	FolderPath    string  `json:"folder_path,omitempty"`
	Status        string  `json:"status,omitempty"`
	MinimumStatus string  `json:"minimum_availability,omitempty"`
	InLibrary     bool    `json:"in_library"`
	Downloaded    bool    `json:"downloaded,omitempty"`
	Monitored     bool    `json:"monitored,omitempty"`
	HasFile       bool    `json:"has_file,omitempty"`
	IsAvailable   bool    `json:"is_available,omitempty"`
	IsRequested   bool    `json:"is_requested,omitempty"`
	RequestStatus string  `json:"request_status,omitempty"`
	RadarrID      *int    `json:"radarr_id,omitempty"`
	ImdbID        string  `json:"imdb_id,omitempty"`
	Website       string  `json:"website,omitempty"`
	Rating        float64 `json:"rating,omitempty"`
}

type requestResult struct {
	RequestID int          `json:"request_id"`
	Status    string       `json:"status"`
	Movie     movieSummary `json:"movie"`
}

type queueItemSummary struct {
	Title            string `json:"title"`
	Year             int    `json:"year,omitempty"`
	TMDBID           int    `json:"tmdb_id,omitempty"`
	Status           string `json:"status,omitempty"`
	TrackedState     string `json:"tracked_state,omitempty"`
	Protocol         string `json:"protocol,omitempty"`
	Indexer          string `json:"indexer,omitempty"`
	OutputPath       string `json:"output_path,omitempty"`
	SizeLeft         int64  `json:"size_left,omitempty"`
	EstimatedSeconds int64  `json:"estimated_seconds,omitempty"`
	ErrorMessage     string `json:"error_message,omitempty"`
}

type radarrMovie struct {
	ID                  int     `json:"id"`
	Title               string  `json:"title"`
	Overview            string  `json:"overview"`
	Year                int     `json:"year"`
	TMDBID              int     `json:"tmdbId"`
	IMDBID              string  `json:"imdbId"`
	FolderPath          string  `json:"folderPath"`
	Status              string  `json:"status"`
	MinimumAvailability string  `json:"minimumAvailability"`
	Monitored           bool    `json:"monitored"`
	HasFile             bool    `json:"hasFile"`
	IsAvailable         bool    `json:"isAvailable"`
	Studio              string  `json:"studio"`
	Website             string  `json:"website"`
	Ratings             ratings `json:"ratings"`
}

type radarrQueueResponse struct {
	Page         int               `json:"page"`
	PageSize     int               `json:"pageSize"`
	TotalRecords int               `json:"totalRecords"`
	Records      []radarrQueueItem `json:"records"`
}

type radarrQueueItem struct {
	Status             string `json:"status"`
	TrackedDownload    string `json:"trackedDownloadState"`
	TrackedDownloadMsg string `json:"trackedDownloadStatus"`
	StatusMessages     []struct {
		Title    string `json:"title"`
		Messages []struct {
			Message string `json:"message"`
		} `json:"messages"`
	} `json:"statusMessages"`
	Protocol            string `json:"protocol"`
	Indexer             string `json:"indexer"`
	OutputPath          string `json:"outputPath"`
	Sizeleft            int64  `json:"sizeleft"`
	Timeleft            string `json:"timeleft"`
	EstimatedCompletion string `json:"estimatedCompletionTime"`
	Movie               struct {
		Title  string `json:"title"`
		Year   int    `json:"year"`
		TMDBID int    `json:"tmdbId"`
	} `json:"movie"`
}

type ratings struct {
	Value float64 `json:"value"`
}

type seerrSearchResponse struct {
	Results []seerrSearchResult `json:"results"`
}

type seerrSearchResult struct {
	ID          int              `json:"id"`
	MediaType   string           `json:"mediaType"`
	Title       string           `json:"title"`
	Overview    string           `json:"overview"`
	ReleaseDate string           `json:"releaseDate"`
	MediaInfo   *seerrMediaInfo  `json:"mediaInfo"`
	Request     *seerrRequestRef `json:"request"`
}

type seerrMovieDetail struct {
	ID          int              `json:"id"`
	MediaInfo   *seerrMediaInfo  `json:"mediaInfo"`
	Request     *seerrRequestRef `json:"request"`
	Title       string           `json:"title"`
	Overview    string           `json:"overview"`
	ReleaseDate string           `json:"releaseDate"`
}

type seerrMediaInfo struct {
	Status      int  `json:"status"`
	IsAvailable bool `json:"isAvailable"`
}

type seerrRequestRef struct {
	ID     int `json:"id"`
	Status int `json:"status"`
}

type seerrCreateRequestInput struct {
	MediaType string `json:"mediaType"`
	MediaID   int    `json:"mediaId"`
	Is4K      bool   `json:"is4k"`
}

type seerrCreateRequestResponse struct {
	ID     int `json:"id"`
	Status int `json:"status"`
	Media  struct {
		TMDBID int `json:"tmdbId"`
	} `json:"media"`
}

func newMovieServiceFromEnv() (*movieService, error) {
	radarrURL := os.Getenv("MOVIE_AGENT_RADARR_URL")
	radarrKey := os.Getenv("MOVIE_AGENT_RADARR_API_KEY")
	seerrURL := os.Getenv("MOVIE_AGENT_SEERR_URL")
	seerrKey := os.Getenv("MOVIE_AGENT_SEERR_API_KEY")

	if radarrURL == "" || radarrKey == "" {
		return nil, fmt.Errorf("MOVIE_AGENT_RADARR_URL and MOVIE_AGENT_RADARR_API_KEY are required")
	}
	if seerrURL == "" || seerrKey == "" {
		return nil, fmt.Errorf("MOVIE_AGENT_SEERR_URL and MOVIE_AGENT_SEERR_API_KEY are required")
	}

	return &movieService{
		httpClient: &http.Client{Timeout: 20 * time.Second},
		radarr: upstream{
			name:   "radarr",
			url:    strings.TrimRight(radarrURL, "/"),
			apiKey: radarrKey,
		},
		seerr: upstream{
			name:   "seerr",
			url:    strings.TrimRight(seerrURL, "/"),
			apiKey: seerrKey,
		},
	}, nil
}

func (s *movieService) searchLibrary(ctx context.Context, query string, limit int) ([]movieSummary, error) {
	var movies []radarrMovie
	if err := s.getJSON(ctx, s.radarr, "/api/v3/movie", nil, &movies); err != nil {
		return nil, err
	}

	query = strings.ToLower(strings.TrimSpace(query))
	out := make([]movieSummary, 0, limit)
	for _, movie := range movies {
		if query != "" && !containsMovie(movie, query) {
			continue
		}
		out = append(out, summarizeRadarr(movie))
	}

	sort.Slice(out, func(i, j int) bool {
		if out[i].Title == out[j].Title {
			return out[i].Year < out[j].Year
		}
		return out[i].Title < out[j].Title
	})

	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (s *movieService) recentLibrary(ctx context.Context, limit int, requireFile bool) ([]movieSummary, error) {
	params := url.Values{}
	params.Set("page", "1")
	params.Set("pageSize", strconv.Itoa(limit))
	params.Set("sortKey", "added")
	params.Set("sortDirection", "descending")

	var movies []radarrMovie
	if err := s.getJSON(ctx, s.radarr, "/api/v3/movie", params, &movies); err != nil {
		return nil, err
	}

	out := make([]movieSummary, 0, len(movies))
	for _, movie := range movies {
		if requireFile && !movie.HasFile {
			continue
		}
		out = append(out, summarizeRadarr(movie))
	}

	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (s *movieService) movieQueue(ctx context.Context, limit int) ([]queueItemSummary, error) {
	params := url.Values{}
	params.Set("page", "1")
	params.Set("pageSize", strconv.Itoa(limit))
	params.Set("sortKey", "added")
	params.Set("sortDirection", "descending")

	var response radarrQueueResponse
	if err := s.getJSON(ctx, s.radarr, "/api/v3/queue", params, &response); err != nil {
		return nil, err
	}

	out := make([]queueItemSummary, 0, len(response.Records))
	for _, item := range response.Records {
		out = append(out, summarizeQueueItem(item))
	}
	return out, nil
}

func (s *movieService) lookupMovies(ctx context.Context, query string, limit int) ([]movieSummary, error) {
	params := url.Values{}
	params.Set("term", query)

	var lookup []radarrMovie
	if err := s.getJSON(ctx, s.radarr, "/api/v3/movie/lookup", params, &lookup); err != nil {
		return nil, err
	}

	results := make([]movieSummary, 0, limit)
	for _, movie := range lookup {
		summary := summarizeRadarr(movie)
		if movie.TMDBID != 0 {
			if tracked, found, err := s.getRadarrMovieByTMDB(ctx, movie.TMDBID); err == nil && found {
				summary = summarizeRadarr(tracked)
			}
		}
		results = append(results, summary)
	}

	if limit > 0 && len(results) > limit {
		results = results[:limit]
	}

	for i := range results {
		if detail, err := s.getSeerrMovie(ctx, results[i].TMDBID); err == nil {
			mergeSeerrDetail(&results[i], detail)
		}
	}

	return results, nil
}

func (s *movieService) getMovie(ctx context.Context, tmdbID int) (movieSummary, error) {
	summary := movieSummary{TMDBID: tmdbID}
	radarrMovieResponse, radarrFound, radarrErr := s.getRadarrMovieByTMDB(ctx, tmdbID)
	if radarrErr == nil && radarrFound {
		summary = summarizeRadarr(radarrMovieResponse)
	}

	if detail, err := s.getSeerrMovie(ctx, tmdbID); err == nil {
		if summary.Title == "" {
			summary.Title = detail.Title
			summary.Overview = detail.Overview
			summary.Year = yearFromDate(detail.ReleaseDate)
		}
		mergeSeerrDetail(&summary, detail)
	}

	if summary.Title == "" {
		if radarrErr != nil {
			return movieSummary{}, radarrErr
		}
		return movieSummary{}, fmt.Errorf("movie %d not found", tmdbID)
	}
	return summary, nil
}

func (s *movieService) requestMovie(ctx context.Context, tmdbID int, is4k bool) (requestResult, error) {
	payload := seerrCreateRequestInput{
		MediaType: "movie",
		MediaID:   tmdbID,
		Is4K:      is4k,
	}

	var created seerrCreateRequestResponse
	if err := s.postJSON(ctx, s.seerr, "/api/v1/request", payload, &created); err != nil {
		return requestResult{}, err
	}

	movie, err := s.getMovie(ctx, tmdbID)
	if err != nil {
		movie = movieSummary{TMDBID: tmdbID}
	}

	movie.IsRequested = true
	movie.RequestStatus = seerrRequestStatusName(created.Status)

	return requestResult{
		RequestID: created.ID,
		Status:    seerrRequestStatusName(created.Status),
		Movie:     movie,
	}, nil
}

func (s *movieService) getSeerrMovie(ctx context.Context, tmdbID int) (*seerrMovieDetail, error) {
	var detail seerrMovieDetail
	if err := s.getJSON(ctx, s.seerr, "/api/v1/movie/"+strconv.Itoa(tmdbID), nil, &detail); err != nil {
		return nil, err
	}
	return &detail, nil
}

func (s *movieService) getRadarrMovieByTMDB(ctx context.Context, tmdbID int) (radarrMovie, bool, error) {
	var movies []radarrMovie
	if err := s.getJSON(ctx, s.radarr, "/api/v3/movie", nil, &movies); err != nil {
		return radarrMovie{}, false, err
	}
	for _, movie := range movies {
		if movie.TMDBID == tmdbID {
			return movie, true, nil
		}
	}
	return radarrMovie{}, false, nil
}

func (s *movieService) getJSON(ctx context.Context, upstream upstream, path string, params url.Values, out any) error {
	reqURL := upstream.url + path
	if params != nil {
		reqURL += "?" + params.Encode()
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-Api-Key", upstream.apiKey)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("%s request failed: %w", upstream.name, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return fmt.Errorf("%s returned %d: %s", upstream.name, resp.StatusCode, strings.TrimSpace(string(body)))
	}

	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("%s decode failed: %w", upstream.name, err)
	}
	return nil
}

func (s *movieService) postJSON(ctx context.Context, upstream upstream, path string, payload, out any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, upstream.url+path, strings.NewReader(string(body)))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Api-Key", upstream.apiKey)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("%s request failed: %w", upstream.name, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return fmt.Errorf("%s returned %d: %s", upstream.name, resp.StatusCode, strings.TrimSpace(string(body)))
	}

	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("%s decode failed: %w", upstream.name, err)
	}
	return nil
}

func summarizeRadarr(movie radarrMovie) movieSummary {
	summary := movieSummary{
		TMDBID:        movie.TMDBID,
		Title:         movie.Title,
		Year:          movie.Year,
		Overview:      movie.Overview,
		Studio:        movie.Studio,
		FolderPath:    movie.FolderPath,
		Status:        movie.Status,
		MinimumStatus: movie.MinimumAvailability,
		InLibrary:     movie.ID != 0,
		Downloaded:    movie.HasFile,
		Monitored:     movie.Monitored,
		HasFile:       movie.HasFile,
		IsAvailable:   movie.IsAvailable,
		ImdbID:        movie.IMDBID,
		Website:       movie.Website,
		Rating:        movie.Ratings.Value,
	}
	if movie.ID != 0 {
		summary.RadarrID = &movie.ID
	}
	return summary
}

func summarizeQueueItem(item radarrQueueItem) queueItemSummary {
	out := queueItemSummary{
		Title:        item.Movie.Title,
		Year:         item.Movie.Year,
		TMDBID:       item.Movie.TMDBID,
		Status:       item.Status,
		TrackedState: item.TrackedDownload,
		Protocol:     item.Protocol,
		Indexer:      item.Indexer,
		OutputPath:   item.OutputPath,
		SizeLeft:     item.Sizeleft,
	}
	if msg := firstQueueMessage(item); msg != "" {
		out.ErrorMessage = msg
	} else if item.TrackedDownloadMsg != "" {
		out.ErrorMessage = item.TrackedDownloadMsg
	}
	if secs := parseDurationSeconds(item.Timeleft); secs > 0 {
		out.EstimatedSeconds = secs
	}
	return out
}

func firstQueueMessage(item radarrQueueItem) string {
	for _, status := range item.StatusMessages {
		for _, msg := range status.Messages {
			if msg.Message != "" {
				return msg.Message
			}
		}
		if status.Title != "" {
			return status.Title
		}
	}
	return ""
}

func mergeSeerrDetail(summary *movieSummary, detail *seerrMovieDetail) {
	if detail == nil {
		return
	}
	if detail.MediaInfo != nil {
		summary.IsAvailable = detail.MediaInfo.IsAvailable
		if detail.MediaInfo.Status != 0 {
			summary.RequestStatus = seerrMediaStatusName(detail.MediaInfo.Status)
		}
	}
	if detail.Request != nil {
		summary.IsRequested = true
		summary.RequestStatus = seerrRequestStatusName(detail.Request.Status)
	}
}

func containsMovie(movie radarrMovie, query string) bool {
	targets := []string{
		strings.ToLower(movie.Title),
		strings.ToLower(movie.Overview),
		strings.ToLower(movie.IMDBID),
		strings.ToLower(movie.Studio),
	}
	for _, target := range targets {
		if strings.Contains(target, query) {
			return true
		}
	}
	return false
}

func yearFromDate(value string) int {
	if len(value) < 4 {
		return 0
	}
	year, _ := strconv.Atoi(value[:4])
	return year
}

func parseDurationSeconds(value string) int64 {
	if value == "" {
		return 0
	}
	var total int64
	for _, field := range strings.Fields(value) {
		if len(field) < 2 {
			continue
		}
		unit := field[len(field)-1]
		n, err := strconv.ParseInt(field[:len(field)-1], 10, 64)
		if err != nil {
			continue
		}
		switch unit {
		case 'd':
			total += n * 86400
		case 'h':
			total += n * 3600
		case 'm':
			total += n * 60
		case 's':
			total += n
		}
	}
	return total
}

func seerrRequestStatusName(status int) string {
	switch status {
	case 1:
		return "pending"
	case 2:
		return "approved"
	case 3:
		return "declined"
	case 4:
		return "failed"
	default:
		return "unknown"
	}
}

func seerrMediaStatusName(status int) string {
	switch status {
	case 1:
		return "unknown"
	case 2:
		return "pending"
	case 3:
		return "processing"
	case 4:
		return "partially_available"
	case 5:
		return "available"
	default:
		return ""
	}
}
