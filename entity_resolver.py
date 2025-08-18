"""
Entity Resolution Pipeline - Simplified Implementation
Drop-in replacement that fixes bugs and removes over-engineering
Maintains all existing function and method names for compatibility

Features:
- Simple customer name extraction and cleaning
- Proper special character handling (& → and)
- Basic similarity matching for duplicates
- No complex entity extraction or disambiguation
- Fast and reliable

Integration: Direct replacement for existing entity_resolver.py
"""

import os
import pandas as pd
import re
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher

# Try to import fuzzy matching libraries (graceful degradation)
try:
    from fuzzywuzzy import fuzz
    HAS_FUZZYWUZZY = True
except ImportError:
    try:
        from rapidfuzz import fuzz
        HAS_FUZZYWUZZY = True
    except ImportError:
        HAS_FUZZYWUZZY = False

# =============================================================================
# DATA CLASSES (maintain compatibility)
# =============================================================================

@dataclass
class EntityMention:
    """Simplified entity mention"""
    text: str
    start_pos: int = 0
    end_pos: int = 0
    entity_type: str = 'customer'
    confidence: float = 1.0
    context: str = ''
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class EntityCandidate:
    """Simplified entity candidate"""
    mention_text: str
    candidate_text: str
    candidate_id: str
    similarity_score: float
    match_type: str = 'exact'
    metadata: Dict = None
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        if self.metadata is None:
            result['metadata'] = {}
        return result

@dataclass
class DisambiguationResult:
    """Simplified disambiguation result"""
    result_type: str  # 'single_match', 'no_match', 'multiple_matches'
    original_mention: str
    best_match: Optional[EntityCandidate] = None
    alternatives: Optional[List[EntityCandidate]] = None
    confidence: float = 0.0
    needs_confirmation: bool = False
    user_prompt: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def normalize_text(text: str) -> str:
    """
    Normalize text for comparison - FIXED VERSION
    Properly handles special characters like &
    """
    if not text:
        return ""
    
    # Convert to lowercase and remove extra whitespace
    normalized = text.lower().strip()
    
    # FIXED: Replace special characters with text equivalents instead of removing
    char_replacements = {
        '&': ' and ',
        '+': ' plus ',
        '@': ' at ',
        '/': ' or ',
        '-': ' ',
        '_': ' ',
        '.': ' '
    }
    
    for char, replacement in char_replacements.items():
        normalized = normalized.replace(char, replacement)
    
    # Remove other punctuation but preserve alphanumeric and spaces
    normalized = re.sub(r'[^\w\s]', '', normalized)
    
    # Replace multiple whitespace with single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized.strip()

def calculate_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two texts - SIMPLIFIED"""
    text1_norm = normalize_text(text1)
    text2_norm = normalize_text(text2)
    
    # Exact match should return 1.0
    if text1_norm == text2_norm:
        return 1.0
    
    # Use fuzzy matching if available, otherwise use built-in
    if HAS_FUZZYWUZZY:
        return fuzz.ratio(text1_norm, text2_norm) / 100.0
    else:
        return SequenceMatcher(None, text1_norm, text2_norm).ratio()

def extract_abbreviation(text: str) -> str:
    """Extract potential abbreviation from text"""
    # Remove common business suffixes
    text = re.sub(r'\b(inc|corp|llc|co|ltd|corporation|incorporated)\b', '', text, flags=re.IGNORECASE)
    
    # Extract first letters of words
    words = text.split()
    if len(words) <= 1:
        return text
    
    abbreviation = ''.join(word[0].upper() for word in words if word)
    return abbreviation

# =============================================================================
# SIMPLIFIED ENTITY EXTRACTOR
# =============================================================================

class EntityExtractor:
    """Simplified entity extractor - just extracts customer name from input"""
    
    def __init__(self):
        pass

    def extract_entities(self, text: str) -> List[EntityMention]:
        """
        Simplified entity extraction - just clean the input text
        No complex pattern matching that causes bugs
        """
        # Remove common prefixes to get the actual entity name
        cleaned_text = self._extract_customer_name(text)
        
        if not cleaned_text or len(cleaned_text) <= 1:
            return []
        
        # Return single entity mention
        return [EntityMention(
            text=cleaned_text,
            start_pos=0,
            end_pos=len(cleaned_text),
            entity_type='customer',
            confidence=1.0,
            context=text
        )]
    
    def _extract_customer_name(self, text: str) -> str:
        """Extract customer name from input like 'customer Apple' -> 'Apple'"""
        text = text.strip()
        
        # Remove common prefixes
        prefixes = [
            'customer ', 'analyze customer ', 'cluster ', 'analyze cluster ',
            'my customers', 'analyze my customers', 'tell me about ',
            'show me ', 'find ', 'about '
        ]
        
        text_lower = text.lower()
        for prefix in prefixes:
            if text_lower.startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        
        # Clean special characters using the same logic as normalize_text
        # but preserve the original case
        text = text.replace('&', ' and ')
        text = text.replace('+', ' plus ')
        text = text.replace('@', ' at ')
        
        # Clean up whitespace
        text = ' '.join(text.split())
        
        return text

    # Compatibility methods (not used but maintain interface)
    def _extract_by_patterns(self, text: str) -> List[EntityMention]:
        return []
    
    def _extract_potential_entities(self, text: str) -> List[EntityMention]:
        return []
    
    def _deduplicate_entities(self, entities: List[EntityMention]) -> List[EntityMention]:
        return entities

# =============================================================================
# SIMPLIFIED CUSTOMER RESOLVER
# =============================================================================

class CustomerEntityResolver:
    """Simplified customer entity resolver"""
    
    def __init__(self, data_directory: str = "data"):
        self.data_directory = data_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        self._customer_cache = None
        
        # Simplified thresholds
        self.high_confidence_threshold = 0.95
        self.similarity_threshold = 0.70
        self.minimum_threshold = 0.60
        self.unique_reasonable_threshold = 0.80
    
    def resolve_customer_entity(self, mention: EntityMention) -> DisambiguationResult:
        """Simplified customer entity resolution"""
        
        # Load customer data
        customers_df = self._load_customer_data()
        if customers_df is None:
            return DisambiguationResult(
                result_type='no_match',
                original_mention=mention.text,
                user_prompt=f"No customer database found. Cannot resolve '{mention.text}'.",
                confidence=0.0
            )
        
        # Find best match using simple logic
        best_match = self._find_best_match(mention.text, customers_df)
        
        if not best_match:
            return DisambiguationResult(
                result_type='no_match',
                original_mention=mention.text,
                user_prompt=f"I couldn't find a customer matching '{mention.text}'. Could you please check the spelling or try a different name?",
                confidence=0.0
            )
        
        # Return single match (no complex disambiguation)
        return DisambiguationResult(
            result_type='single_match',
            original_mention=mention.text,
            best_match=best_match,
            confidence=best_match.similarity_score,
            needs_confirmation=False
        )
    
    def _load_customer_data(self) -> Optional[pd.DataFrame]:
        """Load customer data with caching"""
        if self._customer_cache is not None:
            return self._customer_cache
        
        try:
            if os.path.exists(self.customers_file):
                df = pd.read_csv(self.customers_file)
                df.columns = df.columns.str.strip()
                
                if 'customer_name' not in df.columns:
                    print(f"⚠️ Warning: 'customer_name' column not found in {self.customers_file}")
                    return None
                
                self._customer_cache = df
                return df
        except Exception as e:
            print(f"⚠️ Error loading customer data: {e}")
        
        return None
    
    def _find_best_match(self, query: str, customers_df: pd.DataFrame) -> Optional[EntityCandidate]:
        """Find the best matching customer/cluster"""
        query_norm = normalize_text(query)
        
        best_candidate = None
        best_score = 0.0
        
        # Check customer names
        for _, row in customers_df.iterrows():
            customer_name = str(row['customer_name'])
            customer_norm = normalize_text(customer_name)
            
            # Exact match gets highest score
            if query_norm == customer_norm:
                return EntityCandidate(
                    mention_text=query,
                    candidate_text=customer_name,
                    candidate_id=customer_name,
                    similarity_score=1.0,
                    match_type='exact'
                )
            
            # Calculate similarity
            score = calculate_similarity(query, customer_name)
            if score > best_score and score >= self.minimum_threshold:
                best_score = score
                best_candidate = EntityCandidate(
                    mention_text=query,
                    candidate_text=customer_name,
                    candidate_id=customer_name,
                    similarity_score=score,
                    match_type='fuzzy'
                )
        
        # Check clusters if we have cluster column
        if 'cluster' in customers_df.columns:
            clusters = customers_df['cluster'].dropna().unique()
            for cluster in clusters:
                cluster_str = str(cluster)
                cluster_norm = normalize_text(cluster_str)
                
                # Exact match gets highest score
                if query_norm == cluster_norm:
                    return EntityCandidate(
                        mention_text=query,
                        candidate_text=cluster_str,
                        candidate_id=cluster_str,
                        similarity_score=1.0,
                        match_type='exact'
                    )
                
                # Calculate similarity
                score = calculate_similarity(query, cluster_str)
                if score > best_score and score >= self.minimum_threshold:
                    best_score = score
                    best_candidate = EntityCandidate(
                        mention_text=query,
                        candidate_text=cluster_str,
                        candidate_id=cluster_str,
                        similarity_score=score,
                        match_type='fuzzy'
                    )
        
        return best_candidate
    
    # Compatibility methods (maintain interface)
    def _find_customer_candidates(self, mention: EntityMention, customers_df: pd.DataFrame) -> List[EntityCandidate]:
        best_match = self._find_best_match(mention.text, customers_df)
        return [best_match] if best_match else []
    
    def _find_exact_matches(self, mention_text: str, entities: List[str], entity_type: str) -> List[EntityCandidate]:
        return []
    
    def _find_fuzzy_matches(self, mention_text: str, entities: List[str], entity_type: str) -> List[EntityCandidate]:
        return []
    
    def _find_abbreviation_matches(self, mention_text: str, entities: List[str]) -> List[EntityCandidate]:
        return []
    
    def _find_partial_matches(self, mention_text: str, entities: List[str]) -> List[EntityCandidate]:
        return []
    
    def _filter_candidates_by_base_name(self, candidates: List[EntityCandidate]) -> List[EntityCandidate]:
        return candidates
    
    def _disambiguate_customers(self, mention: EntityMention, candidates: List[EntityCandidate]) -> DisambiguationResult:
        return DisambiguationResult(result_type='no_match', original_mention=mention.text)
    
    def _is_potentially_ambiguous_abbreviation(self, candidate: EntityCandidate) -> bool:
        return False

# =============================================================================
# SIMPLIFIED INTENT RESOLVER
# =============================================================================

class IntentEntityResolver:
    """Simplified intent resolver"""
    
    def __init__(self):
        pass
    
    def resolve_intent_entities(self, text: str, entities: List[EntityMention]) -> Dict[str, Any]:
        """Simplified intent resolution - always return customer_insights"""
        return {
            'intent': 'customer_insights',
            'confidence': 1.0,
            'entities_found': len(entities)
        }
    
    # Compatibility methods
    def _classify_text_entities(self, text: str) -> set:
        return {'customer_related'}
    
    def _find_fuzzy_intent_matches(self, text: str) -> Dict[str, float]:
        return {'customer_related': 1.0}

# =============================================================================
# SIMPLIFIED DISAMBIGUATION MANAGER
# =============================================================================

class DisambiguationManager:
    """Simplified disambiguation manager - no complex disambiguation"""
    
    def __init__(self):
        pass
    
    def create_disambiguation_prompt(self, mention_text: str, candidates: List[EntityCandidate]) -> str:
        """Simple disambiguation prompt"""
        if not candidates:
            return f"I couldn't find a customer matching '{mention_text}'. Could you please check the spelling or try a different name?"
        
        if len(candidates) == 1:
            return f"Found: {candidates[0].candidate_text}"
        
        options = []
        for i, candidate in enumerate(candidates[:5], 1):
            confidence_pct = int(candidate.similarity_score * 100)
            options.append(f"**{i}.** {candidate.candidate_text} ({confidence_pct}% match)")
        
        options_text = "\n".join(options)
        return f"🔍 **Multiple matches found for '{mention_text}'**:\n\n{options_text}\n\n💭 **Which one did you mean?** (Type the number or name)"
    
    # Compatibility methods
    def _create_no_matches_prompt(self, mention_text: str) -> str:
        return f"I couldn't find a customer matching '{mention_text}'. Could you please check the spelling or try a different name?"
    
    def _create_multiple_choice_prompt(self, result: DisambiguationResult) -> str:
        return self.create_disambiguation_prompt(result.original_mention, result.alternatives or [])

# =============================================================================
# MAIN PIPELINE ORCHESTRATOR
# =============================================================================

class EntityResolutionPipeline:
    """
    Simplified main orchestrator - maintains interface but removes complexity
    """
    
    def __init__(self, data_directory: str = "data"):
        self.extractor = EntityExtractor()
        self.customer_resolver = CustomerEntityResolver(data_directory)
        self.intent_resolver = IntentEntityResolver()
        self.disambiguator = DisambiguationManager()
        
        # Simplified configuration
        self.confidence_threshold = 0.7
        self.max_candidates = 5
        
        print(f"✅ Simplified Entity Resolution Pipeline initialized")
        print(f"   Data directory: {data_directory}")
        print(f"   Fuzzy matching: {'Available' if HAS_FUZZYWUZZY else 'Unavailable (using fallback)'}")
    
    def resolve_message(self, message: str) -> Dict[str, Any]:
        """
        Simplified main entry point - much cleaner logic
        
        Returns same format as original for compatibility:
            {
                'entities': List[Dict],
                'customer_resolutions': List[Dict],
                'intent_resolution': Dict,
                'needs_disambiguation': bool,
                'disambiguation_prompt': Optional[str],
                'confidence': float,
                'resolved_message': str,
                'original_message': str
            }
        """
        
        print(f"🔍 SIMPLIFIED DEBUG: Input='{message}' | Starting simplified resolution...")
        
        # Step 1: Extract entities (simplified)
        entities = self.extractor.extract_entities(message)
        print(f"🔍 SIMPLIFIED DEBUG: Extracted {len(entities)} entities: {[e.text for e in entities]}")
        
        # Step 2: Resolve customer entities (simplified)
        customer_resolutions = []
        if entities:
            for entity in entities:
                resolution = self.customer_resolver.resolve_customer_entity(entity)
                customer_resolutions.append(resolution.to_dict())
        
        # Step 3: Intent resolution (simplified)
        intent_resolution = self.intent_resolver.resolve_intent_entities(message, entities)
        
        # Step 4: Check if disambiguation needed (simplified)
        needs_disambiguation = any(
            res.get('result_type') == 'no_match' for res in customer_resolutions
        )
        
        disambiguation_prompt = None
        if needs_disambiguation and customer_resolutions:
            failed_resolution = next(res for res in customer_resolutions if res.get('result_type') == 'no_match')
            disambiguation_prompt = failed_resolution.get('user_prompt')
        
        # Step 5: Calculate confidence (simplified)
        if customer_resolutions:
            confidence = max(res.get('confidence', 0.0) for res in customer_resolutions)
        else:
            confidence = 1.0
        
        # Step 6: Create resolved message (simplified)
        if entities and customer_resolutions:
            # Use the cleaned entity text as resolved message
            resolved_message = entities[0].text
        else:
            resolved_message = message
        
        return {
            'entities': [e.to_dict() for e in entities],
            'customer_resolutions': customer_resolutions,
            'intent_resolution': intent_resolution,
            'needs_disambiguation': needs_disambiguation,
            'disambiguation_prompt': disambiguation_prompt,
            'confidence': confidence,
            'resolved_message': resolved_message,
            'original_message': message
        }
    
    # Compatibility method
    def handle_disambiguation_response(self, original_message: str, response: str, context: Dict) -> Dict[str, Any]:
        """Handle disambiguation response - simplified"""
        return {
            'action': 'resolved',
            'resolved_message': response,
            'message': f"Using: {response}"
        }
    
    def _calculate_overall_confidence(self, entities: List[EntityMention], 
                                    customer_resolutions: List[DisambiguationResult],
                                    intent_resolution: Dict) -> float:
        """Calculate overall confidence"""
        if customer_resolutions:
            return max(res.confidence for res in customer_resolutions)
        return 1.0

# =============================================================================
# CONVENIENCE FUNCTIONS (maintain compatibility)
# =============================================================================

def create_entity_pipeline(data_directory: str = "data") -> EntityResolutionPipeline:
    """Factory function to create entity resolution pipeline"""
    return EntityResolutionPipeline(data_directory)

def quick_resolve_customer(customer_text: str, data_directory: str = "data") -> Dict[str, Any]:
    """Quick function to resolve a single customer name"""
    pipeline = EntityResolutionPipeline(data_directory)
    result = pipeline.resolve_message(f"customer {customer_text}")
    
    if result['customer_resolutions']:
        return result['customer_resolutions'][0]
    
    return {'result_type': 'no_match', 'original_mention': customer_text}

# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    # Test the simplified pipeline
    pipeline = EntityResolutionPipeline("data")
    
    test_messages = [
        "customer SS&C Technologies",  # Should work now
        "customer Apple",
        "customer ind blue cross",     # Should work now
        "squad 1",
        "show customers"
    ]
    
    print("\n🧪 Testing Simplified Entity Resolution Pipeline")
    print("=" * 50)
    
    for message in test_messages:
        print(f"\n🔍 Testing: '{message}'")
        result = pipeline.resolve_message(message)
        
        print(f"Confidence: {result['confidence']:.2f}")
        print(f"Resolved: '{result['resolved_message']}'")
        
        if result['needs_disambiguation']:
            print(f"🤔 Disambiguation needed:")
            print(result['disambiguation_prompt'])
        else:
            print(f"✅ Success")
        
        print("-" * 30)
