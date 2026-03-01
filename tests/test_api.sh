#!/bin/bash
# API test script for agent-server
# Usage: ./tests/test_api.sh [--with-gripper]

set -e
BASE_URL="${BASE_URL:-http://localhost:8080}"
WITH_GRIPPER=false

if [[ "$1" == "--with-gripper" ]]; then
    WITH_GRIPPER=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; }
skip() { echo -e "${YELLOW}⊘ $1 (skipped)${NC}"; }

echo "========================================"
echo "  TidyBot Agent Server API Tests"
echo "  URL: $BASE_URL"
echo "========================================"
echo ""

# --- GET Endpoints (no lease required) ---
echo "=== GET Endpoints ==="

# Health
RESP=$(curl -s "$BASE_URL/health")
if echo "$RESP" | grep -q '"status":"ok"'; then
    pass "GET /health"
    echo "    Backends: $(echo "$RESP" | python3 -c "import sys,json; b=json.load(sys.stdin)['backends']; print(' '.join(f'{k}={v}' for k,v in b.items()))")"
else
    fail "GET /health"
fi

# State
RESP=$(curl -s "$BASE_URL/state")
if echo "$RESP" | grep -q '"arm"'; then
    pass "GET /state"
else
    fail "GET /state"
fi

# Trajectory
RESP=$(curl -s "$BASE_URL/trajectory")
if echo "$RESP" | grep -q '"waypoints"'; then
    COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
    pass "GET /trajectory (waypoints: $COUNT)"
else
    fail "GET /trajectory"
fi

# Lease status
RESP=$(curl -s "$BASE_URL/lease/status")
if echo "$RESP" | grep -q 'holder'; then
    pass "GET /lease/status"
else
    fail "GET /lease/status"
fi

# Rewind status
RESP=$(curl -s "$BASE_URL/rewind/status")
if echo "$RESP" | grep -q '"is_rewinding"'; then
    pass "GET /rewind/status"
else
    fail "GET /rewind/status"
fi

# Rewind config
RESP=$(curl -s "$BASE_URL/rewind/config")
if echo "$RESP" | grep -q '"base_settle_time"'; then
    pass "GET /rewind/config"
else
    fail "GET /rewind/config"
fi

# Rewind check
RESP=$(curl -s "$BASE_URL/rewind/check")
if echo "$RESP" | grep -q '"out_of_bounds"'; then
    pass "GET /rewind/check"
else
    fail "GET /rewind/check"
fi

# Rewind boundary
RESP=$(curl -s "$BASE_URL/rewind/boundary")
if echo "$RESP" | grep -q '"x_min"'; then
    pass "GET /rewind/boundary"
else
    fail "GET /rewind/boundary"
fi

# Safety monitor status
RESP=$(curl -s "$BASE_URL/rewind/monitor/status")
if echo "$RESP" | grep -q '"is_running"'; then
    pass "GET /rewind/monitor/status"
else
    fail "GET /rewind/monitor/status"
fi

echo ""
echo "=== POST Endpoints (lease required) ==="

# Acquire lease
RESP=$(curl -s -X POST "$BASE_URL/lease/acquire" \
    -H "Content-Type: application/json" \
    -d '{"holder": "test-script"}')
if echo "$RESP" | grep -q '"status":"granted"'; then
    LEASE=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['lease_id'])")
    pass "POST /lease/acquire (lease: ${LEASE:0:8}...)"
else
    fail "POST /lease/acquire"
    echo "Cannot continue without lease"
    exit 1
fi

# Lease extend
RESP=$(curl -s -X POST "$BASE_URL/lease/extend" \
    -H "Content-Type: application/json" \
    -d "{\"lease_id\": \"$LEASE\"}")
if echo "$RESP" | grep -q '"status":"extended"'; then
    pass "POST /lease/extend"
else
    fail "POST /lease/extend"
fi

# Code execution - validate
RESP=$(curl -s "$BASE_URL/code/sdk")
if echo "$RESP" | grep -q '"modules"'; then
    pass "GET /code/sdk"
else
    fail "GET /code/sdk: $RESP"
fi

# Code execution - simple test (print only, no hardware)
RESP=$(curl -s -X POST "$BASE_URL/code/execute" \
    -H "X-Lease-Id: $LEASE" \
    -H "Content-Type: application/json" \
    -d '{"code": "print(\"hello from code execution\")", "timeout": 10}')
if echo "$RESP" | grep -q '"execution_id"'; then
    pass "POST /code/execute (submitted)"
else
    fail "POST /code/execute: $RESP"
fi

# Wait for code execution to complete
sleep 2

RESP=$(curl -s "$BASE_URL/code/status")
if echo "$RESP" | grep -q '"is_running"'; then
    pass "GET /code/status"
else
    fail "GET /code/status: $RESP"
fi

RESP=$(curl -s "$BASE_URL/code/result")
if echo "$RESP" | grep -q '"result"'; then
    pass "GET /code/result"
else
    fail "GET /code/result: $RESP"
fi

# Rewind steps (dry_run)
RESP=$(curl -s -X POST "$BASE_URL/rewind/steps" \
    -H "X-Lease-Id: $LEASE" \
    -H "Content-Type: application/json" \
    -d '{"steps": 3, "dry_run": true}')
if echo "$RESP" | grep -q '"success":true'; then
    STEPS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['steps_rewound'])")
    pass "POST /rewind/steps (dry_run, steps: $STEPS)"
else
    fail "POST /rewind/steps: $RESP"
fi

# Rewind percentage (dry_run)
RESP=$(curl -s -X POST "$BASE_URL/rewind/percentage" \
    -H "X-Lease-Id: $LEASE" \
    -H "Content-Type: application/json" \
    -d '{"percentage": 1.0, "dry_run": true}')
if echo "$RESP" | grep -q '"success":true'; then
    pass "POST /rewind/percentage (dry_run)"
else
    fail "POST /rewind/percentage: $RESP"
fi

# Release lease
RESP=$(curl -s -X POST "$BASE_URL/lease/release" \
    -H "Content-Type: application/json" \
    -d "{\"lease_id\": \"$LEASE\"}")
if echo "$RESP" | grep -q '"status":"released"'; then
    pass "POST /lease/release"
else
    fail "POST /lease/release: $RESP"
fi

echo ""
echo "========================================"
echo "  Tests complete"
echo "========================================"
