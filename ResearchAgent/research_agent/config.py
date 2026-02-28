from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_KEYWORDS = [
    "rlhf",
    "ppo",
    "dpo",
    "grpo",
    "reward modeling",
    "preference optimization",
    "moe",
    "mixture of experts",
    "distributed training",
    "llm infrastructure",
    "reinforcement learning",
    "alignment",
    "inference engine",
    "serving",
    "deepspeed",
    "trl",
    "vllm",
]

DEFAULT_GITHUB_REPOS = [
    "vllm-project/vllm",
    "huggingface/trl",
    "microsoft/DeepSpeed",
]


@dataclass(slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    logs_dir: Path
    prompt_path: Path
    gemini_api_key: str | None
    github_token: str | None
    host: str = "127.0.0.1"
    port: int = 8000
    schedule_time: str = "08:30"
    max_arxiv_results: int = 20
    gemini_model: str = "gemini-3-flash-preview"
    secondary_filter_model: str = "gemini-2.5-flash"
    enable_llm_filter: bool = False
    tracked_github_repos: list[str] = field(default_factory=lambda: list(DEFAULT_GITHUB_REPOS))
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or Path(__file__).resolve().parents[1]
        data_dir = Path(os.getenv("RESEARCH_AGENT_DATA_DIR", root / "data")).expanduser()
        logs_dir = root / "logs"
        return cls(
            project_root=root,
            data_dir=data_dir,
            logs_dir=logs_dir,
            prompt_path=root / "prompts" / "gemini_system_prompt.txt",
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            github_token=os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"),
            host=os.getenv("RESEARCH_AGENT_HOST", "127.0.0.1"),
            port=int(os.getenv("RESEARCH_AGENT_PORT", "8000")),
            schedule_time=os.getenv("RESEARCH_AGENT_SCHEDULE_TIME", "08:30"),
            max_arxiv_results=int(os.getenv("RESEARCH_AGENT_MAX_ARXIV_RESULTS", "20")),
            gemini_model=os.getenv("RESEARCH_AGENT_GEMINI_MODEL", "gemini-3-flash-preview"),
            secondary_filter_model=os.getenv("RESEARCH_AGENT_GEMINI_FILTER_MODEL", "gemini-2.5-flash"),
            enable_llm_filter=os.getenv("RESEARCH_AGENT_ENABLE_LLM_FILTER", "false").lower() in {"1", "true", "yes"},
        )

    def load_gemini_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")
