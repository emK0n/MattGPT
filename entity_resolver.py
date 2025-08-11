"""
Entity Resolution Pipeline - Final Implementation
Intelligent entity detection, fuzzy matching, typo correction, and disambiguation
Designed for offline operation with conservative abbreviation handling

Features:
- Fuzzy string matching with configurable thresholds
- Offline-safe abbreviation detection
- Uniqueness-based auto-resolution
- Smart confidence-based prompting
- Typo correction and partial matching
- Conservative handling of ambiguous short inputs

Integration: Insert after pattern detection, before final intent handling
"""

import os
import pandas as pd
import re
import json
from typing import List, Dict, Optional, Tuple, Any, Union
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
import unicodedata

# Try to import fuzzy matching libraries (graceful degradation)
try:
    from fuzzywuzzy import fuzz, process
    HAS_FUZZYWUZZY = True
except ImportError:
    try:
        from rapidfuzz import fuzz, process  # ← Fix: import fuzz from rapidfuzz
        HAS_FUZZYWUZZY = True
    except ImportError:
        HAS_FUZZYWUZZY = False

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class EntityMention:
    """Raw entity mention extracted from user input"""
    text: str
    start_pos: int
    end_pos: int
    entity_type: str  # 'customer', 'cluster', 'topic', 'article'
    confidence: float
    context: str  # surrounding text
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class EntityCandidate:
    """Potential match for an entity mention"""
    mention_text: str
    candidate_text: str
    candidate_id: str
    similarity_score: float
    match_type: str  # 'exact', 'fuzzy', 'semantic', 'phonetic', 'abbreviation', 'initials', 'partial'
    metadata: Dict = None
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        if self.metadata is None:
            result['metadata'] = {}
        return result

@dataclass
class DisambiguationResult:
    """Result of entity disambiguation process"""
    result_type: str  # 'single_match', 'multiple_matches', 'no_match'
    original_mention: str
    best_match: Optional[EntityCandidate] = None
    alternatives: List[EntityCandidate] = None
    user_prompt: Optional[str] = None
    confidence: float = 0.0
    needs_confirmation: bool = False
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        if self.best_match:
            result['best_match'] = self.best_match.to_dict()
        if self.alternatives:
            result['alternatives'] = [alt.to_dict() for alt in self.alternatives]
        return result

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower().strip()

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)

    # Remove common punctuation
    text = re.sub(r'[.,;:!?()"\'-]', '', text)

    # Normalize unicode
    text = unicodedata.normalize('NFKD', text)

    return text


def calculate_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two texts using available algorithms - FIXED for exact matching"""
    text1_norm = normalize_text(text1)
    text2_norm = normalize_text(text2)

    # Exact match should return 1.0
    if text1_norm == text2_norm:
        return 1.0

    if _are_different_semantic_domains(text1_norm, text2_norm):
        return 0.0

    length_ratio = min(len(text1_norm), len(text2_norm)) / max(len(text1_norm), len(text2_norm))
    if length_ratio < 0.5:  # Very different lengths
        length_penalty = 0.5
    else:
        length_penalty = 1.0

    if HAS_FUZZYWUZZY:
        # Use fuzzy matching if available
        return fuzz.ratio(text1_norm, text2_norm) / 100.0
    else:
        # Fallback to built-in SequenceMatcher
        return SequenceMatcher(None, text1_norm, text2_norm).ratio()

def _are_different_semantic_domains(text1: str, text2: str) -> bool:
    """Check if two texts are from completely different semantic domains"""
    # Define domain keywords
    tech_keywords = {'tesla', 'apple', 'microsoft', 'google', 'amazon', 'meta', 'nvidia'}
    retail_keywords = {'staples', 'walmart', 'target', 'costco', 'kroger', 'macys'}
    finance_keywords = {'bank', 'goldman', 'jpmorgan', 'wells', 'citi', 'chase'}

    # Check if one is tech and other is retail (classic mismatch)
    text1_is_tech = any(keyword in text1 for keyword in tech_keywords)
    text1_is_retail = any(keyword in text1 for keyword in retail_keywords)
    text2_is_tech = any(keyword in text2 for keyword in tech_keywords)
    text2_is_retail = any(keyword in text2 for keyword in retail_keywords)

    # Return True if they're in different domains
    if (text1_is_tech and text2_is_retail) or (text1_is_retail and text2_is_tech):
        return True

    return False

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
# CORE ENTITY EXTRACTION
# =============================================================================

class EntityExtractor:
    """Extracts potential entity mentions from raw text"""
    
    def __init__(self):
        self.entity_patterns = {
            'customer_indicators': [
                r'(?:customer|client|account|company|corp|inc|llc)\s+([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)',
                # FIXED: More restrictive pattern to avoid matching partial words
                r'([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)\s+(?:customer|client|account|company)(?:\s|$)',
                # SAFE: Keep legitimate customer query patterns, remove problematic "relate to"
                r'(?:tell me about|show me|find)\s+([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)',
                r'(?:about)\s+([A-Z][^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)',
                r'(?:customer insights?|analyze customer|customer analysis)\s+(?:on|for|about)?\s*([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)',
                r'(?:show|list)\s+customers?(?:\s+in\s+([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*))?',
                # REMOVED: r'(?:tell me about|show me|find|about|relate to)\s+([^\s.,;?!]+(?:\s+[^\s.,;?!&]+)*&?[^\s.,;?!]*)',
            ],
            'cluster_indicators': [
                r'([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)\s+(?:cluster|analysis|customers)',
                r'(?:show|analyze)\s+([^\s.,;?!]+(?:\s+[^\s.,;?!]+)*)',
                r'\b(squad\s+\d+)\b',  # Matches "squad 10", "squad 1", etc.
                r'\b(team\s+\d+)\b',  # Matches "team 10", "team 1", etc.
                r'\b(group\s+\d+)\b',  # Matches "group 10", "group 1", etc.
            ],
            'business_patterns': [
                r'(\d+-\d+-[a-zA-Z]+[a-zA-Z\s]*)',  # "1-800-flowers"
                r'([a-zA-Z]*\d+[a-zA-Z]+[a-zA-Z\s]*)',  # "800flowers"
                r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Inc|Corp|LLC|Co|Ltd)\.?)',  # "Apple Inc"
                # REMOVED: The problematic all-caps pattern that was matching everything
            ],
            'general_entities': [
                # FIXED: More restrictive proper noun matching
                r'(?<!\w)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)(?!\w)',  # Proper nouns with word boundaries
                r'\b([a-zA-Z]+\.[a-zA-Z]+)\b',  # Domain-like patterns
            ]
        }
        
        # Stop words to filter out
        self.stop_words = {
            'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
            'me', 'my', 'i', 'you', 'your', 'this', 'that', 'these', 'those', 'is', 'are',
            'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'may', 'might', 'must', 'shall', 'can',
            'show', 'tell', 'find', 'get', 'see', 'look', 'about', 'what', 'how', 'when',
            'where', 'why', 'who', 'which', 'relate'
        }

    def extract_entities(self, text: str) -> List[EntityMention]:
        """Extract all potential entity mentions from text"""
        entities = []

        # Pattern-based extraction
        pattern_entities = self._extract_by_patterns(text)
        print(f"🔍 EXTRACTION DEBUG: Pattern entities: {[e.text for e in pattern_entities]}")
        entities.extend(pattern_entities)

        # Word-based extraction (look for potential entity words)
        word_entities = self._extract_potential_entities(text)
        print(f"🔍 EXTRACTION DEBUG: Word entities: {[e.text for e in word_entities]}")
        entities.extend(word_entities)

        # Deduplication
        deduplicated = self._deduplicate_entities(entities)
        print(f"🔍 EXTRACTION DEBUG: Final entities: {[e.text for e in deduplicated]}")

        return deduplicated

    def _extract_by_patterns(self, text: str) -> List[EntityMention]:
        """Extract entities using regex patterns - FIXED VERSION"""
        entities = []

        for pattern_type, patterns in self.entity_patterns.items():
            for pattern in patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    entity_text = match.group(1) if match.groups() else match.group(0)
                    entity_text = entity_text.strip()

                    # FIX: Handle captured text that contains multiple words
                    if ' ' in entity_text:
                        # Split into words and filter out stop words
                        words = entity_text.split()
                        filtered_words = [word for word in words if word.lower() not in self.stop_words]

                        # If we have filtered words, use them
                        if filtered_words:
                            entity_text = ' '.join(filtered_words)
                        # If all words were stop words, take the last word (likely the entity)
                        else:
                            entity_text = words[-1] if words else entity_text

                    # Additional filter: skip if the entity text itself is a stop word
                    if (len(entity_text) > 1 and
                            entity_text.lower() not in self.stop_words and
                            not entity_text.isdigit()):
                        # Determine entity type based on pattern
                        if 'customer' in pattern_type or 'business' in pattern_type:
                            entity_type = 'customer'
                        elif 'cluster' in pattern_type:
                            entity_type = 'cluster'
                        else:
                            entity_type = 'general'

                        # Extract context around the match
                        context_start = max(0, match.start() - 20)
                        context_end = min(len(text), match.end() + 20)
                        context = text[context_start:context_end].strip()

                        entities.append(EntityMention(
                            text=entity_text,
                            start_pos=match.start(),
                            end_pos=match.end(),
                            entity_type=entity_type,
                            confidence=0.8 if 'customer' in pattern_type else 0.6,
                            context=context
                        ))

        return entities

    def _extract_potential_entities(self, text: str) -> List[EntityMention]:
        """Extract potential entities by analyzing words"""
        entities = []
        words = text.split()

        for i, word in enumerate(words):
            cleaned_word = re.sub(r'[^\w\s-]', '', word).strip()

            if (len(cleaned_word) > 2 and
                    cleaned_word.lower() not in self.stop_words and
                    not cleaned_word.isdigit()):

                # Look for potential company/customer indicators
                confidence = 0.3
                entity_type = 'general'

                # Boost confidence for certain patterns
                if any(indicator in text.lower() for indicator in ['customer', 'company', 'client']):
                    confidence = 0.7
                    entity_type = 'customer'
                elif any(phrase in text.lower() for phrase in ['relate to', 'relates to', 'connection']):
                    confidence = 0.1  # Very low confidence for relation phrases
                    entity_type = 'general'

                # Get surrounding context
                start_idx = max(0, i - 3)
                end_idx = min(len(words), i + 4)
                context = ' '.join(words[start_idx:end_idx])

                entities.append(EntityMention(
                    text=cleaned_word,
                    start_pos=0,  # Approximate
                    end_pos=len(cleaned_word),
                    entity_type=entity_type,
                    confidence=confidence,
                    context=context
                ))

        return entities

    def _deduplicate_entities(self, entities: List[EntityMention]) -> List[EntityMention]:
        """Remove duplicate entities, keeping the highest confidence - FIXED to prefer multi-word"""
        if not entities:
            return []

        # First, remove entities that are substrings of other entities
        filtered_entities = []

        for entity in entities:
            # Check if this entity's text is contained in any other entity's text
            is_substring = False
            for other_entity in entities:
                if (entity != other_entity and
                        entity.text.lower().strip() in other_entity.text.lower().strip() and
                        len(entity.text.strip()) < len(other_entity.text.strip())):
                    is_substring = True
                    break

            if not is_substring:
                filtered_entities.append(entity)

        # Then group by normalized text for final deduplication
        entity_groups = {}
        for entity in filtered_entities:
            key = normalize_text(entity.text)
            if key not in entity_groups:
                entity_groups[key] = []
            entity_groups[key].append(entity)

        # Keep the highest confidence entity from each group
        deduplicated = []
        for group in entity_groups.values():
            # Prefer complete cluster names, then multi-word entities, then by confidence
            best_entity = max(group, key=lambda e: (
                1 if e.entity_type == 'cluster' else 0,  # Prioritize cluster entities
                len(e.text.split()),  # Then multi-word
                e.confidence  # Then confidence
            ))
            deduplicated.append(best_entity)

        return sorted(deduplicated, key=lambda e: e.confidence, reverse=True)

# =============================================================================
# CUSTOMER-SPECIFIC RESOLVER
# =============================================================================

class CustomerEntityResolver:
    """Specialized resolver for customer and cluster entities with offline-safe logic"""
    
    def __init__(self, data_directory: str = "data"):
        self.data_directory = data_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        self._customer_cache = None
        self._cluster_cache = None
        
        # Final agreed-upon thresholds
        self.high_confidence_threshold = 0.95    # Auto-resolve without prompting (lowered)
        self.similarity_threshold = 0.80         # Prompt for confirmation (lowered)
        self.minimum_threshold = 0.60            # Minimum to consider as candidate
        self.unique_reasonable_threshold = 0.70  # Minimum for unique auto-resolve
    
    def resolve_customer_entity(self, mention: EntityMention) -> DisambiguationResult:
        """Resolve a customer entity mention to actual customers"""
        
        # Load customer data
        customers_df = self._load_customer_data()
        if customers_df is None:
            return DisambiguationResult(
                result_type='no_match',
                original_mention=mention.text,
                user_prompt=f"No customer database found. Cannot resolve '{mention.text}'.",
                confidence=0.0
            )
        
        # Find candidate matches using multiple algorithms
        candidates = self._find_customer_candidates(mention, customers_df)
        
        # Apply offline-safe disambiguation logic
        return self._disambiguate_customers(mention, candidates)
    
    def _load_customer_data(self) -> Optional[pd.DataFrame]:
        """Load customer data with caching"""
        if self._customer_cache is not None:
            return self._customer_cache
        
        try:
            if os.path.exists(self.customers_file):
                df = pd.read_csv(self.customers_file)
                df.columns = df.columns.str.strip()
                
                # Ensure required columns exist
                if 'customer_name' not in df.columns:
                    print(f"⚠️ Warning: 'customer_name' column not found in {self.customers_file}")
                    return None
                
                self._customer_cache = df
                return df
        except Exception as e:
            print(f"⚠️ Error loading customer data: {e}")
        
        return None
    
    def _find_customer_candidates(self, mention: EntityMention, 
                                customers_df: pd.DataFrame) -> List[EntityCandidate]:
        """Find all potential customer matches using multiple algorithms"""
        candidates = []
        mention_text = mention.text.strip()
        
        # Get customer names and clusters
        customer_names = customers_df['customer_name'].dropna().astype(str).tolist()
        clusters = customers_df['cluster'].dropna().astype(str).unique().tolist() if 'cluster' in customers_df.columns else []
        
        # 1. Exact matching
        candidates.extend(self._find_exact_matches(mention_text, customer_names, 'customer'))
        if clusters:
            candidates.extend(self._find_exact_matches(mention_text, clusters, 'cluster'))
        
        # 2. Fuzzy string matching
        candidates.extend(self._find_fuzzy_matches(mention_text, customer_names, 'customer'))
        if clusters:
            candidates.extend(self._find_fuzzy_matches(mention_text, clusters, 'cluster'))
        
        # 3. Abbreviation/initials matching (offline-safe)
        candidates.extend(self._find_abbreviation_matches(mention_text, customer_names))
        
        # 4. Partial matching
        candidates.extend(self._find_partial_matches(mention_text, customer_names))
        
        # Sort by confidence and remove duplicates with uniqueness logic
        return self._rank_and_filter_candidates(candidates)
    
    def _find_exact_matches(self, mention_text: str, target_list: List[str], 
                          match_type: str) -> List[EntityCandidate]:
        """Find exact matches"""
        candidates = []
        mention_norm = normalize_text(mention_text)
        
        for target in target_list:
            target_norm = normalize_text(target)
            if mention_norm == target_norm:
                candidates.append(EntityCandidate(
                    mention_text=mention_text,
                    candidate_text=target,
                    candidate_id=target,
                    similarity_score=1.0,
                    match_type='exact',
                    metadata={'entity_type': match_type}
                ))
        
        return candidates
    
    def _find_fuzzy_matches(self, mention_text: str, target_list: List[str], 
                          match_type: str) -> List[EntityCandidate]:
        """Find fuzzy matches using string similarity"""
        candidates = []
        
        for target in target_list:
            similarity = calculate_similarity(mention_text, target)
            
            if similarity >= self.similarity_threshold:
                candidates.append(EntityCandidate(
                    mention_text=mention_text,
                    candidate_text=target,
                    candidate_id=target,
                    similarity_score=similarity,
                    match_type='fuzzy',
                    metadata={'entity_type': match_type}
                ))
        
        return candidates
    
    def _find_abbreviation_matches(self, mention_text: str, customer_names: List[str]) -> List[EntityCandidate]:
        """Find matches based on abbreviations - OFFLINE-SAFE with local knowledge only"""
        candidates = []
        mention_norm = normalize_text(mention_text)

        # Only work with what we can extract from the customer names themselves
        for customer_name in customer_names:
            # Extract potential abbreviation from customer name
            customer_abbrev = extract_abbreviation(customer_name)
            customer_norm = normalize_text(customer_name)
            
            # Check if mention could be abbreviation of customer name
            if (len(mention_norm) >= 2 and len(customer_abbrev) >= 2):
                
                # Case 1: User input matches extracted abbreviation
                if mention_norm == customer_abbrev.lower():
                    # Conservative confidence for abbreviations without external knowledge
                    confidence = 0.75 if len(customer_abbrev) >= 3 else 0.55
                    
                    candidates.append(EntityCandidate(
                        mention_text=mention_text,
                        candidate_text=customer_name,
                        candidate_id=customer_name,
                        similarity_score=confidence,
                        match_type='abbreviation',
                        metadata={'entity_type': 'customer', 'abbreviation': customer_abbrev}
                    ))
                
                # Case 2: Check if customer name contains the mention as initials
                elif self._could_be_initials(mention_norm, customer_norm):
                    confidence = 0.60  # Medium confidence for initials without external validation
                    
                    candidates.append(EntityCandidate(
                        mention_text=mention_text,
                        candidate_text=customer_name,
                        candidate_id=customer_name,
                        similarity_score=confidence,
                        match_type='initials',
                        metadata={'entity_type': 'customer', 'initials_pattern': True}
                    ))
        
        return candidates

    def _could_be_initials(self, mention: str, customer_name: str) -> bool:
        """Check if mention could be initials of customer name (offline pattern matching only)"""
        if len(mention) < 2:
            return False
        
        # Split customer name into words
        words = [word for word in customer_name.split() if len(word) > 0]
        
        # Skip common business words when checking initials
        business_words = {'inc', 'corp', 'llc', 'ltd', 'company', 'co', 'group', 'the', 'and', '&'}
        meaningful_words = [word for word in words if word.lower() not in business_words]
        
        if len(meaningful_words) < len(mention):
            return False
        
        # Check if mention letters match first letters of meaningful words
        for i, char in enumerate(mention):
            if i >= len(meaningful_words):
                return False
            if char.lower() != meaningful_words[i][0].lower():
                return False
        
        return True
    
    def _find_partial_matches(self, mention_text: str, customer_names: List[str]) -> List[EntityCandidate]:
        """Find partial matches (substring matching)"""
        candidates = []
        mention_norm = normalize_text(mention_text)
        
        for customer_name in customer_names:
            customer_norm = normalize_text(customer_name)
            
            # Check if mention is contained in customer name or vice versa
            if (len(mention_norm) >= 3 and 
                (mention_norm in customer_norm or customer_norm in mention_norm)):
                
                # Calculate similarity based on length ratio
                overlap_ratio = min(len(mention_norm), len(customer_norm)) / max(len(mention_norm), len(customer_norm))
                similarity = overlap_ratio * 0.7  # Reduce confidence for partial matches
                
                if similarity >= self.similarity_threshold:
                    candidates.append(EntityCandidate(
                        mention_text=mention_text,
                        candidate_text=customer_name,
                        candidate_id=customer_name,
                        similarity_score=similarity,
                        match_type='partial',
                        metadata={'entity_type': 'customer'}
                    ))
        
        return candidates
    
    def _rank_and_filter_candidates(self, candidates: List[EntityCandidate]) -> List[EntityCandidate]:
        """Rank candidates by confidence, remove duplicates, and apply uniqueness logic"""
        if not candidates:
            return []
        
        # Remove duplicates (same candidate_text)
        seen_candidates = {}
        for candidate in candidates:
            key = candidate.candidate_text.lower()
            if key not in seen_candidates or candidate.similarity_score > seen_candidates[key].similarity_score:
                seen_candidates[key] = candidate
        
        # Sort by similarity score (highest first)
        ranked_candidates = sorted(seen_candidates.values(), 
                                 key=lambda c: c.similarity_score, reverse=True)
        
        # Apply uniqueness detection: check if multiple candidates share the same "base name"
        unique_candidates = self._apply_uniqueness_filtering(ranked_candidates)
        
        # Return top 5 candidates
        return unique_candidates[:5]
    
    def _apply_uniqueness_filtering(self, candidates: List[EntityCandidate]) -> List[EntityCandidate]:
        """Filter candidates based on uniqueness of base names"""
        if len(candidates) <= 1:
            return candidates
        
        # Group candidates by base name (remove common suffixes)
        base_name_groups = {}
        
        for candidate in candidates:
            base_name = self._extract_base_name(candidate.candidate_text)
            
            if base_name not in base_name_groups:
                base_name_groups[base_name] = []
            base_name_groups[base_name].append(candidate)
        
        # If only one base name group exists, return just the best candidate from that group
        if len(base_name_groups) == 1:
            base_name, group_candidates = next(iter(base_name_groups.items()))
            # Return only the highest scoring candidate from this group
            return [max(group_candidates, key=lambda c: c.similarity_score)]
        
        # Multiple base name groups exist, return top candidate from each group
        filtered_candidates = []
        for base_name, group_candidates in base_name_groups.items():
            best_from_group = max(group_candidates, key=lambda c: c.similarity_score)
            filtered_candidates.append(best_from_group)
        
        # Sort final list by similarity score
        return sorted(filtered_candidates, key=lambda c: c.similarity_score, reverse=True)
    
    def _extract_base_name(self, company_name: str) -> str:
        """Extract base name from company name by removing common suffixes"""
        base_name = company_name.lower().strip()
        
        # Remove common business suffixes
        suffixes = [
            'inc', 'incorporated', 'corp', 'corporation', 'llc', 'ltd', 'limited',
            'co', 'company', 'group', 'holdings', 'enterprises', 'solutions',
            'technologies', 'tech', 'systems', 'services'
        ]
        
        for suffix in suffixes:
            # Remove suffix if it appears at the end (with or without punctuation)
            patterns = [
                f' {suffix}$',
                f' {suffix}\\.$',
                f', {suffix}$',
                f', {suffix}\\.$'
            ]
            
            for pattern in patterns:
                base_name = re.sub(pattern, '', base_name)
        
        return base_name.strip()
    
    def _disambiguate_customers(self, mention: EntityMention, 
                              candidates: List[EntityCandidate]) -> DisambiguationResult:
        """Apply offline-safe disambiguation logic with uniqueness override"""

        if not candidates:
            return DisambiguationResult(
                result_type='no_match',
                original_mention=mention.text,
                user_prompt=f"I couldn't find a customer matching '{mention.text}'. Could you please check the spelling or try a different name?",
                confidence=0.0
            )
        
        best_candidate = candidates[0]

        print(f"🔍 DEBUG: Disambiguating '{mention.text}'")
        print(f"   Best candidate: '{best_candidate.candidate_text}'")
        print(f"   Similarity score: {best_candidate.similarity_score}")
        print(f"   Match type: {best_candidate.match_type}")
        is_abbrev = self._is_potentially_ambiguous_abbreviation(best_candidate)
        print(f"   Is abbreviation: {is_abbrev}")

        # OFFLINE-SAFE UNIQUENESS CHECK: Only auto-resolve if:
        # 1. Only one candidate AND
        # 2. Confidence is reasonable (≥50%) AND  
        # 3. It's not a potentially ambiguous abbreviation
        if (len(candidates) == 1 and 
            best_candidate.similarity_score >= self.unique_reasonable_threshold and
            not self._is_potentially_ambiguous_abbreviation(best_candidate)):
            
            return DisambiguationResult(
                result_type='single_match',
                original_mention=mention.text,
                best_match=best_candidate,
                confidence=best_candidate.similarity_score,
                needs_confirmation=False,  # Auto-resolve unique matches
                user_prompt=None
            )
        
        # HIGH CONFIDENCE: Auto-resolve without prompting (≥85%)
        elif best_candidate.similarity_score >= self.high_confidence_threshold:
            return DisambiguationResult(
                result_type='single_match',
                original_mention=mention.text,
                best_match=best_candidate,
                confidence=best_candidate.similarity_score,
                needs_confirmation=False,
                user_prompt=None
            )
        
        # MEDIUM CONFIDENCE OR POTENTIALLY AMBIGUOUS: Prompt for confirmation
        elif best_candidate.similarity_score >= self.similarity_threshold:
            return DisambiguationResult(
                result_type='single_match',
                original_mention=mention.text,
                best_match=best_candidate,
                confidence=best_candidate.similarity_score,
                needs_confirmation=True,
                user_prompt=f"Did you mean '{best_candidate.candidate_text}'?"
            )
        
        # MULTIPLE LOW CONFIDENCE MATCHES: Show options
        else:
            return DisambiguationResult(
                result_type='multiple_matches',
                original_mention=mention.text,
                best_match=best_candidate,
                alternatives=candidates[:3],  # Top 3 alternatives
                confidence=best_candidate.similarity_score,
                needs_confirmation=True,
                user_prompt=self._create_multiple_choice_prompt(mention.text, candidates[:3])
            )

    def _is_potentially_ambiguous_abbreviation(self, candidate: EntityCandidate) -> bool:
        """Check if this could be an ambiguous abbreviation that needs confirmation (offline-safe)"""

        # If it's an abbreviation or initials match with medium confidence, be cautious
        if (candidate.match_type in ['abbreviation', 'initials'] and
                candidate.similarity_score < 0.80):
            return True

        # FIXED: Don't flag multi-word phrases as potentially ambiguous
        mention_text = candidate.mention_text.strip()
        mention_length = len(mention_text)
        word_count = len(mention_text.split())

        # If the original mention is very short (≤3 chars) AND single word AND not an exact match
        if mention_length <= 3 and word_count == 1 and candidate.similarity_score < 1.0:
            return True

        return False
    
    def _create_multiple_choice_prompt(self, mention_text: str, 
                                     candidates: List[EntityCandidate]) -> str:
        """Create a user-friendly multiple choice prompt"""
        options = []
        for i, candidate in enumerate(candidates, 1):
            confidence_pct = int(candidate.similarity_score * 100)
            options.append(f"{i}. {candidate.candidate_text} ({confidence_pct}% match)")
        
        options_text = "\n".join(options)
        
        return f"I found multiple customers that might match '{mention_text}':\n\n{options_text}\n\nWhich one did you mean, or is it something else?"

# =============================================================================
# INTENT ENTITY RESOLVER  
# =============================================================================

class IntentEntityResolver:
    """Resolves intent entities using semantic understanding"""
    
    def __init__(self):
        self.intent_entities = {
            'customer_related': ['customer', 'client', 'account', 'buyer', 'customers', 'companies', 'clients'],
            'analysis_related': ['insights', 'analysis', 'opportunities', 'relevance', 'analytics', 'analyze', 'examine'],
            'search_related': ['find', 'search', 'show', 'list', 'display', 'browse', 'get', 'fetch'],
            'article_related': ['article', 'content', 'document', 'story', 'articles', 'documents'],
            'database_related': ['database', 'db', 'collection', 'repository', 'storage']
        }
        
        self.intent_combinations = {
            frozenset(['customer_related', 'analysis_related']): 'customer_insights',
            frozenset(['customer_related', 'search_related']): 'customer_search',
            frozenset(['article_related', 'search_related']): 'database_search',
            frozenset(['article_related', 'database_related']): 'database_search',
            frozenset(['customer_related']): 'customer_insights',
            frozenset(['search_related']): 'general_search',
            frozenset(['analysis_related']): 'analysis_request'
        }
    
    def resolve_intent_entities(self, text: str, entities: List[EntityMention]) -> Dict[str, Any]:
        """Resolve intent from text and extracted entities"""
        
        # Classify entity types found in text
        entity_types = self._classify_text_entities(text)

        fuzzy_matches = self._find_fuzzy_intent_matches(text)
        for entity_type, confidence in fuzzy_matches.items():
            entity_types.add(entity_type)
        
        # Boost confidence if entities match the text classification
        for entity in entities:
            if entity.entity_type == 'customer':
                entity_types.add('customer_related')
        
        # Determine intent from entity combination
        intent = self._determine_intent(entity_types)
        confidence = self._calculate_intent_confidence(text, entity_types, intent)
        
        return {
            'intent': intent,
            'confidence': confidence,
            'supporting_entities': [e.to_dict() for e in entities],
            'entity_types': list(entity_types)
        }
    
    def _classify_text_entities(self, text: str) -> set:
        """Classify what types of entities are referenced in text"""
        text_lower = text.lower()
        found_types = set()
        
        for entity_type, keywords in self.intent_entities.items():
            for keyword in keywords:
                if keyword in text_lower:
                    found_types.add(entity_type)
                    break
        
        return found_types
    
    def _determine_intent(self, entity_types: set) -> str:
        """Determine intent from entity types"""
        entity_types_frozen = frozenset(entity_types)
        
        # Check for exact matches first
        for combination, intent in self.intent_combinations.items():
            if combination == entity_types_frozen:
                return intent
        
        # Check for subset matches
        for combination, intent in self.intent_combinations.items():
            if combination.issubset(entity_types_frozen):
                return intent
        
        # Default intent
        return 'general_conversation'
    
    def _calculate_intent_confidence(self, text: str, entity_types: set, intent: str) -> float:
        """Calculate confidence in intent classification"""
        base_confidence = 0.7
        
        # Boost confidence based on number of matching entity types
        if len(entity_types) >= 2:
            base_confidence += 0.1
        
        # Boost for specific patterns
        text_lower = text.lower()
        if intent == 'customer_insights':
            if any(phrase in text_lower for phrase in ['customer insight', 'relate to customer', 'customer analysis']):
                base_confidence += 0.2
        elif intent == 'database_search':
            if any(phrase in text_lower for phrase in ['show me', 'find article', 'search database']):
                base_confidence += 0.2
        
        return min(base_confidence, 1.0)

    def _find_fuzzy_intent_matches(self, text: str) -> Dict[str, float]:
        """Find fuzzy matches for intent keywords"""
        fuzzy_matches = {}
        text_words = text.lower().split()

        for entity_type, keywords in self.intent_entities.items():
            for keyword in keywords:
                for word in text_words:
                    # Skip very short words
                    if len(word) < 3:
                        continue

                    # Calculate similarity
                    similarity = calculate_similarity(word, keyword)

                    # If similarity is high enough, record it
                    if similarity >= 0.7:  # Threshold for fuzzy intent matching
                        if entity_type not in fuzzy_matches or similarity > fuzzy_matches[entity_type]:
                            fuzzy_matches[entity_type] = similarity

        return fuzzy_matches

# =============================================================================
# DISAMBIGUATION MANAGER
# =============================================================================

class DisambiguationManager:
    """Manages user disambiguation when entities are ambiguous"""
    
    def __init__(self):
        self.disambiguation_templates = {
            'customer_disambiguation': "I found multiple customers that might match '{mention}'. Did you mean:\n{options}\n\nOr something else?",
            'no_customer_match': "I couldn't find a customer named '{mention}'. Here are some similar customers:\n{suggestions}\n\nDid you mean one of these?",
            'typo_correction': "Did you mean '{suggestion}' instead of '{mention}'?",
            'cluster_clarification': "I found customers in the '{cluster}' cluster. Would you like to see:\n1. All customers in this cluster\n2. Analysis for the entire cluster\n3. Something else?"
        }

    def create_disambiguation_prompt(self, result: DisambiguationResult) -> str:
        """Create user-friendly disambiguation prompt with article context awareness"""

        # Check if we're in article discussion context (passed via result metadata if available)
        context_prefix = ""
        if hasattr(result, 'article_context') and result.article_context:
            article_title = result.article_context.get('title', 'Current Article')
            context_prefix = f"📖 **Analyzing**: {article_title}\n🎯 **Looking for customer/cluster**:\n\n"

        if result.result_type == 'multiple_matches':
            base_prompt = result.user_prompt or self._create_multiple_choice_prompt(result)
            return context_prefix + base_prompt
        elif result.result_type == 'no_match':
            base_prompt = result.user_prompt or f"❌ I couldn't find a customer matching '{result.original_mention}'.\n\n💡 **Options**:\n• Check spelling\n• Try a different customer/cluster name\n• Type 'show customers' to browse database"
            return context_prefix + base_prompt
        elif result.result_type == 'single_match' and result.needs_confirmation:
            base_prompt = result.user_prompt or f"🤔 Did you mean **'{result.best_match.candidate_text}'**?\n\nType 'yes' to confirm or provide a different name."
            return context_prefix + base_prompt
        else:
            return context_prefix + "I'm not sure what you're referring to. Could you be more specific?"

    def _create_multiple_choice_prompt(self, result: DisambiguationResult) -> str:
        """Create multiple choice prompt with better formatting"""
        options = []
        for i, candidate in enumerate(result.alternatives or [], 1):
            confidence_pct = int(candidate.similarity_score * 100)
            options.append(f"**{i}.** {candidate.candidate_text} ({confidence_pct}% match)")

        options_text = "\n".join(options)
        return f"🔍 **Multiple matches found for '{result.original_mention}'**:\n\n{options_text}\n\n💭 **Which one did you mean?** (Type the number or name)"

# =============================================================================
# MAIN PIPELINE ORCHESTRATOR
# =============================================================================

class EntityResolutionPipeline:
    """
    Main orchestrator that coordinates all entity resolution components
    Designed for offline operation with configurable thresholds
    """
    
    def __init__(self, data_directory: str = "data"):
        self.extractor = EntityExtractor()
        self.customer_resolver = CustomerEntityResolver(data_directory)
        self.intent_resolver = IntentEntityResolver()
        self.disambiguator = DisambiguationManager()
        
        # Pipeline configuration - final agreed values
        self.confidence_threshold = 0.7
        self.max_candidates = 5
        
        print(f"✅ Entity Resolution Pipeline initialized")
        print(f"   Data directory: {data_directory}")
        print(f"   Fuzzy matching: {'Available' if HAS_FUZZYWUZZY else 'Unavailable (using fallback)'}")
        print(f"   High confidence threshold: {self.customer_resolver.high_confidence_threshold}")
        print(f"   Similarity threshold: {self.customer_resolver.similarity_threshold}")
    
    def resolve_message(self, message: str) -> Dict[str, Any]:
        """
        Main entry point - resolve all entities in a user message
        
        Returns:
            {
                'entities': List[Dict],  # EntityMention dicts
                'customer_resolutions': List[Dict],  # DisambiguationResult dicts
                'intent_resolution': Dict,
                'needs_disambiguation': bool,
                'disambiguation_prompt': Optional[str],
                'confidence': float,
                'resolved_message': str,  # Message with resolved entities
                'original_message': str
            }
        """

        # Step 1: Extract potential entities
        entities = self.extractor.extract_entities(message)
        
        # Step 2: Resolve customer entities
        customer_resolutions = []
        for entity in entities:
            if entity.entity_type in ['customer', 'cluster', 'general'] and len(entity.text) > 2:
                resolution = self.customer_resolver.resolve_customer_entity(entity)
                customer_resolutions.append(resolution)
        
        # Step 3: Resolve intent entities  
        intent_resolution = self.intent_resolver.resolve_intent_entities(message, entities)
        
        # Step 4: Check if disambiguation needed
        needs_disambiguation = any(
            res.needs_confirmation or res.result_type in ['multiple_matches', 'no_match']
            for res in customer_resolutions
        )
        
        disambiguation_prompt = None
        if needs_disambiguation:
            # Get the first result that needs disambiguation
            ambiguous_result = next(
                res for res in customer_resolutions 
                if res.needs_confirmation or res.result_type in ['multiple_matches', 'no_match']
            )
            disambiguation_prompt = self.disambiguator.create_disambiguation_prompt(ambiguous_result)
        
        # Step 5: Create resolved message
        resolved_message = self._create_resolved_message(message, customer_resolutions)
        
        # Step 6: Calculate overall confidence
        confidence = self._calculate_overall_confidence(entities, customer_resolutions, intent_resolution)
        
        return {
            'entities': [e.to_dict() for e in entities],
            'customer_resolutions': [r.to_dict() for r in customer_resolutions],
            'intent_resolution': intent_resolution,
            'needs_disambiguation': needs_disambiguation,
            'disambiguation_prompt': disambiguation_prompt,
            'confidence': confidence,
            'resolved_message': resolved_message,
            'original_message': message
        }
    
    def handle_disambiguation_response(self, original_message: str, 
                                     user_choice: str, 
                                     disambiguation_context: Dict) -> Dict[str, Any]:
        """Handle user's response to disambiguation prompt"""
        
        # Parse user choice (could be number, name, or "none"/"other")
        choice_lower = user_choice.lower().strip()
        
        # Check for cancellation
        if choice_lower in ['none', 'cancel', 'other', 'something else', 'no']:
            return {
                'action': 'cancelled',
                'message': 'Okay, let me know if you need help with something else.',
                'resolved_message': original_message
            }

        # Handle "yes" confirmation properly
        if choice_lower in ['yes', 'y', 'confirm', 'ok', 'yep', 'yeah']:
            customer_resolutions = disambiguation_context.get('customer_resolutions', [])
            if customer_resolutions:
                first_resolution = customer_resolutions[0]
                if (first_resolution.get('result_type') == 'single_match' and
                        first_resolution.get('needs_confirmation') and
                        first_resolution.get('best_match')):
                    best_match = first_resolution['best_match']
                    resolved_message = original_message.replace(
                        first_resolution['original_mention'],
                        best_match['candidate_text']
                    )
                    return {
                        'action': 'resolved',
                        'chosen_entity': best_match,
                        'resolved_message': resolved_message,
                        'message': f"Great! I'll use '{best_match['candidate_text']}'."
                    }

        # Try to parse as number choice
        try:
            choice_num = int(choice_lower)
            alternatives = disambiguation_context.get('alternatives', [])
            if 1 <= choice_num <= len(alternatives):
                chosen_candidate = alternatives[choice_num - 1]
                resolved_message = original_message.replace(
                    disambiguation_context['original_mention'],
                    chosen_candidate['candidate_text']
                )
                return {
                    'action': 'resolved',
                    'chosen_entity': chosen_candidate,
                    'resolved_message': resolved_message,
                    'message': f"Great! I'll use '{chosen_candidate['candidate_text']}'."
                }
        except ValueError:
            pass
        
        # Try to match choice against alternatives
        alternatives = disambiguation_context.get('alternatives', [])
        for candidate in alternatives:
            if calculate_similarity(user_choice, candidate['candidate_text']) > 0.8:
                resolved_message = original_message.replace(
                    disambiguation_context['original_mention'],
                    candidate['candidate_text']
                )
                return {
                    'action': 'resolved',
                    'chosen_entity': candidate,
                    'resolved_message': resolved_message,
                    'message': f"Got it! I'll use '{candidate['candidate_text']}'."
                }
        
        # Could not parse choice
        return {
            'action': 'unclear',
            'message': "I didn't understand your choice. Could you please select a number from the list or say 'none' if none of them match?",
            'resolved_message': original_message
        }
    
    def _create_resolved_message(self, message: str, 
                               customer_resolutions: List[DisambiguationResult]) -> str:
        """Create a resolved version of the message with confirmed entities"""
        resolved_message = message
        
        for resolution in customer_resolutions:
            if (resolution.result_type == 'single_match' and 
                resolution.best_match and 
                not resolution.needs_confirmation):
                
                # Replace the original mention with the resolved entity
                resolved_message = resolved_message.replace(
                    resolution.original_mention,
                    resolution.best_match.candidate_text
                )
        
        return resolved_message
    
    def _calculate_overall_confidence(self, entities: List[EntityMention], 
                                    customer_resolutions: List[DisambiguationResult],
                                    intent_resolution: Dict) -> float:
        """Calculate overall confidence in the resolution"""
        if not entities:
            return 1.0  # No entities to resolve
        
        confidences = []
        
        # Add customer resolution confidences
        for resolution in customer_resolutions:
            confidences.append(resolution.confidence)
        
        # Add intent resolution confidence
        confidences.append(intent_resolution.get('confidence', 0.5))
        
        # Return average confidence
        return sum(confidences) / len(confidences) if confidences else 0.5

# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_entity_pipeline(data_directory: str = "data") -> EntityResolutionPipeline:
    """Factory function to create entity resolution pipeline"""
    return EntityResolutionPipeline(data_directory)

def quick_resolve_customer(customer_text: str, data_directory: str = "data") -> Dict[str, Any]:
    """Quick function to resolve a single customer name"""
    pipeline = EntityResolutionPipeline(data_directory)
    result = pipeline.resolve_message(f"find customer {customer_text}")
    
    if result['customer_resolutions']:
        return result['customer_resolutions'][0]
    
    return {'result_type': 'no_match', 'original_mention': customer_text}

# =============================================================================
# TESTING AND VALIDATION
# =============================================================================

if __name__ == "__main__":
    # Test the pipeline with various scenarios
    pipeline = EntityResolutionPipeline("data")
    
    test_messages = [
        "show me customeres",  # Typo - should auto-resolve
        "tell me about apple",  # Unique match - should auto-resolve  
        "find 800 flowers info",  # Partial match - may need disambiguation
        "show me IBM data",  # Abbreviation - should prompt (offline-safe)
        "squad analysis",  # Could be multiple squads - should prompt
        "what articles do we have about AI?"  # Intent resolution
    ]
    
    print("\n🧪 Testing Entity Resolution Pipeline")
    print("=" * 50)
    
    for message in test_messages:
        print(f"\n🔍 Testing: '{message}'")
        result = pipeline.resolve_message(message)
        
        print(f"Intent: {result['intent_resolution']['intent']}")
        print(f"Confidence: {result['confidence']:.2f}")
        
        if result['needs_disambiguation']:
            print(f"🤔 Disambiguation needed:")
            print(result['disambiguation_prompt'])
        else:
            print(f"✅ Resolved: '{result['resolved_message']}'")
        
        print("-" * 30)
