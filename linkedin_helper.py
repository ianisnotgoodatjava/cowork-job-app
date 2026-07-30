"""Deliberately does NOT scrape or automate LinkedIn — that violates their
Terms of Service and risks your account. This just builds a search URL you
open and browse yourself; you decide who to log as a contact."""

from urllib.parse import quote_plus


def people_search_url(company: str, extra_keywords: str = "recruiter OR talent OR hiring") -> str:
    keywords = f"{company} {extra_keywords}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(keywords)}"
