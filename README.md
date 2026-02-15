# Intent

Specification and architecture driven code generation toolkit.

## Vision

The idea behind **Intent** is that most software projects can be described well enough to generate most of the code, if you give the problem enough structure upfront. It's not meant to replace developers or do the "vibe coding" thing where you throw a prompt at a model and hope for the best. The opposite really. You write a proper spec, optionally lay out the architecture, and **Intent** breaks that down into a managed build plan that gets executed step by step with an LLM doing the actual code generation under tight constraints.

The key thing is that the human stays in control. You review the plan before anything gets built, you can stop and resume, and you can edit what the planner came up with. If something goes wrong at step 14 of 30 you fix that and keep going, you don't start over. The specs are the source of truth not the generated code, so when requirements change you modify the spec and **Intent** figures out what needs to be regenerated.


## Quick Start

```bash
uv sync --group dev
uv run ntt --help
```
