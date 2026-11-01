import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("pt", sys.argv[1]); pt = importlib.util.module_from_spec(spec); spec.loader.exec_module(pt)
rules = json.load(open(sys.argv[2]))
bad = [  # 危険: allow になってはいけない
 'ls & git push --force origin main', 'git push origin feature/x main:main', 'git push origin feature/x +main',
 'git push origin feature/x -uf', 'git push origin feature/x "--force"', 'git push origin feature/x main --tags',
 'gh api repos/o/r/git/refs/heads/main -X PATCH -F force=true', 'gh api repos/o/r/contents/a -XPUT',
 'gh api repos/o/r/git/refs/heads/x --method=DELETE', 'gh api repos/o/r/actions/workflows/d.yml/dispatches -f ref=main',
 'echo x | xargs git push -f origin main', 'find . -exec git push --force origin main ;',
 "python3 -c 'import os; os.system(\"git push -f origin main\")'", 'node -e "1"',
 'echo {} > .agent-kit/claude/cloud-permissions.json', 'cp /tmp/x .agent-kit/claude/pretool.py', 'echo x > .claude/settings.json',
 'cat .env.staging', 'cat .en*', 'grep -r KEY .', 'git branch -a -D main', 'git -C . push --force origin feature/x',
 'env FOO=1 git push -f origin x', 'timeout 5 git push --force origin x',
 "git -c alias.logx='!git push -f origin HEAD:main' logx", "git -c core.pager='sh -c x' log", "GIT_PAGER='sh -c x' git log",
 "git rebase --exec 'git push -f origin HEAD:main' HEAD~1", "git fetch --upload-pack='x' origin", 'git push origin HEAD',
 'git branch --del main', 'git branch --forc main HEAD~3', 'pnpm exec git push --force origin main', "npx -c 'git push -f'",
 'uv run git push -f origin main', 'poetry run git branch -D main', 'pnpm -r publish', 'yarn publish', 'bun publish',
 'cargo +stable publish', 'npx vercel deploy --prod', 'npx wrangler deploy', 'python -m twine upload x', 'make deploy',
 'npm run deploy', 'python3 -cimport\\ os', 'node --eval=1', "sed -n '1e git push -f origin HEAD:main' README.md",
 "sed -n 'w .claude/settings.json' x", 'sort -o .claude/settings.json x', 'uniq x .claude/settings.json',
 'git diff --output=.agent-kit/claude/pretool.py', 'cp x .claude/', 'cp x .claude', 'cp x .claude/./settings.json',
 'cp x .claude/settings.jso?', 'cp x .claude/settings{,}.json', 'git mv x .claude/settings.json',
 'git checkout evil -- .agent-kit/claude/pretool.py', 'git restore --source=evil .agent-kit/claude/pretool.py',
 'echo x >| .claude/settings.json', 'bash .agent-kit/../tmp/x.sh', 'cat {.env,x}', 'cat .e${x}nv', "cat $'\\x2eenv'",
 'git show HEAD:.env', 'cat .envrc', 'cat ~/.git-credentials', 'cat ~/.config/gh/hosts.yml', 'echo $GITHUB_TOKEN',
 'rg --no-ignore --hidden KEY', 'docker run -v /:/h alpine', 'git commit --amend -m x', 'git rebase -i --root',
 'git stash drop', 'git pull --force', 'git tag -d v1', 'git reset --hard HEAD~5',
]
must_deny = ['git push --force origin feature/x', 'git push origin :feature/x', 'git -C . push --force origin feature/x',
 'cat ./.env', 'git branch -D main', 'git tag --delete v1', 'git push origin feature/x --delete']
good = ['npm test', 'pytest -q', 'git commit -m "msg with ; and &"', 'git push -u origin feature/12-x', 'gh api repos/o/r/pulls',
 'npm run build && npm test', 'git status', 'git add -A', 'git diff --stat', 'git log --oneline -5', 'pnpm install',
 'pnpm test', 'yarn build', 'go test ./...', 'cargo test', 'python3 -m pytest -q', 'uv sync', 'ls', 'ls *',
 'cat README.md', 'cat src/x.ts', 'bash .agent-kit/bin/setup-deps.sh deps', 'python3 .agent-kit/bin/agent-log status',
 'gh api -H Accept:application/json repos/o/r/pulls', 'git switch -c feature/3-x', 'git fetch origin', 'git pull --rebase',
 'cp .env.example .env.sample', 'git diff .agent-kit/claude/pretool.py', 'make test', 'git branch', 'git stash pop']
fail = 0
for c in bad:
    d = pt.decide_text(c, rules)
    if d and d[0] == "allow":
        print("STILL ALLOW:", c); fail += 1
for c in must_deny:
    d = pt.decide_text(c, rules)
    if not d or d[0] != "deny":
        print("NOT DENY:", c, d); fail += 1
for c in good:
    d = pt.decide_text(c, rules)
    if not d or d[0] != "allow":
        print("NOT ALLOWED (false positive):", c, d); fail += 1
print(f"bad={len(bad)} deny={len(must_deny)} good={len(good)} problems={fail}")
sys.exit(1 if fail else 0)
