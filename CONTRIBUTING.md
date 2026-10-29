# Contributing

How the team works in this repository.

## Workflow: GitHub Flow

- `main` always works. Nobody pushes to it directly; the ruleset on `main` rejects direct pushes.
- Every change belongs to a Jira issue. Create one branch per issue from the latest `main`:

  ```
  git checkout main
  git pull
  git checkout -b story/GES-12-recording-script
  ```

- When the work is ready, push the branch and open a pull request into `main`.
- After at least one approval, the author merges the pull request and deletes the branch.

## Branch names

```
<type>/GES-<number>-<short-description>
```

- `<type>` follows the Jira issue type: `story`, `task` or `bug`.
- The short description is in English and lowercase, with words separated by hyphens.

Examples: `story/GES-12-recording-script`, `task/GES-13-update-readme`, `bug/GES-14-fix-port-detection`.

## Commit messages

```
GES-<number>: <summary>
```

- Start with the Jira key, followed by a colon.
- Write the summary in one line, in English and in the imperative: "Add", "Fix", "Update".

Example: `GES-12: Add recording script`

The Jira key in branch names, commits and pull request titles links the work to the issue in Jira.

## Pull requests and review

- The title starts with the Jira key, like a commit message: `GES-12: Add recording script`.
- The description says what changed, why, and what the reviewer should check.
- At least one approval from another team member is required before merging. You cannot approve your own pull request.
- Review comments and answers stay on the pull request, not in chat, so the discussion stays with the code.
- In Jira, move the issue to In Progress when you start, to In Review when the pull request is opened, and to Done after it is merged.

## Other rules

- Never commit recordings or participant data. They go under `data/`, which is in `.gitignore`. The repository is public.
- Everything in the repository (code, comments, documents, commit messages) is written in English.
