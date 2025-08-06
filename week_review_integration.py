"""
Week in Review Integration - Refactored for Chain Operations Manager
Focuses solely on consolidating daily analysis files into weekly summaries
Removed research assistant dependency, uses callback for progress updates
"""

import os
import json
import glob
import threading
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from pathlib import Path
from flask import request, jsonify



logger = logging.getLogger(__name__)

@dataclass
class WeekReviewProgress:
    """Progress tracking for week review"""
    session_id: str
    status: str  # 'starting', 'finding_files', 'parsing', 'consolidating', 'completed', 'error'
    current_message: str
    progress_percentage: int
    files_found: int
    files_processed: int
    start_time: float
    result_data: Optional[Dict] = None
    error_message: Optional[str] = None
    chain_context: bool = False


class WeekInReviewProcessor:
    """Self-contained week in review processor - consolidates daily analysis files"""
    
    def __init__(self, chat_assistant, research_assistant=None, data_directory="data"):
        """Initialize week review processor"""
        self.chat_assistant = chat_assistant
        # Keep research_assistant for legacy compatibility but don't use it for progress
        self.research_assistant = research_assistant
        self.data_directory = Path(data_directory)
        self.topics_directory = Path(os.environ.get('TOPICS_DIR', 'currentevents'))
        self.active_sessions = {}
        self.completed_sessions = {}
        self.frontend_callback = None
        self.data_directory.mkdir(exist_ok=True)
        
        print(f"📊 Week Review Processor initialized")
        print(f"   Data directory: {self.data_directory}")

    def set_frontend_callback(self, callback_func):
        """Set the frontend message callback for direct chat messaging"""
        self.frontend_callback = callback_func
        print("✅ Frontend callback connected to week_review")

    def check_existing_week_review(self) -> Optional[Dict]:
        """Check if week review already exists for today"""
        today = datetime.now().strftime('%Y-%m-%d')
        filename = f"week_review_{today}.json"
        filepath = self.topics_directory / filename

        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"✅ Found existing week review: {filepath}")
                    return data
            except Exception as e:
                print(f"❌ Error loading existing week review: {e}")
                return None

        return None

    def save_week_review_to_file(self, review_data: Dict) -> str:
        """Save week review data to JSON file in data directory"""
        try:
            # Generate filename using today's date
            today = datetime.now().strftime('%Y-%m-%d')
            filename = f"week_review_{today}.json"
            filepath = self.topics_directory / filename

            # Add metadata
            save_data = {
                **review_data,
                'generated_date': datetime.now().isoformat(),
                'analysis_end_date': today,
                'cache_version': '1.0',
                'filename': filename
            }

            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            print(f"✅ Week review saved to: {filepath}")
            return str(filepath)

        except Exception as e:
            print(f"❌ Error saving week review: {e}")
            raise e

    def start_week_review(self, session_id: str, chain_context: bool = False, 
                         progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Start week review analysis - check for existing file first"""
        
        print(f"🔍 Week Review Start:")
        print(f"  - session_id: '{session_id}'")
        print(f"  - chain_context: {chain_context}")
        print(f"  - progress_callback: {'Available' if progress_callback else 'None'}")

        try:
            if not session_id:
                session_id = f"week_review_{int(time.time())}"
                print(f"⚠️ No session ID provided, using generated: {session_id}")

            existing_review = self.check_existing_week_review()

            # Check for existing file first
            if existing_review:
                # File exists - handle completely and return (no background processing)
                print(f"✅ Found cached week review, using existing file")

                if chain_context:
                    # Create completed progress object for tracking
                    progress = WeekReviewProgress(
                        session_id=session_id,
                        status='completed',
                        current_message='Week review loaded from cache',
                        progress_percentage=100,
                        files_found=0,
                        files_processed=0,
                        start_time=time.time(),
                        chain_context=chain_context,
                        result_data=existing_review
                    )
                    self.completed_sessions[session_id] = progress

                    # Notify completion via callback if available
                    if progress_callback:
                        progress_callback(session_id, 100, 'Week review loaded from cache', 'completed')

                return {
                    'success': True,
                    'from_cache': True,
                    'message': 'Week review loaded from cache',
                    'result_data': existing_review,
                    'session_id': session_id
                }

            # STEP 2: No file exists - start background analysis
            print(f"📝 No cached file found, starting fresh analysis")

            # If no cache, proceed with generation
            progress = WeekReviewProgress(
                session_id=session_id,
                status='starting',
                current_message='Starting week in review analysis...',
                progress_percentage=0,
                files_found=0,
                files_processed=0,
                start_time=time.time(),
                chain_context=chain_context
            )
            self.active_sessions[session_id] = progress

            # Start analysis in background thread
            def run_analysis():
                try:
                    self._run_week_review_analysis(session_id, progress_callback)
                except Exception as e:
                    logger.error(f"Week review analysis failed: {e}")
                    self._update_progress(
                        session_id,
                        status='error',
                        error_message=str(e),
                        current_message=f'Analysis failed: {str(e)}',
                        progress_callback=progress_callback
                    )

            analysis_thread = threading.Thread(target=run_analysis, daemon=True)
            analysis_thread.start()

            return {
                'success': True,
                'status': 'started',
                'session_id': session_id,
                'message': 'Week in review analysis started'
            }

        except Exception as e:
            logger.error(f"Error starting week review: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _run_week_review_analysis(self, session_id: str, progress_callback: Optional[Callable] = None):
        """Run the complete week review analysis pipeline"""
        try:
            # Phase 1: Find recent analysis files
            self._update_progress(session_id,
                status='finding_files',
                current_message='Searching for current events analysis files...',
                progress_percentage=10,
                progress_callback=progress_callback
            )
            
            recent_files = self._find_recent_analysis_files()
            if not recent_files:
                raise Exception("No current events analysis files found for the past 7 days")
            
            self._update_progress(session_id,
                files_found=len(recent_files),
                current_message=f'Found {len(recent_files)} analysis files',
                progress_percentage=20,
                progress_callback=progress_callback
            )
            
            # Phase 2: Parse files
            self._update_progress(session_id,
                status='parsing',
                current_message='Parsing analysis files...',
                progress_percentage=30,
                progress_callback=progress_callback
            )
            
            daily_data = []
            for i, file_path in enumerate(recent_files):
                try:
                    data = self._parse_analysis_file(file_path)
                    if data:
                        daily_data.append(data)
                    
                    files_processed = i + 1
                    progress_pct = 30 + (files_processed / len(recent_files)) * 30  # 30-60%
                    self._update_progress(session_id,
                        files_processed=files_processed,
                        current_message=f'Processed {files_processed}/{len(recent_files)} files',
                        progress_percentage=int(progress_pct),
                        progress_callback=progress_callback
                    )
                    
                    time.sleep(0.1)  # Brief pause for UI updates
                    
                except Exception as e:
                    logger.warning(f"Error parsing {file_path}: {e}")
                    continue
            
            if not daily_data:
                raise Exception("Could not parse any analysis files")
            
            # Phase 3: Consolidate with LLM
            self._update_progress(session_id,
                status='consolidating',
                current_message='Using AI to consolidate keywords and trends...',
                progress_percentage=70,
                progress_callback=progress_callback
            )
            
            consolidated_data = self._consolidate_with_llm(daily_data)

            # Phase 4: Complete and save
            self._update_progress(session_id,
                status='completed',
                current_message='Week in review analysis completed',
                progress_percentage=100,
                result_data=consolidated_data,
                progress_callback=progress_callback
            )

            # Save to file
            try:
                self.save_week_review_to_file(consolidated_data)
                print(f"✅ Week review cached for future requests")
            except Exception as e:
                print(f"⚠️ Warning: Could not save week review cache: {e}")
                # Continue without caching

            # Handle chain context completion
            progress_obj = self.active_sessions.get(session_id)
            if progress_obj and getattr(progress_obj, 'chain_context', False):
                self._notify_chain_completion(session_id, consolidated_data, progress_callback)

            # Move to completed sessions
            if session_id in self.active_sessions:
                self.completed_sessions[session_id] = self.active_sessions[session_id]
                del self.active_sessions[session_id]

            print(f"🔍 DEBUG: _run_week_review_analysis COMPLETED for session {session_id}")

        except Exception as e:
            logger.error(f"Week review analysis error: {e}")
            self._update_progress(session_id,
                status='error',
                error_message=str(e),
                current_message=f'Analysis failed: {str(e)}',
                progress_callback=progress_callback
            )

    def _find_recent_analysis_files(self, days_back: int = 7) -> List[str]:
        """Find recent current events analysis files"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        # Look for files in data directory
        pattern = str(self.topics_directory / "current_events_analysis_*.json")
        all_files = glob.glob(pattern)
        
        recent_files = []
        for file_path in all_files:
            try:
                # Extract date from filename
                filename = os.path.basename(file_path)
                
                # Handle different filename formats
                if "current_events_analysis_" in filename:
                    date_part = filename.replace("current_events_analysis_", "").replace(".json", "")
                    
                    # Remove timestamp if present (e.g., 20241221_143022 -> 20241221)
                    if "_" in date_part:
                        date_part = date_part.split("_")[0]
                    
                    # Try different date formats
                    try:
                        if len(date_part) == 8:  # YYYYMMDD
                            file_date = datetime.strptime(date_part, "%Y%m%d")
                        else:  # YYYY-MM-DD
                            file_date = datetime.strptime(date_part, "%Y-%m-%d")
                        
                        if start_date <= file_date <= end_date:
                            recent_files.append(file_path)
                            
                    except ValueError:
                        logger.warning(f"Could not parse date from filename: {filename}")
                        continue
                        
            except Exception as e:
                logger.warning(f"Error processing file {file_path}: {e}")
                continue
        
        # Sort by modification time (most recent first)
        recent_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        
        print(f"📁 Found {len(recent_files)} recent analysis files")
        for file_path in recent_files[:3]:  # Show first 3
            print(f"   {os.path.basename(file_path)}")
        
        return recent_files
    
    def _parse_analysis_file(self, file_path: str) -> Optional[Dict]:
        """Parse a single analysis file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Extract date from filename
            filename = os.path.basename(file_path)
            date_part = filename.replace("current_events_analysis_", "").replace(".json", "")
            if "_" in date_part:
                date_part = date_part.split("_")[0]
            
            try:
                if len(date_part) == 8:
                    file_date = datetime.strptime(date_part, "%Y%m%d")
                else:
                    file_date = datetime.strptime(date_part, "%Y-%m-%d")
            except ValueError:
                file_date = datetime.now()
            
            # Extract data with flexible structure handling
            keywords = []
            trends = []
            summary = ""
            
            if isinstance(data, dict):
                # Handle different possible structures
                analysis = data.get('analysis', data)
                
                keywords = (analysis.get('search_keywords', []) or 
                           analysis.get('keywords', []) or 
                           analysis.get('key_topics', []) or [])
                
                trends = (analysis.get('trending_topics', []) or 
                         analysis.get('trends', []) or 
                         analysis.get('key_trends', []) or [])
                
                summary = (analysis.get('summary', '') or 
                          analysis.get('daily_summary', '') or 
                          analysis.get('analysis_summary', '') or '')
                
                # Extract titles from trending topics if they're objects
                if trends and isinstance(trends[0], dict):
                    trends = [topic.get('title', topic.get('name', str(topic))) for topic in trends]
            
            return {
                'date': file_date,
                'keywords': keywords[:20],  # Limit to prevent overwhelming
                'trends': trends[:15],
                'summary': summary,
                'filename': filename
            }
            
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            return None
    
    def _consolidate_with_llm(self, daily_data: List[Dict]) -> Dict[str, Any]:
        """Consolidate data using LLM through chat assistant"""
        
        # Collect all keywords and trends
        all_keywords = []
        all_trends = []
        all_summaries = []
        
        for day_data in daily_data:
            all_keywords.extend(day_data.get('keywords', []))
            all_trends.extend(day_data.get('trends', []))
            if day_data.get('summary'):
                date_str = day_data['date'].strftime('%Y-%m-%d')
                all_summaries.append(f"**{date_str}**: {day_data['summary']}")
        
        try:
            # Consolidate keywords
            keyword_prompt = f"""Analyze these keywords from 7 days of news analysis and group only very similar ones:

{json.dumps(all_keywords[:100], indent=2)}

Only group keywords that are essentially the same (e.g., "AI" and "artificial intelligence").
Keep most keywords separate to preserve diversity.
Return JSON format: {{"keyword": count, "another_keyword": count}}
Limit to top 30 most frequent.
"""
            
            keyword_response = self.chat_assistant.generate_response(keyword_prompt)
            keyword_counts = self._parse_llm_json_response(keyword_response, all_keywords)
            
            # Consolidate trends  
            trend_prompt = f"""Analyze these trends from 7 days of news and group similar ones with counts:

{json.dumps(all_trends[:100], indent=2)}

Group similar trends (e.g., "inflation concerns"+"rising prices"+"cost increases" = "inflation concerns").
Return only JSON format: {{"consolidated_trend": count, "another_trend": count}}
Focus on significant trends, limit to top 20.
"""
            
            trend_response = self.chat_assistant.generate_response(trend_prompt)
            trend_counts = self._parse_llm_json_response(trend_response, all_trends)
            
            # Generate weekly summary
            summary_prompt = f"""Create a comprehensive weekly news summary from these daily summaries:

{chr(10).join(all_summaries)}

Write 3-4 paragraphs covering:
1. Major breaking news and headlines
2. Economic and political developments  
3. Technology and social trends
4. Key implications and outlook

Be analytical and show connections between events.
"""
            
            weekly_summary = self.chat_assistant.generate_response(summary_prompt)
            
            # Calculate date range
            dates = [day['date'] for day in daily_data]
            start_date = min(dates)
            end_date = max(dates)
            date_range = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
            
            consolidated_result = {
                'keyword_counts': keyword_counts,
                'trend_counts': trend_counts,
                'weekly_summary': weekly_summary.strip(),
                'date_range': date_range,
                'total_days_analyzed': len(daily_data),
                'files_analyzed': [day['filename'] for day in daily_data]
            }
            
            print(f"✅ Week review consolidation completed:")
            print(f"   Keywords: {len(keyword_counts)}")
            print(f"   Trends: {len(trend_counts)}")
            print(f"   Days analyzed: {len(daily_data)}")
            
            return consolidated_result
            
        except Exception as e:
            logger.error(f"LLM consolidation error: {e}")
            # Fallback to simple counting
            return {
                'keyword_counts': self._simple_count(all_keywords),
                'trend_counts': self._simple_count(all_trends),
                'weekly_summary': f"Analysis of {len(daily_data)} days of current events from {daily_data[0]['date'].strftime('%Y-%m-%d')} to {daily_data[-1]['date'].strftime('%Y-%m-%d')}.",
                'date_range': f"{daily_data[0]['date'].strftime('%Y-%m-%d')} to {daily_data[-1]['date'].strftime('%Y-%m-%d')}",
                'total_days_analyzed': len(daily_data),
                'files_analyzed': [day['filename'] for day in daily_data]
            }
    
    def _parse_llm_json_response(self, response: str, fallback_items: List[str]) -> Dict[str, int]:
        """Parse LLM JSON response with fallback"""
        try:
            # Clean the response
            response = response.strip()
            
            # Remove markdown code blocks if present
            if response.startswith('```json'):
                response = response[7:-3]
            elif response.startswith('```'):
                response = response[3:-3]
            
            # Find JSON in the response
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                result = json.loads(json_str)
                
                # Validate and clean
                cleaned = {}
                for term, count in result.items():
                    if isinstance(count, (int, float)) and count > 0:
                        cleaned[term.strip()] = int(count)
                
                return cleaned
                
        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
        
        # Fallback to simple counting
        return self._simple_count(fallback_items)
    
    def _simple_count(self, items: List[str]) -> Dict[str, int]:
        """Simple fallback counting"""
        counts = {}
        for item in items:
            if item and isinstance(item, str):
                key = item.lower().strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        
        # Return top items
        sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return dict(sorted_items[:20])
    
    def _update_progress(self, session_id: str, progress_callback: Optional[Callable] = None, **kwargs):
        """
        Update progress for a session with optional callback to chain manager
        
        Args:
            session_id: Session identifier
            progress_callback: Optional callback function(session_id, percentage, message, status)
            **kwargs: Progress fields to update
        """
        if session_id in self.active_sessions:
            progress = self.active_sessions[session_id]
            for key, value in kwargs.items():
                if hasattr(progress, key):
                    setattr(progress, key, value)

            # Send progress update via callback if available (for chain context)
            if progress_callback:
                try:
                    current_message = getattr(progress, 'current_message', 'Processing...')
                    current_percentage = getattr(progress, 'progress_percentage', 0)
                    current_status = getattr(progress, 'status', 'running')
                    
                    # Call the chain manager's progress callback
                    progress_callback(session_id, current_percentage, current_message, current_status)
                    print(f"📊 Progress sent to chain manager: {current_percentage}% - {current_message}")
                    
                except Exception as e:
                    print(f"⚠️ Warning: Could not send progress to chain manager: {e}")

            # ALSO send progress to frontend chat if callback available
            if self.frontend_callback and 'current_message' in kwargs:
                try:
                    chat_message = f"📊 **Week Review:** {kwargs['current_message']}"
                    self.frontend_callback(chat_message, 'progress')
                except Exception as e:
                    print(f"⚠️ Warning: Could not send progress to frontend chat: {e}")
    
    def get_progress(self, session_id: str) -> Dict[str, Any]:
        """Get progress for a session"""
        # Check active sessions
        if session_id in self.active_sessions:
            progress = self.active_sessions[session_id]
            return {
                'exists': True,
                'status': progress.status,
                'current_message': progress.current_message,
                'progress_percentage': progress.progress_percentage,
                'files_found': progress.files_found,
                'files_processed': progress.files_processed,
                'is_completed': False,
                'has_error': progress.status == 'error',
                'error_message': progress.error_message,
                'elapsed_time': time.time() - progress.start_time
            }
        
        # Check completed sessions
        if session_id in self.completed_sessions:
            progress = self.completed_sessions[session_id]
            return {
                'exists': True,
                'status': progress.status,
                'current_message': progress.current_message,
                'progress_percentage': progress.progress_percentage,
                'files_found': progress.files_found,
                'files_processed': progress.files_processed,
                'is_completed': True,
                'has_error': progress.status == 'error',
                'error_message': progress.error_message,
                'result_data': progress.result_data,
                'elapsed_time': time.time() - progress.start_time
            }
        
        return {'exists': False}

    def _notify_chain_completion(self, session_id: str, week_review_data: Dict, 
                               progress_callback: Optional[Callable] = None):
        """
        Notify chain manager that week review completed as part of chain workflow
        
        Args:
            session_id: Session identifier  
            week_review_data: Completed week review data
            progress_callback: Optional callback to notify chain manager
        """
        print(f"🔗 Week review completed for chain session {session_id}")
        
        try:
            # Store the week review data in the session context for chain access
            if session_id in self.active_sessions:
                self.active_sessions[session_id].result_data = week_review_data
            
            # Notify chain manager via callback if available
            if progress_callback:
                progress_callback(session_id, 100, 'Week review analysis completed', 'completed')
                print(f"✅ Chain manager notified of week review completion")
            
            print(f"📊 Week review data available for chain continuation")
            
        except Exception as e:
            print(f"❌ Failed to notify chain of completion: {e}")


# Global instance - will be initialized in app.py
week_review_processor = None

def initialize_week_review(chat_assistant, research_assistant=None, data_directory="data"):
    """Initialize the week review processor"""
    global week_review_processor
    week_review_processor = WeekInReviewProcessor(chat_assistant, research_assistant, data_directory)
    return week_review_processor

def add_week_review_endpoints(app):
    """Add week review endpoints to Flask app"""
    
    @app.route('/api/week-in-review/start', methods=['POST'])
    def start_week_review():
        """Start week in review analysis"""
        try:
            if not week_review_processor:
                return jsonify({'success': False, 'error': 'Week review not initialized'}), 500

            data = request.get_json() or {}
            session_id = data.get('session_id', f"week_review_{int(time.time())}")
            chain_context = data.get('chain_context', False)

            # No progress_callback for direct API calls - only for chain operations
            result = week_review_processor.start_week_review(session_id, chain_context)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error starting week review: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/week-in-review/progress/<session_id>')
    def get_week_review_progress(session_id):
        """Get week review progress"""
        try:
            if not week_review_processor:
                return jsonify({'success': False, 'error': 'Week review not initialized'}), 500
            
            progress = week_review_processor.get_progress(session_id)
            return jsonify({'success': True, 'progress': progress})
            
        except Exception as e:
            logger.error(f"Error getting week review progress: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500


# Helper function for external initialization
def create_week_review_processor(chat_assistant, research_assistant=None, data_directory="data"):
    """Factory function to create a week review processor instance"""
    return WeekInReviewProcessor(chat_assistant, research_assistant, data_directory)