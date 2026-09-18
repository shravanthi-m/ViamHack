# Fault recovery

Own diagnosis and the next action. The executor aborts a failed phase and requests
StopAll; it does not decide whether retrying, replanning, or a scene reset is right.

1. Read the phase's `result.json`, `events.jsonl`, plan, and config. Record the last
   completed step, failed/incomplete step, exception, and whether StopAll was
   acknowledged. A failed call may have partially changed the physical state.
2. Obtain current observations before motion. Establish held-object state, object
   locations, and any other conditions needed by the proposed recovery. If stopping
   failed or state is unknown, hold the motion gate and request the specific reset
   or observation needed. Do not automatically open a potentially loaded gripper.
3. Search the skill library for a recovery recipe matching the fault **and** state.
   Choose supported primitives within the current configuration and remaining
   budget. A known transient localization failure may justify fresh localization;
   a failed pour does not justify repeating the pour without checking what already
   reached the cup. Avoid duplicating an irreversible task effect.
4. Record the recovery hypothesis, fresh-state evidence, selected recipe/revision,
   expected outcome, and attempt number. Validate the recovery plan and evaluate
   its gate. Execute within existing authorized scope; no repeated permission
   request is needed for an already authorized, bounded attempt.
5. Verify recovery's postconditions, then replan the remaining goal from observed
   state. Use a fresh plan and fresh references; there is no resume-from-step API.
   If recovery fails, preserve its run and stop when the budget is exhausted.

When the available tools cannot recover the observed state, request a concrete
human action (for example, restore an empty gripper and reset the receiving cup).
Do not invent a recovery primitive or rewrite a driver. Human reset is a valid
recovery path; record it and the verified resulting state before continuing.

Link the failed run, recovery run, remaining-task run, and observations in the task
review. Feed the outcome into `agents/skill_learning.md`. Successful recovery can
become a candidate library recipe; one recovery is not evidence of generality.
