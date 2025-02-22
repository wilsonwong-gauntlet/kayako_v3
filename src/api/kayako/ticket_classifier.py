"""Ticket classification logic for Kayako tickets."""

import re
from typing import Dict, Tuple, List, Optional
import logging

logger = logging.getLogger(__name__)
# Set logger to DEBUG level for more detailed output
logger.setLevel(logging.DEBUG)

class TicketClassifier:
    """Classifies tickets based on conversation context."""
    
    # Priority levels
    PRIORITY_LOW = 1
    PRIORITY_NORMAL = 2
    PRIORITY_HIGH = 3
    PRIORITY_URGENT = 4
    
    # Case types
    TYPE_QUESTION = 1
    TYPE_TASK = 2
    TYPE_PROBLEM = 3
    TYPE_INCIDENT = 4
    TYPE_TEST = 5
    TYPE_TECHNICAL = 6
    TYPE_SERVICE_REQUEST = 7
    
    # Priority names for logging
    PRIORITY_NAMES = {
        1: "LOW",
        2: "NORMAL",
        3: "HIGH",
        4: "URGENT"
    }
    
    # Type names for logging
    TYPE_NAMES = {
        1: "QUESTION",
        2: "TASK",
        3: "PROBLEM",
        4: "INCIDENT",
        5: "TEST",
        6: "TECHNICAL",
        7: "SERVICE_REQUEST"
    }
    
    # Priority classification patterns
    PRIORITY_PATTERNS = {
        PRIORITY_URGENT: [
            r"urgent", r"emergency", r"critical", r"asap", r"immediately",
            r"system.+down", r"cannot.+(work|access|use)", r"broken",
            r"production.+issue", r"security"
        ],
        PRIORITY_HIGH: [
            r"important", r"serious", r"significant", r"affecting.+work",
            r"high.+priority", r"blocking", r"stuck", r"major"
        ],
        PRIORITY_NORMAL: [
            r"normal", r"regular", r"standard", r"when.+possible",
            r"would.+like", r"please.+help", r"need.+assistance"
        ],
        PRIORITY_LOW: [
            r"minor", r"low.+priority", r"suggestion", r"feedback",
            r"question", r"curious", r"wondering"
        ]
    }
    
    # Type classification patterns
    TYPE_PATTERNS = {
        TYPE_QUESTION: [
            r"how.+(?:do|can|should|would|could)", r"what.+is",
            r"explain", r"clarify", r"help.+understand",
            r"guide", r"documentation"
        ],
        TYPE_TASK: [
            r"create", r"setup", r"configure", r"update",
            r"change", r"modify", r"add", r"remove",
            r"please.+(?:do|make|set)"
        ],
        TYPE_PROBLEM: [
            r"not.+working", r"error", r"issue", r"bug",
            r"problem", r"failed", r"incorrect", r"wrong"
        ],
        TYPE_INCIDENT: [
            r"down", r"outage", r"unavailable", r"disruption",
            r"crash", r"emergency", r"incident", r"impact"
        ],
        TYPE_TECHNICAL: [
            r"api", r"integration", r"code", r"technical",
            r"developer", r"sdk", r"endpoint", r"authentication"
        ],
        TYPE_SERVICE_REQUEST: [
            r"request", r"new.+account", r"upgrade",
            r"provision", r"access", r"permission", r"enable"
        ]
    }
    
    def __init__(self):
        """Initialize the classifier with compiled regex patterns."""
        # Compile regex patterns for efficiency
        self.priority_patterns = {
            priority: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for priority, patterns in self.PRIORITY_PATTERNS.items()
        }
        
        self.type_patterns = {
            type_id: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for type_id, patterns in self.TYPE_PATTERNS.items()
        }
    
    def _count_matches(self, text: str, patterns: List[re.Pattern]) -> Dict[str, List[str]]:
        """Count how many patterns match in the text and return matched patterns."""
        matches = []
        for pattern in patterns:
            found = pattern.findall(text)
            if found:
                matches.append(pattern.pattern)
        return matches
    
    def _calculate_confidence(self, match_count: int, total_patterns: int) -> float:
        """Calculate confidence score based on number of matches."""
        if match_count == 0:
            return 0.0
        # Give more weight to having any match at all
        base_confidence = 0.5  # Start at 50% confidence for a single match
        # Add up to 50% more based on ratio of matches
        ratio_boost = (match_count / total_patterns) * 0.5
        return min(base_confidence + ratio_boost, 1.0)
    
    def classify_priority(self, text: str) -> Tuple[int, float, List[str]]:
        """
        Determine ticket priority based on conversation text.
        
        Args:
            text: The conversation text to analyze
            
        Returns:
            Tuple of (priority_id, confidence_score, matched_patterns)
        """
        best_priority = None
        best_matches = []
        best_confidence = 0.0
        total_patterns = max(len(patterns) for patterns in self.priority_patterns.values())
        
        logger.debug("\n=== Priority Classification Analysis ===")
        logger.debug(f"Analyzing text: {text[:100]}...")
        
        # First pass: Check for explicit priority matches
        for priority, patterns in self.priority_patterns.items():
            matches = self._count_matches(text, patterns)
            confidence = self._calculate_confidence(len(matches), total_patterns)
            
            logger.debug(f"\nChecking {self.PRIORITY_NAMES[priority]} priority patterns:")
            if matches:
                logger.debug(f"✓ Matched patterns: {matches}")
                logger.debug(f"Confidence score: {confidence:.2f}")
                
                # Update if we have better confidence
                if confidence > best_confidence:
                    best_matches = matches
                    best_priority = priority
                    best_confidence = confidence
            else:
                logger.debug("✗ No matches found")
        
        # Second pass: If no strong matches, analyze content sentiment
        if not best_priority or best_confidence < 0.5:
            # Look for urgency indicators
            urgency_indicators = [
                (r"(need|required).+immediately", self.PRIORITY_URGENT),
                (r"(blocking|preventing).+(work|progress)", self.PRIORITY_HIGH),
                (r"(would|could).+(help|appreciate)", self.PRIORITY_NORMAL),
                (r"(when|if).+possible", self.PRIORITY_LOW)
            ]
            
            for pattern, priority in urgency_indicators:
                if re.search(pattern, text, re.IGNORECASE):
                    if not best_priority:  # Only use if we don't have a direct match
                        best_priority = priority
                        best_confidence = 0.4  # Lower confidence for inferred priority
                        best_matches = [pattern]
                    break
        
        # If still no priority determined, use content length and complexity
        if not best_priority:
            # Short, simple queries likely lower priority
            words = text.split()
            if len(words) < 10:
                best_priority = self.PRIORITY_LOW
            elif len(words) < 20:
                best_priority = self.PRIORITY_NORMAL
            else:
                best_priority = self.PRIORITY_HIGH
            best_confidence = 0.3  # Even lower confidence for length-based priority
            best_matches = ["content_analysis"]
        
        logger.info(f"\nFinal Priority Classification:")
        logger.info(f"Selected Priority: {self.PRIORITY_NAMES[best_priority]}")
        logger.info(f"Confidence Score: {best_confidence:.2f}")
        logger.info(f"Matched Patterns: {best_matches}")
        
        return best_priority, best_confidence, best_matches
    
    def classify_type(self, text: str) -> Tuple[int, float, List[str]]:
        """
        Determine ticket type based on conversation text.
        
        Args:
            text: The conversation text to analyze
            
        Returns:
            Tuple of (type_id, confidence_score, matched_patterns)
        """
        best_type = None
        best_matches = []
        best_confidence = 0.0
        total_patterns = max(len(patterns) for patterns in self.type_patterns.values())
        
        logger.debug("\n=== Type Classification Analysis ===")
        logger.debug(f"Analyzing text: {text[:100]}...")
        
        # First pass: Check for explicit type matches
        for type_id, patterns in self.type_patterns.items():
            matches = self._count_matches(text, patterns)
            confidence = self._calculate_confidence(len(matches), total_patterns)
            
            logger.debug(f"\nChecking {self.TYPE_NAMES[type_id]} type patterns:")
            if matches:
                logger.debug(f"✓ Matched patterns: {matches}")
                logger.debug(f"Confidence score: {confidence:.2f}")
                
                # Update if we have better confidence
                if confidence > best_confidence:
                    best_matches = matches
                    best_type = type_id
                    best_confidence = confidence
            else:
                logger.debug("✗ No matches found")
        
        # Second pass: If no strong matches, analyze content structure
        if not best_type or best_confidence < 0.5:
            # Look for structural indicators
            if "?" in text:
                best_type = self.TYPE_QUESTION
                best_confidence = 0.4
                best_matches = ["question_mark"]
            elif any(word in text.lower() for word in ["broken", "error", "issue", "bug"]):
                best_type = self.TYPE_PROBLEM
                best_confidence = 0.4
                best_matches = ["problem_indicators"]
            elif any(word in text.lower() for word in ["create", "add", "update", "change"]):
                best_type = self.TYPE_TASK
                best_confidence = 0.4
                best_matches = ["task_indicators"]
            elif any(word in text.lower() for word in ["access", "permission", "account"]):
                best_type = self.TYPE_SERVICE_REQUEST
                best_confidence = 0.4
                best_matches = ["service_indicators"]
            else:
                # Analyze sentence structure
                sentences = [s.strip() for s in text.split(".") if s.strip()]
                if any(s.lower().startswith(("how", "what", "why", "when", "where", "who")) for s in sentences):
                    best_type = self.TYPE_QUESTION
                    best_confidence = 0.35
                    best_matches = ["question_structure"]
                elif any(s.lower().startswith(("please", "could you", "would you")) for s in sentences):
                    best_type = self.TYPE_TASK
                    best_confidence = 0.35
                    best_matches = ["request_structure"]
        
        logger.info(f"\nFinal Type Classification:")
        logger.info(f"Selected Type: {self.TYPE_NAMES[best_type]}")
        logger.info(f"Confidence Score: {best_confidence:.2f}")
        logger.info(f"Matched Patterns: {best_matches}")
        
        return best_type, best_confidence, best_matches
    
    def get_classification(self, conversation_text: str) -> Dict:
        """
        Get complete ticket classification including priority and type.
        
        Args:
            conversation_text: The full conversation text to analyze
            
        Returns:
            Dictionary containing priority, type and confidence scores
        """
        logger.info("\n=== Starting Ticket Classification ===")
        logger.info(f"Input text: {conversation_text[:100]}...")
        
        priority_id, priority_confidence, priority_patterns = self.classify_priority(conversation_text)
        type_id, type_confidence, type_patterns = self.classify_type(conversation_text)
        
        classification = {
            "priority": {
                "id": priority_id,
                "name": self.PRIORITY_NAMES[priority_id],
                "confidence": priority_confidence,
                "matched_patterns": priority_patterns
            },
            "type": {
                "id": type_id,
                "name": self.TYPE_NAMES[type_id],
                "confidence": type_confidence,
                "matched_patterns": type_patterns
            }
        }
        
        logger.info("\n=== Final Classification Results ===")
        logger.info(f"Priority: {classification['priority']['name']} (ID: {priority_id})")
        logger.info(f"Priority Confidence: {priority_confidence:.2f}")
        logger.info(f"Type: {classification['type']['name']} (ID: {type_id})")
        logger.info(f"Type Confidence: {type_confidence:.2f}")
        
        return classification 