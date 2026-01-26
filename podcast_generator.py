import sqlite3
import json
import os
import threading
import time
import uuid
import subprocess
import tempfile
import shutil
import glob
import concurrent.futures
from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple, Callable


try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    print("⚠️ pydub not available. Install with: pip install pydub")


class PodcastSession:
    """Session management for podcast generation"""
    def __init__(self, session_id):
        self.session_id = session_id
        self.status = 'initializing'
        self.progress = {}
        self.context = {}
        self.created_at = datetime.now()
        self.completed = False
        self.error = None
        self.files_generated = {}
        self.chain_context = False


class DialoguePiece:
    """Represents a single piece of dialogue with speaker and text"""
    def __init__(self, speaker, text, sequence_order):
        self.speaker = speaker
        self.text = text.strip()
        self.sequence_order = sequence_order
        self.audio_file = None
        self.duration = 0.0
    
    def __repr__(self):
        return f"DialoguePiece(speaker='{self.speaker}', order={self.sequence_order}, text='{self.text[:50]}...')"


class PodcastGenerator:
    """Self-Contained Daily Podcast Generator - Refactored for Chain Operations Manager"""
    
    VOICE_CONFIG = {
        'Alex': 'en-US-AriaNeural',
        'Sam': 'en-US-ChristopherNeural'
    }
    
    SPEAKER_PAUSE_DURATION = 0.4
    SENTENCE_PAUSE_DURATION = 0.2
    
    def __init__(self, content_curator, chat_assistant, pcast_directory="podcasts", topics_directory="currentevents", research_assistant=None):
        self.content_curator = content_curator
        self.chat_assistant = chat_assistant
        # Keep research_assistant for legacy compatibility but don't use it for progress
        self.research_assistant = research_assistant
        self.sessions = {}
        self.pcast_directory = os.path.abspath(pcast_directory)
        self.topics_directory = os.path.abspath(topics_directory)
        os.makedirs(self.pcast_directory, exist_ok=True)

        self.progress_callback = None

        self.edge_tts_available = self._check_edge_tts_availability()
        if not self.edge_tts_available:
            print("⚠️ edge-tts not available. Install with: pip install edge-tts")
        
        print(f"🎙️ Podcast Generator initialized")
        print(f"   Podcast Storage: {self.pcast_directory}")
        print(f"   Hot Topics Repository: {self.topics_directory}")
        print(f"   Edge TTS: {'Available' if self.edge_tts_available else 'Not Available'}")
        print(f"   PyDub: {'Available' if PYDUB_AVAILABLE else 'Not Available'}")
    
    def _check_edge_tts_availability(self):
        """Check if edge-tts is available"""
        try:
            result = subprocess.run(['edge-tts', '--list-voices'], 
                                  capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return False

    def _cleanup_old_audio_files(self) -> int:
        """Delete podcast audio files older than 14 days. Returns count of deleted files."""
        deleted = 0
        cutoff = datetime.now() - timedelta(days=14)

        for ext in ['.mp3', '.wav', '.aiff']:
            for filepath in glob.glob(os.path.join(self.pcast_directory, f"podcast_*{ext}")):
                filename = os.path.basename(filepath)
                try:
                    date_str = filename.split('_')[1].split('.')[0][:8]
                    file_date = datetime.strptime(date_str, "%Y%m%d")
                    if file_date < cutoff:
                        os.remove(filepath)
                        print(f"🗑️ Deleted old audio: {filename}")
                        deleted += 1
                except (ValueError, IndexError):
                    continue

        if deleted:
            print(f"🧹 Cleaned up {deleted} audio file(s) older than 14 days")
        return deleted

    def start_podcast_generation(self, session_id, chain_context: bool = False,
                                 progress_callback: Optional[Callable] = None):
        """Start the self-contained podcast generation workflow"""

        print(f"🎙️ Podcast Generation Start:")
        print(f"  - session_id: '{session_id}'")
        print(f"  - chain_context: {chain_context}")
        print(f"  - progress_callback: {'Available' if progress_callback else 'None'}")

        try:
            session = PodcastSession(session_id)
            session.chain_context = chain_context
            self.sessions[session_id] = session

            print(f"🎧 Starting fresh podcast generation")

            # Start generation in background thread
            def run_generation():
                try:
                    self._run_podcast_generation(session, progress_callback)
                except Exception as e:
                    print(f"❌ Podcast generation failed: {e}")
                    session.status = 'error'
                    session.error = str(e)
                    self._update_progress(session, f'Podcast generation failed: {str(e)}', 0, progress_callback)

            generation_thread = threading.Thread(target=run_generation, daemon=True)
            generation_thread.start()

            return {
                'status': 'started',
                'message': 'Daily podcast generation started',
                'session_id': session_id,
                'edge_tts_enabled': self.edge_tts_available
            }

        except Exception as e:
            print(f"❌ Error starting podcast generation: {e}")
            return {
                'status': 'error',
                'message': f'Failed to start podcast generation: {str(e)}'
            }
    
    def _check_existing_podcast_files(self, date_str: str) -> Dict[str, str]:
        """Check if podcast files already exist for the given date"""
        existing_files = {}
        
        # Check for script file
        script_file = f"podcast_{date_str}.txt"
        script_path = os.path.join(self.pcast_directory, script_file)
        if os.path.exists(script_path):
            existing_files['script'] = script_file
        
        # Check for audio files (multiple formats)
        audio_extensions = ['.wav', '.mp3', '.aiff']
        for ext in audio_extensions:
            audio_file = f"podcast_{date_str}{ext}"
            audio_path = os.path.join(self.pcast_directory, audio_file)
            if os.path.exists(audio_path):
                existing_files['audio'] = audio_file
                break
        
        return existing_files
    
    def _run_podcast_generation(self, session, progress_callback: Optional[Callable] = None):
        """Complete self-contained podcast generation pipeline"""
        try:
            session.status = 'running'
            print(f"🎙️ Starting podcast generation for session {session.session_id}")

            # Cleanup old audio files
            self._cleanup_old_audio_files()

            self._update_progress(session, 'Starting daily podcast generation...', 0, progress_callback)

            # Step 1: Load current events data (10%)
            self._update_progress(session, 'Loading current events analysis...', 10, progress_callback)
            events_data = self._load_current_events_data()
            
            if not events_data:
                raise Exception("No current events data available for podcast generation")
            
            session.context['events_data'] = events_data
            
            # Step 2: Generate podcast script from events data (20-40%)
            self._update_progress(session, 'Generating podcast script from events...', 20, progress_callback)
            script = self._generate_script_from_events(events_data, session, progress_callback)
            
            # Step 3: Save initial script file
            today = datetime.now().strftime("%Y%m%d")
            script_filename = f"podcast_{today}.txt"
            script_path = os.path.join(self.pcast_directory, script_filename)
            
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(script)
            
            session.files_generated['script'] = script_filename
            self._update_progress(session, f'Script generated and saved: {script_filename}', 48, progress_callback)
            
            # Step 4: Convert script to audio (50-70%)
            self._update_progress(session, 'Converting script to audio with distinct voices...', 50, progress_callback)
            audio_filename = self._convert_to_audio_enhanced(script, today, session, progress_callback)
            
            session.files_generated['audio'] = audio_filename
            self._update_progress(session, 'Audio generation completed', 70, progress_callback)
            
            # Step 5: Measure audio duration (80%)
            self._update_progress(session, 'Measuring podcast duration...', 80, progress_callback)
            audio_duration = self._measure_audio_duration(audio_filename)
            session.context['audio_duration'] = audio_duration
            
            # Step 6: Generate enhanced metadata (80-95%)
            self._update_progress(session, 'Generating podcast metadata...', 85, progress_callback)
            metadata = self._generate_podcast_metadata(script)
            session.context['metadata'] = metadata
            
            # Step 7: Overwrite script file with enhanced format (95-100%)
            self._update_progress(session, 'Finalizing podcast files...', 95, progress_callback)
            self._create_enhanced_script_file(script_path, script, metadata, audio_duration, today)
            
            session.status = 'completed'
            session.completed = True
            
            self._update_progress(session, 'Daily podcast generation completed successfully!', 100, progress_callback)
            
            print(f"✅ Podcast generation completed for session {session.session_id}")
            print(f"   Script: {script_filename}")
            print(f"   Audio: {audio_filename}")
            print(f"   Duration: {audio_duration}")
            print(f"   Title: {metadata.get('title', 'N/A')}")

            # Notify completion if in chain context
            if getattr(session, 'chain_context', False):
                self._notify_chain_completion(session.session_id, session.files_generated, progress_callback)

        except Exception as e:
            print(f"❌ Podcast generation error: {str(e)}")
            import traceback
            traceback.print_exc()
            session.status = 'error'
            session.error = str(e)
            self._update_progress(session, f'Error during podcast generation: {str(e)}', 0, progress_callback)

    def _load_current_events_data(self) -> Optional[Dict]:
        """Load current events analysis data from files"""
        try:
            # Look for today's current events analysis first
            today = datetime.now().strftime("%Y%m%d")
            pattern = os.path.join(self.topics_directory, f"current_events_analysis_{today}*.json")
            files = glob.glob(pattern)
            
            if not files:
                # Try yesterday as fallback
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
                pattern = os.path.join(self.topics_directory, f"current_events_analysis_{yesterday}*.json")
                files = glob.glob(pattern)
                print(f"📅 No events file for today, trying yesterday: {yesterday}")
            
            if not files:
                # Try last 3 days as final fallback
                for days_back in range(2, 4):
                    fallback_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
                    pattern = os.path.join(self.topics_directory, f"current_events_analysis_{fallback_date}*.json")
                    files = glob.glob(pattern)
                    if files:
                        print(f"📅 Using events file from {days_back} days ago: {fallback_date}")
                        break
            
            if not files:
                print("❌ No current events analysis files found in data directory")
                return None
                
            # Use most recent file
            latest_file = max(files, key=os.path.getctime)
            
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            print(f"📊 Loaded current events data from: {os.path.basename(latest_file)}")
            return data
            
        except Exception as e:
            print(f"❌ Error loading current events data: {e}")
            return None

    def _get_articles_for_scripts(self) -> List[Dict]:
        """Get 5 articles with fallback strategy: boosted -> high relevance -> recent"""
        try:
            conn = sqlite3.connect(self.content_curator.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            articles = []

            # Step 1: Try to get boosted articles first
            cursor.execute('''
                SELECT *, (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                FROM content 
                WHERE relevance_boost > 0
                ORDER BY total_relevance DESC, published_date DESC
                LIMIT 5
            ''')

            boosted_articles = [dict(row) for row in cursor.fetchall()]
            articles.extend(boosted_articles)

            print(f"📊 Found {len(boosted_articles)} boosted articles")

            # Step 2: If we need more, get high relevance articles
            if len(articles) < 5:
                remaining_needed = 5 - len(articles)

                # Get URLs we already have to avoid duplicates
                existing_urls = [article['url'] for article in articles]
                url_placeholders = ','.join(['?' for _ in existing_urls]) if existing_urls else "''"

                if existing_urls:
                    cursor.execute(f'''
                        SELECT *, (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                        FROM content 
                        WHERE url NOT IN ({url_placeholders})
                        ORDER BY total_relevance DESC, published_date DESC
                        LIMIT ?
                    ''', existing_urls + [remaining_needed])
                else:
                    cursor.execute('''
                        SELECT *, (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                        FROM content 
                        ORDER BY total_relevance DESC, published_date DESC
                        LIMIT ?
                    ''', (remaining_needed,))

                high_relevance_articles = [dict(row) for row in cursor.fetchall()]
                articles.extend(high_relevance_articles)

                print(f"📊 Added {len(high_relevance_articles)} high relevance articles")

            # Step 3: If still need more, get most recent
            if len(articles) < 5:
                remaining_needed = 5 - len(articles)
                existing_urls = [article['url'] for article in articles]
                url_placeholders = ','.join(['?' for _ in existing_urls]) if existing_urls else "''"

                if existing_urls:
                    cursor.execute(f'''
                        SELECT *, (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                        FROM content 
                        WHERE url NOT IN ({url_placeholders})
                        ORDER BY published_date DESC
                        LIMIT ?
                    ''', existing_urls + [remaining_needed])
                else:
                    cursor.execute('''
                        SELECT *, (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance
                        FROM content 
                        ORDER BY published_date DESC
                        LIMIT ?
                    ''', (remaining_needed,))

                recent_articles = [dict(row) for row in cursor.fetchall()]
                articles.extend(recent_articles)

                print(f"📊 Added {len(recent_articles)} recent articles")

            conn.close()

            print(f"📋 Total articles selected: {len(articles)}")
            return articles[:5]  # Ensure we never exceed 5

        except Exception as e:
            print(f"❌ Error getting articles for scripts: {e}")
            return []

    def _get_script_config(self) -> Dict:
        """Get shared script configuration for consistent character handling"""
        return {
            'characters': {
                'alex': {
                    'name': 'Alex',
                    'description': 'female, tech-focused',
                    'personality': 'Enthusiastic about technology, data trends, and analytical insights. Loves diving into details.',
                    'style': 'tends to go deeper analytically'
                },
                'sam': {
                    'name': 'Sam',
                    'description': 'male, business-focused',
                    'personality': 'Business-minded, practical implications, market perspectives. More skeptical and grounded.',
                    'style': 'pulls back to practical relevance'
                }
            },
            'conversation_guidelines': [
                'Natural conversation with agreements, disagreements, and insights',
                'Include smooth transitions between topics',
                'Natural back-and-forth with interruptions and agreements',
                'Make it feel like a real conversation between knowledgeable hosts'
            ],
            'format_instruction': 'FORMAT (exactly like this):\nAlex: [dialogue]\nSam: [dialogue]',
            'word_targets': {
                'intro': 500,
                'article': 300,
                'closing': 300
            }
        }

    def _build_character_context(self, config: Dict) -> str:
        """Build character context string for prompts"""
        alex = config['characters']['alex']
        sam = config['characters']['sam']

        return f"""HOST PERSONALITIES:
    - {alex['name']}: {alex['description']} - {alex['personality']} {alex['style']}.
    - {sam['name']}: {sam['description']} - {sam['personality']} {sam['style']}.

    CONVERSATION STYLE:
    {chr(10).join([f'- {guideline}' for guideline in config['conversation_guidelines']])}

    {config['format_instruction']}"""

    def _build_base_prompt_template(self, config: Dict, word_target: int, content_type: str) -> str:
        """Build base prompt template with shared elements"""
        character_context = self._build_character_context(config)

        return f"""Generate a natural, engaging {word_target}-word podcast script segment between two hosts discussing {content_type}.

    {character_context}

    TARGET: Exactly {word_target} words for this segment.

    CONTENT TO DISCUSS:
    {{content_placeholder}}

    REQUIREMENTS:
    - Natural conversation between the hosts
    - Stay focused on the {content_type} 
    - Maintain the {word_target}-word target
    - Use the exact format specified above

    Begin the script now:"""

    def _generate_script_from_events(self, events_data: Dict, session=None, progress_callback=None) -> str:
        """Generate podcast script from current events analysis data using 5-step concurrent process"""
        try:
            analysis = events_data.get('analysis', {})
            daily_summary = analysis.get('summary', analysis.get('daily_summary', ''))

            if not daily_summary:
                daily_summary = "Today's technology and business developments across various sectors."

            print("🎙️ Starting 5-step concurrent podcast script generation...")

            # Initialize shared configuration (Step 1)
            script_config = self._get_script_config()

            # Generate all scripts concurrently (Steps 2-4)
            intro_script, article_scripts, closing_script = self._generate_scripts_concurrent(
                daily_summary, script_config, session, progress_callback
            )

            # Validate we have content for each segment
            if not intro_script:
                intro_script = "Alex: Welcome to today's tech and business update!\nSam: Great to have you with us today."

            if not article_scripts:
                article_scripts = [
                    "Alex: Let's dive into today's key developments.\nSam: There's certainly a lot happening."]

            if not closing_script:
                closing_script = "Alex: That's all for today's episode.\nSam: Thanks for listening, see you next time!"

            # Generate transitions and assemble final script (Step 5)
            final_script = self._assemble_script_with_transitions(
                intro_script, article_scripts, closing_script, script_config
            )

            print(f"📝 Generated complete podcast script: {len(final_script)} characters")
            print(f"   🎬 Structure: Intro → {len(article_scripts)} Articles → Closing")
            self._update_progress(session, '🔄 Generating transitions and assembling final script...', 45, progress_callback)

            return final_script

        except Exception as e:
            print(f"❌ Error in 5-step script generation: {e}")
            # Fallback to simplified single-prompt generation if all else fails
            return self._generate_fallback_script(events_data)

    def _generate_fallback_script(self, events_data: Dict) -> str:
        """Fallback to original single-prompt method if concurrent generation fails"""
        try:
            print("🔄 Using fallback script generation...")
            analysis = events_data.get('analysis', {})

            # Extract key information (maintain original logic)
            trending_topics = analysis.get('trending_topics', [])[:8]
            search_keywords = analysis.get('search_keywords', [])[:10]
            daily_summary = analysis.get('summary', analysis.get('daily_summary', ''))

            # Build formatted content for script generation
            topics_text = ""
            for i, topic in enumerate(trending_topics, 1):
                if isinstance(topic, dict):
                    title = topic.get('title', topic.get('name', str(topic)))
                    description = topic.get('description', topic.get('summary', ''))
                    topics_text += f"\n{i}. {title}"
                    if description:
                        topics_text += f" - {description}"
                else:
                    topics_text += f"\n{i}. {topic}"

            keywords_text = ", ".join(search_keywords) if search_keywords else "General current events"

            # Use original prompt structure
            prompt = f"""Generate a natural, engaging 2000-2500 word podcast script between two hosts named Alex and Sam discussing today's trending topics and current events.

    CURRENT EVENTS DATA TO DISCUSS:
    Daily Summary: {daily_summary}

    Top Trending Topics:{topics_text}

    Key Keywords: {keywords_text}

    SCRIPT REQUIREMENTS:
    - Natural conversation between Alex (female, tech-focused) and Sam (male, business-focused)
    - Cover the most important trending topics with deep analysis and context
    - Include intro, substantive topic discussions, and conclusion
    - Make it feel like a real conversation with agreements, disagreements, and insights

    FORMAT (exactly like this):
    Alex: [dialogue]
    Sam: [dialogue]

    Begin the script now:"""

            return self.chat_assistant.generate_response(prompt)

        except Exception as e:
            print(f"❌ Fallback script generation failed: {e}")
            return "Alex: Welcome to today's podcast.\nSam: Thanks for joining us for our tech and business update."

    def _generate_scripts_concurrent(self, daily_summary: str, script_config: Dict,
                                     session=None, progress_callback=None) -> Tuple[str, List[str], str]:
        """Generate intro, article, and closing scripts using true concurrency with ThreadPoolExecutor"""

        print("🎙️ Starting concurrent script generation with simple prompts...")

        # Configure threading for M4 chip (optimal for AI API calls)
        MAX_WORKERS = 5  # Optimal for M4: allows intro + 3 articles + closing concurrently

        intro_script = ""
        article_scripts = []
        closing_script = ""

        # Use ThreadPoolExecutor for true concurrency
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS,
                                                   thread_name_prefix="ScriptGen") as executor:
            try:
                # Submit all script generation tasks concurrently
                future_to_task = {}

                # Task 1: Generate intro script
                self._update_progress(session, '📝 Generating introduction', 25, progress_callback)
                intro_future = executor.submit(self._generate_intro_script_task, daily_summary)
                future_to_task[intro_future] = "intro"

                # Task 2: Generate article scripts (can run in parallel)
                articles = self._get_articles_for_scripts()
                article_futures = []

                for i, article in enumerate(articles):
                    self._update_progress(session, f'📰 Generating script for article {i + 1}/{len(articles)}...',
                                          28 + (i * 2), progress_callback)
                    article_future = executor.submit(self._generate_article_script_task, article, i + 1)
                    article_futures.append(article_future)
                    future_to_task[article_future] = f"article_{i + 1}"

                # Task 3: Generate closing script
                self._update_progress(session, '🎬 Generating closing script...', 40, progress_callback)
                closing_future = executor.submit(self._generate_closing_script_task, daily_summary)
                future_to_task[closing_future] = "closing"

                # Collect results as they complete
                completed_tasks = 0
                total_tasks = 2 + len(article_futures)  # intro + articles + closing

                # Process completed futures as they finish
                for future in concurrent.futures.as_completed(future_to_task):
                    task_name = future_to_task[future]
                    completed_tasks += 1

                    try:
                        result = future.result(timeout=60)  # 60 second timeout per task

                        # Handle different task types
                        if task_name == "intro":
                            intro_script = result

                        elif task_name.startswith("article_"):
                            article_num = int(task_name.split("_")[1])
                            # Store with index for proper ordering
                            while len(article_scripts) < article_num:
                                article_scripts.append("")
                            article_scripts[article_num - 1] = result

                        elif task_name == "closing":
                            closing_script = result

                    except concurrent.futures.TimeoutError:
                        print(f"❌ {task_name} script generation timed out")
                        self._handle_script_task_failure(task_name, "timeout",
                                                         intro_script, article_scripts, closing_script)

                    except Exception as e:
                        print(f"❌ {task_name} script generation failed: {e}")
                        self._handle_script_task_failure(task_name, str(e),
                                                         intro_script, article_scripts, closing_script)

            except Exception as e:
                print(f"❌ ThreadPoolExecutor error: {e}")
                # Fall back to sequential generation
                return self._generate_scripts_sequential_fallback(daily_summary, script_config,
                                                                  session, progress_callback)

        # Validate and clean up results
        intro_script, article_scripts, closing_script = self._validate_concurrent_results(
            intro_script, article_scripts, closing_script)

        print("✅ All script generations completed sequentially")
        print(f"   📝 Intro: {len(intro_script)} chars")
        print(f"   📰 Articles: {len(article_scripts)} scripts")
        print(f"   🎬 Closing: {len(closing_script)} chars")

        return intro_script, article_scripts, closing_script

    def _generate_intro_script_task(self, daily_summary: str) -> str:
        """Thread-safe task for generating intro script"""
        try:
            prompt = f"""Generate a natural, engaging 400-500 word podcast introduction between two hosts named Alex and Sam.

    DAILY SUMMARY TO DISCUSS:
    {daily_summary}

    INTRO REQUIREMENTS:
    - Alex is female, tech-focused; Sam is male, business-focused
    - Welcome listeners and brief banter between hosts
    - Overview of what will be covered today
    - Set expectations for the episode
    - Natural conversation style

    FORMAT (exactly like this):
    Alex: [dialogue]
    Sam: [dialogue]

    Begin the intro now:"""

            result = self.chat_assistant.generate_response(prompt)

            if not result or not isinstance(result, str):
                return "Alex: Welcome to today's tech and business podcast!\nSam: Thanks for joining us today."

            return result

        except Exception as e:
            print(f"❌ Intro script generation failed: {e}")
            return "Alex: Welcome to today's tech and business podcast!\nSam: Thanks for joining us today."

    def _generate_article_script_task(self, article: Dict, article_num: int) -> str:
        """Thread-safe task for generating individual article script"""
        try:
            prompt = f"""Generate a natural, engaging 250-300 word podcast discussion between Alex and Sam about this specific article.

    ARTICLE TO DISCUSS:
    Title: {article.get('title', 'No title')}
    Summary: {article.get('summary', 'No summary available')}
    Source: {article.get('source', 'Unknown source')}

    DISCUSSION REQUIREMENTS:
    - Alex (female, tech-focused) and Sam (male, business-focused)
    - What this article means for the industry
    - Why it's significant or trending
    - Practical implications for listeners
    - Natural conversation with agreements and insights

    FORMAT (exactly like this):
    Alex: [dialogue]
    Sam: [dialogue]

    Begin the discussion now:"""

            result = self.chat_assistant.generate_response(prompt)

            if not result or not isinstance(result, str):
                return f"Alex: Let's discuss this article about {article.get('title', 'current events')}.\nSam: This is definitely an interesting development."

            return result

        except Exception as e:
            print(f"❌ Article {article_num} script generation failed: {e}")
            return f"Alex: Let's discuss today's article {article_num}.\nSam: This is definitely worth exploring."

    def _generate_closing_script_task(self, daily_summary: str) -> str:
        """Thread-safe task for generating closing script"""
        try:
            prompt = f"""Generate a natural, engaging 250-300 word podcast conclusion between Alex and Sam.

    DAILY SUMMARY CONTEXT:
    {daily_summary}

    CLOSING REQUIREMENTS:
    - Alex (female, tech-focused) and Sam (male, business-focused)
    - Recap key insights from today's discussion
    - Thank listeners for tuning in
    - Tease tomorrow's content or encourage engagement
    - Natural conversation style with warm conclusion

    FORMAT (exactly like this):
    Alex: [dialogue]
    Sam: [dialogue]

    Begin the closing now:"""

            result = self.chat_assistant.generate_response(prompt)

            if not result or not isinstance(result, str):
                return "Alex: That's a wrap for today's episode!\nSam: Thanks for listening, see you next time!"

            return result

        except Exception as e:
            print(f"❌ Closing script generation failed: {e}")
            return "Alex: That's a wrap for today's episode!\nSam: Thanks for listening, see you next time!"

    def _handle_script_task_failure(self, task_name: str, error: str,
                                    intro_script: str, article_scripts: List[str],
                                    closing_script: str) -> None:
        """Handle individual script task failures with appropriate fallbacks"""
        print(f"🔄 Handling {task_name} task failure: {error}")

        if task_name == "intro" and not intro_script:
            intro_script = "Alex: Welcome to today's tech and business update!\nSam: Great to have you with us today."

        elif task_name.startswith("article_"):
            article_num = int(task_name.split("_")[1])
            while len(article_scripts) < article_num:
                article_scripts.append("")
            if not article_scripts[article_num - 1]:
                article_scripts[
                    article_num - 1] = f"Alex: Let's discuss today's article {article_num}.\nSam: This is definitely worth exploring."

        elif task_name == "closing" and not closing_script:
            closing_script = "Alex: That's all for today's episode.\nSam: Thanks for listening, see you next time!"

    def _validate_concurrent_results(self, intro_script: str, article_scripts: List[str],
                                     closing_script: str) -> Tuple[str, List[str], str]:
        """Validate and clean up results from concurrent script generation"""

        # Validate intro script
        if not intro_script or not isinstance(intro_script, str):
            print("⚠️ Intro script validation failed, using fallback")
            intro_script = "Alex: Welcome to today's tech and business update!\nSam: Great to have you with us today."

        # Validate article scripts (remove empty entries, ensure we have at least one)
        validated_articles = [script for script in article_scripts if script and isinstance(script, str)]
        if not validated_articles:
            print("⚠️ No valid article scripts found, using fallback")
            validated_articles = [
                "Alex: Let's dive into today's key developments.\nSam: There's certainly a lot happening."]

        # Validate closing script
        if not closing_script or not isinstance(closing_script, str):
            print("⚠️ Closing script validation failed, using fallback")
            closing_script = "Alex: That's all for today's episode.\nSam: Thanks for listening, see you next time!"

        return intro_script, validated_articles, closing_script

    def _generate_scripts_sequential_fallback(self, daily_summary: str, script_config: Dict,
                                              session=None, progress_callback=None) -> Tuple[str, List[str], str]:
        """Fallback to sequential processing if concurrent method fails"""
        print("🔄 Falling back to sequential script generation...")

        intro_script = ""
        article_scripts = []
        closing_script = ""

        try:
            # Sequential intro
            self._update_progress(session, '📝 Generating introduction', 25, progress_callback)
            intro_script = self._generate_intro_script_task(daily_summary)

            # Sequential articles
            articles = self._get_articles_for_scripts()
            for i, article in enumerate(articles):
                self._update_progress(session, f'📰 Generating script for article {i + 1}/{len(articles)}...',
                                      28 + (i * 2), progress_callback)
                script = self._generate_article_script_task(article, i + 1)
                article_scripts.append(script)

            # Sequential closing
            self._update_progress(session, '🎬 Generating closing script...', 40, progress_callback)
            closing_script = self._generate_closing_script_task(daily_summary)

        except Exception as e:
            print(f"❌ Sequential fallback also failed: {e}")
            # Use hard-coded fallbacks
            intro_script = "Alex: Welcome to today's tech and business update!\nSam: Great to have you with us today."
            article_scripts = [
                "Alex: Let's dive into today's key developments.\nSam: There's certainly a lot happening."]
            closing_script = "Alex: That's all for today's episode.\nSam: Thanks for listening, see you next time!"

        return intro_script, article_scripts, closing_script

    def _assemble_script_with_transitions(self, intro_script: str, article_scripts: List[str],
                                          closing_script: str, script_config: Dict) -> str:
        """Generate transitions and assemble final script"""
        try:
            print("🔄 Generating transitions and assembling final script...")

            # Build transition prompts using shared config
            character_context = self._build_character_context(script_config)

            transitions = []

            # Generate intro-to-articles transition
            intro_transition = self._generate_transition(
                intro_script, article_scripts[0] if article_scripts else "",
                "introduction", "first article discussion", character_context
            )

            # Generate article-to-article transitions
            article_transitions = []
            for i in range(len(article_scripts) - 1):
                transition = self._generate_transition(
                    article_scripts[i], article_scripts[i + 1],
                    f"article {i + 1}", f"article {i + 2}", character_context
                )
                article_transitions.append(transition)

            # Generate articles-to-closing transition
            closing_transition = self._generate_transition(
                article_scripts[-1] if article_scripts else intro_script, closing_script,
                "final article discussion", "closing segment", character_context
            )

            # Assemble final script
            final_script_parts = []

            # Add intro
            final_script_parts.append(intro_script.strip())

            # Add intro-to-articles transition
            if intro_transition:
                final_script_parts.append(intro_transition.strip())

            # Add articles with transitions between them
            for i, article_script in enumerate(article_scripts):
                final_script_parts.append(article_script.strip())

                # Add transition to next article (if not the last one)
                if i < len(article_scripts) - 1 and i < len(article_transitions):
                    final_script_parts.append(article_transitions[i].strip())

            # Add articles-to-closing transition
            if closing_transition:
                final_script_parts.append(closing_transition.strip())

            # Add closing
            final_script_parts.append(closing_script.strip())

            # Join all parts with double newlines for readability
            final_script = "\n\n".join(final_script_parts)

            print(f"✅ Final script assembled: {len(final_script)} characters")
            print(f"   📊 Total segments: {len(final_script_parts)}")

            return final_script

        except Exception as e:
            print(f"❌ Error assembling script with transitions: {e}")
            # Fallback: simple concatenation without transitions
            fallback_parts = [intro_script]
            fallback_parts.extend(article_scripts)
            fallback_parts.append(closing_script)
            return "\n\n".join(part.strip() for part in fallback_parts if part)

    def _generate_transition(self, from_script: str, to_script: str, from_type: str,
                             to_type: str, character_context: str) -> str:
        """Generate a smooth transition between script segments"""
        try:
            # Extract last few lines from previous segment and first few from next
            from_lines = from_script.strip().split('\n')[-3:] if from_script else []
            to_lines = to_script.strip().split('\n')[:3] if to_script else []

            from_context = "\n".join(from_lines) if from_lines else "Previous segment ending"
            to_context = "\n".join(to_lines) if to_lines else "Next segment beginning"

            prompt = f"""Generate a brief, natural transition between podcast segments.

    {character_context}

    TRANSITION FROM: {from_type}
    Previous segment ending:
    {from_context}

    TRANSITION TO: {to_type}  
    Next segment beginning:
    {to_context}

    REQUIREMENTS:
    - 2-3 lines maximum (30-50 words total)
    - Natural conversation flow between Alex and Sam
    - Smooth bridge from previous topic to next topic
    - Don't repeat content from either segment
    - Keep it conversational and brief

    Generate only the transition dialogue:"""

            transition = self.chat_assistant.generate_response(prompt)

            # Clean up the transition (remove extra whitespace, ensure proper format)
            transition = transition.strip()
            if transition and not transition.startswith(('Alex:', 'Sam:')):
                transition = f"Alex: {transition}"

            print(f"✅ Generated transition: {from_type} → {to_type}")
            return transition

        except Exception as e:
            print(f"❌ Error generating transition {from_type} → {to_type}: {e}")
            return f"Alex: Let's move on to our next topic.\nSam: Absolutely, this next one is interesting."

    def _measure_audio_duration(self, audio_filename: str) -> str:
        """Measure the duration of the generated audio file using pydub"""
        try:
            if not PYDUB_AVAILABLE:
                print("⚠️ PyDub not available, cannot measure audio duration")
                return "Unknown"
            
            audio_path = os.path.join(self.pcast_directory, audio_filename)
            
            if not os.path.exists(audio_path):
                print(f"⚠️ Audio file not found: {audio_path}")
                return "Unknown"
            
            # Load audio file and get duration
            audio = AudioSegment.from_file(audio_path)
            duration_seconds = len(audio) / 1000.0
            
            # Convert to MM:SS format
            minutes = int(duration_seconds // 60)
            seconds = int(duration_seconds % 60)
            duration_formatted = f"{minutes}:{seconds:02d}"
            
            print(f"📏 Measured audio duration: {duration_formatted} ({duration_seconds:.1f}s)")
            return duration_formatted
            
        except Exception as e:
            print(f"❌ Error measuring audio duration: {e}")
            return "Unknown"
    
    def _generate_podcast_metadata(self, script: str) -> Dict[str, str]:
        """Generate catchy title and description for the podcast"""
        try:
            prompt = f"""Analyze this podcast script and generate marketing metadata:

SCRIPT TO ANALYZE:
{script}

Generate the following:
1. TITLE: Create a catchy, attention-grabbing title that is a phrase of 7 words or less. Make it compelling enough that people would want to click and listen. Focus on the most interesting or surprising topic discussed.

2. DESCRIPTION: Write three sentences that summarize the podcast in an engaging way. Make it compelling and highlight what listeners will learn or find interesting.

Requirements:
- Title must be a complete phrase designed to attract attention, no more than 7 words
- Description must be exactly 3 sentences which accurately represent some of the key topics
- Both should be accurate to the content but designed to attract attention
- Focus on the most newsworthy or interesting topics discussed

Format your response exactly like this:
TITLE: [your title here]
DESCRIPTION: [your one sentence description here]"""

            response = self.chat_assistant.generate_response(prompt)
            
            # Parse the response
            metadata = {}
            lines = response.strip().split('\n')
            
            for line in lines:
                if line.startswith('TITLE:'):
                    metadata['title'] = line.replace('TITLE:', '').strip()
                elif line.startswith('DESCRIPTION:'):
                    metadata['description'] = line.replace('DESCRIPTION:', '').strip()
            
            # Fallback defaults if parsing failed
            if 'title' not in metadata:
                metadata['title'] = "Daily Tech News"
            if 'description' not in metadata:
                metadata['description'] = "Alex and Sam discuss today's most important technology and business developments."
            
            return metadata
            
        except Exception as e:
            print(f"❌ Error generating metadata: {e}")
            return {
                'title': 'Daily Tech News',
                'description': 'Alex and Sam discuss today\'s most important technology and business developments.'
            }
    
    def _create_enhanced_script_file(self, script_path: str, original_script: str, 
                                   metadata: Dict[str, str], duration: str, date_str: str):
        """Create enhanced script file with metadata and full script"""
        try:
            # Format date for display
            date_obj = datetime.strptime(date_str, "%Y%m%d")
            formatted_date = date_obj.strftime("%Y-%m-%d")
            
            # Create enhanced content
            enhanced_content = f"""Title: {metadata.get('title', 'Daily Tech News')}
Description: {metadata.get('description', 'Alex and Sam discuss today\'s most important developments.')}
Length: {duration}
Date: {formatted_date}

Script:
{original_script}"""
            
            # Write to file
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(enhanced_content)
            
            print(f"📄 Enhanced script file created with metadata")
            
        except Exception as e:
            print(f"❌ Error creating enhanced script file: {e}")
            # Fallback: keep original script if enhancement fails
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(original_script)
    
    def _parse_script_by_speaker(self, script):
        """Parse script text to identify speaker lines and create dialogue sequence"""
        dialogue_pieces = []
        sequence_order = 0
        
        lines = script.split('\n')
        current_speaker = None
        current_text = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Detect speaker changes
            if line.startswith('Alex:') or line.startswith('Sam:'):
                # Save previous speaker's dialogue
                if current_speaker and current_text:
                    dialogue_pieces.append(DialoguePiece(current_speaker, current_text, sequence_order))
                    sequence_order += 1
                
                # Start new speaker's dialogue
                if line.startswith('Alex:'):
                    current_speaker = 'Alex'
                    current_text = line[5:].strip()
                elif line.startswith('Sam:'):
                    current_speaker = 'Sam'
                    current_text = line[4:].strip()
            
            elif current_speaker:
                # Continue current speaker's dialogue
                current_text += " " + line
            
            else:
                # Handle lines without speaker labels (treat as Alex)
                if line and not line.startswith('---'):
                    dialogue_pieces.append(DialoguePiece('Alex', line, sequence_order))
                    sequence_order += 1
        
        # Don't forget the last piece
        if current_speaker and current_text:
            dialogue_pieces.append(DialoguePiece(current_speaker, current_text, sequence_order))
        
        print(f"📝 Parsed script into {len(dialogue_pieces)} dialogue pieces")
        for i, piece in enumerate(dialogue_pieces[:3]):
            print(f"   {i+1}. {piece.speaker}: {piece.text[:60]}...")
        
        return dialogue_pieces
    
    def _generate_speaker_audio(self, text, voice, output_path, session=None):
        """Generate audio using edge-tts for a specific speaker"""
        try:
            if not self.edge_tts_available:
                raise Exception("edge-tts not available")

            clean_text = self._clean_text_for_tts(text)
            ssml_text = self._add_conversational_ssml(clean_text)
            if not clean_text.strip():
                return False
            
            cmd = [
                'edge-tts',
                '--voice', voice,
                '--rate', '+10%',
                '--text', clean_text,
                '--write-media', output_path,
                '--write-subtitles', output_path.replace('.mp3', '.vtt')
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.pcast_directory
            )
            
            if result.returncode != 0:
                print(f"❌ edge-tts failed: {result.stderr}")
                return False
            
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                if file_size > 1000:
                    return True
                else:
                    print(f"⚠️ Generated audio file too small: {file_size} bytes")
                    return False
            else:
                print(f"❌ Audio file not created: {output_path}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"❌ edge-tts timed out for voice {voice}")
            return False
        except Exception as e:
            print(f"❌ Error generating audio with edge-tts: {e}")
            return False

    def _clean_text_for_tts(self, text):
        """Clean text and add SSML markup for natural, conversational TTS - FIXED VERSION"""
        import re

        try:
            # FIXED: Only apply regex substitution if pattern actually matches
            # Remove markdown formatting - check if patterns exist first
            if re.search(r'\*\*[^*]+\*\*', text):
                text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

            if re.search(r'\*[^*]+\*', text):
                text = re.sub(r'\*([^*]+)\*', r'\1', text)

            if re.search(r'`[^`]+`', text):
                text = re.sub(r'`([^`]+)`', r'\1', text)

            # Remove URLs - safe operation (no backreferences)
            text = re.sub(r'https?://[^\s]+', '', text)

            # Normalize whitespace - safe operation
            text = re.sub(r'\s+', ' ', text)
            text = text.strip()

            # Ensure proper sentence ending
            if text and not text.endswith(('.', '!', '?')):
                text += '.'

            return text

        except Exception as e:
            # Fallback: return original text if cleaning fails
            print(f"⚠️ Text cleaning failed, using original: {e}")
            return text.strip() if text else ""

    def _add_conversational_ssml(self, text):
        """Add SSML markup for natural, conversational speech patterns"""
        import re

        # Start with SSML wrapper
        ssml = '<speak>'

        # Add overall prosody for natural speech rate and pitch variation
        ssml += '<prosody rate="medium" pitch="medium" volume="medium">'

        # Split into sentences for individual processing
        sentences = re.split(r'([.!?]+)', text)

        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i].strip()
            punctuation = sentences[i + 1] if i + 1 < len(sentences) else '.'

            if not sentence:
                continue

            # Process sentence for conversational markers
            processed_sentence = self._process_sentence_for_speech(sentence, punctuation)
            ssml += processed_sentence

            # Add natural pauses between sentences
            if punctuation in '.!':
                ssml += '<break time="500ms"/>'
            elif punctuation == '?':
                ssml += '<break time="600ms"/>'

        ssml += '</prosody>'
        ssml += '</speak>'

        return ssml

    def _process_sentence_for_speech(self, sentence, punctuation):
        """Process individual sentences for natural speech patterns"""
        import re

        # Detect and emphasize transition words
        transition_words = [
            'well', 'actually', 'you know', 'so', 'now', 'however',
            'but', 'and', 'plus', 'also', 'meanwhile', 'furthermore'
        ]

        # Detect and emphasize important terms
        emphasis_patterns = [
            r'\b(\d+(?:,\d{3})*(?:\.\d+)?)\b',  # Numbers
            r'\b([A-Z]{2,})\b',  # Acronyms
            r'\b(percent|percentage|billion|million|thousand)\b'  # Scale words
        ]

        processed = sentence

        # Add emphasis to transition words at sentence start
        for word in transition_words:
            pattern = rf'\b({word})\b'
            if re.match(pattern, processed.lower()):
                processed = re.sub(pattern, r'<emphasis level="moderate">\1</emphasis>', processed, 1, re.IGNORECASE)
                processed = '<break time="200ms"/>' + processed
                break

        # Add emphasis to numbers and important terms
        for pattern in emphasis_patterns:
            processed = re.sub(pattern, r'<emphasis level="strong">\1</emphasis>', processed, flags=re.IGNORECASE)

        # Handle questions with rising intonation
        if punctuation == '?':
            processed = f'<prosody pitch="+10%">{processed}</prosody>'

        # Handle exclamations with emphasis
        elif punctuation == '!':
            processed = f'<prosody volume="+20%" pitch="+5%">{processed}</prosody>'

        return processed + punctuation
    
    def _combine_audio_clips(self, dialogue_pieces, final_path, session=None, progress_callback: Optional[Callable] = None):
        """Combine individual audio clips into final podcast"""
        if not PYDUB_AVAILABLE:
            raise Exception("pydub not available for audio assembly")
        
        try:
            print(f"🎵 Combining {len(dialogue_pieces)} audio clips...")
            
            final_audio = AudioSegment.empty()
            last_speaker = None
            
            for i, piece in enumerate(dialogue_pieces):
                if not piece.audio_file or not os.path.exists(piece.audio_file):
                    print(f"⚠️ Skipping missing audio file for piece {i+1}")
                    continue
                
                try:
                    audio_clip = AudioSegment.from_mp3(piece.audio_file)
                    
                    # Add pauses between speakers and sentences
                    if last_speaker and last_speaker != piece.speaker:
                        pause = AudioSegment.silent(duration=int(self.SPEAKER_PAUSE_DURATION * 1000))
                        final_audio += pause
                    elif last_speaker == piece.speaker:
                        pause = AudioSegment.silent(duration=int(self.SENTENCE_PAUSE_DURATION * 1000))
                        final_audio += pause
                    
                    final_audio += audio_clip
                    last_speaker = piece.speaker
                    
                    if session:
                        progress = 60 + (10 * (i + 1) / len(dialogue_pieces))
                        self._update_progress(session, f'Combining audio: {i+1}/{len(dialogue_pieces)}', progress, progress_callback)
                    
                    print(f"   Added clip {i+1}: {piece.speaker} ({len(audio_clip)/1000:.1f}s)")
                    
                except Exception as e:
                    print(f"❌ Error loading audio clip {i+1}: {e}")
                    continue
            
            if len(final_audio) == 0:
                raise Exception("No audio clips could be combined")
            
            print(f"💾 Exporting final audio to {final_path}...")
            final_audio.export(final_path, format="wav")
            
            if os.path.exists(final_path):
                file_size = os.path.getsize(final_path)
                duration = len(final_audio) / 1000.0
                print(f"✅ Final podcast: {os.path.basename(final_path)} ({file_size} bytes, {duration:.1f}s)")
                return True
            else:
                raise Exception("Final audio file was not created")
                
        except Exception as e:
            print(f"❌ Error combining audio clips: {e}")
            raise
    
    def _cleanup_temp_files(self, temp_dir):
        """Clean up temporary audio files"""
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                print(f"🧹 Cleaned up temporary directory: {temp_dir}")
        except Exception as e:
            print(f"⚠️ Error cleaning up temp files: {e}")
    
    def _convert_to_audio_enhanced(self, script, date_str, session, progress_callback: Optional[Callable] = None):
        """Enhanced audio conversion using Edge-TTS with distinct voices"""
        temp_dir = None
        
        try:
            print(f"🎤 Starting enhanced audio conversion for {date_str}")
            
            self._update_progress(session, 'Parsing script by speaker...', 52, progress_callback)
            dialogue_pieces = self._parse_script_by_speaker(script)
            
            if not dialogue_pieces:
                raise Exception("No dialogue pieces found in script")
            
            if not self.edge_tts_available or not PYDUB_AVAILABLE:
                print("⚠️ Falling back to legacy TTS method")
                return self._convert_to_audio_legacy(script, date_str)
            
            temp_dir = tempfile.mkdtemp(prefix=f"podcast_{date_str}_")
            print(f"📁 Using temporary directory: {temp_dir}")
            
            self._update_progress(session, 'Generating individual voice clips...', 55, progress_callback)
            successful_clips = 0
            
            for i, piece in enumerate(dialogue_pieces):
                voice = self.VOICE_CONFIG.get(piece.speaker, self.VOICE_CONFIG['Alex'])
                clip_filename = f"clip_{i:03d}_{piece.speaker.lower()}.mp3"
                clip_path = os.path.join(temp_dir, clip_filename)
                
                if self._generate_speaker_audio(piece.text, voice, clip_path, session):
                    piece.audio_file = clip_path
                    successful_clips += 1
                else:
                    print(f"❌ Failed to generate audio for piece {i+1}")
                
                progress = 55 + (10 * (i + 1) / len(dialogue_pieces))
                self._update_progress(session, f'Generated voice clip {i+1}/{len(dialogue_pieces)}', progress, progress_callback)
            
            if successful_clips == 0:
                raise Exception("No audio clips were successfully generated")
            
            print(f"✅ Successfully generated {successful_clips}/{len(dialogue_pieces)} audio clips")
            
            self._update_progress(session, 'Combining audio clips into final podcast...', 65, progress_callback)
            
            audio_filename = f"podcast_{date_str}.mp3"
            final_audio_path = os.path.join(self.pcast_directory, audio_filename)
            
            self._combine_audio_clips(dialogue_pieces, final_audio_path, session, progress_callback)
            
            self._cleanup_temp_files(temp_dir)
            
            return audio_filename
            
        except Exception as e:
            print(f"❌ Enhanced audio conversion failed: {e}")
            
            if temp_dir:
                self._cleanup_temp_files(temp_dir)
            
            try:
                print("🔄 Attempting legacy TTS fallback...")
                return self._convert_to_audio_legacy(script, date_str)
            except Exception as fallback_error:
                print(f"❌ Legacy fallback also failed: {fallback_error}")
                return self._create_error_fallback(script, date_str, str(e))
    
    def _convert_to_audio_legacy(self, script, date_str):
        """Legacy audio conversion method (simplified system TTS fallback)"""
        legacy_temp_dir = None
        try:
            print("🎤 Using legacy TTS method...")
            
            import platform
            legacy_temp_dir = tempfile.mkdtemp(prefix=f"podcast_legacy_{date_str}_")
            audio_filename = f"podcast_{date_str}.mp3"
            audio_path = os.path.join(self.pcast_directory, audio_filename)
            
            clean_script = self._extract_clean_text(script)
            
            if platform.system() == "Darwin":  # macOS
                print("🍎 Using macOS system TTS...")
                
                temp_script_path = os.path.join(legacy_temp_dir, f"temp_script_{date_str}.txt")
                with open(temp_script_path, 'w', encoding='utf-8') as f:
                    f.write(clean_script)
                
                result = subprocess.run([
                    'say',
                    '-f', temp_script_path,
                    '-o', audio_path.replace('.mp3', '.aiff'),
                    '--data-format=LEF32@22050'
                ], capture_output=True, text=True, timeout=120)

                aiff_path = audio_path.replace('.mp3', '.aiff')

                # Check if file was created (say can return non-zero but still produce output)
                if result.returncode != 0:
                    print(f"⚠️ say returned code {result.returncode}: {result.stderr}")

                if os.path.exists(aiff_path) and os.path.getsize(aiff_path) > 0:
                    if shutil.which('ffmpeg'):
                        subprocess.run(['ffmpeg', '-y', '-i', aiff_path, audio_path], check=True)
                        os.remove(aiff_path)
                    else:
                        audio_filename = f"podcast_{date_str}.aiff"

                    if legacy_temp_dir:
                        self._cleanup_temp_files(legacy_temp_dir)

                    return audio_filename
            
            raise Exception("System TTS not available or failed")
            
        except Exception as e:
            print(f"❌ Legacy TTS failed: {e}")
            return self._create_error_fallback(script, date_str, str(e))
    
    def _extract_clean_text(self, script):
        """Extract clean text from script for legacy TTS"""
        clean_text = ""
        for line in script.split('\n'):
            line = line.strip()
            if line:
                if line.startswith('Alex:') or line.startswith('Sam:'):
                    dialogue = line.split(':', 1)[1].strip()
                    if dialogue:
                        clean_text += dialogue + " "
                else:
                    clean_text += line.strip() + " "
        return clean_text
    
    def _create_error_fallback(self, script, date_str, error_msg):
        """Create enhanced script file as final fallback when audio generation fails"""
        fallback_filename = f"podcast_{date_str}_script_only.txt"
        fallback_path = os.path.join(self.pcast_directory, fallback_filename)
        
        with open(fallback_path, 'w', encoding='utf-8') as f:
            f.write(f"DAILY PODCAST SCRIPT - {date_str}\n")
            f.write("=" * 60 + "\n\n")
            f.write("AUDIO GENERATION FAILED - SCRIPT ONLY\n")
            f.write(f"Error: {error_msg}\n\n")
            f.write("To create audio manually:\n")
            f.write("1. Install edge-tts: pip install edge-tts pydub\n")
            f.write("2. Copy the script below\n")
            f.write("3. Use text-to-speech software of your choice\n\n")
            f.write("VOICE ASSIGNMENTS:\n")
            f.write("- Alex (female): en-US-AriaNeural\n")
            f.write("- Sam (male): en-US-ChristopherNeural\n\n")
            f.write("SCRIPT:\n")
            f.write("-" * 40 + "\n\n")
            f.write(script)
            f.write(f"\n\n--- Generated at {datetime.now().isoformat()} ---")
        
        print(f"📄 Enhanced script file created: {fallback_filename}")
        return fallback_filename

    def _update_progress(self, session, message, percentage, progress_callback: Optional[Callable] = None):
        """Update session progress with optional callback to chain manager"""
        session.progress = {
            'status': session.status,
            'message': message,
            'percentage': percentage,
            'timestamp': datetime.now().isoformat(),
            'completed': session.completed,
            'error': session.error,
            'files_generated': session.files_generated
        }

        # Send progress update via callback if available (for chain context)
        if progress_callback:
            try:
                current_status = session.status if session.status != 'running' else 'running'
                progress_callback(session.session_id, percentage, message, current_status)
                print(f"📊 Progress sent to chain manager: {percentage}% - {message}")
            except Exception as e:
                print(f"⚠️ Warning: Could not send progress to chain manager: {e}")

        # Add this section - Send to frontend message queue if available
        if self.progress_callback:
            try:
                progress_msg = f"🎙️ **{message}** ({int(percentage)}%)"
                self.progress_callback(progress_msg, 'progress')
            except Exception as e:
                print(f"⚠️ Warning: Could not send progress to frontend: {e}")

        # Always keep terminal logging for debugging
        print(f"🎙️ Podcast Progress: {message}")
    
    def get_progress(self, session_id):
        """Get current progress for a podcast generation session"""
        session = self.sessions.get(session_id)
        if not session:
            return None
        
        return {
            'session_id': session_id,
            'status': session.status,
            'progress': session.progress,
            'completed': session.completed,
            'error': session.error,
            'files_generated': session.files_generated,
            'context': {
                'has_events_data': 'events_data' in session.context,
                'edge_tts_available': self.edge_tts_available,
                'pydub_available': PYDUB_AVAILABLE
            }
        }

    def _notify_chain_completion(self, session_id: str, podcast_files: Dict, 
                               progress_callback: Optional[Callable] = None):
        """
        Notify chain manager that podcast generation completed as part of chain workflow
        
        Args:
            session_id: Session identifier  
            podcast_files: Generated podcast files
            progress_callback: Optional callback to notify chain manager
        """
        print(f"🎙️ Podcast generation completed for chain session {session_id}")
        
        try:
            # Store the podcast data in the session context for chain access
            if session_id in self.sessions:
                self.sessions[session_id].files_generated = podcast_files
            
            # Notify chain manager via callback if available
            if progress_callback:
                progress_callback(session_id, 100, 'Podcast generation completed', 'completed')
                print(f"✅ Chain manager notified of podcast completion")
            
            print(f"🎧 Podcast files available for chain continuation:")
            for file_type, filename in podcast_files.items():
                print(f"   {file_type}: {filename}")
            
        except Exception as e:
            print(f"❌ Failed to notify chain of completion: {e}")

# Helper function for external initialization
def create_podcast_generator(content_curator, chat_assistant, pcast_directory="podcasts", research_assistant=None):
    """Factory function to create a podcast generator instance"""
    return PodcastGenerator(content_curator, chat_assistant, pcast_directory, research_assistant)