#!/usr/bin/env python3
"""
Test AI processing specifically
"""

import sqlite3
import requests
import json

def check_database_state():
    """Check what's in the databases"""
    print("🗄️  Checking database state...")
    
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        # Check raw_feeds count
        cursor.execute('SELECT COUNT(*) FROM raw_feeds')
        raw_count = cursor.fetchone()[0]
        print(f"   Raw feeds: {raw_count} articles")
        
        # Check processed content count
        cursor.execute('SELECT COUNT(*) FROM content')
        content_count = cursor.fetchone()[0]
        print(f"   Processed content: {content_count} articles")
        
        # Check recent raw feeds
        cursor.execute('SELECT title, url, added_date FROM raw_feeds ORDER BY added_date DESC LIMIT 5')
        recent_raw = cursor.fetchall()
        print(f"\n   Recent raw feeds:")
        for i, (title, url, date) in enumerate(recent_raw, 1):
            print(f"   {i}. {title[:60]}...")
            print(f"      URL: {url}")
            print(f"      Added: {date}")
        
        # Check recent processed content
        cursor.execute('SELECT title, url, relevance_score, added_date FROM content ORDER BY added_date DESC LIMIT 5')
        recent_content = cursor.fetchall()
        print(f"\n   Recent processed content:")
        for i, (title, url, score, date) in enumerate(recent_content, 1):
            print(f"   {i}. {title[:60]}...")
            print(f"      Score: {score}")
            print(f"      Added: {date}")
        
        # Check for URLs that are in raw_feeds but not in content
        cursor.execute('''
            SELECT rf.title, rf.url, rf.added_date 
            FROM raw_feeds rf 
            LEFT JOIN content c ON rf.url = c.url 
            WHERE c.url IS NULL 
            ORDER BY rf.added_date DESC 
            LIMIT 10
        ''')
        unprocessed = cursor.fetchall()
        
        print(f"\n   Unprocessed articles (in raw_feeds but not content): {len(unprocessed)}")
        for i, (title, url, date) in enumerate(unprocessed[:3], 1):
            print(f"   {i}. {title[:60]}...")
            print(f"      Added: {date}")
        
        conn.close()
        return raw_count, content_count, len(unprocessed)
        
    except Exception as e:
        print(f"   ❌ Database check error: {e}")
        return 0, 0, 0

def test_ollama_directly():
    """Test Ollama directly"""
    print("\n🤖 Testing Ollama directly...")
    
    try:
        import ollama
        
        response = ollama.chat(model="granite3.2:8b", messages=[
            {'role': 'user', 'content': 'Please respond with exactly: "Ollama is working correctly"'}
        ])
        
        result = response['message']['content'].strip()
        print(f"   ✅ Ollama response: {result}")
        return True
        
    except Exception as e:
        print(f"   ❌ Ollama error: {e}")
        return False

def force_processing_test():
    """Try to force process some articles"""
    print("\n🔬 Testing forced processing...")
    
    try:
        # Get some unprocessed articles from database
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT rf.title, rf.content, rf.url, rf.source
            FROM raw_feeds rf 
            LEFT JOIN content c ON rf.url = c.url 
            WHERE c.url IS NULL 
            LIMIT 1
        ''')
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            print("   ℹ️  No unprocessed articles found - all articles already processed")
            return
        
        title, content, url, source = result
        print(f"   Found unprocessed article: {title[:50]}...")
        
        # Test the AI processing function directly
        from app import ContentCurator
        
        curator = ContentCurator()
        
        test_article = {
            'title': title,
            'content': content or 'No content available',
            'url': url,
            'source': source
        }
        
        print("   🧠 Testing AI processing...")
        summary, relevance_score = curator.generate_summary_and_relevance(test_article)
        
        print(f"   ✅ AI processing successful!")
        print(f"   Summary: {summary[:100]}...")
        print(f"   Relevance score: {relevance_score}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ AI processing test error: {e}")
        return False

def clear_some_processed_content():
    """Clear some processed content to allow reprocessing"""
    print("\n🧹 Option to clear some processed content...")
    
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM content')
        content_count = cursor.fetchone()[0]
        
        if content_count > 5:
            choice = input(f"   Clear last 5 processed articles to test reprocessing? (y/N): ").strip().lower()
            if choice == 'y':
                cursor.execute('DELETE FROM content WHERE id IN (SELECT id FROM content ORDER BY added_date DESC LIMIT 5)')
                conn.commit()
                deleted = cursor.rowcount
                print(f"   ✅ Deleted {deleted} articles from processed content")
                print("   Now try running collection again - these should get reprocessed")
        else:
            print(f"   Only {content_count} articles in processed content - not clearing")
        
        conn.close()
        
    except Exception as e:
        print(f"   ❌ Error clearing content: {e}")

def main():
    print("🔬 AI Processing Debug Test")
    print("=" * 50)
    
    # Check database state
    raw_count, content_count, unprocessed_count = check_database_state()
    
    # Test Ollama
    ollama_working = test_ollama_directly()
    
    if unprocessed_count > 0:
        # Test forced processing
        force_processing_test()
    else:
        print(f"\n💡 Diagnosis: All {raw_count} raw articles have already been processed into the {content_count} processed articles.")
        print("   This is why no AI processing happened - there's nothing new to process!")
        
        # Offer to clear some for testing
        clear_some_processed_content()
    
    print(f"\n📊 Summary:")
    print(f"   Raw articles: {raw_count}")
    print(f"   Processed articles: {content_count}")
    print(f"   Unprocessed articles: {unprocessed_count}")
    print(f"   Ollama working: {'✅' if ollama_working else '❌'}")
    
    if unprocessed_count == 0 and content_count > 0:
        print(f"\n✅ System is working correctly!")
        print(f"   All articles have been processed. To see AI processing in action:")
        print(f"   1. Wait for new articles to be published")
        print(f"   2. Search for a specific topic to get new content")
        print(f"   3. Clear some processed articles (offered above)")

if __name__ == "__main__":
    main()