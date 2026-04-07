package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"sync/atomic"
	"time"
)

type RateTestPhase struct {
	RPS      float64       // requests per second
	Duration time.Duration // how long to run this phase
}

type PhaseResult struct {
	RPS           float64
	TotalRequests int
	Successes     int
	Errors        int
	Status429     int
	Status403     int
	StatusOther   map[int]int
	Latencies     []float64 // ms
	ErrorMsgs     []string
}

func runRateTest(logger *AsyncLogger, client *http.Client, station string) {
	phases := []RateTestPhase{
		{RPS: 0.5, Duration: 20 * time.Second},  // baseline: 1 req/2s
		{RPS: 1, Duration: 30 * time.Second},     // 1 req/s
		{RPS: 2, Duration: 30 * time.Second},     // 2 req/s
		{RPS: 3, Duration: 30 * time.Second},     // 3 req/s
		{RPS: 5, Duration: 30 * time.Second},     // 5 req/s
		{RPS: 10, Duration: 30 * time.Second},    // 10 req/s
		{RPS: 20, Duration: 20 * time.Second},    // 20 req/s
	}

	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("WU API Rate Limit Test")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("Station:  %s", station)
	logger.Log("Location: %s", *flagLocation)
	logger.Log("Phases:   %d (%.1f -> %.1f req/s)", len(phases), phases[0].RPS, phases[len(phases)-1].RPS)
	logger.Log("Strategy: Gradual increase, stop on 429/403 or >20%% error rate")
	logger.Log("")

	// Warmup connection
	logger.Log("Warming up connection...")
	doV3Request(context.Background(), client, station)
	doV3Request(context.Background(), client, station)
	logger.Log("Connection warm. Starting rate test...")
	logger.Log("")

	var baselineLatency float64
	var allResults []PhaseResult
	stopped := false

	for i, phase := range phases {
		if stopped {
			break
		}

		logger.Log("-" + strings.Repeat("-", 79))
		logger.Log("Phase %d/%d: %.1f req/s for %v", i+1, len(phases), phase.RPS, phase.Duration)
		logger.Log("-" + strings.Repeat("-", 79))

		result := runPhase(client, station, phase)
		allResults = append(allResults, result)

		if i == 0 && len(result.Latencies) > 0 {
			sort.Float64s(result.Latencies)
			baselineLatency = percentile(result.Latencies, 50)
		}

		// Print phase summary
		errRate := 0.0
		if result.TotalRequests > 0 {
			errRate = float64(result.Errors) / float64(result.TotalRequests) * 100
		}
		var latP50, latP99, latAvg float64
		if len(result.Latencies) > 0 {
			sorted := make([]float64, len(result.Latencies))
			copy(sorted, result.Latencies)
			sort.Float64s(sorted)
			latP50 = percentile(sorted, 50)
			latP99 = percentile(sorted, 99)
			for _, v := range sorted {
				latAvg += v
			}
			latAvg /= float64(len(sorted))
		}

		statusStr := fmt.Sprintf("200:%d", result.Successes)
		if result.Status429 > 0 {
			statusStr += fmt.Sprintf(" 429:%d", result.Status429)
		}
		if result.Status403 > 0 {
			statusStr += fmt.Sprintf(" 403:%d", result.Status403)
		}
		for code, count := range result.StatusOther {
			statusStr += fmt.Sprintf(" %d:%d", code, count)
		}

		logger.Log("  Requests: %d | OK: %d | Errors: %d (%.1f%%)",
			result.TotalRequests, result.Successes, result.Errors, errRate)
		logger.Log("  Status codes: %s", statusStr)
		logger.Log("  Latency: avg=%.1fms p50=%.1fms p99=%.1fms", latAvg, latP50, latP99)

		if len(result.ErrorMsgs) > 0 {
			shown := result.ErrorMsgs
			if len(shown) > 3 {
				shown = shown[:3]
			}
			for _, msg := range shown {
				logger.Log("  Error sample: %s", msg)
			}
		}

		// Check stop conditions
		if result.Status429 > 0 {
			logger.Log("")
			logger.Log("⛔ 429 DETECTED at %.1f req/s — rate limit reached!", phase.RPS)
			stopped = true
		} else if result.Status403 > 0 {
			logger.Log("")
			logger.Log("⛔ 403 DETECTED at %.1f req/s — possible IP ban!", phase.RPS)
			stopped = true
		} else if errRate > 20 {
			logger.Log("")
			logger.Log("⛔ Error rate >20%% at %.1f req/s — stopping", phase.RPS)
			stopped = true
		} else if baselineLatency > 0 && latP50 > baselineLatency*5 {
			logger.Log("")
			logger.Log("⚠ Latency 5x degraded (baseline p50=%.1fms, current p50=%.1fms) — possible throttling", baselineLatency, latP50)
			stopped = true
		} else {
			logger.Log("  ✓ OK — no throttling detected")
		}

		if !stopped && i < len(phases)-1 {
			logger.Log("  Cooling down 5s before next phase...")
			time.Sleep(5 * time.Second)
		}
	}

	// Final summary
	logger.Log("")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("Rate Test Summary")
	logger.Log("=" + strings.Repeat("=", 79))
	logger.Log("")
	logger.Log("%-10s %-8s %-8s %-8s %-10s %-10s %-10s %s",
		"RPS", "Total", "OK", "Errors", "Err%", "p50(ms)", "p99(ms)", "Status")

	maxSafeRPS := 0.0
	for _, r := range allResults {
		errRate := 0.0
		if r.TotalRequests > 0 {
			errRate = float64(r.Errors) / float64(r.TotalRequests) * 100
		}
		var latP50, latP99 float64
		if len(r.Latencies) > 0 {
			sorted := make([]float64, len(r.Latencies))
			copy(sorted, r.Latencies)
			sort.Float64s(sorted)
			latP50 = percentile(sorted, 50)
			latP99 = percentile(sorted, 99)
		}
		status := "OK"
		if r.Status429 > 0 {
			status = "RATE LIMITED"
		} else if r.Status403 > 0 {
			status = "BANNED"
		} else if errRate > 20 {
			status = "HIGH ERRORS"
		}

		logger.Log("%-10.1f %-8d %-8d %-8d %-10.1f %-10.1f %-10.1f %s",
			r.RPS, r.TotalRequests, r.Successes, r.Errors, errRate, latP50, latP99, status)

		if status == "OK" && r.RPS > maxSafeRPS {
			maxSafeRPS = r.RPS
		}
	}

	logger.Log("")
	if maxSafeRPS > 0 {
		safeInterval := 1.0 / maxSafeRPS
		logger.Log("✅ Max safe rate: %.1f req/s (interval: %.1fs)", maxSafeRPS, safeInterval)
		logger.Log("   Recommended for production: %.1f req/s (interval: %.1fs) with safety margin",
			maxSafeRPS*0.7, safeInterval/0.7)
	} else {
		logger.Log("❌ No safe rate found — even lowest rate was throttled")
	}
	logger.Log("=" + strings.Repeat("=", 79))
}

func runPhase(client *http.Client, station string, phase RateTestPhase) PhaseResult {
	result := PhaseResult{
		RPS:         phase.RPS,
		StatusOther: make(map[int]int),
	}

	interval := time.Duration(float64(time.Second) / phase.RPS)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	deadline := time.After(phase.Duration)
	var count atomic.Int64

	for {
		select {
		case <-deadline:
			return result
		case <-ticker.C:
			count.Add(1)
			statusCode, latencyMs, err := doV3Request(context.Background(), client, station)
			result.TotalRequests++

			if err != nil {
				result.Errors++
				if len(result.ErrorMsgs) < 10 {
					result.ErrorMsgs = append(result.ErrorMsgs, err.Error())
				}
				continue
			}

			result.Latencies = append(result.Latencies, latencyMs)

			switch statusCode {
			case 200:
				result.Successes++
			case 429:
				result.Status429++
				result.Errors++
			case 403:
				result.Status403++
				result.Errors++
			default:
				result.StatusOther[statusCode]++
				result.Errors++
			}
		}
	}
}

func doV3Request(ctx context.Context, client *http.Client, station string) (statusCode int, latencyMs float64, err error) {
	req, err := http.NewRequestWithContext(ctx, "GET", v3BaseURL, nil)
	if err != nil {
		return 0, 0, err
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
	if err != nil {
		return 0, ms(time.Since(t0)), err
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()

	return resp.StatusCode, ms(time.Since(t0)), nil
}
