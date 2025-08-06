"""
Refactored Research Agent - Session Management and Enhanced Chat Coordination Only
Removes all chain workflow logic, routing, and complexity
Focuses on session coordination and enhanced chat integration per project_plan_restore.md
"""

import sqlite3
import json
import time
import os
import glob
from typing import Dict, Optional, Any
from contextlib import contextmanager
from datetime import datetime

# =============================================================================
# CONSTANTS AND CONFIGURATION
# =============================================================================

DATA_DIR = "data"
TOPICS_DIR = os.environ.get('TOPICS_DIR', 'currentevents')

# =============================================================================
# ENHANCED FEATURES IMPORT - GRACEFUL FALLBACK
# =============================================================================

# Enhanced chat integration - graceful import
HAS_ENHANCED_CHAT = False
try:
    from data_access_helper import ArticleCrossReferencer, HistoricalDataAccessor
    HAS_ENHANCED_CHAT = True
    print("✅ Enhanced chat features available in research agent")
except ImportError as e:
    HAS_ENHANCED_CHAT = False
    print(f"⚠️ Enhanced chat features not available: {e}")

# PDF support - graceful import
try:
    from pdf_parser import is_pdf_url, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

# =============================================================================
# SIMPLIFIED SESSION MANAGEMENT
# =============================================================================

class ResearchAgentSession:
    """Simplified session management - no chain state, only enhanced chat context"""
    
    def __init__(self, session_id: str, db_path: str):
        self.session_id = session_id
        self.db_path = db_path
        self.context = {}
        self.created_at = datetime.now()
        self.last_activity = datetime.now()

    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def get_context(self, key: str, default=None):
        """Get context value"""
        return self.context.get(key, default)
    
    def set_context(self, key: str, value):
        """Set context value"""
        self.context[key] = value
    
    def to_dict(self) -> Dict:
        """Convert session to dictionary"""
        return {
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat(),
            'last_activity': self.last_activity.isoformat(),
            'context': self.context,
        }

# =============================================================================
# MAIN RESEARCH ASSISTANT CLASS - SIMPLIFIED
# =============================================================================

class ResearchAssistant:
    """
    Simplified Research Assistant - Session Management and Enhanced Chat Only
    
    Responsibilities:
    - Session management and persistence
    - Enhanced chat coordination
    - URL/PDF analysis coordination
    - Database querying coordination
    
    NOT responsible for:
    - Chain workflows (delegated to ChainOperationsManager)
    - Message routing (delegated to EnhancedChatManager)
    - Complex intent detection (delegated to EnhancedChatManager)
    """
    
    def __init__(self, content_curator, chat_assistant, data_directory: str = DATA_DIR):
        self.content_curator = content_curator
        self.chat_assistant = chat_assistant
        self.data_directory = data_directory
        
        # Session management
        self.sessions = {}
        self.session_db_path = os.path.join(data_directory, "sessions.db")
        
        # Enhanced chat components (if available)
        self.enhanced_chat_available = HAS_ENHANCED_CHAT
        self.article_cross_referencer = None
        self.historical_data_accessor = None
        
        # Initialize session database
        self._initialize_session_db()
        
        # Initialize enhanced features if available
        if self.enhanced_chat_available:
            self._initialize_enhanced_features()
        
        print("🔧 Research Assistant initialized with simplified architecture")
        print(f"   Session Management: ✅")
        print(f"   Enhanced Chat: {'✅' if self.enhanced_chat_available else '❌'}")
        print(f"   PDF Support: {'✅' if HAS_PDF_SUPPORT else '❌'}")
    
    def _initialize_session_db(self):
        """Initialize session database"""
        try:
            with sqlite3.connect(self.session_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        created_at TEXT,
                        last_activity TEXT,
                        context TEXT
                    )
                """)
                conn.commit()
        except Exception as e:
            print(f"⚠️ Could not initialize session database: {e}")
    
    def _initialize_enhanced_features(self):
        """Initialize enhanced chat features if available"""
        if not self.enhanced_chat_available:
            return False
        
        try:
            # Initialize enhanced components
            self.article_cross_referencer = ArticleCrossReferencer(self.content_curator)

            self.historical_data_accessor = HistoricalDataAccessor(
                self.chat_assistant, self.data_directory
            )
            
            print("✅ Enhanced features initialized successfully")
            return True
            
        except Exception as e:
            print(f"⚠️ Could not initialize enhanced features: {e}")
            self.enhanced_chat_available = False
            return False
    
    # =============================================================================
    # SESSION MANAGEMENT METHODS
    # =============================================================================
    
    def get_or_create_session(self, session_id: str) -> ResearchAgentSession:
        """Get existing session or create new one"""

        from datetime import datetime
        today = datetime.now().date()
        expired_sessions = [sid for sid, session in self.sessions.items() if session.created_at.date() != today]
        for expired_id in expired_sessions: del self.sessions[expired_id]

        if session_id not in self.sessions:
            # Try to load from database
            session = self._load_session_from_db(session_id)
            if not session:
                # Create new session
                session = ResearchAgentSession(session_id, self.session_db_path)
            
            self.sessions[session_id] = session
        
        session = self.sessions[session_id]
        session.update_activity()
        return session
    
    def _load_session_from_db(self, session_id: str) -> Optional[ResearchAgentSession]:
        """Load session from database"""
        try:
            with sqlite3.connect(self.session_db_path) as conn:
                cursor = conn.execute(
                    "SELECT created_at, last_activity, context FROM sessions WHERE session_id = ?",
                    (session_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    session = ResearchAgentSession(session_id, self.session_db_path)
                    session.created_at = datetime.fromisoformat(row[0])
                    session.last_activity = datetime.fromisoformat(row[1])
                    session.context = json.loads(row[2]) if row[2] else {}
                    return session
                    
        except Exception as e:
            print(f"⚠️ Could not load session {session_id}: {e}")
        
        return None
    
    def save_session(self, session: ResearchAgentSession):
        """Save session to database"""
        try:
            with sqlite3.connect(self.session_db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (session_id, created_at, last_activity, context)
                    VALUES (?, ?, ?, ?)
                """, (
                    session.session_id,
                    session.created_at.isoformat(),
                    session.last_activity.isoformat(),
                    json.dumps(session.context)
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Could not save session {session.session_id}: {e}")

    # =============================================================================
    # ENHANCED CHAT COORDINATION METHODS
    # =============================================================================
    
    def process_chat_message(self, session_id: str, message: str, 
                           article_data: Optional[Dict] = None, streaming: bool = True):
        """
        Process chat message through enhanced chat manager
        This is the main entry point for all chat interactions
        """
        session = self.get_or_create_session(session_id)
        
        # If enhanced chat is available, use it
        if self.enhanced_chat_available and self.chat_assistant:
            try:
                if streaming:
                    # Use existing streaming response with session context
                    response_generator = self.chat_assistant.generate_streaming_response(
                        message,
                        session_context=session.context
                    )
                    session.update_activity()
                    self.save_session(session)
                    return response_generator
                else:
                    # Use existing response with session context
                    response = self.chat_assistant.generate_response(
                        message,
                        session_context=session.context
                    )
                    session.update_activity()
                    self.save_session(session)
                    return response

            except Exception as e:
                print(f"⚠️ Enhanced chat error, falling back to basic: {e}")

        # Fallback to basic chat processing
        return self._process_basic_chat(session, message, article_data, streaming)
        
        # Fallback to basic chat processing
        return self._process_basic_chat(session, message, article_data, streaming)
    
    def _process_basic_chat(self, session: ResearchAgentSession, message: str, 
                          article_data: Optional[Dict] = None, streaming: bool = True):
        """Fallback basic chat processing"""
        try:
            if self.chat_assistant:
                if streaming:
                    # Basic streaming response
                    def basic_response_generator():
                        response = self.chat_assistant.generate_response(message)
                        for word in response.split():
                            yield word + " "
                            time.sleep(0.05)  # Simulate streaming
                    
                    session.update_activity()
                    self.save_session(session)
                    return basic_response_generator()
                else:
                    # Basic synchronous response
                    response = self.chat_assistant.generate_response(message)
                    session.update_activity()
                    self.save_session(session)
                    return response
            else:
                # No chat assistant available
                fallback_response = "I'm currently in basic mode. Enhanced chat features are not available."
                if streaming:
                    def fallback_generator():
                        for word in fallback_response.split():
                            yield word + " "
                            time.sleep(0.05)
                    return fallback_generator()
                else:
                    return fallback_response
                    
        except Exception as e:
            print(f"⚠️ Basic chat error: {e}")
            raise

    # =============================================================================
    # SESSION STATUS AND ANALYTICS
    # =============================================================================
    
    def get_session_status(self, session_id: str) -> Dict:
        """Get session status and context"""
        session = self.get_or_create_session(session_id)
        return session.to_dict()

# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_research_agent_with_enhanced_features(content_curator, chat_assistant,
                                                 data_directory: str = DATA_DIR) -> ResearchAssistant:
    """Factory function to create a simplified research agent"""
    research_agent = ResearchAssistant(content_curator, chat_assistant, data_directory)
    
    print("✅ Simplified Research Agent created:")
    print(f"   Session Management: ✅")
    print(f"   Enhanced Chat: {'✅' if research_agent.enhanced_chat_available else '❌'}")
    print(f"   Architecture: Simplified Coordination Layer")
    
    return research_agent

# =============================================================================
# MODULE INITIALIZATION
# =============================================================================

if __name__ == "__main__":
    print("Simplified Research Agent Module - Session Management Only")
    print(f"Enhanced Chat Features: {'Available' if HAS_ENHANCED_CHAT else 'Not Available'}")
    print(f"PDF Support: {'Available' if HAS_PDF_SUPPORT else 'Not Available'}")
    print("🎯 Focus: Session management and enhanced chat coordination")
    print("🚫 Removed: Chain workflows, complex routing, intent detection")
    print("✅ Aligns with project_plan_restore.md and two-track architecture")