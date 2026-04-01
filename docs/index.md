> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Cambridge Battlecode

> Documentation for the Cambridge Battlecode programming competition.

<img className="block dark:hidden" src="https://mintcdn.com/cambridgebattlecode/OKSh4NmznooMGXzL/images/battlecode-logo-dark.png?fit=max&auto=format&n=OKSh4NmznooMGXzL&q=85&s=6a36d0498d6d2aad05206f559c8f1727" alt="Cambridge Battlecode" style={{ maxWidth: 400, marginBottom: 16 }} width="1090" height="303" data-path="images/battlecode-logo-dark.png" />

<img className="hidden dark:block" src="https://mintcdn.com/cambridgebattlecode/OKSh4NmznooMGXzL/images/battlecode-logo-light.png?fit=max&auto=format&n=OKSh4NmznooMGXzL&q=85&s=6cba937ea4ed750e7f6373130a09f373" alt="Cambridge Battlecode" style={{ maxWidth: 400, marginBottom: 16 }} width="1090" height="303" data-path="images/battlecode-logo-light.png" />

Cambridge Battlecode is a programming competition where you write Python bots that compete in a turn-based strategy game. Your bots control autonomous mining fleets on Titan — harvesting resources, building infrastructure, and destroying the enemy core.

For competition details, dates, prizes, and eligibility, visit the [main website](https://battlecode.cam).

## Quick start

```bash  theme={"dark"}
pip install cambc
cambc starter
cambc run starter starter --watch
```

<CardGroup cols={2}>
  <Card title="Install and scaffold" icon="terminal" href="/getting-started/installation">
    Install the CLI and run `cambc starter` to set up your project.
  </Card>

  <Card title="Write your first bot" icon="code" href="/getting-started/first-bot">
    Learn the basics: spawning builder bots, placing conveyors, and harvesting resources.
  </Card>

  <Card title="Game rules" icon="book" href="/spec/overview">
    Full game specification — map, resources, units, buildings, turrets, and win conditions.
  </Card>

  <Card title="API reference" icon="rectangle-terminal" href="/api/controller">
    Every method available to your bot via the Controller object.
  </Card>
</CardGroup>


Built with [Mintlify](https://mintlify.com).