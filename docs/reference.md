> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Reference Tables

> Quick-reference stat tables for all entities.

export const DenseTable = ({children}) => <div className="dense-table">{children}</div>;

## Entity stats

<DenseTable>
  <table>
    <thead>
      <tr>
        <th>Entity</th>
        <th>HP</th>
        <th>Cost</th>
        <th>Scale</th>
        <th>Notes</th>
      </tr>
    </thead>

    <tbody>
      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/core.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=a80e75be7e23b7c16f3fd1ef71d7b9a4" alt="" width="1024" height="1024" data-path="images/entities/core.png" />

          Core
        </td>

        <td>500</td>
        <td>—</td>
        <td>—</td>
        <td>3×3; spawns builders</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/builder-bot.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=b0b8f534c879d31c95e22691fccade5b" alt="" width="512" height="512" data-path="images/entities/builder-bot.png" />

          Builder bot
        </td>

        <td>30</td>
        <td>30 Ti</td>
        <td>20%</td>
        <td>Mobile; build, heal, attack, destroy</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/conveyor.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=487e50d068b9c397ad4c16a993f6a257" alt="" width="512" height="512" data-path="images/entities/conveyor.png" />

          Conveyor
        </td>

        <td>20</td>
        <td>3 Ti</td>
        <td>1%</td>
        <td>3 inputs, 1 output</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/splitter.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=4e711af42fbce57a77c38341f7f2da39" alt="" width="512" height="512" data-path="images/entities/splitter.png" />

          Splitter
        </td>

        <td>20</td>
        <td>6 Ti</td>
        <td>1%</td>
        <td>1 input, 3 rotating outputs</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/bridge.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=662d002ce068c838da058b5924ed9772" alt="" width="1536" height="512" data-path="images/entities/bridge.png" />

          Bridge
        </td>

        <td>20</td>
        <td>20 Ti</td>
        <td>10%</td>
        <td>Output to tile within dist 3</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/armoured-conveyor.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=c706382c9d7e9718c9a2d3e41322a373" alt="" width="512" height="512" data-path="images/entities/armoured-conveyor.png" />

          Armoured conv.
        </td>

        <td>50</td>
        <td>5 Ti, 5 Ax</td>
        <td>1%</td>
        <td>Conveyor with more HP</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/harvester.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=992df718cfea7d1bdf21c47ed90faddb" alt="" width="512" height="512" data-path="images/entities/harvester.png" />

          Harvester
        </td>

        <td>30</td>
        <td>20 Ti</td>
        <td>5%</td>
        <td>Outputs every 4 rounds</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/foundry.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=9661cc6db96a60c933245749ab6f843a" alt="" width="512" height="512" data-path="images/entities/foundry.png" />

          Foundry
        </td>

        <td>50</td>
        <td>40 Ti</td>
        <td>100%</td>
        <td>Ti + raw Ax → refined Ax</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/road.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=62a856e25500d9cca10e041f6c621364" alt="" width="512" height="512" data-path="images/entities/road.png" />

          Road
        </td>

        <td>5</td>
        <td>1 Ti</td>
        <td>0.5%</td>
        <td>Walkable</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/barrier.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=5e85d4c7c912ff7bca23755fcf4f2847" alt="" width="512" height="512" data-path="images/entities/barrier.png" />

          Barrier
        </td>

        <td>30</td>
        <td>3 Ti</td>
        <td>1%</td>
        <td>Blocks space</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/marker.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=684b25be1be46ae82cb48c7205ad5910" alt="" width="512" height="512" data-path="images/entities/marker.png" />

          Marker
        </td>

        <td>1</td>
        <td>Free</td>
        <td>—</td>
        <td>No action cooldown</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/gunner.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=62439f66dff4e5aa36645340d4daad02" alt="" width="512" height="512" data-path="images/entities/gunner.png" />

          Gunner
        </td>

        <td>40</td>
        <td>10 Ti</td>
        <td>10%</td>

        <td>
          Forward ray; markers are targetable but do not block; walls block and
          are not targetable; builder bots and non-marker buildings block; can
          rotate to any direction for 10 Ti
        </td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/sentinel.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=d9e59a60e07e843f244324b5a144cb5f" alt="" width="512" height="512" data-path="images/entities/sentinel.png" />

          Sentinel
        </td>

        <td>30</td>
        <td>30 Ti</td>
        <td>20%</td>
        <td>Line ±1; refined Ax stuns +5 cd</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/breach.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=f1d1f22a28280a787eb9ef85f4a4f145" alt="" width="512" height="512" data-path="images/entities/breach.png" />

          Breach
        </td>

        <td>60</td>
        <td>15 Ti, 10 Ax</td>
        <td>10%</td>
        <td>180° cone; friendly fire</td>
      </tr>

      <tr>
        <td>
          <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/launcher.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=0f147103b2279e8b33821454d9377b40" alt="" width="512" height="512" data-path="images/entities/launcher.png" />

          Launcher
        </td>

        <td>30</td>
        <td>20 Ti</td>
        <td>10%</td>
        <td>Throws adjacent builders</td>
      </tr>
    </tbody>
  </table>
</DenseTable>

## Unit combat stats

<DenseTable>
  <table>
    <thead>
      <tr>
        <th>Unit</th>
        <th>Vision r²</th>
        <th>Action r²</th>
        <th>Attack r²</th>
        <th>Damage</th>
        <th>Reload</th>
        <th>Ammo/shot</th>
      </tr>
    </thead>

    <tbody>
      <tr>
        <td>Core</td>
        <td>36</td>
        <td>8</td>
        <td>—</td>
        <td>—</td>
        <td>—</td>
        <td>—</td>
      </tr>

      <tr>
        <td>Builder bot</td>
        <td>20</td>
        <td>2</td>
        <td>0 (own tile)</td>
        <td>2</td>
        <td>—</td>
        <td>2 Ti</td>
      </tr>

      <tr>
        <td>Gunner</td>
        <td>13</td>
        <td>2</td>
        <td>13</td>
        <td>10 (30 with Ax)</td>
        <td>1</td>
        <td>2</td>
      </tr>

      <tr>
        <td>Sentinel</td>
        <td>32</td>
        <td>2</td>
        <td>32</td>
        <td>18</td>
        <td>3</td>
        <td>10</td>
      </tr>

      <tr>
        <td>Breach</td>
        <td>13</td>
        <td>2</td>
        <td>5</td>
        <td>40 + 20 splash</td>
        <td>1</td>
        <td>5</td>
      </tr>

      <tr>
        <td>Launcher</td>
        <td>26</td>
        <td>2 (pickup)</td>
        <td>26 (throw)</td>
        <td>—</td>
        <td>1</td>
        <td>—</td>
      </tr>
    </tbody>
  </table>
</DenseTable>

## Game constants

<DenseTable>
  <table>
    <thead>
      <tr>
        <th>Constant</th>
        <th>Value</th>
      </tr>
    </thead>

    <tbody>
      <tr>
        <td>Max rounds</td>
        <td>2000</td>
      </tr>

      <tr>
        <td>Unit cap per team</td>
        <td>50 living units (including the core)</td>
      </tr>

      <tr>
        <td>Stack size</td>
        <td>10</td>
      </tr>

      <tr>
        <td>Starting titanium</td>
        <td>500</td>
      </tr>

      <tr>
        <td>Starting axionite</td>
        <td>0</td>
      </tr>

      <tr>
        <td>Passive titanium income</td>
        <td>10 every 4 rounds</td>
      </tr>

      <tr>
        <td>Builder bot heal</td>

        <td>
          4 HP for 1 Ti to all friendly entities on a tile within action radius
        </td>
      </tr>

      <tr>
        <td>Builder bot attack</td>
        <td>2 damage for 2 Ti (own tile only)</td>
      </tr>

      <tr>
        <td>Builder bot self-destruct damage</td>
        <td>0</td>
      </tr>

      <tr>
        <td>Harvester output interval</td>
        <td>Every 4 rounds</td>
      </tr>

      <tr>
        <td>Sentinel stun (refined axionite ammo)</td>
        <td>+5 action and move cooldown</td>
      </tr>

      <tr>
        <td>CPU time per unit per round</td>
        <td>2ms (+5% buffer)</td>
      </tr>

      <tr>
        <td>Memory limit per bot</td>
        <td>1 GB</td>
      </tr>
    </tbody>
  </table>
</DenseTable>

## Cost scaling

Every entity you build increases the cost multiplier. Scale starts at 1.0x (100%). Increases are **additive** — two gunners at +10% each give 1.2x, not 1.21x.

<DenseTable>
  <table>
    <thead>
      <tr>
        <th>Entity</th>
        <th>Scale increase</th>
      </tr>
    </thead>

    <tbody>
      <tr>
        <td>Road</td>
        <td>+0.5%</td>
      </tr>

      <tr>
        <td>Conveyor, splitter, armoured conveyor, barrier</td>
        <td>+1%</td>
      </tr>

      <tr>
        <td>Bridge</td>
        <td>+10%</td>
      </tr>

      <tr>
        <td>Harvester</td>
        <td>+5%</td>
      </tr>

      <tr>
        <td>Gunner, breach, launcher</td>
        <td>+10%</td>
      </tr>

      <tr>
        <td>Builder bot, sentinel</td>
        <td>+20%</td>
      </tr>

      <tr>
        <td>Axionite foundry</td>
        <td>+100%</td>
      </tr>
    </tbody>
  </table>
</DenseTable>

$$
\text{cost} = \lfloor \text{scale} \times \text{base cost} \rfloor
$$


Built with [Mintlify](https://mintlify.com).