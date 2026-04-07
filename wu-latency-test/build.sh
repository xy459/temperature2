#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Building wu-latency-test..."

# Local (macOS arm64)
echo "  -> darwin/arm64"
go build -o bin/wu-latency-test-darwin-arm64 .

# Linux amd64 (for servers)
echo "  -> linux/amd64"
GOOS=linux GOARCH=amd64 go build -o bin/wu-latency-test-linux-amd64 .

# Linux arm64
echo "  -> linux/arm64"
GOOS=linux GOARCH=arm64 go build -o bin/wu-latency-test-linux-arm64 .

echo ""
echo "Built binaries:"
ls -lh bin/

echo ""
echo "Deploy examples:"
echo "  scp bin/wu-latency-test-linux-amd64 sv-server:~/wu-latency-test"
echo "  scp bin/wu-latency-test-linux-amd64 fra-server:~/wu-latency-test"
echo ""
echo "Run examples:"
echo "  ./wu-latency-test --station=LEMD --location=silicon-valley"
echo "  ./wu-latency-test --station=LEMD --location=frankfurt"
