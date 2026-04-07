package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	apiKey       = "e1f10a1e78da46f5b10a1e78da96f525"
	v3BaseURL    = "https://api.weather.com/v3/wx/observations/current"
	v1BaseURLFmt = "https://api.weather.com/v1/location/%s:9:ES/observations/historical.json"
)

// ── CLI Flags ──────────────────────────────────────────────────────────────────

var (
	flagStation  = flag.String("station", "LEMD", "ICAO station code")
	flagLocation = flag.String("location", "unknown", "Server location tag (e.g. silicon-valley, frankfurt)")
	flagLogDir   = flag.String("log-dir", "./logs", "Log output directory")
	flagInterval = flag.Int("interval", 5, "Base polling interval in seconds (dense window)")
	flagVerbose  = flag.Bool("verbose", false, "Verbose output mode")
	flagRateTest = flag.Bool("rate-test", false, "Run rate limit test instead of latency test")
)

// ── Data Structures ────────────────────────────────────────────────────────────

type V3Response struct {
	Temperature        *float64 `json:"temperature"`
	TempMaxSince7AM    *float64 `json:"temperatureMaxSince7Am"`
	TempMax24H         *float64 `json:"temperatureMax24Hour"`
	TempMin24H         *float64 `json:"temperatureMin24Hour"`
	ValidTimeUTC       *int64   `json:"validTimeUtc"`
	ValidTimeLocal     string   `json:"validTimeLocal"`
	ObsQualifierCode   *string  `json:"obsQualifierCode"`
	ObsQualifierSeverity *int   `json:"obsQualifierSeverity"`
}

type V1Observation struct {
	ValidTimeGMT int64    `json:"valid_time_gmt"`
	Temp         *float64 `json:"temp"`
	WxPhraseLong *string  `json:"wx_phrase_long"`
}

type V1Response struct {
	Observations []V1Observation `json:"observations"`
	Metadata     json.RawMessage `json:"metadata"`
}

type PollRecord struct {
	Timestamp    time.Time `json:"timestamp"`
	Source       string    `json:"source"`
	IsNewData    bool      `json:"is_new_data"`
	ObsTimeUTC   int64     `json:"obs_time_utc"`
	Temperature  float64   `json:"temperature"`
	TempMax7AM   float64   `json:"temp_max_7am,omitempty"`
	DetectionMs  float64   `json:"detection_delay_ms"`
	HTTPMs       float64   `json:"http_ms"`
	BodyReadMs   float64   `json:"body_read_ms"`
	TotalMs      float64   `json:"total_ms"`
	CacheControl string    `json:"cache_control"`
	Age          string    `json:"age"`
	StatusCode   int       `json:"status_code"`
	BodySize     int       `json:"body_size"`
	Error        string    `json:"error,omitempty"`
	V1TotalObs   int       `json:"v1_total_obs,omitempty"`
}

type LatencyEvent struct {
	Timestamp   time.Time `json:"timestamp"`
	Source      string    `json:"source"`
	ObsTimeUTC  int64     `json:"obs_time_utc"`
	DelayMs     float64   `json:"delay_ms"`
	HTTPMs      float64   `json:"http_ms"`
	Temperature float64   `json:"temperature"`
	TempMax7AM  float64   `json:"temp_max_7am,omitempty"`
}

// ── Async Logger ───────────────────────────────────────────────────────────────

type AsyncLogger struct {
	ch      chan string
	file    *os.File
	wg      sync.WaitGroup
}

func NewAsyncLogger(path string) (*AsyncLogger, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return nil, err
	}
	l := &AsyncLogger{
		ch:   make(chan string, 1024),
		file: f,
	}
	l.wg.Add(1)
	go l.run()
	return l, nil
}

func (l *AsyncLogger) run() {
	defer l.wg.Done()
	for msg := range l.ch {
		fmt.Println(msg)
		fmt.Fprintln(l.file, msg)
	}
}

func (l *AsyncLogger) Log(format string, args ...any) {
	ts := time.Now().UTC().Format("2006-01-02 15:04:05.000 UTC")
	msg := fmt.Sprintf("[%s] %s", ts, fmt.Sprintf(format, args...))
	select {
	case l.ch <- msg:
	default:
		// channel full, write directly to avoid blocking hot path
		fmt.Println(msg)
		fmt.Fprintln(l.file, msg)
	}
}

func (l *AsyncLogger) Close() {
	close(l.ch)
	l.wg.Wait()
	l.file.Close()
}

// ── HTTP Client Setup ──────────────────────────────────────────────────────────

func buildHTTPClient() *http.Client {
	transport := &http.Transport{
		MaxIdleConns:        10,
		MaxIdleConnsPerHost: 5,
		IdleConnTimeout:     120 * time.Second,
		DisableCompression:  true,
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		TLSHandshakeTimeout: 5 * time.Second,
	}
	return &http.Client{
		Transport: transport,
		Timeout:   10 * time.Second,
	}
}

// ── API Fetch Functions ────────────────────────────────────────────────────────

func fetchV3(ctx context.Context, client *http.Client, station string) (*V3Response, *PollRecord) {
	record := &PollRecord{Source: "v3_current"}

	req, err := http.NewRequestWithContext(ctx, "GET", v3BaseURL, nil)
	if err != nil {
		record.Error = err.Error()
		return nil, record
	}
	q := req.URL.Query()
	q.Set("apiKey", apiKey)
	q.Set("language", "en-US")
	q.Set("units", "m")
	q.Set("format", "json")
	q.Set("icaoCode", station)
	req.URL.RawQuery = q.Encode()

	t0 := time.Now()
	resp, err := client.Do(req)
	tResp := time.Now()

	if err != nil {
		record.Error = err.Error()
		record.HTTPMs = ms(tResp.Sub(t0))
		return nil, record
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	tDone := time.Now()

	record.Timestamp = t0
	record.HTTPMs = ms(tResp.Sub(t0))
	record.BodyReadMs = ms(tDone.Sub(tResp))
	record.TotalMs = ms(tDone.Sub(t0))
	record.StatusCode = resp.StatusCode
	record.CacheControl = resp.Header.Get("Cache-Control")
	record.Age = resp.Header.Get("Age")
	record.BodySize = len(body)

	if err != nil {
		record.Error = fmt.Sprintf("body read: %v", err)
		return nil, record
	}

	var data V3Response
	if err := json.Unmarshal(body, &data); err != nil {
		record.Error = fmt.Sprintf("json parse: %v", err)
		return nil, record
	}

	if data.ValidTimeUTC != nil {
		record.ObsTimeUTC = *data.ValidTimeUTC
		record.DetectionMs = ms(tDone.Sub(time.Unix(*data.ValidTimeUTC, 0)))
	}
	if data.Temperature != nil {
		record.Temperature = *data.Temperature
	}
	if data.TempMaxSince7AM != nil {
		record.TempMax7AM = *data.TempMaxSince7AM
	}

	return &data, record
}

func fetchV1(ctx context.Context, client *http.Client, station string) (*V1Response, *PollRecord) {
	record := &PollRecord{Source: "v1_history"}
	dateStr := time.Now().UTC().Format("20060102")
	url := fmt.Sprintf(v1BaseURLFmt, station)

	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		record.Error = err.Error()
		return nil, record
	}
	q := req.URL.Query()
	q.Set("apiKey", apiKey)
	q.Set("units", "m")
	q.Set("startDate", dateStr)
	q.Set("endDate", dateStr)
	req.URL.RawQuery = q.Encode()

	t0 := time.Now()
	resp, err := client.Do(req)
	tResp := time.Now()

	if err != nil {
		record.Error = err.Error()
		record.HTTPMs = ms(tResp.Sub(t0))
		return nil, record
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	tDone := time.Now()

	record.Timestamp = t0
	record.HTTPMs = ms(tResp.Sub(t0))
	record.BodyReadMs = ms(tDone.Sub(tResp))
	record.TotalMs = ms(tDone.Sub(t0))
	record.StatusCode = resp.StatusCode
	record.CacheControl = resp.Header.Get("Cache-Control")
	record.Age = resp.Header.Get("Age")
	record.BodySize = len(body)

	if err != nil {
		record.Error = fmt.Sprintf("body read: %v", err)
		return nil, record
	}

	var data V1Response
	if err := json.Unmarshal(body, &data); err != nil {
		record.Error = fmt.Sprintf("json parse: %v", err)
		return nil, record
	}

	record.V1TotalObs = len(data.Observations)
	if n := len(data.Observations); n > 0 {
		last := data.Observations[n-1]
		record.ObsTimeUTC = last.ValidTimeGMT
		if last.Temp != nil {
			record.Temperature = *last.Temp
		}
		record.DetectionMs = ms(tDone.Sub(time.Unix(last.ValidTimeGMT, 0)))
	}

	return &data, record
}

// ── Polling Strategy ───────────────────────────────────────────────────────────

func getPollInterval(baseInterval int) time.Duration {
	now := time.Now().UTC()
	// Observations arrive every ~10 minutes (at :X0 ± ~2 min)
	// Use a 10-minute cycle: dense near each :X0 boundary
	secInCycle := (now.Minute()%10)*60 + now.Second() // 0-599 within 10-min cycle

	switch {
	case secInCycle <= 180: // 0-3 min past :X0 — just after expected observation
		if secInCycle <= 60 {
			return time.Duration(baseInterval) * time.Second // densest: first 60s
		}
		return time.Duration(baseInterval*2) * time.Second // 1-3 min: moderate
	case secInCycle >= 420: // 3 min before next :X0 — approaching next observation
		remaining := 600 - secInCycle
		if remaining <= 60 {
			return time.Duration(baseInterval) * time.Second // densest: last 60s
		}
		return time.Duration(baseInterval*2) * time.Second // 7-9 min: moderate
	default: // 3-7 min past :X0 — low probability window
		return time.Duration(baseInterval*3) * time.Second
	}
}

// ── Diagnostics ────────────────────────────────────────────────────────────────

func printStartupDiagnostics(logger *AsyncLogger, client *http.Client, station string) {
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("WU Latency Test (Go) — Startup Diagnostics")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("Go version:   %s", runtime.Version())
	logger.Log("OS/Arch:      %s/%s", runtime.GOOS, runtime.GOARCH)
	logger.Log("Station:      %s", station)
	logger.Log("Location:     %s", *flagLocation)
	logger.Log("Base interval: %ds (10-min cycle: dense at :X0 ±3min)", *flagInterval)
	logger.Log("Log dir:      %s", *flagLogDir)
	logger.Log("PID:          %d", os.Getpid())

	// DNS resolution
	logger.Log("-" + strings.Repeat("-", 79))
	logger.Log("DNS Resolution for api.weather.com:")
	t0 := time.Now()
	ips, err := net.LookupHost("api.weather.com")
	dnsMs := ms(time.Since(t0))
	if err != nil {
		logger.Log("  ERROR: %v (%.1fms)", err, dnsMs)
	} else {
		logger.Log("  Resolved to %d IPs in %.1fms:", len(ips), dnsMs)
		for _, ip := range ips {
			logger.Log("    %s", ip)
		}
	}

	// Connection warmup: v3
	logger.Log("-" + strings.Repeat("-", 79))
	logger.Log("Connection warmup (v3):")
	ctx := context.Background()
	v3Data, v3Rec := fetchV3(ctx, client, station)
	if v3Rec.Error != "" {
		logger.Log("  ERROR: %s", v3Rec.Error)
	} else {
		logger.Log("  HTTP: %.1fms | Body: %.1fms | Total: %.1fms | Size: %d bytes",
			v3Rec.HTTPMs, v3Rec.BodyReadMs, v3Rec.TotalMs, v3Rec.BodySize)
		logger.Log("  Status: %d | Cache: %s | Age: %s", v3Rec.StatusCode, v3Rec.CacheControl, v3Rec.Age)
		if v3Data != nil && v3Data.ValidTimeUTC != nil {
			obsStr := time.Unix(*v3Data.ValidTimeUTC, 0).UTC().Format("15:04:05 UTC")
			logger.Log("  Obs time: %s | Temp: %.0f°C | Max7AM: %.0f°C",
				obsStr, safeFloat(v3Data.Temperature), safeFloat(v3Data.TempMaxSince7AM))
		}
	}

	// Connection warmup: v1
	logger.Log("Connection warmup (v1):")
	v1Data, v1Rec := fetchV1(ctx, client, station)
	if v1Rec.Error != "" {
		logger.Log("  ERROR: %s", v1Rec.Error)
	} else {
		logger.Log("  HTTP: %.1fms | Body: %.1fms | Total: %.1fms | Size: %d bytes",
			v1Rec.HTTPMs, v1Rec.BodyReadMs, v1Rec.TotalMs, v1Rec.BodySize)
		logger.Log("  Status: %d | Cache: %s | Age: %s", v1Rec.StatusCode, v1Rec.CacheControl, v1Rec.Age)
		if v1Data != nil && len(v1Data.Observations) > 0 {
			last := v1Data.Observations[len(v1Data.Observations)-1]
			obsStr := time.Unix(last.ValidTimeGMT, 0).UTC().Format("15:04:05 UTC")
			logger.Log("  Total obs: %d | Latest: %s | Temp: %.0f°C",
				len(v1Data.Observations), obsStr, safeFloat(last.Temp))
		}
	}

	// Second v3 request to measure keep-alive benefit
	logger.Log("Keep-alive verification (v3 second request):")
	_, v3Rec2 := fetchV3(ctx, client, station)
	if v3Rec2.Error != "" {
		logger.Log("  ERROR: %s", v3Rec2.Error)
	} else {
		logger.Log("  HTTP: %.1fms (vs first: %.1fms, saved: %.1fms)",
			v3Rec2.HTTPMs, v3Rec.HTTPMs, v3Rec.HTTPMs-v3Rec2.HTTPMs)
	}

	logger.Log("-" + strings.Repeat("-", 79))
	logger.Log("Starting continuous polling... (Ctrl+C to stop)")
	logger.Log("")
}

func printShutdownStats(logger *AsyncLogger, events []LatencyEvent, records []PollRecord, startTime time.Time) {
	elapsed := time.Since(startTime)
	logger.Log("")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("WU Latency Test — Shutdown Summary")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("Location:     %s", *flagLocation)
	logger.Log("Station:      %s", *flagStation)
	logger.Log("Duration:     %s", elapsed.Round(time.Second))
	logger.Log("Total polls:  %d", len(records))

	v3Events := filterEvents(events, "v3_current")
	v1Events := filterEvents(events, "v1_history")

	if len(v3Events) > 0 {
		delays := extractDelays(v3Events)
		httpMs := extractHTTPMs(v3Events)
		logger.Log("")
		logger.Log("v3 Real-time API:")
		logger.Log("  New observations detected: %d", len(v3Events))
		printPercentiles(logger, "  Detection delay", delays)
		printPercentiles(logger, "  HTTP RTT", httpMs)
		logger.Log("  Detail:")
		for _, e := range v3Events {
			obsStr := time.Unix(e.ObsTimeUTC, 0).UTC().Format("15:04:05")
			detStr := e.Timestamp.UTC().Format("15:04:05.000")
			logger.Log("    Obs %s UTC -> Detected %s UTC = %.0fms (%.1fs) | %.0f°C | Max7AM: %.0f°C | HTTP: %.1fms",
				obsStr, detStr, e.DelayMs, e.DelayMs/1000, e.Temperature, e.TempMax7AM, e.HTTPMs)
		}
	} else {
		logger.Log("")
		logger.Log("v3 Real-time API: No new observations detected")
	}

	if len(v1Events) > 0 {
		delays := extractDelays(v1Events)
		httpMs := extractHTTPMs(v1Events)
		logger.Log("")
		logger.Log("v1 History API:")
		logger.Log("  New observations detected: %d", len(v1Events))
		printPercentiles(logger, "  Detection delay", delays)
		printPercentiles(logger, "  HTTP RTT", httpMs)
		logger.Log("  Detail:")
		for _, e := range v1Events {
			obsStr := time.Unix(e.ObsTimeUTC, 0).UTC().Format("15:04:05")
			detStr := e.Timestamp.UTC().Format("15:04:05.000")
			logger.Log("    Obs %s UTC -> Detected %s UTC = %.0fms (%.1fs) | %.0f°C | HTTP: %.1fms",
				obsStr, detStr, e.DelayMs, e.DelayMs/1000, e.Temperature, e.HTTPMs)
		}
	} else {
		logger.Log("")
		logger.Log("v1 History API: No new observations detected")
	}

	// HTTP RTT stats across all successful polls
	var allHTTP []float64
	for _, r := range records {
		if r.Error == "" {
			allHTTP = append(allHTTP, r.HTTPMs)
		}
	}
	if len(allHTTP) > 0 {
		logger.Log("")
		logger.Log("Overall HTTP RTT (all polls):")
		printPercentiles(logger, "  RTT", allHTTP)
	}

	logger.Log("=" + strings.Repeat("=", 79))
}

func printPercentiles(logger *AsyncLogger, label string, data []float64) {
	if len(data) == 0 {
		return
	}
	sorted := make([]float64, len(data))
	copy(sorted, data)
	sort.Float64s(sorted)

	avg := 0.0
	for _, v := range sorted {
		avg += v
	}
	avg /= float64(len(sorted))

	logger.Log("%s: min=%.1f avg=%.1f p50=%.1f p95=%.1f p99=%.1f max=%.1f ms (n=%d)",
		label,
		sorted[0],
		avg,
		percentile(sorted, 50),
		percentile(sorted, 95),
		percentile(sorted, 99),
		sorted[len(sorted)-1],
		len(sorted),
	)
}

// ── JSON Export ────────────────────────────────────────────────────────────────

type ExportData struct {
	Location   string         `json:"location"`
	Station    string         `json:"station"`
	StartTime  time.Time      `json:"start_time"`
	EndTime    time.Time      `json:"end_time"`
	Duration   string         `json:"duration"`
	Events     []LatencyEvent `json:"latency_events"`
	PollCount  int            `json:"poll_count"`
}

func exportJSON(path string, events []LatencyEvent, records []PollRecord, startTime time.Time) error {
	data := ExportData{
		Location:  *flagLocation,
		Station:   *flagStation,
		StartTime: startTime,
		EndTime:   time.Now(),
		Duration:  time.Since(startTime).Round(time.Second).String(),
		Events:    events,
		PollCount: len(records),
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(data)
}

// ── Main Loop ──────────────────────────────────────────────────────────────────

func main() {
	flag.Parse()

	os.MkdirAll(*flagLogDir, 0755)
	ts := time.Now().Format("20060102_150405")

	prefix := "wu_latency"
	if *flagRateTest {
		prefix = "wu_ratetest"
	}
	logPath := filepath.Join(*flagLogDir, fmt.Sprintf("%s_%s_%s.log", prefix, *flagLocation, ts))
	jsonPath := filepath.Join(*flagLogDir, fmt.Sprintf("%s_%s_%s.json", prefix, *flagLocation, ts))

	logger, err := NewAsyncLogger(logPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create log file: %v\n", err)
		os.Exit(1)
	}
	defer logger.Close()

	client := buildHTTPClient()

	if *flagRateTest {
		runRateTest(logger, client, *flagStation)
		return
	}

	printStartupDiagnostics(logger, client, *flagStation)

	// Initial state
	ctx := context.Background()
	v3Data, _ := fetchV3(ctx, client, *flagStation)
	v1Data, _ := fetchV1(ctx, client, *flagStation)

	var lastV3ObsTime int64
	var lastV1ObsCount int
	var lastV1LatestTime int64

	if v3Data != nil && v3Data.ValidTimeUTC != nil {
		lastV3ObsTime = *v3Data.ValidTimeUTC
	}
	if v1Data != nil {
		lastV1ObsCount = len(v1Data.Observations)
		if n := len(v1Data.Observations); n > 0 {
			lastV1LatestTime = v1Data.Observations[n-1].ValidTimeGMT
		}
	}

	// Signal handling
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	var (
		events   []LatencyEvent
		records  []PollRecord
		mu       sync.Mutex
		pollCount int
	)
	startTime := time.Now()

	// Main polling loop
	for {
		interval := getPollInterval(*flagInterval)

		select {
		case <-sigCh:
			mu.Lock()
			printShutdownStats(logger, events, records, startTime)
			if err := exportJSON(jsonPath, events, records, startTime); err != nil {
				logger.Log("Failed to export JSON: %v", err)
			} else {
				logger.Log("JSON exported: %s", jsonPath)
			}
			logger.Log("Log file: %s", logPath)
			mu.Unlock()
			// give async logger time to flush
			time.Sleep(100 * time.Millisecond)
			return
		case <-time.After(interval):
		}

		pollCount++

		// Parallel fetch v3 and v1
		var v3Rec, v1Rec *PollRecord
		var v3Resp *V3Response
		var v1Resp *V1Response
		var wg sync.WaitGroup

		wg.Add(2)
		go func() {
			defer wg.Done()
			v3Resp, v3Rec = fetchV3(ctx, client, *flagStation)
		}()
		go func() {
			defer wg.Done()
			v1Resp, v1Rec = fetchV1(ctx, client, *flagStation)
		}()
		wg.Wait()

		now := time.Now()
		mu.Lock()

		// Detect v3 new observation
		if v3Rec != nil && v3Rec.Error == "" {
			records = append(records, *v3Rec)
			if v3Resp != nil && v3Resp.ValidTimeUTC != nil && *v3Resp.ValidTimeUTC != lastV3ObsTime {
				obsTime := *v3Resp.ValidTimeUTC
				delayMs := ms(now.Sub(time.Unix(obsTime, 0)))
				evt := LatencyEvent{
					Timestamp:   now,
					Source:      "v3_current",
					ObsTimeUTC:  obsTime,
					DelayMs:     delayMs,
					HTTPMs:      v3Rec.HTTPMs,
					Temperature: safeFloat(v3Resp.Temperature),
					TempMax7AM:  safeFloat(v3Resp.TempMaxSince7AM),
				}
				events = append(events, evt)
				v3Rec.IsNewData = true

				obsStr := time.Unix(obsTime, 0).UTC().Format("15:04:05")
				logger.Log("⚡ v3 NEW OBS | obs: %s UTC | delay: %.0fms (%.1fs) | temp: %.0f°C | max7am: %.0f°C | http: %.1fms | cache: %s",
					obsStr, delayMs, delayMs/1000,
					safeFloat(v3Resp.Temperature),
					safeFloat(v3Resp.TempMaxSince7AM),
					v3Rec.HTTPMs, v3Rec.CacheControl)

				lastV3ObsTime = obsTime
			}
		} else if v3Rec != nil {
			records = append(records, *v3Rec)
			logger.Log("⚠ v3 ERROR: %s", v3Rec.Error)
		}

		// Detect v1 new observation
		if v1Rec != nil && v1Rec.Error == "" {
			records = append(records, *v1Rec)
			if v1Resp != nil {
				newCount := len(v1Resp.Observations)
				var newLatestTime int64
				if newCount > 0 {
					newLatestTime = v1Resp.Observations[newCount-1].ValidTimeGMT
				}
				if newCount != lastV1ObsCount || newLatestTime != lastV1LatestTime {
					delayMs := ms(now.Sub(time.Unix(newLatestTime, 0)))
					var temp float64
					if newCount > 0 && v1Resp.Observations[newCount-1].Temp != nil {
						temp = *v1Resp.Observations[newCount-1].Temp
					}
					evt := LatencyEvent{
						Timestamp:   now,
						Source:      "v1_history",
						ObsTimeUTC:  newLatestTime,
						DelayMs:     delayMs,
						HTTPMs:      v1Rec.HTTPMs,
						Temperature: temp,
					}
					events = append(events, evt)
					v1Rec.IsNewData = true

					obsStr := time.Unix(newLatestTime, 0).UTC().Format("15:04:05")
					logger.Log("📊 v1 NEW OBS | obs: %s UTC | delay: %.0fms (%.1fs) | total: %d | temp: %.0f°C | http: %.1fms | cache: %s",
						obsStr, delayMs, delayMs/1000,
						newCount, temp, v1Rec.HTTPMs, v1Rec.CacheControl)

					lastV1ObsCount = newCount
					lastV1LatestTime = newLatestTime
				}
			}
		} else if v1Rec != nil {
			records = append(records, *v1Rec)
			logger.Log("⚠ v1 ERROR: %s", v1Rec.Error)
		}

		// Heartbeat
		if pollCount%20 == 0 {
			nowUTC := time.Now().UTC()
			secInCycle := (nowUTC.Minute()%10)*60 + nowUTC.Second()
			nextBoundary := 600 - secInCycle

			parts := []string{}
			if v3Rec != nil && v3Rec.Error == "" {
				obsStr := time.Unix(v3Rec.ObsTimeUTC, 0).UTC().Format("15:04:05")
				parts = append(parts, fmt.Sprintf("v3: %s %.0f°C (%.1fms)", obsStr, v3Rec.Temperature, v3Rec.HTTPMs))
			}
			if v1Rec != nil && v1Rec.Error == "" {
				obsStr := time.Unix(v1Rec.ObsTimeUTC, 0).UTC().Format("15:04:05")
				parts = append(parts, fmt.Sprintf("v1: %dobs latest=%s (%.1fms)", v1Rec.V1TotalObs, obsStr, v1Rec.HTTPMs))
			}
			logger.Log("[heartbeat #%d] interval=%v | next_:X0=%ds | %s",
				pollCount, interval, nextBoundary, strings.Join(parts, " | "))
		}

		if *flagVerbose && pollCount%5 == 0 {
			if v3Rec != nil && v3Rec.Error == "" {
				logger.Log("[verbose #%d] v3 http=%.1fms body=%.1fms cache=%s age=%s",
					pollCount, v3Rec.HTTPMs, v3Rec.BodyReadMs, v3Rec.CacheControl, v3Rec.Age)
			}
		}

		mu.Unlock()
	}
}

// ── Helpers ────────────────────────────────────────────────────────────────────

func ms(d time.Duration) float64 {
	return float64(d.Nanoseconds()) / 1e6
}

func safeFloat(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := (p / 100) * float64(len(sorted)-1)
	lower := int(math.Floor(idx))
	upper := int(math.Ceil(idx))
	if lower == upper || upper >= len(sorted) {
		return sorted[lower]
	}
	frac := idx - float64(lower)
	return sorted[lower]*(1-frac) + sorted[upper]*frac
}

func filterEvents(events []LatencyEvent, source string) []LatencyEvent {
	var out []LatencyEvent
	for _, e := range events {
		if e.Source == source {
			out = append(out, e)
		}
	}
	return out
}

func extractDelays(events []LatencyEvent) []float64 {
	out := make([]float64, len(events))
	for i, e := range events {
		out[i] = e.DelayMs
	}
	return out
}

func extractHTTPMs(events []LatencyEvent) []float64 {
	out := make([]float64, len(events))
	for i, e := range events {
		out[i] = e.HTTPMs
	}
	return out
}
