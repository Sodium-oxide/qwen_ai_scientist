"""Splice the replacement block into _pipeline.py."""
import pathlib

pipeline = pathlib.Path("_pipeline.py")
replacement = pathlib.Path("_replacement_block.py")

pipe_lines = pipeline.read_text(encoding="utf-8").split("\n")
repl_lines = replacement.read_text(encoding="utf-8").split("\n")

# Find boundaries
run_boxue_start = None
create_science_start = None
for i, line in enumerate(pipe_lines):
    if line.startswith("def run_boxue_research_round("):
        run_boxue_start = i
    if line.startswith("def create_science_delegation_tasks("):
        create_science_start = i

assert run_boxue_start is not None, "Could not find run_boxue_research_round"
assert create_science_start is not None, "Could not find create_science_delegation_tasks"

print(f"Replacing lines {run_boxue_start + 1}-{create_science_start} with {len(repl_lines)} new lines")

# Splice: everything before run_boxue + replacement + everything from create_science onward
new_lines = pipe_lines[:run_boxue_start] + repl_lines + pipe_lines[create_science_start:]
pipeline.write_text("\n".join(new_lines), encoding="utf-8")
print(f"Done. New total: {len(new_lines)} lines")
