#!/usr/bin/env python3
"""
Diagnostic script for Content Curator
Run this to test if your system is working properly
"""

import sqlite3
import time
import requests
import ollama
import feedparser
from datetime import datetime

def test_database():
    """Test database connectivity and content"""
    print("🗄️  Testing database...")
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"   Tables found: {tables}")
        
        # Count records
        for table in ['content', 'raw_feeds', 'user_interests', 'statistics']:
            if table in tables:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                count = cursor.fetchone()[0]
                print(f"   {table}: {count} records")
        
        conn.close()
        print("   ✅ Database OK")
        return True
    except Exception as e:
        print(f"   ❌ Database error: {e}")
        return False

def test_ollama():
    """Test Ollama connectivity"""
    print("\n🤖 Testing Ollama...")
    try:
        response = ollama.chat(model="granite3.2:8b", messages=[
            {'role': 'user', 'content': 'Respond with exactly: "Ollama test successful"'}
        ])
        result = response['message']['content'].strip()
        print(f"   Response: {result}")
        print("   ✅ Ollama OK")
        return True
    except Exception as e:
        print(f"   ❌ Ollama error: {e}")
        print("   💡 Make sure Ollama is running and granite3.2:8b is installed")
        print("   Run: ollama pull granite3.2:8b")
        return False

def test_rss_feeds():
    """Test RSS feed connectivity"""
    print("\n📡 Testing RSS feeds...")
    test_feeds = [
        "https://news.ycombinator.com/rss",
        "http://arxiv.org/rss/cs.AI"
    ]
    
    working_feeds = 0
    for feed_url in test_feeds:
        try:
            print(f"   Testing: {feed_url}")
            feed = feedparser.parse(feed_url)
            if feed.entries:
                print(f"   ✅ {len(feed.entries)} entries found")
                working_feeds += 1
            else:
                print(f"   ⚠️  No entries found")
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print(f"   {working_feeds}/{len(test_feeds)} feeds working")
    return working_feeds > 0

def test_flask_app():
    """Test if Flask app is running"""
    print("\n🌐 Testing Flask app...")
    try:
        response = requests.get('http://localhost:5000/api/debug/status', timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Flask app responding")
            print(f"   Database articles: {data.get('database', {}).get('content_articles', 0)}")
            print(f"   Ollama status: {data.get('ollama', {}).get('status', 'Unknown')}")
            return True
        else:
            print(f"   ❌ HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Flask app error: {e}")
        print("   💡 Make sure the Flask app is running on port 5000")
        return False

def run_minimal_collection():
    """Run a minimal collection test"""
    print("\n🔄 Testing minimal collection...")
    try:
        from app import ContentCurator
        
        curator = ContentCurator()
        
        # Test single arXiv paper collection
        print("   Testing arXiv collection...")
        papers = curator.collect_arxiv_papers("machine learning", max_results=2)
        print(f"   Collected {len(papers)} papers")
        
        if papers:
            # Test LLM processing on first paper
            print("   Testing LLM processing...")
            summary, score = curator.generate_summary_and_relevance(papers[0])
            print(f"   Summary: {summary[:100]}...")
            print(f"   Relevance score: {score}")
            print("   ✅ Minimal collection test passed")
            return True
        else:
            print("   ❌ No papers collected")
            return False
            
    except Exception as e:
        print(f"   ❌ Collection test error: {e}")
        return False

def main():
    """Run all diagnostic tests"""
    print("🔍 Content Curator Diagnostic Tool")
    print("=" * 50)
    
    tests = [
        ("Database", test_database),
        ("Ollama", test_ollama),
        ("RSS Feeds", test_rss_feeds),
        ("Flask App", test_flask_app),
        ("Minimal Collection", run_minimal_collection)
    ]
    
    results = {}
    for test_name, test_func in tests:
        results[test_name] = test_func()
    
    print("\n" + "=" * 50)
    print("📊 DIAGNOSTIC SUMMARY")
    print("=" * 50)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:20} {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All tests passed! Your system should be working correctly.")
        print("If collection is still slow, it's likely due to:")
        print("   • Large number of articles to process")
        print("   • Slow LLM processing (normal for detailed analysis)")
        print("   • Network delays fetching RSS feeds")
    else:
        print("\n⚠️  Some tests failed. Fix the failing components first.")
    
    print("\n💡 Tips:")
    print("   • Check terminal output for detailed progress")
    print("   • Collection can take 5-30 minutes depending on settings")
    print("   • Watch the browser dev tools console for API responses")

if __name__ == "__main__":
    main()