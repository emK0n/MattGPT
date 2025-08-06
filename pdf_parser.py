"""
PDF Parser Module
Handles PDF detection, downloading, and text extraction
Separate module to avoid burdening core application with PDF dependencies
"""

import requests
import io
import re
import os
import requests
from urllib.parse import urlparse
from typing import Optional, Dict, Any
from datetime import datetime

# PDF parsing imports with graceful fallbacks
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


class PDFProcessor:
    """Handles PDF detection, download, and text extraction"""
    
    def __init__(self, max_pages: int = 50, max_content_length: int = 50000):
        """
        Initialize PDF processor
        
        Args:
            max_pages: Maximum number of pages to process per PDF
            max_content_length: Maximum characters to extract
        """
        self.max_pages = max_pages
        self.max_content_length = max_content_length
        self.capabilities = self._check_capabilities()
    
    def _check_capabilities(self) -> Dict[str, bool]:
        """Check which PDF parsing libraries are available"""
        return {
            'pypdf2': HAS_PYPDF2,
            'pdfplumber': HAS_PDFPLUMBER,
            'pymupdf': HAS_PYMUPDF,
            'any_available': any([HAS_PYPDF2, HAS_PDFPLUMBER, HAS_PYMUPDF])
        }
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Get processor capabilities and recommendations"""
        caps = self.capabilities.copy()
        
        if not caps['any_available']:
            caps['recommendations'] = [
                "pip install pdfplumber  # Recommended for best text extraction",
                "pip install PyPDF2      # Alternative option",
                "pip install pymupdf     # Advanced features"
            ]
            caps['status'] = 'no_libraries'
        elif caps['pdfplumber']:
            caps['status'] = 'optimal'
            caps['primary_method'] = 'pdfplumber'
        elif caps['pymupdf']:
            caps['status'] = 'good'
            caps['primary_method'] = 'pymupdf'
        elif caps['pypdf2']:
            caps['status'] = 'basic'
            caps['primary_method'] = 'pypdf2'
        
        return caps
    
    def is_pdf_url(self, url: str) -> bool:
        """
        Check if URL points to a PDF file
        
        Args:
            url: URL to check
            
        Returns:
            True if URL appears to point to a PDF
        """
        try:
            # Quick check: file extension
            parsed_url = urlparse(url)
            if parsed_url.path.lower().endswith('.pdf'):
                return True
            
            # More thorough check: HEAD request for content-type
            try:
                response = requests.head(url, timeout=10, allow_redirects=True)
                content_type = response.headers.get('content-type', '').lower()
                if 'application/pdf' in content_type:
                    return True
            except requests.RequestException:
                pass
                
            return False
        except Exception:
            return False
    
    def extract_pdf_text(self, pdf_url: str) -> Optional[str]:
        """
        Extract text from PDF URL using best available method
        
        Args:
            pdf_url: URL of the PDF to process
            
        Returns:
            Extracted text or None if extraction failed
        """
        if not self.capabilities['any_available']:
            print("❌ No PDF parsing libraries available")
            return None
        
        print(f"📄 Extracting text from PDF: {pdf_url}")
        
        try:
            pdf_content = self._download_pdf(pdf_url)
            if not pdf_content:
                return None
            
            # Try extraction methods in order of preference
            extraction_methods = []
            
            if HAS_PDFPLUMBER:
                extraction_methods.append(('pdfplumber', self._extract_with_pdfplumber))
            if HAS_PYMUPDF:
                extraction_methods.append(('pymupdf', self._extract_with_pymupdf))
            if HAS_PYPDF2:
                extraction_methods.append(('pypdf2', self._extract_with_pypdf2))
            
            for method_name, method_func in extraction_methods:
                try:
                    print(f"   🔧 Trying {method_name} extraction...")
                    
                    # Reset stream position
                    pdf_content.seek(0)
                    
                    text = method_func(pdf_content)
                    if text and text.strip():
                        print(f"   ✅ {method_name} extracted {len(text)} characters")
                        return self._clean_pdf_text(text)
                        
                except Exception as e:
                    print(f"   ❌ {method_name} failed: {e}")
                    continue
            
            print("   ❌ All extraction methods failed")
            return None
            
        except Exception as e:
            print(f"   ❌ PDF processing error: {e}")
            return None

    def _download_pdf(self, pdf_url: str) -> Optional[io.BytesIO]:
        """Download PDF from URL or read from local file"""
        try:
            # Check if it's a local file path
            if os.path.exists(pdf_url):
                print(f"   📁 Reading local PDF file...")
                with open(pdf_url, 'rb') as f:
                    content = f.read()

                # Verify it's actually a PDF
                if not content.startswith(b'%PDF'):
                    print("   ❌ File is not a valid PDF")
                    return None

                return io.BytesIO(content)

            # Original URL download logic
            print(f"   ⬇️ Downloading PDF...")
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()

            # Verify it's actually a PDF
            if not response.content.startswith(b'%PDF'):
                print("   ❌ Downloaded content is not a PDF")
                return None

            return io.BytesIO(response.content)

        except Exception as e:
            print(f"   ❌ Error accessing PDF: {e}")
            return None
    
    def _extract_with_pdfplumber(self, pdf_content: io.BytesIO) -> str:
        """Extract text using pdfplumber"""
        with pdfplumber.open(pdf_content) as pdf:
            text_parts = []
            
            for page_num, page in enumerate(pdf.pages):
                if page_num >= self.max_pages:
                    break
                    
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            
            return "\n\n".join(text_parts)
    
    def _extract_with_pymupdf(self, pdf_content: io.BytesIO) -> str:
        """Extract text using PyMuPDF (fitz)"""
        pdf_document = fitz.open(stream=pdf_content.read(), filetype="pdf")
        text_parts = []
        
        try:
            for page_num in range(min(pdf_document.page_count, self.max_pages)):
                page = pdf_document[page_num]
                page_text = page.get_text()
                if page_text:
                    text_parts.append(page_text)
        finally:
            pdf_document.close()
        
        return "\n\n".join(text_parts)
    
    def _extract_with_pypdf2(self, pdf_content: io.BytesIO) -> str:
        """Extract text using PyPDF2"""
        pdf_reader = PyPDF2.PdfReader(pdf_content)
        text_parts = []
        
        for page_num in range(min(len(pdf_reader.pages), self.max_pages)):
            page = pdf_reader.pages[page_num]
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        
        return "\n\n".join(text_parts)
    
    def _clean_pdf_text(self, text: str) -> str:
        """Clean and normalize extracted PDF text"""
        if not text:
            return ""
        
        # Remove excessive whitespace and normalize
        text = re.sub(r' +', ' ', text)
        text = re.sub(r'\n+', '\n\n', text)
        
        # Remove common PDF artifacts
        text = re.sub(r'\x0c', '', text)  # Form feed characters
        text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)  # Control characters
        
        # Limit total length
        if len(text) > self.max_content_length:
            text = text[:self.max_content_length] + "\n\n[Content truncated due to length...]"
        
        return text.strip()
    
    def process_pdf_url(self, pdf_url: str, title: str = None) -> Dict[str, Any]:
        """
        Complete PDF processing workflow
        
        Args:
            pdf_url: URL of the PDF
            title: Optional title for the PDF
            
        Returns:
            Dictionary with processing results
        """
        result = {
            'url': pdf_url,
            'title': title or self._extract_title_from_url(pdf_url),
            'source': self._extract_source_from_url(pdf_url),
            'content_type': 'pdf',
            'success': False,
            'error': None,
            'content': None,
            'content_length': 0,
            'pages_processed': 0,
            'extraction_method': None,
            'published_date': datetime.now().isoformat()
        }
        
        # Verify it's a PDF URL
        if not self.is_pdf_url(pdf_url):
            result['error'] = 'URL does not appear to point to a PDF file'
            return result
        
        # Check if we have PDF processing capabilities
        if not self.capabilities['any_available']:
            result['error'] = 'No PDF processing libraries available. Install: pip install pdfplumber'
            return result
        
        # Extract text
        extracted_text = self.extract_pdf_text(pdf_url)
        
        if extracted_text:
            result.update({
                'success': True,
                'content': extracted_text,
                'content_length': len(extracted_text),
                'extraction_method': self.capabilities.get('primary_method', 'unknown')
            })
        else:
            result['error'] = 'Failed to extract text from PDF'
        
        return result
    
    def _extract_title_from_url(self, url: str) -> str:
        """Extract a title from PDF URL"""
        try:
            parsed = urlparse(url)
            path_parts = parsed.path.strip('/').split('/')
            if path_parts and path_parts[-1]:
                filename = path_parts[-1]
                # Remove .pdf extension and clean up
                title = filename.replace('.pdf', '').replace('-', ' ').replace('_', ' ')
                return title.title()
            return f"PDF from {parsed.netloc}"
        except:
            return "PDF Document"
    
    def _extract_source_from_url(self, url: str) -> str:
        """Extract source name from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain.replace('.com', '').replace('.org', '').replace('.edu', '').title()
        except:
            return "PDF Source"


def is_pdf_url(url: str) -> bool:
    """Quick check if URL points to a PDF"""
    processor = PDFProcessor()
    return processor.is_pdf_url(url)


def extract_pdf_text(pdf_url: str) -> Optional[str]:
    """Extract text from PDF URL"""
    processor = PDFProcessor()
    return processor.extract_pdf_text(pdf_url)


def process_pdf(pdf_url: str, title: str = None) -> Dict[str, Any]:
    """Process PDF URL and return complete result"""
    processor = PDFProcessor()
    return processor.process_pdf_url(pdf_url, title)


def get_pdf_capabilities() -> Dict[str, Any]:
    """Get PDF processing capabilities"""
    processor = PDFProcessor()
    return processor.get_capabilities()