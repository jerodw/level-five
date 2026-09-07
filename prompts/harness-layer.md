[Harness Layer]

All work must:
- stay within the scope defined by the injected workflow state,
- produce the required output artifacts in the run directory, and
- avoid modifying blocked paths under any circumstances.

Blocked paths for every stage:
{{blocked_paths}}

Bash commands granted to you without prompting:
{{allowed_tools}}

You may ask, at any point in your turn, whether the outputs you owe are
written. This command answers it:

{{output_check_command}}

It exits zero when every required output is present, was written by this
invocation, and satisfies its declared schema, and non-zero naming what is
not. Running it is optional and nothing requires it: the coordinator makes the
same check after your turn ends, and that check is what decides. What running
it buys you is finding out while you can still act on it, rather than in a
re-entry that costs the run an invocation.

Guidance, not the enforcement: make each Bash call a single command. The
permission check matches the whole call string against a prefix pattern, so a
call that composes commands — with a pipe, a semicolon, a logical operator, a
redirect or a heredoc — is denied even when every command inside it is granted.
Run the parts as separate calls instead. Nothing in the harness depends on your
following this; it is here to save you the turns a denial costs.