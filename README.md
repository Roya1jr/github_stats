# 📊 GitHub Language Stats

A Python script that generates an SVG file showcasing the languages used in a user's GitHub repositories, including both owned and contributed projects.

---

## 🚀 How to Use

### 1. Prerequisites

- **Python 3.10+**
- **A GitHub Personal Access Token (classic)** with the following scopes:
  - `repo` (to access public and private repositories)
  - `user` (to read user data and contributions)

### 2. Setup

First, install the necessary dependencies using `uv`:

```bash
uv sync
````

Next, set your GitHub username and token as environment variables to authenticate with the GitHub API.

```bash
export GITHUB_ACTOR="your-username"
export ACCESS_TOKEN="your-token"
```

### 3\. Run the Script

Execute the script from your terminal:

```bash
python your_script_name.py
```

The script will generate a file named `languages.svg` in the same directory.

---

## 🖼️ Displaying the SVG

To display the generated SVG on your GitHub profile or in a repository README, simply add the following Markdown:

```markdown
![Languages Used](languages.svg)
```

-----

## ⚙️ GitHub Actions Workflow

This project includes a pre-configured workflow to automate the process of updating the SVG file.

[Workflow File](./.github/workflows/update-languages.yml)

To use it, you must add your GitHub Personal Access Token as a repository secret named `ACCESS_TOKEN` under **Settings \> Secrets and variables \> Actions**. The workflow will run automatically on a weekly schedule.

## License

[LICENSE](LICENSE)
