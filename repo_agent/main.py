#!/usr/bin/env python3
"""Agent 1: Repository Watchdog & Git Automation Engine.

Features:
- GitHub REST API / PyGithub polling with SHA & ETag caching to conserve rate limits.
- CI/CD workflow status & version inspection.
- Automated branch creation (`chore/agent-upgrade-*`) and patch submission.
- Guardrails: Never pushes directly to default branch; opens Pull Request.
"""

import base64
from datetime import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import requests

# Ensure agents root is in import path
AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

try:
    from github import Github, GithubException
except ImportError:
    Github = None
    GithubException = Exception

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [repo-agent] %(message)s")
logger = logging.getLogger("repo-agent")

CACHE_DIR = Path.home() / "agents" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "repo_sha_cache.json"


def send_desktop_notification(repo_name: str, pr_title: str, pr_url: str) -> None:
    """Sends a desktop notification using notify-send from a systemd user service."""
    if not shutil.which("notify-send"):
        return

    # Inherit current process environment
    env = os.environ.copy()

    # Ensure DBUS session bus address is set for systemd user services
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"

    # Display environment fallback if running Wayland or X11
    if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        run_user = Path(f"/run/user/{uid}")
        wayland_sockets = list(run_user.glob("wayland-*")) if run_user.exists() else []
        if wayland_sockets:
            env["WAYLAND_DISPLAY"] = wayland_sockets[0].name
        else:
            env["DISPLAY"] = ":0"

    summary = f"GitHub PR Created: {repo_name}"
    body = f"{pr_title}\n{pr_url}"

    cmd = [
        "notify-send",
        "--urgency=normal",
        "--expire-time=10000",
        "--app-name=RepoWatchdog",
        "--icon=git",
        summary,
        body,
    ]

    try:
        subprocess.run(cmd, env=env, check=False)
        logger.info(f"[+] Desktop notification dispatched for {repo_name}")
    except Exception as e:
        logger.warning(f"[!] Notification dispatch warning: {e}")


def load_sha_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_sha_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not persist SHA cache: {e}")


def enable_auto_merge(repo, pr, token: str, merge_method: str = "SQUASH") -> bool:
    """Enables auto-merge on a Pull Request using GitHub's GraphQL API.
    merge_method options: SQUASH, MERGE, REBASE
    """
    # 1. Ensure repository settings permit auto-merge
    try:
        repo.edit(allow_auto_merge=True, delete_branch_on_merge=True)
    except Exception as e:
        logger.debug(f"[!] Note on repo auto-merge settings for {repo.full_name}: {e}")

    # 2. GraphQL mutation
    query = """
    mutation EnableAutoMerge($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      enablePullRequestAutoMerge(input: {
        pullRequestId: $pullRequestId,
        mergeMethod: $mergeMethod
      }) {
        pullRequest {
          id
          autoMergeRequest {
            enabledAt
            mergeMethod
          }
        }
      }
    }
    """
    variables = {
        "pullRequestId": pr.node_id,
        "mergeMethod": merge_method
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15
        )

        if response.status_code == 200:
            result = response.json()
            if "errors" in result:
                logger.warning(f"[!] GraphQL error enabling auto-merge on PR #{pr.number}: {result['errors']}")
                return False
            logger.info(f"[+] Auto-merge ({merge_method}) enabled on PR #{pr.number} ({repo.full_name})")
            return True
        else:
            logger.warning(f"[!] HTTP error {response.status_code} enabling auto-merge: {response.text}")
            return False
    except Exception as e:
        logger.warning(f"[!] Exception calling GraphQL auto-merge: {e}")
        return False


START_TAG = "<!-- START_AGENT_MAINTENANCE_LOG -->"
END_TAG = "<!-- END_AGENT_MAINTENANCE_LOG -->"


def update_readme_content(original_text: str, change_summary: str) -> str:
    """Injects or appends an automated maintenance record into the repository README.md."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    new_entry = (
        f"#### Maintenance Run: `{timestamp}`\n"
        f"{change_summary}\n\n"
    )

    if START_TAG in original_text and END_TAG in original_text:
        # Prepend new entry right below the START_TAG
        before, rest = original_text.split(START_TAG, 1)
        inside, after = rest.split(END_TAG, 1)
        updated_inside = f"\n{new_entry}" + inside.lstrip("\n")
        return f"{before}{START_TAG}{updated_inside}{END_TAG}{after}"
    else:
        # Append section at the bottom of the README if tags do not exist
        section = (
            f"\n\n## Automated CI Maintenance Log\n"
            f"{START_TAG}\n"
            f"{new_entry}"
            f"{END_TAG}\n"
        )
        return original_text.rstrip() + section


def patch_readme_in_branch(repo, branch_name: str, explanations: list) -> None:
    """Fetches README.md from the target branch, modifies it, and commits the update."""
    if not explanations:
        return

    change_summary = "\n".join(f"- {exp}" for exp in explanations)
    readme_names = ["README.md", "readme.md", "README"]

    readme_file = None
    for name in readme_names:
        try:
            readme_file = repo.get_contents(name, ref=branch_name)
            break
        except Exception:
            continue

    if not readme_file:
        # Create README.md if it does not exist
        content = (
            f"# {repo.name}\n\n"
            f"## Automated CI Maintenance Log\n"
            f"{START_TAG}\n"
            f"#### Maintenance Run: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
            f"{change_summary}\n\n"
            f"{END_TAG}\n"
        )
        repo.create_file(
            path="README.md",
            message="docs(ci): initialize maintenance log in README.md",
            content=content,
            branch=branch_name
        )
        logger.info(f"[+] Created README.md on branch {branch_name}")
        return

    # Decode existing content safely
    if hasattr(readme_file, "decoded_content") and readme_file.decoded_content:
        original_text = readme_file.decoded_content.decode("utf-8")
    else:
        original_text = base64.b64decode(readme_file.content).decode("utf-8")

    updated_text = update_readme_content(original_text, change_summary)

    if updated_text != original_text:
        repo.update_file(
            path=readme_file.path,
            message="docs(ci): document automated workflow upgrades in README.md",
            content=updated_text,
            sha=readme_file.sha,
            branch=branch_name
        )
        logger.info(f"[+] Updated {readme_file.path} on branch {branch_name}")


class RepoWatchdog:
    def __init__(self, config_path: Path):
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as f:
            self.config = json.load(f)

        token = os.getenv("GITHUB_TOKEN")
        if not token:
            env_file = config_path.parent / ".env"
            if env_file.exists():
                try:
                    with open(env_file, "r") as ef:
                        for line in ef:
                            clean_line = line.strip()
                            if clean_line.startswith("GITHUB_TOKEN="):
                                token = clean_line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                except Exception as e:
                    logger.warning(f"Could not read local .env file: {e}")

        if not token:
            token = self.config.get("github_token")

        if not token or token.startswith("your_"):
            # Check if GitHub CLI 'gh' is authenticated
            try:
                result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    token = result.stdout.strip()
                    logger.info("[+] Discovered active GitHub token from 'gh' CLI.")
            except Exception:
                pass

        if not token or token.startswith("your_"):
            raise ValueError(
                "Valid GITHUB_TOKEN required via environment variable, .env file, config.json, or 'gh auth login'."
            )

        if Github is None:
            raise ImportError("PyGithub is required. Install via `pip install PyGithub`.")

        self.token = token
        self.gh = Github(token)
        self.scraper_url = self.config.get("scraper_url", "http://127.0.0.1:8484/research")
        self.poll_interval = self.config.get("scan_interval_seconds", self.config.get("poll_interval_seconds", 3600))
        self.filters = self.config.get("filters", {})
        self.include_forks = self.filters.get("include_forks", False)
        target_list = self.filters.get("target_repos", self.config.get("target_repos", []))
        self.target_repos = set(target_list)
        self.excluded_repos = set(self.filters.get("excluded_repos", []))
        self.required_topics = set(
            topic.lower() for topic in self.filters.get("required_topics", [])
        )
        self.sha_cache = load_sha_cache()

    def query_scraper_agent(self, repo_name: str, workflow_files: list, workflows: dict = None) -> list:
        payload = {
            "repo": repo_name,
            "query": "Check GitHub Action versions and CI runner deprecations",
            "workflow_files": workflow_files,
            "workflows": workflows or {},
        }
        try:
            resp = requests.post(self.scraper_url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("recommendations", [])
            else:
                logger.warning(f"Scraper returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to communicate with scraper-agent at {self.scraper_url}: {e}")
        return []

    def prune_merged_branches(self, repo) -> None:
        """Deletes remote chore/agent-upgrade-* branches whose pull requests are merged."""
        try:
            pulls = repo.get_pulls(state="closed", sort="updated", direction="desc")
            for i, pr in enumerate(pulls):
                if i >= 20:
                    break
                if pr.merged and pr.head.ref.startswith("chore/agent-upgrade-"):
                    branch_ref_str = f"heads/{pr.head.ref}"
                    try:
                        ref = repo.get_git_ref(branch_ref_str)
                        ref.delete()
                        logger.info(f"[-] Pruned merged branch {pr.head.ref} on {repo.full_name}")
                    except GithubException:
                        pass
        except Exception as e:
            logger.debug(f"Could not check closed PRs for pruning on {repo.full_name}: {e}")

    def inspect_and_patch_repo(self, repo) -> None:
        logger.info(f"Inspecting repository: {repo.full_name}")

        # Automatically prune branches for merged agent PRs
        self.prune_merged_branches(repo)

        try:
            default_branch = repo.get_branch(repo.default_branch)
            current_sha = default_branch.commit.sha
        except GithubException as e:
            logger.error(f"Could not retrieve default branch for {repo.full_name}: {e}")
            return

        # Check commit cache to avoid redundant API scans
        cached_sha = self.sha_cache.get(repo.full_name)
        if cached_sha == current_sha:
            logger.info(f"No new commits on {repo.full_name} ({current_sha[:7]}). Skipping.")
            return

        # Identify workflow files
        workflow_paths = []
        workflow_map = {}
        try:
            contents = repo.get_contents(".github/workflows")
            for item in contents:
                if item.path.endswith(".yml") or item.path.endswith(".yaml"):
                    workflow_paths.append(item.path)
                    try:
                        if hasattr(item, "decoded_content") and item.decoded_content:
                            workflow_map[item.path] = item.decoded_content.decode("utf-8")
                        else:
                            raw = repo.get_contents(item.path, ref=default_branch.name)
                            workflow_map[item.path] = raw.decoded_content.decode("utf-8")
                    except Exception:
                        pass
        except GithubException:
            logger.info(f"No .github/workflows directory in {repo.full_name}.")

        if not workflow_paths:
            self.sha_cache[repo.full_name] = current_sha
            save_sha_cache(self.sha_cache)
            return

        recommendations = self.query_scraper_agent(repo.full_name, workflow_paths, workflow_map)
        if not recommendations:
            logger.info(f"No patches recommended for {repo.full_name}.")
            self.sha_cache[repo.full_name] = current_sha
            save_sha_cache(self.sha_cache)
            return

        self.apply_patches_via_pull_request(repo, default_branch, recommendations)
        self.sha_cache[repo.full_name] = current_sha
        save_sha_cache(self.sha_cache)

    def apply_patches_via_pull_request(self, repo, base_branch, recommendations: list) -> None:
        # Pre-verify that at least one patch is applicable on the base branch
        unique_paths = set(rec.get("file_path") for rec in recommendations)
        base_files = {}
        for path in unique_paths:
            try:
                base_files[path] = repo.get_contents(path, ref=base_branch.name)
            except GithubException:
                pass

        applicable = False
        for rec in recommendations:
            file_path = rec.get("file_path")
            target = rec.get("target_line")
            replacement = rec.get("replacement")
            if file_path in base_files:
                text = base_files[file_path].decoded_content.decode("utf-8")
                if target in text and replacement.strip() not in text:
                    applicable = True
                    break

        if not applicable:
            logger.info(f"No applicable targets remain on {repo.full_name}. Skipping branch creation.")
            return

        timestamp = int(time.time())
        branch_name = f"chore/agent-upgrade-{timestamp}"
        ref_path = f"refs/heads/{branch_name}"

        patches_applied = 0
        pr_body_lines = ["### Automated Dependency & CI Upgrades", ""]
        readme_explanations = []

        try:
            # Create feature branch from base
            repo.create_git_ref(ref=ref_path, sha=base_branch.commit.sha)
            logger.info(f"Created branch {branch_name} on {repo.full_name}")

            for rec in recommendations:
                file_path = rec.get("file_path")
                target = rec.get("target_line")
                replacement = rec.get("replacement")
                summary = rec.get("summary")

                try:
                    file_content = repo.get_contents(file_path, ref=branch_name)
                    original_text = file_content.decoded_content.decode("utf-8")

                    if target in original_text:
                        # Skip if already hardened or upgraded to avoid duplicate patches
                        if replacement.strip() in original_text:
                            continue
                        updated_text = original_text.replace(target, replacement)
                        repo.update_file(
                            path=file_path,
                            message=f"chore(ci): {summary}",
                            content=updated_text,
                            sha=file_content.sha,
                            branch=branch_name,
                        )
                        logger.info(f"Patched {file_path} on {branch_name}")
                        pr_body_lines.append(f"- **{file_path}**: {summary} (`{target}` → `{replacement}`)")
                        readme_explanations.append(f"`{file_path}`: {summary}")
                        patches_applied += 1
                except GithubException as ge:
                    logger.warning(f"Could not patch {file_path}: {ge}")

            if patches_applied > 0:
                # Patch README.md on the same branch before opening the PR
                patch_readme_in_branch(repo, branch_name, readme_explanations)

                pr_body_lines.append("\n> Generated automatically by `repo-agent` with auto-merge verification.")
                pr = repo.create_pull(
                    title=f"chore(ci): automated actions & dependency updates ({timestamp})",
                    body="\n".join(pr_body_lines),
                    base=base_branch.name,
                    head=branch_name,
                )
                logger.info(f"[+] Pull Request created: {pr.html_url}")
                enable_auto_merge(repo, pr, self.token, merge_method="SQUASH")
                send_desktop_notification(
                    repo_name=repo.full_name,
                    pr_title=pr.title,
                    pr_url=pr.html_url,
                )
            else:
                logger.info("No matching targets found to patch. Deleting temporary branch.")
                ref = repo.get_git_ref(f"heads/{branch_name}")
                ref.delete()

        except GithubException as e:
            logger.error(f"Git automation failure on {repo.full_name}: {e}")

    def should_process_repo(self, repo) -> bool:
        """
        Determines whether a repository matches inclusion and exclusion rules.
        """
        repo_name = repo.full_name

        # 1. Skip third-party forks if disabled
        if repo.fork and not self.include_forks:
            logger.info(f"[-] Skipping fork: {repo_name}")
            return False

        # 2. Check explicit exclusion list
        if repo_name in self.excluded_repos:
            logger.info(f"[-] Skipping excluded repository: {repo_name}")
            return False

        # 3. Check explicit target allowlist (if populated)
        if self.target_repos and repo_name not in self.target_repos:
            return False

        # 4. Filter by GitHub topics
        if self.required_topics:
            try:
                # PyGithub returns a list of string topics
                repo_topics = set(t.lower() for t in repo.get_topics())
            except Exception as e:
                logger.warning(f"[!] Warning reading topics for {repo_name}: {e}")
                repo_topics = set()

            # Require at least one matching topic
            if not (self.required_topics & repo_topics):
                logger.info(f"[-] Skipping {repo_name}: Missing required topics ({self.required_topics})")
                return False

        return True

    def scan_all_repositories(self) -> None:
        """Audits and patches all repositories matching the filter criteria."""
        user = self.gh.get_user()
        repos = user.get_repos()
        for repo in repos:
            if not self.should_process_repo(repo):
                continue

            logger.info(f"[*] Auditing target: {repo.full_name}")
            self.inspect_and_patch_repo(repo)

    def run_cycle(self) -> None:
        self.scan_all_repositories()

    def run_forever(self) -> None:
        logger.info("repo-agent loop started.")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Error in watchdog cycle: {e}")
            logger.info(f"Sleeping for {self.poll_interval} seconds...")
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    cfg_path = Path(__file__).parent / "config.json"
    agent = RepoWatchdog(cfg_path)
    agent.run_forever()
