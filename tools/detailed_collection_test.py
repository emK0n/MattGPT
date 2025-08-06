#!/usr/bin/env python3
"""
Detailed test to debug collection issues
Run this while your Flask app is running
"""

import requests
import time
import json

def test_debug_endpoint():
    """Test the debug endpoint to see system status"""
    print("🔍 Testing debug endpoint...")
    try:
        response = requests.get('http://localhost:5000/api/debug/status', timeout=10)
        if response.status_code == 200:
            data = response.json()
            print("✅ Debug endpoint working")
            print(f"   Database articles: {data.get('database', {}).get('content_articles', 0)}")
            print(f"   Raw feeds: {data.get('database', {}).get('raw_feeds', 0)}")
            print(f"   Ollama status: {data.get('ollama', {}).get('status', 'Unknown')}")
            print(f"   Current operation: {data.get('current_operation', 'None')}")
            print(f"   RSS feeds configured: {data.get('config', {}).get('rss_feeds', 0)}")
            print(f"   arXiv queries configured: {data.get('config', {}).get('arxiv_queries', 0)}")
            return True, data
        else:
            print(f"❌ Debug endpoint failed: {response.status_code}")
            return False, None
    except Exception as e:
        print(f"❌ Debug endpoint error: {e}")
        return False, None

def test_individual_apis():
    """Test individual API endpoints"""
    print("\n🧪 Testing individual API endpoints...")
    
    # Test content endpoint
    try:
        response = requests.get('http://localhost:5000/api/content', timeout=10)
        if response.status_code == 200:
            content = response.json()
            print(f"✅ Content API: {len(content)} articles")
        else:
            print(f"❌ Content API failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Content API error: {e}")
    
    # Test interests endpoint
    try:
        response = requests.get('http://localhost:5000/api/interests', timeout=10)
        if response.status_code == 200:
            interests = response.json()
            print(f"✅ Interests API: {len(interests)} interests")
            for interest in interests:
                print(f"   - {interest.get('interest', 'N/A')} (weight: {interest.get('weight', 0)})")
        else:
            print(f"❌ Interests API failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Interests API error: {e}")

def test_collection_start():
    """Test starting a collection"""
    print("\n🚀 Testing collection start...")
    try:
        response = requests.post('http://localhost:5000/api/collect', 
                               json={'max_process': 1}, 
                               timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Collection started: {data.get('message', 'N/A')}")
            print(f"   Status: {data.get('status', 'N/A')}")
            return True
        else:
            print(f"❌ Collection start failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Collection start error: {e}")
        return False

def monitor_collection_detailed():
    """Monitor collection with detailed progress tracking"""
    print("\n📊 Monitoring collection progress...")
    
    start_time = time.time()
    last_phase = ""
    last_completed = 0
    
    for i in range(120):  # Monitor for 2 minutes
        try:
            response = requests.get('http://localhost:5000/api/progress', timeout=5)
            if response.status_code == 200:
                progress = response.json()
                status = progress.get('status', 'unknown')
                phase = progress.get('phase', 'N/A')
                completed = progress.get('completed', 0)
                total = progress.get('total', 0)
                elapsed = progress.get('elapsed_seconds', 0)
                
                # Show progress if it changed
                if phase != last_phase or completed != last_completed:
                    if status == 'running':
                        if total > 0:
                            percent = (completed / total) * 100
                            print(f"   ⚡ {phase} | {completed}/{total} ({percent:.1f}%) | {elapsed}s")
                        else:
                            print(f"   ⚡ {phase} | {completed} items | {elapsed}s")
                        
                        # Show source breakdown if available
                        sources = progress.get('source_counts', {})
                        if sources:
                            sources_str = ', '.join(f'{k}:{v}' for k, v in sources.items())
                            print(f"      Sources: {sources_str}")
                    
                    elif status == 'initializing':
                        print(f"   🔄 Initializing... ({elapsed}s)")
                    
                    last_phase = phase
                    last_completed = completed
                
                if status == 'completed':
                    stats = progress.get('stats', {})
                    print(f"\n✅ COLLECTION COMPLETED!")
                    print(f"   Duration: {elapsed} seconds")
                    print(f"   Total collected: {stats.get('total_collected', 0)}")
                    print(f"   New articles: {stats.get('new_articles', 0)}")
                    print(f"   AI processed: {stats.get('ai_processed', 0)}")
                    print(f"   Cached/skipped: {stats.get('cached_skipped', 0)}")
                    return True
                    
                elif status == 'idle':
                    elapsed_total = int(time.time() - start_time)
                    print(f"\n⏸️  Collection went idle after {elapsed_total} seconds")
                    if elapsed_total < 10:
                        print("   This might indicate an error - collection finished too quickly")
                    return False
                    
            else:
                print(f"   ❌ Progress API error: {response.status_code}")
                
        except Exception as e:
            print(f"   ❌ Progress check error: {e}")
            
        time.sleep(1)
    
    print(f"\n⏰ Collection monitoring timed out after 2 minutes")
    return False

def check_final_results():
    """Check what happened after collection"""
    print("\n📈 Checking final results...")
    
    # Check content again
    try:
        response = requests.get('http://localhost:5000/api/content', timeout=10)
        if response.status_code == 200:
            content = response.json()
            print(f"   Content articles: {len(content)}")
            if content:
                latest = content[0]
                print(f"   Latest: {latest.get('title', 'N/A')[:60]}...")
                print(f"   Source: {latest.get('source', 'N/A')}")
                print(f"   Relevance: {latest.get('relevance_score', 0)}")
        else:
            print(f"   ❌ Content check failed: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Content check error: {e}")
    
    # Check raw feeds
    try:
        response = requests.get('http://localhost:5000/api/raw-feeds', timeout=10)
        if response.status_code == 200:
            raw_feeds = response.json()
            print(f"   Raw feeds: {len(raw_feeds)}")
            if raw_feeds:
                latest_raw = raw_feeds[0]
                print(f"   Latest raw: {latest_raw.get('title', 'N/A')[:60]}...")
                print(f"   Raw source: {latest_raw.get('source', 'N/A')}")
        else:
            print(f"   ❌ Raw feeds check failed: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Raw feeds check error: {e}")

def main():
    print("🔬 Detailed Collection Test")
    print("=" * 60)
    
    # Step 1: Check debug endpoint
    debug_success, debug_data = test_debug_endpoint()
    if not debug_success:
        print("\n❌ Debug endpoint failed - cannot continue")
        return
    
    # Step 2: Test individual APIs
    test_individual_apis()
    
    # Step 3: Start collection
    if not test_collection_start():
        print("\n❌ Collection failed to start")
        return
    
    # Step 4: Monitor collection
    success = monitor_collection_detailed()
    
    # Step 5: Check final results
    check_final_results()
    
    # Summary
    print("\n" + "=" * 60)
    if success:
        print("🎉 Collection test PASSED!")
    else:
        print("❌ Collection test FAILED")
        print("\n💡 Troubleshooting tips:")
        print("   1. Check your terminal running the Flask app for error messages")
        print("   2. Make sure Ollama is running: ollama serve")
        print("   3. Test Ollama manually: ollama run granite3.2:8b")
        print("   4. Check internet connection for RSS feeds")
        print("   5. Look for specific error messages in the Flask terminal")

if __name__ == "__main__":
    main()