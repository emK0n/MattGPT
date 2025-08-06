#!/usr/bin/env python3
"""
Database Reset Utility for Testing Research Agent Workflow
Resets database, current events, hot topics, and flags to previous day state
"""

import os
import sqlite3
import json
import glob
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import argparse


class WorkflowResetUtility:
    """Utility to reset the research agent workflow to a clean previous-day state"""
    
    def __init__(self, data_directory="data", backup_directory="backups"):
        self.data_dir = Path(data_directory)
        self.backup_dir = Path(backup_directory)
        self.db_path = self.data_dir / "content_curator.db"
        
        # Ensure directories exist
        self.data_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(exist_ok=True)
        
        # Get date strings
        self.today = datetime.now()
        self.yesterday = self.today - timedelta(days=1)
        self.today_str = self.today.strftime('%Y-%m-%d')
        self.yesterday_str = self.yesterday.strftime('%Y-%m-%d')
        self.today_compact = self.today.strftime('%Y%m%d')
        self.yesterday_compact = self.yesterday.strftime('%Y%m%d')
    
    def create_backup(self):
        """Create backup of current state before reset"""
        print("📦 Creating backup of current state...")
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_subdir = self.backup_dir / f"backup_{timestamp}"
        backup_subdir.mkdir(exist_ok=True)
        
        # Backup database
        if self.db_path.exists():
            shutil.copy2(self.db_path, backup_subdir / "content_curator.db")
            print(f"   ✅ Database backed up")
        
        # Backup current events files
        events_files = list(self.data_dir.glob("current_events_analysis_*.json"))
        for file in events_files:
            shutil.copy2(file, backup_subdir / file.name)
            print(f"   ✅ Backed up {file.name}")
        
        # Backup week review files
        week_files = list(self.data_dir.glob("week_review_*.json"))
        for file in week_files:
            shutil.copy2(file, backup_subdir / file.name)
            print(f"   ✅ Backed up {file.name}")
        
        # Backup podcast files
        podcast_files = list(self.data_dir.glob("podcast_*"))
        for file in podcast_files:
            if file.is_file():
                shutil.copy2(file, backup_subdir / file.name)
                print(f"   ✅ Backed up {file.name}")
        
        # Backup flag files
        flag_files = list(self.data_dir.glob("morning_greeting_*.flag"))
        for file in flag_files:
            shutil.copy2(file, backup_subdir / file.name)
            print(f"   ✅ Backed up {file.name}")
        
        print(f"📦 Backup completed: {backup_subdir}")
        return backup_subdir
    
    def reset_database_hot_topics(self):
        """Reset hot topics boost scoring in database"""
        print("🗄️ Resetting database hot topics data...")
        
        if not self.db_path.exists():
            print("   ⚠️ Database not found - will be created fresh")
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Clear hot topics boost data
            cursor.execute('''
                UPDATE content 
                SET relevance_boost = 0, 
                    hot_topics_date = NULL, 
                    hot_topics_keywords_used = NULL
            ''')
            
            rows_updated = cursor.rowcount
            conn.commit()
            conn.close()
            
            print(f"   ✅ Cleared hot topics data from {rows_updated} articles")
            
        except Exception as e:
            print(f"   ❌ Error resetting database: {e}")
    
    def remove_todays_articles(self):
        """Remove articles added today from both content and raw_feeds tables"""
        print("🗄️ Removing articles added today...")
        
        if not self.db_path.exists():
            print("   ⚠️ Database not found")
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get today's date range (start of day)
            today_start = self.today.strftime('%Y-%m-%d')
            
            # Remove from content table
            cursor.execute('DELETE FROM content WHERE DATE(added_date) = ?', (today_start,))
            content_deleted = cursor.rowcount
            
            # Remove from raw_feeds table
            cursor.execute('DELETE FROM raw_feeds WHERE DATE(added_date) = ?', (today_start,))
            raw_deleted = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            print(f"   ✅ Removed {content_deleted} processed articles from today")
            print(f"   ✅ Removed {raw_deleted} raw feed items from today")
            
        except Exception as e:
            print(f"   ❌ Error removing today's articles: {e}")
    
    def remove_current_events_files(self):
        """Remove today's current events analysis files"""
        print("📰 Removing current events analysis files...")
        
        patterns = [
            f"current_events_analysis_{self.today_compact}.json",
            f"current_events_analysis_{self.today_str}.json"
        ]
        
        removed_count = 0
        for pattern in patterns:
            files = list(self.data_dir.glob(pattern))
            for file in files:
                print(f"   🗑️ Removing {file.name}")
                file.unlink()
                removed_count += 1
        
        if removed_count == 0:
            print("   ℹ️ No current events files found for today")
        else:
            print(f"   ✅ Removed {removed_count} current events files")
    
    def remove_week_review_files(self):
        """Remove today's week review files"""
        print("📊 Removing week review files...")
        
        week_files = list(self.data_dir.glob(f"week_review_{self.today_str}.json"))
        
        if not week_files:
            print("   ℹ️ No week review files found for today")
            return
        
        for file in week_files:
            print(f"   🗑️ Removing {file.name}")
            file.unlink()
        
        print(f"   ✅ Removed {len(week_files)} week review files")
    
    def remove_podcast_files(self):
        """Remove today's podcast files"""
        print("🎙️ Removing podcast files...")
        
        podcast_patterns = [
            f"podcast_{self.today_compact}.*",
            f"podcast_{self.today_str}.*"
        ]
        
        removed_count = 0
        for pattern in podcast_patterns:
            files = list(self.data_dir.glob(pattern))
            for file in files:
                print(f"   🗑️ Removing {file.name}")
                file.unlink()
                removed_count += 1
        
        if removed_count == 0:
            print("   ℹ️ No podcast files found for today")
        else:
            print(f"   ✅ Removed {removed_count} podcast files")
    
    def reset_morning_greeting_flag(self):
        """Reset morning greeting flag to previous day"""
        print("☀️ Resetting morning greeting flag...")
        
        # Remove today's flag
        today_flag = self.data_dir / f"morning_greeting_{self.today_str}.flag"
        if today_flag.exists():
            today_flag.unlink()
            print(f"   🗑️ Removed today's flag: {today_flag.name}")
        
        # Create yesterday's flag to simulate it was shown yesterday
        yesterday_flag = self.data_dir / f"morning_greeting_{self.yesterday_str}.flag"
        with open(yesterday_flag, 'w') as f:
            # Write yesterday's timestamp
            yesterday_timestamp = (self.yesterday.replace(hour=8, minute=0)).timestamp()
            f.write(str(yesterday_timestamp))
        
        print(f"   ✅ Created yesterday's flag: {yesterday_flag.name}")
    
    def create_sample_user_interests(self):
        """Create sample user interests if database is empty"""
        print("👤 Setting up sample user interests...")
        
        if not self.db_path.exists():
            print("   ⚠️ Database not found - cannot set interests")
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if interests exist
            cursor.execute('SELECT COUNT(*) FROM user_interests')
            count = cursor.fetchone()[0]
            
            if count > 0:
                print(f"   ℹ️ {count} interests already exist - not modifying")
                conn.close()
                return
            
            # Add sample interests
            sample_interests = [
                ('artificial intelligence', 8.5),
                ('machine learning', 8.0),
                ('technology', 7.5),
                ('business', 6.5),
                ('innovation', 6.0),
                ('startup', 5.5),
                ('climate change', 7.0),
                ('cybersecurity', 6.5)
            ]
            
            for interest, weight in sample_interests:
                cursor.execute(
                    'INSERT OR REPLACE INTO user_interests (interest, weight) VALUES (?, ?)',
                    (interest, weight)
                )
            
            conn.commit()
            conn.close()
            
            print(f"   ✅ Added {len(sample_interests)} sample interests")
            
        except Exception as e:
            print(f"   ❌ Error setting up interests: {e}")
    
    def show_status(self):
        """Show current status of files and database"""
        print("\n📋 Current Status:")
        print("=" * 50)
        
        # Database status
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('SELECT COUNT(*) FROM content')
                content_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM raw_feeds')
                raw_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM user_interests')
                interests_count = cursor.fetchone()[0]
                
                cursor.execute('''
                    SELECT COUNT(*) FROM content 
                    WHERE hot_topics_date = ? AND relevance_boost > 0
                ''', (self.today_str,))
                boosted_today = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM content WHERE DATE(added_date) = ?', (self.today_str,))
                content_today = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM raw_feeds WHERE DATE(added_date) = ?', (self.today_str,))
                raw_today = cursor.fetchone()[0]
                
                conn.close()
                
                print(f"🗄️ Database: {content_count} articles, {raw_count} raw items, {interests_count} interests")
                print(f"📅 Today's articles: {content_today} processed, {raw_today} raw")
                print(f"🔥 Hot topics: {boosted_today} articles boosted today")
                
            except Exception as e:
                print(f"🗄️ Database: Error reading - {e}")
        else:
            print("🗄️ Database: Not found")
        
        # Current events files
        events_files = list(self.data_dir.glob(f"current_events_analysis_{self.today_compact}.json"))
        print(f"📰 Current events: {len(events_files)} files for today")
        
        # Week review files
        week_files = list(self.data_dir.glob(f"week_review_{self.today_str}.json"))
        print(f"📊 Week review: {len(week_files)} files for today")
        
        # Podcast files
        podcast_files = list(self.data_dir.glob(f"podcast_{self.today_compact}*"))
        print(f"🎙️ Podcast: {len(podcast_files)} files for today")
        
        # Morning greeting flag
        flag_file = self.data_dir / f"morning_greeting_{self.today_str}.flag"
        print(f"☀️ Morning greeting: {'shown' if flag_file.exists() else 'not shown'} today")
        
        print("=" * 50)
    
    def full_reset(self, backup=True):
        """Perform complete reset to previous day state"""
        print("🔄 Starting full workflow reset...")
        print(f"📅 Resetting from {self.today_str} to {self.yesterday_str}")
        print("=" * 60)
        
        if backup:
            backup_dir = self.create_backup()
        
        # Reset all components
        self.reset_database_hot_topics()
        self.remove_todays_articles()
        self.remove_current_events_files()
        self.remove_week_review_files()
        self.remove_podcast_files()
        self.reset_morning_greeting_flag()
        self.create_sample_user_interests()
        
        print("\n" + "=" * 60)
        print("✅ Full reset completed!")
        print("\n📋 The system is now in a clean previous-day state:")
        print("   • Hot topics boost data cleared")
        print("   • Today's articles removed from both content and raw_feeds tables")
        print("   • Current events files removed")
        print("   • Week review files removed")
        print("   • Podcast files removed")
        print("   • Morning greeting flag reset to yesterday")
        print("   • Sample user interests configured")
        print("\n🚀 You can now test the complete workflow:")
        print("   1. Click 'What's Going On?' to start fresh analysis")
        print("   2. System will show morning greeting (if enabled)")
        print("   3. Generate hot topics analysis")
        print("   4. Run collection with boost scoring")
        print("   5. Generate week review and podcast")
        
        if backup:
            print(f"\n📦 Original state backed up to: {backup_dir}")


def main():
    parser = argparse.ArgumentParser(description='Reset Research Agent Workflow for Testing')
    parser.add_argument('--data-dir', default='data', help='Data directory path')
    parser.add_argument('--backup-dir', default='backups', help='Backup directory path')
    parser.add_argument('--no-backup', action='store_true', help='Skip backup creation')
    parser.add_argument('--status-only', action='store_true', 
                       help='Only show current status, do not reset')
    
    args = parser.parse_args()
    
    # Create utility instance
    utility = WorkflowResetUtility(args.data_dir, args.backup_dir)
    
    if args.status_only:
        utility.show_status()
        return
    
    # Show current status
    utility.show_status()
    
    # Confirm reset
    print(f"\n⚠️ This will reset the workflow to a clean previous-day state!")
    print(f"⚠️ This will remove all articles and data added today!")
    
    response = input("\nProceed with reset? (y/N): ").strip().lower()
    if response not in ['y', 'yes']:
        print("❌ Reset cancelled")
        return
    
    # Perform reset
    utility.full_reset(backup=not args.no_backup)
    
    print("\n📋 Final Status:")
    utility.show_status()


if __name__ == "__main__":
    main()
