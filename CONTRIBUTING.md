# Contributing to This Repository

First off, thank you for considering contributing to our project! 

## Fork Workflow

We use the standard GitHub fork and pull request workflow.

1. **Fork** the repository to your own GitHub account.
2. **Clone** the project to your local machine:
   `git clone https://github.com/YOUR_USERNAME/REPOSITORY_NAME.git`
3. **Add Upstream**: Keep your fork in sync with the original repository.
   `git remote add upstream https://github.com/ORIGINAL_OWNER/REPOSITORY_NAME.git`

## Branch Naming Convention

Create a new branch for your work. Use the following naming convention:
- `feature/issue-number-short-description` (e.g., `feature/42-add-login`)
- `bugfix/issue-number-short-description` (e.g., `bugfix/103-fix-header`)
- `docs/issue-number-short-description`

## Commit Message Format

We follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

`<type>[optional scope]: <description>`

Examples:
- `feat: add user authentication`
- `fix(header): correct logo alignment`
- `docs: update setup instructions`

## Creating Issues

- Before creating an issue, search the existing issues to avoid duplicates.
- Use the provided Issue Templates.
- Provide as much detail as possible, including reproduction steps for bugs.

## Creating PRs

- **One PR per Issue**: Do not combine multiple unrelated changes into one PR.
- Fill out the Pull Request Template completely.
- Ensure your PR uses the required keyword to link the issue: `Closes #123`.

## Review Process & Merge Policy

- Your PR will be reviewed by a Mentor first.
- Address any requested changes promptly.
- Once a Mentor approves, a Maintainer will merge it.
- PRs failing automated validations (PR validation, tests) will not be reviewed until fixed.

## Code Style

Follow the existing code style in the repository. Run your linter and formatter locally before committing.
