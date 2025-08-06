#!/usr/bin/env python3
"""
Purge Content Curator database tables
CAUTION: This will delete all your collected articles and statistics!
"""

import sqlite3
import os
from datetime import datetime

def backup_database():
    """Create a backup before purging"""
    if os.path.exists('content_curator.db'):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'content_curator_backup_{timestamp}.db'
        
        try:
            import shutil
            shutil.copy2('content_curator.db', backup_name)
            print(f"✅ Created backup: {backup_name}")
            return backup_name
        except Exception as e:
            print(f"⚠️  Backup failed: {e}")
            return None
    else:
        print("ℹ️  No existing database to backup")
        return None

def show_current_counts():
    """Show current database contents"""
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        tables = ['content', 'raw_feeds', 'user_interests', 'statistics']
        print("📊 Current database contents:")
        
        for table in tables:
            try:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                count = cursor.fetchone()[0]
                print(f"   {table}: {count} records")
            except:
                print(f"   {table}: table doesn't exist")
        
        conn.close()
        
    except Exception as e:
        print(f"Error checking database: {e}")

def purge_options():
    """Show purge options"""
    print("\n🗑️  Purge Options:")
    print("1. Purge ALL data (complete reset)")
    print("2. Purge only processed content (keep raw feeds)")
    print("3. Purge only raw feeds (keep processed content)")
    print("4. Purge only statistics (keep articles)")
    print("5. Delete entire database file")
    print("6. Cancel")
    
    choice = input("\nSelect option (1-6): ").strip()
    return choice

def purge_all_data():
    """Purge all data from all tables"""
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        tables = ['content', 'raw_feeds', 'statistics']
        deleted_counts = {}
        
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            
            cursor.execute(f'DELETE FROM {table}')
            deleted_counts[table] = count
        
        # Reset user interests to defaults
        cursor.execute('DELETE FROM user_interests')
        cursor.execute("INSERT OR IGNORE INTO user_interests (interest, weight) VALUES ('machine learning', 1.0)")
        cursor.execute("INSERT OR IGNORE INTO user_interests (interest, weight) VALUES ('AI', 1.0)")
        
        conn.commit()
        conn.close()
        
        print("✅ Purged all data:")
        for table, count in deleted_counts.items():
            print(f"   {table}: deleted {count} records")
        print("   user_interests: reset to defaults")
        
    except Exception as e:
        print(f"❌ Purge failed: {e}")

def purge_processed_content():
    """Purge only processed content table"""
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM content')
        count = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM content')
        
        conn.commit()
        conn.close()
        
        print(f"✅ Purged processed content: deleted {count} records")
        print("   Raw feeds and interests preserved")
        
    except Exception as e:
        print(f"❌ Purge failed: {e}")

def purge_raw_feeds():
    """Purge only raw feeds table"""
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM raw_feeds')
        count = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM raw_feeds')
        
        conn.commit()
        conn.close()
        
        print(f"✅ Purged raw feeds: deleted {count} records")
        print("   Processed content and interests preserved")
        
    except Exception as e:
        print(f"❌ Purge failed: {e}")

def purge_statistics():
    """Purge only statistics table"""
    try:
        conn = sqlite3.connect('content_curator.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM statistics')
        count = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM statistics')
        
        conn.commit()
        conn.close()
        
        print(f"✅ Purged statistics: deleted {count} records")
        print("   Articles and interests preserved")
        
    except Exception as e:
        print(f"❌ Purge failed: {e}")

def delete_database_file():
    """Delete the entire database file"""
    try:
        if os.path.exists('content_curator.db'):
            os.remove('content_curator.db')
            print("✅ Deleted entire database file")
            print("   A new database will be created when you restart the app")
        else:
            print("ℹ️  Database file doesn't exist")
            
    except Exception as e:
        print(f"❌ Delete failed: {e}")

def main():
    print("🗑️  Content Curator Database Purge Tool")
    print("=" * 50)
    print("⚠️  WARNING: This will delete your collected data!")
    print("=" * 50)
    
    # Show current state
    show_current_counts()
    
    # Create backup
    backup_file = backup_database()
    
    # Get user choice
    choice = purge_options()
    
    if choice == '1':
        confirm = input("\n⚠️  Purge ALL data? Type 'YES' to confirm: ")
        if confirm == 'YES':
            purge_all_data()
        else:
            print("❌ Cancelled")
            
    elif choice == '2':
        confirm = input("\n⚠️  Purge processed content only? Type 'YES' to confirm: ")
        if confirm == 'YES':
            purge_processed_content()
        else:
            print("❌ Cancelled")
            
    elif choice == '3':
        confirm = input("\n⚠️  Purge raw feeds only? Type 'YES' to confirm: ")
        if confirm == 'YES':
            purge_raw_feeds()
        else:
            print("❌ Cancelled")
            
    elif choice == '4':
        confirm = input("\n⚠️  Purge statistics only? Type 'YES' to confirm: ")
        if confirm == 'YES':
            purge_statistics()
        else:
            print("❌ Cancelled")
            
    elif choice == '5':
        confirm = input("\n⚠️  Delete entire database file? Type 'YES' to confirm: ")
        if confirm == 'YES':
            delete_database_file()
        else:
            print("❌ Cancelled")
            
    elif choice == '6':
        print("❌ Cancelled")
        
    else:
        print("❌ Invalid choice")
    
    # Show final state
    if choice in ['1', '2', '3', '4', '5'] and 'YES' in locals():
        print("\n📊 After purge:")
        show_current_counts()
        
        if backup_file:
            print(f"\n💾 Your data is backed up in: {backup_file}")
        
        print(f"\n🚀 Next steps:")
        print("1. Restart your Flask app if it's running")
        print("2. Run a collection to see AI processing in action")
        print("3. Your interests and configuration are preserved")

if __name__ == "__main__":
    main()