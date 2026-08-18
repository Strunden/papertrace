#!/bin/bash
# Double-click to publish the current state of this folder to GitHub Pages.
cd "$(dirname "$0")" || exit 1

echo "=== PaperTrace: publishing to GitHub ==="

# Claude's file bridge can write but not delete, so any git command it runs
# leaves stale lock files behind that would block the next commit. Clear them.
find .git -name '*.lock' -delete 2>/dev/null
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null
rm -f papertrace-repo.tgz

git add -A
if git diff --cached --quiet; then
  echo "No new changes - pushing anything not yet on GitHub."
else
  git commit -m "${1:-Update from Claude}" | tail -2
fi

echo
if git push -u origin main; then
  echo
  echo "Done. Live in ~30 seconds at:"
  echo "    https://strunden.github.io/papertrace/"
else
  echo
  echo "Push failed."
  echo "If it asked for a password: GitHub no longer accepts account passwords."
  echo "Paste a personal access token as the password instead"
  echo "(github.com/settings/tokens - classic token with 'repo' scope is fine)."
  echo "macOS keychain will remember it after the first time."
  echo
  echo "If you use SSH keys instead, switch the remote once with:"
  echo "    git remote set-url origin git@github.com:Strunden/papertrace.git"
fi
echo
read -n 1 -s -r -p "Press any key to close."
echo
