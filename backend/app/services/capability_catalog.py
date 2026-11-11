"""User-facing supported-scope cards derived from the shared tool registry."""
from __future__ import annotations

from typing import Any

from app.tools import registry

_CARDS = (
    {
        "id": "web_research",
        "title": "Web research and browser",
        "categories": ["research", "browser"],
        "scope": "Search public web sources, open pages, and interact with browser pages.",
        "limitations": [
            "Search results and page content are untrusted; citations identify sources, not verified truth.",
            "Live search and browser behavior depend on configured providers and are not certified by local tests.",
        ],
        "tests": [
            "tests/test_web_p6.py",
            "tests/test_browser_phase_c.py",
            "tests/test_agentic_hardening.py",
        ],
    },
    {
        "id": "email",
        "title": "Email",
        "categories": ["email"],
        "scope": "Search and read supported mailboxes, prepare drafts, and send through approval-gated actions.",
        "limitations": [
            "A prepared draft or provider-accepted request is not proof of delivery.",
            "Live mailbox access and delivery require a connected provider; mock tests do not verify external delivery.",
        ],
        "tests": [
            "tests/test_phase_e_email.py",
            "tests/test_email_inbound.py",
            "tests/test_approvals.py",
        ],
    },
    {
        "id": "messaging_and_calls",
        "title": "SMS, WhatsApp, and calls",
        "categories": ["comms"],
        "scope": "Send SMS or WhatsApp messages and place outbound calls through configured communications providers.",
        "limitations": [
            "Only configured supported providers and accounts are usable; personal message-history access is not provided.",
            "External sends and calls require approval; a queued receipt does not establish delivery or call completion.",
        ],
        "tests": ["tests/test_comms.py", "tests/test_phase_f_voice.py"],
    },
    {
        "id": "contacts",
        "title": "Contacts",
        "categories": ["contacts"],
        "scope": "Look up and manage the authenticated user's saved contacts.",
        "limitations": [
            "Contact records are user-scoped and do not imply access to a device address book.",
            "Phone-number lookup must resolve unambiguously before a communication action.",
        ],
        "tests": ["tests/test_people.py", "tests/test_comms.py"],
    },
    {
        "id": "files_and_documents",
        "title": "Files and documents",
        "categories": ["documents", "files"],
        "scope": "Process, search, read, and generate supported user-owned documents; destructive deletion is approval-gated.",
        "limitations": [
            "Supported file formats and extraction depend on installed parsers.",
            "Generated files are structurally validated, but users should review their contents before relying on them.",
        ],
        "tests": [
            "tests/test_documents.py",
            "tests/test_phase_d_generation.py",
            "tests/test_phase_d_media.py",
        ],
    },
    {
        "id": "calendar_and_reminders",
        "title": "Calendar and reminders",
        "categories": ["calendar", "scheduling"],
        "scope": "Read calendar events, prepare approval-gated calendar changes, and create tracked reminders or scheduled tasks.",
        "limitations": [
            "Provider support, timezone, recurrence, and delivery vary by configured integration.",
            "A reminder notification is distinct from an action task and does not prove an external action occurred.",
        ],
        "tests": ["tests/test_oauth_calendar.py", "tests/test_phase_g_schedules.py"],
    },
    {
        "id": "device_media",
        "title": "Device media and YouTube",
        "categories": ["device_control", "media"],
        "scope": "Use supported local/paired-device controls and analyze user-provided image, audio, or video files.",
        "limitations": [
            "YouTube support is limited to available device/player controls; it does not provide unrestricted account or viewing-history access.",
            "Media analysis requires a compatible configured model or transcription/video engine; unavailable engines fail explicitly.",
        ],
        "tests": [
            "tests/test_device_tools.py",
            "tests/test_device_bridge.py",
            "tests/test_phase_d_media.py",
        ],
    },
    {
        "id": "cloud_drive",
        "title": "Cloud drive",
        "categories": ["drive"],
        "scope": "Search, read, create, or upload files through supported connected drive providers.",
        "limitations": [
            "Provider availability and file permissions are account-specific.",
            "Local mock/provider tests do not establish live cloud-provider access.",
        ],
        "tests": ["tests/test_phase_i_integrations.py"],
    },
    {
        "id": "maps",
        "title": "Maps and places",
        "categories": ["maps"],
        "scope": "Find places, nearby results, directions, and distances through the configured maps provider.",
        "limitations": [
            "Coverage and freshness depend on the provider; results are not a guarantee of current road or business conditions.",
        ],
        "tests": ["tests/test_phase_i_integrations.py"],
    },
    {
        "id": "weather",
        "title": "Weather",
        "categories": ["weather"],
        "scope": "Retrieve provider-supplied weather information for supported locations.",
        "limitations": [
            "Forecast freshness and coverage depend on the configured provider; the assistant must disclose unavailable live data.",
        ],
        "tests": ["tests/test_phase_i_integrations.py"],
    },
    {
        "id": "translation",
        "title": "Translation",
        "categories": ["translate"],
        "scope": "Translate supplied text using the configured translation capability.",
        "limitations": [
            "Translation output may require human review for legal, medical, or other high-stakes use.",
        ],
        "tests": ["tests/test_phase_i_integrations.py"],
    },
    {
        "id": "travel",
        "title": "Travel search and booking",
        "categories": ["travel"],
        "scope": "Search supported transport and lodging options; booking actions use shared approval policy.",
        "limitations": [
            "Live inventory, prices, and booking confirmation depend on provider integration; a search result is not a reservation.",
        ],
        "tests": ["tests/test_payments_travel.py", "tests/test_hotel_tools.py"],
    },
    {
        "id": "payments",
        "title": "Payments",
        "categories": ["payments"],
        "scope": "Initiate supported payment actions only through configured providers and explicit approval.",
        "limitations": [
            "No payment is claimed complete without provider evidence; live financial transactions are not verified by mock tests.",
        ],
        "tests": ["tests/test_payments_travel.py"],
    },
    {
        "id": "jobs",
        "title": "Job search and applications",
        "categories": ["jobs"],
        "scope": "Search job listings and prepare application materials; submission actions follow approval policy.",
        "limitations": [
            "Listings may be stale; application submission depends on supported provider flows and is not implied by drafting.",
        ],
        "tests": ["tests/test_job_tools.py"],
    },
    {
        "id": "creative",
        "title": "Creative generation",
        "categories": ["creative"],
        "scope": "Generate or edit supported creative media using configured generation providers.",
        "limitations": [
            "Availability and output quality depend on the configured model/provider; generated output is not independently factual verification.",
        ],
        "tests": ["tests/test_phase_d_generation.py", "tests/test_content_creation.py"],
    },
    {
        "id": "coding_workspace",
        "title": "Coding and workspace",
        "categories": ["coding", "workspace"],
        "scope": "Inspect and edit user-authorized workspaces; external coding agents stage proposals for review before writes.",
        "limitations": [
            "Command execution and project writes remain policy-gated and scoped; live Copilot/Codex credentials are not assumed.",
        ],
        "tests": ["tests/test_phase_j_coding.py", "tests/test_workspace_projects.py"],
    },
    {
        "id": "math",
        "title": "Math and charts",
        "categories": ["math"],
        "scope": "Calculate and verify supported expressions and generate data charts.",
        "limitations": [
            "Unsupported or ambiguous expressions fail validation instead of being treated as verified calculations.",
        ],
        "tests": ["tests/test_math.py", "tests/test_capability_acceptance.py"],
    },
)


def capability_catalog() -> list[dict[str, Any]]:
    """Return registered operations with policy and test-boundary disclosures."""
    specs_by_category: dict[str, list[Any]] = {}
    for spec in registry.all():
        specs_by_category.setdefault(spec.category, []).append(spec)

    cards: list[dict[str, Any]] = []
    for card in _CARDS:
        specs = sorted(
            (
                spec
                for category in card["categories"]
                for spec in specs_by_category.get(category, [])
            ),
            key=lambda spec: spec.name,
        )
        if not specs:
            continue
        cards.append({
            **card,
            "status": "registered",
            "live_provider_verified": False,
            "tools": [
                {
                    "name": spec.name,
                    "permission_level": int(spec.permission),
                    "permission_name": spec.permission.name.lower(),
                    "approval_required_by_default": int(spec.permission) >= 3,
                    "side_effect": spec.side_effect,
                    "input_fields": sorted(
                        spec.tool.args_schema.model_json_schema()
                        .get("properties", {})
                    ),
                }
                for spec in specs
            ],
        })
    return cards
