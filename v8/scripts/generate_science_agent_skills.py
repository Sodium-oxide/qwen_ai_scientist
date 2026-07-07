from __future__ import annotations

import re
from pathlib import Path


SOURCE = Path(r"C:\Users\31390\.qwenpaw\workspaces\default\agent_prompts_v3.md")
SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

AGENT_NAMES = {
    0: "boxue",
    1: "zhizhi",
    2: "tanxi",
    3: "gewu",
    4: "mingli",
    5: "duzhi",
    6: "bianlun",
    7: "yanzhen",
    8: "mingbian",
    9: "reviewer",
    10: "codeengineer",
    11: "paperwriter",
}


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    matches = list(re.finditer(r"^## Agent\s+(\d+):\s*(.+)$", text, flags=re.MULTILINE))
    if not matches:
        raise SystemExit(f"No agent sections found in {SOURCE}")

    for index, match in enumerate(matches):
        number = int(match.group(1))
        if number not in AGENT_NAMES:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else text.find("---\n\n## Appendix", match.end())
        if end == -1:
            end = len(text)
        section = text[start:end].strip()
        name = f"science_{AGENT_NAMES[number]}"
        title = readable_title(match.group(2))
        body = render_skill(name, title, section)
        target_dir = SKILLS_DIR / name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(body, encoding="utf-8")
        print(f"wrote {target_dir / 'SKILL.md'}")


def readable_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip()
    title = title.replace('"', "'")
    return title or "Qwen-Zhikan science agent"


def render_skill(name: str, title: str, section: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Full Qwen-Zhikan AI Scientist prompt for {title}.\n"
        "---\n\n"
        "# Full Science Agent Prompt\n\n"
        "Use this skill when acting as this specialized Qwen-Zhikan AI Scientist agent. "
        "Follow the prompt exactly, preserve the TAO workflow, and return the specified JSON format.\n\n"
        f"{section}\n"
    )


if __name__ == "__main__":
    main()
