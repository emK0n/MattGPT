#!/usr/bin/env python3
"""
Customer Cache Setup Utility
============================
Standalone utility that processes customers.csv and sets up cache directories
for customers with null cache_path values.

Uses exact methods from cache_mgmt.py to:
1. Create subdirectories for customers with null cache_path
2. Update cache_path column in customers.csv
3. Download reference 1 links to cache subdirectories
4. Generate checklist output with success/failure indicators
"""

import os
import sys
import pandas as pd
import requests
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re

# Import existing parsers if available
try:
    from pdf_parser import is_pdf_url, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False
    print("⚠️ PDF support not available - PDF URLs will be skipped")


class CacheSetupUtility:
    """Standalone utility for setting up customer cache directories"""
    
    def __init__(self, data_directory: str = "data", cache_directory: str = "cache"):
        self.data_directory = data_directory
        self.cache_directory = cache_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        self.max_content_length = 8000
        self.session_timeout = 15
        
        # Ensure cache directory exists
        os.makedirs(self.cache_directory, exist_ok=True)
    
    def run(self) -> None:
        """Main entry point - run the cache setup process"""
        print("Customer Cache Setup Utility")
        print("============================")
        
        # Load customers CSV
        customers_df = self._load_customers_csv()
        if customers_df is None:
            print("❌ Could not load customers.csv - exiting")
            return
        
        # Find customers with null cache_path
        null_cache_customers = self._find_null_cache_customers(customers_df)
        
        if not null_cache_customers:
            print("✅ All customers already have cache_path set - nothing to do!")
            return
        
        print(f"📋 Found {len(null_cache_customers)} customers with null cache_path")
        print("Processing customers...\n")
        
        # Process each customer
        results = []
        for customer in null_cache_customers:
            result = self._process_customer(customer)
            results.append(result)
        
        # Display summary
        self._display_summary(results)
    
    def _load_customers_csv(self) -> Optional[pd.DataFrame]:
        """Load customers CSV file - exact method from cache_mgmt.py"""
        try:
            if os.path.exists(self.customers_file):
                df = pd.read_csv(self.customers_file)
                df.columns = df.columns.str.strip()
                return df
        except Exception as e:
            print(f"❌ Error loading customers.csv: {e}")
        return None
    
    def _find_null_cache_customers(self, customers_df: pd.DataFrame) -> List[Dict]:
        """Find customers where cache_path is null"""
        # Check if cache_path column exists
        if 'cache_path' not in customers_df.columns:
            print("⚠️ cache_path column not found - treating all customers as needing setup")
            return customers_df.to_dict('records')
        
        # Find customers with null/empty cache_path
        null_mask = (
            customers_df['cache_path'].isna() | 
            (customers_df['cache_path'].astype(str).str.strip() == '') |
            (customers_df['cache_path'].astype(str).str.strip() == 'nan')
        )
        
        null_customers = customers_df[null_mask]
        return null_customers.to_dict('records')
    
    def _process_customer(self, customer: Dict) -> Dict:
        """Process a single customer - create cache and download reference"""
        customer_name = customer.get('customer_name', 'Unknown')
        
        try:
            # Step 1: Create cache_path using exact method from cache_mgmt.py
            cache_path = self._sanitize_customer_name(customer_name)
            
            # Step 2: Update CSV with cache_path using exact method from cache_mgmt.py
            update_success = self._update_csv_cache_path(customer_name, cache_path)
            
            if not update_success:
                return {
                    'customer_name': customer_name,
                    'cache_path': cache_path,
                    'cache_created': False,
                    'csv_updated': False,
                    'reference_downloaded': False,
                    'error': 'Failed to update CSV'
                }
            
            # Step 3: Create cache directory
            cache_dir = os.path.join(self.cache_directory, cache_path)
            os.makedirs(cache_dir, exist_ok=True)
            
            # Step 4: Download reference 1 link using exact method from cache_mgmt.py
            reference_url_raw = customer.get('reference 1', '')
            if pd.isna(reference_url_raw) or reference_url_raw is None:
                reference_url = ''
            else:
                reference_url = str(reference_url_raw).strip()
            
            download_success = False
            if reference_url:
                download_success = self._download_reference_to_cache(reference_url, cache_dir, customer_name)
            
            return {
                'customer_name': customer_name,
                'cache_path': cache_path,
                'cache_created': True,
                'csv_updated': True,
                'reference_downloaded': download_success,
                'reference_url': reference_url,
                'error': None
            }
            
        except Exception as e:
            return {
                'customer_name': customer_name,
                'cache_path': cache_path if 'cache_path' in locals() else 'unknown',
                'cache_created': False,
                'csv_updated': False,
                'reference_downloaded': False,
                'error': str(e)
            }
    
    def _sanitize_customer_name(self, customer_name: str) -> str:
        """Convert customer name to filesystem-safe directory name - exact method from cache_mgmt.py"""
        if not customer_name:
            return "unknown_customer"
        
        # Convert to lowercase and replace problematic characters
        sanitized = customer_name.lower()
        sanitized = re.sub(r'[^\w\s-]', '', sanitized)  # Remove special chars except spaces and hyphens
        sanitized = re.sub(r'[-\s]+', '_', sanitized)   # Replace spaces and hyphens with underscores
        
        return sanitized.strip('_')
    
    def _update_csv_cache_path(self, customer_name: str, cache_path: str) -> bool:
        """Update the cache_path for a customer in the CSV file - exact method from cache_mgmt.py"""
        try:
            customers_df = self._load_customers_csv()
            if customers_df is None:
                return False

            # Find the customer row
            customer_name_clean = customer_name.strip().lower()
            mask = customers_df['customer_name'].str.strip().str.lower() == customer_name_clean

            if mask.any():
                # Update the cache_path column
                customers_df.loc[mask, 'cache_path'] = cache_path

                # Save back to CSV
                customers_df.to_csv(self.customers_file, index=False)
                return True
            else:
                print(f"⚠️ Customer not found in CSV: {customer_name}")
                return False

        except Exception as e:
            print(f"⚠️ Warning: Could not update CSV cache_path for {customer_name}: {e}")
            return False
    
    def _download_reference_to_cache(self, reference_url: str, cache_dir: str, customer_name: str) -> bool:
        """Download reference URL content to cache directory - exact method from cache_mgmt.py"""
        try:
            # Skip if already cached locally
            if reference_url.startswith('cache/') or os.path.exists(reference_url):
                print(f"  📄 File already cached locally: {reference_url}")
                return True

            if HAS_PDF_SUPPORT and is_pdf_url(reference_url):
                # Handle PDF URLs
                pdf_result = process_pdf(reference_url)
                if pdf_result.get('success'):
                    content = pdf_result.get('content', '')
                    if content:
                        # Save as text file in cache
                        filename = "reference_pdf_content.txt"
                        file_path = os.path.join(cache_dir, filename)

                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        return True
                return False
            else:
                # Handle web URLs
                content = self._download_web_content(reference_url)
                if content:
                    # Save as HTML/text file in cache
                    filename = "reference_web_content.html"
                    file_path = os.path.join(cache_dir, filename)

                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    return True
                return False

        except Exception as e:
            print(f"  ⚠️ Download error for {customer_name}: {e}")
            return False
    
    def _download_web_content(self, url: str) -> Optional[str]:
        """Download and extract web content - exact method from cache_mgmt.py"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=self.session_timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                element.decompose()
            
            # Extract text
            text = soup.get_text()
            
            # Clean text
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            clean_text = ' '.join(chunk for chunk in chunks if chunk)
            
            # Truncate if too long
            if len(clean_text) > self.max_content_length:
                clean_text = clean_text[:self.max_content_length] + "\n\n[Content truncated for analysis...]"
            
            return clean_text.strip()
            
        except Exception as e:
            print(f"  ⚠️ Web download error: {e}")
            return None
    
    def _display_summary(self, results: List[Dict]) -> None:
        """Display colored summary of results"""
        print("\n" + "="*50)
        print("CACHE SETUP SUMMARY")
        print("="*50)
        
        success_count = 0
        partial_success_count = 0
        failure_count = 0
        
        for result in results:
            customer_name = result['customer_name']
            cache_created = result['cache_created']
            csv_updated = result['csv_updated']
            reference_downloaded = result['reference_downloaded']
            reference_url = result.get('reference_url', '')
            error = result.get('error')
            
            if error:
                print(f"❌ {customer_name}: {error}")
                failure_count += 1
            elif cache_created and csv_updated and reference_downloaded:
                print(f"✅ {customer_name}: cache created, reference downloaded")
                success_count += 1
            elif cache_created and csv_updated:
                if reference_url:
                    print(f"🟡 {customer_name}: cache created, reference download failed")
                else:
                    print(f"✅ {customer_name}: cache created, no reference URL")
                partial_success_count += 1
            else:
                print(f"❌ {customer_name}: cache setup failed")
                failure_count += 1
        
        print("\n" + "-"*50)
        print(f"✅ Complete Success: {success_count}")
        print(f"🟡 Partial Success:  {partial_success_count}")
        print(f"❌ Failures:        {failure_count}")
        print(f"📊 Total Processed: {len(results)}")
        print("-"*50)


def main():
    """Main function"""
    # Allow custom directories via command line arguments
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    cache_dir = sys.argv[2] if len(sys.argv) > 2 else "cache"
    
    utility = CacheSetupUtility(data_dir, cache_dir)
    utility.run()


if __name__ == "__main__":
    main()
