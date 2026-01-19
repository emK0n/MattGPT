"""
Refactored app.py - Two-Track Architecture Implementation
Implements separation of concerns between Chain Operations and Enhanced Chat
Based on usage flows, project_plan_restore, and chain_operations_manager.py patterns
"""

from typing import Dict
from flask import Flask, render_template, jsonify, request, session, Response, send_file
import sqlite3
from datetime import datetime, timedelta
import os
import threading
import json
import glob
import time


# Import core components
from content_curator import ContentCurator, ChatAssistant
from research_agent import ResearchAssistant
from current_events import current_events_bp, set_data_directory, set_ollama_model
from week_review_integration import initialize_week_review, add_week_review_endpoints
from podcast_generator import PodcastGenerator
from interest_mgmt import InterestManager
from chain_operations_manager import create_chain_operations_manager, add_chain_operations_endpoints
from customer_mgmt import CustomerManager
from file_upload_processor import create_file_uploader

# Define Data structure and create directory first
DATA_DIR = os.environ.get('DATA_DIR', 'data')
os.makedirs(DATA_DIR, exist_ok=True)

PCAST_DIR = os.environ.get('PCAST_DIR', 'podcasts')
os.makedirs(PCAST_DIR, exist_ok=True)

TOPICS_DIR = os.environ.get('TOPICS_DIR', 'currentevents')
os.makedirs(TOPICS_DIR, exist_ok=True)

os.makedirs('templates', exist_ok=True)

# Initialize core components
curator = ContentCurator(
    db_path=os.path.join(DATA_DIR, "content_curator.db"),
    config_file=os.path.join(DATA_DIR, "sources_config.json")
)

curator.progress.output_callback = lambda msg, msg_type='info': queue_frontend_message(msg, msg_type, None, None)

interest_manager = InterestManager(db_path=os.path.join(DATA_DIR, "content_curator.db"), ollama_model=curator.ollama_model)

# Import enhanced chat components (graceful degradation)
try:
    from data_access_helper import ArticleCrossReferencer, HistoricalDataAccessor
    HAS_ENHANCED_CHAT = True
    print("✅ Enhanced chat features available")
except ImportError as e:
    HAS_ENHANCED_CHAT = False
    print(f"⚠️ Enhanced chat features not available: {e}")

customer_manager = CustomerManager(data_directory=DATA_DIR, cache_directory="cache")

# Import PDF Support (graceful degradation)
try:
    from pdf_parser import PDFProcessor, is_pdf_url, get_pdf_capabilities, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

def get_interests_for_content_scoring():
    """Helper function to get interests for content scoring integration"""
    try:
        return interest_manager.get_interests_for_scoring()
    except Exception as e:
        print(f"Warning: Could not load interests for scoring: {e}")
        return []

# Global message queue for frontend delivery
frontend_message_queue = []
frontend_message_lock = threading.Lock()

def queue_frontend_message(message: str, message_type: str = "info", chain_flag=None, session_id=None):
    """Queue a message for frontend delivery"""
    with frontend_message_lock:
        frontend_message_queue.append({
            'content': message,
            'type': message_type,
            'timestamp': time.time(),
            'chain_flag': chain_flag,
            'session_id': session_id
        })
        # Keep only last 50 messages to prevent memory issues
        if len(frontend_message_queue) > 50:
            frontend_message_queue.pop(0)

# Flask Web Interface
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Initialize components in dependency order
chat_assistant = ChatAssistant(os.path.join(DATA_DIR, "content_curator.db"), curator.ollama_model)

# Initialize enhanced chat components (if available)
if HAS_ENHANCED_CHAT:
    article_cross_referencer = ArticleCrossReferencer(curator)
    historical_data_accessor = HistoricalDataAccessor(chat_assistant, DATA_DIR)

# Initialize research assistant (simplified to session management only)
research_assistant = ResearchAssistant(curator, chat_assistant)

# Init enhanced chat manager
enhanced_chat_manager = None
if HAS_ENHANCED_CHAT:
    try:
        from enhanced_chat_manager import create_enhanced_chat_manager, add_enhanced_chat_endpoints
        print("🧠 Initializing Enhanced Chat Manager...")
        enhanced_chat_manager = create_enhanced_chat_manager(
            content_curator=curator,
            chat_assistant=chat_assistant,
            data_directory=DATA_DIR
        )
        # Add enhanced chat endpoints to the app
        add_enhanced_chat_endpoints(app, enhanced_chat_manager)
        print("✅ Enhanced Chat Manager initialized and endpoints added")
    except Exception as e:
        print(f"⚠️ Failed to initialize Enhanced Chat Manager: {e}")
        enhanced_chat_manager = None
        HAS_ENHANCED_CHAT = False

# Initialize specialized processors
week_review_processor = initialize_week_review(chat_assistant, research_assistant, data_directory=TOPICS_DIR)
import current_events
current_events.research_assistant = research_assistant
podcast_generator = PodcastGenerator(curator, chat_assistant, PCAST_DIR, TOPICS_DIR, research_assistant)
current_events.set_frontend_callback(lambda msg, msg_type='info': queue_frontend_message(msg, msg_type, None, None))
podcast_generator.progress_callback = lambda msg, msg_type='info': queue_frontend_message(msg, msg_type, None, None)
week_review_processor.set_frontend_callback(lambda msg, msg_type='info': queue_frontend_message(msg, msg_type, None, None))

# =============================================================================
# INITIALIZE CHAIN OPERATIONS MANAGER
# =============================================================================

print("🏭 Initializing Chain Operations Manager...")
chain_operations_manager = create_chain_operations_manager(
    content_curator=curator,
    current_events_module=current_events,
    week_review_processor=week_review_processor,
    podcast_generator=podcast_generator,
)

# Register blueprints
app.register_blueprint(current_events_bp)
set_data_directory(DATA_DIR)
set_ollama_model(curator.ollama_model)

# Add week review endpoints (legacy support)
add_week_review_endpoints(app)

# Add new chain operations endpoints
add_chain_operations_endpoints(app, chain_operations_manager)

print("   📊 Chain Operations Track: /api/chain/*")
print("   💬 Enhanced Chat Track: /api/chat/*")

@app.route('/')
def index():
    template_path = os.path.join('templates', 'index.html')
    if not os.path.exists(template_path):
        with open(template_path, 'w', encoding='utf-8') as template_file:
            template_file.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Research Assistant Agent</title>
</head>
<body>
</body>
</html>""")
    return render_template('index.html')

@app.route('/arch')
def new_page():
    return render_template('arch.html')

# Dictionary to store active search sessions
active_searches = {}

@app.route('/api/research-agent/search', methods=['POST'])
def research_agent_search():
    """Internet search endpoint for research agent"""
    try:
        data = request.json or {}
        session_id = data.get('session_id', f"search_{int(time.time())}")
        query = data.get('query', '').strip()
        max_results = data.get('max_results', 10)

        if not query:
            return jsonify({
                'status': 'error',
                'error': 'Search query is required'
            }), 400

        print(f"🔍 Research Agent Search: {query} (session: {session_id})")

        # Store search info for progress endpoint
        active_searches[session_id] = {
            'query': query,
            'max_results': max_results,
            'status': 'started'
        }

        return jsonify({
            'status': 'started',
            'session_id': session_id,
            'query': query,
            'message': f'Search started for: {query}'
        })

    except Exception as e:
        print(f"❌ Research agent search error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/research-agent/search-progress/<session_id>')
def research_agent_search_progress(session_id):
    """Get search progress for a session"""
    try:
        print(f"🔍 Checking search progress for session: {session_id}")

        # Get search info
        search_info = active_searches.get(session_id)
        if not search_info:
            return jsonify({
                'completed': True,
                'success': False,
                'error': 'Search session not found'
            })

        query = search_info['query']
        max_results = search_info.get('max_results', 10)

        # Use content_curator's search functionality
        search_results = curator.search_provider.search(query, max_results)

        # Format results for frontend
        formatted_results = []
        for result in search_results:
            formatted_results.append({
                'title': result.get('title', 'Untitled'),
                'summary': result.get('snippet', 'No summary available'),
                'url': result.get('url', '#'),
                'source': result.get('source', 'Internet Search'),
                'relevance_score': 0.8  # Default score
            })

        # Clean up completed search
        if session_id in active_searches:
            del active_searches[session_id]

        return jsonify({
            'completed': True,
            'success': True,
            'result': {
                'response': f'🔍 **Search Complete**\n\nFound {len(formatted_results)} results for "{query}".',
                'search_results': formatted_results
            }
        })

    except Exception as e:
        print(f"❌ Search progress error: {e}")
        # Clean up failed search
        if session_id in active_searches:
            del active_searches[session_id]
        return jsonify({
            'completed': True,
            'success': False,
            'error': str(e)
        })
# =============================================================================
# TRACK 1: CHAIN OPERATIONS ENDPOINTS
# Note: Chain endpoints are added via add_chain_operations_endpoints() above
# These include:
# - /api/chain/whats-going-on
# - /api/chain/week-review  
# - /api/chain/podcast
# - /api/chain/force-refresh
# - /api/chain/progress/<session_id>
# =============================================================================

# =============================================================================
# TRACK 2: ENHANCED CHAT ENDPOINTS
# Note: Enhanced chat endpoints are added via add_enhanced_chat_endpoints() above
# These include:
# - /api/chat/message
# - /api/chat/article-discussion
# - /api/chat/url-analysis
# The enhanced_chat_manager.py handles all /api/chat/* endpoints
# =============================================================================

@app.route('/api/frontend-messages')
def get_frontend_messages():
    """Get queued messages for frontend display"""
    global frontend_message_queue
    with frontend_message_lock:
        messages = list(frontend_message_queue)
        frontend_message_queue.clear()  # Clear after retrieval

    return jsonify({
        'success': True,
        'messages': messages
    })


@app.route('/api/download/<filename>')
def download_file(filename):
    """
    Enhanced file operations endpoint with directory routing:
    - GET /api/download/file.json -> serves file directly (auto-detects directory)
    - GET /api/download/file.json?source=podcasts -> serves from PCAST_DIR
    - GET /api/download/file.json?source=topics -> serves from TOPICS_DIR
    - GET /api/download/file.json?source=data -> serves from DATA_DIR
    - GET /api/download/file.json?check=true -> returns {"exists": true/false}
    - GET /api/download/file.json?read=true -> returns file contents as JSON
    """
    try:
        # Determine the correct directory
        source = request.args.get('source')

        if source == 'podcasts':
            base_dir = PCAST_DIR
        elif source == 'topics':
            base_dir = TOPICS_DIR
        elif source == 'data':
            base_dir = DATA_DIR
        else:
            # Auto-detect based on file type/name
            if filename.startswith('podcast_') and (filename.endswith('.mp3') or filename.endswith('.txt')):
                base_dir = PCAST_DIR
            elif (filename.startswith('current_events_analysis_') or filename.startswith(
                    'week_review_')) and filename.endswith('.json'):
                base_dir = TOPICS_DIR
            elif filename.endswith('_complete.flag'):
                base_dir = DATA_DIR
            elif filename.endswith('.db'):
                base_dir = DATA_DIR
            else:
                # Default to DATA_DIR for everything else
                base_dir = DATA_DIR

        file_path = os.path.join(base_dir, filename)

        # Check if file exists
        file_exists = os.path.exists(file_path)

        # Handle check parameter
        if request.args.get('check') == 'true':
            return jsonify({
                'exists': file_exists,
                'filename': filename,
                'directory': base_dir
            })

        # Return 404 if file doesn't exist for read/serve operations
        if not file_exists:
            return jsonify({'error': 'File not found', 'searched_in': base_dir}), 404

        # Handle read parameter
        if request.args.get('read') == 'true':
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    if filename.endswith('.json'):
                        content = json.load(f)
                    else:
                        content = f.read()

                return jsonify({
                    'success': True,
                    'filename': filename,
                    'content': content,
                    'directory': base_dir
                })
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid JSON file'}), 400
            except UnicodeDecodeError:
                return jsonify({'error': 'Cannot read binary file as text'}), 400

        # Default: serve file directly
        if filename.endswith('.json'):
            return send_file(file_path, mimetype='application/json')
        elif filename.endswith(('.mp3', '.wav')):
            return send_file(file_path, mimetype='audio/mpeg')
        elif filename.endswith(('.txt', '.md')):
            return send_file(file_path, mimetype='text/plain')
        else:
            return send_file(file_path)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# LINKEDIN NEWSLETTER ENDPOINT
# =============================================================================
@app.route('/api/linkedin-newsletter/generate', methods=['POST'])
def generate_linkedin_newsletter():
    """Generate LinkedIn newsletter - checks for existing first, supports force_new"""
    try:
        data = request.json or {}
        force_new = data.get('force_new', False)

        today = datetime.now().strftime("%Y%m%d")
        filename = f"linkedin_newsletter_{today}.txt"
        filepath = os.path.join(TOPICS_DIR, filename)

        # Check if already exists (unless force_new)
        if os.path.exists(filepath) and not force_new:
            return jsonify({'success': True, 'exists': True, 'filename': filename})

        # Import and run generator
        from linkedin_newsletter import generate_newsletter
        success, message = generate_newsletter()

        return jsonify({
            'success': success,
            'exists': False,
            'filename': filename if success else None,
            'message': message
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file upload from drag and drop"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        session_id = request.form.get('session_id', f"upload_{int(time.time())}")

        # Create file uploader and process
        file_uploader = create_file_uploader(curator, enhanced_chat_manager)
        result = file_uploader.process_uploaded_file(file, session_id)

        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'title': result['article_data'].get('title', 'Unknown'),
                'session_id': session_id,
                'article_data': result['article_data']
            })
        else:
            return jsonify(result), 400

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# CORE UTILITY ENDPOINTS (Simple Delegation)
# =============================================================================
@app.route('/api/progress')
def get_progress():
    """Legacy progress endpoint - redirect to chain progress if needed"""
    try:
        # Check if there's an active chain operation
        session_id = request.args.get('session_id')

        if session_id:
            # Use chain operations progress
            progress = chain_operations_manager.get_operation_progress(session_id)
            if progress and progress.get('status') != 'idle':
                return jsonify(progress)

        # Fall back to content curator progress
        progress = curator.get_operation_progress()

        if progress and progress.get('status') == 'running':
            if progress.get('total', 0) > 0:
                progress['percentage'] = round((progress['completed'] / progress['total']) * 100, 1)
            else:
                progress['percentage'] = 0

            elapsed = progress.get('elapsed_seconds', 0)
            mins, secs = divmod(elapsed, 60)
            progress['elapsed_formatted'] = f"{mins}m {secs}s"

        return jsonify(progress if progress else {'status': 'idle'})

    except Exception as e:
        print(f"❌ Progress endpoint error: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/hot-topics')
def get_hot_topics():
    """Get hot topics - simple file reading"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        files = glob.glob(os.path.join(TOPICS_DIR, f"current_events_analysis_{today}.json"))

        if not files:
            return jsonify({
                'status': 'no_analysis',
                'message': 'No current events analysis found for today',
                'keywords': [],
                'trending_topics': [],
                'last_analysis': None
            })

        latest_file = max(files, key=os.path.getctime)
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        analysis = data.get('analysis', {})
        
        return jsonify({
            'status': 'success',
            'keywords': analysis.get('search_keywords', [])[:15],
            'trending_topics': analysis.get('trending_topics', [])[:10],
            'summary': analysis.get('summary', ''),
            'last_analysis': analysis.get('analysis_timestamp'),
            'headline_count': data.get('headline_count', 0),
            'sources_analyzed': data.get('sources_analyzed', [])
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error loading hot topics: {str(e)}',
            'keywords': [],
            'trending_topics': []
        })

@app.route('/api/interests')
def get_interests():
    """Get user interests - simple database query"""
    try:
        conn = sqlite3.connect(curator.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM user_interests ORDER BY weight DESC')
        interests = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(interests)
    except Exception as e:
        return jsonify({'error': f'Error getting interests: {str(e)}'}), 500

@app.route('/api/interest-control', methods=['POST'])
def interest_control():
    """Handle interest management control actions"""
    try:
        data = request.get_json() or {}
        action = data.get('action')

        if action == 'query':
            # Get all interests for display
            result = interest_manager.get_all_interests()
            return jsonify({
                'success': True,
                'interests': result.get('interests', []) if isinstance(result, dict) else result
            })

        elif action == 'startIM':
            # Start interest management session
            session_id = f"interest_{int(time.time())}"
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'Interest management session started'
            })

        elif action == 'endIM':
            # End interest management session
            session_id = data.get('session_id')
            return jsonify({
                'success': True,
                'message': 'Interest management session ended'
            })

        else:
            return jsonify({
                'success': False,
                'error': f'Unknown action: {action}'
            }), 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/customer-control', methods=['POST'])
def customer_control():
    """Customer management control endpoint - handles all customer operations"""
    try:
        data = request.get_json() or {}
        action = data.get('action', '')

        if action == 'query':
            # Return current customers for display
            try:
                customers_df = customer_manager._load_customers_csv()
                if customers_df is None or len(customers_df) == 0:
                    return jsonify({
                        'success': True,
                        'customers': [],
                        'clusters': [],
                        'total_customers': 0
                    })

                # Group by cluster
                customers_by_cluster = customers_df.groupby('cluster')
                cluster_data = []

                for cluster_name, group in customers_by_cluster:
                    customers_in_cluster = []
                    for _, customer in group.iterrows():
                        customers_in_cluster.append({
                            'name': customer['customer_name'],
                            'reference': customer.get('reference 1', ''),
                            'cache_path': customer.get('cache_path', '')
                        })

                    cluster_data.append({
                        'cluster': cluster_name,
                        'count': len(group),
                        'customers': customers_in_cluster
                    })

                return jsonify({
                    'success': True,
                    'customers': cluster_data,
                    'clusters': [c['cluster'] for c in cluster_data],
                    'total_customers': len(customers_df)
                })

            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'Error loading customers: {str(e)}'
                })

        elif action == 'startCM':
            # Start customer management mode
            session_id = data.get('session_id', f'customer_{int(time.time())}')

            # Clean up any expired sessions
            customer_manager.cleanup_expired_sessions()

            return jsonify({
                'success': True,
                'session_id': session_id,
                'action': 'customer_mode_started',
                'response': 'Customer management mode activated. Use commands like "list customers", "add customer", or "help".'
            })

        elif action == 'process':
            # Process customer management command
            message = data.get('message', '').strip()
            session_id = data.get('session_id', f'customer_{int(time.time())}')

            if not message:
                return jsonify({
                    'success': False,
                    'error': 'Message is required'
                })

            # Process the command
            result = customer_manager.process_command_with_session(message, session_id)

            # Add session_id to response for frontend tracking
            result['session_id'] = session_id

            return jsonify(result)

        else:
            return jsonify({
                'success': False,
                'error': f'Unknown action: {action}'
            }), 400

    except Exception as e:
        print(f"❌ Customer control error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/interest-dialog', methods=['POST'])
def interest_dialog():
    """Handle interest management dialog commands"""
    try:
        data = request.get_json() or {}
        session_id = data.get('session_id')
        command = data.get('command', '').strip()

        if not session_id:
            return jsonify({
                'success': False,
                'error': 'Session ID required'
            }), 400

        if not command:
            return jsonify({
                'success': False,
                'error': 'Command required'
            }), 400

        # Process the command through the interest manager
        result = interest_manager.process_command(command, session_id)

        # Format the response
        response_data = {
            'success': result.get('success', False),
            'response': result.get('response', result.get('error', 'Unknown error'))
        }

        # Include interests list if available
        if 'interests' in result:
            response_data['interests'] = result['interests']

        return jsonify(response_data)

    except Exception as e:
        return jsonify({
            'success': False,
            'response': f'Error processing command: {str(e)}'
        }), 500

@app.route('/api/content')
def get_content():
    """Get content - delegate to content_curator"""
    try:
        hours = int(request.args.get('hours', 24))
        limit = int(request.args.get('limit', 20))
        min_relevance = float(request.args.get('min_relevance', 0.0))
        
        results = curator.get_recent_content(hours=hours, limit=limit, min_relevance=min_relevance)
        return jsonify(results)
    except Exception as e:
        return jsonify([]), 500

@app.route('/api/research-agent/all-articles', methods=['POST'])
def get_all_articles():
    """Get all articles - delegate to content_curator"""
    try:
        data = request.json or {}
        session_id = data.get('session_id', 'anonymous')  # Make optional
        limit = min(data.get('limit', 400), 2000)  # Cap at 2000
        search_keyword = data.get('search_keyword', '').strip()

        # Check database exists
        if not os.path.exists(curator.db_path):
            return jsonify({'success': False, 'error': 'Database not initialized'}), 500

        # Database query with better error handling
        try:
            conn = sqlite3.connect(curator.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if search_keyword:
                # Escape special characters for LIKE
                import re
                safe_keyword = re.escape(search_keyword)
                search_term = f'%{safe_keyword}%'

                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           COALESCE(starred, 0) as starred
                    FROM content 
                    WHERE LOWER(title) LIKE LOWER(?) 
                       OR LOWER(summary) LIKE LOWER(?) 
                       OR LOWER(source) LIKE LOWER(?)
                    ORDER BY total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', (search_term, search_term, search_term, limit))
            else:
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score,
                           COALESCE(starred, 0) as starred
                    FROM content 
                    ORDER BY total_relevance DESC, published_date DESC 
                    LIMIT ?
                ''', (limit,))

            articles = [dict(row) for row in cursor.fetchall()]

        except sqlite3.Error as e:
            return jsonify({'success': False, 'error': f'Database query failed: {str(e)}'}), 500
        finally:
            if conn:
                conn.close()

        return jsonify({
            'success': True,
            'session_id': session_id,
            'articles': articles,
            'total_found': len(articles),
            'limit_applied': limit,
            'search_keyword': search_keyword,
            'timestamp': time.time()  # Optional: for debugging
        })

    except Exception as e:
        print(f"❌ All-articles endpoint error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500

@app.route('/api/articles/toggle-star', methods=['POST'])
def toggle_article_star():
    """Toggle starred status of an article"""
    try:
        data = request.json or {}
        article_url = data.get('url')

        if not article_url:
            return jsonify({'success': False, 'error': 'URL required'}), 400

        conn = sqlite3.connect(curator.db_path)
        cursor = conn.cursor()

        # Toggle the starred value (0->1, 1->0)
        cursor.execute('''
            UPDATE content 
            SET starred = CASE WHEN starred = 1 THEN 0 ELSE 1 END 
            WHERE url = ?
        ''', (article_url,))

        # Get the new value to return to frontend
        cursor.execute('SELECT starred FROM content WHERE url = ?', (article_url,))
        result = cursor.fetchone()

        conn.commit()
        conn.close()

        new_starred_value = result[0] if result else 0

        return jsonify({
            'success': True,
            'starred': new_starred_value,
            'url': article_url
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# PDF ENDPOINTS (Conditional)
# =============================================================================

@app.route('/api/pdf/check-capabilities')
def check_pdf_capabilities():
    """Check PDF capabilities"""
    try:
        if HAS_PDF_SUPPORT:
            capabilities = get_pdf_capabilities()
            return jsonify({'available': True, 'capabilities': capabilities})
        else:
            return jsonify({'available': False, 'reason': 'PDF support not installed'})
    except Exception as e:
        return jsonify({'available': False, 'reason': str(e)})

# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# =============================================================================
# STARTUP VERIFICATION
# =============================================================================

def verify_architecture():
    """Verify the two-track architecture is properly set up"""
    print("\n🔍 Verifying Two-Track Architecture...")
    
    # Check Chain Operations Manager
    if hasattr(chain_operations_manager, 'start_whats_going_on'):
        print("   ✅ Chain Operations Manager: Ready")
    else:
        print("   ❌ Chain Operations Manager: Missing methods")
    
    # Check Enhanced Chat availability
    if HAS_ENHANCED_CHAT:
        print("   ✅ Enhanced Chat Manager: Available")
    else:
        print("   ⚠️ Enhanced Chat Manager: Not available (graceful degradation)")
    
    # Check PDF Support
    if HAS_PDF_SUPPORT:
        print("   ✅ PDF Support: Available")
    else:
        print("   ⚠️ PDF Support: Not available")

# =============================================================================
# LOGGING CONFIGURATION - SUPPRESS HTTP ACCESS LOGS
# =============================================================================

# Configure werkzeug logging to suppress HTTP access logs
import logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Alternatively, if you want to completely disable werkzeug logs:
# logging.getLogger('werkzeug').disabled = True

if __name__ == '__main__':
    verify_architecture()
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))