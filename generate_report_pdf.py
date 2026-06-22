import os
import time
from fpdf import FPDF

class PDFReport(FPDF):
    def header(self):
        # Draw top banner line
        self.set_fill_color(16, 24, 48) # Dark blue/indigo
        self.rect(0, 0, 210, 15, 'F')
        
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 10)
        self.cell(0, -2, 'SYSTEMS DESIGN: SEARCH TYPEAHEAD PROJECT REPORT', 0, 1, 'C')
        self.ln(8)

    def footer(self):
        # Position at 1.5 cm from bottom
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')

    def add_title(self, title_text):
        self.set_font('Helvetica', 'B', 22)
        self.set_text_color(16, 24, 48)
        self.cell(0, 15, title_text, 0, 1, 'L')
        self.set_fill_color(0, 242, 254) # Cyan line
        self.rect(10, 36, 190, 1, 'F')
        self.ln(12)

    def add_section(self, title):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(16, 24, 48)
        self.ln(6)
        self.cell(0, 10, title, 0, 1, 'L')
        self.set_draw_color(16, 24, 48)
        self.line(self.get_x(), self.get_y(), self.get_x() + 190, self.get_y())
        self.ln(4)

    def add_subsection(self, title):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(48, 64, 96)
        self.ln(3)
        self.cell(0, 8, title, 0, 1, 'L')

    def add_paragraph(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def add_bullet(self, bold_text, normal_text):
        self.set_font('Helvetica', 'B', 9.5)
        self.set_text_color(60, 60, 60)
        self.write(5.5, " -  " + bold_text + ": ")
        self.set_font('Helvetica', '', 9.5)
        self.set_text_color(80, 80, 80)
        self.write(5.5, normal_text + "\n")
        self.ln(1)

    def add_code_block(self, code_text):
        self.set_font('Courier', '', 8.5)
        self.set_text_color(240, 240, 240)
        self.set_fill_color(30, 30, 35) # Dark gray background
        
        # Split text into lines to render inside a nice container
        lines = code_text.strip().split('\n')
        self.ln(2)
        for line in lines:
            # We add a small rect background for code block
            self.cell(0, 4.5, line, 0, 1, 'L', fill=True)
        self.ln(2)

def generate_pdf():
    pdf = PDFReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_margins(10, 20, 10)
    
    # Title Section
    pdf.add_title("Search Typeahead System Report")
    pdf.add_paragraph("A high-performance, distributed typeahead suggestion system designed to handle high write pressure, serve low-latency suggestions, and provide recency-boosted trending rankings.")
    
    # 1. Architecture Section
    pdf.add_section("1. Architecture Explanation")
    pdf.add_paragraph(
        "The system is built on a split architecture consisting of a persistent primary SQL database, "
        "a logical distributed caching layer managed by a consistent hashing ring, a thread-safe write buffering "
        "manager, and a real-time analytics web dashboard."
    )
    
    pdf.add_subsection("Distributed Cache & Consistent Hashing Ring")
    pdf.add_paragraph(
        "To simulate a production-grade distributed environment, we instantiate 3 logical caching nodes: "
        "Cache-Node-A, Cache-Node-B, and Cache-Node-C. To map incoming search prefixes evenly across these nodes, "
        "we implement a Consistent Hashing Ring with 50 virtual nodes per cache node (150 total). "
        "A search prefix key (e.g., 'py') is hashed using MD5 into a 128-bit space. The query is routed to the first "
        "virtual cache node located clockwise from the prefix hash value. Virtual nodes ensure that cache keys are "
        "balanced uniformly across physical servers, mitigating hot-spotting."
    )
    
    pdf.add_subsection("Trending & Recency-Aware Ranking")
    pdf.add_paragraph(
        "The typeahead system supports two ranking mechanisms: Basic and Enhanced (Trending). "
        "Basic mode sorts queries starting with a prefix purely by historical search count. Enhanced mode calculates "
        "a score combining historical volume and recent searches with a linear decay time window:"
    )
    pdf.add_paragraph(
        "Score(q) = HistoricalCount(q) + Sum_{s in RecentSearches(q)} Max(0, 1 - (t_now - t_s) / window_size) * boost_factor"
    )
    pdf.add_paragraph(
        "This decay scoring ensures queries searched recently climb to the top of suggestions immediately and drop back "
        "to their historical baseline score after the time window (e.g., 60 seconds) expires. It prevents temporary "
        "spikes from permanently dominating search rankings."
    )

    pdf.add_subsection("Batch Write Buffer")
    pdf.add_paragraph(
        "To protect the database from lock contention and high disk write overhead, we introduce a thread-safe "
        "BatchWriteManager. Search submissions are intercepted and placed into an in-memory queue. "
        "The queue is flushed to the database only when the queue size reaches a threshold (default: 10) "
        "or a period interval is met (default: 5 seconds). During a flush, duplicates are aggregated, "
        "and a single SQLite transaction upserts historical counts and inserts recent timestamp logs, "
        "minimizing disk write transactions."
    )
    
    # 2. Dataset Section
    pdf.add_section("2. Dataset Source & Loading Instructions")
    pdf.add_paragraph(
        "The system seeds an initial SQLite database ('search_database.db') with a rich dataset of 5,200 unique "
        "queries across programming languages, e-commerce products, recipes, movies, travel destinations, "
        "and general search queries. Frequencies are generated using a Zipf-like popularity distribution."
    )
    pdf.add_subsection("Loading Instructions:")
    pdf.add_bullet("Generate Database", "Run 'python generate_dataset.py' to rebuild the SQLite database and seed the tables.")
    pdf.add_bullet("Verify Database", "Upon execution, a database file ('search_database.db') is populated containing 'queries' (query, historical_count) and 'recent_searches' (query, timestamp) tables.")

    # 3. API Documentation
    pdf.add_section("3. API Documentation")
    pdf.add_bullet("GET /suggest?q=<prefix>", "Fetches the top 10 typeahead suggestions matching the prefix. Basic/Enhanced scoring modes apply.")
    pdf.add_bullet("POST /search", "Registers a search query, adding it to the in-memory write buffer. Returns 'Searched'.")
    pdf.add_bullet("GET /cache/debug?prefix=<prefix>", "Returns cache debug stats: responsible cache node, hit/miss status, TTL time remaining, and MD5 hash fraction.")
    pdf.add_bullet("GET /cache/ring", "Exposes the exact hash ring fractions and node mappings for all 150 virtual nodes.")
    pdf.add_bullet("GET /metrics", "Returns live stats: avg/p95 suggestion latency, cache hits/misses, DB reads, DB writes saved, and current buffer list.")
    pdf.add_bullet("GET /trending", "Fetches the top 10 popular queries and top 10 recency-boosted trending queries.")
    
    # Add new page for remaining sections
    pdf.add_page()
    
    # 4. Design Choices & Trade-offs
    pdf.add_section("4. Explanations of Design Choices & Trade-offs")
    
    pdf.add_subsection("Buffer-Based Batching vs Synchronous Writes")
    pdf.add_bullet("Buffered Batching", "Saves disk I/O operations by grouping queries into bulk transactions. Improves suggestion performance by freeing the database from write locks.")
    pdf.add_bullet("Trade-off (Data Loss)", "If the server crashes, unflushed searches in memory are lost. We accept this trade-off because search frequency tracking is non-critical (unlike financial transactions). WAL or a message queue (Kafka/Redis) can mitigate this in production.")
    
    pdf.add_subsection("Consistent Hashing vs Simple Hashing")
    pdf.add_bullet("Consistent Hashing", "When adding/removing cache nodes, only K/N keys are remapped. Standard hashing (hash(k) % N) invalidates the entire cache, overloading the DB.")
    pdf.add_bullet("Virtual Nodes", "Adding 50 virtual nodes per server balances the keys uniformly across all cache memory banks, preventing hot-spotting.")

    # 5. Performance Report
    pdf.add_section("5. Performance & Automated Verification Report")
    pdf.add_paragraph(
        "A verification script ('verify_system.py') was executed to validate the core system modules. "
        "The test suite outputs show that all performance expectations were successfully met:"
    )
    
    test_logs = (
        "============================================================\n"
        "STARTING SEARCH TYPEAHEAD SYSTEM VERIFICATION SUITE\n"
        "============================================================\n\n"
        "[TEST 1] Verifying Consistent Hashing Ring...\n"
        "  [+] Prefix 'py' consistently routes to 'Node-C'\n"
        "  [+] Prefix 'con' consistently routes to 'Node-A'\n"
        "  [+] Prefix 'dist' consistently routes to 'Node-C'\n"
        "  [+] Prefix 'web' consistently routes to 'Node-C'\n"
        "  [+] Prefix 'mach' consistently routes to 'Node-B'\n"
        "  [+] Ring distribution: {'Node-A': 50, 'Node-B': 50, 'Node-C': 50}\n"
        "  [+] Hashing ring verified successfully.\n\n"
        "[TEST 2] Verifying Cache Node behavior...\n"
        "  [+] Cache miss handled correctly.\n"
        "  [+] Cache hit value retrieved correctly.\n"
        "  [*] Waiting for TTL expiration (1.7s)...\n"
        "  [+] Cache TTL expiration verified successfully.\n\n"
        "[TEST 3] Verifying Batch Write Buffer...\n"
        "  [*] Submitting 3 searches (below threshold of 5)...\n"
        "  [+] Confirmed database has NOT been written to yet (write reduction in progress).\n"
        "  [*] Submitting 2 more searches (triggering threshold 5)...\n"
        "  [+] Confirmed buffer flushed. DB count is now: 5\n"
        "  [*] Submitting 2 more searches and waiting 2.5s for time flush...\n"
        "  [+] Confirmed periodic flush. DB count is now: 7\n"
        "  [+] Batch write logic verified successfully.\n\n"
        "[TEST 4] Verifying Trending Searches Recency-Aware Scoring...\n"
        "  [+] Basic mode sorting verified: trending_test_popular ranks first (Score: 100)\n"
        "  [*] Enhanced Suggestions: [('trending_test_recent', 509), ('trending_test_popular', 100)]\n"
        "  [+] Enhanced mode sorting verified: trending_test_recent boosted to first (Score: 509)\n"
        "  [*] Waiting for recency window decay (11s)...\n"
        "  [*] Suggestions after decay: [('trending_test_popular', 100), ('trending_test_recent', 10)]\n"
        "  [+] Decay verified successfully: trending_test_popular returned to rank 1.\n\n"
        "============================================================\n"
        "ALL TESTS PASSED SUCCESSFULLY! SYSTEM LOGIC CORRECT.\n"
        "============================================================"
    )
    
    pdf.add_code_block(test_logs)
    
    # Save the output file
    output_path = os.path.join(os.path.dirname(__file__), "Search_TypeAhead_Project_Report.pdf")
    pdf.output(output_path)
    print(f"Successfully generated PDF report at {output_path}")

if __name__ == "__main__":
    generate_pdf()
