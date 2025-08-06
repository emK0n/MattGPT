"""
Refactored Chain Operations Manager - Direct Orchestration Implementation
FIXED: Corrected all self.sessions references to use self.active_chains
Removes research assistant dependency and implements self-contained chain workflows
Adheres to two-track architecture with proper separation of concerns
"""

import os
import json
import time
import threading
import glob
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

# =============================================================================
# CHAIN OPERATION RESULT CLASSES
# =============================================================================

@dataclass
class ChainOperationResult:
    """Standardized result from chain operations"""
    success: bool
    operation: str
    session_id: str
    response_message: str
    actions: List[Dict] = None
    show_modal: bool = False
    show_progress: bool = False
    result_data: Dict = None
    error_message: str = None
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class ChainProgress:
    """Chain progress tracking"""
    session_id: str
    operation: str
    current_step: str
    steps_completed: List[str]
    total_steps: int
    progress_percentage: int
    current_message: str
    start_time: float
    error_message: str = None
    result_data: Dict = None
    force_refresh: bool = False

# =============================================================================
# SELF-CONTAINED CHAIN OPERATIONS MANAGER
# =============================================================================

class ChainOperationsManager:
    """
    Self-contained chain orchestrator - no research assistant dependency
    Directly manages all chain workflows using individual module APIs
    """
    
    def __init__(self, content_curator, current_events_module, week_review_processor, 
                 podcast_generator):
        """Initialize with direct module access only - no research assistant"""
        # Direct module references for orchestration
        self.content_curator = content_curator
        self.current_events = current_events_module  
        self.week_review_processor = week_review_processor
        self.podcast_generator = podcast_generator
        
        # Chain state tracking
        self.active_chains = {}
        self.chain_history = {}
        self.monitoring_threads = {}
        
        # Data directory for file operations
        self.data_directory = os.environ.get('DATA_DIR', 'data')
        self.topics_directory = os.environ.get('TOPICS_DIR', 'currentevents')
        
        print("🔗 Chain Operations Manager initialized with direct orchestration")
        print(f"   Content Curator: {'✅' if content_curator else '❌'}")
        print(f"   Current Events: {'✅' if current_events_module else '❌'}")
        print(f"   Week Review: {'✅' if week_review_processor else '❌'}")
        print(f"   Podcast Generator: {'✅' if podcast_generator else '❌'}")
        print("   🎯 Self-contained - no research assistant dependency")
        
    # =============================================================================
    # MAIN ENTRY POINTS - DIRECT ORCHESTRATION
    # =============================================================================
    
    def start_whats_going_on(self, session_id: str, force_refresh: bool = False) -> ChainOperationResult:
        """
        What's Going On button handler - direct orchestration
        FIXED: Corrected session cleanup to use self.active_chains
        """
        try:
            print(f"🚀 Starting What's Going On - session: {session_id}, force: {force_refresh}")
            
            # Clean up expired chains from previous days (FIXED)
            today = datetime.now().date()
            expired_chains = [sid for sid, chain in self.active_chains.items() 
                             if hasattr(chain, 'start_time') and datetime.fromtimestamp(chain.start_time).date() != today]
            for expired_id in expired_chains: 
                del self.active_chains[expired_id]
                print(f"🧹 Cleaned up expired chain: {expired_id}")
            
            # Step 1: Check flag file for existing results
            if not force_refresh and self._check_analysis_flag():
                print("✅ Existing results found, showing existing results flow")
                return self._show_existing_results_flow(session_id)
            
            # Step 2: Start fresh analysis chain with direct orchestration
            print("🔄 No existing results or force refresh - starting fresh analysis chain")
            return self._start_fresh_analysis_chain(session_id, force_refresh)
            
        except Exception as e:
            print(f"❌ Error in start_whats_going_on: {e}")
            return ChainOperationResult(
                success=False,
                operation="whats_going_on",
                session_id=session_id,
                response_message=f"❌ **Error Starting Analysis**\n\n{str(e)}",
                error_message=str(e)
            )
    
    def start_week_review(self, session_id: str) -> ChainOperationResult:
        """
        Week Review button handler - direct week review processor usage
        FIXED: Corrected session cleanup to use self.active_chains
        """
        try:
            print(f"📊 Starting Week Review - session: {session_id}")
            
            # Clean up expired chains from previous days (FIXED)
            today = datetime.now().date()
            expired_chains = [sid for sid, chain in self.active_chains.items() 
                             if hasattr(chain, 'start_time') and datetime.fromtimestamp(chain.start_time).date() != today]
            for expired_id in expired_chains: 
                del self.active_chains[expired_id]
                print(f"🧹 Cleaned up expired chain: {expired_id}")
            
            # Check for existing review first
            existing_review = self.week_review_processor.check_existing_week_review()
            
            if existing_review:
                print("✅ Found existing week review, showing cached results")
                return ChainOperationResult(
                    success=True,
                    operation="week_review",
                    session_id=session_id,
                    response_message=self._format_existing_week_review_message(existing_review),
                    actions=[{'type': 'show_week_review_modal', 'data': existing_review}],
                    show_modal=True,
                    result_data=existing_review
                )
            
            # Start fresh generation - direct processor usage
            print("🔄 No existing week review found, starting fresh generation")
            result = self.week_review_processor.start_week_review(session_id, chain_context=False)
            
            if result['success']:
                print("✅ Week review generation started successfully")
                return ChainOperationResult(
                    success=True,
                    operation="week_review",
                    session_id=session_id,
                    response_message="📊 **Starting Week Review Analysis**\n\nAnalyzing past 7 days of current events...",
                    show_progress=True
                )
            else:
                print(f"❌ Week review generation failed: {result.get('error')}")
                return ChainOperationResult(
                    success=False,
                    operation="week_review", 
                    session_id=session_id,
                    response_message=f"❌ **Week Review Error**\n\n{result.get('error', 'Unknown error')}",
                    error_message=result.get('error')
                )
                
        except Exception as e:
            print(f"❌ Error in start_week_review: {e}")
            return ChainOperationResult(
                success=False,
                operation="week_review",
                session_id=session_id,
                response_message=f"❌ **Week Review Error**\n\n{str(e)}",
                error_message=str(e)
            )
    
    def start_daily_podcast(self, session_id: str, force_new: bool = False) -> ChainOperationResult:
        """
        Daily Podcast button handler - direct podcast generator usage
        FIXED: Corrected session cleanup to use self.active_chains
        """
        try:
            print(f"🎙️ Starting Daily Podcast - session: {session_id}")
            
            # Clean up expired chains from previous days (FIXED)
            today = datetime.now().date()
            expired_chains = [sid for sid, chain in self.active_chains.items() 
                             if hasattr(chain, 'start_time') and datetime.fromtimestamp(chain.start_time).date() != today]
            for expired_id in expired_chains: 
                del self.active_chains[expired_id]
                print(f"🧹 Cleaned up expired chain: {expired_id}")

            # Skip existing file check if force_new is True
            if not force_new:
                # Check for existing files first
                today_str = datetime.now().strftime('%Y%m%d')
                existing_files = self.podcast_generator._check_existing_podcast_files(today_str)

                if existing_files:
                    print("✅ Found existing podcast files, showing cached results")
                    return ChainOperationResult(
                        success=True,
                        operation="daily_podcast",
                        session_id=session_id,
                        response_message=self._format_existing_podcast_message(existing_files),
                        result_data=existing_files
                    )
            
            # Start fresh generation - direct generator usage
            print("🔄 No existing podcast found, starting fresh generation")
            result = self.podcast_generator.start_podcast_generation(session_id, chain_context=False)
            
            if result['status'] in ['started', 'completed']:
                print("✅ Podcast generation started successfully")
                return ChainOperationResult(
                    success=True,
                    operation="daily_podcast",
                    session_id=session_id,
                    response_message="🎙️ Creating new podcast from current events...",
                    show_progress=True
                )
            else:
                print(f"❌ Podcast generation failed: {result.get('message')}")
                return ChainOperationResult(
                    success=False,
                    operation="daily_podcast",
                    session_id=session_id, 
                    response_message=f"❌ **Podcast Error**\n\n{result.get('message', 'Unknown error')}",
                    error_message=result.get('message')
                )
                
        except Exception as e:
            print(f"❌ Error in start_daily_podcast: {e}")
            return ChainOperationResult(
                success=False,
                operation="daily_podcast",
                session_id=session_id,
                response_message=f"❌ **Podcast Error**\n\n{str(e)}",
                error_message=str(e)
            )
    
    def start_force_refresh_content(self, session_id: str, timeframe_hours: int = 48) -> ChainOperationResult:
        """Force refresh content - simplified collection and rescoring"""
        try:
            print(f"🔄 Starting Force Refresh - session: {session_id}, timeframe: {timeframe_hours}h")
            
            # Start simplified refresh: collection → rescore only
            return self._start_refresh_chain(session_id, timeframe_hours)
            
        except Exception as e:
            print(f"❌ Error in start_force_refresh_content: {e}")
            return ChainOperationResult(
                success=False,
                operation="force_refresh",
                session_id=session_id,
                response_message=f"❌ **Force Refresh Error**\n\n{str(e)}",
                error_message=str(e)
            )
    
    # =============================================================================
    # DIRECT CHAIN WORKFLOW ORCHESTRATION
    # =============================================================================
    
    def _start_fresh_analysis_chain(self, session_id: str, force_refresh: bool = False) -> ChainOperationResult:
        """Start fresh analysis chain with direct orchestration - no research assistant"""
        try:
            print(f"🚀 Starting fresh analysis chain - session: {session_id}, force: {force_refresh}")
            
            # Create flag file for this analysis
            self._create_analysis_flag()
            
            # Initialize chain progress tracking
            self.active_chains[session_id] = ChainProgress(
                session_id=session_id,
                operation="whats_going_on",
                current_step="hot_topics",
                steps_completed=[],
                total_steps=6 if force_refresh else 6,
                progress_percentage=0,
                current_message="Starting analysis chain...",
                start_time=time.time(),
                force_refresh = force_refresh
            )
            
            # Step 1: Check for shortened chain call
            if force_refresh:
                print(f"Initiating short chain")
                self._advance_to_collection(session_id)

                # Start chain monitoring in background
                self._start_chain_monitoring(session_id)

                return ChainOperationResult(
                    success=True,
                    operation="whats_going_on",
                    session_id=session_id,
                    response_message="🔍 **Content Refresh**\n",
                    show_progress=True
                )


            # Step 2: Start hot topics analysis directly
            print(f"🔥 Starting hot topics analysis for session {session_id}")
            self._start_hot_topics_analysis(session_id)
            
            # Start chain monitoring in background
            self._start_chain_monitoring(session_id)
            
            return ChainOperationResult(
                success=True,
                operation="whats_going_on",
                session_id=session_id,
                response_message="🔍 **Starting Fresh Analysis Chain**",
                show_progress=True
            )
            
        except Exception as e:
            print(f"❌ Error starting fresh analysis chain: {e}")
            return ChainOperationResult(
                success=False,
                operation="whats_going_on",
                session_id=session_id,
                response_message=f"❌ **Analysis Start Error**\n\n{str(e)}",
                error_message=str(e)
            )
    
    def _start_hot_topics_analysis(self, session_id: str):
        """Start hot topics analysis directly"""
        try:
            # Import and trigger current events analysis
            from current_events import trigger_current_events_analysis
            trigger_current_events_analysis(session_id)
            
            # Update progress
            self._update_chain_progress(session_id, 
                current_message="Analyzing current events and generating hot topics...",
                progress_percentage=10
            )
            
        except Exception as e:
            print(f"❌ Error starting hot topics analysis: {e}")
            self._handle_chain_error(session_id, f"Hot topics analysis failed: {str(e)}")
    
    def _start_chain_monitoring(self, session_id: str):
        """Start monitoring thread for chain progress"""
        def monitor_loop():
            print(f"🔍 Starting chain monitoring for session {session_id}")
            
            while session_id in self.active_chains:
                try:
                    chain = self.active_chains[session_id]
                    current_step = chain.current_step
                    
                    if current_step == "hot_topics":
                        self._check_hot_topics_completion(session_id)
                    elif current_step == "collection":
                        self._check_collection_completion(session_id)
                    elif current_step == "rescoring":
                        self._check_rescoring_completion(session_id)
                    elif current_step == "week_review":
                        self._check_week_review_completion(session_id)
                    elif current_step == "podcast":
                        self._check_podcast_completion(session_id)
                    elif current_step == "completed":
                        self._finalize_chain(session_id)
                        break
                        
                    time.sleep(3)  # Check every 3 seconds
                    
                except Exception as e:
                    print(f"❌ Chain monitoring error for {session_id}: {e}")
                    self._handle_chain_error(session_id, str(e))
                    break
            
            print(f"🏁 Chain monitoring ended for session {session_id}")
        
        # Start monitoring thread
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
        self.monitoring_threads[session_id] = thread
    
    def _check_hot_topics_completion(self, session_id: str):
        """Check if hot topics analysis is complete"""
        try:
            status = self.current_events.get_current_events_status()
            
            if status.get('completed'):
                if status.get('success'):
                    print(f"✅ Hot topics completed for {session_id}, advancing to collection")
                    self._advance_to_collection(session_id)
                else:
                    error_msg = status.get('error', 'Hot topics analysis failed')
                    self._handle_chain_error(session_id, error_msg)
                    
        except Exception as e:
            print(f"❌ Error checking hot topics completion: {e}")
            self._handle_chain_error(session_id, f"Hot topics check failed: {str(e)}")
    
    def _advance_to_collection(self, session_id: str):
        """Advance to content collection phase"""
        try:
            # Update chain progress
            chain = self.active_chains[session_id]
            chain.current_step = "collection"
            chain.steps_completed.append("hot_topics")
            chain.progress_percentage = 25
            if chain.force_refresh:
                chain.progress_percentage = 25  # 25% of 2-step process
                chain.current_message = "Starting content collection..."
            else:
                chain.progress_percentage = 25  # 25% of 6-step process
                chain.current_message = "Hot topics analysis complete. Starting content collection..."
            
            print(f"📚 Starting collection phase for session {session_id}")
            
            # Get hot topics keywords for collection
            keywords = self._get_hot_topics_keywords()
            
            # Start collection with hot topics context
            collection_stats = self.content_curator.run_collection_cycle(
                hot_topics_keywords=keywords,
                max_process=None,
                session_context={'chain_session_id': session_id}
            )

            if chain.force_refresh:
                progress_msg = "Content collection started..."
                progress_pct = 40
            else:
                progress_msg = "Content collection started with hot topics keywords..."
                progress_pct = 30

            self._update_chain_progress(session_id,
                current_message=progress_msg,
                progress_percentage=progress_pct
            )
            
        except Exception as e:
            print(f"❌ Error advancing to collection: {e}")
            self._handle_chain_error(session_id, f"Collection start failed: {str(e)}")

    def _check_collection_completion(self, session_id: str):
        """Check if content collection is complete with stall detection"""
        try:
            progress = self.content_curator.get_operation_progress()

            # Check for stalled operation
            if self._is_collection_stalled(progress, session_id):
                print(f"⚠️ Collection appears stalled for {session_id}, attempting recovery")
                self._recover_stalled_collection(session_id)
                return

            if progress.get('status') == 'completed' or not progress.get('status') == 'running':
                print(f"✅ Collection completed for {session_id}, advancing to rescoring")
                self._advance_to_rescoring(session_id)

        except Exception as e:
            print(f"❌ Error checking collection completion: {e}")
            self._handle_chain_error(session_id, f"Collection check failed: {str(e)}")

    def _is_collection_stalled(self, progress: Dict, session_id: str) -> bool:
        """Check if collection operation is stalled"""
        if not progress or progress.get('status') != 'running':
            return False

        # Check if operation has been running too long without progress
        elapsed = progress.get('elapsed_seconds', 0)
        if elapsed > 300:  # 5 minutes
            print(f"⚠️ Collection running for {elapsed}s, checking for stall")
            return True

        return False

    def _recover_stalled_collection(self, session_id: str):
        """Attempt to recover from stalled collection"""
        try:
            # Force finish the collection operation
            self.content_curator.progress.finish(final_status='recovered')

            # Advance to next phase
            print(f"🔄 Forcing collection completion for {session_id}")
            self._advance_to_rescoring(session_id)

        except Exception as e:
            print(f"❌ Recovery failed: {e}")
            self._handle_chain_error(session_id, f"Collection recovery failed: {str(e)}")

    def _advance_to_rescoring(self, session_id: str):
        """Advance to rescoring phase"""
        try:
            # Update chain progress
            chain = self.active_chains[session_id]
            chain.current_step = "rescoring"
            chain.steps_completed.append("collection")
            if chain.force_refresh:
                chain.progress_percentage = 75  # 75% of 2-step process
                chain.current_message = "Content collection complete. Starting rescoring..."
            else:
                chain.progress_percentage = 50  # 50% of 6-step process
                chain.current_message = "Content collection complete. Starting hot topics rescoring..."
            print(f"⚡ Starting rescoring phase for session {session_id}")
            
            # Get hot topics keywords for rescoring
            keywords = self._get_hot_topics_keywords()
            
            if keywords:
                # Start rescoring with hot topics
                rescore_stats = self.content_curator.rescore_with_hot_topics(keywords, force_rescore=False)

                if chain.force_refresh:
                    progress_msg = f"Rescoring complete. Boosted {rescore_stats.get('articles_boosted', 0)} articles. Opening modal..."
                    progress_pct = 95
                else:
                    progress_msg = f"Rescoring complete. Boosted {rescore_stats.get('articles_boosted', 0)} articles..."
                    progress_pct = 60

                self._update_chain_progress(session_id,
                                            current_message=progress_msg,
                                            progress_percentage=progress_pct
                                            )
                
                # Move to next step
                chain = self.active_chains[session_id]
                if chain.force_refresh:
                    # For force_refresh, complete after rescoring
                    print(f"🔄 Force refresh chain completed for {session_id}")
                    self._finalize_chain(session_id)
                else:
                    # For normal chains, continue to week review
                    self._advance_to_week_review(session_id)
            else:
                print(f"⚠️ No hot topics keywords found, skipping rescoring")
                self._advance_to_week_review(session_id)
                
        except Exception as e:
            print(f"❌ Error advancing to rescoring: {e}")
            self._handle_chain_error(session_id, f"Rescoring failed: {str(e)}")
    
    def _check_rescoring_completion(self, session_id: str):
        """Check if rescoring is complete (immediate for direct call)"""
        # Rescoring is synchronous, so advance immediately
        self._advance_to_week_review(session_id)
    
    def _advance_to_week_review(self, session_id: str):
        """Advance to week review phase"""
        try:
            # Update chain progress
            chain = self.active_chains[session_id]
            chain.current_step = "week_review"
            chain.steps_completed.append("rescoring")
            chain.progress_percentage = 70
            chain.current_message = "Rescoring complete. Starting week review generation..."
            
            print(f"📊 Starting week review phase for session {session_id}")
            
            # Start week review generation
            result = self.week_review_processor.start_week_review(session_id, chain_context=True)
            
            if result.get('success'):
                self._update_chain_progress(session_id,
                    current_message="Week review generation started...",
                    progress_percentage=75
                )
            else:
                print(f"⚠️ Week review failed, continuing to podcast: {result.get('error')}")
                self._advance_to_podcast(session_id)
                
        except Exception as e:
            print(f"❌ Error advancing to week review: {e}")
            # Continue to podcast even if week review fails
            self._advance_to_podcast(session_id)
    
    def _check_week_review_completion(self, session_id: str):
        """Check if week review is complete"""
        try:
            progress = self.week_review_processor.get_progress(session_id)
            
            if progress.get('is_completed'):
                print(f"✅ Week review completed for {session_id}, advancing to podcast")
                self._advance_to_podcast(session_id)
                
        except Exception as e:
            print(f"❌ Error checking week review completion: {e}")
            # Continue to podcast even if week review check fails
            self._advance_to_podcast(session_id)
    
    def _advance_to_podcast(self, session_id: str):
        """Advance to podcast generation phase"""
        try:
            # Update chain progress
            chain = self.active_chains[session_id]
            chain.current_step = "podcast"
            chain.steps_completed.append("week_review")
            chain.progress_percentage = 85
            chain.current_message = "Week review complete. Starting podcast generation..."
            
            print(f"🎙️ Starting podcast phase for session {session_id}")
            
            # Start podcast generation
            result = self.podcast_generator.start_podcast_generation(session_id, chain_context=True)
            
            if result.get('status') in ['started', 'completed']:
                self._update_chain_progress(session_id,
                    current_message="Podcast generation started...",
                    progress_percentage=90
                )
            else:
                print(f"⚠️ Podcast failed, completing chain anyway: {result.get('message')}")
                self._finalize_chain(session_id)
                
        except Exception as e:
            print(f"❌ Error advancing to podcast: {e}")
            # Complete chain even if podcast fails
            self._finalize_chain(session_id)
    
    def _check_podcast_completion(self, session_id: str):
        """Check if podcast generation is complete"""
        try:
            progress = self.podcast_generator.get_progress(session_id)
            
            if progress and progress.get('completed'):
                print(f"✅ Podcast completed for {session_id}, finalizing chain")
                self._finalize_chain(session_id)
            elif progress and progress.get('error'):
                print(f"⚠️ Podcast error, finalizing chain anyway: {progress.get('error')}")
                self._finalize_chain(session_id)
                
        except Exception as e:
            print(f"❌ Error checking podcast completion: {e}")
            # Complete chain even if podcast check fails
            self._finalize_chain(session_id)
    
    def _finalize_chain(self, session_id: str):
        """Finalize the chain workflow"""
        try:
            # Update chain progress
            chain = self.active_chains[session_id]
            chain.current_step = "completed"
            if chain.force_refresh:
                chain.steps_completed.append("rescoring")
                chain.current_message = "Content refresh completed! Opening updated articles..."
            else:
                chain.steps_completed.append("podcast")
                chain.current_message = "Analysis chain completed successfully!"

            chain.progress_percentage = 100
            
            # Collect results for frontend
            if chain.force_refresh:
                chain.result_data = {
                    'articles_analyzed': self._get_articles_count(),
                    'refresh_completed': True,
                    'completion_time': time.time() - chain.start_time,
                    'show_modal': True  # Signal frontend to open modal
                }
            else:
                chain.result_data = {
                    'articles_analyzed': self._get_articles_count(),
                    'hot_topics_generated': bool(self._get_hot_topics_keywords()),
                    'week_review_available': self._check_week_review_exists(),
                    'podcast_available': self._check_podcast_exists(),
                    'completion_time': time.time() - chain.start_time
                }
            
            print(f"✅ Chain workflow completed for session {session_id}")
            print(f"   Total time: {chain.result_data['completion_time']:.1f}s")
            print(f"   Steps completed: {len(chain.steps_completed)}/{chain.total_steps}")
            
            # Move to history
            self.chain_history[session_id] = self.active_chains[session_id]
            
        except Exception as e:
            print(f"❌ Error finalizing chain: {e}")
            self._handle_chain_error(session_id, f"Finalization failed: {str(e)}")
    
    # =============================================================================
    # HELPER METHODS FOR CHAIN ORCHESTRATION
    # =============================================================================
    
    def _get_hot_topics_keywords(self) -> List[str]:
        """Get keywords from completed hot topics analysis"""
        try:
            today = datetime.now().strftime("%Y%m%d")
            pattern = os.path.join(self.topics_directory, f"current_events_analysis_{today}*.json")
            files = glob.glob(pattern)
            
            if files:
                latest_file = max(files, key=os.path.getctime)
                with open(latest_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                analysis = data.get('analysis', {})
                keywords = analysis.get('search_keywords', [])
                print(f"📋 Found {len(keywords)} hot topics keywords")
                return keywords
                
        except Exception as e:
            print(f"⚠️ Error loading hot topics keywords: {e}")
        
        return []
    
    def _get_articles_count(self) -> int:
        """Get count of articles in database"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.content_curator.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM content')
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0
    
    def _check_week_review_exists(self) -> bool:
        """Check if week review exists"""
        try:
            return self.week_review_processor.check_existing_week_review() is not None
        except Exception:
            return False
    
    def _check_podcast_exists(self) -> bool:
        """Check if podcast exists"""
        try:
            today = datetime.now().strftime('%Y%m%d')
            return bool(self.podcast_generator._check_existing_podcast_files(today))
        except Exception:
            return False
    
    def _update_chain_progress(self, session_id: str, current_message: str = None, 
                             progress_percentage: int = None):
        """Update chain progress"""
        if session_id in self.active_chains:
            chain = self.active_chains[session_id]
            if current_message:
                chain.current_message = current_message
            if progress_percentage is not None:
                chain.progress_percentage = progress_percentage
            if current_message and hasattr(self, 'frontend_callback') and self.frontend_callback:
                progress_msg = f"⏳ **{current_message}** ({progress_percentage}%)" if progress_percentage else f"⏳ **{current_message}**"
                self.frontend_callback(progress_msg, 'progress', chain.operation, session_id)
    
    def _handle_chain_error(self, session_id: str, error_message: str):
        """Handle chain error"""
        if session_id in self.active_chains:
            chain = self.active_chains[session_id]
            chain.error_message = error_message
            chain.current_message = f"Error: {error_message}"
            print(f"❌ Chain error for {session_id}: {error_message}")

    def _check_analysis_flag(self) -> bool:
        """Check if analysis flag exists for today"""
        try:
            today = datetime.now().strftime('%Y%m%d')
            flag_file = os.path.join(self.data_directory, f"analysis_complete_{today}.flag")
            return os.path.exists(flag_file)
        except Exception:
            return False
    
    def _create_analysis_flag(self):
        """Create analysis flag for today"""
        try:
            today = datetime.now().strftime('%Y%m%d')
            self._cleanup_old_flag_files(today)
            flag_file = os.path.join(self.data_directory, f"analysis_complete_{today}.flag")
            
            flag_data = {
                'created_at': datetime.now().isoformat(),
                'date': today,
                'status': 'analysis_started'
            }
            
            with open(flag_file, 'w', encoding='utf-8') as f:
                json.dump(flag_data, f, indent=2)
                
        except Exception as e:
            print(f"⚠️ Error creating flag file: {e}")

    def _cleanup_old_flag_files(self, keep_date: str):
        """
        Remove flag files from previous days

        Args:
            keep_date: Date string (YYYYMMDD) to keep, remove all others
        """
        try:
            import glob

            pattern = os.path.join(self.data_directory, "analysis_complete_*.flag")
            flag_files = glob.glob(pattern)

            keep_filename = f"analysis_complete_{keep_date}.flag"

            for flag_file in flag_files:
                filename = os.path.basename(flag_file)
                if filename != keep_filename:
                    try:
                        os.remove(flag_file)
                        print(f"🧹 Cleaned up old flag: {filename}")
                    except Exception as e:
                        print(f"⚠️ Error removing {filename}: {e}")

        except Exception as e:
            print(f"⚠️ Error during flag cleanup: {e}")

    def _show_existing_results_flow(self, session_id: str) -> ChainOperationResult:
        """Handle existing results flow"""
        try:
            print(f"📋 Showing existing results for session: {session_id}")
            
            return ChainOperationResult(
                success=True,
                operation="whats_going_on",
                session_id=session_id,
                response_message="📊 **Today's Analysis Available**\n\nLoading existing results...",
                actions=[{'type': 'show_existing_results_modal'}],
                show_modal=True
            )
            
        except Exception as e:
            print(f"❌ Error showing existing results: {e}")
            # Fallback to fresh analysis
            return self._start_fresh_analysis_chain(session_id, force_refresh=True)
    
    def _start_refresh_chain(self, session_id: str, timeframe_hours: int) -> ChainOperationResult:
        """Start simplified refresh chain: collection → rescore only"""
        try:
            print(f"🔄 Starting refresh chain - session: {session_id}, timeframe: {timeframe_hours}h")
            
            # Start collection directly
            def run_refresh():
                try:
                    print(f"🔄 Running collection cycle with {timeframe_hours}h timeframe")
                    stats = self.content_curator.run_collection_cycle(
                        time_range_hours=timeframe_hours,
                        max_process=None
                    )
                    
                    # Follow up with rescoring if hot topics available
                    keywords = self._get_hot_topics_keywords()
                    if keywords:
                        self.content_curator.rescore_with_hot_topics(keywords, force_rescore=False)
                    
                    print(f"✅ Refresh completed: {stats}")
                except Exception as e:
                    print(f"❌ Refresh error: {e}")
            
            refresh_thread = threading.Thread(target=run_refresh, daemon=True)
            refresh_thread.start()
            
            return ChainOperationResult(
                success=True,
                operation="force_refresh",
                session_id=session_id,
                response_message=f"🔄 **Starting Content Refresh**\n\nCollecting new content from last {timeframe_hours} hours...",
                show_progress=True
            )
            
        except Exception as e:
            print(f"❌ Error starting refresh chain: {e}")
            return ChainOperationResult(
                success=False,
                operation="force_refresh",
                session_id=session_id,
                response_message=f"❌ **Refresh Error**\n\n{str(e)}",
                error_message=str(e)
            )
    
    # =============================================================================
    # PROGRESS MONITORING - UNIFIED PROGRESS FROM ALL OPERATIONS
    # =============================================================================
    
    def get_operation_progress(self, session_id: str) -> Optional[Dict]:
        """Get unified progress from chain operations"""
        try:
            # Check active chain first
            if session_id in self.active_chains:
                chain = self.active_chains[session_id]
                return {
                    'status': 'running' if chain.current_step != 'completed' else 'completed',
                    'message': chain.current_message,
                    'percentage': chain.progress_percentage,
                    'operation': chain.operation,
                    'current_step': chain.current_step,
                    'steps_completed': chain.steps_completed,
                    'total_steps': chain.total_steps,
                    'error': chain.error_message,
                    'result_data': chain.result_data
                }
            
            # Check individual module progress as fallback
            # Week review processor
            week_progress = self.week_review_processor.get_progress(session_id)
            if week_progress and week_progress.get('exists', False):
                return {
                    'status': 'running' if not week_progress.get('is_completed', False) else 'completed',
                    'message': week_progress.get('current_message', 'Processing week review...'),
                    'percentage': week_progress.get('progress_percentage', 0),
                    'operation': 'week_review'
                }
            
            # Podcast generator
            podcast_progress = self.podcast_generator.get_progress(session_id)
            if podcast_progress:
                return {
                    'status': podcast_progress['status'],
                    'message': podcast_progress.get('progress', {}).get('message', 'Processing podcast...'),
                    'percentage': podcast_progress.get('progress', {}).get('percentage', 0),
                    'operation': 'podcast'
                }
            
            # Content curator
            curator_progress = self.content_curator.get_operation_progress()
            if curator_progress and curator_progress.get('status') == 'running':
                return {
                    'status': 'running',
                    'message': curator_progress.get('current_message', 'Processing content...'),
                    'percentage': curator_progress.get('percentage', 0),
                    'operation': 'collection'
                }
            
            print(f"📊 No active operations found for session: {session_id}")
            return {'status': 'idle'}
            
        except Exception as e:
            print(f"❌ Error getting operation progress: {e}")
            return {'status': 'error', 'error': str(e)}
    
    # =============================================================================
    # RESPONSE FORMATTING
    # =============================================================================
    
    def _format_existing_week_review_message(self, review_data: Dict) -> str:
        """Format existing week review message"""
        return f"""✅ **Week Review Already Generated!**

📅 **Analysis Period:** {review_data.get('date_range', 'Unknown')}
📊 **Days Analyzed:** {review_data.get('total_days_analyzed', 0)}
🔍 **Keywords Found:** {len(review_data.get('keyword_counts', {}))}
📈 **Trends Found:** {len(review_data.get('trend_counts', {}))}

*Analysis loaded from cache - no processing time needed!*

📊 **Detailed analysis opened in modal window!**"""

    def _format_existing_podcast_message(self, podcast_files: Dict) -> str:
        """Format existing podcast message"""
        today = datetime.now().strftime('%A, %B %d, %Y')
        
        message = f"""Today's Podcast Already Created!"""
        message += "\nNo processing time needed! Listen Below"
        
        return message

# =============================================================================
# FLASK INTEGRATION ENDPOINTS (Updated for direct orchestration)
# =============================================================================

def add_chain_operations_endpoints(app, chain_ops_manager):
    """Add chain operations endpoints to Flask app"""
    from flask import request, jsonify
    
    @app.route('/api/chain/whats-going-on', methods=['POST'])
    def chain_whats_going_on():
        """What's Going On button endpoint"""
        try:
            data = request.json or {}
            session_id = data.get('session_id', f"chain_{int(time.time())}")
            force_refresh = data.get('force_refresh', False)

            result = chain_ops_manager.start_whats_going_on(session_id, force_refresh)
            return jsonify(result.to_dict())
        except Exception as e:
            print(f"❌ API Error: /api/chain/whats-going-on - {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'operation': 'whats_going_on'
            }), 500
    
    @app.route('/api/chain/week-review', methods=['POST'])  
    def chain_week_review():
        """Week Review button endpoint"""
        try:
            data = request.json or {}
            session_id = data.get('session_id', f"chain_{int(time.time())}")

            result = chain_ops_manager.start_week_review(session_id)
            return jsonify(result.to_dict())
        except Exception as e:
            print(f"❌ API Error: /api/chain/week-review - {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'operation': 'week_review'
            }), 500
    
    @app.route('/api/chain/podcast', methods=['POST'])
    def chain_podcast():
        """Daily Podcast button endpoint"""
        try:
            data = request.json or {}
            session_id = data.get('session_id', f"chain_{int(time.time())}")
            force_new = data.get('force_new', False)

            result = chain_ops_manager.start_daily_podcast(session_id, force_new)
            return jsonify(result.to_dict())
        except Exception as e:
            print(f"❌ API Error: /api/chain/podcast - {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'operation': 'daily_podcast'
            }), 500
    
    @app.route('/api/chain/force-refresh', methods=['POST'])
    def chain_force_refresh():
        """Force content refresh endpoint"""
        try:
            data = request.json or {}
            session_id = data.get('session_id', f"chain_{int(time.time())}")
            timeframe_hours = data.get('timeframe_hours', 48)
            
            result = chain_ops_manager.start_force_refresh_content(session_id, timeframe_hours)
            return jsonify(result.to_dict())
        except Exception as e:
            print(f"❌ API Error: /api/chain/force-refresh - {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'operation': 'force_refresh'
            }), 500
    
    @app.route('/api/chain/progress/<session_id>')
    def chain_progress(session_id):
        """Unified progress endpoint"""
        try:
            progress = chain_ops_manager.get_operation_progress(session_id)
            return jsonify(progress or {'status': 'idle'})
        except Exception as e:
            print(f"❌ API Error: /api/chain/progress/{session_id} - {e}")
            return jsonify({'status': 'error', 'error': str(e)}), 500

# =============================================================================
# FACTORY FUNCTION (Updated)
# =============================================================================

def create_chain_operations_manager(content_curator, current_events_module, 
                                   week_review_processor, podcast_generator):
    """Factory function to create chain operations manager - no research assistant needed"""
    print("🏭 Creating Chain Operations Manager with direct orchestration...")
    
    return ChainOperationsManager(
        content_curator=content_curator,
        current_events_module=current_events_module,
        week_review_processor=week_review_processor,
        podcast_generator=podcast_generator
    )