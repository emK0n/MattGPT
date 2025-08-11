"""
Refactored Enhanced Chat Manager - Advanced Stateful Chat Implementation
Implements usage flows for conversational interactions without interfering with chain operations
Provides memory, context awareness, local files access, and database integration
Based on usage flows, project_plan_restore, and two-track architecture principles
"""

import sqlite3
import json
import os
import glob
import ollama
import time
import threading
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Generator
from contextlib import contextmanager
from flask import request, jsonify, Response
from customer_insights import create_customer_insights_handler
from entity_resolver import EntityResolutionPipeline
from file_upload_processor import create_file_uploader

DATA_DIRECTORY = "data"

try:
    from pdf_parser import is_pdf_url, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False
    print("⚠️ PDF processing not available")

try:
    from data_access_helper import ArticleCrossReferencer, HistoricalDataAccessor
    HAS_DATA_ACCESS = True
except ImportError:
    HAS_DATA_ACCESS = False
    print("⚠️ Data access helper not available")

class ChatIntentDetector:
    """
    Simplified intent detection for chat operations only
    """
    
    def __init__(self):
        # Chat-only intent patterns - NO chain operations
        self._chat_patterns = {
            'database_search': [
                r'articles in (?:my|your|the) database',
                r'what articles (?:do you have|are available)',
                r'show me (?:all )?articles (?:in|from)',
                r'list (?:all )?articles (?:in|from)',
                r'browse (?:all )?articles (?:in|from)',
                r'articles with (?:score|relevance)',
                r'other articles (?:in|from|available)',
                r'search (?:my|your|the) database',
                r'database articles',
                r'what articles have',
                r'articles about .+ in (?:database|collection)'
            ],
            'article_discussion': [
                r'(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+article',
                r'article\s+(\d+)',
                r'(?:the\s+)?(\d+)(?:st|nd|rd|th)\s+article',
                r'tell me more about\s+["\']([^"\']+)["\']',
                r'discuss\s+["\']([^"\']+)["\']',
                r'what\'?s (?:the )?(?:most )?important (?:finding|point|aspect|takeaway)',
                r'(?:most )?important (?:finding|point|aspect|takeaway)',
                r'key (?:finding|point|aspect|takeaway)',
                r'main (?:finding|point|aspect|takeaway)',
                r'what does (?:this|it) (?:say|mean|conclude)',
                r'what are (?:the )?(?:main|key|important) points',
                r'summarize (?:this|it)',
                r'explain (?:this|it)',
                r'break (?:this|it) down',
                r'what\'?s (?:the )?conclusion',
                r'findings?',
                r'results?',
                r'implications?'
            ],
            'url_analysis': [
                r'https?://[^\s]+',
                r'www\.[^\s]+',
                r'\.pdf\b',
                r'\.com\b',
                r'\.org\b',
                r'\.edu\b',
                r'\.gov\b'
            ],
            'cross_reference': [
                r'compare (?:this|these) (?:with|to|against)',
                r'similar articles',
                r'related (?:content|articles|stories)',
                r'other (?:articles|sources) (?:about|on)',
                r'cross.?reference',
                r'what else (?:do you have|is available)',
                r'additional (?:sources|articles)',
                r'more (?:on|about) (?:this|that) topic'
            ],
            'podcast_query': [
                r'(?:podcast|audio) (?:script|content|summary)',
                r'today\'?s podcast',
                r'recent podcast',
                r'podcast (?:about|on)',
                r'what\'?s in (?:the|today\'?s) podcast'
            ],
            'week_review_query': [
                r'week (?:in )?review',
                r'weekly (?:summary|analysis|report)',
                r'this week\'?s (?:events|news|highlights)',
                r'what happened (?:this|last) week'
            ],
            'current_events_query': [
                r'current events',
                r'today\'?s (?:news|events|analysis)',
                r'what\'?s (?:happening|going on) (?:today|now)',
                r'recent (?:news|events|developments)',
                r'trending (?:topics|stories)'
            ],
            'interest_management_trigger': [
                r'show\s+(?:my\s+)?interests?',
                r'list\s+(?:my\s+)?interests?',
                r'(?:what\s+are\s+)?my\s+interests?',
                r'manage\s+(?:my\s+)?interests?',
                r'interest\s+management',
                r'show\s+interest',
                r'display\s+interests?'
            ],
            'customer_insights': [
                r'^my customers?$',
                r'^analyze my customers?$',
                r'^do customer analysis$',
                r'^relate to my customers?$'
            ],
            'file_upload_trigger': [
                r'add (?:my )?file',
                r'upload (?:my )?file',
                r'upload (?:a )?document',
                r'add (?:my )?document',
                r'analyze (?:my )?file',
                r'process (?:my )?file',
                r'import (?:a )?file',
                r'load (?:my )?file'
            ]
        }
        
        self._confidence_threshold = 0.7
        self.entity_pipeline = EntityResolutionPipeline()

    def detect_intent(self, message: str) -> Dict[str, Any]:
        """
        Detect chat intent from message using pattern matching only
        Returns intent type and confidence score
        """
        message_lower = message.lower().strip()

        # Check all patterns (no special handling needed)
        for intent_type, patterns in self._chat_patterns.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    confidence = self._calculate_confidence(message_lower, pattern, intent_type)
                    if confidence >= self._confidence_threshold:
                        # NEW: Skip entity resolution for file upload and URL analysis contexts
                        if intent_type in ['file_upload_trigger', 'url_analysis']:
                            return {
                                'intent': intent_type,
                                'confidence': confidence,
                                'matched_pattern': pattern,
                                'original_message': message,
                                'resolved_message': message,
                                'skip_entity_resolution': True  # Flag to prevent customer matching
                            }

                        return {
                            'intent': intent_type,
                            'confidence': confidence,
                            'matched_pattern': pattern,
                            'original_message': message,
                            'resolved_message': message  # No entity resolution needed
                        }

        # Check for customer entities using EntityResolutionPipeline
        resolution_result = self.entity_pipeline.resolve_message(message)

        # Handle disambiguation needed case
        if resolution_result.get('needs_disambiguation'):
            return {
                'intent': 'disambiguation_needed',
                'confidence': 0.95,
                'disambiguation_prompt': resolution_result.get('disambiguation_prompt'),
                'disambiguation_context': resolution_result,
                'original_message': message
            }

        # Ensure resolved entities trigger customer_insights intent
        if resolution_result.get('customer_resolutions'):
            resolved_customers = [r for r in resolution_result['customer_resolutions']
                                  if r.get('result_type') == 'single_match' and not r.get('needs_confirmation')]
            if resolved_customers:
                return {
                    'intent': 'customer_insights',
                    'confidence': 0.9,
                    'matched_pattern': 'entity_resolution',
                    'original_message': message,
                    'resolved_message': resolution_result.get('resolved_message', message)
                }

        # Default to general conversation if no patterns match
        return {
            'intent': 'general_conversation',
            'confidence': 1.0,
            'matched_pattern': None,
            'original_message': message,
            'resolved_message': message
        }

    def _calculate_confidence(self, message: str, pattern: str, intent_type: str) -> float:
        """Calculate confidence score for pattern match"""
        base_confidence = 0.8
        
        # Boost confidence for exact matches
        if pattern in message:
            base_confidence += 0.15
        
        # Boost for intent-specific keywords
        if intent_type == 'database_search' and any(word in message for word in ['database', 'articles', 'search']):
            base_confidence += 0.1
        elif intent_type == 'url_analysis' and any(word in message for word in ['http', 'www', 'pdf', 'link']):
            base_confidence += 0.1
        elif intent_type == 'article_discussion' and any(word in message for word in ['article', 'discuss', 'explain']):
            base_confidence += 0.1
        
        return min(base_confidence, 1.0)

# =============================================================================
# CONVERSATION STATE AND MEMORY MANAGEMENT
# =============================================================================

class ConversationMemory:
    """
    Advanced conversation memory with context awareness
    Maintains conversation state, article discussions, and cross-references
    """
    
    def __init__(self, session_id: str, data_directory: str = DATA_DIRECTORY):
        self.session_id = session_id
        self.data_directory = data_directory
        self.conversation_history = []
        self.active_articles = {}
        self.discussion_threads = {}
        self.context_cache = {}
        self.last_activity = datetime.now()
        self.pending_disambiguation = None
        self.discuss_mode = False
        
        # Initialize storage
        os.makedirs(data_directory, exist_ok=True)
        self._load_existing_context()
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add message to conversation history with metadata"""
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        }
        
        self.conversation_history.append(message)
        self.last_activity = datetime.now()
        
        # Auto-persist periodically
        if len(self.conversation_history) % 10 == 0:
            self.persist_state()
    
    def add_article_context(self, article_data: Dict, discussion_context: Optional[str] = None):
        """Add article to active context for discussion"""
        article_id = article_data.get('url') or article_data.get('title', 'unknown')
        
        self.active_articles[article_id] = {
            'data': article_data,
            'added_at': datetime.now().isoformat(),
            'discussion_context': discussion_context,
            'discussion_count': 0
        }
        
        print(f"📝 Added article to context: {article_data.get('title', 'Unknown')}")

    def get_recent_messages(self, count: int = 5) -> List[Dict]:
        """Get the most recent messages from conversation history"""
        if not self.conversation_history:
            return []

        # Return the last 'count' messages
        return self.conversation_history[-count:] if len(
            self.conversation_history) >= count else self.conversation_history

    def get_active_articles(self) -> List[Dict]:
        """Get list of articles in active context"""
        return [
            {
                'id': article_id,
                'title': article_info['data'].get('title', 'Unknown'),
                'url': article_info['data'].get('url', ''),
                'added_at': article_info['added_at'],
                'discussion_count': article_info['discussion_count']
            }
            for article_id, article_info in self.active_articles.items()
        ]

    def has_active_articles(self) -> bool:
        """Check if there are any active articles in the conversation context"""
        return len(self.active_articles) > 0

    def get_conversation_context(self, max_messages: int = 10) -> str:
        """Generate conversation context for LLM"""
        recent_messages = self.conversation_history[-max_messages:] if self.conversation_history else []

        context_parts = []

        # Add active articles context with FULL content for discussion
        if self.active_articles:
            context_parts.append("## Active Articles in Discussion:")
            for article_id, article_info in self.active_articles.items():
                article_data = article_info['data']
                context_parts.append(f"\n### **{article_data.get('title', 'Unknown')}**")
                context_parts.append(f"**Source:** {article_data.get('source', 'Unknown')}")
                context_parts.append(f"**URL:** {article_data.get('url', '')}")

                # Include FULL AI summary and content for proper discussion
                if article_data.get('ai_summary'):
                    context_parts.append(f"**AI Summary:** {article_data['ai_summary']}")
                elif article_data.get('summary'):
                    context_parts.append(f"**Summary:** {article_data['summary']}")

                # Include article content if available (truncated to reasonable length)
                if article_data.get('content'):
                    content = article_data['content']
                    if len(content) > 3000:
                        content = content[:3000] + "... [content truncated]"
                    context_parts.append(f"**Content:** {content}")

                if article_data.get('relevance_score'):
                    context_parts.append(f"**Relevance Score:** {article_data['relevance_score']}")

                context_parts.append("")  # Add spacing between articles

        # Add recent conversation
        if recent_messages:
            context_parts.append("\n## Recent Conversation:")
            for msg in recent_messages:
                role_indicator = "User" if msg['role'] == 'user' else "Assistant"
                context_parts.append(f"{role_indicator}: {msg['content']}")

        return "\n".join(context_parts)
    
    def persist_state(self):
        """Save conversation state to disk"""
        try:
            state_data = {
                'session_id': self.session_id,
                'conversation_history': self.conversation_history,
                'active_articles': self.active_articles,
                'discussion_threads': self.discussion_threads,
                'last_activity': self.last_activity.isoformat(),
                'saved_at': datetime.now().isoformat()
            }
            
            filename = f"conversation_state_{self.session_id}.json"
            filepath = os.path.join(self.data_directory, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
            
            print(f"💾 Conversation state saved for session {self.session_id}")
            
        except Exception as e:
            print(f"⚠️ Error saving conversation state: {e}")
    
    def _load_existing_context(self):
        """Load existing conversation state from disk"""
        try:
            filename = f"conversation_state_{self.session_id}.json"
            filepath = os.path.join(self.data_directory, filename)
            
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                
                self.conversation_history = state_data.get('conversation_history', [])
                self.active_articles = state_data.get('active_articles', {})
                self.discussion_threads = state_data.get('discussion_threads', {})
                
                last_activity_str = state_data.get('last_activity')
                if last_activity_str:
                    self.last_activity = datetime.fromisoformat(last_activity_str)
                
                print(f"📂 Loaded existing conversation state for session {self.session_id}")
                print(f"   Messages: {len(self.conversation_history)}")
                print(f"   Active articles: {len(self.active_articles)}")
                
        except Exception as e:
            print(f"⚠️ Error loading conversation state: {e}")

    def set_disambiguation_context(self, context: Dict):
        """Set disambiguation context for pending entity resolution"""
        self.pending_disambiguation = context

    def has_pending_disambiguation(self) -> bool:
        """Check if there's a pending disambiguation"""
        return self.pending_disambiguation is not None

    def get_disambiguation_context(self) -> Dict:
        """Get the pending disambiguation context"""
        return self.pending_disambiguation

    def clear_disambiguation_context(self):
        """Clear the pending disambiguation context"""
        self.pending_disambiguation = None

    def set_discuss_mode(self, active: bool, article_title: str = None):
        """Set discuss mode state"""
        self.discuss_mode = active
        if active and article_title:
            print(f"🎯 Entering discuss mode for: {article_title}")
        elif not active:
            print("🎯 Exiting discuss mode")

    def is_in_discuss_mode(self) -> bool:
        """Check if currently in discuss mode"""
        return self.discuss_mode

    def clear_discuss_mode(self):
        """Clear discuss mode state"""
        self.discuss_mode = False
        print("🎯 Discuss mode cleared")

# =============================================================================
# LOCAL FILES AND DATABASE ACCESS
# =============================================================================

class LocalDataAccessor:
    """
    Access local files and database for reference and analysis
    Provides interface to articles, podcast scripts, week reviews, etc.
    """
    
    def __init__(self, content_curator, data_directory: str = DATA_DIRECTORY):
        self.content_curator = content_curator
        self.data_directory = data_directory
        self.topics_directory = os.environ.get('TOPICS_DIR', 'currentevents')
        
        # Initialize data access helper if available
        if HAS_DATA_ACCESS:
            self.cross_referencer = ArticleCrossReferencer(content_curator)
            self.historical_accessor = HistoricalDataAccessor(data_directory)
        else:
            self.cross_referencer = None
            self.historical_accessor = None

    def search_articles(self, query: str, limit: int = 10) -> List[Dict]:
        """Search articles in database"""
        try:
            with sqlite3.connect(self.content_curator.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Search in title, summary, and content - use correct table name 'content'
                search_query = f"%{query.lower()}%"
                cursor.execute("""
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE LOWER(title) LIKE ? 
                       OR LOWER(summary) LIKE ? 
                       OR LOWER(content) LIKE ?
                    ORDER BY total_relevance DESC, published_date DESC
                    LIMIT ?
                """, (search_query, search_query, search_query, limit))

                articles = []
                for row in cursor.fetchall():
                    article = dict(row)
                    articles.append(article)
                
                return articles
                
        except Exception as e:
            print(f"⚠️ Error searching articles: {e}")
            return []

    def get_recent_articles(self, days: int = 7, limit: int = 20) -> List[Dict]:
        """Get recent articles from database"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            with sqlite3.connect(self.content_curator.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Use correct table name 'content' instead of 'articles'
                cursor.execute("""
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE published_date >= ?
                    ORDER BY total_relevance DESC, published_date DESC
                    LIMIT ?
                """, (cutoff_date.isoformat(), limit))
                
                articles = []
                for row in cursor.fetchall():
                    article = dict(row)
                    articles.append(article)
                
                return articles
                
        except Exception as e:
            print(f"⚠️ Error getting recent articles: {e}")
            return []
    
    def get_podcast_scripts(self, days: int = 7) -> List[Dict]:
        """Get recent podcast scripts"""
        try:
            podcast_files = []
            podcast_dir = os.path.join(self.data_directory, "podcast_scripts")
            
            if os.path.exists(podcast_dir):
                pattern = os.path.join(podcast_dir, "*.md")
                for filepath in glob.glob(pattern):
                    stat = os.stat(filepath)
                    if datetime.fromtimestamp(stat.st_mtime) >= datetime.now() - timedelta(days=days):
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        podcast_files.append({
                            'filename': os.path.basename(filepath),
                            'filepath': filepath,
                            'content': content,
                            'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat()
                        })
            
            return sorted(podcast_files, key=lambda x: x['created_at'], reverse=True)
            
        except Exception as e:
            print(f"⚠️ Error getting podcast scripts: {e}")
            return []
    
    def get_week_reviews(self, weeks: int = 4) -> List[Dict]:
        """Get recent week review files"""
        try:
            review_files = []
            
            # Check multiple possible locations
            possible_dirs = [
                os.path.join(self.data_directory, "week_reviews"),
                os.path.join(self.data_directory, "reports"),
                self.data_directory
            ]
            
            for review_dir in possible_dirs:
                if os.path.exists(review_dir):
                    pattern = os.path.join(review_dir, "*week*review*.md")
                    for filepath in glob.glob(pattern):
                        stat = os.stat(filepath)
                        if datetime.fromtimestamp(stat.st_mtime) >= datetime.now() - timedelta(weeks=weeks):
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()
                            
                            review_files.append({
                                'filename': os.path.basename(filepath),
                                'filepath': filepath,
                                'content': content,
                                'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat()
                            })
            
            return sorted(review_files, key=lambda x: x['created_at'], reverse=True)
            
        except Exception as e:
            print(f"⚠️ Error getting week reviews: {e}")
            return []
    
    def get_current_events_analysis(self) -> Optional[Dict]:
        """Get latest current events analysis"""
        try:
            analysis_files = []
            
            # Look for current events analysis files
            patterns = [
                os.path.join(self.topics_directory, "*current*events*.json"),
                os.path.join(self.topics_directory, "*analysis*.json"),
                os.path.join(self.topics_directory, "current_events", "*.json")
            ]
            
            for pattern in patterns:
                for filepath in glob.glob(pattern):
                    stat = os.stat(filepath)
                    analysis_files.append((filepath, stat.st_mtime))
            
            if analysis_files:
                # Get most recent file
                latest_file = max(analysis_files, key=lambda x: x[1])[0]
                
                with open(latest_file, 'r', encoding='utf-8') as f:
                    analysis_data = json.load(f)
                
                return {
                    'filepath': latest_file,
                    'data': analysis_data,
                    'created_at': datetime.fromtimestamp(os.stat(latest_file).st_mtime).isoformat()
                }
            
            return None
            
        except Exception as e:
            print(f"⚠️ Error getting current events analysis: {e}")
            return None

# =============================================================================
# URL AND PDF PROCESSING
# =============================================================================

class URLProcessor:
    """
    Handle URL analysis and PDF processing for chat
    Implements usage flow: URL/PDF paste → summarize → add relevance score → add to database → open for discussion
    """
    
    def __init__(self, content_curator):
        self.content_curator = content_curator

    def process_url_from_chat(self, url: str, session_id: str) -> Dict[str, Any]:
        """
        Process URL from chat message - implements usage flow
        Returns processed article data for immediate discussion
        """
        try:
            print(f"🔗 Processing URL from chat: {url}")

            # Check if it's a PDF
            if HAS_PDF_SUPPORT and is_pdf_url(url):
                return self._process_pdf_url(url, session_id)
            else:
                return self._process_article_url(url, session_id)

        except Exception as e:
            print(f"⚠️ Error processing URL: {e}")
            return {
                'success': False,
                'error': str(e),
                'url': url
            }
    
    def _process_pdf_url(self, url: str, session_id: str) -> Dict[str, Any]:
        """Process PDF URL"""
        try:
            # Use PDF processor
            pdf_result = process_pdf(url)
            
            if pdf_result.get('success'):

                from pdf_parser import PDFProcessor
                processor = PDFProcessor()
                cleaned_content = processor._clean_pdf_text(pdf_result.get('content', ''))

                # Create article data for processing
                article_data = {
                    'url': url,
                    'title': self._get_human_readable_pdf_title(url, pdf_result.get('title'), cleaned_content),
                    'content': cleaned_content,
                    'source': pdf_result.get('source', 'pdf_upload'),
                    'published_date': datetime.now().isoformat(),
                    'search_topic': 'pdf_upload'
                }

                # Get hot topics for boost scoring
                hot_topics_keywords = []
                try:
                    # Get current hot topics for boost scoring
                    today_date = datetime.now().strftime('%Y-%m-%d')
                    hot_topics_status = self.content_curator.check_hot_topics_boost_status(today_date)
                    if hot_topics_status.get('keywords_used'):
                        hot_topics_keywords = hot_topics_status['keywords_used']
                except Exception as e:
                    print(f"⚠️ Could not get hot topics: {e}")

                # Generate AI summary and relevance score using existing pipeline
                session_context = {'session_id': session_id}
                summary, relevance_score = self.content_curator.generate_summary_and_relevance(
                    article_data, hot_topics_keywords=hot_topics_keywords, session_context=session_context
                )

                # Update article data with AI processing results
                article_data.update({
                    'ai_summary': summary,
                    'relevance_score': relevance_score,
                    'session_id': session_id
                })

                # Map article_data to the format expected by add_search_result_to_database
                search_result_format = {
                    'title': article_data['title'],
                    'url': article_data['url'],
                    'source': article_data['source'],
                    'published_date': article_data['published_date'],
                    'search_topic': article_data['search_topic'],
                    'ai_summary': article_data['ai_summary'],
                    'full_content': article_data['content'],  # This maps to 'content' field in database
                    'snippet': article_data['content'][:200] + '...' if len(article_data['content']) > 200 else
                    article_data['content'],
                    'relevance_score': article_data['relevance_score']
                }

                # Add to database using existing method
                self.content_curator.add_search_result_to_database(search_result_format, session_context)

                return {
                    'success': True,
                    'type': 'pdf',
                    'article_data': article_data,
                    'title': article_data['title'],
                    'source': article_data['source'],
                    'relevance_score': relevance_score,
                    'ai_summary': summary,
                    'message': f"PDF processed and added to database: **{article_data['title']}**"
                }
            else:
                return {
                    'success': False,
                    'error': pdf_result.get('error', 'Failed to process PDF'),
                    'url': url
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'url': url
            }

    def _get_human_readable_pdf_title(self, url: str, url_title: str, content: str) -> str:
        """
        Get human-readable title for PDF, checking URL readability first,
        then extracting from content if needed.
        """
        try:
            # Import here to avoid circular import
            from pdf_parser import PDFProcessor

            # Check if the current title (from URL) looks human-readable
            if url_title and self._is_human_readable_title(url_title):
                return url_title

            # If not readable, try to extract from content
            if content:
                return self._extract_title_from_pdf_content(content)

            # Fallback to original title or URL-based name
            return url_title or f"PDF Document - {url}"

        except Exception as e:
            print(f"   ⚠️ Error getting human readable title: {e}")
            return url_title or f"PDF Document - {url}"

    def _is_human_readable_title(self, title: str) -> bool:
        """Check if title appears human-readable."""
        if not title or len(title) < 5:
            return False

        # Remove common URL artifacts
        # Remove common non-human readable patterns (hashes, GUIDs, random strings)
        non_readable_patterns = [
            r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',  # GUIDs (check first)
            r'[0-9a-f]{8,}',  # Long hex strings (hashes, IDs)
            r'[0-9a-zA-Z]{12,}',  # Long alphanumeric strings
            r'temp_\w+',  # Temporary file patterns
            r'\w*[0-9]{6,}\w*',  # Strings with 6+ consecutive digits
        ]

        clean_title = title
        for pattern in non_readable_patterns:
            clean_title = re.sub(pattern, '', clean_title, flags=re.IGNORECASE)
        clean_title = clean_title.strip()

        # Check for human-readable patterns
        indicators = [
            len(clean_title.split()) >= 2,  # Multiple words
            any(char.isalpha() for char in clean_title),  # Contains letters
            not clean_title.replace('-', '').replace('_', '').isdigit(),  # Not just numbers/separators
            len(clean_title) > 10,  # Reasonable length
            any(word in clean_title.lower() for word in [
                'report', 'analysis', 'study', 'guide', 'cost', 'data', 'breach',
                'security', 'research', 'white', 'paper', 'document'
            ])  # Contains document-related keywords
        ]

        return sum(indicators) >= 2

    def _extract_title_from_pdf_content(self, content: str) -> str:
        """Extract title from PDF content by analyzing first few lines."""
        if not content:
            return "PDF Document"

        lines = [line.strip() for line in content.split('\n') if line.strip()]

        # Look for title in first 5 lines
        for line in lines[:5]:
            if self._looks_like_title_line(line):
                return self._clean_extracted_title(line)

        # Fallback: try to generate title from keywords
        return self._generate_title_from_keywords(content)

    def _looks_like_title_line(self, line: str) -> bool:
        """Check if line looks like a document title."""
        if len(line) < 10 or len(line) > 100:
            return False

        # Skip lines that look like headers/footers/metadata
        skip_patterns = [
            line.isdigit(),
            'page ' in line.lower(),
            '©' in line or 'copyright' in line.lower(),
            line.startswith(('http', 'www', 'ftp')),
            line.count('.') > 3,  # Too many periods (likely not a title)
        ]

        if any(skip_patterns):
            return False

        # Positive indicators for titles
        title_indicators = [
            line[0].isupper(),  # Starts with capital
            ' ' in line,  # Multiple words
            sum(c.isupper() for c in line) >= 2,  # Has some capitals
            not line.endswith('.'),  # Doesn't end with period
            any(word in line.lower() for word in [
                'report', 'analysis', 'cost', 'data', 'breach', 'security',
                'study', 'guide', 'research', 'overview', 'survey'
            ])
        ]

        return sum(title_indicators) >= 3

    def _clean_extracted_title(self, title: str) -> str:
        """Clean up extracted title."""
        # Remove common prefixes
        prefixes = ['title:', 'subject:', 'document:', 'report:']
        for prefix in prefixes:
            if title.lower().startswith(prefix):
                title = title[len(prefix):].strip()

        # Clean whitespace and limit length
        title = ' '.join(title.split())
        if len(title) > 80:
            title = title[:77] + "..."

        return title

    def _generate_title_from_keywords(self, content: str) -> str:
        """Generate title from content keywords as last resort."""
        # Look for key terms in first paragraph
        first_part = content[:500]

        # Common document keywords to look for
        keywords = []
        key_terms = [
            'data breach', 'cost', 'report', 'analysis', 'security',
            'cyber', 'incident', 'risk', 'threat', 'vulnerability'
        ]

        for term in key_terms:
            if term in first_part.lower():
                keywords.append(term.title())

        if keywords:
            return f"{' '.join(keywords[:3])} Report"

        return "PDF Document"

    def _process_article_url(self, url: str, session_id: str) -> Dict[str, Any]:
        """Process regular article URL"""
        try:
            # Use content curator to fetch and process
            result = self.content_curator.fetch_and_process_url_for_chat(url,
                                                                         session_context={'session_id': session_id})

            # Handle different return formats
            if isinstance(result, dict):
                if result.get('error'):
                    return {
                        'success': False,
                        'error': result['error'],
                        'url': url
                    }
                else:
                    # Success case - result contains the article data directly
                    return {
                        'success': True,
                        'type': 'article',
                        'article_data': result,
                        'title': result.get('title', 'Unknown'),
                        'source': result.get('source', 'Unknown'),
                        'relevance_score': result.get('relevance_score', 0),
                        'message': f"Article processed and added to database: **{result.get('title', 'Unknown')}**"
                    }
            else:
                return {
                    'success': False,
                    'error': 'No data returned from URL processing',
                    'url': url
                }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'url': url
            }

# =============================================================================
# MAIN ENHANCED CHAT MANAGER
# =============================================================================

class EnhancedChatManager:
    """
    Advanced stateful chat manager with memory, context awareness, and local data access
    Implements usage flows for conversational interactions only
    NO chain operations - those are handled by chain_operations_manager
    """
    
    def __init__(self, content_curator, chat_assistant, data_directory: str = DATA_DIRECTORY):
        self.content_curator = content_curator
        self.chat_assistant = chat_assistant
        self.data_directory = data_directory
        
        # Initialize components
        self.intent_detector = ChatIntentDetector()
        self.data_accessor = LocalDataAccessor(content_curator, data_directory)
        self.url_processor = URLProcessor(content_curator)
        self.customer_insights = create_customer_insights_handler(
            content_curator, chat_assistant, data_directory
        )
        self.file_uploader = create_file_uploader(content_curator)
        
        # Session management
        self.active_sessions = {}
        
        print("🧠 Enhanced Chat Manager initialized")
        print(f"   PDF Support: {'✅' if HAS_PDF_SUPPORT else '❌'}")
        print(f"   Data Access Helper: {'✅' if HAS_DATA_ACCESS else '❌'}")
        print("   🎯 Focus: Conversational interactions only")
        print("   🚫 NO chain operations (handled by chain_operations_manager)")

    def get_or_create_session(self, session_id: str) -> ConversationMemory:
        """Get or create conversation session"""

        from datetime import datetime
        today = datetime.now().date()

        # FIX: Change self.sessions to self.active_sessions and check if last_activity exists
        expired_sessions = [sid for sid, session in self.active_sessions.items() if
                            hasattr(session, 'last_activity') and session.last_activity.date() != today]
        for expired_id in expired_sessions:
            del self.active_sessions[expired_id]

        if session_id not in self.active_sessions:
            self.active_sessions[session_id] = ConversationMemory(session_id, self.data_directory)
            print(f"📱 Created new chat session: {session_id}")

        return self.active_sessions[session_id]
    
    def process_chat_message(self, session_id: str, message: str, 
                           article_data: Optional[Dict] = None) -> Generator[str, None, None]:
        """
        Main entry point for chat message processing
        Returns streaming response generator
        Implements usage flows for chat interactions
        """
        try:
            print(f"💬 Processing chat message - Session: {session_id}")
            
            # Get or create session
            conversation = self.get_or_create_session(session_id)
            
            # Add user message to history
            conversation.add_message('user', message)

            # Check if this is a disambiguation response
            if conversation.has_pending_disambiguation():
                context = conversation.get_disambiguation_context()
                result = self.intent_detector.entity_pipeline.handle_disambiguation_response(
                    context['original_message'], message, context
                )
                conversation.clear_disambiguation_context()

                if result['action'] == 'resolved':
                    message = result['resolved_message']
                    yield result['message']  # Confirmation message
                elif result['action'] == 'cancelled':
                    yield result['message']
                    return
                else:  # unclear
                    yield result['message']
                    return

            url_pattern = r'https?://[^\s]+'
            urls = re.findall(url_pattern, message)

            if urls:
                yield "🔍 **URL Detected** - Processing and adding to database...\n\n"
                for url in urls:
                    try:
                        result = self.url_processor.process_url_from_chat(url, session_id)
                        if result.get('success'):
                            yield f"✅ **Added to Database:** {result.get('title', 'Document')}\n"
                            yield f"**Source:** {result.get('source', 'Unknown')}\n"
                            if result.get('relevance_score'):
                                yield f"**Relevance Score:** {result.get('relevance_score'):.2f}\n\n"
                            yield "🗣️ **Ready to Discuss** - Ask me anything about this content!\n\n"

                            # Add processed article to conversation context
                            if result.get('article_data'):
                                conversation.add_article_context(result['article_data'], f"URL processed: {url}")
                        else:
                            yield f"⚠️ **Processing Failed:** {result.get('error', 'Unknown error')}\n\n"
                    except Exception as e:
                        yield f"❌ **Error processing URL:** {str(e)}\n\n"
                return

            # If article data provided, add to context
            if article_data:
                conversation.add_article_context(article_data, f"User discussing: {message}")

            # Check for new customer insights triggers using fuzzy detection
            from fuzzy_commands import detect_fuzzy_command
            command_result = detect_fuzzy_command(message)

            # FIX: Use discuss mode state to determine priority
            if conversation.is_in_discuss_mode():
                # IN DISCUSS MODE: Prioritize specific customer lookup over general insights
                if command_result['matched'] and command_result['command'].startswith('dynamic_customer_lookup'):
                    yield from self._handle_customer_insights(conversation, message,
                                                              {'intent': 'customer_insights', 'confidence': 1.0})
                    return
                elif command_result['matched'] and command_result['command'] == 'show_customers':
                    yield from self._handle_customer_insights(conversation, message,
                                                              {'intent': 'customer_insights', 'confidence': 1.0})
                    return
            else:
                # NOT IN DISCUSS MODE: Use original general customer insights logic
                if command_result['matched'] and command_result['command'] == 'customer_insights_trigger':
                    yield from self._handle_customer_insights(conversation, message,
                                                              {'intent': 'customer_insights', 'confidence': 1.0})
                    return
                elif command_result['matched'] and command_result['command'] == 'show_customers':
                    yield from self._handle_customer_insights(conversation, message,
                                                              {'intent': 'customer_insights', 'confidence': 1.0})
                    return

            # Check if this is an article discussion request (has article_data)
            if article_data:
                print("🎯 Article discussion detected via article_data parameter")
                intent_result = {'intent': 'article_discussion', 'confidence': 1.0}
                yield from self._handle_article_discussion(conversation, message, intent_result, article_data)
                return

            # Check for customer insights patterns in discuss mode
            if conversation.is_in_discuss_mode():
                customer_insight_patterns = [
                    'customer', 'relate to customers', 'customer insights', 'customer relevance',
                    'opportunities', 'which customers', 'customer conversations'
                ]

                if any(pattern in message.lower() for pattern in customer_insight_patterns):
                    # Auto-prompt for customer selection in discuss mode
                    yield "🎯 **Customer Insights in Discuss Mode**\n\nI can analyze how this article relates to your customers!\n\nPlease specify:\n• **Customer name** (e.g., 'TechCorp Inc')\n• **Cluster name** (e.g., 'Tech Startups')\n• **'show customers'** to browse database\n\nWhat would you like to analyze?"

                    conversation.add_message('assistant', "Customer insights prompt in discuss mode", {
                        'intent': 'customer_insights_prompt',
                        'discuss_mode': True
                    })
                    return

            # Detect intent
            intent_result = self.intent_detector.detect_intent(message)
            intent = intent_result['intent']

            # Handle disambiguation needed
            if intent_result['intent'] == 'disambiguation_needed':
                yield intent_result['disambiguation_prompt']
                conversation.set_disambiguation_context(intent_result['disambiguation_context'])
                return

            print(f"🎯 Detected intent: {intent} (confidence: {intent_result['confidence']:.2f})")

            if intent_result['intent'] == 'interest_management_trigger':
                yield json.dumps({'type': 'frontend_callback', 'function': 'offerInterestManagement',
                                  'message': '🎯 Opening interest management...'})
                return
            # Route to appropriate handler
            if intent == 'url_analysis':
                yield from self._handle_url_analysis(conversation, message, intent_result)
            elif intent == 'database_search':
                yield from self._handle_database_search(conversation, message, intent_result)
            elif intent == 'article_discussion':
                yield from self._handle_article_discussion(conversation, message, intent_result)
            elif intent == 'cross_reference':
                yield from self._handle_cross_reference(conversation, message, intent_result)
            elif intent == 'podcast_query':
                yield from self._handle_podcast_query(conversation, message, intent_result)
            elif intent == 'week_review_query':
                yield from self._handle_week_review_query(conversation, message, intent_result)
            elif intent == 'current_events_query':
                yield from self._handle_current_events_query(conversation, message, intent_result)
            elif intent == 'customer_insights':
                yield from self._handle_customer_insights(conversation, message, intent_result)
            elif intent == 'file_upload_trigger':
                yield from self._handle_file_upload_trigger(conversation, message, intent_result)
            else:
                yield from self._handle_general_conversation(conversation, message, intent_result)
            
        except Exception as e:
            print(f"❌ Error processing chat message: {e}")
            yield f"❌ **Error**: {str(e)}"
    
    def _handle_url_analysis(self, conversation: ConversationMemory, 
                           message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle URL/PDF analysis from usage flows"""
        try:
            # Extract URL from message
            url_pattern = r'https?://[^\s]+'
            url_match = re.search(url_pattern, message)
            
            if not url_match:
                yield "I couldn't find a valid URL in your message. Please provide a complete URL."
                return
            
            url = url_match.group(0)
            
            yield f"🔗 **Processing URL**: {url}\n\n"
            
            # Process URL using URL processor
            result = self.url_processor.process_url_from_chat(url, conversation.session_id)
            
            if result.get('success'):
                article_data = result['article_data']
                
                # Add to conversation context
                conversation.add_article_context(article_data, "URL analysis from chat")
                
                # Stream response
                yield f"✅ {result['message']}\n\n"
                
                if article_data.get('ai_summary'):
                    yield f"**Summary**: {article_data['ai_summary']}\n\n"
                
                if article_data.get('key_topics'):
                    topics_str = ", ".join(article_data['key_topics'][:5])
                    yield f"**Key Topics**: {topics_str}\n\n"
                
                yield f"**Relevance Score**: {article_data.get('relevance_score', 0):.2f}\n\n"
                yield "The article is now available for discussion. What would you like to know about it?"
                
                # Add assistant message to history
                response_content = f"Processed URL: {article_data.get('title', 'Unknown')}"
                conversation.add_message('assistant', response_content, {
                    'intent': 'url_analysis',
                    'article_url': url,
                    'article_title': article_data.get('title')
                })
                
            else:
                error_msg = result.get('error', 'Unknown error')
                yield f"❌ **Error processing URL**: {error_msg}"
                
        except Exception as e:
            yield f"❌ **Error in URL analysis**: {str(e)}"
    
    def _handle_database_search(self, conversation: ConversationMemory, 
                              message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle database article search"""
        try:
            # Extract search terms
            search_terms = self._extract_search_terms(message)
            
            yield f"🔍 **Searching database for**: {search_terms}\n\n"
            
            # Search articles
            articles = self.data_accessor.search_articles(search_terms, limit=10)
            
            if articles:
                yield f"📚 **Found {len(articles)} articles:**\n\n"
                
                for i, article in enumerate(articles, 1):
                    title = article.get('title', 'Unknown Title')
                    score = article.get('relevance_score', 0)
                    created_date = article.get('created_date', '')
                    
                    yield f"**{i}.** {title}\n"
                    yield f"   Score: {score:.2f} | Date: {created_date[:10]}\n\n"
                
                yield "Would you like me to discuss any of these articles? Just ask about 'article 1', 'article 2', etc."
                
                # Add articles to conversation context
                for article in articles[:5]:  # Add top 5 to context
                    conversation.add_article_context(article, f"Database search: {search_terms}")
                
            else:
                yield f"❌ No articles found for '{search_terms}'. Try different search terms or check if articles have been added to the database."
            
            # Add to conversation history
            conversation.add_message('assistant', f"Database search for: {search_terms}", {
                'intent': 'database_search',
                'search_terms': search_terms,
                'results_count': len(articles)
            })
            
        except Exception as e:
            yield f"❌ **Error searching database**: {str(e)}"

    def _handle_article_discussion(self, conversation: ConversationMemory,
                                   message: str, intent_result: Dict, article_data: Dict = None) -> Generator[
        str, None, None]:
        """Handle article discussion from usage flows"""
        try:
            # NEW: Check if this is actually a customer correlation request in discuss mode
            if self._detect_customer_correlation_request(message, conversation):
                print(
                    "🔄 Detected customer correlation request during article discussion - routing to customer insights")
                # Route to customer insights instead
                yield from self._handle_customer_insights(conversation, message,
                                                          {'intent': 'customer_insights', 'confidence': 1.0})
                return

            # First, check if article_data was passed directly (from modal discuss function)
            if article_data:
                # Add the article to conversation context for this discussion
                conversation.add_article_context(article_data, "Article discussion from modal")
                conversation.set_discuss_mode(True, article_data.get('title', 'Unknown Title'))
                yield f"📖 **Discussing Article**: {article_data.get('title', 'Unknown Title')}\n\n"

                # Build context with the specific article
                context_parts = [
                    "You are discussing a specific article with a user. Here's the article context:",
                    "",
                    f"**Title**: {article_data.get('title', 'Unknown')}",
                    f"**Summary**: {article_data.get('ai_summary', 'No summary available')}",
                    "",
                    "Please provide a helpful, detailed response about this article or answer the user's question.",
                    "Focus on the content, implications, and insights from the article."
                ]
            else:
                # Check if there are active articles in context
                active_articles = conversation.get_active_articles()

                if not active_articles:
                    yield "I don't have any articles in our current conversation context. Please search for articles or paste a URL first."
                    return

                # Build enhanced context for discussion
                context_parts = [
                    "You are discussing articles with a user. Here's the context:",
                    "",
                    conversation.get_conversation_context(),
                    "",
                    "Please provide a helpful, detailed response about the articles or answer the user's question.",
                    "Focus on the content, implications, and insights from the articles."
                ]

            enhanced_context = "\n".join(context_parts)

            # For direct article discussion, use content curator's chat method with article context
            if article_data:
                # Build comprehensive article context
                article_discussion_context = f"""You are discussing a specific article with a user. Here's the complete article information:

            **Title:** {article_data.get('title', 'Unknown')}
            **Source:** {article_data.get('source', 'Unknown')}
            **URL:** {article_data.get('url', '')}
            **Summary:** {article_data.get('ai_summary', article_data.get('summary', 'No summary available'))}
            **Content:** {article_data.get('content', 'No content available')}

            Please provide a helpful, detailed response about this article. You have access to the full content above.
            Focus on the content, implications, and insights from the article.

            User's question: {message}

            Response:"""

                # Use chat assistant's streaming response method
                for chunk in self.chat_assistant.generate_streaming_response(article_discussion_context):
                    yield chunk
            else:
                # Generate response using chat assistant for general article discussion
                yield from self._generate_streaming_response(
                    message,
                    enhanced_context,
                    conversation,
                    intent_result
                )
            
        except Exception as e:
            yield f"❌ **Error in article discussion**: {str(e)}"
    
    def _handle_cross_reference(self, conversation: ConversationMemory, 
                              message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle cross-referencing articles"""
        try:
            # Get active articles
            active_articles = conversation.get_active_articles()
            
            if not active_articles:
                yield "I need some articles in our conversation context first. Please search for articles or paste URLs."
                return
            
            yield "🔗 **Cross-referencing articles...**\n\n"
            
            # Get related articles from database
            search_terms = self._extract_search_terms(message)
            if not search_terms:
                # Use topics from active articles
                all_topics = []
                for article_info in conversation.active_articles.values():
                    topics = article_info['data'].get('key_topics', [])
                    all_topics.extend(topics)
                search_terms = " ".join(all_topics[:3]) if all_topics else "related"
            
            related_articles = self.data_accessor.search_articles(search_terms, limit=15)
            
            # Filter out articles already in context
            active_urls = {info['data'].get('url') for info in conversation.active_articles.values()}
            new_related = [art for art in related_articles if art.get('url') not in active_urls]
            
            if new_related:
                yield f"📚 **Found {len(new_related)} related articles:**\n\n"
                
                for i, article in enumerate(new_related[:8], 1):
                    title = article.get('title', 'Unknown')
                    score = article.get('relevance_score', 0)
                    yield f"**{i}.** {title} (Score: {score:.2f})\n"
                
                yield "\n**Analysis of Connections:**\n\n"
                
                # Use LLM to analyze connections
                context = self._build_cross_reference_context(conversation.active_articles, new_related[:5])
                yield from self._generate_streaming_response(
                    "Analyze the connections and common themes between these articles. What insights emerge from comparing them?",
                    context,
                    conversation,
                    intent_result
                )
            else:
                yield "No additional related articles found in the database."
            
        except Exception as e:
            yield f"❌ **Error in cross-referencing**: {str(e)}"
    
    def _handle_podcast_query(self, conversation: ConversationMemory, 
                            message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle podcast script queries"""
        try:
            yield "🎙️ **Searching podcast scripts...**\n\n"
            
            podcast_scripts = self.data_accessor.get_podcast_scripts(days=7)
            
            if podcast_scripts:
                yield f"📻 **Found {len(podcast_scripts)} recent podcast scripts:**\n\n"
                
                for script in podcast_scripts:
                    filename = script['filename']
                    created_at = script['created_at'][:10]
                    yield f"- **{filename}** (Created: {created_at})\n"
                
                yield "\n"
                
                # Analyze query for specific questions
                latest_script = podcast_scripts[0]
                context = f"Latest podcast script content:\n\n{latest_script['content'][:2000]}..."
                
                yield from self._generate_streaming_response(
                    message,
                    context,
                    conversation,
                    intent_result
                )
            else:
                yield "❌ No recent podcast scripts found. Podcasts may not have been generated yet."
            
        except Exception as e:
            yield f"❌ **Error querying podcast scripts**: {str(e)}"
    
    def _handle_week_review_query(self, conversation: ConversationMemory, 
                                message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle week review queries"""
        try:
            yield "📊 **Searching week reviews...**\n\n"
            
            week_reviews = self.data_accessor.get_week_reviews(weeks=4)
            
            if week_reviews:
                yield f"📋 **Found {len(week_reviews)} recent week reviews:**\n\n"
                
                for review in week_reviews:
                    filename = review['filename']
                    created_at = review['created_at'][:10]
                    yield f"- **{filename}** (Created: {created_at})\n"
                
                yield "\n"
                
                # Use latest review for context
                latest_review = week_reviews[0]
                context = f"Latest week review content:\n\n{latest_review['content'][:2000]}..."
                
                yield from self._generate_streaming_response(
                    message,
                    context,
                    conversation,
                    intent_result
                )
            else:
                yield "❌ No recent week reviews found. Week reviews may not have been generated yet."
            
        except Exception as e:
            yield f"❌ **Error querying week reviews**: {str(e)}"
    
    def _handle_current_events_query(self, conversation: ConversationMemory, 
                                   message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle current events queries"""
        try:
            yield "📰 **Searching current events analysis...**\n\n"
            
            current_events = self.data_accessor.get_current_events_analysis()
            
            if current_events:
                analysis_data = current_events['data']
                created_at = current_events['created_at'][:10]
                
                yield f"📅 **Current events analysis from {created_at}:**\n\n"
                
                # Extract key information
                if isinstance(analysis_data, dict):
                    if 'summary' in analysis_data:
                        yield f"**Summary**: {analysis_data['summary']}\n\n"
                    
                    if 'top_stories' in analysis_data:
                        yield "**Top Stories:**\n"
                        for story in analysis_data['top_stories'][:5]:
                            if isinstance(story, dict):
                                title = story.get('title', str(story))
                                yield f"- {title}\n"
                        yield "\n"
                
                # Use current events data for context
                context = f"Current events analysis:\n\n{json.dumps(analysis_data, indent=2)[:2000]}..."
                
                yield from self._generate_streaming_response(
                    message,
                    context,
                    conversation,
                    intent_result
                )
            else:
                yield "❌ No current events analysis found. The analysis may not have been run yet."
            
        except Exception as e:
            yield f"❌ **Error querying current events**: {str(e)}"

    def _handle_general_conversation(self, conversation: ConversationMemory,
                                     message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle general conversation with context awareness"""
        try:
            # Clear discuss mode for general conversation (unless it's a follow-up question about articles)
            article_follow_up_indicators = ['article', 'this', 'that', 'it', 'what does', 'explain']
            is_article_follow_up = any(indicator in message.lower() for indicator in article_follow_up_indicators)

            if conversation.is_in_discuss_mode() and not is_article_follow_up:
                conversation.clear_discuss_mode()
            # Build context from conversation history and active articles
            context = conversation.get_conversation_context()

            # Detect URL requests
            url_request_patterns = [
                r'what.*url', r'url.*article', r'link.*article', r'where.*read',
                r'source.*url', r'give.*url', r'provide.*url'
            ]
            is_url_request = any(re.search(pattern, message.lower()) for pattern in url_request_patterns)

            if is_url_request:
                enhanced_context = f"""You are an AI assistant helping with article analysis and research. Here's the current context:

    {context}

    USER MESSAGE: {message}

    IMPORTANT: The user is asking for a URL. Respond with ONLY the URL in this exact format:
    🔗 **Article Title**: URL

    Do not provide summaries, explanations, or additional information. Just the title and URL.
    """
            else:
                enhanced_context = f"""You are an AI assistant helping with article analysis and research. Here's the current context:

    {context}

    Please provide a helpful response to the user's message. If there are articles or research data in the context, you can reference them in your response. Be conversational and helpful.
    """

            yield from self._generate_streaming_response(
                message,
                enhanced_context,
                conversation,
                intent_result
            )

        except Exception as e:
            yield f"❌ **Error in general conversation**: {str(e)}"

    def _generate_streaming_response(self, message: str, context: str, 
                                   conversation: ConversationMemory, 
                                   intent_result: Dict) -> Generator[str, None, None]:
        """Generate streaming response using chat assistant"""
        try:
            # Build full prompt
            full_prompt = f"{context}\n\nUser: {message}\n\nAssistant:"
            
            # Use chat assistant for response
            response_text = ""
            
            for chunk in self.chat_assistant.generate_streaming_response(full_prompt):
                response_text += chunk
                yield chunk
            
            # Add response to conversation history
            conversation.add_message('assistant', response_text, {
                'intent': intent_result['intent'],
                'confidence': intent_result['confidence']
            })
            
        except Exception as e:
            yield f"❌ **Error generating response**: {str(e)}"
    
    def _extract_search_terms(self, message: str) -> str:
        """Extract search terms from message"""
        # Remove common stop words and extract meaningful terms
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'about', 'into', 'through', 'during', 'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once', 'what', 'how', 'when', 'where', 'why', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'articles', 'database', 'search', 'show', 'find', 'tell', 'me'}
        
        words = re.findall(r'\b\w+\b', message.lower())
        meaningful_words = [word for word in words if word not in stop_words and len(word) > 2]
        
        return ' '.join(meaningful_words[:5])  # Limit to 5 most meaningful words
    
    def _build_cross_reference_context(self, active_articles: Dict, related_articles: List[Dict]) -> str:
        """Build context for cross-referencing articles"""
        context_parts = [
            "Cross-reference analysis context:",
            "",
            "ACTIVE ARTICLES IN DISCUSSION:"
        ]
        
        for article_id, article_info in active_articles.items():
            article_data = article_info['data']
            context_parts.append(f"- {article_data.get('title', 'Unknown')}")
            if article_data.get('ai_summary'):
                context_parts.append(f"  Summary: {article_data['ai_summary'][:150]}...")
        
        context_parts.extend([
            "",
            "RELATED ARTICLES FOUND:"
        ])
        
        for article in related_articles:
            context_parts.append(f"- {article.get('title', 'Unknown')}")
            if article.get('ai_summary'):
                context_parts.append(f"  Summary: {article['ai_summary'][:150]}...")
        
        return "\n".join(context_parts)

    def _handle_customer_insights(self, conversation: ConversationMemory,
                                  message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle customer insights requests - simplified for trigger-based entry"""
        try:
            response_parts = []

            # 1. Check for explicit exit first
            if self.customer_insights.detect_exit_request(message):
                conversation.clear_discuss_mode()
                yield "✅ **Returning to general chat.** Feel free to ask about anything!"
                conversation.add_message('assistant', "Exited customer insights mode", {
                    'intent': 'customer_insights_exit'
                })
                return

            # 2. Check for follow-up questions
            if self.customer_insights.detect_follow_up_question(message):
                response = self.customer_insights.process_follow_up_question(message)
                yield response
                conversation.add_message('assistant', response, {
                    'intent': 'customer_insights',
                    'interaction_type': 'follow_up'
                })
                return

            # 3. Set article context for customer insights
            active_articles_dict = conversation.active_articles
            if active_articles_dict:
                # Extract article data for context
                active_articles_data = []
                for article_info in active_articles_dict.values():
                    active_articles_data.append(article_info['data'])

                context_data = {
                    'active_articles': active_articles_data
                }
                self.customer_insights.set_conversation_context(context_data)
            else:
                self.customer_insights.set_conversation_context(None)

            # 4. COMMENTING IN CASE THIS BREAKS SOMETHING Show analysis target if in discuss mode
            #if conversation.is_in_discuss_mode():
            #    active_articles = conversation.get_active_articles()
            #    if active_articles:
            #        first_article = active_articles[0]
            #        article_title = first_article.get('data', {}).get('title', 'Unknown Article')
            #        analysis_target = f"🎯 **Analysis Target**: How '{article_title}' relates to your selection...\n\n"
            #        response_parts.append(analysis_target)
            #        yield analysis_target

            # 5. Process customer selection
            resolved_message = intent_result.get('resolved_message', message)
            for chunk in self.customer_insights.process_customer_selection(resolved_message):
                response_parts.append(chunk)
                yield chunk

            # 6. Save complete response to conversation memory
            full_response = "".join(response_parts)
            conversation.add_message('assistant', full_response, {
                'intent': 'customer_insights',
                'interaction_type': 'customer_selection'
            })

        except Exception as e:
            yield f"❌ **Error in customer insights**: {str(e)}"

    def _handle_file_upload_trigger(self, conversation: ConversationMemory,
                                    message: str, intent_result: Dict) -> Generator[str, None, None]:
        """Handle file upload trigger - prompt user to drag and drop"""
        try:
            yield "📁 **Ready for file upload!** Please drag and drop your file into the chat area. PDF, TXT, DOC, DOCX, RTF, MD are all supported"
        except Exception as e:
            yield f"❌ **Error initiating file upload**: {str(e)}"

    def _handle_file_upload_result(self, conversation: ConversationMemory,
                                   upload_result: Dict) -> Generator[str, None, None]:
        """Handle completed file upload result"""
        if upload_result['success']:
            # Add to conversation context for discussion
            article_data = upload_result['article_data']
            conversation.add_article_to_context(article_data)

            # Yield the success message
            yield upload_result['message']
        else:
            yield f"❌ **Upload Failed:** {upload_result['error']}"

    def _is_article_discussion_context(self, conversation: ConversationMemory, message: str) -> bool:
        """Check if user is asking about article content in relational context"""
        # Check if user has active articles AND is using relational language
        has_active_articles = conversation.has_active_articles()
        is_relational_query = any(phrase in message.lower() for phrase in [
            'relate to', 'relates to', 'how does this relate', 'connection to',
            'how does', 'what does this', 'does this relate'
        ])

        return has_active_articles and is_relational_query

    def _is_customer_name_in_message(self, message: str) -> bool:
        """Check if message contains a customer name from the database"""
        try:
            message_lower = message.lower().strip()

            # Check customer names
            all_customers = self.customer_insights.data_manager.get_all_customers()
            for customer in all_customers:
                customer_name = customer.get('customer_name', '').lower()
                if customer_name and customer_name in message_lower:
                    return True

            # Check cluster names
            clusters = self.customer_insights.data_manager.get_clusters()
            for cluster in clusters:
                if cluster.lower() in message_lower:
                    return True

            return False
        except Exception as e:
            print(f"Error checking customer/cluster names: {e}")
            return False

    def _detect_customer_correlation_request(self, message: str, conversation: ConversationMemory) -> bool:
        """
        Detect if user is requesting customer correlation analysis in discuss mode
        This distinguishes between general article questions vs customer analysis requests
        """
        # Only applies when in discuss mode with active articles
        if not conversation.is_in_discuss_mode():
            return False

        # COMMENTING OUT IN CASE NEW CODE BREAKS
        #customer_correlation_patterns = [
        #    'relate to customers', 'relates to customers', 'customer relevance',
        #    'customer opportunities', 'which customers', 'customer insights',
        #    'customer conversations', 'customers would', 'customer correlation',
        #    'relevant to customers', 'customer analysis', 'customer implications'
        #]
        customer_correlation_patterns = [
            r'^how can i use this\b(?:\s+with\s+(.+))?$',
            r'^how is this relevant\b(?:\s+for\s+(.+))?$',
            r'^what can i do with this\?$'
        ]

        message_lower = message.lower()

        # Check for explicit customer correlation requests
        has_correlation_pattern = any(pattern in message_lower for pattern in customer_correlation_patterns)

        # Check for customer/cluster names (indicates switching to customer analysis)
        has_customer_reference = self._is_customer_name_in_message(message)

        # Check for cluster-specific keywords
        cluster_keywords = ['cluster', 'segment', 'group of customers']
        has_cluster_reference = any(keyword in message_lower for keyword in cluster_keywords)

        # Return True if any correlation indicators are present
        return has_correlation_pattern or has_customer_reference or has_cluster_reference

# =============================================================================
# FACTORY FUNCTIONS AND INTEGRATION
# =============================================================================

def create_enhanced_chat_manager(content_curator, chat_assistant, 
                                data_directory: str = DATA_DIRECTORY) -> EnhancedChatManager:
    """Factory function to create enhanced chat manager"""
    print("🏭 Creating Enhanced Chat Manager...")
    
    return EnhancedChatManager(
        content_curator=content_curator,
        chat_assistant=chat_assistant,
        data_directory=data_directory
    )

def add_enhanced_chat_endpoints(app, enhanced_chat_manager):
    """Add enhanced chat endpoints to Flask app"""
    
    @app.route('/api/chat/message', methods=['POST'])
    def chat_message():
        """Enhanced chat message endpoint with streaming"""
        try:
            data = request.get_json()
            session_id = data.get('session_id', 'default')
            message = data.get('message', '')
            article_data = data.get('article_data')

            def generate():
                for chunk in enhanced_chat_manager.process_chat_message(session_id, message, article_data):
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            
            return Response(generate(), mimetype='text/plain')
            
        except Exception as e:
            print(f"❌ API Error: /api/chat/message - {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/chat/article-discussion', methods=['POST'])
    def article_discussion():
        """Article discussion endpoint"""
        try:
            data = request.get_json()
            session_id = data.get('session_id', 'default')
            message = data.get('message', '')
            article_data = data.get('article_data', {})
            
            def generate():
                for chunk in enhanced_chat_manager.process_chat_message(session_id, message, article_data):
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            
            return Response(generate(), mimetype='text/plain')
            
        except Exception as e:
            print(f"❌ API Error: /api/chat/article-discussion - {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/chat/url-analysis', methods=['POST'])
    def url_analysis():
        """URL analysis endpoint"""
        try:
            data = request.get_json()
            session_id = data.get('session_id', 'default')
            url = data.get('url', '')
            
            # Process URL analysis as chat message
            message = f"Please analyze this URL: {url}"
            
            def generate():
                for chunk in enhanced_chat_manager.process_chat_message(session_id, message):
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            
            return Response(generate(), mimetype='text/plain')
            
        except Exception as e:
            print(f"❌ API Error: /api/chat/url-analysis - {e}")
            return jsonify({'error': str(e)}), 500

# =============================================================================
# MODULE INITIALIZATION
# =============================================================================

if __name__ == "__main__":
    print("Enhanced Chat Manager Module - Advanced Stateful Chat")
    print(f"PDF Support: {'Available' if HAS_PDF_SUPPORT else 'Not Available'}")
    print(f"Data Access Helper: {'Available' if HAS_DATA_ACCESS else 'Not Available'}")
    print("🎯 Focus: Conversational interactions, memory, context awareness")
    print("🚫 NO chain operations (delegated to chain_operations_manager)")
    print("✅ Implements usage flows for chat-based article discussion and analysis")
