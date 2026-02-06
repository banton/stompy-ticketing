Create a session handoff summary and persist it to Stompy context.

Steps:

1. Switch to project: `mcp__stompy__project_switch(name="stompy_ticketing")`
2. Gather session state:
   - Run `python3 -m pytest tests/ -v --tb=line 2>&1` to get current test status
   - Run `git -C /Users/banton/Sites/stompy-ticketing log --oneline -10` for recent commits
   - Run `git -C /Users/banton/Sites/stompy-ticketing status` for working tree state
   - Check board: `mcp__stompy__ticket_board(view="summary")`
3. Compose a handoff summary with:
   - What was accomplished this session
   - Current test status (passing/failing count)
   - Open tickets and their status
   - Any blockers or decisions needed
   - Suggested next steps
4. Lock the handoff to Stompy:
   ```
   mcp__stompy__lock_context(
     content="<handoff summary>",
     topic="ticketing_session_handoff",
     priority="important",
     tags="handoff,session,stompy-ticketing"
   )
   ```
5. Display the handoff summary to the user.
