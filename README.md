# Install

```bash
clone https://github.com/Doggy-Footprint/scored-web-search

mkdir -p ~/.claude/skills
mkdir -p ~/.codex/skills

# symlink로 연결
ln -s "$(realpath scored-web-search)" ~/.claude/skills/scored-web-search
ln -s "$(realpath scored-web-search)" ~/.codex/skills/scored-web-search
```

# Customization (policy)

Check `scripts/policy.json` and `scripts/modes/` to edit this skill permanently.