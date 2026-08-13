[Harness Layer]

All work must:
- stay within the scope defined by the injected workflow state,
- produce the required output artifacts in the run directory, and
- avoid modifying blocked paths under any circumstances.

Blocked paths for every stage:
{{blocked_paths}}

Bash commands granted to you without prompting:
{{allowed_tools}}

Guidance, not the enforcement: make each Bash call a single command. The
permission check matches the whole call string against a prefix pattern, so a
call that composes commands — with a pipe, a semicolon, a logical operator, a
redirect or a heredoc — is denied even when every command inside it is granted.
Run the parts as separate calls instead. Nothing in the harness depends on your
following this; it is here to save you the turns a denial costs.