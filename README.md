# Barista Bot
Robot arm pours and preps drinks (espresso, americano, milk coffee) using Viam SDK, with perception-based froth check for milk coffee.

Start with pouring first then build on actions incrementally. Hard coded positions for the cup to beginh with, then maybe multiple hard coded cup/ bottle positions. Controlling amount poured and eventually build to making specific drinks.

## Setup
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in robot address and API key
3. `python main.py <drink_name>`

## Status
- [ ] Pour motion working
- [ ] Froth detection
- [ ] Button press for machine controls


Since pour has to work before anything else makes sense, here's how I'd split it with three people, front loading the sequence you gave.

Stage 1: Pour motion (blocks everything, so don't fully parallelize yet)

Person A owns this alone at first: get one basic pour cycle working between the two fixed points from your position testing. No amount control, no froth, just reliable pour.
Person B, while A is on this, builds the perception test harness offline: record a few videos or photos of pouring at different amounts, work on froth_score() and a volume/fill-level estimate against those saved frames, not live yet. This way B isn't blocked waiting on the arm.
Person C, also in parallel, sets up the coffee machine side: mapping button positions, pickup and placement points for the cup, and gets that pick up motion working independently of the pour logic. Also good person to own the git repo structure and config file since it touches everyone's work.

Stage 2: Once pour motion works, split into "how much" and "how frothy"

Person A moves to controlling pour amount: this is really about timing and tilt angle, how long you hold the tilt or how many back and forth cycles before stopping. Tie this into config.py per drink size.
Person B brings the froth detection from offline testing into a live check against the camera feed, and sets real thresholds using actual footage from A's pours.
Person C connects the machine interaction (button press, cup pickup) into the same flow, so by end of this stage you can go: pick up cup, press button, pour to the right amount.

Stage 3: Frothing loop

Person A and B work together here since it's the actual closed loop: pour, check froth score from B's function, decide to pour again or stop. A owns the pour repeat logic, B owns feeding in the score.
Person C keeps working on the three drink presets end to end, making sure espresso and americano (no froth needed) run cleanly through the same pipeline without hitting the froth check at all.

Stage 4: Full integration, everyone

Run all three drinks end to end: pickup, button press, pour to amount, froth check if needed
This is where you'll find the rough edges between each person's piece, so keep this stage light on new features and heavy on just making the handoffs between stages actually work together

The reason I'd keep A solo on stage 1 is that pour is the one piece nobody else can build against until it exists, everything downstream (amount, froth, integration) needs real pour data or real pour behavior to test against, not stubs.
