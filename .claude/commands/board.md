Show the current kanban board for stompy-ticketing development.

Steps:
1. Switch to the stompy_ticketing project: `mcp__stompy__project_switch(name="stompy_ticketing")`
2. Show the full board summary: `mcp__stompy__ticket_board(view="summary")`
3. If there are tickets in_progress, show details for each type:
   - `mcp__stompy__ticket_board(type="task")`
   - `mcp__stompy__ticket_board(type="bug")`
   - `mcp__stompy__ticket_board(type="feature")`
   - `mcp__stompy__ticket_board(type="decision")`
4. Format the output as a clean kanban board with columns for each status.
