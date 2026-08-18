#!/bin/bash
# Double-click this to publish the current state of the folder to GitHub Pages.
cd "$(dirname "$0")" || exit 1
rm -f papertrace-repo.tgz

echo "=== PaperTrace: publishing to GitHub ==="
git add -A
if git diff --cached --quiet; then
  echo "Nothing new to commit - pushing whatever is unpushed."
else
  git commit -m "${1:-Update from Claude}" | tail -3
fi

echo
echo "Pushing..."
if git push -u origin main; then
  echo
  echo "Done. Live in ~30 seconds at:"
  echo "   https://strunden.github.io/papertrace/"
else
  echo
  echo "Push failed. If it asked for a password: GitHub stopped accepting"
  echo "account passwords - paste a personal access token as the password"
  echo "instead (github.com/settings/tokens). macOS will remember it."
  echo "If you use SSH keys instead, run:"
  echo "   git remote set-url origin git@github.com:Strunden/papertrace.git"
fi
echo
read -n 1 -s -r -p "Press any key to close."
