"""
Customer Insights Module - Refactored for Scenario-Based Analysis
Analyzes individual customers vs customer clusters with proper process isolation
"""

import sqlite3
import pandas as pd
import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple, Generator
import re
import ollama
from contextlib import contextmanager
from entity_resolver import EntityResolutionPipeline
from fuzzy_commands import detect_fuzzy_command

# Import PDF support if available
try:
    from pdf_parser import is_pdf_url, process_pdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

# Import cache manager
try:
    from cache_mgmt import CustomerCacheManager
    HAS_CACHE_SUPPORT = True
except ImportError:
    HAS_CACHE_SUPPORT = False


class CustomerDataManager:
    """Manages customer data loading and organization"""
    
    def __init__(self, data_directory: str = "data"):
        self.data_directory = data_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        self.customers_data = None
        self._last_csv_mtime = None
        self._load_customers()
        self._load_company_data()

    def _load_company_data(self):
        """Load company information from JSON file"""
        try:
            company_file = os.path.join(self.data_directory, "my_company.JSON")
            if os.path.exists(company_file):
                with open(company_file, 'r', encoding='utf-8') as f:
                    self.company_data = json.load(f)
                    industries_count = len(self.company_data.get('industries', []))
                    products_count = len(self.company_data.get('products', []))
                    specialties_count = len(self.company_data.get('specialties', []))
                    print(
                        f"✅ Loaded company data: {industries_count} industries, {products_count} products, {specialties_count} specialties")
            else:
                print("⚠️ my_company.JSON not found - battle cards will use basic information only")
                self.company_data = None
        except Exception as e:
            print(f"❌ Error loading company data: {e}")
            self.company_data = None

    def _load_customers(self):
        """Load customers from CSV file"""
        try:
            if os.path.exists(self.customers_file):
                self.customers_data = pd.read_csv(self.customers_file)
                self.customers_data.columns = self.customers_data.columns.str.strip()
                print(f"✅ Loaded {len(self.customers_data)} customers from CSV")

                # Validate required columns
                required_columns = ['cluster', 'customer_name']
                missing_columns = [col for col in required_columns if col not in self.customers_data.columns]

                if missing_columns:
                    print(f"⚠️ Missing required columns: {missing_columns}")
                    self.customers_data = None
                    return

                # Validate reference columns (reference 1 is required, 2-5 are optional)
                if 'reference 1' not in self.customers_data.columns:
                    print("⚠️ Missing required column: 'reference 1'")
                    self.customers_data = None
                    return

                # Add optional reference columns if missing
                for col in ['cluster', 'customer_name', 'reference 1']:
                    if col in self.customers_data.columns:
                        self.customers_data[col] = self.customers_data[col].astype(str).str.strip()

                print(f"   Clusters: {self.customers_data['cluster'].nunique()}")
                print(f"   Columns: {list(self.customers_data.columns)}")

            else:
                print(f"⚠️ Customer CSV file not found: {self.customers_file}")
                self.customers_data = None

        except Exception as e:
            print(f"❌ Error loading customers CSV: {e}")
            self.customers_data = None

        # Track file timestamp after loading (successful or not)
        try:
            if os.path.exists(self.customers_file):
                self._last_csv_mtime = os.path.getmtime(self.customers_file)
        except Exception:
            self._last_csv_mtime = None

    def _check_and_reload_customers(self):
        """Check if CSV has changed and reload if needed"""
        try:
            if os.path.exists(self.customers_file):
                current_mtime = os.path.getmtime(self.customers_file)

                # If we haven't checked before OR file has been modified
                if self._last_csv_mtime is None or current_mtime > self._last_csv_mtime:
                    print(f"🔄 CSV file changed, reloading customers...")
                    self._load_customers()
                    self._last_csv_mtime = current_mtime
                    return True  # File was reloaded
                return False  # No changes
            else:
                # File doesn't exist, clear data if we had some
                if self.customers_data is not None:
                    print(f"⚠️ CSV file no longer exists, clearing customer data")
                    self.customers_data = None
                    self._last_csv_mtime = None
                return False
        except Exception as e:
            print(f"❌ Error checking CSV file: {e}")
            return False

    def get_company_data(self) -> Optional[Dict]:
        """Get loaded company data"""
        return self.company_data

    def get_clusters(self) -> List[str]:
        """Get list of unique clusters"""
        self._check_and_reload_customers()  # Check for CSV changes
        if self.customers_data is None:
            return []
        unique_clusters = self.customers_data['cluster'].unique().tolist()
        return sorted(unique_clusters, key=self.natural_sort_key)

    def natural_sort_key(self, text: str) -> List:
        """Generate a sorting key for natural/alphanumeric sorting"""
        import re

        def convert(part):
            if part.isdigit():
                return int(part)
            return part.lower()

        return [convert(c) for c in re.split(r'(\d+)', text)]

    def get_customers_by_cluster(self, cluster: str) -> List[Dict]:
        """Get customers in a specific cluster"""
        self._check_and_reload_customers()  # Check for CSV changes
        if self.customers_data is None:
            return []

        cluster_customers = self.customers_data[self.customers_data['cluster'] == cluster]
        return cluster_customers.to_dict('records')

    def get_all_customers(self) -> List[Dict]:
        """Get all customers"""
        self._check_and_reload_customers()  # Check for CSV changes
        if self.customers_data is None:
            return []
        return self.customers_data.to_dict('records')

    def get_customer_by_name(self, customer_name: str) -> Optional[Dict]:
        """Get specific customer by name"""
        self._check_and_reload_customers()  # Check for CSV changes
        if self.customers_data is None:
            return None

        # Case-insensitive search
        customer_name_clean = customer_name.strip().lower()
        matches = self.customers_data[
            self.customers_data['customer_name'].str.strip().str.lower() == customer_name_clean
        ]
        
        if len(matches) > 0:
            return matches.iloc[0].to_dict()
        return None
    
    def search_customers(self, query: str) -> List[Dict]:
        """Search customers by name"""
        if self.customers_data is None:
            return []

        query_lower = query.strip().lower()

        # Search in customer name only (simplified)
        matches = self.customers_data[
            self.customers_data['customer_name'].str.strip().str.lower().str.contains(query_lower, na=False)
        ]
        
        return matches.to_dict('records')

    def find_customer_matches(self, customer_input: str) -> List[Dict]:
        """Find customers matching input (by name or cluster) - NEW METHOD"""
        self._check_and_reload_customers()  # Check for CSV changes
        if self.customers_data is None:
            return []

        customer_input_clean = customer_input.strip().lower()

        # Try exact customer name match first
        name_matches = self.customers_data[
            self.customers_data['customer_name'].str.strip().str.lower() == customer_input_clean
        ]
        
        if len(name_matches) > 0:
            return name_matches.to_dict('records')
        
        # Try cluster match
        cluster_matches = self.customers_data[
            self.customers_data['cluster'].str.strip().str.lower() == customer_input_clean
        ]
        
        if len(cluster_matches) > 0:
            return cluster_matches.to_dict('records')
        
        return []


class CustomerContentCollector:
    """Collects public information about customers from reference URLs"""
    
    def __init__(self, cache_directory: str = "cache", session_timeout: int = 15):
        self.cache_manager = CustomerCacheManager() if HAS_CACHE_SUPPORT else None
        self.session_timeout = session_timeout

    def collect_customer_data(self, customer_name: str) -> List[Dict]:
        """Collect all available data for a customer using cache manager"""
        if not self.cache_manager:
            print("⚠️ Cache manager not available")
            return []

        try:
            # Use cache manager to get customer research data
            research_data = self.cache_manager.get_customer_research(customer_name)
            if research_data:
                print(f"✅ Collected {len(research_data)} research items for {customer_name}")
                return research_data
            else:
                print(f"📂 No research data found for {customer_name}")
                return []
        except Exception as e:
            print(f"❌ Error collecting customer data for {customer_name}: {e}")
            return []


class CustomerInsightsAnalyzer:
    """Analyzes customer data against trending topics with scenario-based routing"""
    
    def __init__(self, content_curator, chat_assistant, data_manager=None):
        self.content_curator = content_curator
        self.chat_assistant = chat_assistant
        self.ollama_model = content_curator.ollama_model
        self.analysis_cache = {}  # Cache for follow-up questions
        self.data_manager = data_manager

    def get_boosted_articles_today(self) -> List[Dict]:
        """Get articles with relevance boost from today, with fallback"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')

            with sqlite3.connect(self.content_curator.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # First try to get today's boosted articles
                cursor.execute('''
                    SELECT *, 
                           (relevance_score + COALESCE(relevance_boost, 0)) as total_relevance,
                           COALESCE(relevance_boost, 0) as boost_score
                    FROM content 
                    WHERE DATE(added_date) = ? 
                    AND relevance_boost > 0
                    ORDER BY boost_score DESC, total_relevance DESC
                    LIMIT 20
                ''', (today,))

                articles = [dict(row) for row in cursor.fetchall()]

                # If no boosted articles found, get fallback articles
                if not articles:
                    articles = self._get_fallback_articles()

                return articles

        except Exception as e:
            print(f"❌ Error getting boosted articles: {e}")
            return self._get_fallback_articles()

    def _get_fallback_articles(self) -> List[Dict]:
        """Get fallback articles when no trending articles are available"""
        try:
            with sqlite3.connect(self.content_curator.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Get recent high-relevance articles from last 3 days
                three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')

                cursor.execute('''
                    SELECT *, 
                           relevance_score as total_relevance,
                           0 as boost_score
                    FROM content 
                    WHERE DATE(added_date) >= ? 
                    AND relevance_score >= 7.0
                    ORDER BY relevance_score DESC, published_date DESC
                    LIMIT 15
                ''', (three_days_ago,))

                articles = [dict(row) for row in cursor.fetchall()]

                # If still no articles, get any recent articles
                if not articles:
                    cursor.execute('''
                        SELECT *, 
                               relevance_score as total_relevance,
                               0 as boost_score
                        FROM content 
                        WHERE DATE(added_date) >= ? 
                        ORDER BY relevance_score DESC, published_date DESC
                        LIMIT 10
                    ''', (three_days_ago,))

                    articles = [dict(row) for row in cursor.fetchall()]

                return articles

        except Exception as e:
            print(f"❌ Error getting fallback articles: {e}")
            return []

    def load_current_events_analysis(self) -> Optional[Dict]:
        """Load current events analysis for cluster challenges"""
        try:
            # Try to find current events analysis file
            data_dir = getattr(self.data_manager, 'data_directory', 'data')
            topics_dir = os.environ.get('TOPICS_DIR', 'currentevents')
            
            if not os.path.exists(topics_dir):
                print("⚠️ Topics directory not found")
                return None
            
            # Look for today's current events file
            today = datetime.now().strftime('%Y%m%d')
            filename = f"current_events_analysis_{today}.json"
            filepath = os.path.join(topics_dir, filename)
            
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"✅ Loaded current events analysis: {filename}")
                    return data
            else:
                print(f"⚠️ Current events analysis not found: {filename}")
                return None
                
        except Exception as e:
            print(f"❌ Error loading current events analysis: {e}")
            return None

    # =============================================================================
    # SCENARIO 1: INDIVIDUAL CUSTOMER ANALYSIS
    # =============================================================================

    def analyze_individual_customer(self, customer_name: str, customer_data: List[Dict], 
                              boosted_articles: List[Dict], active_articles: List[Dict] = None) -> Generator[str, None, None]:
        """SCENARIO 1: Complete individual customer analysis with battle card"""

        # Enhanced header with article context
        active_articles = active_articles or []
        if active_articles:
            # Show article context prominently
            article_title = active_articles[0].get('title', 'Unknown Article')
            yield f"🎯 **Article-to-Customer Analysis**\n\n"
            yield f"📖 **Article**: {article_title}\n"
            yield f"🏢 **Customer**: {customer_name}\n\n"
            yield f"💡 **Finding connections and conversation opportunities...**\n\n"
        else:
            yield f"\n<hr><h2>🎯 Individual Customer Analysis of <span style='color: #4169E1;'>{customer_name}</span></h2>\n"
        
        if not customer_data:
            yield "🤷 **No profile data available** - skipping analysis\n"
            return
        
        if not boosted_articles:
            yield "📊 **No trending articles today** - limited analysis available\n"
            return

        # Step 1: Customer Profile Analysis
        if not active_articles:  # Only run company analysis when NOT discussing articles
            yield "📊 **Customer Profile Analysis**\n"
            profile_insights = []
            for chunk in self._stream_customer_profile_analysis(customer_name, customer_data):
                yield chunk
                profile_insights.append(chunk)
        else:
            profile_insights = []
        
        # Step 2: Executive Conversation Starters  
        yield "\n\n<hr>\n🗣️ **Executive Conversation Starters**\n"
        conversation_starters = []
        for chunk in self._stream_conversation_starters(customer_name, customer_data, boosted_articles):
            yield chunk
            conversation_starters.append(chunk)
            
        # Step 3: Battle Card Generation
        yield "\n\n<hr>\n⚔️ **Battle Card Generation**\n"
        company_data = self.data_manager.get_company_data() if self.data_manager else {}
        for chunk in self._stream_battle_card_generation(customer_name, customer_data, 
                                                       conversation_starters, company_data):
            yield chunk

        # Cache for follow-up questions
        try:
            self.analysis_cache[customer_name] = {
                'analysis_type': 'individual_customer',
                'customer_data': customer_data,
                'articles': boosted_articles,
                'timestamp': datetime.now().isoformat(),
                'profile_insights': ''.join(profile_insights),
                'conversation_starters': ''.join(conversation_starters)
            }
            print(f"✅ Cached individual analysis for {customer_name}")
        except Exception as e:
            yield f"\n⚠️ *Note: Analysis completed but caching failed: {str(e)}*\n"

    def _stream_customer_profile_analysis(self, customer_name: str, 
                                        customer_data: List[Dict]) -> Generator[str, None, None]:
        """Stream customer profile analysis"""
        try:
            # Build customer intelligence context
            context_parts = [f"CUSTOMER INTELLIGENCE ANALYSIS FOR: {customer_name}\n"]
            
            # Process customer research data
            for i, item in enumerate(customer_data, 1):
                content = item.get('content', '')
                title = item.get('metadata', {}).get('filename', f'Document {i}')
                
                if content:
                    # Extract business-relevant insights
                    business_keywords = [
                        'strategy', 'technology', 'digital transformation', 'cloud', 'AI',
                        'artificial intelligence', 'machine learning', 'automation', 'efficiency',
                        'cybersecurity', 'infrastructure', 'operations', 'growth', 'innovation',
                        'challenges', 'opportunities', 'investment', 'modernization'
                    ]
                    
                    sentences = content.split('.')
                    key_insights = []
                    for sentence in sentences[:30]:
                        sentence = sentence.strip()
                        if len(sentence) > 30 and any(
                                keyword.lower() in sentence.lower() for keyword in business_keywords):
                            key_insights.append(sentence)
                        if len(key_insights) >= 5:
                            break
                    
                    context_parts.append(f"\nSOURCE {i}: {title}")
                    context_parts.append(f"Content: {content[:1000]}")
                    if key_insights:
                        context_parts.append(f"Key Business Insights: {'. '.join(key_insights)}")

            customer_context = '\n'.join(context_parts)

            # Generate streaming profile analysis
            profile_prompt = f"""Analyze this customer's business profile and extract key intelligence:

{customer_context}

Provide a concise business intelligence summary covering:
1. Industry and business focus
2. Technology initiatives and digital maturity
3. Key business challenges and priorities
4. Strategic direction and growth areas

Keep analysis factual and specific to information provided. Focus on business and technology insights relevant for executive conversations."""

            #yield "Analyzing customer profile...\n"
            
            for chunk in ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': profile_prompt}
            ], stream=True):
                yield chunk['message']['content']
                
        except Exception as e:
            yield f"❌ Profile analysis failed: {str(e)}\n"

    def _stream_conversation_starters(self, customer_name: str, customer_data: List[Dict], 
                                    boosted_articles: List[Dict]) -> Generator[str, None, None]:
        """Stream executive conversation starter generation"""
        try:
            # Build articles context
            articles_context = []
            for i, article in enumerate(boosted_articles[:10], 1):
                title = article.get('title', 'Unknown')
                summary = article.get('ai_summary', '')
                topics = article.get('key_topics', [])
                
                articles_context.append(f"ARTICLE {i}: {title}")
                if summary:
                    articles_context.append(f"Summary: {summary[:500]}")
                if topics:
                    articles_context.append(f"Topics: {', '.join(topics[:5])}")
                articles_context.append("")

            articles_text = '\n'.join(articles_context)

            conversation_prompt = f"""Based on today's trending business and technology topics, generate 3 executive conversation starters specifically relevant to {customer_name}.

CUSTOMER CONTEXT:
{customer_name} - Use the customer profile analysis above to understand their business focus.

TODAY'S TRENDING TOPICS:
{articles_text}

Generate exactly 3 conversation starters that:
1. Reference specific current trends/developments from the articles
2. Connect to the customer's likely business interests and challenges
3. Position these as executive-level strategic discussions
4. Include the source article title for each

Format as:
**Conversation Starter 1:** [Topic based on Article X]
[Specific conversation starter text]

**Conversation Starter 2:** [Topic based on Article Y] 
[Specific conversation starter text]

**Conversation Starter 3:** [Topic based on Article Z]
[Specific conversation starter text]"""

            #yield "Generating conversation starters...\n"
            
            for chunk in ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': conversation_prompt}
            ], stream=True):
                yield chunk['message']['content']
                
        except Exception as e:
            yield f"❌ Conversation starter generation failed: {str(e)}\n"

    def _stream_battle_card_generation(self, customer_name: str, customer_data: List[Dict],
                                       conversation_starters: List[str],
                                       company_data: Dict) -> Generator[str, None, None]:
        """Stream entry points generation with enhanced company analysis"""
        try:
            # Build comprehensive company context
            company_context = []
            if company_data:
                company_info = company_data.get('company_info', {})
                company_name = company_info.get('name', 'Your Company')
                company_context.append(f"Company: {company_name}")
                if company_info.get('description'):
                    company_context.append(f"Description: {company_info.get('description')}")

                # Add industries with detailed capabilities
                industries = company_data.get('industries', [])
                if industries:
                    company_context.append("\nINDUSTRY EXPERTISE:")
                    for industry in industries:
                        industry_name = industry.get('name', 'Unknown')
                        company_context.append(f"• {industry_name}")

                        specialties = industry.get('specialties', [])
                        if specialties:
                            company_context.append(f"  Specialties: {', '.join(specialties)}")

                        key_products = industry.get('key_products', [])
                        if key_products:
                            company_context.append(f"  Key Products: {', '.join(key_products)}")

                        advantages = industry.get('competitive_advantages', [])
                        if advantages:
                            company_context.append(f"  Competitive Advantages: {', '.join(advantages)}")

                # Add products with full details
                products = company_data.get('products', [])
                if products:
                    company_context.append("\nPRODUCTS:")
                    for product in products:
                        name = product.get('name', 'Unknown')
                        category = product.get('category', '')
                        target_industries = product.get('target_industries', [])
                        features = product.get('key_features', [])
                        advantages = product.get('competitive_advantages', [])

                        company_context.append(f"• {name}")
                        if category:
                            company_context.append(f"  Category: {category}")
                        if target_industries:
                            company_context.append(f"  Target Industries: {', '.join(target_industries)}")
                        if features:
                            company_context.append(f"  Key Features: {', '.join(features)}")
                        if advantages:
                            company_context.append(f"  Competitive Advantages: {', '.join(advantages)}")

                # Add specialties with full details
                specialties = company_data.get('specialties', [])
                if specialties:
                    company_context.append("\nSPECIALTIES:")
                    for specialty in specialties:
                        name = specialty.get('name', 'Unknown')
                        description = specialty.get('description', '')
                        applicable_industries = specialty.get('applicable_industries', [])
                        advantages = specialty.get('competitive_advantages', [])

                        company_context.append(f"• {name}")
                        if description:
                            company_context.append(f"  Description: {description}")
                        if applicable_industries:
                            company_context.append(f"  Applicable Industries: {', '.join(applicable_industries)}")
                        if advantages:
                            company_context.append(f"  Competitive Advantages: {', '.join(advantages)}")

            company_text = '\n'.join(company_context)
            conversation_text = ''.join(conversation_starters)

            # Enhanced battle card prompt with specific requirements
            battle_card_prompt = f"""Create executive-level entry points for our company based on the customer analysis.

    OUR COMPANY DATA:
    {company_text}

    CUSTOMER CONTEXT:
    {customer_name}
    {conversation_text}

    Create entry points with this EXACT format:

    🚀 **Entry Points for {company_info.get('name', 'Your Company')}**

    **Company Analysis:** [ONE sentence summarizing our company's core value proposition and market position]

    **Positioning Strategies:**
    • **Strategy 1:** [TWO sentences describing specific positioning approach based on customer needs and our capabilities]
    • **Strategy 2:** [TWO sentences describing alternative positioning approach that leverages different company strengths]

    **Recommended Products/Specialties:**
    • **[Exact Product/Specialty Name]:** [ONE sentence explaining why this specifically makes sense for this customer's business context]
    • **[Exact Product/Specialty Name]:** [ONE sentence explaining why this specifically makes sense for this customer's business context]  
    • **[Exact Product/Specialty Name]:** [ONE sentence explaining why this specifically makes sense for this customer's business context]

    REQUIREMENTS:
    - Use exact product/specialty names from company data provided above
    - Focus on executive-level strategic value, not technical features
    - Be specific to {customer_name}'s business context and challenges
    - Avoid generic statements - reference specific customer insights and company capabilities
    - Select the 3 most relevant products/specialties based on customer profile analysis
    - Ensure positioning strategies differentiate our company's unique value"""

            #yield "Generating entry points analysis...\n"

            for chunk in ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': battle_card_prompt}
            ], stream=True):
                yield chunk['message']['content']

            yield "\n\n✅ Entry points analysis complete!\n<hr>"

        except Exception as e:
            yield f"❌ Entry points generation failed: {str(e)}\n"

    # =============================================================================
    # SCENARIO 2: CLUSTER ANALYSIS
    # =============================================================================

    def analyze_customer_cluster(self, cluster_name: str, 
                           customers: List[Dict], active_articles: List[Dict] = None) -> Generator[str, None, None]:
        """SCENARIO 2: Multi-customer cluster analysis"""

        # Enhanced cluster header with article context
        active_articles = active_articles or []
        if active_articles:
            article_title = active_articles[0].get('title', 'Unknown Article')
            yield f"🎯 **Article-to-Cluster Analysis**\n\n"
            yield f"📖 **Article**: {article_title}\n"
            yield f"🏢 **Cluster**: {cluster_name} ({len(customers)} customers)\n\n"
            yield f"💡 **Finding cluster-wide connections and opportunities...**\n\n"
        else:
            yield f"🏢 **Cluster Analysis: {cluster_name}** ({len(customers)} customers)\n\n"
        
        # Step 1: Cluster Summary
        yield "📈 **Cluster Profile Summary**\n"
        for chunk in self._stream_cluster_summary(cluster_name, customers):
            yield chunk
            
        # Step 2: Executive Challenges (using current events)
        yield "\n⚠️ **Common Executive Challenges** (Based on Current Events)\n"
        current_events = self.load_current_events_analysis()
        for chunk in self._stream_cluster_challenges(cluster_name, customers, current_events):
            yield chunk
            
        # Step 3: Customer List
        yield f"\n👥 **Companies in {cluster_name}:**\n"
        for customer in customers:
            customer_name = customer.get('customer_name', 'Unknown')
            yield f"• **{customer_name}**\n"
        
        yield "\n*For a deep dive on a specific business, just ask by name*\n"

        # Cache for follow-up questions
        try:
            self.analysis_cache[cluster_name] = {
                'analysis_type': 'cluster_analysis',
                'cluster_name': cluster_name,
                'customers': customers,
                'timestamp': datetime.now().isoformat()
            }
            print(f"✅ Cached cluster analysis for {cluster_name}")
        except Exception as e:
            yield f"\n⚠️ *Note: Analysis completed but caching failed: {str(e)}*\n"

    def _stream_cluster_summary(self, cluster_name: str, 
                              customers: List[Dict]) -> Generator[str, None, None]:
        """Stream cluster profile summary"""
        try:
            # Analyze cluster characteristics
            industries = []
            customer_names = []
            
            for customer in customers:
                customer_names.append(customer.get('customer_name', 'Unknown'))
                # Could extract industry info from reference data if available
                
            cluster_context = f"""Analyze this customer cluster to identify common characteristics:

CLUSTER: {cluster_name}
CUSTOMERS: {', '.join(customer_names)}

Based on the cluster name and customer names, provide analysis of:
1. Likely industries represented
2. Types of accounts (size, maturity, business model)
3. Common business characteristics
4. Shared market segments or focus areas

Keep analysis concise and focus on actionable business intelligence for sales strategy."""

            yield "Analyzing cluster characteristics...\n"
            
            for chunk in ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': cluster_context}
            ], stream=True):
                yield chunk['message']['content']
                
        except Exception as e:
            yield f"❌ Cluster summary failed: {str(e)}\n"

    def _stream_cluster_challenges(self, cluster_name: str, customers: List[Dict], 
                                 current_events: Optional[Dict]) -> Generator[str, None, None]:
        """Stream cluster challenges based on current events"""
        try:
            if not current_events:
                yield "⚠️ Today's trends not available, so I'll use general business trends\n"
                
                # Fallback to general challenges
                general_prompt = f"""Based on current business and technology trends, identify 3 common executive challenges that companies in the '{cluster_name}' cluster would likely face:

CLUSTER: {cluster_name}
COMPANIES: {', '.join([c.get('customer_name', 'Unknown') for c in customers])}

Focus on:
1. Technology transformation challenges
2. Market pressures and competitive dynamics  
3. Operational efficiency and growth challenges

Format as bullet points with specific business impact."""

                for chunk in ollama.chat(model=self.ollama_model, messages=[
                    {'role': 'user', 'content': general_prompt}
                ], stream=True):
                    yield chunk['message']['content']
                return

            # Use current events analysis
            events_data = current_events.get('analysis', {}) if 'analysis' in current_events else current_events
            
            # Extract key trends and challenges
            top_stories = events_data.get('top_stories', [])
            tech_trends = events_data.get('tech_trends', [])
            business_impacts = events_data.get('business_impacts', [])
            
            events_context = []
            if top_stories:
                events_context.append("TOP CURRENT STORIES:")
                for story in top_stories[:5]:
                    if isinstance(story, dict):
                        title = story.get('title', str(story))
                        events_context.append(f"• {title}")
                    else:
                        events_context.append(f"• {story}")
                        
            if tech_trends:
                events_context.append("\nTECH TRENDS:")
                for trend in tech_trends[:5]:
                    events_context.append(f"• {trend}")
                    
            if business_impacts:
                events_context.append("\nBUSINESS IMPACTS:")
                for impact in business_impacts[:5]:
                    events_context.append(f"• {impact}")

            events_text = '\n'.join(events_context)

            challenges_prompt = f"""Based on today's current events and market developments, identify 3 common executive challenges that companies in the '{cluster_name}' cluster are likely facing:

CLUSTER: {cluster_name}
COMPANIES: {', '.join([c.get('customer_name', 'Unknown') for c in customers])}

CURRENT MARKET DEVELOPMENTS:
{events_text}

Identify challenges that:
1. Are driven by current market events and trends
2. Are relevant to this specific cluster of companies
3. Represent executive-level strategic concerns
4. Create business urgency requiring immediate attention

Format as:
• **Challenge 1:** [Title] - [Description with current market context]
• **Challenge 2:** [Title] - [Description with current market context]  
• **Challenge 3:** [Title] - [Description with current market context]"""

            yield "Analyzing current market challenges...\n"
            
            for chunk in ollama.chat(model=self.ollama_model, messages=[
                {'role': 'user', 'content': challenges_prompt}
            ], stream=True):
                yield chunk['message']['content']
                
        except Exception as e:
            yield f"❌ Challenges analysis failed: {str(e)}\n"

    # =============================================================================
    # FOLLOW-UP QUESTIONS SUPPORT
    # =============================================================================

    def answer_follow_up_question(self, question: str, customer_name: Optional[str] = None) -> str:
        """Answer follow-up questions about the analysis"""
        try:
            # If specific customer mentioned, use their cached data
            if customer_name and customer_name in self.analysis_cache:
                cached_data = self.analysis_cache[customer_name]
                context = self._build_follow_up_context(cached_data, question)
            else:
                # Find relevant cached data based on question content
                relevant_cache = None
                for name, cache_data in self.analysis_cache.items():
                    if name.lower() in question.lower():
                        relevant_cache = cache_data
                        break
                
                if relevant_cache:
                    context = self._build_follow_up_context(relevant_cache, question)
                else:
                    return "I don't have any recent customer analysis data to reference. Please run a customer insights analysis first."
            
            # Generate response using chat assistant
            response = self.chat_assistant.generate_response(question, {'context': context})
            return response
            
        except Exception as e:
            return f"Error answering follow-up question: {str(e)}"

    def _build_follow_up_context(self, cached_data: Dict, question: str) -> str:
        """Build context for follow-up questions"""
        analysis_type = cached_data.get('analysis_type', 'unknown')
        
        if analysis_type == 'individual_customer':
            return self._build_individual_follow_up_context(cached_data, question)
        elif analysis_type == 'cluster_analysis':
            return self._build_cluster_follow_up_context(cached_data, question)
        else:
            return "Analysis context not available"

    def _build_individual_follow_up_context(self, cached_data: Dict, question: str) -> str:
        """Build follow-up context for individual customer analysis"""
        context_parts = [
            "CUSTOMER ANALYSIS CONTEXT:",
            "",
            f"Customer: {cached_data.get('customer_name', 'Unknown')}",
            "Analysis Type: Individual Customer",
            "",
            "PROFILE INSIGHTS:",
            cached_data.get('profile_insights', 'Not available'),
            "",
            "CONVERSATION STARTERS:",
            cached_data.get('conversation_starters', 'Not available'),
            "",
            f"Analysis Date: {cached_data.get('timestamp', 'Unknown')}"
        ]
        
        return '\n'.join(context_parts)

    def _build_cluster_follow_up_context(self, cached_data: Dict, question: str) -> str:
        """Build follow-up context for cluster analysis"""
        customers = cached_data.get('customers', [])
        customer_names = [c.get('customer_name', 'Unknown') for c in customers]
        
        context_parts = [
            "CLUSTER ANALYSIS CONTEXT:",
            "",
            f"Cluster: {cached_data.get('cluster_name', 'Unknown')}",
            f"Companies: {', '.join(customer_names)}",
            "Analysis Type: Cluster Analysis",
            "",
            f"Analysis Date: {cached_data.get('timestamp', 'Unknown')}"
        ]
        
        return '\n'.join(context_parts)


class CustomerInsightsHandler:
    """Main handler for customer insights with scenario-based routing"""
    
    def __init__(self, content_curator, chat_assistant, data_directory: str = "data"):
        self.content_curator = content_curator
        self.chat_assistant = chat_assistant
        self.data_manager = CustomerDataManager(data_directory)
        self.content_collector = CustomerContentCollector()
        self.analyzer = CustomerInsightsAnalyzer(content_curator, chat_assistant, self.data_manager)
        self.conversation_context = None
        self.entity_pipeline = EntityResolutionPipeline()

    def set_conversation_context(self, context_data: Optional[Dict]):
        """Set conversation context (for active articles)"""
        self.conversation_context = context_data

    def get_welcome_message(self) -> str:
        """Show welcome message with available options"""
        try:
            clusters = self.data_manager.get_clusters()
            all_customers = self.data_manager.get_all_customers()
            
            if clusters and all_customers:
                example_cluster = clusters[0] if clusters else "Tech Startups"
                example_customer = all_customers[0].get('customer_name', 'TechCorp Inc') if all_customers else "TechCorp Inc"
                
                return f"""🏢 **Customer Insights Ready**

I'll analyze customers against trending topics to find conversation opportunities.

**Choose your approach:**
• **Individual Analysis:** Type a customer name (e.g., `{example_customer}`)
• **Cluster Analysis:** Type a cluster name (e.g., `{example_cluster}`)
• **Browse Database:** Type `show customers`

**Quick examples:**
• `{example_cluster}` - Analyze Account Cluster
• `{example_customer}` - Analyze customer
• `show customers` - View database
• `help` - All commands

Ready to start?"""
            
        except Exception as e:
            return """🏢 **Customer Insights**

I'll find conversation opportunities by analyzing trending topics against your customers.

**Quick examples:**
• `Cluster X` - Analyze all customers in that cluster
• `TechCorp Inc` - Analyze specific customer
• `show customers` - View database
• `help` - All commands

Ready to start?"""

    def show_welcome_message(self) -> str:
        """Show welcome message when entering customer insights mode from general chat"""
        return self.get_welcome_message()

    def show_exit_message(self) -> str:
        """Show message when exiting customer insights"""
        return """✅ **Customer Insights Complete**

Back to general chat. Type "customer insights" anytime to return!"""

    def present_customer_selection(self) -> Generator[str, None, None]:
        """Present customer selection interface with streaming"""
        try:
            clusters = self.data_manager.get_clusters()
            all_customers = self.data_manager.get_all_customers()

            if not clusters and not all_customers:
                yield "❌ **No Customer Data Found**\n\n"
                yield "Add customer information to `data/customers.csv` and try again.\n"
                return

            yield f"📊 **Database Ready**\n"
            yield f"• **{len(clusters)} clusters** with **{len(all_customers)} customers**\n\n"
            yield "Type any cluster name or customer name to start, or `show customers` to browse."

        except Exception as e:
            yield f"❌ **Error accessing customer data**: {str(e)}"

    def show_customers_list(self) -> str:
        """Show organized customer list"""
        try:
            clusters = self.data_manager.get_clusters()
            all_customers = self.data_manager.get_all_customers()
            
            if not clusters and not all_customers:
                return """❌ **No Customer Data Available**
Add customer information to `data/customers.csv`"""
                
            response_parts = [
                "👥 **Available customers grouped by cluster**",
                ""
            ]
            
            if clusters:
                for cluster in clusters:
                    cluster_customers = self.data_manager.get_customers_by_cluster(cluster)
                    response_parts.append(f"🏙 **{cluster}** ({len(cluster_customers)} customers)")
                    
                    for customer in cluster_customers:
                        name = customer.get('customer_name', 'Unknown')
                        response_parts.append(f"• **{name}**")
                    
                    response_parts.append("")
            
            response_parts.extend([
                "**Just type any customer or cluster name above to start analysis.**"
            ])
            
            return "\n".join(response_parts)
            
        except Exception as e:
            return f"❌ **Error showing customers**: {str(e)}"

    def detect_follow_up_question(self, message: str) -> bool:
        """Detect if message is a follow-up question"""
        follow_up_patterns = [
            r'tell me more', r'more details', r'explain', r'elaborate',
            r'what about', r'how about', r'what article', r'which article',
            r'more on', r'details on', r'expand on'
        ]
        
        message_lower = message.lower()
        return any(re.search(pattern, message_lower) for pattern in follow_up_patterns)

    def _get_active_articles_from_context(self) -> List[Dict]:
        """Get active articles from conversation context"""
        if not self.conversation_context:
            return []
        
        active_articles = self.conversation_context.get('active_articles', [])
        return active_articles if active_articles else []

    # =============================================================================
    # MAIN ROUTING LOGIC
    # =============================================================================

    def process_customer_selection(self, customer_input: str) -> Generator[str, None, None]:
        """Main routing method - determines scenario and processes accordingly"""
        try:
            command_result = detect_fuzzy_command(customer_input)

            if command_result['matched']:
                if command_result['command'] == 'show_customers':
                    yield self.show_customers_list()
                    return

            # Handle special commands
            message_lower = customer_input.lower()
            if any(phrase in message_lower for phrase in ['show customers', 'list customers', 'customers', 'database']):
                yield self.show_customers_list()
                return

            if customer_input.lower().strip() == 'help':
                yield self.get_welcome_message()
                return

            # ADD THIS MISSING LINE:
            resolution = self.entity_pipeline.resolve_message(customer_input)

            # Handle disambiguation
            if resolution['needs_disambiguation']:
                yield resolution['disambiguation_prompt']
                return

            # FIXED: Extract best customer match even if needs confirmation
            resolved_customer_name = None
            for customer_resolution in resolution.get('customer_resolutions', []):
                if (customer_resolution.get('result_type') == 'single_match' and
                        customer_resolution.get('best_match') and
                        customer_resolution.get('best_match', {}).get('similarity_score', 0) > 0.7):
                    resolved_customer_name = customer_resolution['best_match']['candidate_text']
                    break

            # Use the resolved customer name if found, otherwise use original input
            if resolved_customer_name:
                selected_customers = self.data_manager.find_customer_matches(resolved_customer_name)
            else:
                resolved_input = resolution['resolved_message']
                selected_customers = self.data_manager.find_customer_matches(resolved_input)

            if not selected_customers:
                yield f"❌ **No matches found for '{customer_input}'**\n\n"
                yield "Please try again with a specific customer name or cluster name.\n"
                yield "Use `show customers` to see available options."
                return

            # Get boosted articles (check conversation context first)
            active_articles = self._get_active_articles_from_context()
            if active_articles:
                boosted_articles = active_articles
                yield f"📖 **Analyzing with active article**: {active_articles[0].get('title', 'Unknown')}\n\n"
            else:
                boosted_articles = self.analyzer.get_boosted_articles_today()
                yield f"📈 **Using trending topics**: {len(boosted_articles)} articles found\n\n"

            if not boosted_articles:
                yield "📊 **No trending articles available** - limited analysis possible\n\n"

            # SCENARIO ROUTING
            if len(selected_customers) == 1:
                # SCENARIO 1: Individual customer analysis
                customer = selected_customers[0]
                customer_name = customer.get('customer_name', 'Unknown')
                
                yield f"🎯 **Scenario: Individual Customer Analysis**\n"
                
                # Collect customer data
                customer_data = self.content_collector.collect_customer_data(customer_name)
                
                # Route to individual customer analysis
                yield from self.analyzer.analyze_individual_customer(
                    customer_name, customer_data, boosted_articles, active_articles)
                    
            else:
                # SCENARIO 2: Cluster analysis
                cluster_name = selected_customers[0].get('cluster', 'Unknown Cluster')
                
                yield f"🏢 **Scenario: Cluster Analysis**\n"
                
                # Route to cluster analysis
                yield from self.analyzer.analyze_customer_cluster(cluster_name, selected_customers, active_articles)

        except Exception as e:
            yield f"❌ **Error processing customer selection**: {str(e)}"

    def process_follow_up_question(self, message: str) -> str:
        """Process follow-up questions about customer analysis"""
        try:
            # Extract customer name if mentioned
            customer_name = None
            for name in self.analyzer.analysis_cache.keys():
                if name.lower() in message.lower():
                    customer_name = name
                    break
            
            response = self.analyzer.answer_follow_up_question(message, customer_name)
            return response
            
        except Exception as e:
            return f"❌ **Error processing follow-up question**: {str(e)}"


    def detect_exit_request(self, message: str) -> bool:
        """Detect if user wants to exit customer insights mode"""
        exit_patterns = [
            r'(?:done|finished|end|exit|stop).*(?:customer|insight)',
            r'(?:customer|insight).*(?:done|finished|end|exit|stop)',
            r'back to (?:general|normal|regular) chat',
            r'no more customer',
            r'that\'?s (?:all|enough).*(?:customer|insight)',
            r'end insights?',
            r'stop (?:customer|insight)',
            r'return to chat',
            r'exit (?:customer|insight)',
            r'done with (?:customer|insight)',
            r'finished with (?:customer|insight)',
            r'(?:customer|insight).*mode.*(?:off|exit|end)',
            r'^done$',
            r'^exit$'
        ]

        message_lower = message.lower().strip()
        return any(re.search(pattern, message_lower) for pattern in exit_patterns)

# Factory function for integration
def create_customer_insights_handler(content_curator, chat_assistant, data_directory: str = "data") -> CustomerInsightsHandler:
    """Factory function to create customer insights handler"""
    return CustomerInsightsHandler(content_curator, chat_assistant, data_directory)