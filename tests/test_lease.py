#!/usr/bin/env python3
"""Test lease manager — non-blocking ticket queue."""

import asyncio
import sys
from lease import LeaseManager
from config import LeaseConfig


async def test_immediate_grant():
    """When no one holds the lease, acquire grants immediately."""
    print("\n[Test 1] Immediate grant when lease is free")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        result = await mgr.acquire("alice")
        assert result["status"] == "granted"
        assert "lease_id" in result
        print(f"  result: {result}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_queued_ticket():
    """When lease is held, acquire returns a ticket immediately (non-blocking)."""
    print("\n[Test 2] Queued ticket (non-blocking)")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        # Alice gets the lease
        alice = await mgr.acquire("alice")
        assert alice["status"] == "granted"

        # Bob gets queued — returns immediately, no blocking
        bob = await mgr.acquire("bob")
        assert bob["status"] == "queued"
        assert "ticket_id" in bob
        assert bob["position"] == 1
        assert bob["queue_length"] == 1
        print(f"  bob queued: {bob}")

        # Charlie gets queued at position 2
        charlie = await mgr.acquire("charlie")
        assert charlie["status"] == "queued"
        assert charlie["position"] == 2
        assert charlie["queue_length"] == 2
        print(f"  charlie queued: {charlie}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_check_ticket():
    """Poll ticket status — waiting then granted after release."""
    print("\n[Test 3] Check ticket (poll for grant)")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        alice = await mgr.acquire("alice")
        bob = await mgr.acquire("bob")
        ticket_id = bob["ticket_id"]

        # Check — should be waiting
        status = mgr.check_ticket(ticket_id)
        assert status["status"] == "waiting"
        assert status["position"] == 1
        print(f"  before release: {status}")

        # Release alice → bob auto-granted
        await mgr.release(alice["lease_id"])

        # Check — should be granted now
        status = mgr.check_ticket(ticket_id)
        assert status["status"] == "granted"
        assert "lease_id" in status
        print(f"  after release: {status}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_idempotent_acquire():
    """Same holder calling acquire twice returns the same ticket."""
    print("\n[Test 4] Idempotent re-acquire")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        await mgr.acquire("alice")
        bob1 = await mgr.acquire("bob")
        bob2 = await mgr.acquire("bob")
        assert bob1["ticket_id"] == bob2["ticket_id"]
        assert bob2["status"] == "queued"
        print(f"  same ticket: {bob1['ticket_id']}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_cancel_ticket():
    """Cancel a waiting ticket removes from queue."""
    print("\n[Test 5] Cancel ticket")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        await mgr.acquire("alice")
        bob = await mgr.acquire("bob")
        charlie = await mgr.acquire("charlie")

        # Cancel bob
        result = mgr.cancel_ticket(bob["ticket_id"])
        assert result["status"] == "cancelled"
        print(f"  cancel result: {result}")

        # Charlie should now be position 1
        status = mgr.check_ticket(charlie["ticket_id"])
        assert status["position"] == 1
        print(f"  charlie after cancel: {status}")

        # Queue length should be 1
        lease_status = mgr.status()
        assert lease_status["queue_length"] == 1
        print("  PASS")
    finally:
        await mgr.stop()


async def test_cancel_granted_ticket():
    """Cancelling an already-granted ticket returns an error."""
    print("\n[Test 6] Cancel granted ticket → error")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        alice = await mgr.acquire("alice")
        bob = await mgr.acquire("bob")
        await mgr.release(alice["lease_id"])

        # Bob's ticket is now granted
        result = mgr.cancel_ticket(bob["ticket_id"])
        assert result["status"] == "error"
        assert "release" in result["message"].lower()
        print(f"  cancel granted: {result}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_check_not_found():
    """Checking a nonexistent ticket returns not_found."""
    print("\n[Test 7] Check nonexistent ticket")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        result = mgr.check_ticket("bogus-id")
        assert result["status"] == "not_found"
        print(f"  result: {result}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_clear_queue():
    """clear_queue cancels all tickets and revokes lease."""
    print("\n[Test 8] Clear queue")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        await mgr.acquire("alice")
        bob = await mgr.acquire("bob")
        charlie = await mgr.acquire("charlie")

        result = await mgr.clear_queue()
        assert result["status"] == "cleared"
        assert result["removed"] == 2
        assert result["lease_revoked"] is True
        print(f"  clear result: {result}")

        # Tickets should be cancelled
        bob_status = mgr.check_ticket(bob["ticket_id"])
        assert bob_status["status"] == "cancelled"
        charlie_status = mgr.check_ticket(charlie["ticket_id"])
        assert charlie_status["status"] == "cancelled"
        print("  PASS")
    finally:
        await mgr.stop()


async def test_status_includes_ticket_ids():
    """Status endpoint includes ticket_id in queue entries."""
    print("\n[Test 9] Status includes ticket IDs")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        await mgr.acquire("alice")
        bob = await mgr.acquire("bob")

        status = mgr.status()
        assert status["holder"] == "alice"
        assert status["queue_length"] == 1
        assert status["queue"][0]["holder"] == "bob"
        assert status["queue"][0]["ticket_id"] == bob["ticket_id"]
        assert status["queue"][0]["position"] == 1
        assert "lease_id" not in status  # security: lease_id not in public status
        print(f"  status: {status}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_already_held():
    """Current holder calling acquire gets already_held with their lease_id."""
    print("\n[Test 10] Already held")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        alice = await mgr.acquire("alice")
        again = await mgr.acquire("alice")
        assert again["status"] == "already_held"
        assert again["lease_id"] == alice["lease_id"]
        print(f"  result: {again}")
        print("  PASS")
    finally:
        await mgr.stop()


async def test_queue_progression():
    """Multiple holders get granted in order as leases are released."""
    print("\n[Test 11] Queue progression")
    config = LeaseConfig(idle_timeout_s=5.0, max_duration_s=30.0,
                         check_interval_s=1.0, reset_on_release=False)
    mgr = LeaseManager(config, lambda: 0.0)
    await mgr.start()
    try:
        alice = await mgr.acquire("alice")
        bob = await mgr.acquire("bob")
        charlie = await mgr.acquire("charlie")

        # Release alice → bob gets granted
        await mgr.release(alice["lease_id"])
        bob_status = mgr.check_ticket(bob["ticket_id"])
        assert bob_status["status"] == "granted"
        bob_lease_id = bob_status["lease_id"]
        print(f"  bob granted: {bob_status}")

        # Charlie still waiting at position 1 now
        charlie_status = mgr.check_ticket(charlie["ticket_id"])
        assert charlie_status["status"] == "waiting"
        assert charlie_status["position"] == 1
        print(f"  charlie waiting: {charlie_status}")

        # Release bob → charlie gets granted
        await mgr.release(bob_lease_id)
        charlie_status = mgr.check_ticket(charlie["ticket_id"])
        assert charlie_status["status"] == "granted"
        print(f"  charlie granted: {charlie_status}")
        print("  PASS")
    finally:
        await mgr.stop()


async def main():
    tests = [
        test_immediate_grant,
        test_queued_ticket,
        test_check_ticket,
        test_idempotent_acquire,
        test_cancel_ticket,
        test_cancel_granted_ticket,
        test_check_not_found,
        test_clear_queue,
        test_status_includes_ticket_ids,
        test_already_held,
        test_queue_progression,
    ]

    print("=" * 60)
    print("Lease Manager Tests — Non-Blocking Ticket Queue")
    print("=" * 60)

    passed = 0
    failed = 0
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
