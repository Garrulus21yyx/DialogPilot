# Domain instruction packages

The API and Target runtime share `SkillManager`. Each `*/SKILL.md` has YAML
metadata (`name`, `description`, `agents`) and a concise method. `agents` contains
configured domain IDs, not permissions. Only matching domain workers receive the
`read_skill` tool with package metadata; Conversation Agent receives no package
body or domain SOP.

The worker chooses whether to read `SKILL.md` and referenced Markdown files under
`references/` or `examples/`. Reads are normal LangChain tool messages, retained
and compacted by the existing working-memory mechanism. No extra summary/model
call or keyword-triggered prompt insertion is added. This initial version does
not execute package scripts or expose arbitrary filesystem paths.

`GET /skills` describes the same catalog used for execution. `POST /skills/reload`
atomically replaces it. A tool captures a snapshot for its invocation; hashes cover
metadata and all resources. A resumed call naming an old version fails explicitly
instead of silently reading changed instructions. Existing history can retain old
instructions as history; business policy and tools remain authoritative.

`product_upgrade` is the first package, available to product_technical and retail.
It supplies comparison methodology, not catalog facts, user consent or tool grants.
The registered executable `product_identification` capability remains unchanged;
it is a composite executor, not a Markdown package and not a second document loader.

The four old broad role-prompt Skills and keyword injection loader were removed.
Role descriptions and execution policy remain in AgentDefinition. Add a package
only for a reusable method that helps beyond tool descriptions, rather than one
Skill per atomic tool. Configure its domain IDs explicitly in metadata.

Validation covers loading and framework integration. Real-model quality gains
require a separately preregistered comparison; a readable guide is not evidence
that repeat queries or approval errors have been fixed.
