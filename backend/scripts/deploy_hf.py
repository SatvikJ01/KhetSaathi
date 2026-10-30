"""Deploy the whole app (Dockerfile at repo root) to a Hugging Face Docker Space.

- creates the Space if needed (public, Docker SDK)
- copies API keys from backend/.env into Space *secrets* (never into the repo)
- uploads only the files the Docker build needs, plus a Space README with config

Usage (from backend/):  .venv/bin/python -m scripts.deploy_hf [--space-name KhetSaathi]
Needs HF_TOKEN (write) in backend/.env and `pip install huggingface_hub`.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
SECRET_KEYS = ["GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATA_GOV_API_KEY", "SARVAM_API_KEY"]
VARIABLE_KEYS = ["LLM_PROVIDER", "LLM_MODEL"]
ALLOW = ["Dockerfile", ".dockerignore", "backend/**", "frontend/**"]
IGNORE = ["**/.env", "**/.venv/**", "**/node_modules/**", "frontend/dist/**", "**/.cache/**",
          "**/__pycache__/**", "**/.pytest_cache/**", "backend/tests/**"]

SPACE_README = """---
title: KhetSaathi
emoji: 🌾
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Field-level, verifiable multilingual farm copilot
---

# KhetSaathi

Field-level farm advisor: live Sentinel-2 NDVI, weather, soil and mandi prices plus official ICAR/MPKV
guides, reasoned over by a LangGraph agent whose every number is verified against the data.

Source code: https://github.com/SatvikJ01/KhetSaathi
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space-name", default="KhetSaathi")
    args = parser.parse_args()

    env = dotenv_values(ROOT / "backend" / ".env")
    api = HfApi(token=env["HF_TOKEN"])
    user = api.whoami()["name"]
    repo_id = f"{user}/{args.space_name}"

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    for key in SECRET_KEYS:
        if env.get(key):
            api.add_space_secret(repo_id, key, env[key])
    for key in VARIABLE_KEYS:
        if env.get(key):
            api.add_space_variable(repo_id, key, env[key])

    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=ROOT,
                      allow_patterns=ALLOW, ignore_patterns=IGNORE,
                      commit_message="Deploy KhetSaathi")
    api.upload_file(repo_id=repo_id, repo_type="space", path_or_fileobj=io.BytesIO(SPACE_README.encode()),
                    path_in_repo="README.md", commit_message="Space config")

    host = repo_id.lower().replace("/", "-").replace("_", "-")
    print(f"Space:  https://huggingface.co/spaces/{repo_id}")
    print(f"App:    https://{host}.hf.space   (first build takes ~5-10 min)")


if __name__ == "__main__":
    main()
