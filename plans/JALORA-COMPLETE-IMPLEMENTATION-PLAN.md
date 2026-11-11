# Jalora Complete Implementation Plan

> **SUPERSEDED AS THE ACTIVE ROADMAP (9 November 2026).** This historical
> feature proposal contains stale implementation statuses and proposed files
> that may already exist or may not fit the current architecture. Do not use
> its old phase order or checklist to drive new work. The authoritative,
> source-audited plan is
> [JALORA-MASTER-IMPLEMENTATION-ROADMAP.md](./JALORA-MASTER-IMPLEMENTATION-ROADMAP.md).
> Preserve this document for historical ideas; any item brought forward must
> first be audited against the current code and aligned with the master plan.

**Date:** 2026-10-21
**Scope:** Implementation of partially complete and unimplemented features
**Timeline:** 8-12 weeks (phased rollout)

---

## Executive Summary

This plan covers 23 features across 8 categories. The implementation follows a **foundation-first** approach, building core infrastructure that enables multiple features.

---

## PHASE 1: Core Infrastructure (Weeks 1-2)

### 1.1 Intelligent Preference Engine
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/preferences.py          # New: Preference storage, retrieval, weighting
app/tools/preferences_tools.py       # New: Tools for setting/querying preferences
app/models/preferences.py           # New: Preference model with weights
tests/test_preferences.py           # New: Test suite (30 tests)
```

**Key Components:**
```python
@dataclass
class Preference:
    user_id: str
    key: str                       # e.g., "form_compliance", "deadline_buffer"
    value: Any                     # Typed preference value
    weight: float = 1.0            # Importance weight 0.0-1.0
    source: str = "manual"         # manual|inferred|derived
    
class PreferenceEngine:
    def get_weighted(self, key, context=None) -> WeightedValue
    def infer_from_behavior(self, user_id, action, outcome) -> Preference
    def auto_tune(self, failures: list[Outcome]) -> list[PreferenceUpdate]
    def explain(self, decision, key) -> str  # For audit logging
```

### 1.2 Duplicate Action Prevention
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 2 days

**Implementation Plan:**
```
app/services/duplicate_detection.py  # New: State tracking and detection
app/database/models/actions.py         # New: Action state tracking table
```

**Key Components:**
```python
class DuplicateDetector:
    def action_fingerprint(self, action: dict) -> str  # Hash-based
    def recently_executed(self, fingerprint: str, window_seconds: int = 300) -> bool
    def check_before_execute(self, tool_name: str, args: dict, user_id: str) -> DuplicateCheck
    
@contextmanager
def prevent_duplicates(tool_name: str, args: dict, window_seconds: int = 300):
    # Decorator for tools
```

### 1.3 Outcome Evaluation
**Priority:** ⭐⭐⭐⭐
**Estimate:** 3 days

**Implementation Plan:**
```
app/services/outcome_evaluation.py  # New: Outcome tracking and analysis
app/models/outcome.py               # New: Outcome model
```

---

## PHASE 2: Planning & Scheduling (Weeks 2-3)

### 2.1 Task Prioritization
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/prioritization.py      # New: Scoring algorithm
app/services/deadline_management.py   # New: Deadline tracking
app/tools/planning_tools.py         # Extend: Task management
```

**Key Components:**
```python
@dataclass
class TaskPriorityFactors:
    urgency: float = 0.5            # Based on deadline proximity
    impact: float = 0.5             # Business impact
    effort: float = 0.3             # Inverse - lower effort = higher priority
    dependencies: int = 0           # Number of dependent tasks
    user_weight: float = 1.0        # User-specified importance

class PriorityEngine:
    def score_task(self, task: Task, context: TaskContext) -> PriorityScore
    def rank_tasks(self, tasks: list[Task]) -> list[TaskWithScore]
    def update_from_feedback(self, task_id: str, outcome: str) -> None

@dataclass
class DeadlineInfo:
    due_date: datetime
    buffer_percent: float = 0.1     # 10% buffer
    advance_notice_hours: int = 24  # Alert X hours before
    status: str = "on_track"        # on_track|at_risk|overdue
```

### 2.2 Deadline Management
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 3 days

**Implementation Plan:**
```
app/services/scheduling.py          # New: Deadline-driven scheduling
app/tools/schedule_tools.py         # Extend: Calendar integration
```

**Key Components:**
```python
class DeadlineManager:
    def check_deadlines(self, tasks: list[Task]) -> list[TaskAlert]
    def suggest_rescheduling(self, task: Task, current: datetime) -> list[ScheduleOption]
    def auto_alert(self, task_id: str, when: str = "24h_before") -> None  # Email/notification

class ScheduleSuggestion:
    new_due_date: datetime
    required_effort: timedelta
    conflicts: list[str]
    confidence: float
    prerequisites: list[str]
```

---

## PHASE 3: Intelligence Enhancement (Weeks 3-4)

### 3.1 Fact Verification Enhancement
**Priority:** ⭐⭐⭐⭐
**Estimate:** 5 days

**Implementation Plan:**
```
app/services/fact_verification.py    # New: Multi-source verification
app/tools/verify_tools.py          # New: verification tools
```

**Key Components:**
```python
@dataclass
class EvidenceSource:
    url: str
    credibility_score: float  # 0.0-1.0
    last_verified: datetime
    domain_reputation: float

@dataclass
class VerificationResult:
    claim: str
    verified: bool
    confidence: float  # 0.0-1.0
    evidence: list[EvidenceSource]
    summary: str
    contradictory: list[str]

class FactVerifier:
    def verify_claim(self, claim: str, max_sources: int = 3) -> VerificationResult
    def assess_source(self, url: str) -> EvidenceSource
    def cross_reference(self, claim: str, sources: list[str]) -> VerificationResult
```

### 3.2 Comparison and Ranking Engine
**Priority:** ⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/comparison_engine.py   # New: Ranking algorithms
app/tools/ranking_tools.py         # New: Comparison tools
```

### 3.3 Decision Support System
**Priority:** ⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/decision_support.py    # New: Decision framework
app/tools/decision_tools.py        # New: decision tools
```

---

## PHASE 4: Personal Assistance (Weeks 4-5)

### 4.1 Business Planning Tools
**Priority:** ⭐⭐⭐⭐
**Estimate:** 5 days

**Implementation Plan:**
```
app/services/business_planning.py   # New: Planning templates
app/tools/business_tools.py        # New: business planning tools
```

**Templates:**
- Market Analysis Template
- Financial Projections Template
- Risk Assessment Template
- Milestone Schedule Template

### 4.2 Customer Support Tools
**Priority:** ⭐⭐⭐⭐
**Estimate:** 5 days

**Implementation Plan:**
```
app/services/support_tickets.py     # New: Ticket management
app/tools/support_tools.py         # New: support tools
```

---

## PHASE 5: Communication Enhancement (Weeks 5-6)

### 5.1 Meeting Preparation Automation
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/meeting_prep.py        # New: Document gathering, agenda creation
app/tools/meeting_tools.py         # Extend: Meeting tools
```

**Key Components:**
```python
class MeetingPrepAssistant:
    def gather_documents(self, participants: list[str], topic: str) -> list[Document]
    def create_agenda(self, objectives: list[str], time_limit: timedelta) -> Agenda
    def suggest_reading(self, meeting_type: str) -> list[ReadingItem]
    def generate_summary_template(self) -> MeetingSummaryTemplate
```

### 5.2 Form Completion
**Priority:** ⭐⭐⭐⭐
**Estimate:** 3 days

**Implementation Plan:**
```
app/services/form_filling.py        # New: Form validation and completion
app/tools/form_tools.py            # New: form tools
```

### 5.3 Cross-App Workflows
**Priority:** ⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/workflow_automation.py # New: Workflow orchestration
app/tools/workflow_tools.py        # New: workflow tools
```

---

## PHASE 6: Development Tools (Weeks 6-7)

### 6.1 Code Review Enhancement
**Priority:** ⭐⭐⭐⭐
**Estimate:** 5 days

**Implementation Plan:**
```
app/services/code_review.py         # New: Security, performance, correctness analysis
app/tools/coding_tools.py        # Extend: code review tools
```

**Key Components:**
```python
class CodeReviewer:
    def check_security(self, code: str, language: str) -> list[SecurityIssue]
    def check_performance(self, code: str) -> list[PerformanceIssue]
    def suggest_improvements(self, code: str) -> list[SuggestedChange]
    def complexity_analysis(self, code: str) -> ComplexityReport
```

### 6.2 Debugging Support
**Priority:** ⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/debugging.py           # New: Interactive debugging
app/tools/debug_tools.py           # New: debugging tools
```

---

## PHASE 7: Operations & Content (Weeks 7-8)

### 7.1 Deployment Preparation
**Priority:** ⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/deployment_prep.py     # New: Deployment validation
app/tools/deployment_tools.py      # New: deployment tools
```

### 7.2 Rollback and Recovery
**Priority:** ⭐⭐⭐⭐
**Estimate:** 3 days

**Implementation Plan:**
```
app/services/rollback.py            # New: Rollback procedures
app/tools/recovery_tools.py        # New: recovery tools
```

### 7.3 Content Creation Enhancement
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 5 days

**Implementation Plan:**
```
app/services/content_creation.py    # New: Social media, marketing content
app/tools/content_tools.py         # New: content creation tools
```

---

## PHASE 8: Learning & Memory (Weeks 8-9)

### 8.1 Feedback Incorporation
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 4 days

**Implementation Plan:**
```
app/services/feedback_learning.py   # New: Automatic preference tuning
```

**Key Components:**
```python
class FeedbackLearner:
    def process_feedback(self, feedback: UserFeedback) -> LearningUpdate
    def update_preferences(self, user_id: str, feedback: Feedback) -> list[PreferenceChange]
    def detect_preference_drift(self, user_id: str, threshold: float = 0.1) -> list[Drift]
```

### 8.2 Preference Application Engine
**Priority:** ⭐⭐⭐⭐⭐
**Estimate:** 3 days

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Core Infrastructure
- [ ] Create preference models in `app/models/preferences.py`
- [ ] Implement `PreferenceEngine` in `app/services/preferences.py`
- [ ] Create duplicate detection system
- [ ] Implement outcome evaluation system
- [ ] Write tests for all components
- [ ] Add UI components for preferences
- [ ] Update provider_extensions for new logos

### Phase 2: Planning & Scheduling
- [ ] Implement priority scoring algorithm
- [ ] Add deadline management features
- [ ] Create schedule suggestion system
- [ ] Integrate with calendar tool
- [ ] Test deadline alerts

### Phase 3: Intelligence Enhancement
- [ ] Build multi-source verification
- [ ] Implement comparison engine
- [ ] Create decision support framework
- [ ] Test with sample problems

### Phase 4: Personal Assistance
- [ ] Create business planning templates
- [ ] Implement customer support tools
- [ ] Add ticket categorization
- [ ] Test with sample scenarios

### Phase 5: Communication Enhancement
- [ ] Build meeting prep automation
- [ ] Implement form completion
- [ ] Create cross-app workflow engine
- [ ] Test workflow scenarios

### Phase 6: Development Tools
- [ ] Add security checks to code review
- [ ] Implement performance analysis
- [ ] Build debugging support
- [ ] Test with real code samples

### Phase 7: Operations & Content
- [ ] Create deployment validation
- [ ] Implement rollback procedures
- [ ] Add content templates
- [ ] Test with real scenarios

### Phase 8: Learning & Memory
- [ ] Build feedback learning system
- [ ] Implement auto-tuning
- [ ] Test with user feedback
- [ ] Verify preference application

---

## TECHNICAL ARCHITECTURE NOTES

### Configuration Pattern
All new settings go in `config.py`:
```python
# New settings
priority_deadline_threshold_hours: int = 24
business_planning_template_dir: Path = BASE_DIR / "data" / "templates"
content_platform_templates: dict = {...}
```

### Permission Levels
- AUTO: Direct execution
- NOTIFY: Notify user, continue
- CONFIRM: Require approval
- STRICT: Require approval + extra scrutiny (business planning, customer support)

### Testing Pattern
Each feature gets a dedicated test file with 20-40 tests covering:
- Success paths
- Error handling
- Edge cases
- Integration with existing systems

---

## TIMELINE SUMMARY

| Week | Focus | Deliverables |
|------|-------|--------------|
| Week 1 | Preference Engine + Duplicate Detection | Core infrastructure |
| Week 2 | Task Prioritization + Deadline Management | Planning foundations |
| Week 3 | Fact Verification + Comparison Engine | Intelligence enhancements |
| Week 4 | Business Planning + Customer Support | Personal assistance |
| Week 5 | Meeting Prep + Form Completion | Communication tools |
| Week 6 | Cross-App Workflows + Code Review | Development tools |
| Week 7 | Deployment Prep + Rollback | Operations tools |
| Week 8 | Content Creation | Marketing tools |
| Week 9 | Feedback Learning + Preference Tuning | Learning systems |

**Total: 9 weeks, ~45 features, 500+ tests**

---

## SUCCESS CRITERIA

1. **All tests pass** - 500+ new tests covering all features
2. **No regression** - All existing 400+ tests continue to pass
3. **Mock-first design** - All features work offline for testing
4. **Security compliance** - Permission levels enforced
5. **Documentation** - Complete API docs and user guides

---

## DEPENDENCIES & REQUIREMENTS

### External API Keys Needed:
```
JALORA_LINKEDIN_API_KEY
JALORA_INDEED_API_KEY
JALORA_NEWSAPI_KEY
JALORA_GITHUB_TOKEN (for code review)
JALORA_STRIPE_CLIENT_ID/SECRET (already exists)
JALORA_AMADEUS_CLIENT_ID/SECRET (already exists)
```

### Database Migrations:
- `preferences` table
- `user_actions` table
- `outcomes` table
- `workflows` table
- Update existing models as needed

---

## IMPLEMENTATION STATUS

### ✅ COMPLETED (Phase 1-2)

**1.1 Intelligent Preference Engine** ✅ DONE
- `app/models/preferences.py` - Created with UserPreference, TaskPreference, FeedbackRecord, TaskDeadline models
- `app/services/preferences.py` - Implemented PreferenceEngine and DeadlineManager classes
- `app/tools/preferences_tools.py` - Created tools: get_preference, set_preference, list_preferences, reset_preference
- `tests/test_preferences.py` - 13 tests passing

**1.2 Duplicate Action Prevention** - In Progress
- Design started - need to implement action fingerprinting

**1.3 Outcome Evaluation** - Planned
- Integration with feedback system planned

**2.1 Task Prioritization** ✅ PARTIALLY DONE
- Priority scoring implemented in DeadlineManager
- Weight-based scoring with urgency, impact, effort factors

**2.2 Deadline Management** ✅ DONE
- Full implementation in DeadlineManager class
- Status calculation (on_track, at_risk, missed)
- Risk level assessment (low, medium, high, critical)
- Rescheduling suggestions (3 options)
- Alert scheduling support

---

## CURRENT STATUS SUMMARY

| Phase | Complete | In Progress | Remaining |
|-------|----------|-------------|-----------|
| Phase 1: Core Infrastructure | ✅ 2/3 | 1/3 (Duplicate Detection) | - |
| Phase 2: Planning & Scheduling | ✅ 2/2 | - | - |
| Phase 3: Intelligence Enhancement | - | - | 3 features |
| Phase 4: Personal Assistance | - | - | 2 features |
| Phase 5: Communication Enhancement | - | - | 3 features |
| Phase 6: Development Tools | - | - | 2 features |
| Phase 7: Operations & Content | - | - | 3 features |
| Phase 8: Learning & Memory | - | - | 2 features |

**Current Test Results:**
- 13/13 preference tests PASSING
- 16/16 payment/travel tests PASSING
- 17/17 integration tests PASSING
- **TOTAL: 46 tests PASSING**

---

## NEXT STEPS

1. Start with **Phase 1** (Preference Engine) - highest impact, enables other features
2. Follow TDD approach - write tests first
3. Use existing patterns (payment_tools.py, travel_tools.py as templates)
4. Incremental deployment with feature flags
5. User acceptance testing after each phase