"""Content creation tools (Phase 7).

Tools for generating social media posts, marketing content, and other materials.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.content_creation import (
    get_content_creator,
    ContentRequest,
    ContentIdea,
    generate_content_idea
)


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Content Generation Tools
# ============================================================================

async def generate_content(
    content_type: str,
    topic: str,
    tone: str = "professional",
    audience: str = "general",
    include_hashtags: bool = False,
    include_links: bool = False
) -> ToolResult:
    """Generate content for a given topic and type.
    
    Args:
        content_type: Type of content (linkedin_post, twitter_thread, blog_post, 
                      email_marketing, sales_pitch)
        topic: The topic to generate content about
        tone: Desired tone (professional, casual, inspiring, technical, etc.)
        audience: Target audience
        include_hashtags: Whether to include hashtags
        include_links: Whether to include relevant links
        
    Returns:
        ToolResult with generated content
    """
    creator = get_content_creator()
    
    request = ContentRequest(
        content_type=content_type,
        topic=topic,
        tone=tone,
        audience=audience,
        include_hashtags=include_hashtags,
        include_links=include_links
    )
    
    result = creator.generate_content(request)
    
    return ToolResult.ok(
        f"Generated {content_type} about '{topic}'",
        {
            "title": result.title,
            "body": result.body,
            "hashtags": result.hashtags,
            "links": result.links,
            "call_to_action": result.call_to_action,
            "metadata": result.metadata
        }
    )


async def get_content_ideas(topic: str, count: int = 3) -> ToolResult:
    """Generate content ideas for a topic.
    
    Args:
        topic: The topic to brainstorm ideas for
        count: Number of ideas to generate
        
    Returns:
        ToolResult with content ideas
    """
    ideas = []
    creator = get_content_creator()
    
    # Generate variations
    variations = [
        f"Complete guide to {topic}",
        f"{topic} basics: Start here",
        f"Advanced {topic} techniques",
        f"{topic} vs alternatives",
        f"Common {topic} mistakes to avoid"
    ]
    
    for i, variation in enumerate(variations[:count]):
        idea = ContentIdea(
            title=topic if i == 0 else variation,
            description=f"Content about {topic}",
            keywords=[topic] + variation.lower().split(),
            estimated_difficulty="medium"
        )
        ideas.append({
            "title": idea.title,
            "description": idea.description,
            "keywords": idea.keywords,
            "difficulty": idea.estimated_difficulty
        })
    
    return ToolResult.ok(
        f"Generated {len(ideas)} content ideas for '{topic}'",
        {"ideas": ideas, "total": len(ideas)}
    )


async def generate_linkedin_post(
    topic: str,
    tone: str = "professional",
    include_hashtags: bool = True
) -> ToolResult:
    """Generate a LinkedIn post about a topic.
    
    Args:
        topic: The topic to write about
        tone: Post tone (professional, casual, etc.)
        include_hashtags: Whether to include hashtags
        
    Returns:
        ToolResult with the LinkedIn post
    """
    return await generate_content(
        content_type="linkedin_post",
        topic=topic,
        tone=tone,
        include_hashtags=include_hashtags
    )


async def generate_twitter_thread(
    topic: str,
    tone: str = "casual",
    num_tweets: int = 5
) -> ToolResult:
    """Generate a Twitter thread.
    
    Args:
        topic: The topic to cover
        tone: Thread tone
        num_tweets: Number of tweets in the thread
        
    Returns:
        ToolResult with the Twitter thread
    """
    return await generate_content(
        content_type="twitter_thread",
        topic=topic,
        tone=tone,
        include_hashtags=False
    )


async def generate_blog_post(
    topic: str,
    tone: str = "informative"
) -> ToolResult:
    """Generate a blog post.
    
    Args:
        topic: The blog post topic
        tone: Post tone
        
    Returns:
        ToolResult with the blog post
    """
    return await generate_content(
        content_type="blog_post",
        topic=topic,
        tone=tone
    )


async def generate_email(
    topic: str,
    campaign: str = "marketing"
) -> ToolResult:
    """Generate a marketing email.
    
    Args:
        topic: Email topic
        campaign: Campaign name/type
        
    Returns:
        ToolResult with the email content
    """
    return await generate_content(
        content_type="email_marketing",
        topic=topic,
        tone="professional",
        include_links=True
    )


async def generate_sales_pitch(
    product: str,
    problem: str = "common problem"
) -> ToolResult:
    """Generate a sales pitch.
    
    Args:
        product: Product or service name
        problem: Problem being solved
        
    Returns:
        ToolResult with the sales pitch
    """
    topic = f"{product} for {problem}"
    return await generate_content(
        content_type="sales_pitch",
        topic=topic,
        tone="confident"
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(generate_content, permission=PermissionLevel.AUTO,
                  category="content", tags=["content", "generation", "marketing"])
registry.register(get_content_ideas, permission=PermissionLevel.AUTO,
                  category="content", tags=["content", "ideas", "brainstorming"])
registry.register(generate_linkedin_post, permission=PermissionLevel.AUTO,
                  category="content", tags=["social", "linkedin", "post"])
registry.register(generate_twitter_thread, permission=PermissionLevel.AUTO,
                  category="content", tags=["social", "twitter", "thread"])
registry.register(generate_blog_post, permission=PermissionLevel.AUTO,
                  category="content", tags=["content", "blog", "post"])
registry.register(generate_email, permission=PermissionLevel.AUTO,
                  category="content", tags=["content", "email", "marketing"])
registry.register(generate_sales_pitch, permission=PermissionLevel.AUTO,
                  category="content", tags=["content", "sales", "pitch"])