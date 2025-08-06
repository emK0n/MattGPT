#!/usr/bin/env python3
"""
Current Events Analysis - Unified Module
Combines Flask routes with analysis logic for simplicity
Externalized RSS sources configuration to JSON file
"""

import glob
import feedparser
import ollama
import json
import time
import os
import threading
from datetime import datetime
from flask import Blueprint, jsonify
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass
import re
import sqlite3
from contextlib import contextmanager
import ssl

# FIX SSL certificate issues on macOS
ssl._create_default_https_context = ssl._create_unverified_context

# Create blueprint for Flask integration
current_events_bp = Blueprint('current_events', __name__)

# Global variables for tracking analysis progress
current_events_output = []
current_events_progress = 0
current_events_status = "idle"
current_events_completed = False
current_events_success = False
current_events_error = None
current_events_thread = None
analyzer_instance = None
DATA_DIR = "data"
TOPICS_DIR = os.environ.get('TOPICS_DIR', 'currentevents')

# Global reference for completion signaling
research_assistant = None
current_session_id = None

def set_data_directory(data_dir: str):
    """Configure data directory"""
    global DATA_DIR
    DATA_DIR = data_dir

OLLAMA_MODEL = None

def set_ollama_model(model: str):
    """Configure ollama model"""
    global OLLAMA_MODEL
    OLLAMA_MODEL = model
    print(f"✅ Current events configured with model: {model}")

# Frontend callback setup
def set_frontend_callback(callback_func):
    """Set the frontend message callback"""
    add_output.frontend_callback = callback_func
    update_progress.frontend_callback = callback_func
    print("✅ Frontend callback connected to current_events")

@dataclass
class NewsSource:
    """Represents a news source with its RSS feed"""
    name: str
    url: str
    category: str

class NewsEventsAnalyzer:
    """Core analysis logic - can be used standalone or via Flask routes"""

    def __init__(self, ollama_model: str = None,
                 output_callback: Optional[Callable[[str, str], None]] = None,
                 data_dir: str = "data",
                 topics_dir: str = "currentevents"):
        self.ollama_model = ollama_model or "granite3.2:8b"
        self.output_callback = output_callback
        self.data_directory = data_dir
        self.topics_directory = topics_dir

        os.makedirs(self.data_directory, exist_ok=True)
        os.makedirs(self.topics_directory, exist_ok=True)
        
        # Load configuration from JSON file
        self.news_sources = self._load_news_sources()
        self.tech_business_keywords = self._load_tech_keywords()

    def _load_news_sources(self) -> List[NewsSource]:
        """Load news sources from JSON configuration"""
        config_path = os.path.join(self.data_directory, "current_events_sources.json")
        
        if not os.path.exists(config_path):
            self._log("❌ News and keyword sources not found, please create them then try again.", "error")
            raise FileNotFoundError("News and keyword sources not found, please create them then try again.")
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            sources = []
            for source_data in config.get('sources', []):
                sources.append(NewsSource(
                    name=source_data['name'],
                    url=source_data['url'],
                    category=source_data['category']
                ))
            
            if not sources:
                self._log("❌ No sources found in configuration file", "error")
                raise ValueError("No sources found in configuration file")
            
            self._log(f"✅ Loaded {len(sources)} sources from configuration")
            return sources
            
        except json.JSONDecodeError as e:
            self._log(f"❌ Invalid JSON in configuration file: {e}", "error")
            raise ValueError(f"Invalid JSON in configuration file: {e}")
        except Exception as e:
            self._log(f"❌ Error loading sources config: {e}", "error")
            raise

    def _load_tech_keywords(self) -> List[str]:
        """Load tech business keywords from JSON configuration"""
        config_path = os.path.join(self.data_directory, "current_events_sources.json")
        
        if not os.path.exists(config_path):
            self._log("❌ News and keyword sources not found, please create them then try again.", "error")
            raise FileNotFoundError("News and keyword sources not found, please create them then try again.")
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            keywords = config.get('tech_business_keywords', [])
            if not keywords:
                self._log("❌ No keywords found in configuration file", "error")
                raise ValueError("No keywords found in configuration file")
            
            self._log(f"✅ Loaded {len(keywords)} tech keywords")
            return keywords
            
        except json.JSONDecodeError as e:
            self._log(f"❌ Invalid JSON in configuration file: {e}", "error")
            raise ValueError(f"Invalid JSON in configuration file: {e}")
        except Exception as e:
            self._log(f"❌ Error loading keywords: {e}", "error")
            raise



    @contextmanager
    def get_db_connection(self, db_path: str):
        """Context manager for database connections"""
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def _log(self, message: str, message_type: str = "info"):
        """Internal logging method"""
        if self.output_callback:
            self.output_callback(message, message_type)
        else:
            print(message)
    
    def fetch_headlines(self, max_headlines_per_source: int = 5) -> List[Dict]:
        """Fetch headlines with SSL fix"""
        all_headlines = []
        successful_sources = 0
        
        self._log("Fetching headlines from tech & business sources...")
        self._log("=" * 50)
        
        for i, source in enumerate(self.news_sources, 1):
            try:
                self._log(f"{i}/{len(self.news_sources)} 📰 {source.name}")
                
                feed = feedparser.parse(source.url)
                
                if not hasattr(feed, 'entries') or len(feed.entries) == 0:
                    self._log(f"    ❌ No entries found in {source.name}")
                    continue
                
                source_count = 0
                for entry in feed.entries[:max_headlines_per_source * 2]:
                    try:
                        title = getattr(entry, 'title', '').strip()
                        if not title:
                            continue
                            
                        headline = {
                            'title': title,
                            'source': source.name,
                            'category': source.category,
                            'url': getattr(entry, 'link', ''),
                            'published': getattr(entry, 'published', ''),
                            'summary': getattr(entry, 'summary', '')[:300] if hasattr(entry, 'summary') and entry.summary else ''
                        }
                        
                        if self._is_tech_business_relevant(headline):
                            all_headlines.append(headline)
                            source_count += 1
                            
                            if source_count <= 2:
                                self._log(f"    ✅ '{title[:50]}{'...' if len(title) > 50 else ''}'")
                            
                            if source_count >= max_headlines_per_source:
                                break
                                
                    except Exception as e:
                        continue
                
                time.sleep(0.5)
                
            except Exception as e:
                self._log(f"    ❌ Error fetching from {source.name}: {e}", "error")
                continue
        
        self._log(f"   • Total headlines: {len(all_headlines)}")
        
        return all_headlines
    
    def _is_tech_business_relevant(self, headline: Dict) -> bool:
        """Check if headline is tech/business relevant"""
        title = headline['title'].lower()
        summary = headline.get('summary', '').lower()
        combined_text = f"{title} {summary}"
        
        if headline['category'] in ['technology', 'business']:
            return True
        
        if headline['category'] == 'world':
            return any(keyword in combined_text for keyword in self.tech_business_keywords)
        
        return True
    
    def analyze_headlines(self, headlines: List[Dict]) -> Dict:
        """Analyze headlines with LLM"""
        if not headlines:
            return {
                "summary": "No headlines available for analysis",
                "keywords": [],
                "trending_topics": [],
                "search_keywords": [],
                "analysis_timestamp": datetime.now().isoformat()
            }
        
        headlines_text = ""
        for headline in headlines[:20]:
            headlines_text += f"• {headline['title']} ({headline['source']})\n"

        prompt = f"""Analyze these tech and business headlines and extract key information.

Headlines:
{headlines_text}

Respond in JSON format:
{{
      "summary": "2-3 sentence summary of key tech/business trends",
      "keywords": ["word1", "word2", "word3"],
      "trending_topics": ["Multi word topic 1", "Another trending topic"]
}}

Requirements:
- keywords: List of words (not phrases) that can be used to search for other content related to today's top trending articles
- trending_topics: Can be phrases with spaces (e.g., "regulatory concerns", "AI advancements")
- summary: 2-3 sentences describing key trends
"""
        try:
            self._log("🧠 Analyzing headlines with AI...")
            
            response = ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': prompt}
            ])

            response_text = response['message']['content'].strip()
            
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
                
                if 'keywords' in analysis:
                    single_words = []
                    for kw in analysis['keywords']:
                        clean_kw = kw.strip().lower()
                        if ' ' not in clean_kw and 2 <= len(clean_kw) <= 20:
                            single_words.append(clean_kw)
                    analysis['keywords'] = single_words
                
                analysis['search_keywords'] = analysis.get('keywords', [])
                analysis['analysis_timestamp'] = datetime.now().isoformat()
                
                return analysis
            
        except Exception as e:
            self._log(f"❌ AI analysis failed: {e}", "error")
        
        return {
            "summary": f"Analyzed {len(headlines)} headlines covering technology and business topics",
            "keywords": [],
            "trending_topics": [],
            "search_keywords": [],
            "analysis_timestamp": datetime.now().isoformat()
        }
    
    def save_analysis(self, headlines: List[Dict], analysis: Dict, filename: Optional[str] = None):
        """Save analysis to file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d")
            filename = f"current_events_analysis_{timestamp}.json"

        full_path = os.path.join(self.topics_directory, filename)
        
        output = {
            "analysis": analysis,
            "focus": "technology_and_business",
            "headline_count": len(headlines),
            "sources_analyzed": list(set([h['source'] for h in headlines])),
            "raw_headlines": headlines
        }

        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        self._log(f"💾 Analysis saved to: {full_path}")
        return filename
    
    def run_analysis(self, max_headlines_per_source: int = 5, save_results: bool = True, db_path: str = None) -> Dict:
        """Run complete analysis pipeline - assumes we want NEW analysis"""
        
        # Clear previous flags if database provided
        if db_path:
            self._log("🧹 Clearing previous hot topics flags...")
            self.clear_hot_topics_flags(db_path)

        self._log("🚀 Starting Tech & Business Analysis")
        self._log("=" * 40)
        
        headlines = self.fetch_headlines(max_headlines_per_source)
        
        if not headlines:
            self._log("⚠️ No headlines collected - returning empty result", "warning")
            return {
                "summary": "Unable to collect headlines - check configuration and connectivity",
                "keywords": [],
                "trending_topics": [],
                "search_keywords": [],
                "analysis_timestamp": datetime.now().isoformat(),
                "fallback": True
            }
        
        analysis = self.analyze_headlines(headlines)
        
        if save_results:
            filename = self.save_analysis(headlines, analysis)
            analysis['saved_file'] = filename
        
        self._log(f"\n✅ Analysis Complete!")
        self._log(f"📊 Headlines: {len(headlines)}")
        self._log(f"🔍 Keywords: {len(analysis.get('search_keywords', []))}")
        
        keywords = analysis.get('search_keywords', [])
        if keywords:
            self._log(f"💡 Top keywords: {', '.join(keywords[:5])}")
        
        return analysis

    def clear_hot_topics_flags(self, db_path: str):
        """Clear hot topics flags from database"""
        try:
            with self.get_db_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE content 
                    SET relevance_boost = 0, hot_topics_date = NULL, hot_topics_keywords_used = NULL
                ''')
                rows_updated = cursor.rowcount
                conn.commit()
                self._log(f"🧹 Cleared flags from {rows_updated} articles")
                return rows_updated
        except Exception as e:
            self._log(f"❌ Error clearing flags: {e}", "error")
            return 0

# Flask route handlers
def _run_analysis_logic():
    """Shared analysis logic for both routes and podcast"""
    global current_events_completed, current_events_success, current_events_error
    global analyzer_instance

    try:
        add_output("🚀 Initializing Current Events Analyzer...")
        update_progress(5, "Initializing...")

        try:
            analyzer_instance = NewsEventsAnalyzer(
                ollama_model=OLLAMA_MODEL,
                output_callback=analyze_output_callback,
                data_dir=DATA_DIR,
                topics_dir=TOPICS_DIR
            )
            add_output("✅ Analyzer initialized successfully")
        except Exception as e:
            error_msg = f"Failed to initialize analyzer: {str(e)}"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        add_output("📡 Starting tech & business news analysis...")
        update_progress(10, "Starting analysis...")

        try:
            import ollama
            test_response = ollama.chat(
                model=analyzer_instance.ollama_model, 
                messages=[{'role': 'user', 'content': 'test'}]
            )
            add_output("✅ Ollama connection verified")
        except Exception as e:
            error_msg = f"Ollama connection failed: {str(e)}. Make sure Ollama is running with model '{analyzer_instance.ollama_model}'"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        try:
            add_output("🔍 Running complete analysis pipeline...")
            results = analyzer_instance.run_analysis(
                max_headlines_per_source=3,
                save_results=True,
                db_path=os.path.join(DATA_DIR, "content_curator.db")
            )
            add_output(f"📊 Analysis pipeline completed, checking results...")
            
        except Exception as e:
            error_msg = f"Analysis pipeline failed: {str(e)}"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        if not results:
            error_msg = "Analysis completed but returned empty results"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        if not isinstance(results, dict):
            error_msg = f"Analysis returned invalid data type: {type(results)}"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        required_keys = ['search_keywords', 'trending_topics']
        missing_keys = [key for key in required_keys if key not in results]
        if missing_keys:
            error_msg = f"Analysis results missing required keys: {missing_keys}"
            add_output(f"❌ {error_msg}", "error")
            current_events_error = error_msg
            current_events_success = False
            current_events_completed = True
            return

        keywords = results.get('search_keywords', [])
        topics = results.get('trending_topics', [])

        if not keywords and not topics:
            error_msg = "Analysis completed but generated no keywords or topics"
            add_output(f"⚠️ {error_msg}", "warning")

        add_output(f"✅ Analysis completed successfully!")
        add_output(f"📊 Generated {len(keywords)} single-word keywords")
        add_output(f"🔥 Identified {len(topics)} trending topics")

        if keywords:
            add_output(f"💡 Top keywords: {', '.join(keywords[:5])}")

        update_progress(100, "Analysis complete!")
        current_events_success = True

    except Exception as e:
        import traceback
        error_msg = f"Unexpected error during analysis: {str(e)}"
        add_output(f"❌ {error_msg}", "error")
        add_output(f"🔍 Full traceback: {traceback.format_exc()}", "error")
        current_events_error = error_msg
        current_events_success = False
        update_progress(0, "Error occurred")

    finally:
        current_events_completed = True
        analyzer_instance = None


def _reset_and_start_analysis():
    """Reset tracking variables and start analysis thread"""
    global current_events_output, current_events_progress, current_events_status
    global current_events_completed, current_events_success, current_events_error
    global current_events_thread

    current_events_output = []
    current_events_progress = 0
    current_events_status = "Starting analysis..."
    current_events_completed = False
    current_events_success = False
    current_events_error = None

    current_events_thread = threading.Thread(target=_run_analysis_logic, daemon=True)
    current_events_thread.start()

def add_output(message: str, message_type: str = "info"):
    """Add a message to the output log and send to frontend chat"""
    global current_events_output
    current_events_output.append({
        'message': message,
        'type': message_type,
        'timestamp': time.time()
    })
    print(message)

    # Send message directly to frontend via global message queue
    if hasattr(add_output, 'frontend_callback') and add_output.frontend_callback:
        # Format message for chat display with current events identifier
        chat_message = f"🔍 **Current Events:** {message}"
        add_output.frontend_callback(chat_message, message_type)

def update_progress(progress: int, status: str):
    """Update progress as chat message instead of progress bar"""
    global current_events_progress, current_events_status
    current_events_progress = progress
    current_events_status = status

    # Send progress directly to frontend with current events branding
    progress_msg = f"🔍 **Current Events Progress:** {status} ({progress}%)"
    if hasattr(update_progress, 'frontend_callback') and update_progress.frontend_callback:
        update_progress.frontend_callback(progress_msg, 'progress')

def analyze_output_callback(message: str, message_type: str = "info"):
    """Callback function to handle output from the analyzer"""
    global current_events_progress, current_events_status

    add_output(message, message_type)

    message_lower = message.lower()

    if 'starting ai analysis - sending first batch' in message_lower:
        update_progress(40, "AI analyzing first batch...")
    elif 'complete - sending batch' in message_lower:
        if 'batch 2/' in message_lower:
            update_progress(60, "AI analyzing batch 2...")
        elif 'batch 3/' in message_lower:
            update_progress(80, "AI analyzing batch 3...")
        else:
            update_progress(70, "AI analyzing next batch...")
    elif 'analysis complete' in message_lower and 'batch' in message_lower:
        update_progress(85, "Batch analysis complete...")

    if 'fetching headlines' in message_lower:
        update_progress(15, "Fetching headlines...")
    elif 'collected' in message_lower and 'headlines' in message_lower:
        update_progress(25, "Headlines collected")
    elif 'analyzing' in message_lower and 'headlines' in message_lower:
        update_progress(35, "Starting AI analysis...")
    elif 'consolidating' in message_lower:
        update_progress(90, "Consolidating results...")
    elif 'analysis saved' in message_lower:
        update_progress(95, "Saving results...")
    elif 'analysis complete' in message_lower and 'tech/business' in message_lower:
        update_progress(100, "Analysis complete!")


# Flask routes
@current_events_bp.route('/api/run-current-events', methods=['POST'])
def run_current_events():
    """Start current events analysis"""
    try:
        _reset_and_start_analysis()
        return jsonify({"status": "started"})
    except Exception as e:
        error_msg = f"Failed to start current events analysis: {str(e)}"
        return jsonify({"status": "error", "message": error_msg}), 500

@current_events_bp.route('/api/current-events-status', methods=['GET'])
def current_events_status_route():
    """Get current analysis status"""
    global current_events_output, current_events_progress, current_events_status
    global current_events_completed, current_events_success, current_events_error

    recent_output = current_events_output[-10:] if len(current_events_output) > 0 else []

    response = {
        "progress": current_events_progress,
        "status": current_events_status,
        "new_output": recent_output,
        "total_output_lines": len(current_events_output),
        "completed": current_events_completed,
        "success": current_events_success if current_events_completed else None,
        "error": current_events_error,
        "timestamp": time.time()
    }

    try:
        from app import curator
        curator_progress = curator.get_operation_progress()

        if curator_progress and curator_progress.get('batch_processing'):
            response.update({
                'batch_processing': True,
                'current_batch': curator_progress.get('current_batch', 0),
                'total_batches': curator_progress.get('total_batches', 0),
                'batch_progress_percent': curator_progress.get('batch_progress_percent', 0),
                'current_message': curator_progress.get('current_message', '')
            })
        else:
            response['batch_processing'] = False

    except Exception as e:
        response['batch_processing'] = False

    return jsonify(response)

@current_events_bp.route('/api/current-events-results', methods=['GET'])
def get_current_events_results():
    """Get the latest analysis results"""
    try:
        analyzer = NewsEventsAnalyzer(data_dir=DATA_DIR, topics_dir=TOPICS_DIR)
        
        # Check for existing analysis manually since we removed the method
        today = datetime.now().strftime("%Y%m%d")
        pattern = os.path.join(TOPICS_DIR, f"current_events_analysis_{today}.json")
        files = glob.glob(pattern)
        existing_file = max(files, key=os.path.getctime) if files else None
        
        if existing_file:
            with open(existing_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return jsonify({
                "status": "success",
                "data": data,
                "file": existing_file,
                "timestamp": datetime.fromtimestamp(time.time()).isoformat()
            })
        else:
            return jsonify({
                "status": "no_data",
                "message": "No current events analysis found for today"
            })
            
    except Exception as e:
        return jsonify({
            "status": "error", 
            "message": str(e)
        })

@current_events_bp.route('/api/current-events-health', methods=['GET'])
def health_check():
    """Simple health check for the current events service"""
    try:
        analyzer = NewsEventsAnalyzer(data_dir=DATA_DIR)
        
        import ollama
        test_response = ollama.chat(
            model=analyzer.ollama_model, 
            messages=[{'role': 'user', 'content': 'health check'}]
        )
        
        return jsonify({
            "status": "healthy",
            "service": "current_events_analyzer",
            "ollama_model": analyzer.ollama_model,
            "data_directory": DATA_DIR,
            "sources_loaded": len(analyzer.news_sources),
            "keywords_loaded": len(analyzer.tech_business_keywords),
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "service": "current_events_analyzer", 
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

# Utility functions for external use
def trigger_current_events_analysis(session_id=None):
    """Trigger analysis specifically for podcast workflow"""
    global current_session_id

    # Store session ID for completion signaling
    if session_id:
        current_session_id = session_id
        print(f"✅ Stored session ID for completion signaling: {session_id}")

    _reset_and_start_analysis()

def get_current_events_status():
    """Get current status for external monitoring"""
    return {
        'completed': current_events_completed,
        'success': current_events_success,
        'error': current_events_error,
        'status': current_events_status,
        'progress': current_events_progress
    }