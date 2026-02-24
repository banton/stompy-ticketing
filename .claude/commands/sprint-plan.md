Create a new sprint of tickets for stompy-ticketing development.

The user will describe what they want in this sprint. For each item:

1. Create tickets (pass `project="stompy_ticketing"` on all Stompy tool calls): `mcp__stompy__ticket(action="create", title="...", type="task|bug|feature|decision", priority="high|medium|low", description="...")`
3. Set up blocking links where tests must pass before integration:
   - `mcp__stompy__ticket_link(action="create", source_id=<test_ticket>, target_id=<impl_ticket>, link_type="blocks")`
4. Assign tickets to teammates based on file ownership:
   - **tester**: test writing tasks (owns `tests/`)
   - **implementer**: service/model changes (owns `service.py`, `models.py`)
   - **integrator**: plugin boundary work (owns `plugin.py`, `mcp_tools.py`, `api_routes.py`, `migrations.py`)
   - **reviewer**: code review, decision tickets (read-only access)
5. Show the final board: `mcp__stompy__ticket_board(view="summary")`

Sprint ticket naming convention: `[Sprint N] <description>`
