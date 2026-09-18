# Barista Bot
Robot arm pours and preps drinks (espresso, americano, milk coffee) using Viam SDK, with perception-based froth check for milk coffee.

## Setup
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in robot address and API key
3. `python main.py <drink_name>`

## Status
- [ ] Pour motion working
- [ ] Froth detection
- [ ] Button press for machine controls