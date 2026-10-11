"""Profile management: onboarding Q&A, load/save to ~/.higor/profile.toml."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import tomli_w
import tomllib

from higor_agent import paths
from higor_agent.models import SearchError, UserProfile

logger = logging.getLogger(__name__)

# Semantic Scholar's field-of-study taxonomy (used as the ``fieldsOfStudy`` filter).
S2_FIELDS = (
    "Computer Science", "Medicine", "Chemistry", "Biology", "Materials Science", "Physics",
    "Geology", "Psychology", "Art", "History", "Geography", "Sociology", "Business",
    "Political Science", "Economics", "Philosophy", "Mathematics", "Engineering",
    "Environmental Science", "Agricultural and Food Sciences", "Education", "Law", "Linguistics",
)


def load_profile() -> UserProfile:
    """Load the profile from disk. Returns a blank default if none exists."""
    path = paths.profile_path()
    if not path.exists():
        return UserProfile()
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    identity = data.get("identity", {})
    topics = data.get("topics", {})
    journals = data.get("journals", {})
    sources = data.get("sources", {})
    search = data.get("search", {})
    # Older profiles used different key names for the followed-journal list.
    prominent = journals.get("prominent", journals.get("high_impact", journals.get("frequent", [])))

    return UserProfile(
        name=identity.get("name", ""),
        semantic_scholar_author_id=identity.get("semantic_scholar_author_id") or None,
        research_interests=topics.get("research_interests", ""),
        primary_topics=topics.get("primary", []),
        secondary_topics=topics.get("secondary", []),
        prominent_journals=prominent,
        fields_of_study=sources.get("fields_of_study", []),
        arxiv_categories=sources.get("arxiv_categories", []),
        own_paper_ids=search.get("own_paper_ids", []),
        own_paper_titles=search.get("own_paper_titles", {}),
        max_papers_per_week=search.get("max_papers_per_week", 15),
        lookback_days=search.get("lookback_days", 7),
        impact_weight=search.get("impact_weight", 0.25),
    )


def save_profile(profile: UserProfile) -> None:
    """Persist the profile as human-readable TOML."""
    data: dict[str, Any] = {
        "identity": {
            "name": profile.name,
            "semantic_scholar_author_id": profile.semantic_scholar_author_id or "",
        },
        "topics": {
            "research_interests": profile.research_interests,
            "primary": profile.primary_topics,
            "secondary": profile.secondary_topics,
        },
        "journals": {"prominent": profile.prominent_journals},
        "sources": {
            "fields_of_study": profile.fields_of_study,
            "arxiv_categories": profile.arxiv_categories,
        },
        "search": {
            "own_paper_ids": profile.own_paper_ids,
            "own_paper_titles": profile.own_paper_titles,
            "max_papers_per_week": profile.max_papers_per_week,
            "lookback_days": profile.lookback_days,
            "impact_weight": profile.impact_weight,
        },
    }
    paths.higor_dir()
    with paths.profile_path().open("wb") as fh:
        tomli_w.dump(data, fh)
    logger.debug("Profile saved to %s.", paths.profile_path())


def describe_profile(profile: UserProfile) -> str:
    """Plain-text rendering of the profile for prompts."""
    lines = []
    if profile.name:
        lines.append(f"Name: {profile.name}")
    if profile.research_interests:
        lines.append(f"Research interests: {profile.research_interests}")
    lines.append(f"Primary topics: {', '.join(profile.primary_topics) or '-'}")
    lines.append(f"Secondary topics: {', '.join(profile.secondary_topics) or '-'}")
    lines.append(f"Followed journals: {', '.join(profile.prominent_journals) or '-'}")
    lines.append(f"Fields of study (S2 filter): {', '.join(profile.fields_of_study) or '-'}")
    lines.append(f"arXiv categories: {', '.join(profile.arxiv_categories) or '-'}")
    if profile.own_paper_titles:
        titles = list(profile.own_paper_titles.values())[:8]
        lines.append("Own papers (used for citation/recommendation sweeps): " + "; ".join(titles))
    lines.append(
        f"Digest size: {profile.max_papers_per_week} papers, lookback {profile.lookback_days} days, "
        f"impact weight {profile.impact_weight}"
    )
    return "\n".join(lines)


# ── Onboarding ────────────────────────────────────────────────────────────────

def _ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    value = input(display).strip()
    return value or default


def _ask_list(prompt: str, default: list[str] | None = None) -> list[str]:
    display = f"{prompt} (comma-separated)"
    if default:
        display += f" [{', '.join(default)}]"
    raw = input(display + ": ").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ask_int(prompt: str, default: int) -> int:
    try:
        return int(_ask(prompt, str(default)))
    except ValueError:
        return default


def _offer_suggestions(suggested: list[str], existing: list[str], label: str) -> list[str]:
    """Show S2-derived suggestions and let the user pick by number or name."""
    novel = [t for t in suggested if t not in existing][:10]
    if not novel:
        return list(existing)
    print(f"\nBased on your published work, Semantic Scholar suggests these {label}:")
    for i, t in enumerate(novel, 1):
        print(f"  {i}. {t}")
    raw = input("Add any? Enter numbers (e.g. 1,3) or names, or press Enter to skip: ").strip()
    merged = list(existing)
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        pick = novel[int(token) - 1] if token.isdigit() and 0 < int(token) <= len(novel) else token
        if pick not in merged:
            merged.append(pick)
    return merged


def run_onboarding(existing: UserProfile | None = None) -> UserProfile:
    """Interactive Q&A that produces a UserProfile and writes it to disk."""
    ex = existing or UserProfile()
    if existing is not None:
        print("\nEditing your research profile. Press Enter to keep the current value.\n")
    else:
        print("\nWelcome to higor-agent! Let's build your research profile.\n")

    name = _ask("Your name", ex.name)
    s2_author_id = _ask(
        "Semantic Scholar author ID (find it at semanticscholar.org — optional)",
        ex.semantic_scholar_author_id or "",
    )

    own_paper_ids = list(ex.own_paper_ids)
    own_paper_titles = dict(ex.own_paper_titles)
    s2_fields: list[str] = []
    if s2_author_id and (s2_author_id != (ex.semantic_scholar_author_id or "") or not own_paper_ids):
        print(f"\nFetching your papers from Semantic Scholar (ID: {s2_author_id})…")
        try:
            from higor_agent.search import fetch_author_papers_and_topics

            pairs, s2_fields = asyncio.run(fetch_author_papers_and_topics(s2_author_id))
            own_paper_ids = [pid for pid, _ in pairs]
            own_paper_titles = dict(pairs)
            print(f"Found {len(own_paper_ids)} papers to use as citation and recommendation seeds.")
        except SearchError as exc:
            print(f"Warning: could not fetch papers ({exc}). Citation sweeps will be skipped.")

    research_interests = _ask(
        "Describe your research interests in a few sentences (optional)", ex.research_interests
    )
    primary = _ask_list("Primary research topics", ex.primary_topics or None)
    secondary = _ask_list("Secondary / adjacent topics (optional)", ex.secondary_topics or None)
    journals = _ask_list("Journals you follow closely (optional)", ex.prominent_journals or None)

    print(f"\nSemantic Scholar fields of study (used to filter noise): {', '.join(S2_FIELDS)}")
    fields = _ask_list("Your fields of study", ex.fields_of_study or s2_fields[:3] or None)
    fields = [f for f in fields if f in S2_FIELDS] or fields
    arxiv_cats = _ask_list(
        "arXiv categories to sweep (e.g. cond-mat.mtrl-sci, physics.comp-ph; optional)",
        ex.arxiv_categories or None,
    )

    max_papers = _ask_int("Papers per digest", ex.max_papers_per_week)
    lookback = _ask_int("How many days back to search", ex.lookback_days)

    profile = UserProfile(
        name=name,
        semantic_scholar_author_id=s2_author_id or None,
        research_interests=research_interests,
        primary_topics=primary,
        secondary_topics=secondary,
        prominent_journals=journals,
        fields_of_study=fields,
        arxiv_categories=arxiv_cats,
        own_paper_ids=own_paper_ids,
        own_paper_titles=own_paper_titles,
        max_papers_per_week=max_papers,
        lookback_days=lookback,
        impact_weight=ex.impact_weight,
    )
    save_profile(profile)
    print(f"\nProfile saved to {paths.profile_path()}\n")
    return profile
