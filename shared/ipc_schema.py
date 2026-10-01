"""Pydantic schemas for inter-agent communication between repo-agent and scraper-agent."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class RecommendationItem(BaseModel):
    file_path: str = Field(..., description="Target repository file path to patch")
    diff_type: Literal["replace", "insert", "delete"] = Field(
        "replace", description="Modification strategy"
    )
    target_line: str = Field(..., description="Exact string pattern to match/replace")
    replacement: str = Field(..., description="Replacement content")
    summary: str = Field(..., description="Human-readable rationale for the patch")


class ResearchRequest(BaseModel):
    repo: str = Field(..., description="Owner/repo slug, e.g. FreeFades2Black/omarchy-gitops-forge")
    query: str = Field(..., description="Target query or deprecation search topic")
    workflow_files: Optional[List[str]] = Field(
        default=None, description="Current workflow filenames or paths"
    )


class ResearchResponse(BaseModel):
    status: Literal["success", "no_updates", "error"]
    repo: str
    recommendations: List[RecommendationItem] = Field(default_factory=list)
    message: Optional[str] = None
