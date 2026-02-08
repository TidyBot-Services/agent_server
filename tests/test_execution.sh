#!/bin/bash
# Test script for code execution API

set -e

URL="http://localhost:8080"

echo "========================================="
echo "  Code Execution API Test"
echo "========================================="
echo

# Step 1: Acquire lease
echo "=== Acquiring lease ==="
LEASE_RESPONSE=$(curl -s -X POST "$URL/lease/acquire" \
  -H "Content-Type: application/json" \
  -d '{"holder": "test-script"}')

LEASE_ID=$(echo "$LEASE_RESPONSE" | jq -r '.lease_id')
echo "✓ Lease acquired: $LEASE_ID"
echo

# Step 2: Submit code
echo "=== Submitting code ==="
CODE_FILE="${1:-simple_move.py}"

if [ ! -f "$CODE_FILE" ]; then
    echo "Error: Code file '$CODE_FILE' not found"
    exit 1
fi

CODE=$(cat "$CODE_FILE")
CODE_JSON=$(jq -Rs . <<< "$CODE")

EXEC_RESPONSE=$(curl -s -X POST "$URL/code/execute" \
  -H "X-Lease-Id: $LEASE_ID" \
  -H "Content-Type: application/json" \
  -d "{\"code\": $CODE_JSON}")

echo "$EXEC_RESPONSE" | jq .

EXECUTION_ID=$(echo "$EXEC_RESPONSE" | jq -r '.execution_id')
echo "✓ Code submitted (execution ID: $EXECUTION_ID)"
echo

# Step 3: Poll status
echo "=== Polling status ==="
MAX_WAIT=60
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS_RESPONSE=$(curl -s "$URL/code/status")
    STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status')
    IS_RUNNING=$(echo "$STATUS_RESPONSE" | jq -r '.is_running')

    echo "Status: $STATUS (running: $IS_RUNNING)"

    if [ "$IS_RUNNING" = "false" ]; then
        break
    fi

    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo
echo "=== Execution result ==="
RESULT_RESPONSE=$(curl -s "$URL/code/result")
echo "$RESULT_RESPONSE" | jq .

# Extract key fields
RESULT_STATUS=$(echo "$RESULT_RESPONSE" | jq -r '.result.status')
DURATION=$(echo "$RESULT_RESPONSE" | jq -r '.result.duration')
EXIT_CODE=$(echo "$RESULT_RESPONSE" | jq -r '.result.exit_code')

echo
echo "========================================="
echo "  Result Summary"
echo "========================================="
echo "Status: $RESULT_STATUS"
echo "Duration: ${DURATION}s"
echo "Exit code: $EXIT_CODE"
echo

# Show stdout
echo "--- STDOUT ---"
echo "$RESULT_RESPONSE" | jq -r '.result.stdout'
echo

# Show stderr if any
STDERR=$(echo "$RESULT_RESPONSE" | jq -r '.result.stderr')
if [ -n "$STDERR" ] && [ "$STDERR" != "null" ] && [ "$STDERR" != "" ]; then
    echo "--- STDERR ---"
    echo "$STDERR"
    echo
fi

# Step 4: Release lease
echo "=== Releasing lease ==="
curl -s -X POST "$URL/lease/release" \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\": \"$LEASE_ID\"}" | jq .

echo "✓ Lease released"
echo

if [ "$RESULT_STATUS" = "completed" ]; then
    echo "✅ Test passed!"
    exit 0
else
    echo "❌ Test failed: $RESULT_STATUS"
    exit 1
fi
