"""Tests for Content Creation (Phase 7).

Run with: python -m pytest backend/tests/test_content_creation.py -v
"""
import pytest

from app.services.content_creation import (
    ContentCreator,
    ContentRequest,
    ContentIdea,
    GeneratedContent,
    ContentCalendarEntry,
    get_content_creator,
    generate_content_idea,
)


class TestContentCreator:
    """Test the ContentCreator service."""
    
    def test_creator_singleton(self):
        """Test that get_content_creator returns singleton."""
        c1 = get_content_creator()
        c2 = get_content_creator()
        assert c1 is c2
    
    def test_creator_creation(self):
        """Test ContentCreator can be created."""
        creator = ContentCreator()
        assert creator is not None


class TestContentRequest:
    """Test ContentRequest dataclass."""
    
    def test_request_creation(self):
        """Test creating a content request."""
        request = ContentRequest(
            content_type="blog_post",
            topic="AI in healthcare",
            tone="professional",
            include_hashtags=True
        )
        
        assert request.content_type == "blog_post"
        assert request.topic == "AI in healthcare"
        assert request.tone == "professional"
        assert request.include_hashtags == True


class TestGeneratedContent:
    """Test GeneratedContent dataclass."""
    
    def test_content_creation(self):
        """Test creating generated content."""
        content = GeneratedContent(
            content_type="linkedin_post",
            title="Test Title",
            body="Test body content",
            hashtags=["#test", "#example"],
            call_to_action="Share if you agree!"
        )
        
        assert content.content_type == "linkedin_post"
        assert content.title == "Test Title"
        assert len(content.hashtags) == 2
        assert content.call_to_action == "Share if you agree!"


class TestContentIdeas:
    """Test ContentIdea dataclass."""
    
    def test_idea_creation(self):
        """Test creating a content idea."""
        idea = ContentIdea(
            title="Complete Guide to AI",
            description="A comprehensive guide",
            keywords=["AI", "guide", "tutorial"],
            estimated_difficulty="medium"
        )
        
        assert idea.title == "Complete Guide to AI"
        assert len(idea.keywords) == 3
        assert idea.estimated_difficulty == "medium"


class TestGeneration:
    """Test content generation."""
    
    def test_generate_blog_post(self):
        """Test generating a blog post."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="blog_post",
            topic="Machine Learning Basics",
            tone="informative"
        )
        
        result = creator.generate_content(request)
        
        assert result.content_type == "blog_post"
        assert result.title is not None
        assert "Machine Learning Basics" in result.title or len(result.body) > 0
    
    def test_generate_linkedin_post(self):
        """Test generating a LinkedIn post."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="linkedin_post",
            topic="Career Growth",
            tone="professional",
            include_hashtags=True
        )
        
        result = creator.generate_content(request)
        
        assert len(result.hashtags) > 0
        assert "Career Growth" in result.body or len(result.body) > 0
    
    def test_generate_twitter_thread(self):
        """Test generating a Twitter thread."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="twitter_thread",
            topic="Productivity Tips",
            tone="casual"
        )
        
        result = creator.generate_content(request)
        
        assert "1/5" in result.body or "1/" in result.body
    
    def test_generate_sales_pitch(self):
        """Test generating a sales pitch."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="sales_pitch",
            topic="New Project Management Tool",
            tone="confident"
        )
        
        result = creator.generate_content(request)
        
        assert "Solution" in result.body or "solution" in result.body.lower()
    
    def test_generated_content_has_metadata(self):
        """Test that generated content includes metadata."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="blog_post",
            topic="Test Topic"
        )
        
        result = creator.generate_content(request)
        
        assert "generated_at" in result.metadata
        assert "requested_tone" in result.metadata


class TestHashtagGeneration:
    """Test hashtag generation."""
    
    def test_hashtags_generated_for_technology(self):
        """Test hashtag generation for technology topics."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="linkedin_post",
            topic="Artificial Intelligence",
            include_hashtags=True
        )
        
        result = creator.generate_content(request)
        
        assert len(result.hashtags) > 0
    
    def test_hashtags_deduplicated(self):
        """Test that duplicate hashtags are removed."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="linkedin_post",
            topic="AI",
            include_hashtags=True
        )
        
        result = creator.generate_content(request)
        
        # Check no duplicates
        assert len(result.hashtags) == len(set(result.hashtags))


class TestToneGeneration:
    """Test different tone generation."""
    
    def test_professional_tone(self):
        """Test professional tone."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="blog_post",
            topic="Management",
            tone="professional"
        )
        
        result = creator.generate_content(request)
        assert result is not None
    
    def test_casual_tone(self):
        """Test casual tone."""
        creator = get_content_creator()
        
        request = ContentRequest(
            content_type="linkedin_post",
            topic="Team Building",
            tone="casual"
        )
        
        result = creator.generate_content(request)
        assert result is not None


class TestContentCalendarEntry:
    """Test ContentCalendarEntry dataclass."""
    
    def test_calendar_entry_creation(self):
        """Test creating a calendar entry."""
        from datetime import datetime, timezone
        
        entry = ContentCalendarEntry(
            date=datetime.now(timezone.utc),
            content_type="blog_post",
            topic="Test Topic",
            status="draft"
        )
        
        assert entry.content_type == "blog_post"
        assert entry.status == "draft"


class TestGenerateContentIdea:
    """Test the generate_content_idea helper function."""
    
    def test_generate_idea(self):
        """Test generating a content idea."""
        idea = generate_content_idea("Blockchain", "blog_post")
        
        assert isinstance(idea, ContentIdea)
        assert idea.title is not None
    
    def test_idea_has_keywords(self):
        """Test that generated idea has keywords."""
        idea = generate_content_idea("Digital Marketing")
        
        assert len(idea.keywords) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])