"""
File Upload Processor - Conversational File Upload Handler
Handles drag & drop file uploads through conversational interface
Integrates with existing content pipeline and PDF processing infrastructure
"""

import os
from datetime import datetime
from typing import Dict, Any, Optional
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# Import existing PDF processing
try:
    from pdf_parser import process_pdf, get_pdf_capabilities
    HAS_PDF_SUPPORT = True
    print("✅ PDF processing available for uploads")
except ImportError:
    HAS_PDF_SUPPORT = False
    print("⚠️ PDF processing not available for uploads")

class ConversationalFileUploader:
    """
    Handles file uploads triggered by conversational commands
    Leverages existing PDF processing infrastructure
    """
    
    def __init__(self, content_curator, enhanced_chat_manager=None, upload_dir: str = "uploads"):
        self.content_curator = content_curator
        self.enhanced_chat_manager = enhanced_chat_manager
        self.upload_dir = upload_dir
        
        # Security configuration
        self.max_file_size = 50 * 1024 * 1024  # 50MB limit
        self.allowed_extensions = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.doc': 'application/msword',
            '.rtf': 'application/rtf',
            '.md': 'text/markdown'
        }
        
        # Create upload directory
        os.makedirs(upload_dir, exist_ok=True)
        
        # Check PDF capabilities using existing infrastructure
        if HAS_PDF_SUPPORT:
            try:
                self.pdf_capabilities = get_pdf_capabilities()
                print(f"✅ PDF capabilities: {self.pdf_capabilities}")
            except Exception as e:
                print(f"⚠️ Error checking PDF capabilities: {e}")
                self.pdf_capabilities = {'any_available': False}
    
    def validate_file_security(self, file_storage: FileStorage) -> tuple[bool, str]:
        """Security validation for uploaded files"""
        try:
            # Check file size
            file_storage.seek(0, 2)  # Seek to end
            size = file_storage.tell()
            file_storage.seek(0)  # Reset to beginning
            
            if size > self.max_file_size:
                return False, f"File too large. Maximum size: {self.max_file_size // (1024*1024)}MB"
            
            if size == 0:
                return False, "File is empty"
            
            # Check filename
            if not file_storage.filename:
                return False, "No filename provided"
                
            filename = secure_filename(file_storage.filename)
            if not filename:
                return False, "Invalid filename"
                
            # Check file extension
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext not in self.allowed_extensions:
                allowed = ', '.join(self.allowed_extensions.keys())
                return False, f"File type '{file_ext}' not allowed. Supported: {allowed}"
            
            # Validate file signature
            file_content = file_storage.read(8192)  # Read first 8KB
            file_storage.seek(0)  # Reset for later processing
            
            if not self._validate_file_signature(file_content, file_ext):
                return False, f"File content doesn't match extension '{file_ext}'"
                
            return True, "File is safe"
            
        except Exception as e:
            return False, f"File validation error: {str(e)}"
    
    def _validate_file_signature(self, content: bytes, expected_ext: str) -> bool:
        """Validate file signature matches extension"""
        if expected_ext == '.pdf':
            return content.startswith(b'%PDF-')
        elif expected_ext in ['.txt', '.md']:
            try:
                content.decode('utf-8')
                return True
            except UnicodeDecodeError:
                return False
        elif expected_ext == '.docx':
            return content.startswith(b'PK\x03\x04') or content.startswith(b'PK\x05\x06')
        elif expected_ext == '.doc':
            return content.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
        elif expected_ext == '.rtf':
            return content.startswith(b'{\\rtf')
        
        return True  # Default allow if we can't validate

    def process_uploaded_file(self, file_storage: FileStorage, session_id: str) -> Dict[str, Any]:
        """Main file processing method"""
        try:
            print(f"📁 Processing uploaded file: {file_storage.filename}")

            # Security validation
            is_safe, error_msg = self.validate_file_security(file_storage)
            if not is_safe:
                return {
                    'success': False,
                    'error': f"Security validation failed: {error_msg}",
                    'filename': file_storage.filename
                }

            # Generate secure filename
            original_filename = secure_filename(file_storage.filename)
            file_ext = os.path.splitext(original_filename)[1].lower()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"upload_{timestamp}_{original_filename}"

            # Save file temporarily
            temp_file_path = os.path.join(self.upload_dir, safe_filename)
            file_storage.save(temp_file_path)

            try:
                # Extract content based on file type
                content_result = self._extract_file_content(temp_file_path, file_ext)

                if not content_result['success']:
                    return content_result

                # Create article data for unified processing pipeline
                article_data = {
                    'url': f"file_upload://{original_filename}",
                    'title': self._generate_title_from_filename(original_filename, content_result['content']),
                    'content': content_result['content'],
                    'source': 'file_upload',
                    'published_date': datetime.now().isoformat(),
                    'search_topic': 'file_upload',
                    'file_type': file_ext,
                    'original_filename': original_filename,
                    'file_size': os.path.getsize(temp_file_path)
                }

                # Process through unified pipeline (reconvergent flow)
                if self.enhanced_chat_manager:
                    try:
                        print(f"🔗 Processing through unified pipeline: {article_data['title']}")

                        # Use content curator to process and store in database
                        session_context = {'session_id': session_id, 'file_data': article_data}

                        # Create a temporary article data structure that mimics URL processing
                        temp_article = {
                            'title': article_data['title'],
                            'url': article_data['url'],
                            'source': article_data['source'],
                            'content': article_data['content'],
                            'published_date': article_data['published_date'],
                            'search_topic': article_data['search_topic']
                        }

                        # Generate summary and relevance using content curator pipeline
                        summary, relevance_score = self.content_curator.generate_summary_and_relevance(
                            temp_article,
                            session_context=session_context
                        )

                        # Update article data with AI processing results
                        article_data.update({
                            'ai_summary': summary,
                            'relevance_score': relevance_score,
                            'session_id': session_id
                        })

                        # Add to database using existing pipeline
                        search_result_format = {
                            'title': article_data['title'],
                            'url': article_data['url'],
                            'source': article_data['source'],
                            'published_date': article_data['published_date'],
                            'search_topic': article_data['search_topic'],
                            'ai_summary': summary,
                            'full_content': article_data['content'],
                            'snippet': article_data['content'][:200] + '...' if len(article_data['content']) > 200 else
                            article_data['content'],
                            'relevance_score': relevance_score
                        }

                        self.content_curator.add_search_result_to_database(search_result_format, session_context)
                        print(f"✅ File added to database: {article_data['title']}")

                        # Set up auto-discussion trigger (like URL paste does)
                        article_data['auto_discuss'] = True
                        article_data[
                            'discussion_message'] = f"Let's discuss this uploaded file: {article_data['title']}"

                        print(f"🗣️ Auto-discussion prepared for: {article_data['title']}")

                    except Exception as e:
                        print(f"⚠️ Error in enhanced processing: {e}")
                        # Fallback to basic processing
                        article_data.update({
                            'ai_summary': f"Uploaded file: {article_data['title']} (processing error: {str(e)})",
                            'relevance_score': 5.0,
                            'session_id': session_id,
                            'auto_discuss': False  # Don't auto-discuss on error
                        })
                else:
                    # No enhanced chat available - basic fallback
                    print("⚠️ Enhanced chat not available - using basic processing")
                    article_data.update({
                        'ai_summary': f"Uploaded file: {article_data['title']} (basic mode)",
                        'relevance_score': 5.0,
                        'session_id': session_id,
                        'auto_discuss': False  # Don't auto-discuss without enhanced chat
                    })

                # Return success with auto-discussion trigger
                return {
                    'success': True,
                    'type': 'file_upload',
                    'article_data': article_data,
                    'message': f"✅ **File Upload Complete**\n\n**File:** {original_filename}\n**Title:** {article_data['title']}\n\nProcessing complete - ready for discussion!",
                    'auto_discuss': article_data.get('auto_discuss', False),
                    'discussion_message': article_data.get('discussion_message', ''),
                    'title': article_data['title'],
                    'relevance_score': article_data.get('relevance_score', 5.0)
                }

            finally:
                # Clean up temporary file
                try:
                    os.remove(temp_file_path)
                    print(f"🗑️ Cleaned up temp file: {safe_filename}")
                except Exception as e:
                    print(f"⚠️ Warning: Could not remove temp file: {e}")

        except Exception as e:
            print(f"⚠️ Error processing uploaded file: {e}")
            return {
                'success': False,
                'error': str(e),
                'filename': getattr(file_storage, 'filename', 'unknown')
            }
    
    def _extract_file_content(self, file_path: str, file_ext: str) -> Dict[str, Any]:
        """Extract text content from various file types"""
        try:
            if file_ext == '.pdf':
                return self._extract_pdf_content(file_path)
            elif file_ext in ['.txt', '.md']:
                return self._extract_text_content(file_path)
            elif file_ext == '.rtf':
                return self._extract_rtf_content(file_path)
            elif file_ext in ['.doc', '.docx']:
                return self._extract_word_content(file_path)
            else:
                return {
                    'success': False,
                    'error': f"Unsupported file type: {file_ext}"
                }
        except Exception as e:
            return {
                'success': False,
                'error': f"Content extraction failed: {str(e)}"
            }
    
    def _extract_pdf_content(self, file_path: str) -> Dict[str, Any]:
        """Extract content from PDF file using existing process_pdf function"""
        if not HAS_PDF_SUPPORT:
            return {
                'success': False,
                'error': "PDF processing not available"
            }
        
        if not self.pdf_capabilities.get('any_available', False):
            return {
                'success': False,
                'error': "PDF processing libraries not installed. Please install pdfplumber, PyPDF2, or pymupdf."
            }
        
        try:
            print(f"📄 Processing PDF using existing infrastructure: {file_path}")
            
            # Use existing process_pdf function - it handles both URLs and file paths
            result = process_pdf(file_path)
            
            if result.get('success'):
                content = result.get('content', '')
                if content and content.strip():
                    print(f"✅ PDF processed successfully: {len(content)} characters extracted")
                    return {
                        'success': True,
                        'content': content,
                        'extraction_method': result.get('extraction_method', 'process_pdf'),
                        'pages_processed': result.get('pages_processed', 0)
                    }
                else:
                    return {
                        'success': False,
                        'error': "No text content extracted from PDF"
                    }
            else:
                error_msg = result.get('error', 'PDF processing failed')
                print(f"❌ PDF processing failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            print(f"❌ PDF extraction error: {e}")
            return {
                'success': False,
                'error': f"PDF extraction failed: {str(e)}"
            }
    
    def _extract_text_content(self, file_path: str) -> Dict[str, Any]:
        """Extract content from plain text files"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            if not content.strip():
                return {
                    'success': False,
                    'error': "File is empty"
                }
                
            return {
                'success': True,
                'content': content,
                'extraction_method': 'text'
            }
            
        except UnicodeDecodeError:
            # Try other encodings
            for encoding in ['latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    return {
                        'success': True,
                        'content': content,
                        'extraction_method': f'text_{encoding}'
                    }
                except UnicodeDecodeError:
                    continue
                    
            return {
                'success': False,
                'error': "Could not decode text file"
            }
    
    def _extract_rtf_content(self, file_path: str) -> Dict[str, Any]:
        """Extract content from RTF files"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                rtf_content = f.read()
            
            # Basic RTF tag removal
            import re
            text = re.sub(r'\\[a-z]+\d*', '', rtf_content)
            text = re.sub(r'[{}]', '', text)
            text = text.replace('\\', '')
            
            return {
                'success': True,
                'content': text.strip(),
                'extraction_method': 'basic_rtf'
            }
                
        except Exception as e:
            return {
                'success': False,
                'error': f"RTF extraction failed: {str(e)}"
            }
    
    def _extract_word_content(self, file_path: str) -> Dict[str, Any]:
        """Extract content from Word documents - simplified for core functionality"""
        try:
            # Try python-docx for .docx files first
            if file_path.endswith('.docx'):
                try:
                    from docx import Document
                    doc = Document(file_path)
                    content = '\n'.join([paragraph.text for paragraph in doc.paragraphs])
                    if content.strip():
                        return {
                            'success': True,
                            'content': content,
                            'extraction_method': 'python-docx'
                        }
                except ImportError:
                    print("⚠️ python-docx not available for .docx files")
            
            # For .doc files or if .docx processing failed
            return {
                'success': False,
                'error': "Word document processing requires python-docx library. Install with: pip install python-docx"
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f"Word document extraction failed: {str(e)}"
            }
    
    def get_upload_capabilities(self) -> Dict[str, Any]:
        """Get file upload capabilities and supported formats"""
        capabilities = {
            'max_file_size_mb': self.max_file_size // (1024 * 1024),
            'allowed_extensions': list(self.allowed_extensions.keys()),
            'supported_mime_types': list(self.allowed_extensions.values()),
            'pdf_support': HAS_PDF_SUPPORT,
            'text_support': True,
            'rtf_support': True,
            'word_support': False  # Will be updated based on library availability
        }
        
        # Check for Word document support
        try:
            import docx
            capabilities['word_support'] = True
        except ImportError:
            pass
        
        # Add PDF capabilities if available
        if HAS_PDF_SUPPORT and hasattr(self, 'pdf_capabilities'):
            capabilities['pdf_capabilities'] = self.pdf_capabilities
        
        return capabilities
    
    def _generate_title_from_filename(self, filename: str, content: str) -> str:
        """Generate human-readable title"""
        base_name = os.path.splitext(filename)[0]
        title = base_name.replace('_', ' ').replace('-', ' ')
        title = ' '.join(word.capitalize() for word in title.split())
        
        # Try to extract better title from content
        if content:
            lines = content.strip().split('\n')[:5]
            for line in lines:
                line = line.strip()
                if line and len(line) > 5 and len(line) < 100:
                    if not line.endswith('.') and ' ' in line:
                        return line[:80] + ('...' if len(line) > 80 else '')
        
        return title if title else "Uploaded Document"

# Factory function for easy integration
def create_file_uploader(content_curator, enhanced_chat_manager=None) -> ConversationalFileUploader:
    """Create file uploader instance"""
    return ConversationalFileUploader(content_curator, enhanced_chat_manager)

def get_upload_capabilities() -> Dict[str, Any]:
    """Get upload capabilities without creating full uploader instance"""
    uploader = ConversationalFileUploader(None)
    return uploader.get_upload_capabilities()