"""Deterministic intent classification for the UNDERSTAND phase (spec §4).

Offline and rule-based by design: no model call, no network, identical results
in tests and production. The classified intent drives orchestration choices
(research brief injection, plan discipline) and is surfaced to the UI as an
SSE `intent` event — it never replaces the model's own tool selection.

Supported intents: research, task, coding, scheduling, science, math, chat,
email, and web_search.  See ``classify_intent`` for the priority order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IntentResult:
    intent: str  # research | task | coding | scheduling | science | math | chat | email | web_search
    signals: tuple[str, ...] = ()


_SCHEDULING = re.compile(
    r"\b(schedule|remind(?:er|ing)?|calendar|appointment|wake me up|agenda)"
    r"|\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)\b",
    re.I,
)
# Email intent - matches email-specific patterns to help route email tools
_EMAIL = re.compile(
    r"\b(send\s+)?email\b"
    r"|\bmail\b"
    r"|\bmailto:"
    r"|mailto:",
    re.I,
)
_NEGATED_EMAIL_ACTION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|no\s+need\s+to|not\s+to)\b"
    r"(?:\s+\w+){0,3}\s+(?:send|email|mail)\b",
    re.I,
)
_PUBLIC_CONTACT_LOOKUP = re.compile(
    r"""\b(?:search|find|look\s+up|retrieve|discover)\b.{0,120}
    \b(?:public|official|current|valid|contact|support)\b.{0,60}\be-?mails?\b
    |\b(?:current|valid|official|public)\b.{0,60}\b(?:contact|support)\s+e-?mails?\b
    |\b(?:search|find|look\s+up|retrieve|discover)\s+e-?mails?(?:\s+or\s+web)?\s+for\b
    .{0,120}\b(?:contact|support)\s+e-?mails?\b
    |\b(?:search|find|look\s+up|lookup|fetch|get|retrieve|discover)\b.{0,60}
    \b(?:contact\s+e-?mails?|e-?mails|e-?mail\s+addresses?)\b
    .{0,60}\b(?:for|of)\b
    |\b(?:search|find)\s+companies?\s+for\b
    .{0,160}\b(?:contact|support)\s+e-?mails?\b""",
    re.I | re.X,
)
_ORGANIZATION_EMAIL_LOOKUP = re.compile(
    r"""\b(?:search|find|look\s+up|lookup|fetch|get|retrieve|discover|scrape|locate)\b
    .{0,100}\b(?:e-?mails?(?:\s+addresses?)?|contact\s+details?)\b
    .{0,120}\b(?:organizations?|organisations?|companies|businesses|institutions|
    agencies|charities|nonprofits?|universities|schools|hospitals|
    the\s+listed\s+(?:ones|organizations?|organisations?))\b
    |\b(?:e-?mails?(?:\s+addresses?)?|contact\s+details?)\b.{0,100}
    \b(?:of|for|from)\b.{0,100}\b(?:organizations?|organisations?|companies|
    businesses|institutions|agencies|charities|nonprofits?|universities|schools|
    hospitals|the\s+listed\s+(?:ones|organizations?|organisations?))\b
    |\b(?:organizations?|organisations?|companies|businesses|institutions|
    agencies|charities|nonprofits?|universities|schools|hospitals)\b
    .{0,120}\b(?:e-?mails?(?:\s+addresses?)?|contact\s+details?)\b
    .{0,80}\b(?:search|find|look\s+up|lookup|fetch|get|retrieve|discover|scrape|locate)\b""",
    re.I | re.X,
)
_OUTREACH_ACTION = re.compile(
    r"\b(?:draft|compose|write|prepare|send|email|outreach)\b",
    re.I,
)
_MULTIPLE_RECIPIENTS = re.compile(
    r"\b(?:all|each|every|multiple|several|eight|seven|six|five|four|three|"
    r"two|[2-9])\s+(?:of\s+)?(?:the\s+)?(?:target\s+)?"
    r"(?:email\s+)?(?:recipients?|organizations?|orgs|companies|contacts)\b"
    r"|\b(?:emails|messages|drafts)\b"
    r"|\b(?:recipients?|organizations?|orgs|companies|contacts)\b"
    r".{0,80}\b(?:and|each|all)\b",
    re.I,
)
_OUTREACH_STYLE = re.compile(
    r"\b(?:same|identical|shared)\s+(?:email|message|draft|copy)\b"
    r"|\bone\s+(?:email|message|draft|copy)\s+for\s+all\b"
    r"|\bone\s+(?:email|message|draft|copy)\s+(?:for|to)\s+"
    r"(?:each|every)\s+(?:recipient|organization|organisation|contact)\b"
    r"|\bfor\s+each\s+(?:recipient|organization|organisation|contact)\b"
    r".{0,60}\bonly\s+(?:send\s+)?(?:in\s+)?one\s+"
    r"(?:email|message|draft|copy)\b"
    r"|\b(?:custom|tailored|personalized|personalised)\s+outreach\b"
    r"|\bcustom\s+(?:emails?|messages?|drafts?)\b"
    r"|\b(?:personalized|personalised|tailored|individual|customized|"
    r"customised|custom)\s+(?:emails?|messages?|drafts?|copies)\b"
    r"|\b(?:different|separate)\s+(?:emails?|messages?|drafts?|copies)\b"
    r"|\b(?:each|every|per)\s+(?:recipient|organization|contact)\b"
    r".{0,40}\b(?:own|unique|individual|different|tailored|personalized|"
    r"personalised|custom)\b",
    re.I,
)
_EMAIL_ADDRESS = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
)
_EXPLICIT_SEND_ACTION = re.compile(r"\bsend\b", re.I)
_EXPLICIT_EMAIL_APPROVAL_ACTION = re.compile(
    r"\b(?:approve|approval)\b.{0,60}"
    r"\b(?:each|every|individual|email|draft|recipient|send)\b"
    r"|\b(?:each|every|individual)\b.{0,40}\b(?:approve|approval)\b",
    re.I,
)


def needs_outreach_style_clarification(text: str) -> bool:
    """Whether an otherwise ambiguous multi-recipient outreach needs a style choice."""
    request = text or ""
    has_multiple_recipients = has_multiple_outreach_recipients(request)
    return has_multiple_recipients and not _OUTREACH_STYLE.search(request)


def has_multiple_outreach_recipients(text: str) -> bool:
    """Whether a message requests outreach across more than one recipient."""
    request = text or ""
    if not _OUTREACH_ACTION.search(request):
        return False
    has_multiple_recipients = (
        len(_EMAIL_ADDRESS.findall(request)) > 1
        or _MULTIPLE_RECIPIENTS.search(request) is not None
    )
    return has_multiple_recipients


def explicitly_requests_email_send(text: str) -> bool:
    """Distinguish a send request from public email-address research."""
    request = text or ""
    return (
        (
            _EXPLICIT_SEND_ACTION.search(request) is not None
            or _EXPLICIT_EMAIL_APPROVAL_ACTION.search(request) is not None
        )
        and _NEGATED_EMAIL_ACTION.search(request) is None
    )


def _requests_live_data_lookup(text: str) -> bool:
    request = text or ""
    if not _LIVE_DATA_TOPIC.search(request):
        return False
    if _LIVE_TIME_WORD.search(request):
        return True
    current_year = datetime.now().year
    return any(int(match.group(1)) >= current_year for match in _YEAR.finditer(request))


# Email summary intent - matches requests to summarize unread emails
_EMAIL_SUMMARY = re.compile(
    r"\b(sum(?:arize|ise)(?: my)?\s+unread\s+emails?)"
    r"|\b(unread\s+emails?)"
    r"|\b(check\s+my\s+unread\s+emails?)"
    r"|\b(pending\s+emails?)"
    r"|\b(inbox\s+summar(?:iz|ise))"
    r"|\b(summar(?:ize|ise)\s+my\s+(?:email|message)s?)"
    r"|\b(summar(?:ize|ise)\s+my\s+inbox)"
    r"|\b(inbox\s+summary)"
    r"|\b(summary\s+(?:of\s+)?my\s+(?:email|message)s?)"
    r"|\b(summary\s+of\s+unread\s+emails?)",
    re.I,
)

# Explicit outbound communication must remain an actionable task even when
# its message mentions a job application or another research-like topic.  The
# job classifier below intentionally routes career questions to research, but
# it must not prevent a user-authorized call or message from reaching the
# approval-gated communications tools.
_COMMS_ACTION = re.compile(
    r"\b(call|dial|whatsapp)\b",
    re.I,
)
_TRANSIT_LOOKUP = re.compile(
    r"\b(?:bus(?:es)?|coach(?:es)?|matatu(?:s)?|train(?:s)?|rail|ferry|transit)\b"
    r".{0,100}\b(?:routes?|operators?|schedules?|timetables?|durations?|"
    r"travel times?|how long|departures?)\b"
    r"|\b(?:routes?|operators?|schedules?|timetables?|durations?|travel times?|"
    r"how long|departures?)\b.{0,100}"
    r"\b(?:bus(?:es)?|coach(?:es)?|matatu(?:s)?|train(?:s)?|rail|ferry|transit)\b",
    re.I,
)
_TRANSIT_BOOKING = re.compile(
    r"\b(?:book|reserve|buy|purchase|ticket)\b.{0,60}"
    r"\b(?:bus(?:es)?|coach(?:es)?|matatu(?:s)?|seat|ticket(?:s)?)\b"
    r"|\b(?:bus(?:es)?|coach(?:es)?|matatu(?:s)?|seat|ticket(?:s)?)\b.{0,60}"
    r"\b(?:book|reserve|buy|purchase)\b",
    re.I,
)
_EXPLICIT_REMINDER_ACTION = re.compile(
    r"\b(?:remind(?:er|ing)?|calendar|appointment|wake me up|set an alarm)\b",
    re.I,
)

# Public-profile requests are research even when they contain generic action
# words such as "check" or "find".  Keep this deliberately phrase-based so
# private account/file checks are not reclassified as web research.
_PERSON_RESEARCH = re.compile(
    r"\b(?:check|search|find|tell\s+me)\s+(?:everything|all\s+the\s+information|"
    r"information|info|details|facts)\s+about\b"
    r"|\bprofile\s+of\b|\bbackground\s+on\b|\bpublic\s+record\s+of\b",
    re.I,
)

# Email address pattern for extracting recipients from natural language
_EMAIL_ADDR = re.compile(
    r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b",
)

# Job/career intent signals. These are checked before the main priority loop
# so that messages about jobs, internships, CVs, and applications always route
# to ``research`` — regardless of which tech-stack nouns they mention.  This
# prevents the coding classifier from hijacking career requests that mention
# "Python", "React", etc. (see _CODING regex below).
_JOB = re.compile(
    r"\b(job|jobs|vacancy|vacancies|internship|internships?|career|careers|"
    r"recruit|recruitment|hiring|position|positions|opening|openings|"
    r"apply\s+(?:for|to|with|at)|application|applications|resume|cv\b|curriculum vitae|"
    r"software develop|entry.?level|fresh(?:er|man)|graduate|"
    r"tailor.?ed.*?cv|custom.*?cv|cv.*?template)\b",
    re.I,
)
_CODING = re.compile(
    r"```|\b(debug|traceback|stack trace|compiler|compile|regex|refactor|"
    r"function|syntaxerror|typeerror|nameerror|segmentation fault|npm|pip install|"
    r"git (?:init|commit|push|clone)|pull request|unit test|lint|typecheck|"
    r"sql query|api endpoint)\b"
    r"|\.(py|js|ts|tsx|jsx|go|rs|java|cpp)\b",
    re.I,
)
_RESEARCH = re.compile(
    r"(https?://\S+)"
    r"|\b(research|investigate|latest|breaking|current(?:ly)?|news|compare|"
    r"comparison|reviews?|look up|find out|search (?:the )?web|sources?|"
    r"statistics|data on|price|prices|rates?|forecast|who won|how many people|"
    r"everything about|learn about|profile of|background on|public record of|"
    r"studies? show|evidence|claims?|rumou?rs?|allegations?|assertions?)\b",
    re.I,
)
_LIVE_DATA_TOPIC = re.compile(
    r"\b(?:tolls?|toll\s+charges?|fees?|charges?|tariffs?|lending\s+rates?|"
    r"interest\s+rates?|borrowing\s+rates?|central\s+bank\s+rates?|cbk\s+rates?|"
    r"exchange\s+rates?|stock\s+prices?|dividend\s+yields?|market\s+indices|"
    r"inflation)\b",
    re.I,
)
_LOAN_PRODUCT = re.compile(r"\b(?:loan|mortgage|financing)\b", re.I)
_PERCENTAGE_VALUE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_FINANCIAL_CALCULATION = re.compile(
    r"\b(?:calculat\w*|comput\w*|model|amortiz\w*)\b",
    re.I,
)
_EXPLICIT_RESEARCH_ACTION = re.compile(
    r"\b(?:research|investigate|look up|find out|search (?:the )?web|"
    r"find information|compare)\b",
    re.I,
)
_LIVE_TIME_WORD = re.compile(
    r"\b(?:current|currently|latest|today|now|live)\b|\bas\s+of\s+20\d{2}\b",
    re.I,
)
_KENYAN_CURRICULUM = re.compile(
    r"\b(?:kenya|kenyan)\b.{0,80}\b(?:curricul\w*|syllab\w*|"
    r"education\s+system|learning\s+outcomes?)\b"
    r"|\b(?:curricul\w*|syllab\w*|learning\s+outcomes?)\b.{0,80}"
    r"\b(?:kenya|kenyan|KICD|KNEC|KCSE|CBC|CBE)\b"
    r"|\b(?:KICD|KNEC|KCSE)\b",
    re.I,
)
_YEAR = re.compile(
    r"\b(20\d{2})\b",
    re.I,
)
_TASK = re.compile(
    r"\b(write|draft|create|build|generate|make|prepare|compose|summariz(?:e|ing)?"
    r"|translate|design|plan|organize|send|email|post|blog|report|proposal|"
    r"essay|outline|checklist|check|list)\b",
    re.I,
)
_IMAGE_GENERATION = re.compile(
    r"\b(?:generate|create|make|draw|paint|render|illustrate|produce)\b"
    r"(?:\s+\w+){0,3}\s+"
    r"\b(?:images?|pictures?|photos?|photographs?|illustrations?|artworks?|"
    r"graphics?|drawings?)\b",
    re.I,
)
_IMAGE_GENERATION_HOW_TO = re.compile(
    r"^\s*(?:how|what)\b|^\s*(?:explain|show)\s+me\s+how\b",
    re.I,
)
_NEGATED_IMAGE_GENERATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|no\s+need\s+to|not\s+to)\b"
    r"(?:\s+\w+){0,3}\s+(?:generate|create|make|draw|paint|render|"
    r"illustrate|produce)\b",
    re.I,
)
_BIOMEDICAL_DOMAIN = re.compile(
    r"\b(?:hiv(?:-\d)?|aids|crispr|cas\d+[a-z]?|gene[- ]editing|gene therapy|"
    r"viral|virus|provirus|pathogen|immunotherapy|vaccine|oncolog\w*|"
    r"clinical trial|biomedical|therapeutic)\b",
    re.I,
)
_BIOMEDICAL_HYPOTHESIS = re.compile(
    r"\b(?:hypoth(?:esis|eses)|proposal|framework|mechanism|strategy|"
    r"intervention|treatment|therapy|cure|abstract)\b",
    re.I,
)


def _with_image_generation_signal(
    result: IntentResult, text: str
) -> IntentResult:
    if (
        _IMAGE_GENERATION.search(text or "") is None
        or _IMAGE_GENERATION_HOW_TO.search(text or "") is not None
        or _NEGATED_IMAGE_GENERATION.search(text or "") is not None
    ):
        return result
    signals = tuple(dict.fromkeys(("image_generation", *result.signals)))[:_MAX_SIGNALS]
    return IntentResult(
        intent="task" if result.intent == "chat" else result.intent,
        signals=signals,
    )
# Math signals (lowest priority — never shadows scheduling/coding/research/task).
# Operators with operands, equations, calculus/trig/stat verbs, nCr, exponents,
# and explicit unit conversions ("5 km to m").
_MATH = re.compile(
    # Require an operand before the operator.  Allowing whitespace here makes
    # a natural-language phone number such as "+254700000001" look like
    # arithmetic when it follows a word and a space.
    r"\b\d+\s*[+\-*/^%]\s*[\d\w\(]"
    r"|[a-zA-Z]\w*\s*=\s*[^\s=]"
    r"|\b(integrat(?:e|ion|ed)?|differentiat(?:e|ion|ed)?|derivativ(?:e|es)?|"
    r"integral|limit|lim\b|factor|expand|simplify|simplif(?:y|ies|ed|ying)?|"
    r"nCr|nPr|sin|cos|tan|log|sqrt|root|sigma|summation|mean|median|"
    r"variance|probability|circumference|radius|diameter)\b"
    r"|\b\d+\s*\^\s*\d+"
    r"|\b\d+\s*(km|cm|mm|m|kg|g|lb|oz|ft|in|mi)\s*(?:to|in)\s*"
    r"(km|cm|mm|m|kg|g|lb|ft|in|mi)\b",
    re.I,
)
# Science domains and distinctive concepts. Placed below research/task so
# "write an essay about chemistry" / "latest physics news" stays task/research,
# and above math so a domain question gets the science brief; a bare "2+2" has
# no science term → math. Ambiguous generic words such as base/current/
# pressure/mass are deliberately omitted to avoid mis-routing chit-chat.
_SCIENCE = re.compile(
    r"\b(physics|chemistry|chemical|chem|biology|science|scientific|"
    r"astronomy|astrophysics|cosmology|geology|geophysics|meteorology|"
    r"climatology|ecology|ecological|genetics|genomics|microbiology|"
    r"neuroscience|biochemistry|biotechnology|biomedical|environmental science|"
    r"earth science|space science|social science|computer science|data science|"
    r"materials science|oceanography|paleontology|evolutionary biology|"
    r"botany|zoology|anatomy|physiology|immunolog\w*|epidemiolog\w*)\b"
    r"|\b(atom|atomic|molecule|molecular|mole|molar|stoichiometr\w*|isotope|ion|"
    r"valence|oxidation|reduction|acid|alkali|periodic table|catalyst|"
    r"compound|reaction|electron|proton|neutron|nucleus|photosynthesis|"
    r"cell biology|plate tectonics|fossil|biodiversity|ecosystem|"
    r"black hole|exoplanet|stellar evolution|genome)\b"
    r"|\b(force|energy|kinetic|velocity|acceleration|momentum|gravity|"
    r"gravitational|newton|joule|watt|pascal|hertz|ohm|voltage|resistance|"
    r"circuit|resistor|capacitor|magnetic|photon|quantum|wavelength|frequency|"
    r"amplitude|refraction|refractive|density|torque|friction|inertia|"
    r"thermodynamic\w*|entropy|enthalpy|projectile|pendulum)\b"
    r"|\bH2O\b|\bCO2\b|\bO2\b",
    re.I,
)

_MAX_SIGNALS = 4

# Web-search trigger patterns: question phrases and direct search commands that
# indicate a factual, answer-seeking query (best answered by a web search) rather
# than a deep-research project.  These are applied as a *post-processing* step
# on the research/chat intents so they never shadow science/math/coding/task
# classification.
_WEB_SEARCH_TRIGGERS = re.compile(
    r"\b(what (?:is|are|was|were|does|do|did))\b"
    r"|\b(how (?:to|do|does|did|can))\b"
    r"|\b(who (?:is|was|won))\b"
    r"|\b(why (?:does|is|did))\b"
    r"|\b(when (?:is|was|did))\b"
    r"|\b(where (?:is|are|can))\b"
    r"|\b(search (?:the )?web|google|look up|find out|find information|find the)\b"
    r"|\b(?:confirm|verify|fact[- ]?check)\b.{0,80}"
    r"\b(?:claims?|rumou?rs?|allegations?|assertions?)\b"
    r"|\b(?:mess(?:a|e)ge|signal|transmission)\s+(?:from|to)\s+(?:earth|earthlings)\b"
    r"|\b(tell me about|tell me the)\b",
    re.I,
)
_DIRECT_SEARCH_QUERY = re.compile(
    r"^\s*(?:please\s+)?search\s+(?:(?:about|for)\s+)?"
    r"[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,5}\s*[?.!]*$",
    re.I,
)

# Deep-research markers: when any of these appear alongside a web-search trigger,
# the request is about investigation/synthesis (not a quick search) and stays
# "research" instead of being collapsed to "web_search".
_DEEP_RESEARCH = re.compile(
    r"\b(research|investigate|investigation|evidence|statistics|studies?|"
    r"comparison|compare|reviews?|forecast|analysis|analyse)\b",
    re.I,
)


def classify_intent(text: str) -> IntentResult:
    """Classify a user message.

    Priority: scheduling > job(research) > email > research > coding > task >
    science > math > chat, followed by a post-processing step that collapses
    research/chat intents to ``web_search`` when the user phrased a question or
    search command but no deep-research markers are present.

    Job/career requests are short-circuited to research before the main loop
    so that messages about jobs, internships, CVs, and applications always route
    to ``research`` — regardless of which tech-stack nouns they mention.  This
    prevents the coding classifier from hijacking career requests that mention
    "Python", "React", etc. (see _CODING regex below).

    The email intent is checked before research/task so that "send an email",
    "email my boss", etc. route to the dedicated email toolset.  Coding is
    checked before task so that "write a Python function" (which contains
    coding action signals like "function") wins over the generic "write"
    task keyword — while "write a draft proposal" (no coding signals) stays task.

    The web_search reclassification runs only on research/chat results: a
    question-style query ("what is…", "how to…") without deep-research markers
    ("research", "investigate", "evidence", "studies", "statistics",
    "comparison", "reviews", "forecast") is classified as ``web_search`` so the
    model gets a focused browsing brief instead of a full research brief.
    """
    if (
        _TRANSIT_BOOKING.search(text or "")
        and not _EXPLICIT_REMINDER_ACTION.search(text or "")
        and not _EMAIL.search(text or "")
        and not _COMMS_ACTION.search(text or "")
    ):
        return _with_image_generation_signal(
            IntentResult(
                intent="task",
                signals=("regional_transit", "bus_booking"),
            ),
            text or "",
        )

    if (
        _TRANSIT_LOOKUP.search(text or "")
        and not _EXPLICIT_REMINDER_ACTION.search(text or "")
        and not _EMAIL.search(text or "")
        and not _COMMS_ACTION.search(text or "")
    ):
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=("regional_transit",)),
            text or "",
        )

    # Job/career requests short-circuit to research intent so the model
    # gets RESEARCH_BRIEF and access to job-search tools (search_jobs,
    # generate_cv_for_job, etc.) instead of the coding workspace brief.
    # A scheduled communication is still a scheduling request.  Checking the
    # communication action first classified phrases such as "remind me to call
    # mom at 5pm" as a generic task, which removed the scheduling brief and
    # calendar/reminder tools from the turn.  Keep the precedence general:
    # explicit scheduling markers win, while an immediate call/message remains
    # an actionable communications task.
    if _SCHEDULING.search(text or ""):
        matches = _SCHEDULING.findall(text or "")
        signals = tuple(dict.fromkeys(_flatten(matches)))[:_MAX_SIGNALS]
        return _with_image_generation_signal(
            IntentResult(intent="scheduling", signals=signals), text or ""
        )

    # Outreach requirements often describe a "call to action"; that phrase
    # must not route an organization email lookup to phone-call tools.
    if (
        _PUBLIC_CONTACT_LOOKUP.search(text or "")
        or _ORGANIZATION_EMAIL_LOOKUP.search(text or "")
    ):
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=("public_contact_search",)),
            text or "",
        )

    comms_matches = _COMMS_ACTION.findall(text or "")
    if comms_matches:
        signals = tuple(dict.fromkeys(_flatten(comms_matches)))[:_MAX_SIGNALS]
        return _with_image_generation_signal(
            IntentResult(intent="task", signals=signals), text or ""
        )

    if _requests_live_data_lookup(text or ""):
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=("live_data_lookup",)),
            text or "",
        )

    if _KENYAN_CURRICULUM.search(text or ""):
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=("kenyan_curriculum",)),
            text or "",
        )

    # A numeric rate supplied alongside an explicit loan calculation is an
    # input, not a request to research that rate. Keep explicit research
    # requests and live-rate lookups on the evidence-backed research route.
    if (
        _LOAN_PRODUCT.search(text or "")
        and _PERCENTAGE_VALUE.search(text or "")
        and _FINANCIAL_CALCULATION.search(text or "")
        and not _EXPLICIT_RESEARCH_ACTION.search(text or "")
    ):
        return _with_image_generation_signal(
            IntentResult(intent="math", signals=("financial_calculation",)),
            text or "",
        )

    if _PERSON_RESEARCH.search(text or ""):
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=("person_search",)), text or ""
        )

    job_matches = _JOB.findall(text or "")
    if job_matches:
        signals = tuple(dict.fromkeys(_flatten(job_matches)))[:_MAX_SIGNALS]
        return _with_image_generation_signal(
            IntentResult(intent="research", signals=signals), text or ""
        )

    result = IntentResult(intent="chat")
    for intent, pattern in (
        ("scheduling", _SCHEDULING),
        ("email", _EMAIL),
        ("research", _RESEARCH),
        ("coding", _CODING),
        ("task", _TASK),
        ("science", _SCIENCE),
        ("math", _MATH),
    ):
        if intent == "email" and _NEGATED_EMAIL_ACTION.search(text or ""):
            continue
        matches = pattern.findall(text or "")
        if matches:
            signals = tuple(dict.fromkeys(_flatten(matches)))[:_MAX_SIGNALS]
            result = IntentResult(intent=intent, signals=signals)
            break

    if (
        _BIOMEDICAL_DOMAIN.search(text or "")
        and _BIOMEDICAL_HYPOTHESIS.search(text or "")
    ):
        signals = tuple(
            dict.fromkeys(("biomedical_review", *result.signals))
        )[:_MAX_SIGNALS]
        result = IntentResult(
            intent=(
                "research"
                if result.intent in {"chat", "science", "task"}
                else result.intent
            ),
            signals=signals,
        )

    result = _with_image_generation_signal(result, text or "")
    image_generation_requested = "image_generation" in result.signals

    # Post-processing: collapse research/chat → web_search for question-style
    # or direct-search queries that lack deep-research markers.  This runs only
    # on research/chat results — science/math/coding/task intents (returned
    # above) are never collapsed.
    if result.intent in ("research", "chat"):
        if (
            (
                _WEB_SEARCH_TRIGGERS.search(text or "")
                or _DIRECT_SEARCH_QUERY.fullmatch(text or "")
            )
            and not _DEEP_RESEARCH.search(text or "")
        ):
            signals = ("web_search",)
            if image_generation_requested:
                signals += ("image_generation",)
            return IntentResult(intent="web_search", signals=signals)
    return result


def extract_email_address(text: str) -> str | None:
    """Extract an email address from natural language text.
    
    Returns the first valid email address found, or None if not found.
    """
    match = _EMAIL_ADDR.search(text or "")
    return match.group(1) if match else None


def extract_person_search_subject(text: str) -> str | None:
    """Extract the public-profile subject from a person-research phrase."""
    match = re.search(
        r"(?:everything|all\s+the\s+information|information|info|details|facts)\s+about\s+"
        r"(.+?)(?:[?.!]|$)|(?:profile\s+of|background\s+on|public\s+record\s+of)\s+"
        r"(.+?)(?:[?.!]|$)",
        text or "", re.I,
    )
    if not match:
        return None
    return " ".join(next((g for g in match.groups() if g), "").split()).strip() or None


def extract_email_parameters(text: str) -> dict:
    """Extract email parameters (to, subject, body) from a natural language request.
    
    Returns a dict with keys: to, subject, body, confidence.
    confidence is 0-1 indicating how certain the extraction is.
    """
    import re
    
    result = {"to": None, "subject": "", "body": "", "confidence": 0.0}
    
    # Extract recipient email
    to_addr = extract_email_address(text)
    result["to"] = to_addr
    
    # Check for email-related keywords
    email_keywords = re.search(r"\b(send\s+)?email\b", text, re.I)
    
    if to_addr and email_keywords:
        result["confidence"] = 0.9
    elif to_addr:
        result["confidence"] = 0.7
    else:
        result["confidence"] = 0.0
    
    return result


def _flatten(matches: list) -> list[str]:
    out: list[str] = []
    for m in matches:
        if isinstance(m, tuple):
            out.extend(g for g in m if g)
        elif m:
            out.append(m)
    return out
