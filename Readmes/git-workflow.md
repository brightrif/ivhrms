# Git command for first time

```bash
echo "# ivhrms" >> README.md
git init
git add README.md
git commit -m "first commit"
git branch -M main
git remote add origin git@github.com:brightrif/ivhrms.git
git push -u origin main
```

# Git for update

```bash
git add .
git commit -m "comments"
git push -u origin main
```

# Git workflow For Branch and merge it

`main` always works. Every change is made on a short-lived branch, tested, merged into `main`,
and the branch is then deleted.

### 1. Start a branch

```bash
git checkout main
git pull                            # after the first push; brings in anything new from GitHub
git status                          # should say: working tree clean
git checkout -b payroll-models      # a short name for the job
```

### 2. Work, test, commit (often)

```bash
python manage.py test apps          # before committing
git status --short                  # read the list: only files you meant to change?
git add -A
git commit -m "Add payroll models and tests"
```

### 3. Back the branch up on GitHub (optional)

```bash
git push -u origin payroll-models   # the first time only
git push                            # afterwards, while on that branch
```

### 4. Merge into main

```bash
git checkout main
git pull
git merge payroll-models            # or: git merge --no-ff payroll-models  (keeps a visible "merge" commit)
python manage.py test apps          # run the tests again, now on main
git push                            # the very first time: git push -u origin main
```

`Fast-forward` in the output means `main` simply caught up with the branch. `CONFLICT` means
two changes touched the same lines: see "Conflicts" below.

### 5. Delete the branch

```bash
git branch -d payroll-models                # local. Refuses if it is not fully merged (that is the safety net)
git push origin --delete payroll-models     # on GitHub; only if you pushed it in step 3
```

## Where am I?

```bash
git branch --show-current       # the branch I am on
git status -sb                  # changed files, and ahead/behind GitHub
git log --oneline -10           # the last 10 commits
git diff                        # what I changed but have not staged yet
```

## Undo cheat sheet

| I want to...                                        | Command                            |
| --------------------------------------------------- | ---------------------------------- |
| Throw away my edits to one file (not committed)     | `git restore path/to/file`         |
| Throw away ALL uncommitted edits (cannot be undone) | `git reset --hard`                 |
| Undo my last commit but keep the work               | `git reset --soft HEAD~1`          |
| Undo a commit that is already pushed (safe)         | `git revert <commit-id>`           |
| Park half-done work and get a clean tree            | `git stash`, later `git stash pop` |
| Delete a branch I never merged and do not want      | `git branch -D payroll-models`     |
| Stop a merge that went wrong                        | `git merge --abort`                |

`git revert` adds a new commit that cancels the old one, so history stays honest. Find the id
with `git log --oneline`.

## Conflicts

A failed merge leaves markers inside the file:

```
<<<<<<< HEAD
the version on main
=======
the version from the branch
>>>>>>> payroll-models
```

Edit the file to what you want, delete the three marker lines, then:

```bash
git add path/to/file
git commit                          # finishes the merge
```

Not sure? `git merge --abort` puts everything back as it was.

## Never commit

`.env` (your secret key), `db.sqlite3`, `private_media/` (uploaded certificates and receipts),
`media/`, `ivenv/`, `__pycache__/`. All of them belong in `.gitignore`. Before a push, check
that nothing sensitive is tracked:

```bash
git ls-files | grep -E "\.env|db\.sqlite3|private_media|ivenv" || echo "none of those are tracked"
```

## Things that catch people out

- **`git push -u origin main` pushes the local branch called `main`, not the one you are standing on.**
  Check `git branch --show-current` first. To push a branch, name it: `git push -u origin my-branch`.
- **Automated tools and big refactors need a clean tree.** Commit (or `git stash`) first. A commit is
  your undo button.
- **Migrations travel with the code.** Commit the migration files together with the model change
  (`git add -A` does). After switching to a branch with different migrations, run
  `python manage.py migrate`. If a branch's migration was already applied to your dev database and you
  go back to `main` before merging it, the database is ahead of the code: merge the branch, or
  rebuild the dev database (it is only a file and is not in git).
- **"LF will be replaced by CRLF"** warnings on Windows are harmless.
