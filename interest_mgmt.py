"""
Interest Management Module for Content Curator
Handles user interest creation, modification, and deletion with LLM-powered natural language processing
Updated for API integration compatibility
"""

import sqlite3
import json
import ollama
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import re
import time


class InterestManager:
    """Manages user interests with LLM-powered natural language processing"""
    
    def __init__(self, db_path: str, ollama_model: str = None):
        self.db_path = db_path
        self.ollama_model = ollama_model or 'granite3.2:8b'
        self.init_database()
        # Session state for confirmation workflow
        self.pending_confirmations = {}  # session_id -> action_data
        self.confirmation_timeouts = {}  # session_id -> timestamp

    def init_database(self):
        """Initialize the user_interests table if it doesn't exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interest TEXT UNIQUE NOT NULL,
                weight REAL DEFAULT 1.0
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def is_interests_command(self, message: str) -> bool:
        """Check if message is an interest management command"""
        message_lower = message.lower().strip()
        
        # Keywords that indicate interest management
        interest_keywords = [
            'interest', 'interests', 'weight', 'add', 'remove', 'delete', 
            'show', 'list', 'change', 'update', 'set', 'my interests',
            'help', 'machine learning', 'artificial intelligence', 'ai',
            'technology', 'climate', 'science', 'research'
        ]
        
        # Check for any interest-related keywords
        for keyword in interest_keywords:
            if keyword in message_lower:
                return True
        
        # Check for weight-related patterns
        weight_patterns = [
            r'weight\s+\d+',
            r'with\s+weight',
            r'to\s+\d+',
            r'\d+\.\d+',
            r'weight\s+of'
        ]
        
        for pattern in weight_patterns:
            if re.search(pattern, message_lower):
                return True
        
        return False

    def process_command_with_session(self, command: str, session_id: str) -> Dict:
        """
        Process command with session-based confirmation tracking

        Handles the two-step confirmation workflow needed by the API endpoints.
        """
        # Check if this is a confirmation response for a pending action
        if session_id in self.pending_confirmations:
            confirmation_result = handle_confirmation_response(command)

            if confirmation_result['confirmed']:
                # Execute the pending action
                action_data = self.pending_confirmations.pop(session_id)
                self.confirmation_timeouts.pop(session_id, None)
                return self.execute_interest_action(action_data)

            elif confirmation_result['cancelled']:
                # Cancel the pending action
                self.pending_confirmations.pop(session_id, None)
                self.confirmation_timeouts.pop(session_id, None)
                return {
                    'success': True,
                    'action': 'cancelled',
                    'response': confirmation_result['response']
                }

            else:
                # Unclear response - ask again
                return {
                    'success': False,
                    'action': 'confirmation_needed',
                    'response': confirmation_result['response']
                }

        # Process new command
        result = self.process_command(command)

        # If confirmation is needed, store the action data
        if result.get('action') == 'confirmation_needed':
            self.pending_confirmations[session_id] = result.get('action_data', {})
            self.confirmation_timeouts[session_id] = time.time()

        return result

    def parse_interest_command(self, message: str) -> Dict:
        """Parse interest command using LLM and return structured data"""
        try:
            prompt = f"""
Parse this user message as an interest management command. Return ONLY a valid JSON object with these fields:

{{
    "action": "show|add|remove|update_weight|help|error",
    "interest": "name of interest (if applicable)",
    "weight": 5.0,
    "confirmation": "confirmation message for user"
}}

Examples:
- "Show my interests" → {{"action": "show", "confirmation": "Showing all interests"}}
- "Add AI with weight 8.5" → {{"action": "add", "interest": "artificial intelligence", "weight": 8.5, "confirmation": "Add 'artificial intelligence' with weight 8.5?"}}
- "Remove tech interest" → {{"action": "remove", "interest": "technology", "confirmation": "Remove 'technology' interest?"}}
- "Change ML weight to 9" → {{"action": "update_weight", "interest": "machine learning", "weight": 9.0, "confirmation": "Change 'machine learning' weight to 9.0?"}}
- "Help" → {{"action": "help", "confirmation": "Showing help information"}}

User message: "{message}"

Return only the JSON object:"""

            try:
                response = ollama.chat(
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': prompt}],
                    options={'temperature': 0.1}
                )
                
                response_text = response['message']['content'].strip()
                
                # Try to extract JSON from response
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                
                if json_start >= 0 and json_end > json_start:
                    json_text = response_text[json_start:json_end]
                    parsed = json.loads(json_text)
                    
                    # Validate required fields
                    if 'action' not in parsed:
                        raise ValueError("Missing 'action' field")
                    
                    # Set default values
                    parsed.setdefault('interest', '')
                    parsed.setdefault('weight', 5.0)
                    parsed.setdefault('confirmation', 'Confirm this action?')
                    
                    # Validate weight range
                    if parsed.get('weight') and not (0.1 <= float(parsed['weight']) <= 10.0):
                        return {
                            'action': 'error',
                            'error': 'Weight must be between 0.1 and 10.0'
                        }
                    
                    return parsed
                
                else:
                    raise ValueError("No valid JSON found in response")
                    
            except Exception as llm_error:
                print(f"LLM parsing error: {llm_error}")
                # Fallback to simple pattern matching
                return self._fallback_parse(message)
                
        except Exception as e:
            return {
                'action': 'error',
                'error': f'Command parsing failed: {str(e)}'
            }
    
    def _fallback_parse(self, message: str) -> Dict:
        """Fallback parsing using simple patterns"""
        message_lower = message.lower().strip()
        
        # Help command
        if 'help' in message_lower:
            return {'action': 'help', 'confirmation': 'Showing help information'}
        
        # Show command
        if any(phrase in message_lower for phrase in ['show', 'list', 'my interests', 'what are']):
            return {'action': 'show', 'confirmation': 'Showing all interests'}
        
        # Add command
        add_patterns = [
            r'add\s+(.+?)\s+(?:with\s+)?weight\s+([0-9.]+)',
            r'new\s+interest:?\s*(.+?),?\s+weight\s+([0-9.]+)',
            r'add\s+(.+?)\s+weight\s+([0-9.]+)'
        ]
        
        for pattern in add_patterns:
            match = re.search(pattern, message_lower)
            if match:
                interest = match.group(1).strip()
                weight = float(match.group(2))
                return {
                    'action': 'add',
                    'interest': interest,
                    'weight': weight,
                    'confirmation': f"Add '{interest}' with weight {weight}?"
                }
        
        # Remove command
        remove_patterns = [
            r'(?:remove|delete)\s+(.+?)\s+interest',
            r'(?:remove|delete)\s+(.+)'
        ]
        
        for pattern in remove_patterns:
            match = re.search(pattern, message_lower)
            if match:
                interest = match.group(1).strip()
                return {
                    'action': 'remove',
                    'interest': interest,
                    'confirmation': f"Remove '{interest}' interest?"
                }
        
        # Update weight command
        weight_patterns = [
            r'(?:change|update|set)\s+(?:weight\s+of\s+)?(.+?)\s+(?:weight\s+)?to\s+([0-9.]+)',
            r'set\s+(.+?)\s+weight\s+([0-9.]+)'
        ]
        
        for pattern in weight_patterns:
            match = re.search(pattern, message_lower)
            if match:
                interest = match.group(1).strip()
                weight = float(match.group(2))
                return {
                    'action': 'update_weight',
                    'interest': interest,
                    'weight': weight,
                    'confirmation': f"Change '{interest}' weight to {weight}?"
                }
        
        return {
            'action': 'error',
            'error': 'Could not understand command. Try: "show interests", "add AI weight 8", "remove tech"'
        }
    
    def execute_interest_action(self, action_data: Dict) -> Dict:
        """Execute an interest action and return result"""
        action = action_data.get('action', '')
        
        if action == 'show':
            interests = self.get_all_interests()
            if isinstance(interests, list):
                return {
                    'success': True,
                    'action': 'show',
                    'response': self._format_interests_display(interests),
                    'interests': interests
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to retrieve interests'
                }
        
        elif action == 'help':
            return {
                'success': True,
                'action': 'help',
                'response': self.get_interests_help()
            }
        
        elif action == 'add':
            return self._add_interest(action_data['interest'], action_data['weight'])
        
        elif action == 'remove':
            return self._remove_interest(action_data['interest'])
        
        elif action == 'update_weight':
            return self._update_interest_weight(action_data['interest'], action_data['weight'])
        
        else:
            return {
                'success': False,
                'error': f'Unknown action: {action}'
            }
    
    def _add_interest(self, interest: str, weight: float) -> Dict:
        """Add or update an interest"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if interest already exists (case-insensitive)
            cursor.execute('SELECT id, weight FROM user_interests WHERE LOWER(interest) = LOWER(?)', (interest,))
            existing = cursor.fetchone()
            
            if existing:
                # Update existing interest
                cursor.execute('''
                    UPDATE user_interests 
                    SET weight = ?
                    WHERE id = ?
                ''', (weight, existing[0]))
                
                old_weight = existing[1]
                action_type = "updated"
                
            else:
                # Add new interest
                cursor.execute('''
                    INSERT INTO user_interests (interest, weight) 
                    VALUES (?, ?)
                ''', (interest, weight))
                
                old_weight = None
                action_type = "added"
            
            conn.commit()
            conn.close()
            
            # Format response
            weight_bar = "🟦" * min(int(weight), 10) + "⬜" * (10 - min(int(weight), 10))
            
            if action_type == "updated":
                response = f"✅ **Interest Updated**\n\n**{interest}** weight changed from {old_weight:.1f} to {weight:.1f}\n{weight_bar}"
            else:
                response = f"✅ **Interest Added**\n\n**{interest}** with weight {weight:.1f}\n{weight_bar}"
            
            return {
                'success': True,
                'action': 'add',
                'response': response,
                'interest': interest,
                'weight': weight,
                'action_type': action_type
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Database error: {str(e)}'
            }
    
    def _remove_interest(self, interest: str) -> Dict:
        """Remove an interest"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if interest exists (case-insensitive)
            cursor.execute('SELECT id, interest FROM user_interests WHERE LOWER(interest) = LOWER(?)', (interest,))
            existing = cursor.fetchone()
            
            if not existing:
                conn.close()
                return {
                    'success': False,
                    'error': f"Interest '{interest}' not found"
                }
            
            actual_name = existing[1]
            
            # Remove interest
            cursor.execute('DELETE FROM user_interests WHERE id = ?', (existing[0],))
            conn.commit()
            conn.close()
            
            response = f"✅ **Interest Removed**\n\n**{actual_name}** has been removed from your interests."
            
            return {
                'success': True,
                'action': 'remove',
                'response': response,
                'interest': actual_name
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Database error: {str(e)}'
            }
    
    def _update_interest_weight(self, interest: str, weight: float) -> Dict:
        """Update interest weight"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if interest exists
            cursor.execute('SELECT weight FROM user_interests WHERE LOWER(interest) = LOWER(?)', (interest,))
            current = cursor.fetchone()
            
            if not current:
                conn.close()
                return {
                    'success': False,
                    'error': f"Interest '{interest}' not found"
                }
            
            old_weight = current[0]
            
            # Update weight
            cursor.execute('''
                UPDATE user_interests 
                SET weight = ?
                WHERE LOWER(interest) = LOWER(?)
            ''', (weight, interest))
            
            conn.commit()
            conn.close()
            
            weight_bar = "🟦" * min(int(weight), 10) + "⬜" * (10 - min(int(weight), 10))
            response = f"✅ **Weight Updated**\n\n**{interest}** weight changed from {old_weight:.1f} to {weight:.1f}\n{weight_bar}"
            
            return {
                'success': True,
                'action': 'update_weight',
                'response': response,
                'interest': interest,
                'old_weight': old_weight,
                'new_weight': weight
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Database error: {str(e)}'
            }
    
    def get_all_interests(self) -> List[Dict]:
        """Get all user interests"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT interest, weight 
                FROM user_interests 
                ORDER BY weight DESC, interest ASC
            ''')
            
            rows = cursor.fetchall()
            conn.close()

            interests = []
            for row in rows:
                interests.append({
                    'interest': row[0],
                    'weight': row[1]
                })

            return interests
            
        except Exception as e:
            print(f"Error getting interests: {e}")
            return {'error': f'Database error: {str(e)}'}

    def process_command(self, command: str, session_id: str = None) -> Dict:
        """
        Process a natural language interest command with optional session management

        Args:
            command: The user's command text
            session_id: Optional session ID for confirmation workflow

        Returns:
            Dict with response data for the API endpoints
        """
        if session_id:
            # Use session-based processing for confirmation workflow
            return self.process_command_with_session(command, session_id)
        else:
            # Use simple processing without confirmation state
            return process_interest_command(self, command)

    def _format_interests_display(self, interests: List[Dict]) -> str:
        """Format interests for display in chat"""
        if not interests:
            return "📋 **Current Research Interests**\n\n🔍 No interests configured yet\n\nAdd interests to improve article relevance scoring!"
        
        response = "📋 **Current Research Interests**\n\n"
        
        for interest in interests:
            weight = interest['weight']
            weight_bar = "🟦" * min(int(weight), 10) + "⬜" * (10 - min(int(weight), 10))
            
            # Determine category
            if weight <= 3:
                category = "Low"
                category_emoji = "🔵"
            elif weight <= 6:
                category = "Medium"
                category_emoji = "🟡"
            else:
                category = "High"
                category_emoji = "🟢"
            
            response += f"**{interest['interest']}** - Weight: {weight:.1f} {category_emoji}\n"
            response += f"{weight_bar} ({category} Priority)\n\n"
        return response
    
    def get_interests_for_scoring(self) -> List[Tuple[str, float]]:
        """Get interests as list of tuples for content scoring"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT interest, weight FROM user_interests ORDER BY weight DESC')
            interests = cursor.fetchall()
            conn.close()
            
            return interests
            
        except Exception as e:
            print(f"Error getting interests for scoring: {e}")
            return []
    
    def get_interests_summary(self) -> Dict:
        """Get a summary of user interests for API responses"""
        interests = self.get_all_interests()
        
        if isinstance(interests, dict) and 'error' in interests:
            return interests
        
        if not interests:
            return {
                'total_count': 0,
                'average_weight': 0.0,
                'top_interests': [],
                'weight_distribution': {'low': 0, 'medium': 0, 'high': 0}
            }
        
        total_weight = sum(i['weight'] for i in interests)
        average_weight = total_weight / len(interests)
        
        # Weight distribution
        distribution = {'low': 0, 'medium': 0, 'high': 0}
        for interest in interests:
            weight = interest['weight']
            if weight <= 3:
                distribution['low'] += 1
            elif weight <= 6:
                distribution['medium'] += 1
            else:
                distribution['high'] += 1
        
        return {
            'total_count': len(interests),
            'average_weight': round(average_weight, 1),
            'top_interests': interests[:5],  # Top 5 by weight
            'weight_distribution': distribution
        }
    
    def get_interests_help(self) -> str:
        """Get help text for interest management commands"""
        return """📋 **Interest Management Help**

**View Interests:**
• "Show my interests" - List all configured interests
• "What are my interests?" - Same as above

**Add Interests:**
• "Add artificial intelligence with weight 8.5"
• "Add technology weight 7.0" 
• "New interest: climate change, weight 9"

**Modify Weights:**
• "Change weight of technology to 6.5"
• "Set AI weight to 9.0"
• "Update artificial intelligence weight to 8"

**Remove Interests:**
• "Remove technology interest"
• "Delete AI interest"

Interests help me score articles - higher weights mean matching content gets ranked higher in your results!"""


    def cleanup_expired_sessions(self, timeout_seconds: int = 300) -> None:
        """
        Clean up expired confirmation sessions (default: 5 minutes)

        Call this periodically to prevent memory leaks from abandoned sessions.
        """
        current_time = time.time()
        expired_sessions = [
            session_id for session_id, timestamp in self.confirmation_timeouts.items()
            if current_time - timestamp > timeout_seconds
        ]

        for session_id in expired_sessions:
            self.pending_confirmations.pop(session_id, None)
            self.confirmation_timeouts.pop(session_id, None)

        if expired_sessions:
            print(f"🧹 Cleaned up {len(expired_sessions)} expired interest management sessions")

# API-compatible functions for external use
def create_interest_manager(db_path: str, ollama_model: str = None) -> InterestManager:
    """Factory function to create an InterestManager instance"""
    return InterestManager(db_path, ollama_model or 'granite3.2:8b')


def process_interest_command(manager: InterestManager, message: str) -> Dict:
    """
    Process a natural language interest command
    
    Returns:
        Dict with structure:
        {
            'success': bool,
            'action': str,  # 'show', 'add', 'remove', 'update_weight', 'help', 'error'
            'response': str,  # Human-readable response
            'interests': list,  # Current interests (for show action)
            'error': str  # Error message if failed
        }
    """
    # First check if it's an interest command
    if not manager.is_interests_command(message):
        return {
            'success': False,
            'action': 'not_interest_command',
            'error': 'Message is not an interest management command',
            'response': "❓ This doesn't look like an interest management command. Try:\n• 'Show my interests'\n• 'Add AI with weight 8'\n• 'Help with interests'"
        }
    
    # Parse the command using LLM
    parsed = manager.parse_interest_command(message)
    
    if parsed.get('action') == 'error':
        return {
            'success': False,
            'action': 'error',
            'error': parsed.get('error', 'Unknown parsing error'),
            'response': "❌ **Couldn't understand the request**\n\nPlease try commands like:\n• 'Show my interests'\n• 'Add technology with weight 8'\n• 'Remove AI interest'\n• 'Change weight of climate to 9'"
        }
    
    # Execute the action directly (no confirmation needed for API integration)
    result = manager.execute_interest_action(parsed)
    return result


def execute_confirmed_action(manager: InterestManager, action_data: Dict) -> Dict:
    """Execute a previously confirmed interest action"""
    return manager.execute_interest_action(action_data)


def handle_confirmation_response(response: str) -> Dict:
    """
    Handle yes/no confirmation response
    
    Returns:
        {
            'confirmed': bool,
            'cancelled': bool,
            'unclear': bool,
            'response': str
        }
    """
    response_lower = response.lower().strip()
    
    if response_lower in ['yes', 'y', 'confirm', 'ok', 'proceed', 'sure', 'yep', 'yeah']:
        return {
            'confirmed': True,
            'cancelled': False,
            'unclear': False,
            'response': 'Action confirmed'
        }
    elif response_lower in ['no', 'n', 'cancel', 'abort', 'nope', 'nah']:
        return {
            'confirmed': False,
            'cancelled': True,
            'unclear': False,
            'response': '✅ **Action cancelled**\n\nWhat else can I help you with?'
        }
    else:
        return {
            'confirmed': False,
            'cancelled': False,
            'unclear': True,
            'response': '🤔 **Please confirm**\n\nSay "yes" to proceed or "no" to cancel the interests action.'
        }