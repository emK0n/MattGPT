"""
Customer Cache Management Module
Handles local file caching and content extraction for customer insights
"""

import os
import pandas as pd
import requests
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re
from entity_resolver import calculate_similarity

# Import existing parsers
try:
    from pdf_parser import is_pdf_url, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

# Import new file processors
try:
    from docx import Document
    HAS_DOCX_SUPPORT = True
except ImportError:
    HAS_DOCX_SUPPORT = False

try:
    from openpyxl import load_workbook
    HAS_XLSX_SUPPORT = True
except ImportError:
    HAS_XLSX_SUPPORT = False

try:
    from pptx import Presentation
    HAS_PPTX_SUPPORT = True
except ImportError:
    HAS_PPTX_SUPPORT = False


class CustomerCacheManager:
    """Main cache management class - handles ALL file processing"""
    
    def __init__(self, data_directory: str = "data", cache_directory: str = "cache"):
        self.data_directory = data_directory
        self.cache_directory = cache_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        self.max_content_length = 8000
        self.session_timeout = 15

    def get_customer_research(self, customer_input: str) -> List[Dict]:
        """
        Main entry point - returns all research data for a customer/cluster

        Args:
            customer_input: Customer name or cluster name

        Returns:
            List of dictionaries containing extracted text content and metadata
        """
        customers_df = self._load_customers_csv()
        if customers_df is None:
            return []

        customers = self._find_customers(customer_input, customers_df)
        if not customers:
            return []

        all_research = []
        for customer in customers:
            customer_name = customer.get('customer_name', '')
            if customer_name:
                customer_research = self._load_cached_files(customer_name)  # Direct call to master method
                all_research.extend(customer_research)

        return all_research
    
    def _load_customers_csv(self) -> Optional[pd.DataFrame]:
        """Load customers CSV file"""
        try:
            if os.path.exists(self.customers_file):
                df = pd.read_csv(self.customers_file)
                df.columns = df.columns.str.strip()
                return df
        except Exception:
            pass
        return None

    def _find_customers(self, customer_input: str, customers_df: pd.DataFrame) -> List[Dict]:
        """Find customers matching input (by name or cluster) with fuzzy matching support"""
        customer_input_clean = customer_input.strip().lower()

        # Try exact customer name match first
        name_matches = customers_df[
            customers_df['customer_name'].str.strip().str.lower() == customer_input_clean
            ]

        if len(name_matches) > 0:
            return name_matches.to_dict('records')

        # Try cluster match
        cluster_matches = customers_df[
            customers_df['cluster'].str.strip().str.lower() == customer_input_clean
            ]

        if len(cluster_matches) > 0:
            return cluster_matches.to_dict('records')

        # NEW: Fuzzy matching fallback for entity resolution
        return self.find_customers_fuzzy(customer_input, customers_df)

    def _load_cached_files(self, customer_name: str) -> List[Dict]:
        """Master method - handles ALL cache operations for a customer"""

        # 1. Find customer in CSV
        customer_row = self._find_customer_by_name(customer_name)
        if not customer_row:
            return []

        # 2. Get or generate cache_path
        cache_path_raw = customer_row.get('cache_path', '')
        if pd.isna(cache_path_raw) or cache_path_raw is None:
            cache_path = ''
        else:
            cache_path = str(cache_path_raw).strip()

        if not cache_path:
            cache_path = self._sanitize_customer_name(customer_name)
            self._update_csv_cache_path(customer_name, cache_path)  # Save back to CSV

        # 3. Ensure directory exists
        cache_dir = os.path.join(self.cache_directory, cache_path)
        os.makedirs(cache_dir, exist_ok=True)

        # 4. Download reference URL to cache (if not already cached)
        reference_url_raw = customer_row.get('reference 1', '')
        if pd.isna(reference_url_raw) or reference_url_raw is None:
            reference_url = ''
        else:
            reference_url = str(reference_url_raw).strip()

        if reference_url:
            self._download_reference_to_cache(reference_url, cache_dir, customer_name)

        # 5. Load and extract all cached files
        research_data = []
        if os.path.exists(cache_dir):
            for filename in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        content = self._extract_content_by_type(file_path)
                        if content:
                            file_ext = os.path.splitext(filename)[1].lower()
                            research_data.append({
                                'source': file_path,
                                'source_type': 'cached_file',
                                'file_type': file_ext.lstrip('.'),
                                'content': content,
                                'metadata': {
                                    'filename': filename,
                                    'customer_name': customer_name,
                                    'cache_path': cache_path,
                                    'last_modified': datetime.fromtimestamp(
                                        os.path.getmtime(file_path)
                                    ).isoformat()
                                }
                            })
                    except Exception:
                        continue

        return research_data

    def _find_customer_by_name(self, customer_name: str) -> Optional[Dict]:
        """Find a specific customer by name in the CSV"""
        customers_df = self._load_customers_csv()
        if customers_df is None:
            return None

        customer_name_clean = customer_name.strip().lower()
        matches = customers_df[
            customers_df['customer_name'].str.strip().str.lower() == customer_name_clean
            ]

        if len(matches) > 0:
            return matches.iloc[0].to_dict()
        return None

    def _update_csv_cache_path(self, customer_name: str, cache_path: str) -> None:
        """Update the cache_path for a customer in the CSV file"""
        try:
            customers_df = self._load_customers_csv()
            if customers_df is None:
                return

            # Find the customer row
            customer_name_clean = customer_name.strip().lower()
            mask = customers_df['customer_name'].str.strip().str.lower() == customer_name_clean

            if mask.any():
                # Update the cache_path column
                customers_df.loc[mask, 'cache_path'] = cache_path

                # Save back to CSV
                customers_df.to_csv(self.customers_file, index=False)
                print(f"✅ Updated cache_path for {customer_name}: {cache_path}")

        except Exception as e:
            print(f"⚠️ Warning: Could not update CSV cache_path for {customer_name}: {e}")

    def _download_reference_to_cache(self, reference_url: str, cache_dir: str, customer_name: str) -> None:
        """Download reference URL content to cache directory"""
        try:
            if reference_url.startswith('cache/') or os.path.exists(reference_url):
                print(f"📄 File already cached: {reference_url}")
                return

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
                        print(f"📄 Downloaded PDF reference for {customer_name}")
            else:
                # Handle web URLs
                content = self._download_web_content(reference_url)
                if content:
                    # Save as HTML/text file in cache
                    filename = "reference_web_content.html"
                    file_path = os.path.join(cache_dir, filename)

                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f"🌐 Downloaded web reference for {customer_name}")

        except Exception as e:
            print(f"⚠️ Warning: Could not download reference for {customer_name}: {e}")
    
    def _download_web_content(self, url: str) -> Optional[str]:
        """Download and extract web content"""
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
            
            return clean_text if len(clean_text) > 100 else None
            
        except Exception:
            return None
    
    def _extract_content_by_type(self, file_path: str) -> str:
        """Central dispatcher for all file types"""
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.pdf':
            return self._extract_pdf_content(file_path)
        elif ext == '.html':
            return self._extract_html_content(file_path)
        elif ext == '.md':
            return self._extract_md_content(file_path)
        elif ext == '.docx':
            return self._extract_docx_content(file_path)
        elif ext == '.xlsx':
            return self._extract_xlsx_content(file_path)
        elif ext == '.pptx':
            return self._extract_pptx_content(file_path)
        else:
            # Fallback to plain text
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                return self._truncate_content(content)
            except Exception:
                return ""
    
    def _extract_pdf_content(self, file_path: str) -> str:
        """Extract text from PDF using existing PDF parser"""
        if not HAS_PDF_SUPPORT:
            return ""
        
        try:
            # Use existing PDF processor
            result = process_pdf(file_path)
            if result.get('success'):
                content = result.get('content', '')
                return self._truncate_content(content)
        except Exception:
            pass
        
        return ""
    
    def _extract_html_content(self, file_path: str) -> str:
        """Extract and clean text from HTML files"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                element.decompose()
            
            text = soup.get_text()
            
            # Clean text
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            clean_text = ' '.join(chunk for chunk in chunks if chunk)
            
            return self._truncate_content(clean_text)
            
        except Exception:
            return ""
    
    def _extract_md_content(self, file_path: str) -> str:
        """Extract text from Markdown files"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return self._truncate_content(content)
        except Exception:
            return ""
    
    def _extract_docx_content(self, file_path: str) -> str:
        """Extract text from Word documents using python-docx"""
        if not HAS_DOCX_SUPPORT:
            return ""
        
        try:
            doc = Document(file_path)
            paragraphs = []
            
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    paragraphs.append(paragraph.text)
            
            content = '\n'.join(paragraphs)
            return self._truncate_content(content)
            
        except Exception:
            return ""
    
    def _extract_xlsx_content(self, file_path: str) -> str:
        """Extract text content from Excel spreadsheets using openpyxl"""
        if not HAS_XLSX_SUPPORT:
            return ""
        
        try:
            workbook = load_workbook(file_path, data_only=True)
            content_parts = []
            
            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                content_parts.append(f"Sheet: {sheet_name}")
                
                for row in sheet.iter_rows(values_only=True):
                    row_text = []
                    for cell in row:
                        if cell is not None:
                            row_text.append(str(cell))
                    
                    if row_text:
                        content_parts.append(' | '.join(row_text))
            
            content = '\n'.join(content_parts)
            return self._truncate_content(content)
            
        except Exception:
            return ""
    
    def _extract_pptx_content(self, file_path: str) -> str:
        """Extract text from PowerPoint presentations using python-pptx"""
        if not HAS_PPTX_SUPPORT:
            return ""
        
        try:
            presentation = Presentation(file_path)
            content_parts = []
            
            for slide_num, slide in enumerate(presentation.slides, 1):
                content_parts.append(f"Slide {slide_num}:")
                
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        content_parts.append(shape.text)
            
            content = '\n'.join(content_parts)
            return self._truncate_content(content)
            
        except Exception:
            return ""
    
    def _truncate_content(self, content: str) -> str:
        """Truncate content if too long"""
        if not content:
            return ""
        
        if len(content) > self.max_content_length:
            content = content[:self.max_content_length] + "\n\n[Content truncated for analysis...]"
        
        return content.strip()
    
    def _sanitize_customer_name(self, customer_name: str) -> str:
        """Convert customer name to filesystem-safe directory name"""
        if not customer_name:
            return "unknown_customer"
        
        # Convert to lowercase and replace problematic characters
        sanitized = customer_name.lower()
        sanitized = re.sub(r'[^\w\s-]', '', sanitized)  # Remove special chars except spaces and hyphens
        sanitized = re.sub(r'[-\s]+', '_', sanitized)   # Replace spaces and hyphens with underscores
        
        return sanitized.strip('_')

    def find_customers_fuzzy(self, query: str, customers_df: pd.DataFrame) -> List[Dict]:
        """Find fuzzy matches for entity resolution support"""
        matches = []
        query_lower = query.lower().strip()

        for _, row in customers_df.iterrows():
            customer_name = str(row['customer_name']).lower()
            cluster_name = str(row.get('cluster', '')).lower()

            # Check similarity scores
            name_score = calculate_similarity(query_lower, customer_name)
            cluster_score = calculate_similarity(query_lower, cluster_name) if cluster_name else 0

            if name_score > 0.6 or cluster_score > 0.6:
                row_dict = row.to_dict()
                row_dict['similarity_score'] = max(name_score, cluster_score)
                matches.append(row_dict)

        # Sort by similarity score
        matches.sort(key=lambda x: x['similarity_score'], reverse=True)
        return matches
