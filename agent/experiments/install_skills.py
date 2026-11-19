"""Install pinned upstream SKILL directories as DATA (never execute their scripts)."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "third_party_skills"
SOURCES = [
    {
        "name": "context-engineering",
        "url": "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
        "sha": "9ff0c99108b36d3b886defd825d8ac1f47dba374",
        "skills": [("context-fundamentals","runtime"),
                   ("context-compression","runtime"),
                   ("context-degradation","runtime"),
                   ("filesystem-context","runtime"),
                   ("tool-design","runtime"),
                   ("memory-systems","runtime"),
                   ("evaluation","runtime")],
    },
    {
        "name": "planning-with-files",
        "url": "https://github.com/OthmanAdi/planning-with-files.git",
        "sha": "51bf62def62d1cb317d37936929b4ef97cf862c3",
        "skills": [("planning-with-files","runtime")],
    },
    {
        "name": "superpowers",
        "url": "https://github.com/obra/superpowers.git",
        "sha": "22f0b463e7a5e579904dd6211a520321035526f7",
        "skills": [("systematic-debugging","dev"),
                   ("verification-before-completion","dev")],
    },
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args()
    if args.check:
        for path in sorted(DEST.glob("*/*/SKILL.md")):
            print(path.relative_to(ROOT))
        return
    cache = DEST / ".upstream"
    cache.mkdir(parents=True,exist_ok=True)
    manifest = []
    for project in SOURCES:
        local = cache / project["name"]
        if not local.exists():
            subprocess.run(["git","clone","--no-checkout",project["url"],str(local)],
                           check=True)
        subprocess.run(["git","-C",str(local),"fetch","origin",project["sha"]],check=True)
        subprocess.run(["git","-C",str(local),"checkout","--detach",project["sha"]],check=True)
        actual = subprocess.check_output(["git","-C",str(local),"rev-parse","HEAD"],
                                         text=True).strip()
        if actual != project["sha"]:
            raise RuntimeError("SHA mismatch for " + project["name"])
        for skill, category in project["skills"]:
            source = local / "skills" / skill
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(source / "SKILL.md")
            target = DEST / category / skill
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source,target,ignore=shutil.ignore_patterns("__pycache__"))
            manifest.append({"name":skill,"category":category,
                             "source":project["url"],"sha":actual})
    (DEST / "manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Skills installed:", len(manifest),
          "(text only at runtime; do NOT execute bundled scripts)")

if __name__ == "__main__":
    main()
