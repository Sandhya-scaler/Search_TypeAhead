from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import time
import sqlite3
import hashlib
import bisect
import threading
import random
from collections import Counter, deque

app = FastAPI(title="Search Typeahead System API")

# Enable CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.path.join(os.path.dirname(__file__), "search_database.db")

# ----------------------------------------------------
# 1. System Config & State State
# ----------------------------------------------------
class Config:
    def __init__(self):
        self.ranking_mode = "enhanced"  # "basic" or "enhanced"
        self.batch_threshold = 10       # flush writes when buffer size reaches this
        self.batch_interval = 5.0      # flush writes after this many seconds
        self.cache_ttl = 30.0          # cache time-to-live in seconds
        self.decay_window = 60.0       # trending searches recency window in seconds
        self.boost_factor = 50.0       # recency boost multiplier

config = Config()

class MetricsTracker:
    def __init__(self):
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_reads = 0
        self.db_writes = 0
        self.api_searches = 0
        self.writes_saved = 0
        self.latency_history = deque(maxlen=100) # stores suggestions response latency in ms
        self.routing_logs = deque(maxlen=30)
        self.lock = threading.Lock()

    def record_latency(self, latency_ms: float):
        with self.lock:
            self.latency_history.append(latency_ms)

    def increment_metric(self, name: str, count: int = 1):
        with self.lock:
            val = getattr(self, name, 0)
            setattr(self, name, val + count)

    def get_avg_p95_latency(self):
        with self.lock:
            if not self.latency_history:
                return 0.0, 0.0
            sorted_latencies = sorted(list(self.latency_history))
            avg_l = sum(sorted_latencies) / len(sorted_latencies)
            p95_idx = int(len(sorted_latencies) * 0.95)
            p95_l = sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)]
            return round(avg_l, 2), round(p95_l, 2)

    def log_routing(self, message: str):
        with self.lock:
            timestamp = time.strftime("%H:%M:%S")
            self.routing_logs.append(f"[{timestamp}] {message}")

metrics = MetricsTracker()

# ----------------------------------------------------
# 2. Consistent Hashing Ring
# ----------------------------------------------------
class ConsistentHashRing:
    def __init__(self, nodes: List[str] = None, virtual_nodes: int = 50):
        self.virtual_nodes = virtual_nodes
        self.ring = {}  # hash -> physical_node
        self.sorted_keys = []
        self.lock = threading.Lock()
        if nodes:
            for node in nodes:
                self.add_node(node)

    def _hash(self, key: str) -> int:
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)

    def add_node(self, node: str):
        with self.lock:
            for i in range(self.virtual_nodes):
                v_node_key = f"{node}-vnode-{i}"
                h = self._hash(v_node_key)
                self.ring[h] = node
                self.sorted_keys.append(h)
            self.sorted_keys.sort()
            metrics.log_routing(f"Added Cache Node '{node}' to Consistent Hashing Ring with {self.virtual_nodes} virtual nodes.")

    def remove_node(self, node: str):
        with self.lock:
            for i in range(self.virtual_nodes):
                v_node_key = f"{node}-vnode-{i}"
                h = self._hash(v_node_key)
                if h in self.ring:
                    del self.ring[h]
                    self.sorted_keys.remove(h)
            metrics.log_routing(f"Removed Cache Node '{node}' from Consistent Hashing Ring.")

    def get_node(self, key: str) -> str:
        with self.lock:
            if not self.ring:
                return None
            h = self._hash(key)
            idx = bisect.bisect_right(self.sorted_keys, h)
            if idx == len(self.sorted_keys):
                idx = 0
            responsible_node = self.ring[self.sorted_keys[idx]]
            return responsible_node

    def get_distribution(self):
        # Count number of virtual nodes assigned to each physical node
        dist = Counter(self.ring.values())
        return dict(dist)

# Initialize 3 logical cache nodes
hash_ring = ConsistentHashRing(nodes=["Cache-Node-A", "Cache-Node-B", "Cache-Node-C"])

# ----------------------------------------------------
# 3. Cache Node Simulation
# ----------------------------------------------------
class CacheNode:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.store = {}  # prefix -> (suggestions_list, expiry_timestamp)
        self.lock = threading.Lock()

    def get(self, prefix: str):
        with self.lock:
            if prefix in self.store:
                suggestions, expiry = self.store[prefix]
                if time.time() < expiry:
                    metrics.increment_metric("cache_hits")
                    metrics.log_routing(f"[{self.node_id}] HIT for prefix '{prefix}'")
                    return suggestions
                else:
                    del self.store[prefix]
                    metrics.log_routing(f"[{self.node_id}] EXPIRED key '{prefix}'")
            metrics.increment_metric("cache_misses")
            metrics.log_routing(f"[{self.node_id}] MISS for prefix '{prefix}'")
            return None

    def set(self, prefix: str, suggestions: List[tuple], ttl: float):
        with self.lock:
            self.store[prefix] = (suggestions, time.time() + ttl)
            metrics.log_routing(f"[{self.node_id}] Cached prefix '{prefix}' with TTL={ttl}s")

    def invalidate(self, prefix: str):
        with self.lock:
            if prefix in self.store:
                del self.store[prefix]
                metrics.log_routing(f"[{self.node_id}] INVALIDATED prefix '{prefix}' due to DB update")

    def clear(self):
        with self.lock:
            self.store.clear()

    def get_keys(self) -> List[str]:
        with self.lock:
            now = time.time()
            return [k for k, v in self.store.items() if v[1] > now]

# Instantiate nodes
cache_nodes = {
    "Cache-Node-A": CacheNode("Cache-Node-A"),
    "Cache-Node-B": CacheNode("Cache-Node-B"),
    "Cache-Node-C": CacheNode("Cache-Node-C")
}

def invalidate_cache_for_queries(queries: List[str]):
    # For each query, invalidate all its possible prefixes in the cache nodes
    invalidated_prefixes = set()
    for query in queries:
        for i in range(1, len(query) + 1):
            prefix = query[:i]
            if prefix not in invalidated_prefixes:
                responsible_node = hash_ring.get_node(prefix)
                if responsible_node:
                    cache_nodes[responsible_node].invalidate(prefix)
                    invalidated_prefixes.add(prefix)

# ----------------------------------------------------
# 4. Batch Writes Manager
# ----------------------------------------------------
class BatchWriteManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.buffer = []  # list of (query, timestamp)
        self.lock = threading.Lock()
        self.last_flush = time.time()
        self.is_running = True
        self.flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self.flush_thread.start()

    def add_search(self, query: str):
        with self.lock:
            self.buffer.append((query, time.time()))
            buf_len = len(self.buffer)
        
        metrics.increment_metric("api_searches")
        
        # Immediate flush check if threshold exceeded
        if buf_len >= config.batch_threshold:
            self.flush()

    def flush(self):
        with self.lock:
            if not self.buffer:
                return
            to_flush = self.buffer.copy()
            self.buffer.clear()
            self.last_flush = time.time()

        # Database insertion
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Aggregate buffer queries to show write reduction
        counter = Counter(q for q, ts in to_flush)
        unique_queries_count = len(counter)
        total_queries_count = len(to_flush)
        writes_saved = total_queries_count - 1 # batch saves transaction overheads

        try:
            cursor.execute("BEGIN TRANSACTION")
            
            # Upsert counts in database
            for q, count in counter.items():
                cursor.execute("""
                    INSERT INTO queries (query, historical_count) 
                    VALUES (?, ?) 
                    ON CONFLICT(query) 
                    DO UPDATE SET historical_count = historical_count + excluded.historical_count
                """, (q, count))
                
            # Log individual timestamp records for recency
            cursor.executemany(
                "INSERT INTO recent_searches (query, timestamp) VALUES (?, ?)",
                [(q, ts) for q, ts in to_flush]
            )
            
            conn.commit()
            metrics.increment_metric("db_writes")
            metrics.increment_metric("writes_saved", writes_saved)
            metrics.log_routing(f"[Batch Writer] Flushed {total_queries_count} searches ({unique_queries_count} unique) in 1 DB transaction. Saved {writes_saved} write transactions!")
            
            # Invalidate corresponding cache entries
            queries_updated = list(counter.keys())
            invalidate_cache_for_queries(queries_updated)

        except Exception as e:
            conn.rollback()
            metrics.log_routing(f"[Batch Writer] ERROR during DB write: {e}")
        finally:
            conn.close()

    def _periodic_flush(self):
        while self.is_running:
            time.sleep(0.5)
            # Check duration
            with self.lock:
                elapsed = time.time() - self.last_flush
                buf_len = len(self.buffer)
            
            if buf_len > 0 and elapsed >= config.batch_interval:
                self.flush()

    def shutdown(self):
        self.is_running = False
        self.flush()

batch_writer = BatchWriteManager(DB_PATH)

# ----------------------------------------------------
# 5. Database Suggestion Fetching (Ranking Engines)
# ----------------------------------------------------
def get_suggestions_from_db(prefix: str, limit: int = 10) -> List[tuple]:
    metrics.increment_metric("db_reads")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if config.ranking_mode == "basic":
            # Simple sorting by overall counts
            cursor.execute("""
                SELECT query, historical_count 
                FROM queries 
                WHERE query LIKE ? 
                ORDER BY historical_count DESC 
                LIMIT ?
            """, (f"{prefix}%", limit))
            results = cursor.fetchall()
            return [(r[0], r[1]) for r in results]
            
        else: # Enhanced / Trending Recency-Aware Sorting
            # Join matching queries with recent searches in the decay window
            min_timestamp = time.time() - config.decay_window
            cursor.execute("""
                SELECT q.query, q.historical_count, r.timestamp 
                FROM queries q
                LEFT JOIN recent_searches r ON q.query = r.query AND r.timestamp > ?
                WHERE q.query LIKE ?
            """, (min_timestamp, f"{prefix}%"))
            rows = cursor.fetchall()
            
            # Process in memory to apply linear decay formula
            query_groups = {}
            for q, hist_count, ts in rows:
                if q not in query_groups:
                    query_groups[q] = {"hist": hist_count, "timestamps": []}
                if ts is not None:
                    query_groups[q]["timestamps"].append(ts)
            
            now = time.time()
            scored_queries = []
            
            for q, info in query_groups.items():
                score = float(info["hist"])
                for ts in info["timestamps"]:
                    dt = now - ts
                    # Linear decay score: starts at boost_factor, decays linearly to 0 at decay_window seconds
                    decay_ratio = max(0.0, 1.0 - (dt / config.decay_window))
                    score += decay_ratio * config.boost_factor
                scored_queries.append((q, int(score)))
                
            # Sort by total score
            scored_queries.sort(key=lambda x: x[1], reverse=True)
            return scored_queries[:limit]

    except Exception as e:
        metrics.log_routing(f"[DB Error] Suggestions fetch error: {e}")
        return []
    finally:
        conn.close()

# ----------------------------------------------------
# 6. API Endpoints
# ----------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def read_root():
    return FileResponse("index.html")

@app.get("/style.css")
def get_css():
    return FileResponse("style.css")

@app.get("/suggest")
def get_suggestions(q: str = Query("", min_length=0)):
    start_time = time.perf_counter()
    prefix = q.strip().lower()

    if not prefix:
        return []

    # 1. Resolve cache node routing using Consistent Hashing
    responsible_node_id = hash_ring.get_node(prefix)
    node = cache_nodes[responsible_node_id]

    # 2. Query cache node
    suggestions = node.get(prefix)

    # 3. Cache Miss - Query DB & populate cache
    if suggestions is None:
        suggestions = get_suggestions_from_db(prefix, limit=10)
        node.set(prefix, suggestions, config.cache_ttl)

    # Calculate latency metrics
    latency_ms = (time.perf_counter() - start_time) * 1000.0
    metrics.record_latency(latency_ms)

    # Format result: [{query, score}]
    return [{"query": item[0], "score": item[1]} for item in suggestions]

@app.post("/search")
def submit_search(body: dict = Body(...)):
    query = body.get("query", "").strip().lower()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query")
    
    # Send to batch write buffer
    batch_writer.add_search(query)
    return {"message": "Searched", "query": query}

@app.get("/cache/debug")
def debug_cache(prefix: str = Query("", min_length=0)):
    prefix = prefix.strip().lower()
    if not prefix:
         return {"error": "Provide a prefix"}
         
    node_id = hash_ring.get_node(prefix)
    node = cache_nodes[node_id]
    
    # Check if currently cached
    with node.lock:
        is_hit = prefix in node.store
        if is_hit:
            suggestions, expiry = node.store[prefix]
            time_left = max(0.0, expiry - time.time())
            if time_left == 0.0:
                is_hit = False
                
    return {
        "prefix": prefix,
        "responsible_node": node_id,
        "is_cached": is_hit,
        "ttl_remaining_seconds": round(time_left, 2) if is_hit else 0.0,
        "hash_fraction": hash_ring._hash(prefix) / (2**128 - 1)
    }

@app.get("/cache/ring")
def get_cache_ring():
    with hash_ring.lock:
        MAX_HASH = 2**128 - 1
        positions = []
        for h, node in hash_ring.ring.items():
            positions.append({
                "fraction": h / MAX_HASH,
                "node": node
            })
        positions.sort(key=lambda x: x["fraction"])
        return positions

@app.get("/metrics")
def get_metrics():
    avg_lat, p95_lat = metrics.get_avg_p95_latency()
    
    # Get active keys in each cache node
    cache_contents = {}
    for node_id, node in cache_nodes.items():
        cache_contents[node_id] = node.get_keys()
        
    with batch_writer.lock:
        buffer_size = len(batch_writer.buffer)
        current_buffer = [item[0] for item in batch_writer.buffer]

    # Calculate overall cache hit rate
    total_cache_reqs = metrics.cache_hits + metrics.cache_misses
    hit_rate = (metrics.cache_hits / total_cache_reqs * 100.0) if total_cache_reqs > 0 else 0.0

    return {
        "cache_hits": metrics.cache_hits,
        "cache_misses": metrics.cache_misses,
        "cache_hit_rate_pct": round(hit_rate, 2),
        "db_reads": metrics.db_reads,
        "db_writes": metrics.db_writes,
        "api_searches": metrics.api_searches,
        "writes_saved": metrics.writes_saved,
        "avg_latency_ms": avg_lat,
        "p95_latency_ms": p95_lat,
        "buffer_size": buffer_size,
        "current_buffer": current_buffer,
        "hash_ring_distribution": hash_ring.get_distribution(),
        "cache_contents": cache_contents,
        "logs": list(metrics.routing_logs),
        "config": {
            "ranking_mode": config.ranking_mode,
            "batch_threshold": config.batch_threshold,
            "batch_interval": config.batch_interval,
            "cache_ttl": config.cache_ttl,
            "decay_window": config.decay_window,
            "boost_factor": config.boost_factor
        }
    }

class ConfigUpdate(BaseModel):
    ranking_mode: Optional[str] = None
    batch_threshold: Optional[int] = None
    batch_interval: Optional[float] = None
    cache_ttl: Optional[float] = None
    decay_window: Optional[float] = None
    boost_factor: Optional[float] = None

@app.post("/config")
def update_config(update: ConfigUpdate):
    if update.ranking_mode is not None:
        if update.ranking_mode in ["basic", "enhanced"]:
            config.ranking_mode = update.ranking_mode
            metrics.log_routing(f"Config updated: ranking_mode = {config.ranking_mode}")
            
    if update.batch_threshold is not None:
        config.batch_threshold = max(1, update.batch_threshold)
        metrics.log_routing(f"Config updated: batch_threshold = {config.batch_threshold}")
        
    if update.batch_interval is not None:
        config.batch_interval = max(0.5, update.batch_interval)
        metrics.log_routing(f"Config updated: batch_interval = {config.batch_interval}")
        
    if update.cache_ttl is not None:
        config.cache_ttl = max(1.0, update.cache_ttl)
        metrics.log_routing(f"Config updated: cache_ttl = {config.cache_ttl}")
        
    if update.decay_window is not None:
        config.decay_window = max(5.0, update.decay_window)
        metrics.log_routing(f"Config updated: decay_window = {config.decay_window}")
        
    if update.boost_factor is not None:
        config.boost_factor = max(0.0, update.boost_factor)
        metrics.log_routing(f"Config updated: boost_factor = {config.boost_factor}")

    return {"status": "success", "message": "Configuration updated"}

@app.post("/config/flush")
def trigger_manual_flush():
    batch_writer.flush()
    return {"status": "success", "message": "Manual write flush triggered"}

@app.post("/config/clear-cache")
def clear_caches():
    for node in cache_nodes.values():
        node.clear()
    metrics.log_routing("All cache nodes cleared manually.")
    return {"status": "success", "message": "All cache nodes cleared"}

@app.get("/trending")
def get_trending():
    # Returns top 10 overall search terms and top 10 trending search terms
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Top 10 overall
        cursor.execute("SELECT query, historical_count FROM queries ORDER BY historical_count DESC LIMIT 10")
        overall = [{"query": r[0], "count": r[1]} for r in cursor.fetchall()]
        
        # Top 10 trending (recency-aware scores computed across all queries that have recent searches)
        min_timestamp = time.time() - config.decay_window
        cursor.execute("""
            SELECT q.query, q.historical_count, r.timestamp
            FROM queries q
            JOIN recent_searches r ON q.query = r.query
            WHERE r.timestamp > ?
        """, (min_timestamp,))
        rows = cursor.fetchall()
        
        query_groups = {}
        for q, hist_count, ts in rows:
            if q not in query_groups:
                query_groups[q] = {"hist": hist_count, "timestamps": []}
            query_groups[q]["timestamps"].append(ts)
            
        now = time.time()
        scored = []
        for q, info in query_groups.items():
            score = float(info["hist"])
            for ts in info["timestamps"]:
                dt = now - ts
                decay_ratio = max(0.0, 1.0 - (dt / config.decay_window))
                score += decay_ratio * config.boost_factor
            scored.append((q, int(score)))
            
        scored.sort(key=lambda x: x[1], reverse=True)
        trending = [{"query": item[0], "score": item[1]} for item in scored[:10]]
        
        return {"overall": overall, "trending": trending}
    except Exception as e:
        metrics.log_routing(f"[DB Error] Trending fetch error: {e}")
        return {"overall": [], "trending": []}
    finally:
        conn.close()

# ----------------------------------------------------
# 7. Mock Traffic Simulation Support
# ----------------------------------------------------
SIMULATION_QUERIES = [
    "python", "python tutorial", "python documentation", "pytorch", "pytest", "pycharm",
    "consistent hashing", "distributed cache", "distributed systems", "machine learning",
    "react tutorial", "java code", "rust course", "how to build a search engine",
    "pizza recipe", "iphone 14", "wireless headphones", "hotels in london", "weather tomorrow"
]

@app.post("/simulate")
def simulate_traffic(count: int = Body(10, embed=True)):
    for _ in range(count):
        q = random.choice(SIMULATION_QUERIES)
        # Randomly choose if we search or suggest (mostly searches to fill batch writer)
        batch_writer.add_search(q)
    return {"status": "success", "message": f"Simulated {count} searches"}

@app.on_event("shutdown")
def shutdown_event():
    batch_writer.shutdown()
