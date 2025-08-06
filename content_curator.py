import sqlite3
import arxiv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import ollama
import feedparser
import json
import time
from typing import List, Dict, Optional, Tuple, Any
import hashlib
import os
import threading
import re
from urllib.parse import urlparse, quote_plus
from contextlib import contextmanager

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

try:
    from pdf_parser import PDFProcessor, is_pdf_url
    HAS_PDF_SUPPORT = True
    print("✅ PDF processing support available")
except ImportError:
    HAS_PDF_SUPPORT = False
    print("⚠️ PDF processing not available - install pdf_parser.py and dependencies")

# Enhanced chat integration - graceful import
HAS_ENHANCED_CHAT = False
try:
    from data_access_helper import ArticleCrossReferencer, HistoricalDataAccessor
    HAS_ENHANCED_CHAT = True
except ImportError:
    HAS_ENHANCED_CHAT = False

class SourceConfig:
    def __init__(self, config_file="sources_config.json"):
        self.config_file = config_file
        self.config = self.load_config()

    def load_config(self) -> Dict:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            default_config = {
                "rss_feeds": [
                    {"url": "https://feeds.feedburner.com/oreilly/radar", "category": "tech"},
                    {"url": "https://techcrunch.com/feed/", "category": "tech"}
                ],
                "arxiv_queries": [
                    {"query": "machine learning", "max_results": 5}
                ],
                "collection_settings": {
                    "default_max_results_per_source": 5,
                    "rate_limit_delay_seconds": 2,
                    "rss_max_entries_per_feed": 20
                }
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2)
            print(f"Created default configuration file: {self.config_file}")
            return default_config
        except Exception as e:
            raise Exception(f"Error loading config file: {e}")

    def get_rss_feeds(self, category: Optional[str] = None) -> List[str]:
        feeds = self.config.get('rss_feeds', [])
        if category:
            feeds = [feed for feed in feeds if feed.get('category') == category]
        return [feed['url'] for feed in feeds]

    def get_arxiv_queries(self) -> List[Dict]:
        return self.config.get('arxiv_queries', [])

    def get_arxiv_categories(self) -> List[str]:
        return self.config.get('arxiv_categories', [])

    def get_setting(self, key: str, default=None):
        return self.config.get('collection_settings', {}).get(key, default)

    def reload_config(self):
        self.config = self.load_config()
        print("Configuration reloaded")


class EnhancedSearchProvider:
    def __init__(self):
        self.providers = {
            'duckduckgo': self._search_duckduckgo
        }
        self.default_provider = 'duckduckgo'

    def search(self, query: str, max_results: int = 10, provider: str = None) -> List[Dict]:
        if not provider:
            provider = self.default_provider

        search_func = self.providers.get(provider, self._search_duckduckgo)
        try:
            results = search_func(query, max_results)
            print(f"   🔍 Found {len(results)} results for '{query}' using {provider}")
            return results
        except Exception as e:
            print(f"   ❌ Search failed with {provider}: {e}")
            return []

    def _search_duckduckgo(self, query: str, max_results: int) -> List[Dict]:
        try:
            search_url = f"https://api.duckduckgo.com/"
            params = {
                'q': query,
                'format': 'json',
                'no_html': '1',
                'skip_disambig': '1'
            }

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(search_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            results = []

            related_topics = data.get('RelatedTopics', [])
            for i, topic in enumerate(related_topics[:max_results]):
                if isinstance(topic, dict) and 'FirstURL' in topic:
                    results.append({
                        'title': topic.get('Text', '').split(' - ')[0] if ' - ' in topic.get('Text', '') else topic.get('Text', ''),
                        'url': topic.get('FirstURL', ''),
                        'snippet': topic.get('Text', ''),
                        'source': 'Internet Search (DuckDuckGo)'
                    })

            if not results:
                results.append({
                    'title': f"Search results for: {query}",
                    'url': f"https://duckduckgo.com/?q={quote_plus(query)}",
                    'snippet': f"Web search results for '{query}' - click to see full results",
                    'source': 'Internet Search (DuckDuckGo)'
                })

            return results

        except Exception as e:
            print(f"DuckDuckGo search error: {e}")
            return [{
                'title': f"Search for: {query}",
                'url': f"https://duckduckgo.com/?q={quote_plus(query)}",
                'snippet': f"Unable to fetch search results automatically. Click to search for '{query}' manually.",
                'source': 'Internet Search (Manual)'
            }]


class WebProgressTracker:
    def __init__(self, output_callback=None):
        self.current_operation = None
        self.total_items = 0
        self.completed_items = 0
        self.current_phase = ""
        self.current_message = ""
        self.start_time = None
        self.source_counts = {}
        self.is_running = False
        self._lock = threading.RLock()
        self._lock_timeout = 5.0
        self.output_callback = output_callback
        self._callback_queue = []
        self._callback_lock = threading.Lock()

    def _safe_lock_operation(self, operation_func, *args, **kwargs):
        try:
            if self._lock.acquire(timeout=self._lock_timeout):
                try:
                    return operation_func(*args, **kwargs)
                finally:
                    self._lock.release()
            else:
                print("⚠️ Progress tracker lock timeout - operation skipped")
                return None
        except Exception as e:
            print(f"⚠️ Progress tracker error: {e}")
            return None

    def set_message(self, message):
        def _set_message_internal():
            self.current_message = message
            if self.output_callback:
                with self._callback_lock:
                    self._callback_queue.append(('info', message))
                self._process_callback_queue()
            else:
                print(message)

        self._safe_lock_operation(_set_message_internal)

    def _process_callback_queue(self):
        def process_callbacks():
            try:
                with self._callback_lock:
                    callbacks_to_process = self._callback_queue.copy()
                    self._callback_queue.clear()

                for message_type, message in callbacks_to_process:
                    try:
                        if self.output_callback:
                            self.output_callback(message, message_type)
                    except Exception as e:
                        print(f"⚠️ Output callback error: {e}")

            except Exception as e:
                print(f"⚠️ Callback processing error: {e}")

        callback_thread = threading.Thread(target=process_callbacks, daemon=True)
        callback_thread.start()

    def start_operation(self, operation_name):
        def _start_operation_internal():
            self.current_operation = operation_name
            self.total_items = self.completed_items = 0
            self.start_time = time.time()
            self.source_counts = {}
            self.is_running = True
            print(f"\n🚀 Starting {operation_name}...")

        self._safe_lock_operation(_start_operation_internal)

    def update_phase(self, phase, total=None):
        def _update_phase_internal():
            self.current_phase = phase
            if total:
                self.total_items = total
                self.completed_items = 0
            print(f"📋 Phase: {phase}")
            if total:
                print(f"   Items to process: {total}")

        self._safe_lock_operation(_update_phase_internal)

    def increment(self, source=None):
        def _increment_internal():
            self.completed_items += 1
            if source:
                self.source_counts[source] = self.source_counts.get(source, 0) + 1

            if self.completed_items % 5 == 0 or self.completed_items in [1, 2, 3]:
                elapsed = int(time.time() - self.start_time) if self.start_time else 0
                mins, secs = divmod(elapsed, 60)

                if self.total_items > 0:
                    progress = (self.completed_items / self.total_items) * 100
                    message = f"⚡ Progress: {self.completed_items}/{self.total_items} ({progress:.1f}%) - {mins}m {secs}s"
                else:
                    message = f"⚡ Progress: {self.completed_items} items processed - {mins}m {secs}s"

                self.current_message = message
                if self.output_callback:
                    with self._callback_lock:
                        self._callback_queue.append(('info', message))
                    self._process_callback_queue()
                else:
                    print(message)

        self._safe_lock_operation(_increment_internal)

    def get_status(self):
        def _get_status_internal():
            if not self.is_running:
                return {'status': self.current_operation or 'idle'}

            return {
                'status': 'running',
                'operation': self.current_operation,
                'phase': self.current_phase,
                'current_message': self.current_message,
                'completed': self.completed_items,
                'total': self.total_items,
                'elapsed_seconds': int(time.time() - self.start_time) if self.start_time else 0,
                'source_counts': self.source_counts.copy()
            }

        result = self._safe_lock_operation(_get_status_internal)
        return result or {'status': 'error', 'message': 'Failed to get status'}

    def finish(self, stats=None, final_status='completed'):
        def _finish_internal():
            if self.start_time:
                elapsed = int(time.time() - self.start_time)
                mins, secs = divmod(elapsed, 60)
                print(f"\n✅ {self.current_operation} completed in {mins}m {secs}s")
                if stats:
                    print(f"   New articles: {stats.get('new_articles', 0)}")
                    print(f"   AI processed: {stats.get('ai_processed', 0)}")

            self.is_running = False
            self.current_operation = final_status

        self._safe_lock_operation(_finish_internal)

    def _log(self, message, message_type="info"):
        try:
            if self.output_callback:
                def safe_callback():
                    try:
                        self.output_callback(message, message_type)
                    except Exception as e:
                        print(f"⚠️ Callback error: {e}")
                        print(message)

                callback_thread = threading.Thread(target=safe_callback, daemon=True)
                callback_thread.start()
            else:
                print(message)
        except Exception as e:
            print(f"⚠️ Logging error: {e}")
            print(message)


class ChatAssistant:
    def __init__(self, db_path: str, ollama_model: str):
        self.db_path = db_path
        self.ollama_model = ollama_model
        
        # Enhanced chat integration
        self.enhanced_chat_available = HAS_ENHANCED_CHAT
        self.cross_referencer = None
        self.historical_accessor = None
        
        if self.enhanced_chat_available:
            print("✅ ChatAssistant initialized with enhanced chat capabilities")

    def initialize_enhanced_features(self, content_curator, data_directory: str = "data"):
        """Initialize enhanced chat features if available"""
        if not self.enhanced_chat_available:
            return False
        
        try:
            self.cross_referencer = ArticleCrossReferencer(content_curator)
            self.historical_accessor = HistoricalDataAccessor(self, data_directory)
            print("✅ Enhanced chat features initialized in ChatAssistant")
            return True
        except Exception as e:
            print(f"⚠️ Failed to initialize enhanced chat features: {e}")
            self.enhanced_chat_available = False
            return False

    def get_database_context(self, query: str, session_context: Dict = None) -> str:
        """Enhanced database context with optional session awareness"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        context_parts = []

        # Get discussed articles from session context if available
        discussed_urls = []
        if session_context and 'analyzed_articles' in session_context:
            discussed_urls = list(session_context['analyzed_articles'].keys())

        # Enhanced search with session awareness
        base_query = f'%{query.lower()}%'

        # If query has too many wildcards, use just first few words
        if len(base_query) > 1000 or base_query.count('%') > 10:
            query_words = query.split()[:5]
            safe_query = re.escape(' '.join(query_words).lower())
            base_query = f'%{safe_query}%'
        print(f"🐛 DEBUG: LIKE pattern length={len(base_query)}, % count={base_query.count('%')}, pattern preview: {base_query[:100]}...")
        
        if discussed_urls:
            # Prioritize articles not yet discussed
            url_placeholders = ','.join(['?' for _ in discussed_urls])
            cursor.execute(f'''
                SELECT title, summary, source, relevance_score, published_date, url,
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       CASE WHEN url IN ({url_placeholders}) THEN 'discussed' ELSE 'new' END as discussion_status
                FROM content 
                WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(source) LIKE ?
                ORDER BY discussion_status DESC, total_relevance DESC, published_date DESC 
                LIMIT 10
            ''', discussed_urls + [base_query, base_query, base_query])
        else:
            cursor.execute('''
                SELECT title, summary, source, relevance_score, published_date, url,
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                FROM content 
                WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(source) LIKE ?
                ORDER BY total_relevance DESC, published_date DESC LIMIT 10
            ''', (base_query, base_query, base_query))

        processed = [dict(row) for row in cursor.fetchall()]
        processed_rows = cursor.fetchall()
        processed = [dict(row) for row in processed_rows]
        if processed:
            context_parts.append("RECENT PROCESSED ARTICLES:")
            for article in processed:
                status_indicator = ""
                if 'discussion_status' in article and article['discussion_status'] == 'discussed':
                    status_indicator = " [PREVIOUSLY DISCUSSED]"
                
                context_parts.extend([
                    f"- {article['title']}{status_indicator} (Source: {article['source']}, Relevance: {article.get('total_relevance', article['relevance_score']):.1f})",
                    f"  Summary: {article['summary'][:200]}...",
                    f"  URL: {article['url']}", ""
                ])

        # Add active topics from session if available
        if session_context and 'active_topics' in session_context:
            active_topics = list(session_context['active_topics'])[:5]
            if active_topics:
                context_parts.extend([
                    "CURRENT DISCUSSION TOPICS:",
                    f"- {', '.join(active_topics)}", ""
                ])

        # Get raw feeds with session awareness
        cursor.execute('''
            SELECT title, content, source, published_date, url
            FROM raw_feeds 
            WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(source) LIKE ?
            ORDER BY published_date DESC LIMIT 15
        ''', (base_query, base_query, base_query))

        raw_rows = cursor.fetchall()
        raw = [dict(row) for row in raw_rows]
        if raw:
            context_parts.append("RECENT RAW FEED ITEMS:")
            for article in raw:
                context_parts.extend([
                    f"- {article['title']} (Source: {article['source']})",
                    f"  Content: {article['content'][:150]}..." if article['content'] else "",
                    f"  URL: {article['url']}", ""
                ])

        conn.close()
        return "\n".join(context_parts)

    def get_enhanced_database_context(self, query: str, session_context: Dict = None) -> str:
        """Get enhanced database context with cross-references and historical data"""
        if not self.enhanced_chat_available:
            return self.get_database_context(query, session_context)
        
        try:
            # Get base context
            base_context = self.get_database_context(query, session_context)
            
            # Add cross-reference context if available
            cross_ref_context = ""
            if self.cross_referencer and session_context:
                current_article = session_context.get('current_article')
                if current_article and current_article.get('url'):
                    related_articles = self.cross_referencer.find_related_articles(
                        current_article['url'], limit=3
                    )
                    if related_articles:
                        cross_ref_context = "\n\nRELATED ARTICLES TO CURRENT DISCUSSION:\n"
                        for article in related_articles:
                            cross_ref_context += f"- {article['title']} (Relevance: {article.get('total_relevance', 0):.1f})\n"
            
            # Add historical context if relevant
            historical_context = ""
            if self.historical_accessor and any(word in query.lower() for word in ['last', 'previous', 'week', 'trend', 'history']):
                try:
                    historical_results = self.historical_accessor.search_historical_data(query, days=7)
                    if historical_results.get('total_matches', 0) > 0:
                        historical_context = f"\n\nHISTORICAL DATA MATCHES:\n- Found {historical_results['total_matches']} relevant historical entries\n"
                except Exception as e:
                    print(f"Historical context error: {e}")
            
            return base_context + cross_ref_context + historical_context
            
        except Exception as e:
            print(f"Enhanced context error: {e}")
            return self.get_database_context(query, session_context)

    def search_database_content_with_context(self, query: str, discussed_articles: List[str] = None, limit: int = 20) -> List[Dict]:
        """Search database with awareness of previously discussed articles"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            search_term = f'%{query.lower()}%'
            
            if discussed_articles:
                # Prioritize articles not yet discussed
                url_placeholders = ','.join(['?' for _ in discussed_articles])
                cursor.execute(f'''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           CASE WHEN url IN ({url_placeholders}) THEN 0 ELSE 1 END as discussion_priority,
                           'processed' as result_type
                    FROM content 
                    WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?
                    ORDER BY discussion_priority DESC, total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', discussed_articles + [search_term, search_term, search_term, limit])
            else:
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           'processed' as result_type
                    FROM content 
                    WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?
                    ORDER BY total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', (search_term, search_term, search_term, limit))

            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            return results

        except Exception as e:
            print(f"Enhanced database search error: {e}")
            # Fallback to standard search
            return self.search_database_content(query, limit)

    def search_database_content(self, query: str, limit: int = 20) -> List[Dict]:
        """Standard database content search"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            search_term = f'%{query.lower()}%'

            cursor.execute('''
                SELECT *, 
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       COALESCE(relevance_boost, 0) as boost_score,
                       'processed' as result_type
                FROM content 
                WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?
                ORDER BY total_relevance DESC, relevance_score DESC, published_date DESC 
                LIMIT ?
            ''', (search_term, search_term, search_term, limit))

            processed_results = [dict(row) for row in cursor.fetchall()]

            remaining_limit = max(0, limit - len(processed_results))
            if remaining_limit > 0:
                cursor.execute('''
                    SELECT *, 'raw' as result_type
                    FROM raw_feeds 
                    WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(search_topic) LIKE ?
                    AND url NOT IN (SELECT url FROM content WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?)
                    ORDER BY published_date DESC 
                    LIMIT ?
                ''', (search_term, search_term, search_term, search_term, search_term, search_term, remaining_limit))

                raw_results = [dict(row) for row in cursor.fetchall()]
            else:
                raw_results = []

            conn.close()

            all_results = processed_results + raw_results
            print(f"   📊 Database search for '{query}': {len(processed_results)} processed + {len(raw_results)} raw = {len(all_results)} total")

            return all_results

        except Exception as e:
            print(f"   ❌ Database search error: {e}")
            return []

    def find_articles_by_keywords(self, keywords: List[str], exclude_urls: List[str] = None, limit: int = 10) -> List[Dict]:
        """Support ArticleCrossReferencer with keyword-based article discovery"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Build search conditions for keywords
            search_conditions = []
            search_params = []
            
            for keyword in keywords:
                search_term = f'%{keyword.lower()}%'
                search_conditions.append('(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)')
                search_params.extend([search_term, search_term])

            # Add exclusion conditions
            exclusion_clause = ""
            if exclude_urls:
                exclusion_clause = f"AND url NOT IN ({','.join(['?' for _ in exclude_urls])})"
                search_params.extend(exclude_urls)

            search_params.append(limit)

            query = f'''
                SELECT *, 
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       COALESCE(relevance_boost, 0) as boost_score
                FROM content 
                WHERE ({' OR '.join(search_conditions)}) {exclusion_clause}
                ORDER BY total_relevance DESC, published_date DESC
                LIMIT ?
            '''

            cursor.execute(query, search_params)
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()

            return results

        except Exception as e:
            print(f"Keyword search error: {e}")
            return []

    def get_article_similarity_score(self, article1_url: str, article2_url: str) -> float:
        """Calculate similarity between two articles using existing relevance patterns"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('''
                SELECT title, summary, source, relevance_score FROM content 
                WHERE url IN (?, ?)
            ''', (article1_url, article2_url))

            articles = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if len(articles) != 2:
                return 0.0

            article1, article2 = articles[0], articles[1]

            # Calculate similarity score
            similarity_score = 0.0

            # Source similarity
            if article1['source'] == article2['source']:
                similarity_score += 2.0

            # Title similarity (simple word overlap)
            title1_words = set(article1['title'].lower().split())
            title2_words = set(article2['title'].lower().split())
            common_title_words = title1_words.intersection(title2_words)
            if common_title_words:
                similarity_score += len(common_title_words) * 0.5

            # Summary similarity
            if article1['summary'] and article2['summary']:
                summary1_words = set(article1['summary'].lower().split())
                summary2_words = set(article2['summary'].lower().split())
                common_summary_words = summary1_words.intersection(summary2_words)
                if common_summary_words:
                    similarity_score += len(common_summary_words) * 0.1

            # Relevance score similarity
            score_diff = abs(article1['relevance_score'] - article2['relevance_score'])
            if score_diff <= 1.0:
                similarity_score += 1.0 - score_diff

            return min(similarity_score, 10.0)

        except Exception as e:
            print(f"Similarity calculation error: {e}")
            return 0.0

    def get_follow_up_content_recommendations(self, discussed_articles: List[str], limit: int = 5) -> List[Dict]:
        """Recommend content based on discussion history"""
        if not discussed_articles:
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get topics from discussed articles
            url_placeholders = ','.join(['?' for _ in discussed_articles])
            cursor.execute(f'''
                SELECT title, summary, source FROM content 
                WHERE url IN ({url_placeholders})
            ''', discussed_articles)

            discussed_content = cursor.fetchall()
            
            # Extract keywords from discussed articles
            all_keywords = set()
            sources = set()
            
            for article in discussed_content:
                sources.add(article['source'])
                # Simple keyword extraction
                text = f"{article['title']} {article['summary'] or ''}".lower()
                words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
                for word in words:
                    if len(word) > 3:
                        all_keywords.add(word)

            # Find recommendations based on keywords and sources
            keyword_list = list(all_keywords)[:10]  # Top 10 keywords
            search_conditions = []
            search_params = []

            for keyword in keyword_list:
                search_term = f'%{keyword}%'
                search_conditions.append('(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)')
                search_params.extend([search_term, search_term])

            # Exclude already discussed articles
            exclusion_clause = f"AND url NOT IN ({url_placeholders})"
            search_params.extend(discussed_articles)
            search_params.append(limit)

            if search_conditions:
                query = f'''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE ({' OR '.join(search_conditions)}) {exclusion_clause}
                    ORDER BY total_relevance DESC, published_date DESC
                    LIMIT ?
                '''

                cursor.execute(query, search_params)
                recommendations = [dict(row) for row in cursor.fetchall()]
            else:
                recommendations = []

            conn.close()
            return recommendations

        except Exception as e:
            print(f"Recommendation error: {e}")
            return []

    def analyze_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return "Invalid URL format. Please provide a complete URL with http:// or https://"

            headers = {'User-Agent': 'Mozilla/5.0 (compatible; ContentCurator/1.0)'}
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()

            title = soup.title.string.strip() if soup.title else ""
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|article'))
            content = main_content.get_text() if main_content else soup.get_text()

            lines = (line.strip() for line in content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            clean_content = ' '.join(chunk for chunk in chunks if chunk)

            if len(clean_content) > 8000:
                clean_content = clean_content[:8000] + "... [content truncated]"

            return f"ANALYZED URL: {url}\nTITLE: {title}\nCONTENT:\n{clean_content}"

        except Exception as e:
            return f"Error analyzing URL {url}: {str(e)}"

    def generate_response(self, user_message: str, session_context: Dict = None) -> str:
        """Enhanced response generation with optional session context"""
        try:
            article_context = ""
            enhanced_context_used = False
            
            # Extract article discussion context if present
            if "[ARTICLE DISCUSSION CONTEXT]" in user_message:
                parts = user_message.split("USER MESSAGE: ", 1)
                if len(parts) == 2:
                    article_context = parts[0].replace("[ARTICLE DISCUSSION CONTEXT]", "").strip()
                    user_message = parts[1]

            # Handle enhanced context from research sessions
            if session_context and any(key in user_message for key in ["[RECENT ARTICLES DISCUSSED:", "[ACTIVE TOPICS IN SESSION]"]):
                enhanced_context_used = True

            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+\.[a-z]{2,}'
            urls = re.findall(url_pattern, user_message, re.IGNORECASE)
            context_parts = []

            for url in urls[:2]:
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                url_analysis = self.analyze_url(url)
                context_parts.extend([url_analysis, ""])

            # Use enhanced database context if session context available
            if session_context and self.enhanced_chat_available:
                db_context = self.get_enhanced_database_context(user_message, session_context)
                enhanced_context_used = True
            else:
                db_context = self.get_database_context(user_message, session_context)
                
            if db_context.strip():
                context_parts.extend(["DATABASE CONTEXT FROM YOUR CURATED ARTICLES:", db_context])

            full_context = ""
            if article_context:
                full_context += f"CURRENT ARTICLE BEING DISCUSSED:\n{article_context}\n\n"

            if context_parts:
                full_context += "\n".join(context_parts)
            else:
                full_context += "No specific context found in database or provided URLs."

            # Enhanced prompt based on session awareness
            if article_context:
                prompt = f"""You are an AI assistant helping a user discuss a specific article they're interested in. You have access to the article content, summary, and their personal content database.

{full_context}

USER MESSAGE: {user_message}

Please provide a thoughtful, engaging response about the article. You can:
- Answer questions about the article content
- Discuss implications and insights  
- Compare with other articles in their database
- Suggest related topics or questions
- Provide analysis and commentary

Be conversational, informative, and help facilitate a meaningful discussion about this article.

RESPONSE:"""
            elif enhanced_context_used:
                prompt = f"""You are an AI assistant helping a user with their personal content curation and research system. You have access to their collected articles, discussion history, active topics, and enhanced context.

ENHANCED CONTEXT FROM USER'S RESEARCH SESSION:
{full_context}

USER MESSAGE: {user_message}

Please provide a helpful, informative response based on the available context. Consider:
- Previously discussed articles and topics
- Cross-references between related content
- Historical context where relevant
- User's ongoing research interests

Be conversational, insightful, and help advance their research and understanding.

RESPONSE:"""
            else:
                prompt = f"""You are an AI assistant helping a user with their personal content curation system. You have access to their collected articles, summaries, and feed data, plus you can analyze URLs they provide.

CONTEXT FROM USER'S CONTENT DATABASE AND ANALYZED URLS:
{full_context}

USER MESSAGE: {user_message}

Please provide a helpful, informative response based on the available context. If you analyzed any URLs, summarize the key points. If the user asks about their content, use the database context to provide specific, relevant information. Be conversational and helpful.

RESPONSE:"""

            response = ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': prompt}
            ])

            return response['message']['content'].strip()

        except Exception as e:
            return f"I encountered an error processing your request: {str(e)}. Please try again or rephrase your question."

    def generate_streaming_response(self, user_message: str, session_context: Dict = None):
        """Generate streaming response for chat interactions with enhanced context"""
        try:
            # Check if this is actually a pre-generated enhanced chat response
            # (We can detect this by checking if it looks like a formatted response)
            if (user_message.startswith('📖') or user_message.startswith('📚') or
                    user_message.startswith('🔗') or user_message.startswith('📊') or
                    user_message.startswith('✅') or user_message.startswith('❌')):
                # This is a pre-generated enhanced chat response - stream it word by word
                words = user_message.split()
                for i, word in enumerate(words):
                    if i == 0:
                        yield word
                    else:
                        yield ' ' + word
                    time.sleep(0.03)  # Small delay for streaming effect
                return

            # Same message preparation logic as generate_response
            article_context = ""
            enhanced_context_used = False

            if "[ARTICLE DISCUSSION CONTEXT]" in user_message:
                parts = user_message.split("USER MESSAGE: ", 1)
                if len(parts) == 2:
                    article_context = parts[0].replace("[ARTICLE DISCUSSION CONTEXT]", "").strip()
                    user_message = parts[1]

            if session_context and any(
                    key in user_message for key in ["[RECENT ARTICLES DISCUSSED:", "[ACTIVE TOPICS IN SESSION]"]):
                enhanced_context_used = True

            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+\.[a-z]{2,}'
            urls = re.findall(url_pattern, user_message, re.IGNORECASE)
            context_parts = []

            for url in urls[:2]:
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                url_analysis = self.analyze_url(url)
                context_parts.extend([url_analysis, ""])

            # Use enhanced context if available
            if session_context and self.enhanced_chat_available:
                db_context = self.get_enhanced_database_context(user_message, session_context)
                enhanced_context_used = True
            else:
                db_context = self.get_database_context(user_message, session_context)

            if db_context.strip():
                context_parts.extend(["DATABASE CONTEXT FROM YOUR CURATED ARTICLES:", db_context])

            full_context = ""
            if article_context:
                full_context += f"CURRENT ARTICLE BEING DISCUSSED:\n{article_context}\n\n"

            if context_parts:
                full_context += "\n".join(context_parts)
            else:
                full_context += "No specific context found in database or provided URLs."

            # Choose appropriate prompt based on context
            if article_context:
                prompt = f"""You are an AI assistant helping a user discuss a specific article they're interested in. You have access to the article content, summary, and their personal content database.

    {full_context}

    USER MESSAGE: {user_message}

    Please provide a thoughtful, engaging response about the article. You can:
    - Answer questions about the article content
    - Discuss implications and insights  
    - Compare with other articles in their database
    - Suggest related topics or questions
    - Provide analysis and commentary

    Be conversational, informative, and help facilitate a meaningful discussion about this article.

    RESPONSE:"""
            elif enhanced_context_used:
                prompt = f"""You are an AI assistant helping a user with their personal content curation and research system. You have access to their collected articles, discussion history, active topics, and enhanced context.

    ENHANCED CONTEXT FROM USER'S RESEARCH SESSION:
    {full_context}

    USER MESSAGE: {user_message}

    Please provide a helpful, informative response based on the available context. Consider:
    - Previously discussed articles and topics
    - Cross-references between related content
    - Historical context where relevant
    - User's ongoing research interests

    Be conversational, insightful, and help advance their research and understanding.

    RESPONSE:"""
            else:
                prompt = f"""You are an AI assistant helping a user with their personal content curation system. You have access to their collected articles, summaries, and feed data, plus you can analyze URLs they provide.

    CONTEXT FROM USER'S CONTENT DATABASE AND ANALYZED URLS:
    {full_context}

    USER MESSAGE: {user_message}

    Please provide a helpful, informative response based on the available context. If you analyzed any URLs, summarize the key points. If the user asks about their content, use the database context to provide specific, relevant information. Be conversational and helpful.

    RESPONSE:"""

            # Stream the response
            response = ollama.chat(
                model=self.ollama_model,
                messages=[{'role': 'user', 'content': prompt}],
                stream=True
            )

            for chunk in response:
                if chunk['message']['content']:
                    yield chunk['message']['content']

        except Exception as e:
            yield f"I encountered an error processing your request: {str(e)}. Please try again or rephrase your question."

    def get_article_for_chat(self, article_url: str) -> Dict:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM content WHERE url = ?', (article_url,))
            processed_article = cursor.fetchone()

            cursor.execute('SELECT * FROM raw_feeds WHERE url = ?', (article_url,))
            raw_article = cursor.fetchone()

            if not raw_article and not processed_article:
                conn.close()
                return {'error': 'Article not found in database'}

            article_source = processed_article or raw_article

            result = {
                'title': article_source['title'],
                'source': article_source['source'],
                'url': article_source['url'],
                'published_date': article_source['published_date'],
                'content': article_source.get('content') or article_source.get('full_content', ''),
                'has_ai_summary': processed_article is not None
            }

            if processed_article and processed_article['summary']:
                result.update({
                    'ai_summary': processed_article['summary'],
                    'relevance_score': processed_article['relevance_score'],
                    'chat_intro': f"📖 **Let's discuss: {article_source['title']}**\n\n**Source:** {article_source['source']}\n\n**AI Summary:** {processed_article['summary']}\n\n**Relevance Score:** {processed_article['relevance_score']:.1f}/10\n\nWhat would you like to know or discuss about this article?"
                })
            else:
                result.update({
                    'chat_intro': f"📖 **Analyzing: {article_source['title']}**\n\n**Source:** {article_source['source']}\n\nI'm preparing a summary of this article for our discussion...",
                    'needs_summary': True
                })

            conn.close()
            return result

        except Exception as e:
            return {'error': f'Error retrieving article: {str(e)}'}

    def chat_about_internet_result(self, result_data: Dict) -> str:
        try:
            title = result_data.get('title', 'Unknown article')
            url = result_data.get('url', '')
            source = result_data.get('source', 'Unknown source')
            summary = result_data.get('ai_summary', '')
            content = result_data.get('full_content', '')
            snippet = result_data.get('snippet', '')

            content_for_analysis = content or summary or snippet or "No content available"

            prompt = f"""You are helping a user discuss an article they found through internet search. Provide an engaging introduction and analysis.

ARTICLE DETAILS:
Title: {title}
Source: {source}
URL: {url}
Content/Summary: {content_for_analysis[:2000]}

Please provide:
1. A friendly introduction acknowledging this is a newly found article
2. Key insights or takeaways from the content
3. 2-3 thought-provoking questions or discussion points about the article
4. Any connections to broader trends or topics

Keep your response conversational and engaging, as if starting a discussion about this article.

RESPONSE:"""

            response = ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': prompt}
            ])

            return response['message']['content'].strip()

        except Exception as e:
            return f"I found this interesting article: **{result_data.get('title', 'Unknown title')}** from {result_data.get('source', 'unknown source')}.\n\nHowever, I encountered an error analyzing it: {str(e)}\n\nWhat would you like to know about this article?"


class ContentCurator:
    def __init__(self, db_path="data/content_curator.db", config_file="data/sources_config.json"):
        self.db_path = db_path
        self.source_config = SourceConfig(config_file)
        self.progress = WebProgressTracker(output_callback=None)
        self.init_database()
        model_settings = self.source_config.config.get('model_settings', {})
        self.ollama_model = self.source_config.config.get('model_settings', {}).get('ollama_model', 'granite3.2:8b')
        self.model_temperature = model_settings.get('temperature', 0.7)
        self.model_max_tokens = model_settings.get('max_tokens', 2000)
        self.operation_stats = {}
        self.search_provider = EnhancedSearchProvider()
        self.arxiv_client = arxiv.Client(
            page_size=5,
            delay_seconds=0.5,
            num_retries=2
        )
        self._db_lock = threading.RLock()
        
        # Enhanced chat integration
        self.enhanced_chat_available = HAS_ENHANCED_CHAT
        self.article_cross_referencer = None
        self.historical_data_accessor = None
        
        if self.enhanced_chat_available:
            print("✅ ContentCurator initialized with enhanced chat support")

    def initialize_enhanced_features(self, chat_assistant, data_directory: str = "data"):
        """Initialize enhanced chat features if available"""
        if not self.enhanced_chat_available:
            return False
        
        try:
            self.article_cross_referencer = ArticleCrossReferencer(self)
            self.historical_data_accessor = HistoricalDataAccessor(chat_assistant, data_directory)
            
            # Initialize enhanced features in chat assistant
            if hasattr(chat_assistant, 'initialize_enhanced_features'):
                chat_assistant.initialize_enhanced_features(self, data_directory)
            
            print("✅ Enhanced chat features initialized in ContentCurator")
            return True
        except Exception as e:
            print(f"⚠️ Failed to initialize enhanced chat features: {e}")
            self.enhanced_chat_available = False
            return False

    @contextmanager
    def get_db_connection_with_timeout(self, timeout_seconds=30):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=10000')
            conn.execute('PRAGMA synchronous=NORMAL')
            yield conn

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"   ⏳ Database locked, retrying...")
                time.sleep(0.5)
                try:
                    if conn:
                        conn.close()
                    conn = sqlite3.connect(self.db_path, timeout=10.0)
                    conn.execute('PRAGMA journal_mode=WAL')
                    conn.execute('PRAGMA busy_timeout=5000')
                    yield conn
                except Exception as retry_e:
                    print(f"   ❌ Database retry failed: {retry_e}")
                    if conn:
                        conn.rollback()
                    raise
            else:
                if conn:
                    conn.rollback()
                raise
        except Exception as e:
            print(f"   ❌ Database connection error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def get_local_time(self, as_string=False, date_only=False):
        local_now = datetime.now()

        if date_only:
            return local_now.strftime('%Y-%m-%d')
        elif as_string:
            return local_now.isoformat()
        else:
            return local_now

    @contextmanager
    def get_db_connection(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def parse_publication_date(self, date_str: str) -> datetime:
        if not date_str:
            return self.get_local_time()

        try:
            if 'T' in date_str:
                clean_date = date_str.split('T')[0] + 'T' + date_str.split('T')[1].split('+')[0].split('Z')[0]
                return datetime.fromisoformat(clean_date)
            else:
                return self.get_local_time()
        except:
            return self.get_local_time()

    def sort_content_by_date(self, content_list: list) -> list:
        def get_sort_key(content_item):
            return self.parse_publication_date(content_item.get('published_date', ''))
        return sorted(content_list, key=get_sort_key, reverse=True)

    def filter_by_time_range(self, content_list: List[Dict], time_range_hours: Optional[int] = None) -> List[Dict]:
        if not time_range_hours or not content_list:
            return content_list

        cutoff_date = self.get_local_time() - timedelta(hours=time_range_hours)
        filtered_content = []

        for content in content_list:
            try:
                pub_date = self.parse_publication_date(content.get('published_date', ''))
                if pub_date >= cutoff_date:
                    filtered_content.append(content)
            except Exception:
                filtered_content.append(content)

        filtered_count = len(content_list) - len(filtered_content)
        if filtered_count > 0:
            print(f"   🕒 Filtered out {filtered_count} articles older than {time_range_hours} hours")

        return filtered_content

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        tables = [
            '''CREATE TABLE IF NOT EXISTS content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL, title TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
                content TEXT, summary TEXT, relevance_score REAL DEFAULT 0,
                published_date TIMESTAMP, added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_status BOOLEAN DEFAULT 0, user_rating INTEGER, search_topic TEXT,
                relevance_boost REAL DEFAULT 0, hot_topics_date TEXT, hot_topics_keywords_used TEXT,
                discussion_count INTEGER DEFAULT 0, last_discussed TIMESTAMP, conversation_context TEXT
            )''',
            '''CREATE TABLE IF NOT EXISTS raw_feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, content TEXT, url TEXT UNIQUE NOT NULL,
                published_date TIMESTAMP, source TEXT NOT NULL, summary TEXT DEFAULT '',
                author TEXT DEFAULT '', category TEXT DEFAULT '', content_hash TEXT DEFAULT '',
                full_content TEXT DEFAULT '', added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                search_topic TEXT DEFAULT 'general'
            )''',
            '''CREATE TABLE IF NOT EXISTS user_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interest TEXT UNIQUE NOT NULL, weight REAL DEFAULT 1.0
            )''',
            '''CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                operation_type TEXT NOT NULL, duration_seconds INTEGER,
                total_collected INTEGER DEFAULT 0, new_articles INTEGER DEFAULT 0,
                ai_processed INTEGER DEFAULT 0, cached_skipped INTEGER DEFAULT 0,
                arxiv_count INTEGER DEFAULT 0, hackernews_count INTEGER DEFAULT 0,
                reddit_count INTEGER DEFAULT 0, rss_count INTEGER DEFAULT 0, search_topic TEXT
            )'''
        ]

        for table_sql in tables:
            cursor.execute(table_sql)

        # Enhanced chat database schema updates
        enhanced_chat_columns = [
            ('content', 'relevance_boost', 'REAL DEFAULT 0'),
            ('content', 'hot_topics_date', 'TEXT'),
            ('content', 'hot_topics_keywords_used', 'TEXT'),
            ('content', 'discussion_count', 'INTEGER DEFAULT 0'),
            ('content', 'last_discussed', 'TIMESTAMP'),
            ('content', 'conversation_context', 'TEXT')
        ]

        for table, column, definition in enhanced_chat_columns:
            try:
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Create indexes for enhanced chat performance
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_content_keywords ON content(title, summary)',
            'CREATE INDEX IF NOT EXISTS idx_content_relevance_boost ON content(relevance_score, relevance_boost)',
            'CREATE INDEX IF NOT EXISTS idx_content_source_date ON content(source, published_date)',
            'CREATE INDEX IF NOT EXISTS idx_content_discussion ON content(discussion_count, last_discussed)',
            'CREATE INDEX IF NOT EXISTS idx_raw_feeds_search ON raw_feeds(search_topic, published_date)'
        ]

        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
            except sqlite3.OperationalError:
                pass  # Index already exists

        for interest in ['machine learning', 'AI']:
            cursor.execute('INSERT OR IGNORE INTO user_interests (interest) VALUES (?)', (interest,))

        conn.commit()
        conn.close()

    def prune_old_content(self, days_to_keep: int = 14):
        """Remove articles that have been in database longer than specified days."""
        print(f"Pruning content older than {days_to_keep} days...")

        try:
            cutoff_date = (self.get_local_time() - timedelta(days=days_to_keep)).isoformat()

            with self.get_db_connection() as conn:
                cursor = conn.cursor()

                # Count what will be deleted for logging
                cursor.execute('SELECT COUNT(*) FROM content WHERE added_date < ? AND (starred IS NULL OR starred != 1)', (cutoff_date,))
                content_count = cursor.fetchone()[0]

                cursor.execute('SELECT COUNT(*) FROM raw_feeds WHERE added_date < ?', (cutoff_date,))
                raw_count = cursor.fetchone()[0]

                # Delete old content based on when it was added to database
                cursor.execute('DELETE FROM content WHERE added_date < ? AND (starred IS NULL OR starred != 1)', (cutoff_date,))
                cursor.execute('DELETE FROM raw_feeds WHERE added_date < ?', (cutoff_date,))

                conn.commit()

                total_deleted = content_count + raw_count
                if total_deleted > 0:
                    print(
                        f"   ✅ Removed {total_deleted} articles added >14 days ago ({content_count} processed, {raw_count} raw)")
                else:
                    print(f"   ✅ No stale articles to remove")

        except Exception as e:
            print(f"   ⚠️ Pruning failed: {e}")

    def check_hot_topics_boost_status(self, date: str) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('''
                SELECT COUNT(*) as total_count,
                       COUNT(CASE WHEN relevance_boost > 0 THEN 1 END) as boosted_count,
                       AVG(CASE WHEN relevance_boost > 0 THEN relevance_boost END) as avg_boost,
                       MAX(relevance_boost) as max_boost,
                       hot_topics_keywords_used
                FROM content 
                WHERE hot_topics_date = ?
            ''', (date,))

            result = cursor.fetchone()
            total_count = result['total_count'] or 0
            boosted_count = result['boosted_count'] or 0
            avg_boost = result['avg_boost'] or 0.0
            max_boost = result['max_boost'] or 0.0
            keywords_used_raw = result['hot_topics_keywords_used']

            keywords_used = []
            if keywords_used_raw:
                keywords_used = [k.strip() for k in keywords_used_raw.split(',')]

            quality_threshold_met = (boosted_count >= 3 and
                                   (total_count == 0 or boosted_count / total_count >= 0.2))

            conn.close()

            return {
                'has_boost_scoring': total_count > 0,
                'boosted_articles_count': boosted_count,
                'total_articles_count': total_count,
                'boost_date': date,
                'keywords_used': keywords_used,
                'average_boost': round(avg_boost, 1),
                'max_boost': max_boost,
                'quality_threshold_met': quality_threshold_met
            }

        except Exception as e:
            print(f"Error checking hot topics boost status: {e}")
            return {
                'has_boost_scoring': False,
                'boosted_articles_count': 0,
                'total_articles_count': 0,
                'boost_date': date,
                'keywords_used': [],
                'average_boost': 0.0,
                'max_boost': 0.0,
                'quality_threshold_met': False
            }

    def get_recent_content(self, hours: int = 24, limit: int = 20, min_relevance: float = 0.0, 
                          search_topic: Optional[str] = None, session_context: Dict = None) -> List[Dict]:
        """Enhanced recent content retrieval with session awareness"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            since_date = (self.get_local_time() - timedelta(hours=hours)).isoformat()

            # Get discussed articles from session context
            discussed_urls = []
            if session_context and 'analyzed_articles' in session_context:
                discussed_urls = list(session_context['analyzed_articles'].keys())

            if search_topic:
                if discussed_urls:
                    url_placeholders = ','.join(['?' for _ in discussed_urls])
                    cursor.execute(f'''
                        SELECT *, 
                               (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                               COALESCE(relevance_boost, 0) as boost_score,
                               CASE WHEN url IN ({url_placeholders}) THEN 'discussed' ELSE 'new' END as discussion_status
                        FROM content 
                        WHERE added_date > ? AND relevance_score >= ? 
                        AND (LOWER(search_topic) = LOWER(?) OR LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)
                        ORDER BY discussion_status DESC, total_relevance DESC, published_date DESC 
                        LIMIT ?
                    ''', discussed_urls + [since_date, min_relevance, search_topic, f'%{search_topic.lower()}%', f'%{search_topic.lower()}%', limit])
                else:
                    cursor.execute('''
                        SELECT *, 
                               (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                               COALESCE(relevance_boost, 0) as boost_score
                        FROM content 
                        WHERE added_date > ? AND relevance_score >= ? 
                        AND (LOWER(search_topic) = LOWER(?) OR LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)
                        ORDER BY total_relevance DESC, published_date DESC 
                        LIMIT ?
                    ''', (since_date, min_relevance, search_topic, f'%{search_topic.lower()}%', f'%{search_topic.lower()}%', limit))
            else:
                if discussed_urls:
                    url_placeholders = ','.join(['?' for _ in discussed_urls])
                    cursor.execute(f'''
                        SELECT *, 
                               (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                               COALESCE(relevance_boost, 0) as boost_score,
                               CASE WHEN url IN ({url_placeholders}) THEN 'discussed' ELSE 'new' END as discussion_status
                        FROM content 
                        WHERE added_date > ? AND relevance_score >= ?
                        ORDER BY discussion_status DESC, total_relevance DESC, published_date DESC 
                        LIMIT ?
                    ''', discussed_urls + [since_date, min_relevance, limit])
                else:
                    cursor.execute('''
                        SELECT *, 
                               (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                               COALESCE(relevance_boost, 0) as boost_score
                        FROM content 
                        WHERE added_date > ? AND relevance_score >= ?
                        ORDER BY total_relevance DESC, published_date DESC 
                        LIMIT ?
                    ''', (since_date, min_relevance, limit))

            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results

        except Exception as e:
            print(f"Error getting recent content: {e}")
            return []

    def query_articles_by_date_range(self, start_date: datetime, end_date: datetime, topic: str = None) -> List[Dict]:
        """Support historical data queries with database content"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            start_iso = start_date.isoformat()
            end_iso = end_date.isoformat()

            if topic:
                search_term = f'%{topic.lower()}%'
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE published_date BETWEEN ? AND ?
                    AND (LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?)
                    ORDER BY total_relevance DESC, published_date DESC
                ''', (start_iso, end_iso, search_term, search_term, search_term))
            else:
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE published_date BETWEEN ? AND ?
                    ORDER BY total_relevance DESC, published_date DESC
                ''', (start_iso, end_iso))

            results = [dict(row) for row in cursor.fetchall()]
            conn.close()

            return results

        except Exception as e:
            print(f"Date range query error: {e}")
            return []

    def get_trending_topics_from_db(self, days: int = 7) -> Dict:
        """Extract trending topics from database content over time"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            since_date = (self.get_local_time() - timedelta(days=days)).isoformat()

            # Get recent high-relevance articles
            cursor.execute('''
                SELECT title, summary, source, relevance_score, 
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       published_date, hot_topics_keywords_used
                FROM content 
                WHERE published_date > ? AND total_relevance > 5.0
                ORDER BY total_relevance DESC, published_date DESC
                LIMIT 50
            ''', (since_date,))

            articles = cursor.fetchall()
            conn.close()

            if not articles:
                return {'trending_topics': [], 'keyword_frequency': {}, 'sources': [], 'time_range_days': days}

            # Extract keywords and analyze trends
            keyword_frequency = {}
            source_counts = {}
            hot_topics_used = []

            for article in articles:
                source_counts[article['source']] = source_counts.get(article['source'], 0) + 1
                
                # Extract hot topics keywords if available
                if article['hot_topics_keywords_used']:
                    keywords = [k.strip() for k in article['hot_topics_keywords_used'].split(',')]
                    hot_topics_used.extend(keywords)
                
                # Extract keywords from title and summary
                text = f"{article['title']} {article['summary'] or ''}".lower()
                words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
                
                for word in words:
                    if len(word) > 3:
                        keyword_frequency[word] = keyword_frequency.get(word, 0) + 1

            # Get most frequent keywords
            top_keywords = sorted(keyword_frequency.items(), key=lambda x: x[1], reverse=True)[:15]
            top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            
            # Analyze hot topics usage
            hot_topics_frequency = {}
            for keyword in hot_topics_used:
                hot_topics_frequency[keyword] = hot_topics_frequency.get(keyword, 0) + 1

            return {
                'trending_topics': [{'keyword': k, 'frequency': f} for k, f in top_keywords],
                'keyword_frequency': keyword_frequency,
                'sources': [{'source': s, 'count': c} for s, c in top_sources],
                'hot_topics_usage': hot_topics_frequency,
                'time_range_days': days,
                'total_articles_analyzed': len(articles)
            }

        except Exception as e:
            print(f"Trending topics analysis error: {e}")
            return {'trending_topics': [], 'keyword_frequency': {}, 'sources': [], 'time_range_days': days}

    def search_content_by_timeframe(self, query: str, hours: int = 24, limit: int = 20) -> List[Dict]:
        """Search content within specific timeframe for historical searches"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            since_date = (self.get_local_time() - timedelta(hours=hours)).isoformat()
            search_term = f'%{query.lower()}%'

            cursor.execute('''
                SELECT *, 
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       COALESCE(relevance_boost, 0) as boost_score
                FROM content 
                WHERE published_date > ?
                AND (LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?)
                ORDER BY total_relevance DESC, published_date DESC
                LIMIT ?
            ''', (since_date, search_term, search_term, search_term, limit))

            results = [dict(row) for row in cursor.fetchall()]
            conn.close()

            return results

        except Exception as e:
            print(f"Timeframe search error: {e}")
            return []

    def update_article_discussion_metadata(self, article_url: str, session_context: Dict = None):
        """Update article discussion count and metadata"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE content 
                SET discussion_count = COALESCE(discussion_count, 0) + 1,
                    last_discussed = ?,
                    conversation_context = ?
                WHERE url = ?
            ''', (
                self.get_local_time(as_string=True),
                json.dumps(session_context) if session_context else None,
                article_url
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"Discussion metadata update error: {e}")

    def get_articles_by_discussion_activity(self, limit: int = 20) -> List[Dict]:
        """Get articles sorted by discussion activity"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('''
                SELECT *, 
                       (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                       COALESCE(relevance_boost, 0) as boost_score,
                       COALESCE(discussion_count, 0) as discussion_count
                FROM content 
                WHERE discussion_count > 0
                ORDER BY discussion_count DESC, last_discussed DESC, total_relevance DESC
                LIMIT ?
            ''', (limit,))

            results = [dict(row) for row in cursor.fetchall()]
            conn.close()

            return results

        except Exception as e:
            print(f"Discussion activity query error: {e}")
            return []

    def collect_arxiv_papers(self, query: str = None, max_results: int = 20) -> List[Dict]:
        try:
            print(f"📄 Collecting recent arXiv papers (with timeout protection)...")

            categories = self.source_config.get_arxiv_categories()

            if categories:
                category_query = " OR ".join([f"cat:{cat}" for cat in categories])
                if query:
                    search_query = f"({query}) AND ({category_query})"
                else:
                    search_query = category_query
            else:
                if query:
                    search_query = f"({query}) AND cat:cs.*"
                else:
                    search_query = "cat:cs.*"

            print(f"   🔍 arXiv query: {search_query}")
            print(f"   📥 Fetching recent papers with enhanced timeout handling...")

            papers = []

            def arxiv_fetch_with_timeout():
                nonlocal papers
                try:
                    search = arxiv.Search(
                        query=search_query,
                        max_results=30,
                        sort_by=arxiv.SortCriterion.SubmittedDate,
                        sort_order=arxiv.SortOrder.Descending
                    )

                    fetched_count = 0
                    cutoff_time = datetime.now() - timedelta(hours=48)

                    try:
                        results_iter = self.arxiv_client.results(search)

                        for paper in results_iter:
                            if fetched_count >= 30:
                                break

                            paper_date = paper.published.replace(tzinfo=None)

                            if paper_date >= cutoff_time:
                                papers.append({
                                    'source': 'arXiv',
                                    'title': paper.title.strip(),
                                    'url': paper.entry_id,
                                    'content': paper.summary.strip()[:1000],
                                    'published_date': paper.published.isoformat(),
                                    'search_topic': query or 'recent_arxiv'
                                })

                                if self.progress:
                                    self.progress.increment('arXiv')

                            fetched_count += 1
                            time.sleep(0.1)

                    except Exception as e:
                        print(f"   ⚠️ arXiv iterator error: {e}")

                except Exception as e:
                    print(f"   ❌ arXiv fetch error: {e}")

            fetch_thread = threading.Thread(target=arxiv_fetch_with_timeout, daemon=True)
            fetch_thread.start()
            fetch_thread.join(timeout=25)

            if fetch_thread.is_alive():
                print(f"   ⏰ arXiv fetch timed out after 25s, using {len(papers)} papers collected")
            else:
                print(f"   ✅ arXiv fetch completed: {len(papers)} recent papers")

            final_papers = papers[:max_results]
            print(f"   📊 arXiv complete: {len(final_papers)} papers returned ({len(papers)} total found)")

            return final_papers

        except Exception as e:
            print(f"   ❌ arXiv collection failed completely: {e}")
            print(f"   🔄 Continuing with other sources...")
            return []

    def collect_rss_feeds(self, feed_urls: List[str], topic_filter: Optional[str] = None,
                          time_range_hours: Optional[int] = None):
        """Improved RSS feed collection with better timeout handling and error recovery"""
        all_content = []
        max_entries = self.source_config.get_setting('rss_max_entries_per_feed', 20)

        cutoff_date = None
        if time_range_hours:
            cutoff_date = self.get_local_time() - timedelta(hours=time_range_hours)

        # Track failed feeds to prevent infinite loops
        failed_feeds = []

        for i, feed_url in enumerate(feed_urls):
            # Skip if this feed has already failed
            if feed_url in failed_feeds:
                continue

            try:
                feed_name = feed_url.split('/')[-2] if '/' in feed_url else feed_url[:30]
                self.progress.set_message(f"📡 Getting articles from {i + 1}/{len(feed_urls)}: {feed_name}")

                # Process feed with improved timeout handling
                feed_content = self._process_single_feed(feed_url, feed_name, max_entries, cutoff_date, topic_filter)

                if feed_content:
                    all_content.extend(feed_content)
                    print(f"   ✅ Got {len(feed_content)} articles from {feed_name}")
                else:
                    print(f"   ❌ No entries found in {feed_name}")
                    failed_feeds.append(feed_url)

            except Exception as e:
                print(f"   ❌ Error processing feed {feed_url}: {e}")
                failed_feeds.append(feed_url)
                continue

        if failed_feeds:
            print(f"   ⚠️ {len(failed_feeds)} feeds failed or returned no content")

        return all_content

    def _process_single_feed(self, feed_url: str, feed_name: str, max_entries: int,
                             cutoff_date, topic_filter: Optional[str]) -> List[Dict]:
        """Process a single RSS feed with improved error handling and timeout recovery"""

        def fetch_feed_with_timeout():
            """Fetch feed with proper error handling"""
            try:
                import feedparser
                return feedparser.parse(feed_url)
            except Exception as e:
                print(f"   ❌ Error parsing feed {feed_url}: {e}")
                return None

        # Use improved threading with explicit cleanup
        feed_result = [None]
        fetch_error = [None]

        def feed_fetch_thread():
            """Thread function with error capture"""
            try:
                result = fetch_feed_with_timeout()
                feed_result[0] = result
            except Exception as e:
                fetch_error[0] = e
                feed_result[0] = None

        # Create and start thread
        thread = threading.Thread(target=feed_fetch_thread, daemon=True)
        thread.start()

        # Wait for completion with timeout
        thread.join(timeout=15)

        # Check if thread completed or timed out
        if thread.is_alive():
            print(f"   ⏰ RSS feed {feed_name} timed out after 15 seconds")
            return []

        if fetch_error[0]:
            print(f"   ❌ Error fetching {feed_name}: {fetch_error[0]}")
            return []

        feed = feed_result[0]
        if not feed:
            print(f"   ❌ Failed to fetch {feed_name}")
            return []

        # Validate feed structure
        if not hasattr(feed, 'entries'):
            print(f"   ❌ Invalid feed structure for {feed_name}")
            return []

        if len(feed.entries) == 0:
            print(f"   ❌ No entries found in {feed_name}")
            return []

        # Process entries
        content = []
        feed_title = feed.feed.get('title', feed_url)

        for entry in feed.entries[:max_entries]:
            try:
                # Time filtering
                if cutoff_date:
                    try:
                        entry_date = self.parse_publication_date(entry.get('published', ''))
                        if entry_date < cutoff_date:
                            continue
                    except Exception:
                        pass  # Skip date filtering if parsing fails

                # Extract content
                title = entry.get('title', 'No title')
                summary = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()

                # Topic filtering
                if topic_filter:
                    combined_text = f"{title} {summary}".lower()
                    if topic_filter.lower() not in combined_text:
                        continue

                content.append({
                    'source': feed_title,
                    'title': title,
                    'url': entry.get('link', ''),
                    'content': summary,
                    'published_date': entry.get('published', ''),
                    'search_topic': topic_filter or 'general'
                })

            except Exception as e:
                print(f"   ⚠️ Error processing entry in {feed_name}: {e}")
                continue

        return content

    def search_database_content(self, query: str, limit: int = 20, session_context: Dict = None) -> List[Dict]:
        """Enhanced database search with session awareness"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            search_term = f'%{query.lower()}%'

            # Get discussed articles from session context
            discussed_urls = []
            if session_context and 'analyzed_articles' in session_context:
                discussed_urls = list(session_context['analyzed_articles'].keys())

            if discussed_urls:
                # Prioritize articles not yet discussed
                url_placeholders = ','.join(['?' for _ in discussed_urls])
                cursor.execute(f'''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           CASE WHEN url IN ({url_placeholders}) THEN 0 ELSE 1 END as discussion_priority,
                           'processed' as result_type
                    FROM content 
                    WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?
                    ORDER BY discussion_priority DESC, total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', discussed_urls + [search_term, search_term, search_term, limit])
            else:
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           'processed' as result_type
                    FROM content 
                    WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?
                    ORDER BY total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', (search_term, search_term, search_term, limit))

            processed_results = [dict(row) for row in cursor.fetchall()]

            remaining_limit = max(0, limit - len(processed_results))
            if remaining_limit > 0:
                cursor.execute('''
                    SELECT *, 'raw' as result_type
                    FROM raw_feeds 
                    WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(search_topic) LIKE ?
                    AND url NOT IN (SELECT url FROM content WHERE LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(search_topic) LIKE ?)
                    ORDER BY published_date DESC 
                    LIMIT ?
                ''', (search_term, search_term, search_term, search_term, search_term, search_term, remaining_limit))

                raw_results = [dict(row) for row in cursor.fetchall()]
            else:
                raw_results = []

            conn.close()

            all_results = processed_results + raw_results
            print(f"   📊 Database search for '{query}': {len(processed_results)} processed + {len(raw_results)} raw = {len(all_results)} total")

            return all_results

        except Exception as e:
            print(f"   ❌ Database search error: {e}")
            return []

    def simple_relevance_scoring(self, title: str, content: str, query: str) -> float:
        try:
            combined_text = f"{title} {content}".lower()
            query_words = query.lower().split()

            score = 0.0
            for word in query_words:
                if word in combined_text:
                    matches = combined_text.count(word)
                    score += matches * 0.5

                    if word in title.lower():
                        score += 1.0

            max_possible = len(query_words) * 2.0
            normalized_score = min((score / max_possible) * 10 if max_possible > 0 else 0, 10.0)

            return round(normalized_score, 1)

        except Exception:
            return 0.0

    def add_search_result_to_database(self, search_result: Dict, session_context: Dict = None) -> Dict:
        """Enhanced search result addition with session context"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT id FROM raw_feeds WHERE url = ?', (search_result['url'],))
            if cursor.fetchone():
                conn.close()
                return {'success': False, 'message': 'Article already exists in database'}

            cursor.execute('''
                INSERT INTO raw_feeds 
                (title, content, url, published_date, source, summary, author, 
                 category, content_hash, full_content, search_topic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                search_result['title'],
                search_result.get('snippet', ''),
                search_result['url'],
                search_result.get('published_date', datetime.now().isoformat()),
                search_result['source'],
                search_result.get('ai_summary', ''),
                '',
                'internet_search',
                hashlib.md5(search_result.get('full_content', '').encode()).hexdigest(),
                search_result.get('full_content', ''),
                search_result.get('search_topic', 'manual_search')
            ))

            cursor.execute('''
                INSERT INTO content 
                (source, title, url, content, summary, relevance_score, published_date, search_topic, conversation_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                search_result['source'],
                search_result['title'],
                search_result['url'],
                search_result.get('full_content', ''),
                search_result.get('ai_summary', ''),
                search_result.get('relevance_score', 0),
                search_result.get('published_date', datetime.now().isoformat()),
                search_result.get('search_topic', 'manual_search'),
                json.dumps(session_context) if session_context else None
            ))

            conn.commit()
            conn.close()

            print(f"   ✅ Added to database: {search_result['title']}")
            return {'success': True, 'message': 'Article added to database successfully'}

        except Exception as e:
            if 'conn' in locals():
                conn.close()
            print(f"   ❌ Error adding to database: {e}")
            return {'success': False, 'message': f'Error adding to database: {str(e)}'}

    def scrape_webpage(self, url: str) -> Optional[str]:
        if HAS_PDF_SUPPORT and is_pdf_url(url):
            print("   📄 Detected PDF file - using PDF processor")
            try:
                processor = PDFProcessor()
                result = processor.process_pdf_url(url)

                if result['success']:
                    print(f"   ✅ Successfully extracted {result['content_length']} characters from PDF")
                    return result['content']
                else:
                    print(f"   ❌ PDF processing failed: {result['error']}")
                    return None
            except Exception as e:
                print(f"   ❌ PDF processing error: {e}")
                return None

        return self._scrape_regular_webpage(url)

    def _scrape_regular_webpage(self, url):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }

            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            for script in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                script.decompose()

            content_text = ""

            content_selectors = [
                'article', 'main', '[role="main"]',
                '.content', '.article-content', '.post-content',
                '.entry-content', '.article-body', '.story-body',
                '#content', '#main-content', '#article-content'
            ]

            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    content_text = content_elem.get_text()
                    break

            if not content_text or len(content_text) < 200:
                content_text = soup.get_text()

            lines = (line.strip() for line in content_text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            clean_text = ' '.join(chunk for chunk in chunks if chunk)

            if len(clean_text) < 100:
                return None

            error_indicators = [
                'access denied', '404 not found', 'page not found',
                'javascript is disabled', 'enable javascript',
                'cloudflare', 'captcha', 'bot detection'
            ]

            clean_lower = clean_text.lower()
            if any(indicator in clean_lower for indicator in error_indicators):
                return None

            if len(clean_text) > 5000:
                clean_text = clean_text[:5000] + "... [content truncated]"

            return clean_text

        except requests.exceptions.RequestException as e:
            print(f"   ⚠️  Network error scraping {url}: {e}")
            return None
        except Exception as e:
            print(f"   ⚠️  Error scraping {url}: {e}")
            return None

    def check_pdf_support(self):
        if HAS_PDF_SUPPORT:
            from pdf_parser import get_pdf_capabilities
            return get_pdf_capabilities()
        else:
            return {
                'any_available': False,
                'status': 'no_module',
                'recommendations': [
                    'Create pdf_parser.py module',
                    'pip install pdfplumber PyPDF2 pymupdf'
                ]
            }

    def fetch_and_process_url_for_chat(self, url: str, session_context: Dict = None) -> Dict:
        """Fetch, process and save a single URL - reuses existing pipeline"""
        try:
            # Check if already exists
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM content WHERE url = ?', (url,))
                existing = cursor.fetchone()
                if existing:
                    return dict(existing)

            # Fetch content using existing method
            scraped_content = self.scrape_webpage(url)
            if not scraped_content:
                return {'error': f'Could not fetch content from {url}'}

            # Create article data using same structure as collection workflow
            from urllib.parse import urlparse
            domain = urlparse(url).netloc

            article_data = {
                'title': scraped_content.split('\n')[0][:100] if scraped_content else 'Article from URL',
                'url': url,
                'source': domain,
                'content': scraped_content,
                'published_date': self.get_local_time(as_string=True),
                'search_topic': 'direct_url'
            }

            # Process using existing pipeline
            summary, relevance_score = self.generate_summary_and_relevance(
                article_data, session_context=session_context
            )

            # Save using existing method (reuse save_content logic)
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO content 
                    (source, title, url, content, summary, relevance_score, published_date, search_topic, conversation_context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article_data['source'], article_data['title'], url, scraped_content,
                    summary, relevance_score, article_data['published_date'], 'direct_url',
                    json.dumps(session_context) if session_context else None
                ))
                conn.commit()

            return {
                'title': article_data['title'],
                'url': url,
                'source': article_data['source'],
                'content': scraped_content,
                'ai_summary': summary,
                'relevance_score': relevance_score,
                'published_date': article_data['published_date']
            }

        except Exception as e:
            return {'error': f'Error processing URL: {str(e)}'}

    def generate_summary_and_relevance(self, content: Dict, hot_topics_keywords: Optional[List[str]] = None,
                                     session_context: Dict = None) -> Tuple[str, float]:
        """Enhanced summary generation with session context awareness"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT interest, weight FROM user_interests')
            interests = cursor.fetchall()
            conn.close()

            interests_text = '\n'.join([f"- {interest} (importance weight: {weight})" for interest, weight in interests])
            
            hot_topics_text = ""
            if hot_topics_keywords:
                hot_topics_text = f"\n\nCURRENT HOT TOPICS (give extra relevance weight):\n{chr(10).join([f'- {keyword}' for keyword in hot_topics_keywords])}\n\nWhen scoring, add +2 points if the article covers any hot topics."

            # Add session context awareness
            session_context_text = ""
            if session_context and 'active_topics' in session_context:
                active_topics = list(session_context['active_topics'])[:5]
                if active_topics:
                    session_context_text = f"\n\nCURRENT DISCUSSION TOPICS (consider relevance):\n{chr(10).join([f'- {topic}' for topic in active_topics])}"

            text = content.get('content', '')
            if len(text) < 100 and content.get('url'):
                scraped = self.scrape_webpage(content['url'])
                if scraped:
                    text = scraped

            prompt = f"""Analyze this article and provide:
1. A 2-3 sentence summary focusing on key insights
2. A relevance score from 0-10 based on user interests, hot topics, and current discussion context

User interests: {interests_text}{hot_topics_text}{session_context_text}

Article:
Title: {content['title']}
Source: {content['source']}
Content: {text[:3000]}

Respond in JSON format:
{{"summary": "Your summary here", "relevance_score": <0-10>, "relevance_reasoning": "Brief explanation"}}"""

            response = ollama.chat(model=self.ollama_model, messages=[{'role': 'user', 'content': prompt}])
            response_text = response['message']['content'].strip()

            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                summary = result.get('summary', 'Summary generation failed.')
                relevance_score = min(max(float(result.get('relevance_score', 0)), 0), 10)
                return summary, relevance_score
            else:
                return response_text[:200] if response_text else "Summary generation failed.", 0.0

        except Exception as e:
            print(f"\nError generating summary: {e}")
            return "Summary generation failed.", 0.0

    def generate_summary_with_lock(self, content: Dict, session_context: Dict = None) -> Tuple[str, float]:
        """Enhanced summary generation with lock and session context"""
        url = content.get('url', '')

        if not url:
            return self.generate_summary_and_relevance(content, None, session_context)

        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()

                cursor.execute('BEGIN IMMEDIATE')

                cursor.execute('SELECT summary, relevance_score FROM content WHERE url = ?', (url,))
                existing = cursor.fetchone()

                if existing and existing[0] and len(existing[0].strip()) > 10:
                    conn.rollback()
                    print(f"   ✅ Using existing summary for {url}")
                    return existing[0], existing[1]

                conn.rollback()

                print(f"   🧠 Generating new summary for {url}")
                summary, score = self.generate_summary_and_relevance(content, None, session_context)

                with self.get_db_connection() as save_conn:
                    save_cursor = save_conn.cursor()
                    save_cursor.execute('BEGIN IMMEDIATE')

                    save_cursor.execute('SELECT summary FROM content WHERE url = ?', (url,))
                    existing = save_cursor.fetchone()

                    if existing and existing[0] and len(existing[0].strip()) > 10:
                        save_conn.rollback()
                        print(f"   🔄 Another process created summary for {url}, using theirs")
                        return existing[0], score

                    save_cursor.execute('''
                        INSERT OR REPLACE INTO content 
                        (source, title, url, content, summary, relevance_score, published_date, search_topic, conversation_context)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        content['source'], content['title'], url, content.get('content', ''),
                        summary, score, content.get('published_date', datetime.now().isoformat()),
                        content.get('search_topic', 'chat_generated'),
                        json.dumps(session_context) if session_context else None
                    ))

                    save_conn.commit()
                    print(f"   ✅ Saved new summary for {url}")
                    return summary, score

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"   ⏳ Database locked, retrying summary generation for {url}")
                time.sleep(0.1)
                return self.generate_summary_and_relevance(content, None, session_context)
            else:
                raise
        except Exception as e:
            print(f"   ❌ Error in summary generation with lock: {e}")
            return self.generate_summary_and_relevance(content, None, session_context)

    def save_raw_feeds(self, content_list: List[Dict]) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        new_items = []

        for content in content_list:
            try:
                cursor.execute('SELECT id FROM raw_feeds WHERE url = ?', (content['url'],))
                if cursor.fetchone():
                    continue

                content_text = content.get('content', '')
                content_hash = hashlib.md5(content_text.encode()).hexdigest() if content_text else ''

                cursor.execute('''
                    INSERT INTO raw_feeds 
                    (title, content, url, published_date, source, summary, author, 
                     category, content_hash, full_content, search_topic)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    content['title'], content.get('content', ''), content['url'],
                    content.get('published_date', datetime.now().isoformat()),
                    content['source'], content.get('summary', ''), content.get('author', ''),
                    content.get('category', ''), content_hash, content.get('full_content', ''),
                    content.get('search_topic', 'general')
                ))
                new_items.append(content)
            except Exception as e:
                print(f"\nError saving raw feed: {e}")

        conn.commit()
        conn.close()
        self.operation_stats['new_articles'] = len(new_items)
        return new_items

    def filter_new_content(self, content_list: List[Dict]) -> List[Dict]:
        if not content_list:
            return []

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        urls = [content['url'] for content in content_list]
        placeholders = ','.join('?' * len(urls))
        cursor.execute(f'SELECT url FROM content WHERE url IN ({placeholders})', urls)
        existing_urls = set(row[0] for row in cursor.fetchall())
        conn.close()

        new_content = [content for content in content_list if content['url'] not in existing_urls]
        if existing_urls:
            print(f"\nFiltered out {len(existing_urls)} articles already in database")
        return new_content

    def save_content(self, content_list: List[Dict], max_process: Optional[int] = None,
                     hot_topics_keywords: Optional[List[str]] = None, session_context: Dict = None):
        """Enhanced content saving with session context awareness"""
        if not content_list:
            print("   ℹ️  No content to process")
            return

        new_articles = cached_articles = processed_count = 0

        if max_process is not None:
            content_list = self.sort_content_by_date(content_list)
            print(f"   📊 Sorted {len(content_list)} articles by date")

        items_to_process = len(content_list) if max_process is None else min(len(content_list), max_process)
        phase_parts = ["AI Processing"]
        if hot_topics_keywords:
            phase_parts.append("+ Hot Topics Scoring")
        if session_context:
            phase_parts.append("+ Session Context")

        self.progress.update_phase(" ".join(phase_parts), items_to_process)
        print(f"   🔍 Processing {len(content_list)} articles...")

        batch_size = 5
        batches = [content_list[i:i + batch_size] for i in range(0, len(content_list), batch_size)]

        for batch_num, batch in enumerate(batches, 1):
            try:
                with self.get_db_connection_with_timeout(timeout_seconds=30) as conn:
                    cursor = conn.cursor()

                    print(f"   📦 Processing batch {batch_num}/{len(batches)} ({len(batch)} articles)")

                    for content in batch:
                        try:
                            cursor.execute('SELECT id, summary, relevance_score FROM content WHERE url = ?',
                                         (content['url'],))
                            existing = cursor.fetchone()

                            if existing:
                                cached_articles += 1
                                if self.progress:
                                    self.progress.increment()
                                continue

                            if max_process and processed_count >= max_process:
                                print(f"   ⏸️  Reached processing limit of {max_process} articles")
                                break

                            processed_count += 1
                            self.progress.set_message(
                                f"   🧠 Analyzing article {processed_count}/{items_to_process}: {content['title'][:50]}..."
                            )

                            try:
                                summary, relevance_score = self.generate_summary_and_relevance(
                                    content, hot_topics_keywords, session_context
                                )
                            except Exception as e:
                                print(f"   ⚠️ Summary generation failed for {content['title'][:30]}: {e}")
                                summary = "Summary generation failed"
                                relevance_score = 0.0

                            new_articles += 1

                            cursor.execute('''
                                INSERT OR IGNORE INTO content 
                                (source, title, url, content, summary, relevance_score, published_date, search_topic, conversation_context)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                content['source'], content['title'], content['url'],
                                content.get('content', ''), summary, relevance_score,
                                content.get('published_date', datetime.now().isoformat()),
                                content.get('search_topic', 'general'),
                                json.dumps(session_context) if session_context else None
                            ))

                        except Exception as e:
                            print(f"   ❌ Error processing article {content.get('title', 'Unknown')}: {e}")
                            continue

                    conn.commit()
                    print(f"   ✅ Batch {batch_num} committed to database")

            except Exception as e:
                print(f"   ❌ Error processing batch {batch_num}: {e}")
                continue

        self.operation_stats.update({
            'ai_processed': new_articles,
            'cached_skipped': cached_articles
        })

        print(f"   📈 Processing complete: {new_articles} new, {cached_articles} cached")

    def search_all_sources(self, topic: str, max_results_per_source: Optional[int] = None, 
                          time_range_hours: Optional[int] = None, session_context: Dict = None):
        """Enhanced source search with session context"""
        if max_results_per_source is None:
            max_results_per_source = self.source_config.get_setting('default_max_results_per_source', 5)

        time_info = f" (last {time_range_hours}h)" if time_range_hours else ""
        context_info = " [Session-aware]" if session_context else ""
        
        self.progress.update_phase(f"Searching all sources for: {topic}{time_info}{context_info}")
        self.progress.set_message(f"🔍 Starting enhanced search for: {topic}{time_info}")

        all_content = []
        rate_limit_delay = self.source_config.get_setting('rate_limit_delay_seconds', 2)

        arxiv_papers = self.collect_arxiv_papers(topic or "recent", max_results_per_source)
        all_content.extend(arxiv_papers)

        all_content.extend(self.collect_rss_feeds(self.source_config.get_rss_feeds(), topic_filter=topic, time_range_hours=time_range_hours))

        if time_range_hours:
            all_content = self.filter_by_time_range(all_content, time_range_hours)

        return all_content

    def run_collection_cycle(self, topic: Optional[str] = None, max_process: Optional[int] = None,
                             time_range_hours: Optional[int] = None, hot_topics_keywords: Optional[List[str]] = None,
                             session_context: Dict = None):
        """Enhanced collection cycle with session context support"""

        self.prune_old_content()

        operation_type = 'session_enhanced_collection' if session_context else (
            'hot_topics_collection' if hot_topics_keywords else (
                'topic_search' if topic else 'general_collection'))

        self.operation_stats = {
            'operation_type': operation_type,
            'search_topic': topic,
            'time_range_hours': time_range_hours,
            'hot_topics_used': hot_topics_keywords is not None,
            'session_enhanced': session_context is not None,
            'start_time': time.time(),
            'total_collected': 0,
            'new_articles': 0,
            'ai_processed': 0,
            'cached_skipped': 0
        }

        operation_name = "Session-Enhanced Collection" if session_context else (
            "Hot Topics Collection" if hot_topics_keywords else (
                f"Topic Search: {topic}" if topic else "General Collection"))
        if time_range_hours:
            operation_name += f" (last {time_range_hours}h)"

        self.progress.start_operation(operation_name)
        rate_limit = self.source_config.get_setting('rate_limit_delay_seconds', 2)

        try:
            all_content = []

            if topic:
                all_content.extend(self.search_all_sources(topic, time_range_hours=time_range_hours, session_context=session_context))
            else:
                self.progress.update_phase("Collecting from arXiv")
                arxiv_categories = self.source_config.get_arxiv_categories()
                if not arxiv_categories:
                    print("⚠️ Warning: No arXiv categories defined in config. Defaulting to broad query only.")

                for query_config in self.source_config.get_arxiv_queries():
                    try:
                        papers = self.collect_arxiv_papers(
                            query=query_config['query'],
                            max_results=query_config.get('max_results', 5)
                        )
                        all_content.extend(papers)
                        time.sleep(rate_limit)
                    except Exception as e:
                        print(f"❌ arXiv query failed: {e}, continuing...")
                        continue

                self.progress.update_phase("Collecting from RSS feeds")
                all_content.extend(
                    self.collect_rss_feeds(self.source_config.get_rss_feeds(), time_range_hours=time_range_hours))

            self.operation_stats['total_collected'] = len(all_content)
            time_info = f" (filtered to last {time_range_hours}h)" if time_range_hours else ""
            session_info = " [Session-enhanced]" if session_context else ""
            print(f"\n📊 Collection Summary: Total collected: {len(all_content)} articles{time_info}{session_info}")

            self.progress.update_phase("Saving to raw feeds database")
            new_raw_items = self.save_raw_feeds(all_content)
            print(f"   New raw items: {len(new_raw_items)}")

            new_content = self.filter_new_content(new_raw_items)
            print(f"   New content for AI processing: {len(new_content)}")

            if new_content:
                self.save_content(new_content, max_process=max_process, 
                                hot_topics_keywords=hot_topics_keywords, session_context=session_context)
            else:
                print("   ℹ️  No new content to process with AI")

            duration = int(time.time() - self.operation_stats['start_time'])
            self.save_statistics(duration)
            self.progress.finish(self.operation_stats)

            return self.operation_stats

        except Exception as e:
            print(f"\n❌ Collection failed: {e}")
            self.progress.finish()
            raise

    def save_statistics(self, duration_seconds: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        source_counts = self.progress.source_counts

        cursor.execute('''
            INSERT INTO statistics 
            (operation_type, duration_seconds, total_collected, new_articles, 
             ai_processed, cached_skipped, arxiv_count, hackernews_count, 
             reddit_count, rss_count, search_topic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            self.operation_stats.get('operation_type', 'unknown'), duration_seconds,
            self.operation_stats.get('total_collected', 0), self.operation_stats.get('new_articles', 0),
            self.operation_stats.get('ai_processed', 0), self.operation_stats.get('cached_skipped', 0),
            source_counts.get('arXiv', 0), source_counts.get('HackerNews', 0),
            source_counts.get('Reddit', 0), source_counts.get('RSS', 0),
            self.operation_stats.get('search_topic', None)
        ))

        conn.commit()
        conn.close()

    def get_operation_progress(self):
        return self.progress.get_status()

    def reload_sources_config(self):
        self.source_config.reload_config()

    def rescore_with_hot_topics(self, hot_topics_keywords: List[str], force_rescore: bool = False):
        self.progress.start_operation("Hot Topics Rescoring")
        today_date = datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, title, summary, relevance_score, hot_topics_date 
            FROM (
                SELECT * FROM content 
                ORDER BY added_date DESC 
                LIMIT 120
            ) recent_articles
            WHERE hot_topics_date != ? OR hot_topics_date IS NULL
        ''', (today_date,))

        articles_to_score = cursor.fetchall()
        conn.close()

        if not articles_to_score:
            if force_rescore:
                print("✅ No articles found to rescore")
            else:
                print("✅ All articles already have today's hot topics scoring")
            return {'total_articles': 0, 'articles_boosted': 0, 'average_boost': 0, 'max_boost_applied': 0}

        total_articles = len(articles_to_score)
        articles_boosted = total_boost_applied = max_boost_applied = 0

        self.progress.set_message(f"🔥 Re-scoring {total_articles} articles with hot topics: {', '.join(hot_topics_keywords)}")

        batch_size = 6
        batches = [articles_to_score[i:i + batch_size] for i in range(0, len(articles_to_score), batch_size)]
        self.progress.update_phase(f"Re-scoring with hot topics", total_articles)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            for batch_num, batch in enumerate(batches, 1):
                batch_articles = [f"Title: {article['title']}\nSummary: {article['summary'] or 'No summary available'}" for article in batch]

                keywords_text = ', '.join(hot_topics_keywords)
                prompt = f"""Count how many of these hot topics each article covers. Each matching topic adds +1 to the boost score.

HOT TOPICS: {keywords_text}

ARTICLES TO EVALUATE:
{chr(10).join([f"Article {i + 1}: {article}" for i, article in enumerate(batch_articles)])}

For each article, count how many hot topics it covers (0 = no match, 1 = one topic match, etc.).

Respond with ONLY a JSON object: {{"boosts": [0, 2, 1, 0, 3, 1]}}

The boosts array should have exactly {len(batch)} numbers."""

                try:
                    response = ollama.chat(model=self.ollama_model, messages=[{'role': 'user', 'content': prompt}])
                    response_text = response['message']['content'].strip()

                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        result = json.loads(json_match.group())
                        boosts = result.get('boosts', [])

                        if len(boosts) != len(batch):
                            print(f"⚠️  Warning: Expected {len(batch)} boost scores, got {len(boosts)}. Using available scores.")
                            boosts = boosts[:len(batch)]
                            while len(boosts) < len(batch):
                                boosts.append(0)

                        for article, boost in zip(batch, boosts):
                            boost = min(max(int(boost), 0), 10)
                            final_boost = boost * 2

                            cursor.execute('''
                                UPDATE content 
                                SET relevance_boost = ?, hot_topics_date = ?, hot_topics_keywords_used = ?
                                WHERE id = ?
                            ''', (final_boost, today_date, keywords_text, article['id']))

                            if final_boost > 0:
                                articles_boosted += 1
                                total_boost_applied += final_boost
                                max_boost_applied = max(max_boost_applied, final_boost)
                                self.progress.set_message(f"   🔥 '{article['title'][:50]}...' got +{final_boost} boost ({boost} topics matched)")

                            self.progress.increment()
                    else:
                        print(f"⚠️  Could not parse LLM response for batch {batch_num}")
                        for article in batch:
                            cursor.execute('''
                                UPDATE content 
                                SET relevance_boost = 0, hot_topics_date = ?, hot_topics_keywords_used = ?
                                WHERE id = ?
                            ''', (today_date, keywords_text, article['id']))
                            self.progress.increment()

                except Exception as e:
                    print(f"❌ Error processing batch {batch_num}: {e}")
                    for article in batch:
                        cursor.execute('''
                            UPDATE content 
                            SET relevance_boost = 0, hot_topics_date = ?, hot_topics_keywords_used = ?
                            WHERE id = ?
                        ''', (today_date, keywords_text, article['id']))
                        self.progress.increment()

                finally:
                    conn.commit()

            average_boost = total_boost_applied / articles_boosted if articles_boosted > 0 else 0

            return {
                'total_articles': total_articles, 
                'articles_boosted': articles_boosted,
                'average_boost': average_boost, 
                'max_boost_applied': max_boost_applied
            }
        except Exception as e:
            print(f"❌ Error in hot topics rescoring: {e}")
        finally:
            conn.commit()
            conn.close()
            self.progress.finish()

    def generate_summary_with_timeout(self, prompt, timeout_seconds=30):
        result = [None]
        error = [None]

        def ollama_call():
            try:
                response = ollama.chat(model=self.ollama_model, messages=[
                    {'role': 'user', 'content': prompt}
                ])
                result[0] = response
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=ollama_call, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            raise Exception(f"Ollama call timed out after {timeout_seconds} seconds")

        if error[0]:
            raise error[0]

        if result[0] is None:
            raise Exception("Ollama call returned no result")

        return result[0]