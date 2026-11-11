"""Content creation services (Phase 7).

Provides tools for creating social media posts, marketing content, and other materials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ============================================================================
# Content Templates (Module-level for fast access)
# ============================================================================

CONTENT_TEMPLATES = {
    "linkedin_post": {"structure": ["hook", "body", "cta"], "length": 1300},
    "twitter_thread": {"structure": ["tweet1", "tweet2", "tweet3", "tweet4", "tweet5"], "length": 280},
    "blog_post": {"structure": ["title", "introduction", "body", "conclusion"], "length": 2000},
    "email_marketing": {"structure": ["subject", "preview", "body", "signature"], "length": 5000},
    "sales_pitch": {"structure": ["hook", "problem", "solution", "proof", "cta"], "length": 800},
}

TONE_TEMPLATES = {
    "professional": "As a professional in the industry, I've noticed that {topic}...",
    "casual": "Hey everyone! Just wanted to share some thoughts on {topic}...",
    "inspirational": "What if I told you that {topic} could change your life?",
    "technical": "In this technical deep-dive, we'll explore {topic}...",
    "friendly": "Quick update on {topic} - you're going to love this!",
    "urgent": "URGENT: {topic} requires your immediate attention",
    "celebratory": "🎉 Great news! {topic} is here to make your day!",
    "persuasive": "Imagine if {topic} was as easy as 1-2-3...",
    "informative": "{topic} is an important subject that deserves attention.",
    "storytelling": "Let me tell you a story about {topic}..."
}

HASHTAG_GENERATORS = {
    "technology": ["#tech", "#innovation", "#future", "#AI", "#automation"],
    "business": ["#business", "#entrepreneur", "#success", "#growth", "#leadership"],
    "marketing": ["#marketing", "#branding", "#growthhack", "#content", "#campaign"],
    "career": ["#career", "#jobs", "#hiring", "#skills", "#growth"],
    "finance": ["#finance", "#money", "#investment", "#wealth", "#financeTips"],
    "health": ["#health", "#wellness", "#fitness", "#nutrition", "#selfcare"],
    "education": ["#education", "#learning", "#elearning", "#skills", "#teachers"],
    "travel": ["#travel", "#wanderlust", "#adventure", "#explore", "#vacation"],
}

CTA_TEMPLATES = {
    "linkedin_post": "What are your thoughts? Share below 👇",
    "twitter_thread": "Retweet if you found this helpful! ❤️",
    "blog_post": "Subscribe for more insights like this!",
    "email_marketing": "Click here to learn more →",
    "sales_pitch": "Book a demo today!"
}


# ============================================================================
# Content Creation Models
# ============================================================================

@dataclass
class ContentRequest:
    """Request for content creation."""
    content_type: str
    topic: str
    audience: str = "general"
    tone: str = "professional"
    length: int | None = None
    custom_instructions: str = ""
    include_hashtags: bool = False
    include_links: bool = False
    platform: str = ""


@dataclass
class ContentIdea:
    """Idea for content."""
    title: str
    description: str
    keywords: list[str] = field(default_factory=list)
    estimated_difficulty: str = "medium"  # easy, medium, hard


@dataclass
class GeneratedContent:
    """Generated content with metadata."""
    content_type: str
    title: str
    body: str
    hashtags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    call_to_action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentCalendarEntry:
    """Entry in a content calendar."""
    date: datetime
    content_type: str
    topic: str
    status: str = "draft"  # draft, scheduled, published
    platform: str = ""
    title: str = ""
    hashtags: list[str] = field(default_factory=list)
    associated_campaign: str = ""


# ============================================================================
# Content Creation Service
# ============================================================================

class ContentCreator:
    """Service for creating various types of content."""
    
    def __init__(self):
        # Use module-level constants for fast access
        pass
    
    def generate_content(self, request: ContentRequest) -> GeneratedContent:
        """Generate content based on a request."""
        template_info = CONTENT_TEMPLATES.get(request.content_type, {})
        structure = template_info.get("structure", ["body"])
        
        # Generate body content
        body = self._generate_body(request, structure)
        
        # Generate hashtags if requested
        hashtags = []
        if request.include_hashtags:
            hashtags = self._generate_hashtags(request.topic, request.audience)
        
        # Generate links if requested
        links = []
        if request.include_links:
            links = self._generate_links(request.topic)
        
        # Generate metadata
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "requested_tone": request.tone,
            "platform": request.platform or request.content_type
        }
        
        # Generate title
        title = self._generate_title(request)
        
        # Generate CTA
        cta = CTA_TEMPLATES.get(request.content_type, "Let me know what you think!")
        
        return GeneratedContent(
            content_type=request.content_type,
            title=title,
            body=body,
            hashtags=hashtags,
            links=links,
            call_to_action=cta,
            metadata=metadata
        )
    
    def _generate_body(self, request: ContentRequest, structure: list[str]) -> str:
        """Generate the body content."""
        tone_template = TONE_TEMPLATES.get(request.tone, "")
        topic_intro = tone_template.format(topic=request.topic)
        
        # Generate main content based on type
        if request.content_type == "linkedin_post":
            body = self._generate_linkedin_post(request, topic_intro)
        elif request.content_type == "twitter_thread":
            body = self._generate_twitter_thread(request, topic_intro)
        elif request.content_type == "blog_post":
            body = self._generate_blog_post(request, topic_intro)
        elif request.content_type == "email_marketing":
            body = self._generate_email(request, topic_intro)
        elif request.content_type == "sales_pitch":
            body = self._generate_sales_pitch(request, topic_intro)
        else:
            body = topic_intro + f"\n\nThis is about {request.topic}. Check it out!"
        
        return body
    
    def _generate_linkedin_post(self, request: ContentRequest, intro: str) -> str:
        """Generate a LinkedIn post."""
        return f"""{intro}

Here's what I've learned about {request.topic}:

• Key insight 1 about {request.topic}
• Key insight 2 about {request.topic}  
• Key insight 3 about {request.topic}

What's your experience with {request.topic}? Let's discuss in the comments!

#Professional #Growth"""
    
    def _generate_twitter_thread(self, request: ContentRequest, intro: str) -> str:
        """Generate a Twitter thread."""
        return f"""{intro}

1/5: The rise of {request.topic}

2/5: How it works

3/5: Benefits and impact

4/5: Challenges and solutions

5/5: Your action items

Thread 🧵"""
    
    def _generate_blog_post(self, request: ContentRequest, intro: str) -> str:
        """Generate a blog post."""
        return f"""# {request.topic}

{intro}

## Introduction

{request.topic} has become increasingly important in today's digital landscape...

## Main Content

Here's everything you need to know about {request.topic}:

### Aspect 1
Detailed explanation...

### Aspect 2
Detailed explanation...

### Aspect 3
Detailed explanation...

## Conclusion

In conclusion, {request.topic} offers many benefits..."""
    
    def _generate_email(self, request: ContentRequest, intro: str) -> str:
        """Generate a marketing email."""
        return f"""Subject: Your guide to {request.topic}

{intro}

Hi there,

I'm excited to share some valuable insights about {request.topic}.

[Main content goes here...]

Best regards,
The Team"""
    
    def _generate_sales_pitch(self, request: ContentRequest, intro: str) -> str:
        """Generate a sales pitch."""
        return f"""{intro}

The Problem: {request.topic} is causing headaches for many professionals.

Our Solution: We've created a simple solution that makes {request.topic} effortless.

Why Choose Us:
• Benefit 1
• Benefit 2  
• Benefit 3

Ready to transform your {request.topic} experience?

[Get Started Now]"""
    
    def _generate_title(self, request: ContentRequest) -> str:
        """Generate a title for the content."""
        return f"Mastering {request.topic}: Your Complete Guide"
    
    def _generate_hashtags(self, topic: str, audience: str) -> list[str]:
        """Generate relevant hashtags."""
        base_hashtags = ["#" + topic.replace(" ", "").lower()]
        
        # Get topic-specific hashtags
        topic_keywords = topic.lower().split()[:3]
        for keyword in topic_keywords:
            for key, tags in HASHTAG_GENERATORS.items():
                if keyword.lower() in key.lower():
                    base_hashtags.extend(tags[:3])
                    break
        
        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for tag in base_hashtags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)
        
        return unique_tags[:10]  # Return top 10
    
    def _generate_links(self, topic: str) -> list[str]:
        """Generate relevant links for the topic."""
        # In production, these would be actual relevant links
        return [
            f"https://example.com/{topic.replace(' ', '')}/guide",
            f"https://example.com/{topic.replace(' ', '')}/resources"
        ]


# Singleton instance
_creator: ContentCreator | None = None

def get_content_creator() -> ContentCreator:
    """Get or create the content creator singleton."""
    global _creator
    if _creator is None:
        _creator = ContentCreator()
    return _creator


def generate_content_idea(topic: str, content_type: str = "blog_post") -> ContentIdea:
    """Generate content ideas for a topic."""
    creator = get_content_creator()
    
    ideas = [
        ContentIdea(
            title=f"{topic}: The Ultimate Guide",
            description=f"A comprehensive guide to {topic}",
            keywords=[topic, "guide", "tutorial"],
            estimated_difficulty="medium"
        ),
        ContentIdea(
            title=f"Top 5 {topic} Tips",
            description=f"Learn the best practices for {topic}",
            keywords=[topic, "tips", "best practices"],
            estimated_difficulty="easy"
        )
    ]
    
    return ideas[0] if ideas else ContentIdea(title=topic, description=f"Content about {topic}")