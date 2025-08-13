"""
Dynamic Fuzzy Logic Pattern Generator
Generates fuzzy logic patterns directly from customers.csv data
Replaces static patterns with dynamic ones based on actual customer/cluster names
"""

import pandas as pd
import re
import os
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass


@dataclass
class DynamicPattern:
    """Represents a dynamically generated fuzzy pattern"""
    name: str
    exact_patterns: List[str]
    fuzzy_keywords: List[str]
    entity_data: List[str]  # The actual customer/cluster names
    source_column: str  # Which CSV column this came from


class DynamicFuzzyPatternGenerator:
    """
    Generates fuzzy logic patterns dynamically from CSV data
    """
    
    def __init__(self, customers_csv_path: str = "customers.csv"):
        self.customers_csv_path = customers_csv_path
        self.customers_df = None
        self.last_modification_time = None
        self._pattern_cache = None
        
    def _load_csv_data(self) -> bool:
        """Load CSV data and check for modifications"""
        try:
            if not os.path.exists(self.customers_csv_path):
                print(f"⚠️ CSV file not found: {self.customers_csv_path}")
                return False
                
            # Check modification time
            current_mtime = os.path.getmtime(self.customers_csv_path)
            
            # Only reload if file changed or not loaded yet
            if (self.last_modification_time is None or 
                current_mtime > self.last_modification_time):
                
                print(f"🔄 Loading customer data from {self.customers_csv_path}")
                self.customers_df = pd.read_csv(self.customers_csv_path)
                self.customers_df.columns = self.customers_df.columns.str.strip()
                self.last_modification_time = current_mtime
                self._pattern_cache = None  # Clear cache
                
                print(f"✅ Loaded {len(self.customers_df)} rows")
                print(f"📋 Columns: {list(self.customers_df.columns)}")
                return True
                
            return True  # Already loaded and up to date
            
        except Exception as e:
            print(f"❌ Error loading CSV: {e}")
            return False
    
    def _extract_keywords_from_text(self, text: str) -> List[str]:
        """Extract meaningful keywords from customer/cluster names"""
        if pd.isna(text) or not text:
            return []
            
        # Clean and normalize
        text = str(text).strip()
        
        # Split on common separators
        words = re.split(r'[\s\-_\.&,]+', text.lower())
        
        # Filter out common words and very short words
        stop_words = {'inc', 'corp', 'llc', 'ltd', 'co', 'company', 'group', 
                      'the', 'and', 'or', 'of', 'for', 'in', 'on', 'at', 'to'}
        
        keywords = []
        for word in words:
            word = re.sub(r'[^\w]', '', word)  # Remove special chars
            if len(word) >= 2 and word not in stop_words:
                keywords.append(word)
                
        return keywords
    
    def _generate_exact_patterns_for_entity(self, entity_name: str) -> List[str]:
        """Generate exact match patterns for an entity"""
        if pd.isna(entity_name) or not entity_name:
            return []
            
        entity_clean = str(entity_name).strip()
        patterns = []
        
        # Add the full name
        patterns.append(entity_clean.lower())
        
        # Add variations with common prefixes
        patterns.extend([
            f"show {entity_clean.lower()}",
            f"analyze {entity_clean.lower()}",
            f"tell me about {entity_clean.lower()}",
            f"{entity_clean.lower()} analysis",
            f"{entity_clean.lower()} customers",
            f"{entity_clean.lower()} cluster"
        ])
        
        return patterns
    
    def generate_customer_patterns(self) -> Optional[DynamicPattern]:
        """Generate patterns for individual customers"""
        if not self._load_csv_data() or self.customers_df is None:
            return None
            
        if 'customer_name' not in self.customers_df.columns:
            print("⚠️ 'customer_name' column not found in CSV")
            return None
            
        # Get all customer names
        customer_names = self.customers_df['customer_name'].dropna().astype(str).tolist()
        
        # Generate exact patterns
        exact_patterns = []
        fuzzy_keywords = set(['customer', 'customers', 'show', 'analyze', 'tell'])
        
        for customer_name in customer_names:
            exact_patterns.extend(self._generate_exact_patterns_for_entity(customer_name))
            
            # Extract keywords from customer name
            keywords = self._extract_keywords_from_text(customer_name)
            fuzzy_keywords.update(keywords)
        
        return DynamicPattern(
            name='dynamic_customer_lookup',
            exact_patterns=exact_patterns,
            fuzzy_keywords=list(fuzzy_keywords),
            entity_data=customer_names,
            source_column='customer_name'
        )
    
    def generate_cluster_patterns(self) -> Optional[DynamicPattern]:
        """Generate patterns for customer clusters"""
        if not self._load_csv_data() or self.customers_df is None:
            return None
            
        if 'cluster' not in self.customers_df.columns:
            print("⚠️ 'cluster' column not found in CSV")
            return None
            
        # Get all unique cluster names
        cluster_names = self.customers_df['cluster'].dropna().unique().tolist()
        cluster_names = [str(name) for name in cluster_names]
        
        # Generate exact patterns
        exact_patterns = []
        fuzzy_keywords = set(['cluster', 'clusters', 'group', 'segment', 'show', 'analyze'])
        
        for cluster_name in cluster_names:
            exact_patterns.extend(self._generate_exact_patterns_for_entity(cluster_name))
            
            # Extract keywords from cluster name
            keywords = self._extract_keywords_from_text(cluster_name)
            fuzzy_keywords.update(keywords)
        
        return DynamicPattern(
            name='dynamic_cluster_lookup',
            exact_patterns=exact_patterns,
            fuzzy_keywords=list(fuzzy_keywords),
            entity_data=cluster_names,
            source_column='cluster'
        )
    
    def generate_all_patterns(self) -> List[DynamicPattern]:
        """Generate all dynamic patterns from CSV data"""
        patterns = []
        
        # Generate customer patterns
        customer_pattern = self.generate_customer_patterns()
        if customer_pattern:
            patterns.append(customer_pattern)
            
        # Generate cluster patterns  
        cluster_pattern = self.generate_cluster_patterns()
        if cluster_pattern:
            patterns.append(cluster_pattern)
            
        return patterns
    
    def get_cached_patterns(self) -> List[DynamicPattern]:
        """Get patterns with caching for performance"""
        if self._pattern_cache is None:
            self._pattern_cache = self.generate_all_patterns()
            
        return self._pattern_cache or []
    
    def get_pattern_summary(self) -> Dict:
        """Get a summary of generated patterns for debugging"""
        patterns = self.get_cached_patterns()
        
        summary = {
            'total_patterns': len(patterns),
            'patterns': []
        }
        
        for pattern in patterns:
            pattern_info = {
                'name': pattern.name,
                'source_column': pattern.source_column,
                'entity_count': len(pattern.entity_data),
                'exact_patterns_count': len(pattern.exact_patterns),
                'fuzzy_keywords_count': len(pattern.fuzzy_keywords),
                'sample_entities': pattern.entity_data[:5],  # First 5 for preview
                'sample_exact_patterns': pattern.exact_patterns[:5],
                'sample_fuzzy_keywords': pattern.fuzzy_keywords[:10]
            }
            summary['patterns'].append(pattern_info)
            
        return summary

def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]

def _load_dynamic_patterns(customers_csv_path: str = "customers.csv") -> Dict:
    """Load dynamic patterns from CSV file"""
    try:
        if not os.path.exists(customers_csv_path):
            return {'customers': [], 'clusters': []}

        df = pd.read_csv(customers_csv_path)
        df.columns = df.columns.str.strip()

        # Extract customer names
        customers = []
        if 'customer_name' in df.columns:
            customers = df['customer_name'].dropna().astype(str).tolist()

        # Extract cluster names
        clusters = []
        if 'cluster' in df.columns:
            clusters = df['cluster'].dropna().unique().tolist()
            clusters = [str(name) for name in clusters]

        return {'customers': customers, 'clusters': clusters}

    except Exception as e:
        print(f"⚠️ Error loading dynamic patterns: {e}")
        return {'customers': [], 'clusters': []}

def _is_fuzzy_match(word: str, target: str, max_distance: int = 2) -> bool:
    """Check if word fuzzy matches target within max_distance edits"""
    if abs(len(word) - len(target)) > max_distance:
        return False
    return _levenshtein_distance(word, target) <= max_distance


def detect_fuzzy_command(input_text: str) -> Dict:
    """
    Detect commands with typo tolerance + dynamic patterns from CSV

    Args:
        input_text: User input to analyze

    Returns:
        Dict with keys: matched (bool), command (str), method (str), details (dict)
    """
    message_lower = input_text.lower().strip()
    words = message_lower.split()

    # Load dynamic patterns from CSV
    dynamic_data = _load_dynamic_patterns()

    # Static command patterns (your existing ones)
    command_patterns = [
        {
            'name': 'show_customers',
            'exact_patterns': ['show customers', 'list customers'],
            'fuzzy_keywords': ['show', 'list']
        },
        {
            'name': 'customer_insights_trigger',
            'exact_patterns': ['customer', 'my customers', 'analyze my customers', 'do customer analysis',
                               'relate to my customers'],
            'fuzzy_keywords': ['my', 'customers', 'analyze', 'customer', 'analysis', 'relate']
        }
    ]

    # Add dynamic patterns for customers
    if dynamic_data['customers']:
        customer_keywords = ['customer', 'company', 'client', 'show', 'analyze']
        # Extract keywords from customer names
        for customer in dynamic_data['customers']:
            words_in_name = re.split(r'[\s\-_\.&,/()]+', customer.lower())
            for word in words_in_name:
                word = re.sub(r'[^\w]', '', word)
                if len(word) >= 2 and word not in ['inc', 'corp', 'llc', 'ltd', 'co']:
                    customer_keywords.append(word)

        command_patterns.append({
            'name': 'dynamic_customer_lookup',
            'exact_patterns': [customer.lower() for customer in dynamic_data['customers']],
            'fuzzy_keywords': list(set(customer_keywords)),
            'entity_data': dynamic_data['customers']
        })

    # Add dynamic patterns for clusters
    if dynamic_data['clusters']:
        cluster_keywords = ['cluster', 'group', 'segment', 'show', 'analyze']
        # Extract keywords from cluster names
        for cluster in dynamic_data['clusters']:
            words_in_name = re.split(r'[\s\-_\.&,/()]+', cluster.lower())
            for word in words_in_name:
                word = re.sub(r'[^\w]', '', word)
                if len(word) >= 2:
                    cluster_keywords.append(word)

        command_patterns.append({
            'name': 'dynamic_cluster_lookup',
            'exact_patterns': [cluster.lower() for cluster in dynamic_data['clusters']],
            'fuzzy_keywords': list(set(cluster_keywords)),
            'entity_data': dynamic_data['clusters']
        })

    # First try exact matching (preserve existing behavior)
    for pattern in command_patterns:
        for exact_pattern in pattern['exact_patterns']:
            if exact_pattern == message_lower.strip():
                return {
                    'matched': True,
                    'command': pattern['name'],
                    'method': 'exact',
                    'details': {
                        'pattern': exact_pattern,
                        'entity_data': pattern.get('entity_data', [])
                    }
                }

    # If no exact match, try fuzzy matching (your existing logic)
    for pattern in command_patterns:
        matched_words = []

        for keyword in pattern['fuzzy_keywords']:
            for word in words:
                if _is_fuzzy_match(word, keyword, max_distance=2):
                    matched_words.append((word, keyword))
                    break  # Only count each keyword once

        # Command-specific logic (preserve your existing logic)
        if pattern['name'] == 'show_customers':
            # Check if we have a "customers" match (most important)
            has_customers_match = any(match[1] == 'customers' for match in matched_words)

            # Check if we have command words (show/list)
            has_command_words = any(match[1] in ['show', 'list'] for match in matched_words)

            # Accept if we have customers match OR multiple relevant matches
            if has_customers_match or (has_command_words and len(matched_words) >= 2):
                return {
                    'matched': True,
                    'command': pattern['name'],
                    'method': 'fuzzy',
                    'details': {
                        'matches': [f"{word}→{keyword}" for word, keyword in matched_words],
                        'confidence': len(matched_words)
                    }
                }

        elif pattern['name'] == 'customer_insights_trigger':
            # Must have "customer" or "customers" match
            has_customer_match = any(match[1] in ['customer', 'customers'] for match in matched_words)

            # Check for other key terms
            has_my = any(match[1] == 'my' for match in matched_words)
            has_analyze = any(match[1] in ['analyze', 'analysis'] for match in matched_words)
            has_relate = any(match[1] == 'relate' for match in matched_words)

            if has_customer_match and (has_my or has_analyze or has_relate):
                return {
                    'matched': True,
                    'command': 'customer_insights_trigger',
                    'method': 'fuzzy',
                    'details': {
                        'matches': [f"{word}→{keyword}" for word, keyword in matched_words],
                        'confidence': len(matched_words)
                    }
                }

        elif pattern['name'].startswith('dynamic_'):
            # Dynamic pattern logic - accept if we have good matches
            if len(matched_words) >= 2:
                return {
                    'matched': True,
                    'command': pattern['name'],
                    'method': 'fuzzy',
                    'details': {
                        'matches': [f"{word}→{keyword}" for word, keyword in matched_words],
                        'confidence': len(matched_words),
                        'entity_data': pattern.get('entity_data', []),
                        'input_text': input_text
                    }
                }

    return {
        'matched': False,
        'command': None,
        'method': None,
        'details': {}
    }


# Example usage and testing
if __name__ == "__main__":
    # Create generator
    generator = DynamicFuzzyPatternGenerator("customers.csv")
    
    # Test pattern generation
    print("=== Dynamic Fuzzy Pattern Generator Test ===")
    
    # Generate patterns
    patterns = generator.generate_all_patterns()
    print(f"\n📊 Generated {len(patterns)} dynamic patterns")
    
    # Show summary
    summary = generator.get_pattern_summary()
    print(f"\n📋 Pattern Summary:")
    
    for pattern_info in summary['patterns']:
        print(f"\n🔍 {pattern_info['name']}:")
        print(f"   Source: {pattern_info['source_column']}")
        print(f"   Entities: {pattern_info['entity_count']}")
        print(f"   Exact patterns: {pattern_info['exact_patterns_count']}")
        print(f"   Fuzzy keywords: {pattern_info['fuzzy_keywords_count']}")
        print(f"   Sample entities: {pattern_info['sample_entities']}")
        print(f"   Sample patterns: {pattern_info['sample_exact_patterns'][:3]}")
