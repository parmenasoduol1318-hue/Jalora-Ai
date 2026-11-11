# Jalora Capabilities Completion Plan

**Date:** 2026-10-21  
**Objective:** Implement missing capabilities to fully cover all user requirements

---

## Missing Capabilities Summary

Based on the audit, the following capabilities need implementation:

### Category 6: Personal and Business Assistance (4 missing capabilities)

1. **Job Discovery** - Find jobs matching user qualifications
2. **CV and Cover Letters** - Tailor documents for roles
3. **Business Planning** - Create business plans with milestones
4. **Customer Support** - Handle customer complaints/requests

### Category 3: Research and Reasoning (partial capability)

5. **Fact Verification Enhancement** - Multi-source verification with evidence citation

### Category 4: Browser, Apps and Communications (partial capability)

6. **Advanced File Management** - Organize files with metadata, auto-tagging

---

## Implementation Plan

### Phase 1: Job Discovery and Document Tools (Days 1-3)

#### 1.1 Job Discovery Tool (`app/tools/job_tools.py`)
- **Action**: Create `search_jobs` tool with LinkedIn/Indeed mock-first design
- **Permission Level**: AUTO
- **Features**:
  - Keyword-based job search
  - Location filtering
  - Salary range filtering
  - Mock responses for offline testing
  - Real API integration when keys configured

#### 1.2 CV Tool (`app/services/cv_personalization.py`)
- **Action**: Create service for CV personalization
- **Features**:
  - Extract skills and experience from user profile
  - Match against job requirements
  - Generate tailored CV content
  - Score relevance match percentage

---

### Phase 2: Business Planning and Customer Support (Days 4-6)

#### 2.1 Business Planning Tool (`app/tools/business_plan_tools.py`)
- **Action**: Create `create_business_plan` tool
- **Permission Level**: AUTO
- **Features**:
  - Market research template
  - Financial projections
  - Milestone scheduling
  - Risk assessment
  - Mock market data generation

#### 2.2 Customer Support Tool (`app/tools/support_tools.py`)
- **Action**: Create `respond_to_customer` tool
- **Permission Level**: CONFIRM (requires approval)
- **Features**:
  - Complaint categorization
  - Policy-based response generation
  - Empathy analysis
  - Escalation detection

---

### Phase 3: Content Creation Enhancement (Days 7-9)

#### 3.1 Social Media Content Tool (`app/tools/social_tools.py`)
- **Action**: Create `create_social_post` tool
- **Permission Level**: AUTO
- **Features**:
  - Platform-specific templates (LinkedIn, Twitter, Instagram)
  - Hashtag suggestions
  - Image caption generation
  - Engagement optimization

#### 3.2 Blog Content Tool (`app/tools/content_tools.py`)
- **Action**: Create `write_blog_post` tool
- **Permission Level**: AUTO
- **Features**:
  - SEO optimization
  - Outline generation
  - Draft composition
  - Revision suggestions

---

### Phase 4: Fact Verification Enhancement (Days 10-12)

#### 4.1 Multi-Source Verification Service (`app/services/verification.py`)
- **Action**: Enhance existing verification service
- **Features**:
  - Cross-source evidence gathering
  - Confidence scoring algorithm
  - Source credibility weighting
  - Evidence citation format

#### 4.2 Verification Tool (`app/tools/verify_tools.py`)
- **Action**: Create `verify_claim` tool
- **Permission Level**: AUTO
- **Features**:
  - Claim analysis
  - Multi-source fact checking
  - Confidence percentage
  - Evidence summary with sources

---

### Phase 5: Advanced File Management (Days 13-15)

#### 5.1 File Organization Service (`app/services/file_organizer.py`)
- **Action**: Create file organization service
- **Features**:
  - Auto-tagging based on content
  - Date-based sorting
  - Type-based grouping
  - Duplicate detection

#### 5.2 File Management Tools (`app/tools/file_tools.py`)
- **Action**: Create organization tools
- **Permission Level**: AUTO
- **Features**:
  - `organize_files` tool
  - `tag_file` tool
  - `find_files` tool with search
  - `merge_documents` tool

---

## Technical Architecture

### OAuth/Integration Pattern
All new tools follow the existing mock-first pattern:
```
service.py → Tool wrapper → Permission registration
```

### Configuration Pattern
Add new settings to `config.py`:
```python
job_provider: str = "auto"  # auto | mock | linkedin | indeed
linkedin_api_key: str | None = None
indeed_api_key: str | None = None
```

### Test Pattern
Create corresponding test files:
```python
tests/test_job_discovery.py
tests/test_business_planning.py
tests/test_customer_support.py
tests/test_content_creation.py
tests/test_verification.py
tests/test_file_management.py
```

---

## Implementation Checklist

### Phase 1: Job Discovery
- [ ] Create `app/tools/job_tools.py`
- [ ] Create mock job search service
- [ ] Add job provider settings to config
- [ ] Add LinkedIn/Indeed OAuth endpoints to oauth.py
- [ ] Register job cards in provider_extensions.py
- [ ] Create test file `tests/test_job_discovery.py`
- [ ] Add logos to ProviderLogos.tsx

### Phase 2: Business Planning
- [ ] Create `app/tools/business_plan_tools.py`
- [ ] Create business planning service
- [ ] Add business planning tools
- [ ] Create test file `tests/test_business_planning.py`

### Phase 3: Customer Support
- [ ] Create `app/tools/support_tools.py`
- [ ] Create support ticket service
- [ ] Add support tools with CONFIRM permission
- [ ] Create test file `tests/test_customer_support.py`

### Phase 4: Content Creation
- [ ] Create `app/tools/social_tools.py`
- [ ] Create social media templates
- [ ] Create `app/tools/content_tools.py`
- [ ] Add blog writing tools
- [ ] Create test files

### Phase 5: Verification
- [ ] Enhance `app/services/verification.py`
- [ ] Create `app/tools/verify_tools.py`
- [ ] Add multi-source verification
- [ ] Create test file `tests/test_verification_enhanced.py`

### Phase 6: File Management
- [ ] Create `app/services/file_organizer.py`
- [ ] Create `app/tools/file_tools.py`
- [ ] Add organization tools
- [ ] Create test file `tests/test_file_management.py`

---

## Dependencies

### External APIs Needed:
1. **LinkedIn API** - Job listings
2. **Indeed API** - Job listings
3. **NewsAPI** - Fact verification sources
4. **Wikipedia API** - Reference sources

### Environment Variables to Add:
```
JALORA_LINKEDIN_API_KEY
JALORA_INDEED_API_KEY
JALORA_NEWSAPI_KEY
```

---

## Estimated Timeline

| Phase | Duration | Effort | Risk |
|-------|----------|--------|------|
| Phase 1: Job Discovery | 3 days | Medium | Low |
| Phase 2: Business Planning | 3 days | Medium | Low |
| Phase 3: Customer Support | 3 days | Medium | Low |
| Phase 4: Content Creation | 3 days | Medium | Low |
| Phase 5: Verification | 3 days | High | Medium |
| Phase 6: File Management | 3 days | Medium | Low |

**Total Estimated Time: 15 days**

---

## Success Criteria

All tests pass:
- 50+ new test cases
- 100% backward compatibility
- Mock-first offline operation maintained
- No regression in existing 400+ tests

---

## Next Steps

1. Start implementation from Phase 1
2. Follow existing code patterns (payment_tools.py as template)
3. Add to provider_extensions.py for UI cards
4. Create comprehensive test coverage
5. Update documentation