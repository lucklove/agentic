## agentic

Gitea-notification-driven agent runner. Each profile in `profiles/*.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Running

```bash
uv run main.py
uv run main.py <profile-name> [<profile-name> ...]
uv run main.py <profile-name> -i "instruction"
uv run main.py --help
```

## Core Notification Rules

These rules are core invariants for notification handling. They must not be bypassed, weakened, or reordered casually.

For each unread notification, the poller only considers subjects with type `Issue` or `Pull`. It then applies these gates in order:

1. If the issue or pull request is closed, do not process it.
2. If the issue has any open dependencies, do not process it.
3. Inspect the last issue comment.
4. If the last comment was written by the current agent, do not process it.
5. If the last comment mentions anyone, only process it when it mentions the current agent. If it mentions someone else instead, do not process it.
6. If the last comment mentions nobody, fall back to subject-role matching.
7. Process the subject when the current agent is one of the following:
   creator
   assignee
   requested reviewer
   reviewer
   a recorded pull-request reviewer from review history
8. If none of the rules above match, do not process it.

Only after all gates pass does the poller call `agent.run(...)`.

## Notes

- Pull-request relevance still reads the last regular issue comment from `/issues/{number}/comments`.
- Pull-request review history is also consulted when determining reviewer-based relevance.
- Notification threads are marked read only after the notification is intentionally skipped or successfully handled.
