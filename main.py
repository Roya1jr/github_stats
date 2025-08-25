import asyncio
import os
import time
from typing import Dict, Optional, Set
import aiohttp
import json


class EnhancedGitHubStats:
    def __init__(self, username: str, access_token: str):
        self.username = username
        self.access_token = access_token
        self.semaphore = asyncio.Semaphore(3)
        self.languages = {}
        self.repos_found = set()
        self.total_repos_processed = 0

    async def query_graphql(
        self, session: aiohttp.ClientSession, query: str, retries: int = 3
    ) -> Dict:
        """Make a GraphQL API request with retry logic"""
        headers = {"Authorization": f"Bearer {self.access_token}"}

        for attempt in range(retries):
            try:
                async with self.semaphore:
                    await asyncio.sleep(0.1)  # Small delay to avoid rate limits
                    async with session.post(
                        "https://api.github.com/graphql",
                        headers=headers,
                        json={"query": query},
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            if "errors" in result:
                                print(f"GraphQL errors: {result['errors']}")
                                if attempt == retries - 1:
                                    return {}
                                continue
                            return result
                        elif response.status == 403:
                            print(
                                f"Rate limited, waiting 60 seconds... (attempt {attempt + 1})"
                            )
                            await asyncio.sleep(60)
                            continue
                        else:
                            print(
                                f"GraphQL request failed with status {response.status}"
                            )
                            if attempt == retries - 1:
                                return {}
            except Exception as e:
                print(f"GraphQL query failed (attempt {attempt + 1}): {e}")
                if attempt == retries - 1:
                    return {}
                await asyncio.sleep(2**attempt)  # Exponential backoff
        return {}

    def get_owned_repos_query(self, cursor: Optional[str] = None) -> str:
        """Generate GraphQL query to get all owned repositories (including private)"""
        after_clause = f'after: "{cursor}"' if cursor else ""

        return f"""
        query {{
          user(login: "{self.username}") {{
            repositories(
              first: 100, 
              orderBy: {{field: UPDATED_AT, direction: DESC}}
              {after_clause}
            ) {{
              pageInfo {{
                hasNextPage
                endCursor
              }}
              nodes {{
                nameWithOwner
                isPrivate
                isEmpty
                isFork
                languages(first: 50, orderBy: {{field: SIZE, direction: DESC}}) {{
                  edges {{
                    size
                    node {{
                      name
                      color
                    }}
                  }}
                  totalSize
                }}
              }}
            }}
          }}
        }}
        """

    def get_contributed_repos_query(self, cursor: Optional[str] = None) -> str:
        """Generate GraphQL query to get repositories contributed to (including org repos)"""
        after_clause = f'after: "{cursor}"' if cursor else ""

        return f"""
        query {{
          user(login: "{self.username}") {{
            repositoriesContributedTo(
              first: 100,
              includeUserRepositories: false,
              contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, PULL_REQUEST_REVIEW]
              {after_clause}
            ) {{
              pageInfo {{
                hasNextPage
                endCursor
              }}
              nodes {{
                nameWithOwner
                isPrivate
                isEmpty
                isFork
                owner {{
                  __typename
                  login
                }}
                languages(first: 50, orderBy: {{field: SIZE, direction: DESC}}) {{
                  edges {{
                    size
                    node {{
                      name
                      color
                    }}
                  }}
                  totalSize
                }}
              }}
            }}
          }}
        }}
        """

    async def process_repo_languages(self, repo: dict, repo_type: str = "owned"):
        """Process languages for a single repository"""
        if not repo:
            return

        repo_name = repo["nameWithOwner"]
        is_private = repo.get("isPrivate", False)
        is_empty = repo.get("isEmpty", False)
        is_fork = repo.get("isFork", False)

        # Skip empty repositories
        if is_empty:
            # print(f"  Skipping empty repo: {repo_name}")
            return

        self.repos_found.add(repo_name)
        self.total_repos_processed += 1

        # fork_indicator = " (fork)" if is_fork else ""
        # privacy_indicator = " (private)" if is_private else " (public)"
        # print(f"  Processing: {repo_name}{privacy_indicator}{fork_indicator}")

        languages_data = repo.get("languages", {})
        total_size = languages_data.get("totalSize", 0)

        if total_size == 0:
            # print(f"    No language data available")
            return

        # Process language statistics
        for lang_edge in languages_data.get("edges", []):
            if not lang_edge or not lang_edge.get("node"):
                continue

            lang_name = lang_edge["node"]["name"]
            lang_color = lang_edge["node"]["color"] or "#858585"
            lang_size = lang_edge["size"]

            if lang_name not in self.languages:
                self.languages[lang_name] = {
                    "size": 0,
                    "color": lang_color,
                    "repos": set(),
                }

            self.languages[lang_name]["size"] += lang_size
            self.languages[lang_name]["repos"].add(repo_name)

        # print(f"    Languages found: {len(languages_data.get('edges', []))} (total size: {total_size:,} bytes)")

    async def collect_stats(self, session: aiohttp.ClientSession):
        """Collect language statistics from all repositories (owned + contributed)"""
        print("=" * 60)
        print("Collecting owned repositories (including private)...")
        print("=" * 60)

        # First: Get all owned repositories (including private ones)
        cursor = None
        owned_count = 0

        while True:
            query = self.get_owned_repos_query(cursor)
            result = await self.query_graphql(session, query)

            if not result.get("data") or not result["data"].get("user"):
                print("No more owned repositories data available")
                break

            repos_data = result["data"]["user"]["repositories"]

            for repo in repos_data["nodes"]:
                await self.process_repo_languages(repo, "owned")
                owned_count += 1

            if not repos_data["pageInfo"]["hasNextPage"]:
                break
            cursor = repos_data["pageInfo"]["endCursor"]

        print(f"\nFound {owned_count} owned repositories")

        # Second: Get contributed repositories (org repos, etc.)
        print("\n" + "=" * 60)
        print("Collecting contributed repositories (including org repos)...")
        print("=" * 60)

        cursor = None
        contributed_count = 0

        while True:
            query = self.get_contributed_repos_query(cursor)
            result = await self.query_graphql(session, query)

            if not result.get("data") or not result["data"].get("user"):
                print("No more contributed repositories data available")
                break

            repos_data = result["data"]["user"]["repositoriesContributedTo"]

            for repo in repos_data["nodes"]:
                if not repo:
                    continue

                repo_name = repo["nameWithOwner"]

                # Skip if we already processed this repo
                if repo_name in self.repos_found:
                    continue

                owner_type = repo.get("owner", {}).get("__typename", "Unknown")
                owner_login = repo.get("owner", {}).get("login", "unknown")
                # print(f"  Found contributed repo: {repo_name} ({owner_type}: {owner_login})")

                await self.process_repo_languages(repo, "contributed")
                contributed_count += 1

            if not repos_data["pageInfo"]["hasNextPage"]:
                break
            cursor = repos_data["pageInfo"]["endCursor"]

        print(f"\nFound {contributed_count} additional contributed repositories")
        print(f"Total repositories processed: {self.total_repos_processed}")

    def calculate_percentages(self):
        """Calculate language percentages"""
        total_size = sum(lang["size"] for lang in self.languages.values())

        if total_size == 0:
            return {}

        for lang_name, lang_data in self.languages.items():
            lang_data["percentage"] = (lang_data["size"] / total_size) * 100

        return dict(
            sorted(
                self.languages.items(), key=lambda x: x[1]["percentage"], reverse=True
            )
        )

    def generate_svg(
        self, output_file: str = "languages.svg", min_percentage: float = 0.01
    ):
        """Generate GitHub-style language SVG matching the target format exactly"""
        languages = self.calculate_percentages()

        # Filter out languages below minimum percentage for display
        visible_languages = {
            k: v for k, v in languages.items() if v["percentage"] >= min_percentage
        }

        # Calculate dynamic height based on the number of languages
        num_visible_languages = len(visible_languages)
        base_height = 80
        item_height = 21

        # Calculate heights for the SVG and its content container
        svg_height = base_height + (num_visible_languages * item_height)
        foreignobject_height = svg_height - 34

        # Generate progress bar - no spaces between spans
        progress_segments = []
        for lang_name, lang_data in visible_languages.items():
            percentage = lang_data["percentage"]
            color = lang_data["color"]
            progress_segments.append(
                f'<span style="background-color: {color};width: {percentage:.3f}%;" class="progress-item"></span>'
            )

        progress_bar = "".join(progress_segments)

        # Generate language list with exact formatting
        language_items = []
        for i, (lang_name, lang_data) in enumerate(visible_languages.items()):
            percentage = lang_data["percentage"]
            color = lang_data["color"]
            delay = i * 150

            # Match the exact format from target SVG
            language_items.append(
                f"""

<li style="animation-delay: {delay}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang_name}</span>
<span class="percent">{percentage:.2f}%</span>
</li>"""
            )

        language_list = "".join(language_items)

        svg_content = f"""<svg id="gh-dark-mode-only" width="360" height="{svg_height}" xmlns="http://www.w3.org/2000/svg">
<style>
svg {{
  font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif, Apple Color Emoji, Segoe UI Emoji;
  font-size: 14px;
  line-height: 21px;
}}

#background {{
  width: calc(100% - 10px);
  height: calc(100% - 10px);
  fill: white;
  stroke: rgb(225, 228, 232);
  stroke-width: 1px;
  rx: 6px;
  ry: 6px;
}}

#gh-dark-mode-only:target #background {{
  fill: #0d1117;
  stroke-width: 0.5px;
}}

foreignObject {{
  width: calc(100% - 10px - 32px);
  height: {foreignobject_height}px;
}}

h2 {{
  margin-top: 0;
  margin-bottom: 0.75em;
  line-height: 24px;
  font-size: 16px;
  font-weight: 600;
  color: rgb(36, 41, 46);
  fill: rgb(36, 41, 46);
}}

#gh-dark-mode-only:target h2 {{
  color: #c9d1d9;
  fill: #c9d1d9;
}}

ul {{
  list-style: none;
  padding-left: 0;
  margin-top: 0;
  margin-bottom: 0;
}}

li {{
  display: inline-flex;
  font-size: 12px;
  margin-right: 2ch;
  align-items: center;
  flex-wrap: nowrap;
  transform: translateX(-500%);
  animation: slideIn 2s ease-in-out forwards;
}}

@keyframes slideIn {{
  to {{
    transform: translateX(0);
  }}
}}

div.ellipsis {{
  height: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}}

.octicon {{
  fill: rgb(88, 96, 105);
  margin-right: 0.5ch;
  vertical-align: top;
}}

#gh-dark-mode-only:target .octicon {{
  color: #8b949e;
  fill: #8b949e;
}}

.progress {{
  display: flex;
  height: 8px;
  overflow: hidden;
  background-color: rgb(225, 228, 232);
  border-radius: 6px;
  outline: 1px solid transparent;
  margin-bottom: 1em;
}}

#gh-dark-mode-only:target .progress {{
  background-color: rgba(110, 118, 129, 0.4);
}}

.progress-item {{
  outline: 2px solid rgb(225, 228, 232);
  border-collapse: collapse;
}}

#gh-dark-mode-only:target .progress-item {{
  outline: 2px solid #393f47;
}}

.lang {{
  font-weight: 600;
  margin-right: 4px;
  color: rgb(36, 41, 46);
}}

#gh-dark-mode-only:target .lang {{
  color: #c9d1d9;
}}

.percent {{
  color: rgb(88, 96, 105)
}}

#gh-dark-mode-only:target .percent {{
  color: #8b949e;
}}
</style>
<g>
<rect x="5" y="5" id="background" />
<g>
<foreignObject x="21" y="17" width="318" height="{foreignobject_height}">
<div xmlns="http://www.w3.org/1999/xhtml" class="ellipsis">

<h2>Languages Used (By File Size)</h2>

<div>
<span class="progress">
{progress_bar}
</span>
</div>

<ul>{language_list}



</ul>

</div>
</foreignObject>
</g>
</g>
</svg>"""

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(svg_content)

        print(f"\nSVG generated: {output_file}")
        return svg_content

    def print_detailed_stats(self):
        """Print detailed statistics"""
        languages = self.calculate_percentages()

        print(f"\n" + "=" * 60)
        print(f"📊 DETAILED STATISTICS")
        print(f"=" * 60)
        print(f"Total repositories: {self.total_repos_processed}")
        print(f"Languages found: {len(self.languages)}")

        total_bytes = sum(lang["size"] for lang in self.languages.values())
        print(
            f"Total code size: {total_bytes:,} bytes ({total_bytes / (1024*1024):.2f} MB)"
        )

        print(f"\n🔤 All languages (sorted by usage):")
        print(f"{'-' * 60}")

        for i, (lang, data) in enumerate(languages.items()):
            repo_count = len(data["repos"])
            size_mb = data["size"] / (1024 * 1024)
            print(
                f"{i+1:2d}. {lang:<15} {data['percentage']:6.2f}% ({size_mb:8.2f} MB, {repo_count:2d} repos)"
            )

        # Show languages below 0.01% that won't be displayed in SVG
        small_languages = {k: v for k, v in languages.items() if v["percentage"] < 0.01}
        if small_languages:
            print(
                f"\n📋 Languages below 0.01% (not shown in SVG): {len(small_languages)}"
            )
            for lang, data in small_languages.items():
                print(f"    {lang}: {data['percentage']:.4f}%")

    async def run(self):
        """Main execution"""
        print(f"Starting GitHub language statistics collection for: {self.username}")
        start_time = time.time()

        async with aiohttp.ClientSession() as session:
            await self.collect_stats(session)
            self.generate_svg()
            self.print_detailed_stats()

        elapsed = time.time() - start_time
        print(f"\n⏱️  Completed in {elapsed:.2f} seconds")


async def main():
    username = os.getenv("GITHUB_ACTOR")
    access_token = os.getenv("ACCESS_TOKEN")

    if (
        username == "<username>"
        or access_token == "<token>"
        or not username
        or not access_token
    ):
        raise ValueError(
            "Please set username and access_token variables or GITHUB_ACTOR and ACCESS_TOKEN environment variables"
        )

    stats = EnhancedGitHubStats(username, access_token)
    await stats.run()


if __name__ == "__main__":
    asyncio.run(main())
