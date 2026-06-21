import sqlite3
import random
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "search_database.db")

# Seed categories to generate rich search terms
prefixes = [
    "", "how to ", "best ", "learn ", "buy ", "cheap ", "online ", "what is ", 
    "latest ", "simple ", "easy ", "guide to ", "free "
]

topics = [
    "python", "javascript", "java", "rust", "go language", "c++", "react", "html css",
    "machine learning", "deep learning", "artificial intelligence", "large language models",
    "agentic coding", "data science", "sql database", "api design", "consistent hashing",
    "distributed systems", "caching mechanisms", "web development", "git repository",
    
    "iphone 14", "samsung galaxy s23", "macbook pro", "wireless headphones", "gaming mouse",
    "mechanical keyboard", "usb-c hub", "smart watch", "bluetooth speaker", "noise cancelling headphones",
    
    "pizza recipe", "sushi making", "pasta carbonara", "chicken tikka masala", "chocolate chip cookies",
    "pancakes from scratch", "iced coffee", "smoothie recipe", "healthy salad", "vegan dinner ideas",
    
    "premier league", "champions league", "nba playoffs", "formula 1 tickets", "wimbledon tennis",
    "olympics 2026", "cricket highlights", "fitness workout", "yoga for beginners",
    
    "marvel movies", "star wars timeline", "interstellar soundtrack", "inception movie review",
    "best netflix series", "anime recommendations", "indie game releases", "board games for family",
    
    "flights to paris", "hotels in london", "travel insurance", "backpacking tips", "road trip planning",
    "weather tomorrow", "stock market trends", "breaking news today", "cryptocurrency price"
]

suffixes = [
    "", " tutorial", " for beginners", " documentation", " example", " code", " course", 
    " online", " near me", " reviews", " comparison", " pdf", " explanation"
]

def generate_queries():
    generated = set()
    
    # 1. Add specific manual popular entries to ensure high-quality suggestions
    manual_entries = {
        "python": 15200,
        "python tutorial": 9800,
        "python documentation": 8500,
        "python programming": 7200,
        "python lists": 6400,
        "python dictionary": 6100,
        "pytorch": 5800,
        "pytest": 4500,
        "pycharm": 4100,
        "pygame": 3500,
        
        "consistent hashing": 8200,
        "consistent hashing ring": 4900,
        "consistent hashing implementation": 3200,
        
        "distributed cache": 5100,
        "distributed systems": 7600,
        
        "how to build a search engine": 4300,
        "best database for typeahead": 3500,
    }
    
    for query, count in manual_entries.items():
        generated.add((query, count))
        
    # 2. Programmatically generate 5000+ combinations
    attempts = 0
    target_count = 5200
    
    while len(generated) < target_count and attempts < 20000:
        attempts += 1
        p = random.choice(prefixes)
        t = random.choice(topics)
        s = random.choice(suffixes)
        
        query = f"{p}{t}{s}".strip().lower()
        
        # Avoid duplicate queries
        if any(q[0] == query for q in generated):
            continue
            
        # Determine a realistic zipf-like random popularity distribution
        # Highly popular keywords or generic prefixes, and long-tail queries
        if len(query) < 10:
            count = random.randint(1000, 10000)
        elif len(query) < 20:
            count = random.randint(100, 2000)
        else:
            count = random.randint(5, 300)
            
        generated.add((query, count))
        
    return list(generated)

def main():
    print("Generating search dataset queries...")
    queries_data = generate_queries()
    print(f"Generated {len(queries_data)} unique mock queries.")
    
    # Connect to SQLite
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("Removed existing database.")
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS queries (
        query TEXT PRIMARY KEY,
        historical_count INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recent_searches (
        query TEXT,
        timestamp REAL
    )
    """)
    
    # Create indices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_queries_query ON queries(query)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_searches_timestamp ON recent_searches(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_searches_query ON recent_searches(query)")
    
    # Insert data
    cursor.executemany(
        "INSERT INTO queries (query, historical_count) VALUES (?, ?)", 
        queries_data
    )
    
    conn.commit()
    conn.close()
    
    print(f"Successfully seeded SQLite database at {DB_PATH}")

if __name__ == "__main__":
    main()
