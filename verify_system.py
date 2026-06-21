import time
import os
import sqlite3
import random

# Import backend classes directly to verify their logic
try:
    from backend import ConsistentHashRing, CacheNode, BatchWriteManager, config, get_suggestions_from_db, DB_PATH
except ImportError as e:
    print(f"Error importing backend: {e}")
    exit(1)

def run_tests():
    print("=" * 60)
    print("STARTING SEARCH TYPEAHEAD SYSTEM VERIFICATION SUITE")
    print("=" * 60)

    # ----------------------------------------------------
    # TEST 1: Consistent Hashing Ring Verification
    # ----------------------------------------------------
    print("\n[TEST 1] Verifying Consistent Hashing Ring...")
    ring = ConsistentHashRing(nodes=["Node-A", "Node-B", "Node-C"], virtual_nodes=50)
    
    # Verify deterministic routing
    prefix_tests = ["py", "con", "dist", "web", "mach"]
    routes = {}
    for p in prefix_tests:
        node1 = ring.get_node(p)
        node2 = ring.get_node(p)
        if node1 != node2:
            print(f"  [-] FAIL: Nondeterministic routing for prefix '{p}' ({node1} vs {node2})")
            return False
        routes[p] = node1
        print(f"  [+] Prefix '{p}' consistently routes to '{node1}'")
        
    # Verify distribution balance (should have entries for all nodes)
    dist = ring.get_distribution()
    print(f"  [+] Ring distribution: {dist}")
    if len(dist) != 3 or any(count != 50 for count in dist.values()):
        print("  [-] FAIL: Virtual nodes not mapped correctly")
        return False
    print("  [+] Hashing ring verified successfully.")

    # ----------------------------------------------------
    # TEST 2: Cache Node TTL and Hit/Miss Logic
    # ----------------------------------------------------
    print("\n[TEST 2] Verifying Cache Node behavior...")
    node = CacheNode("Test-Node")
    
    # Verify Miss
    val = node.get("py")
    if val is not None:
        print("  [-] FAIL: Expected cache miss, got value")
        return False
    print("  [+] Cache miss handled correctly.")
    
    # Verify Hit after setting
    mock_suggestions = [("python", 100), ("pytorch", 80)]
    node.set("py", mock_suggestions, ttl=1.5) # small TTL for test
    val = node.get("py")
    if val != mock_suggestions:
        print(f"  [-] FAIL: Expected cache hit value {mock_suggestions}, got {val}")
        return False
    print("  [+] Cache hit value retrieved correctly.")
    
    # Verify Expiration
    print("  [*] Waiting for TTL expiration (1.7s)...")
    time.sleep(1.7)
    val = node.get("py")
    if val is not None:
        print("  [-] FAIL: Expected cache key to be expired, but it was found")
        return False
    print("  [+] Cache TTL expiration verified successfully.")

    # ----------------------------------------------------
    # TEST 3: Batch Write Buffer & DB commits
    # ----------------------------------------------------
    print("\n[TEST 3] Verifying Batch Write Buffer...")
    
    # Use database connection to check state before/after
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Reset count of target query
    test_query = "python_verification_test_term"
    cursor.execute("DELETE FROM queries WHERE query = ?", (test_query,))
    cursor.execute("DELETE FROM recent_searches WHERE query = ?", (test_query,))
    conn.commit()
    
    # Configure buffer to flush after 5 writes or 2 seconds
    config.batch_threshold = 5
    config.batch_interval = 2.0
    
    bm = BatchWriteManager(DB_PATH)
    
    print("  [*] Submitting 3 searches (below threshold of 5)...")
    bm.add_search(test_query)
    bm.add_search(test_query)
    bm.add_search(test_query)
    
    # Check that it's NOT in the DB yet (durability/write-reduction check)
    cursor.execute("SELECT historical_count FROM queries WHERE query = ?", (test_query,))
    row = cursor.fetchone()
    if row is not None:
        print(f"  [-] FAIL: Batch write flushed database immediately when below threshold! Row: {row}")
        bm.shutdown()
        conn.close()
        return False
    print("  [+] Confirmed database has NOT been written to yet (write reduction in progress).")
    
    # Submit 2 more to trigger threshold flush (total 5)
    print("  [*] Submitting 2 more searches (triggering threshold 5)...")
    bm.add_search(test_query)
    bm.add_search(test_query)
    
    # Wait a moment for async DB write thread to complete
    time.sleep(0.5)
    
    # Check database now
    cursor.execute("SELECT historical_count FROM queries WHERE query = ?", (test_query,))
    row = cursor.fetchone()
    if row is None or row[0] != 5:
        print(f"  [-] FAIL: Expected DB count to be 5, got {row}")
        bm.shutdown()
        conn.close()
        return False
    print(f"  [+] Confirmed buffer flushed. DB historical_count for '{test_query}' is now: {row[0]} (Expected: 5)")
    
    # Test periodic time-based flush
    print("  [*] Submitting 2 more searches and waiting 2.5s for time-based flush...")
    bm.add_search(test_query)
    bm.add_search(test_query)
    time.sleep(2.5) # Batch interval is 2.0s
    
    cursor.execute("SELECT historical_count FROM queries WHERE query = ?", (test_query,))
    row = cursor.fetchone()
    if row is None or row[0] != 7:
        print(f"  [-] FAIL: Expected periodic time flush to increase count to 7, got {row}")
        bm.shutdown()
        conn.close()
        return False
    print(f"  [+] Confirmed periodic flush. DB historical_count for '{test_query}' is now: {row[0]} (Expected: 7)")
    
    # Clean up test manager
    bm.shutdown()
    
    # Clean up test terms from DB
    cursor.execute("DELETE FROM queries WHERE query = ?", (test_query,))
    cursor.execute("DELETE FROM recent_searches WHERE query = ?", (test_query,))
    conn.commit()
    conn.close()
    print("  [+] Batch write logic verified successfully.")

    # ----------------------------------------------------
    # TEST 4: Trending Recency-Aware Scoring Logic
    # ----------------------------------------------------
    print("\n[TEST 4] Verifying Trending Searches Recency-Aware Scoring...")
    
    # Let's seed a special term in the database manually
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    t_popular = "trending_test_popular"  # high historical count
    t_recent = "trending_test_recent"    # low historical count but searched just now
    
    cursor.execute("DELETE FROM queries WHERE query IN (?, ?)", (t_popular, t_recent))
    cursor.execute("DELETE FROM recent_searches WHERE query IN (?, ?)", (t_popular, t_recent))
    
    # Seed historical
    cursor.execute("INSERT INTO queries (query, historical_count) VALUES (?, ?)", (t_popular, 100))
    cursor.execute("INSERT INTO queries (query, historical_count) VALUES (?, ?)", (t_recent, 10))
    
    # Set configs
    config.ranking_mode = "basic"
    config.decay_window = 10.0 # 10s window
    config.boost_factor = 500.0 # high boost for recent searches
    
    conn.commit()
    
    # Verify Basic mode (sorted by historical count)
    suggestions = get_suggestions_from_db("trending_test")
    if not suggestions or suggestions[0][0] != t_popular:
        print(f"  [-] FAIL: Basic mode did not rank {t_popular} first. Suggestions: {suggestions}")
        conn.close()
        return False
    print(f"  [+] Basic mode sorting verified: {suggestions[0][0]} ranks first (Score: {suggestions[0][1]})")
    
    # Switch to Enhanced mode and inject a recent search for t_recent
    config.ranking_mode = "enhanced"
    cursor.execute("INSERT INTO recent_searches (query, timestamp) VALUES (?, ?)", (t_recent, time.time()))
    conn.commit()
    
    # Query suggestions again in Enhanced mode
    suggestions = get_suggestions_from_db("trending_test")
    print(f"  [*] Enhanced Suggestions: {suggestions}")
    if not suggestions or suggestions[0][0] != t_recent:
        print(f"  [-] FAIL: Enhanced mode did not rank recent search first. Suggestions: {suggestions}")
        conn.close()
        return False
    print(f"  [+] Enhanced mode sorting verified: {suggestions[0][0]} boosted to first (Score: {suggestions[0][1]} vs popular: {suggestions[1][1]})")
    
    # Wait for decay (11s) and check if it drops back below
    print("  [*] Waiting for recency window decay (11s)...")
    time.sleep(11.0)
    suggestions = get_suggestions_from_db("trending_test")
    print(f"  [*] Suggestions after decay: {suggestions}")
    if not suggestions or suggestions[0][0] != t_popular:
        print(f"  [-] FAIL: Search score did not decay back to basic ranks after time window elapsed.")
        conn.close()
        return False
    print(f"  [+] Decay verified successfully: {suggestions[0][0]} returned to rank 1.")
    
    # Clean up
    cursor.execute("DELETE FROM queries WHERE query IN (?, ?)", (t_popular, t_recent))
    cursor.execute("DELETE FROM recent_searches WHERE query IN (?, ?)", (t_popular, t_recent))
    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! SYSTEM LOGIC CORRECT.")
    print("=" * 60)
    return True

if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)
