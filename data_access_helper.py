"""
Data Access Helper for Enhanced Chat System
Lightweight helpers for cross-referencing articles and accessing historical data
"""

import sqlite3
import json
import os
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
import re
from contextlib import contextmanager
import hashlib


class ArticleCrossReferencer:
    """Find connections between articles using existing database capabilities"""
    
    def __init__(self, content_curator):
        self.content_curator = content_curator
        self.db_path = content_curator.db_path
    
    @contextmanager
    def get_db_connection(self):
        """Get database connection with same pattern as content_curator"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.row_factory = sqlite3.Row
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def find_related_articles(self, article_url: str, limit: int = 5) -> List[Dict]:
        """Find related articles using existing database search with relevance scoring"""
        try:
            # Get the source article to extract keywords
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Get the article details
                cursor.execute('''
                    SELECT title, summary, source, relevance_score, published_date,
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content WHERE url = ?
                ''', (article_url,))
                
                source_article = cursor.fetchone()
                if not source_article:
                    return []
                
                # Extract keywords from title and summary for similarity search
                search_keywords = self._extract_keywords(
                    source_article['title'], 
                    source_article['summary'] or ''
                )
                
                if not search_keywords:
                    return self._get_related_by_source(source_article['source'], article_url, limit)
                
                # Find related articles using keyword matching
                related_articles = []
                
                for keyword in search_keywords[:3]:  # Use top 3 keywords
                    search_term = f'%{keyword}%'
                    cursor.execute('''
                        SELECT *, 
                               (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                               COALESCE(relevance_boost, 0) as boost_score
                        FROM content 
                        WHERE url != ? 
                        AND (LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)
                        ORDER BY total_relevance DESC, published_date DESC
                        LIMIT ?
                    ''', (article_url, search_term, search_term, limit * 2))
                    
                    keyword_matches = [dict(row) for row in cursor.fetchall()]
                    related_articles.extend(keyword_matches)
                
                # Remove duplicates and score by relevance
                seen_urls = set()
                unique_related = []
                for article in related_articles:
                    if article['url'] not in seen_urls:
                        seen_urls.add(article['url'])
                        # Add similarity context
                        article['similarity_reason'] = self._get_similarity_reason(
                            source_article, article, search_keywords
                        )
                        unique_related.append(article)
                
                # Sort by total relevance and return top results
                unique_related.sort(key=lambda x: x['total_relevance'], reverse=True)
                return unique_related[:limit]
                
        except Exception as e:
            print(f"Error finding related articles: {e}")
            return []
    
    def _extract_keywords(self, title: str, summary: str) -> List[str]:
        """Extract meaningful keywords from title and summary"""
        try:
            text = f"{title} {summary}".lower()
            
            # Remove common words and extract meaningful terms
            stop_words = {
                'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                'of', 'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during',
                'before', 'after', 'above', 'below', 'between', 'among', 'this', 'that',
                'these', 'those', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 
                'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should',
                'may', 'might', 'must', 'can', 'how', 'what', 'when', 'where', 'why',
                'who', 'which', 'new', 'using', 'study', 'research', 'analysis'
            }
            
            # Extract words and filter
            words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
            keywords = []
            
            for word in words:
                if (word not in stop_words and 
                    len(word) > 2 and 
                    not word.isdigit()):
                    keywords.append(word)
            
            # Count frequency and return most common
            word_counts = {}
            for word in keywords:
                word_counts[word] = word_counts.get(word, 0) + 1
            
            # Sort by frequency and return top keywords
            sorted_keywords = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
            return [word for word, count in sorted_keywords[:10] if count > 1 or word in title.lower()]
            
        except Exception as e:
            print(f"Error extracting keywords: {e}")
            return []
    
    def _get_related_by_source(self, source: str, exclude_url: str, limit: int) -> List[Dict]:
        """Fallback: get articles from same source"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE source = ? AND url != ?
                    ORDER BY total_relevance DESC, published_date DESC
                    LIMIT ?
                ''', (source, exclude_url, limit))
                
                articles = [dict(row) for row in cursor.fetchall()]
                for article in articles:
                    article['similarity_reason'] = f"Same source: {source}"
                
                return articles
                
        except Exception as e:
            print(f"Error getting related by source: {e}")
            return []
    
    def _get_similarity_reason(self, source_article: Dict, related_article: Dict, keywords: List[str]) -> str:
        """Generate a human-readable similarity reason"""
        try:
            title1 = source_article['title'].lower()
            title2 = related_article['title'].lower()
            summary1 = (source_article['summary'] or '').lower()
            summary2 = (related_article['summary'] or '').lower()
            
            # Find matching keywords
            matching_keywords = []
            for keyword in keywords:
                if (keyword in title2 or keyword in summary2):
                    matching_keywords.append(keyword)
            
            if matching_keywords:
                return f"Shares topics: {', '.join(matching_keywords[:3])}"
            elif source_article['source'] == related_article['source']:
                return f"Same source: {source_article['source']}"
            else:
                return "Similar content themes"
                
        except Exception:
            return "Related content"
    
    def get_topic_cluster(self, keywords: List[str], limit: int = 10) -> List[Dict]:
        """Group articles by themes using existing relevance scoring"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Build search query for multiple keywords
                search_conditions = []
                search_params = []
                
                for keyword in keywords:
                    search_term = f'%{keyword.lower()}%'
                    search_conditions.append('(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)')
                    search_params.extend([search_term, search_term])
                
                if not search_conditions:
                    return []
                
                search_params.append(limit)
                
                query = f'''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE {' OR '.join(search_conditions)}
                    ORDER BY total_relevance DESC, published_date DESC
                    LIMIT ?
                '''
                
                cursor.execute(query, search_params)
                articles = [dict(row) for row in cursor.fetchall()]
                
                # Add cluster information
                for article in articles:
                    article['cluster_keywords'] = [
                        kw for kw in keywords 
                        if (kw.lower() in article['title'].lower() or 
                            kw.lower() in (article['summary'] or '').lower())
                    ]
                
                return articles
                
        except Exception as e:
            print(f"Error getting topic cluster: {e}")
            return []
    
    def compare_articles(self, url1: str, url2: str) -> Dict:
        """Generate comparison using existing AI processing"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Get both articles
                cursor.execute('''
                    SELECT title, summary, source, relevance_score, published_date, url
                    FROM content WHERE url IN (?, ?)
                ''', (url1, url2))
                
                articles = [dict(row) for row in cursor.fetchall()]
                
                if len(articles) != 2:
                    return {'error': 'One or both articles not found'}
                
                article1, article2 = articles[0], articles[1]
                
                # Generate comparison using content curator's LLM
                comparison_prompt = f"""Compare these two articles and identify:
1. Key similarities
2. Key differences  
3. Complementary insights
4. Which audience might prefer each

Article 1: {article1['title']}
Source: {article1['source']}
Summary: {article1['summary'] or 'No summary available'}

Article 2: {article2['title']}
Source: {article2['source']}
Summary: {article2['summary'] or 'No summary available'}

Provide a brief, structured comparison."""

                try:
                    import ollama
                    response = ollama.chat(
                        model=self.content_curator.ollama_model,
                        messages=[{'role': 'user', 'content': comparison_prompt}]
                    )
                    
                    return {
                        'article1': article1,
                        'article2': article2,
                        'comparison': response['message']['content'].strip(),
                        'comparison_type': 'ai_generated'
                    }
                    
                except Exception as e:
                    # Fallback to simple comparison
                    return {
                        'article1': article1,
                        'article2': article2,
                        'comparison': self._simple_comparison(article1, article2),
                        'comparison_type': 'rule_based'
                    }
                
        except Exception as e:
            print(f"Error comparing articles: {e}")
            return {'error': f'Comparison failed: {str(e)}'}
    
    def _simple_comparison(self, article1: Dict, article2: Dict) -> str:
        """Simple rule-based comparison fallback"""
        try:
            similarities = []
            differences = []
            
            # Source comparison
            if article1['source'] == article2['source']:
                similarities.append(f"Both from {article1['source']}")
            else:
                differences.append(f"Different sources: {article1['source']} vs {article2['source']}")
            
            # Date comparison
            try:
                date1 = datetime.fromisoformat(article1['published_date'].replace('Z', '+00:00'))
                date2 = datetime.fromisoformat(article2['published_date'].replace('Z', '+00:00'))
                
                if abs((date1 - date2).days) <= 7:
                    similarities.append("Published within a week of each other")
                else:
                    differences.append(f"Different publication periods")
            except:
                pass
            
            # Relevance comparison
            score1 = article1.get('relevance_score', 0)
            score2 = article2.get('relevance_score', 0)
            
            if abs(score1 - score2) <= 1:
                similarities.append("Similar relevance scores")
            else:
                differences.append(f"Different relevance: {score1:.1f} vs {score2:.1f}")
            
            comparison = ""
            if similarities:
                comparison += f"**Similarities:** {'; '.join(similarities)}\n\n"
            if differences:
                comparison += f"**Differences:** {'; '.join(differences)}"
            
            return comparison or "Articles appear to be on different topics"
            
        except Exception:
            return "Unable to generate detailed comparison"
    
    def suggest_follow_up_reading(self, article_url: str, limit: int = 3) -> List[Dict]:
        """Recommend related content based on article analysis"""
        try:
            # Get related articles
            related = self.find_related_articles(article_url, limit * 2)
            
            if not related:
                return []
            
            # Filter and rank for follow-up reading
            follow_ups = []
            
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Get the source article
                cursor.execute('SELECT source, published_date FROM content WHERE url = ?', (article_url,))
                source_article = cursor.fetchone()
                
                if not source_article:
                    return related[:limit]
                
                for article in related:
                    # Prefer newer articles from different sources
                    follow_up_score = article['total_relevance']
                    
                    # Boost articles from different sources
                    if article['source'] != source_article['source']:
                        follow_up_score += 1.0
                    
                    # Boost newer articles
                    try:
                        article_date = datetime.fromisoformat(article['published_date'].replace('Z', '+00:00'))
                        source_date = datetime.fromisoformat(source_article['published_date'].replace('Z', '+00:00'))
                        
                        if article_date > source_date:
                            follow_up_score += 0.5
                    except:
                        pass
                    
                    article['follow_up_score'] = follow_up_score
                    article['follow_up_reason'] = self._get_follow_up_reason(article, source_article)
                    follow_ups.append(article)
                
                # Sort by follow-up score and return top results
                follow_ups.sort(key=lambda x: x['follow_up_score'], reverse=True)
                return follow_ups[:limit]
                
        except Exception as e:
            print(f"Error suggesting follow-up reading: {e}")
            return []
    
    def _get_follow_up_reason(self, article: Dict, source_article: Dict) -> str:
        """Generate reason for follow-up recommendation"""
        try:
            reasons = []
            
            if article['source'] != source_article['source']:
                reasons.append("different perspective")
            
            if article.get('boost_score', 0) > 0:
                reasons.append("trending topic")
            
            if article['total_relevance'] > 8:
                reasons.append("high relevance")
            
            if not reasons:
                reasons.append("related content")
            
            return f"Recommended: {', '.join(reasons)}"
            
        except Exception:
            return "Related follow-up"


class HistoricalDataAccessor:
    """Provide chat access to week reviews and historical analyses"""
    
    def __init__(self, chat_assistant, data_directory: str = "data"):
        self.chat_assistant = chat_assistant
        self.data_directory = data_directory
        self.topics_directory = os.environ.get('TOPICS_DIR', 'currentevents')
        self.podcasts_directory = os.environ.get('PCAST_DIR', 'podcasts')
        self.db_path = chat_assistant.db_path if hasattr(chat_assistant, 'db_path') else None
    
    def query_week_reviews(self, date_range: Tuple[datetime, datetime], topic: Optional[str] = None) -> List[Dict]:
        """Access existing week review files"""
        try:
            start_date, end_date = date_range
            week_review_files = []
            
            # Look for week review files in the date range
            for date in self._date_range(start_date, end_date):
                date_patterns = [
                    f"week_review_{date.strftime('%Y%m%d')}.json",
                    f"weekly_analysis_{date.strftime('%Y%m%d')}.json",
                    f"week_review_{date.strftime('%Y-%m-%d')}.json"
                ]
                
                for pattern in date_patterns:
                    file_path = os.path.join(self.topics_directory, pattern)
                    if os.path.exists(file_path):
                        week_review_files.append(file_path)
            
            reviews = []
            for file_path in week_review_files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        review_data = json.load(f)
                    
                    # Filter by topic if specified
                    if topic:
                        review_content = json.dumps(review_data).lower()
                        if topic.lower() not in review_content:
                            continue
                    
                    review_data['file_path'] = file_path
                    review_data['file_date'] = os.path.getmtime(file_path)
                    reviews.append(review_data)
                    
                except Exception as e:
                    print(f"Error reading week review file {file_path}: {e}")
                    continue
            
            # Sort by date
            reviews.sort(key=lambda x: x.get('file_date', 0), reverse=True)
            return reviews
            
        except Exception as e:
            print(f"Error querying week reviews: {e}")
            return []
    
    def _date_range(self, start_date: datetime, end_date: datetime):
        """Generate date range for file searching"""
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)
    
    def get_trending_topics_history(self, days: int = 7) -> Dict:
        """Analyze hot topics over time using current events data"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Look for current events analysis files
            topic_history = {}
            keyword_trends = {}
            
            for date in self._date_range(start_date, end_date):
                date_str = date.strftime('%Y%m%d')
                analysis_files = glob.glob(
                    os.path.join(self.topics_directory, f"current_events_analysis_{date_str}*.json")
                )
                
                for file_path in analysis_files:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        analysis = data.get('analysis', {})
                        keywords = analysis.get('search_keywords', [])
                        trending_topics = analysis.get('trending_topics', [])
                        
                        # Track keyword frequency over time
                        date_key = date.strftime('%Y-%m-%d')
                        topic_history[date_key] = {
                            'keywords': keywords,
                            'trending_topics': trending_topics,
                            'summary': analysis.get('summary', ''),
                            'file_path': file_path
                        }
                        
                        # Count keyword occurrences
                        for keyword in keywords:
                            if keyword not in keyword_trends:
                                keyword_trends[keyword] = []
                            keyword_trends[keyword].append(date_key)
                            
                    except Exception as e:
                        print(f"Error reading current events file {file_path}: {e}")
                        continue
            
            # Analyze trends
            trending_analysis = self._analyze_keyword_trends(keyword_trends, days)
            
            return {
                'date_range': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
                'daily_topics': topic_history,
                'keyword_trends': keyword_trends,
                'trending_analysis': trending_analysis,
                'total_days_analyzed': len(topic_history)
            }
            
        except Exception as e:
            print(f"Error getting trending topics history: {e}")
            return {}
    
    def _analyze_keyword_trends(self, keyword_trends: Dict, days: int) -> Dict:
        """Analyze keyword trends over time"""
        try:
            analysis = {
                'most_frequent': [],
                'emerging': [],
                'declining': [],
                'consistent': []
            }
            
            for keyword, dates in keyword_trends.items():
                frequency = len(dates)
                
                # Calculate trend direction
                if frequency >= days * 0.6:  # Appears in 60%+ of days
                    analysis['consistent'].append({
                        'keyword': keyword,
                        'frequency': frequency,
                        'percentage': round((frequency / days) * 100, 1)
                    })
                elif frequency >= days * 0.3:  # Appears in 30%+ of days
                    analysis['most_frequent'].append({
                        'keyword': keyword,
                        'frequency': frequency,
                        'percentage': round((frequency / days) * 100, 1)
                    })
                else:
                    # Check if emerging (more recent appearances) or declining
                    recent_appearances = sum(1 for date in dates 
                                           if (datetime.now() - datetime.strptime(date, '%Y-%m-%d')).days <= days // 2)
                    
                    if recent_appearances > frequency // 2:
                        analysis['emerging'].append({
                            'keyword': keyword,
                            'frequency': frequency,
                            'recent_appearances': recent_appearances
                        })
                    else:
                        analysis['declining'].append({
                            'keyword': keyword,
                            'frequency': frequency,
                            'recent_appearances': recent_appearances
                        })
            
            # Sort each category
            analysis['most_frequent'].sort(key=lambda x: x['frequency'], reverse=True)
            analysis['consistent'].sort(key=lambda x: x['frequency'], reverse=True)
            analysis['emerging'].sort(key=lambda x: x['recent_appearances'], reverse=True)
            analysis['declining'].sort(key=lambda x: x['frequency'], reverse=True)
            
            return analysis
            
        except Exception as e:
            print(f"Error analyzing keyword trends: {e}")
            return {}
    
    def find_historical_events(self, query: str, days: int = 30) -> List[Dict]:
        """Search current events analyses for specific topics"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            matching_events = []
            query_lower = query.lower()
            
            # Search through current events files
            for date in self._date_range(start_date, end_date):
                date_str = date.strftime('%Y%m%d')
                analysis_files = glob.glob(
                    os.path.join(self.topics_directory, f"current_events_analysis_{date_str}*.json")
                )
                
                for file_path in analysis_files:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        analysis = data.get('analysis', {})
                        
                        # Check if query matches any content
                        searchable_content = json.dumps(analysis).lower()
                        if query_lower in searchable_content:
                            matching_events.append({
                                'date': date.strftime('%Y-%m-%d'),
                                'analysis': analysis,
                                'file_path': file_path,
                                'relevance_score': self._calculate_event_relevance(analysis, query_lower)
                            })
                            
                    except Exception as e:
                        print(f"Error searching events file {file_path}: {e}")
                        continue
            
            # Sort by relevance and date
            matching_events.sort(key=lambda x: (x['relevance_score'], x['date']), reverse=True)
            return matching_events
            
        except Exception as e:
            print(f"Error finding historical events: {e}")
            return []
    
    def _calculate_event_relevance(self, analysis: Dict, query: str) -> float:
        """Calculate relevance score for historical event match"""
        try:
            score = 0.0
            
            # Check keywords
            keywords = analysis.get('search_keywords', [])
            for keyword in keywords:
                if query in keyword.lower():
                    score += 2.0
            
            # Check trending topics
            trending_topics = analysis.get('trending_topics', [])
            for topic in trending_topics:
                if query in topic.lower():
                    score += 3.0
            
            # Check summary
            summary = analysis.get('summary', '').lower()
            if query in summary:
                score += 1.0
                # Bonus for multiple mentions
                score += summary.count(query) * 0.5
            
            return min(score, 10.0)
            
        except Exception:
            return 0.0
    
    def get_podcast_content(self, date_range: Tuple[datetime, datetime]) -> List[Dict]:
        """Access podcast scripts and metadata"""
        try:
            start_date, end_date = date_range
            podcast_files = []
            
            # Look for podcast files in the date range
            for date in self._date_range(start_date, end_date):
                date_patterns = [
                    f"podcast_{date.strftime('%Y%m%d')}*",
                    f"daily_podcast_{date.strftime('%Y%m%d')}*",
                    f"podcast_{date.strftime('%Y-%m-%d')}*"
                ]
                
                for pattern in date_patterns:
                    files = glob.glob(os.path.join(self.data_directory, pattern))
                    podcast_files.extend(files)
            
            podcasts = []
            for file_path in podcast_files:
                try:
                    file_ext = os.path.splitext(file_path)[1].lower()
                    
                    if file_ext == '.json':
                        # JSON metadata file
                        with open(file_path, 'r', encoding='utf-8') as f:
                            podcast_data = json.load(f)
                        
                        podcast_data['file_path'] = file_path
                        podcast_data['file_type'] = 'metadata'
                        podcasts.append(podcast_data)
                        
                    elif file_ext == '.txt':
                        # Text script file
                        with open(file_path, 'r', encoding='utf-8') as f:
                            script_content = f.read()
                        
                        podcasts.append({
                            'file_path': file_path,
                            'file_type': 'script',
                            'script_content': script_content,
                            'file_date': os.path.getmtime(file_path)
                        })
                        
                    elif file_ext in ['.mp3', '.wav', '.m4a']:
                        # Audio file
                        podcasts.append({
                            'file_path': file_path,
                            'file_type': 'audio',
                            'file_size': os.path.getsize(file_path),
                            'file_date': os.path.getmtime(file_path)
                        })
                        
                except Exception as e:
                    print(f"Error reading podcast file {file_path}: {e}")
                    continue
            
            # Sort by date
            podcasts.sort(key=lambda x: x.get('file_date', 0), reverse=True)
            return podcasts
            
        except Exception as e:
            print(f"Error getting podcast content: {e}")
            return []
    
    def search_historical_data(self, query: str, data_types: List[str] = None, days: int = 30) -> Dict:
        """Unified search across all historical data types"""
        try:
            if data_types is None:
                data_types = ['week_reviews', 'current_events', 'podcasts']
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            date_range = (start_date, end_date)
            
            results = {
                'query': query,
                'date_range': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
                'results': {}
            }
            
            if 'week_reviews' in data_types:
                results['results']['week_reviews'] = self.query_week_reviews(date_range, query)
            
            if 'current_events' in data_types:
                results['results']['current_events'] = self.find_historical_events(query, days)
            
            if 'podcasts' in data_types:
                podcast_content = self.get_podcast_content(date_range)
                # Filter podcasts by query
                matching_podcasts = []
                query_lower = query.lower()
                
                for podcast in podcast_content:
                    searchable_text = json.dumps(podcast).lower()
                    if query_lower in searchable_text:
                        matching_podcasts.append(podcast)
                
                results['results']['podcasts'] = matching_podcasts
            
            # Calculate total matches
            total_matches = sum(len(result_list) for result_list in results['results'].values())
            results['total_matches'] = total_matches
            
            return results
            
        except Exception as e:
            print(f"Error searching historical data: {e}")
            return {'error': f'Historical search failed: {str(e)}'}
    
    def get_data_summary(self, days: int = 7) -> Dict:
        """Get summary of available historical data"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            summary = {
                'date_range': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
                'data_availability': {
                    'week_reviews': 0,
                    'current_events': 0,
                    'podcasts': 0
                },
                'file_counts': {
                    'json_files': 0,
                    'text_files': 0,
                    'audio_files': 0
                }
            }
            
            # Count files by type
            for date in self._date_range(start_date, end_date):
                date_str = date.strftime('%Y%m%d')
                
                # Week reviews
                week_patterns = [f"week_review_{date_str}*", f"weekly_analysis_{date_str}*"]
                for pattern in week_patterns:
                    files = glob.glob(os.path.join(self.topics_directory, pattern))
                    summary['data_availability']['week_reviews'] += len(files)
                
                # Current events
                events_files = glob.glob(os.path.join(self.topics_directory, f"current_events_analysis_{date_str}*"))
                summary['data_availability']['current_events'] += len(events_files)
                
                # Podcasts
                podcast_patterns = [f"podcast_{date_str}*", f"daily_podcast_{date_str}*"]
                for pattern in podcast_patterns:
                    files = glob.glob(os.path.join(self.pcast_directory, pattern))
                    summary['data_availability']['podcasts'] += len(files)
            
            # Count by file extension
            all_files = glob.glob(os.path.join(self.data_directory, "*"))
            for file_path in all_files:
                if os.path.isfile(file_path):
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext == '.json':
                        summary['file_counts']['json_files'] += 1
                    elif ext == '.txt':
                        summary['file_counts']['text_files'] += 1
                    elif ext in ['.mp3', '.wav', '.m4a']:
                        summary['file_counts']['audio_files'] += 1
            
            return summary
            
        except Exception as e:
            print(f"Error getting data summary: {e}")
            return {'error': f'Data summary failed: {str(e)}'}
