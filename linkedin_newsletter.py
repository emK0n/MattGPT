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
from email.utils import parsedate_to_datetime
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

def parse_article_date(date_str: str) -> Optional[datetime]:
    """Parse dates in ISO or RFC 2822 format."""
    if not date_str:
        return None
    try:
        # Try ISO format first (2026-01-17T14:16:51+00:00)
        if 'T' in date_str and date_str[0].isdigit():
            clean = date_str.replace('Z', '')
            if '+' in clean:
                clean = clean.split('+')[0]
            elif clean.count('-') > 2:  # Negative offset like -08:00
                clean = clean.rsplit('-', 1)[0]
            return datetime.fromisoformat(clean)
        # Try RFC 2822 format (Mon, 05 Jan 2026 11:00:00 +0000)
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except:
        return None


def get_top_articles(limit: int = 5, hours: int = 24) -> List[Dict]:
    """Retrieve top articles from database sorted by total relevance."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Fetch more than needed, filter by date in Python
    cursor.execute('''
        SELECT 
            title, url, source, summary, relevance_score,
            COALESCE(relevance_boost, 0) as boost_score,
            (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
            published_date
        FROM content 
        ORDER BY total_relevance DESC
        LIMIT ?
    ''', (limit * 10,))

    cutoff = datetime.now() - timedelta(hours=hours)
    articles = []

    for row in cursor.fetchall():
        pub_date = parse_article_date(row['published_date'])
        if pub_date and pub_date >= cutoff:
            articles.append(dict(row))
            if len(articles) >= limit:
                break

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


def generate_hook(articles: List[Dict], current_events_data: Dict) -> str:
    """
    Generate an attention-grabbing opening hook based on the top story.
    Returns a bold but substantiated claim (1-2 sentences).
    """
    if not articles:
        return ""
    
    # Focus on the top article for the hook
    top_article = articles[0]
    top_title = top_article['title']
    top_summary = condense_summary(top_article.get('summary', ''), 2)
    
    # Context from other articles
    other_titles = [a['title'] for a in articles[1:4]]
    other_context = "\n".join([f"- {t}" for t in other_titles]) if other_titles else "N/A"
    
    prompt = f"""Write a single attention-grabbing opening sentence for a newsletter about today's tech news.
    
TOP STORY:
Title: {top_title}
Summary: {top_summary}

OTHER STORIES TODAY:
{other_context}

Requirements:
- Audience is decision makers and thought leaders in the technology space
- ONE sentence only (two maximum if needed for clarity)
- Make a bold but substantiated claim grounded in the actual story
- Do NOT be sensationalist or clickbait
- Do NOT start with "Breaking:" or similar
- Do NOT use questions
- Professional tone suitable for LinkedIn
- The claim must be directly supportable by the top story content

Good examples:
- "Major AI labs are now publicly admitting they may not understand their own models."
- "A 125-year-old mathematics problem just got solved using particle physics."
- "The enterprise cloud market shifted dramatically this week as pricing wars escalate."

Bad examples (avoid):
- "You won't believe what happened in AI today!"
- "Is AI taking over? Here's what you need to know."
- "Big news in tech this week."

Hook:"""

    try:
        response = ollama.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'user', 'content': prompt}
        ])
        hook = response['message']['content'].strip()
        # Clean up quotes if present
        hook = hook.strip('"\'')
        # Ensure it ends with punctuation
        if hook and hook[-1] not in '.!?':
            hook += '.'
        return hook
    except Exception as e:
        return ""


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
    
    prompt = f"""Write a professional 3-4 sentence paragraph summarizing the past 24 hours in technology and business news. This is for a newsletter.

Top Stories and Their Key Points:
{article_context}

Broader News Context: {ce_summary}
Trending Topics: {', '.join(trending[:5]) if trending else 'N/A'}

Requirements:
- Audience is decision makers and thought leaders in the technology space
- Professional tone suitable for LinkedIn
- 3-4 sentences only
- Focus on implications and significance, not just facts
- Do not use bullet points
- Do not start with "Over the past 24 hours" or similar cliches
- Create a paragraph that reads naturally as a conversational summary
- Do *NOT* simply create a summary of each article pieced together into sentences

Bad Example:
In technology news, Google DeepMind unveiled TranslateGemma, a specialized language model for translation tasks, signaling advancements in offline web services. 

Good Example:
Innovations from Google in local model capabilities are paving the way for greater offline services including translation services with their newly released TranslateGemma.

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
    hook: str,
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
    
    # Build hook section (only if hook exists)
    hook_section = f"{hook}\n\n" if hook else ""
    
    # Assemble newsletter
    newsletter = f"""{hook_section}🎯 {to_bold_unicode('What we need to research today')}: {theme}

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

    print(f"Starting LinkedIn Post Generation")
    
    # Preflight checks
    passed, message, ce_data = run_preflight_checks()
    if not passed:
        return False, message
    
    # Get top articles
    articles = get_top_articles(limit=5, hours=24)
    if not articles:
        return False, "No articles retrieved from database"
    
    # Generate content
    hook = generate_hook(articles, ce_data)
    theme = generate_theme_of_the_day(articles, ce_data)
    summary = generate_daily_summary(articles, ce_data)
    
    # Format newsletter
    today_str = datetime.now().strftime("%Y%m%d")
    newsletter = format_linkedin_newsletter(
        hook=hook,
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
