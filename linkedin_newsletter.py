#!/usr/bin/env python3
"""
LinkedIn Newsletter Generator - Standalone Script
Generates a LinkedIn-ready daily newsletter from curated content.

Prerequisites:
  - Today's current_events analysis must exist in TOPICS_DIR
  - content_curator.db must have recent articles with boost scores

Usage:
  python linkedin_newsletter.py

Output:
  Saves to: currentevents/linkedin_newsletter_YYYYMMDD.txt
"""

import os
import sys
import json
import sqlite3
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import ollama


# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = os.environ.get('DATA_DIR', 'data')
TOPICS_DIR = os.environ.get('TOPICS_DIR', 'currentevents')
DB_PATH = os.path.join(DATA_DIR, 'content_curator.db')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'granite3.2:8b')


# =============================================================================
# UNICODE TEXT FORMATTING FOR LINKEDIN
# =============================================================================

def to_bold_unicode(text: str) -> str:
    """Convert text to Unicode bold characters for LinkedIn compatibility."""
    # Mapping for bold Unicode characters (Mathematical Bold)
    bold_map = {
        'A': '𝗔', 'B': '𝗕', 'C': '𝗖', 'D': '𝗗', 'E': '𝗘', 'F': '𝗙', 'G': '𝗚',
        'H': '𝗛', 'I': '𝗜', 'J': '𝗝', 'K': '𝗞', 'L': '𝗟', 'M': '𝗠', 'N': '𝗡',
        'O': '𝗢', 'P': '𝗣', 'Q': '𝗤', 'R': '𝗥', 'S': '𝗦', 'T': '𝗧', 'U': '𝗨',
        'V': '𝗩', 'W': '𝗪', 'X': '𝗫', 'Y': '𝗬', 'Z': '𝗭',
        'a': '𝗮', 'b': '𝗯', 'c': '𝗰', 'd': '𝗱', 'e': '𝗲', 'f': '𝗳', 'g': '𝗴',
        'h': '𝗵', 'i': '𝗶', 'j': '𝗷', 'k': '𝗸', 'l': '𝗹', 'm': '𝗺', 'n': '𝗻',
        'o': '𝗼', 'p': '𝗽', 'q': '𝗾', 'r': '𝗿', 's': '𝘀', 't': '𝘁', 'u': '𝘂',
        'v': '𝘃', 'w': '𝘄', 'x': '𝘅', 'y': '𝘆', 'z': '𝘇',
        '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰',
        '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵',
    }
    return ''.join(bold_map.get(c, c) for c in text)


# =============================================================================
# PREFLIGHT CHECKS
# =============================================================================

def check_current_events_analysis() -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Check if today's current_events analysis exists.
    Returns: (exists: bool, filepath: str|None, data: dict|None)
    """
    today = datetime.now().strftime("%Y%m%d")
    pattern = os.path.join(TOPICS_DIR, f"current_events_analysis_{today}*.json")
    files = glob.glob(pattern)
    
    if not files:
        return False, None, None
    
    latest_file = max(files, key=os.path.getctime)
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return True, latest_file, data
    except Exception:
        return False, latest_file, None


def check_database_content(hours: int = 24) -> Tuple[bool, int]:
    """
    Check if database has recent articles.
    Returns: (has_content: bool, article_count: int)
    """
    if not os.path.exists(DB_PATH):
        return False, 0
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        since_date = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        cursor.execute('''
            SELECT COUNT(*) FROM content 
            WHERE added_date > ? OR published_date > ?
        ''', (since_date, since_date))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0, count
    except Exception:
        return False, 0


def run_preflight_checks() -> Tuple[bool, str, Optional[Dict]]:
    """
    Run all preflight checks.
    Returns: (passed: bool, message: str, current_events_data: dict|None)
    """
    # Check current events analysis
    ce_exists, ce_path, ce_data = check_current_events_analysis()
    if not ce_exists:
        return False, "Today's current_events analysis not found. Run the daily chain first.", None
    
    if ce_data is None:
        return False, f"Could not parse current_events file: {ce_path}", None
    
    # Check database content
    has_content, article_count = check_database_content()
    if not has_content:
        return False, "No recent articles found in database. Run content collection first.", None
    
    return True, f"Preflight passed: {article_count} recent articles, current_events analysis loaded.", ce_data


# =============================================================================
# DATA RETRIEVAL
# =============================================================================

def get_top_articles(limit: int = 5, hours: int = 24) -> List[Dict]:
    """
    Retrieve top articles from database sorted by total relevance.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    since_date = (datetime.now() - timedelta(hours=hours)).isoformat()
    
    cursor.execute('''
        SELECT 
            title,
            url,
            source,
            summary,
            relevance_score,
            COALESCE(relevance_boost, 0) as boost_score,
            (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
            published_date
        FROM content 
        WHERE added_date > ? OR published_date > ?
        ORDER BY total_relevance DESC, published_date DESC
        LIMIT ?
    ''', (since_date, since_date, limit))
    
    articles = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return articles


def condense_summary(summary: str, max_sentences: int = 1) -> str:
    """
    Condense a multi-sentence summary to specified number of sentences.
    """
    if not summary:
        return ""
    
    # Split on sentence endings
    sentences = []
    current = ""
    for char in summary:
        current += char
        if char in '.!?' and len(current.strip()) > 10:
            sentences.append(current.strip())
            current = ""
    
    if current.strip():
        sentences.append(current.strip())
    
    # Return first N sentences
    result = ' '.join(sentences[:max_sentences])
    
    # Ensure it ends with punctuation
    if result and result[-1] not in '.!?':
        result += '.'
    
    return result


# =============================================================================
# LLM GENERATION
# =============================================================================

def generate_theme_of_the_day(articles: List[Dict], current_events_data: Dict) -> str:
    """
    Generate a concise theme of the day from top articles.
    """
    # Build context from articles
    article_context = "\n".join([
        f"- {a['title']} ({a['source']})"
        for a in articles
    ])
    
    # Get trending topics from current events if available
    analysis = current_events_data.get('analysis', {})
    trending = analysis.get('trending_topics', [])
    trending_text = ", ".join(trending[:5]) if trending else "N/A"
    
    prompt = f"""Based on these top technology and business articles from today, identify the single most prominent theme.

Today's Top Articles:
{article_context}

Trending Topics from News: {trending_text}

Respond with ONLY a short theme phrase (3-7 words). No explanation, no punctuation at the end.
Examples of good responses:
- AI Regulation Takes Center Stage
- Cloud Giants Battle for Enterprise
- Cybersecurity Threats Escalate Globally

Theme:"""

    try:
        response = ollama.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'user', 'content': prompt}
        ])
        theme = response['message']['content'].strip()
        # Clean up any quotes or extra punctuation
        theme = theme.strip('"\'.')
        return theme
    except Exception as e:
        return "Technology and Business Developments"


def generate_daily_summary(articles: List[Dict], current_events_data: Dict) -> str:
    """
    Generate a paragraph summarizing the last 24 hours.
    """
    # Article summaries
    article_context = "\n".join([
        f"- {a['title']}: {condense_summary(a.get('summary', ''), 1)}"
        for a in articles
    ])
    
    # Current events context
    analysis = current_events_data.get('analysis', {})
    ce_summary = analysis.get('summary', '')
    trending = analysis.get('trending_topics', [])
    
    prompt = f"""Write a professional 3-4 sentence paragraph summarizing the past 24 hours in technology and business news. This is for a LinkedIn newsletter.

Top Stories and Their Key Points:
{article_context}

Broader News Context: {ce_summary}
Trending Topics: {', '.join(trending[:5]) if trending else 'N/A'}

Requirements:
- Professional tone suitable for LinkedIn
- 3-4 sentences only
- Focus on implications and significance, not just facts
- Do not use bullet points
- Do not start with "Over the past 24 hours" or similar clichés

Summary:"""

    try:
        response = ollama.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'user', 'content': prompt}
        ])
        return response['message']['content'].strip()
    except Exception as e:
        return "Today's developments reflect ongoing shifts in the technology and business landscape."


# =============================================================================
# NEWSLETTER FORMATTING
# =============================================================================

def format_linkedin_newsletter(
    theme: str,
    articles: List[Dict],
    summary: str,
    generated_date: str
) -> str:
    """
    Format the complete LinkedIn newsletter with professional styling.
    """
    # Number emojis for article list
    number_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']
    
    # Build article list
    article_lines = []
    for i, article in enumerate(articles[:5]):
        emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        title = article['title']
        abstract = condense_summary(article.get('summary', ''), 1)
        url = article['url']
        
        article_lines.append(f"{emoji} {title}")
        if abstract:
            article_lines.append(f"   → {abstract}")
        article_lines.append(f"   🔗 {url}")
        article_lines.append("")  # Blank line between articles
    
    articles_text = "\n".join(article_lines).rstrip()
    
    # Assemble newsletter
    newsletter = f"""🎯 {to_bold_unicode('Theme of the Day')}: {theme}

━━━━━━━━━━━━━━━━━━━━━━

📰 {to_bold_unicode("Today's Top Stories")}

{articles_text}

━━━━━━━━━━━━━━━━━━━━━━

📊 {to_bold_unicode('24-Hour Summary')}

{summary}

━━━━━━━━━━━━━━━━━━━━━━

#Technology #Business #AI #Innovation #TechNews

"""
    
    return newsletter


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def generate_newsletter() -> Tuple[bool, str]:
    """
    Main newsletter generation function.
    Returns: (success: bool, message: str)
    """
    print("Starting LinkedIn Post Generation")
    
    # Preflight checks
    passed, message, ce_data = run_preflight_checks()
    if not passed:
        return False, message
    
    # Get top articles
    articles = get_top_articles(limit=5, hours=24)
    if not articles:
        return False, "No articles retrieved from database"
    
    # Generate content
    theme = generate_theme_of_the_day(articles, ce_data)
    summary = generate_daily_summary(articles, ce_data)
    
    # Format newsletter
    today_str = datetime.now().strftime("%Y%m%d")
    newsletter = format_linkedin_newsletter(
        theme=theme,
        articles=articles,
        summary=summary,
        generated_date=today_str
    )
    
    # Save to file
    output_filename = f"linkedin_newsletter_{today_str}.txt"
    output_path = os.path.join(TOPICS_DIR, output_filename)
    
    os.makedirs(TOPICS_DIR, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(newsletter)
    
    return True, f"Completed - Saved to {output_path}"


def main():
    """Entry point."""
    try:
        success, message = generate_newsletter()
        if success:
            print(message)
            sys.exit(0)
        else:
            print(f"Error - Unable to Generate: {message}")
            sys.exit(1)
    except Exception as e:
        print(f"Error - Unable to Generate: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
